#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成球队队标映射 team_crests.json（英文 FD 名 + 中文竞彩名 双键 → 队标 URL）。

独立脚本，零耦合核心竞彩数据：
- 来源：football-data.org 免费层 /v4/competitions/{code}/teams（复用 fd_config.json 的 api_key）
- 每队取 id / name / crest（crest 形如 https://crests.football-data.org/{id}.png）
- 经 team_map.TEAM_EN2ZH 把英文 FD 名翻译为竞彩中文名，两张键都写入映射：
    英文 FD 名  → crest
    中文竞彩名  → crest
  这样渲染层无论拿到英文（FD 源）还是中文（竞彩历史源）队名，都能查到队标。
- 输出 team_crests.json：{ "队名": "crestUrl", ... }

用法：
    python3 fetch_crests.py            # 联网拉取并覆盖生成
    python3 fetch_crests.py --no-fetch # 仅用已缓存的 score_predict_cache 重建（不联网）
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "fd_config.json")
OUT_PATH = os.path.join(HERE, "team_crests.json")
CACHE_PATH = os.path.join(HERE, "score_predict_cache.json")
API_BASE = "https://api.football-data.org/v4"

# 联赛 code -> 中文名（与 fetch_scores.py 保持一致，避免双字典漂移）
CODE_TO_LEAGUE = {
    "PL": "英超", "ELC": "英冠", "PD": "西甲", "SDG": "西乙",
    "BL1": "德甲", "BL2": "德乙", "SA": "意甲", "SB": "意乙",
    "FL1": "法甲", "FL2": "法乙", "DED": "荷甲", "EDR": "荷乙",
    "PPL": "葡超", "SP1": "苏超", "B1": "比甲", "NL1": "挪超",
    "ML1": "瑞典超", "SL": "丹超", "PL1": "俄超", "SL1": "土超",
    "BSA": "巴甲", "PA1": "阿甲", "JL1": "日职", "JL2": "日乙",
    "K1": "韩职", "AL1": "澳超", "MLS": "美职", "CSL": "中超",
}
GAP = 6.5          # 相邻调用间隔（秒），免费层 10/min 限流
BUDGET = 60        # 联赛请求上限（免费层覆盖有限，超了即停）


def _load_key():
    if os.path.exists(CONFIG_PATH):
        try:
            return (json.load(open(CONFIG_PATH, encoding="utf-8")) or {}).get("api_key", "")
        except Exception:
            pass
    return ""


_PREF = (r"\b(FC|CF|AC|AS|SC|SV|SK|NK|FK|UD|US|SS|RC|CD|BK|SD|JK|KF|GD|RJ|"
         r"VfL|VfB|Calcio|Club|Clube|Futebol|Football|Fotbal|Fotboll)\b")


def py_spzh(n, tme):
    """复刻 template.html 的 spZh：英文 FD 名 → 竞彩中文名（与渲染层完全一致）。"""
    if not n:
        return "?"
    if n in tme:
        return tme[n]
    base = re.sub(_PREF, "", n, flags=re.I)
    base = re.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    if base and base in tme:
        return tme[base]
    lead = re.sub(r"^\s*[A-Z]{1,3}\s+", "", base).strip()
    if lead and lead in tme:
        return tme[lead]
    return n


def _get(path, key, gap, calls):
    if calls[0] >= BUDGET:
        return False, "budget_exceeded"
    now = time.time()
    wait = gap - (now - calls[1])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        API_BASE + path,
        headers={"X-Auth-Token": key, "User-Agent": "jc-crests/1.0"},
    )
    try:
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        calls[0] += 1
        calls[1] = time.time()
        return True, json.loads(raw)
    except urllib.error.HTTPError as e:
        calls[1] = time.time()
        return False, "HTTP %d" % e.code
    except Exception as e:
        calls[1] = time.time()
        return False, str(e)[:80]


def _norm(s):
    """队名归一：小写 + 标点/连字符统一为空格 + 剥离行首缩写(SSC/AS/FC/real 等)与年份，
    便于跨源（FD 队名 vs team_map 别名）稳定匹配。"""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)            # 标点/连字符/空格 统一为空格
    s = re.sub(r"^\s*(?:[a-z]{2,4}\s+)+", "", s)  # 去行首缩写（SSC/AS/AC/FC/US/real…）
    s = re.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build(no_fetch=False):
    key = _load_key()
    sys.path.insert(0, HERE)
    try:
        from team_map import TEAM_EN2ZH as TME
    except Exception:
        TME = {}

    crests = {}        # name -> crestUrl（英文 + 中文双键）
    en_crest = {}      # FD 英文名(归一) -> crestUrl
    norm_index = {}    # 归一名 -> crestUrl（首赢）

    def _put(name, crest):
        if not name or not crest:
            return
        crests[name] = crest
        nl = name.lower()
        if nl != name:
            crests[nl] = crest
        nk = _norm(name)
        if nk and nk not in norm_index:
            norm_index[nk] = crest

    if no_fetch:
        # 不联网：用现有 team_crests.json 里的 FD 英文键反推（保留已抓队标），
        # 再加 score_predict_cache 的 id 兜底；随后统一跑 TME 别名匹配 + 级联。
        seeded = 0
        if os.path.exists(OUT_PATH):
            try:
                exist = json.load(open(OUT_PATH, encoding="utf-8")) or {}
                for kk, vv in exist.items():
                    if isinstance(vv, str) and "football-data.org" in vv and kk and kk[:1].isalpha():
                        _put(kk, vv)
                        seeded += 1
            except Exception:
                pass
        if os.path.exists(CACHE_PATH):
            try:
                cache = json.load(open(CACHE_PATH, encoding="utf-8")) or {}
                for tid, entry in cache.items():
                    nm = entry.get("name")
                    if nm and nm not in crests:
                        cr = "https://crests.football-data.org/%s.png" % tid
                        _put(nm, cr)
            except Exception:
                pass
        print("no-fetch：从现有队标文件反推 %d 个 FD 英文键" % seeded)
    elif not key:
        print("! 未配置 fd api_key，无法联网拉取队标；改用现有文件反推")
        return build(no_fetch=True)
    else:
        calls = [0, 0.0]
        ok_n = 0
        skip_n = 0
        for code in CODE_TO_LEAGUE:
            ok, data = _get("/competitions/%s/teams" % code, key, GAP, calls)
            if not ok:
                skip_n += 1
                continue
            teams = (data.get("teams") or []) if isinstance(data, dict) else []
            for t in teams:
                tid = t.get("id")
                nm = t.get("name")
                cr = t.get("crest")
                if not (tid and nm):
                    continue
                if not cr:
                    cr = "https://crests.football-data.org/%s.png" % tid
                _put(nm, cr)
                nk = _norm(nm)
                if nk:
                    en_crest[nk] = cr
            ok_n += 1
            print("  %-4s %s：%d 队" % (code, CODE_TO_LEAGUE[code], len(teams)))
        print("联赛拉取成功 %d / 跳过 %d；FD 队标 %d 条" % (ok_n, skip_n, len(crests)))

    # ---- 用 team_map 的所有英文别名把「竞彩中文名 → 队标」补齐 ----
    # team_map 含 arsenal / Manchester United / 曼彻斯特城 等大量英文别名与
    # 中文长短名级联（曼彻斯特城→曼城）。把每个英文别名归一后匹配已抓 FD 队标，
    # 再沿中文长短名级联，覆盖「曼城/阿森纳/那不勒斯」等竞彩短名（否则历史回溯查不到队标）。
    for en_key, zh_val in TME.items():
        if not zh_val or zh_val in crests:
            continue
        nk = _norm(en_key)
        cr = norm_index.get(nk) if nk else None
        if cr:
            crests[zh_val] = cr
            zl = zh_val.lower()
            if zl != zh_val:
                crests[zl] = cr
    # 中文长短名级联：曼彻斯特城(已得标) → 曼城
    for k in list(crests.keys()):
        v = TME.get(k)
        if v and v != k and v not in crests:
            crests[v] = crests[k]
    # 形近字归一（尔↔耳 等）：巴黎圣日尔曼 ↔ 巴黎圣日耳曼
    _CONF = [("尔", "耳")]
    for k in list(crests.keys()):
        for a, b in _CONF:
            if a in k and (k.replace(a, b) not in crests):
                crests[k.replace(a, b)] = crests[k]
            if b in k and (k.replace(b, a) not in crests):
                crests[k.replace(b, a)] = crests[k]
    print("补齐 team_map 别名 + 中文级联后：队标 %d 条" % len(crests))
    return crests


def main():
    no_fetch = "--no-fetch" in sys.argv
    crests = build(no_fetch=no_fetch)
    try:
        json.dump(crests, open(OUT_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("已写出 %s（%d 条）" % (OUT_PATH, len(crests)))
    except Exception as e:
        print("写出失败：%s" % e)


if __name__ == "__main__":
    main()
