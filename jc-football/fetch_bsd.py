#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BSD 增强数据模块（伤停 / 首发 / 教练 / 天气 / 裁判 / AI预测）

【接管补充说明】
原源码包缺失本模块，导致 fetch_daily.py 第 5.8 步报 "No module named 'fetch_bsd'"。
本实现为「可插拔适配器」：
  - 未配置 API Key 时静默降级（返回空 dict），规则引擎自动跳过该增强维度，不影响主流程；
  - 配置 API Key 后自动启用，数据写入 bsd_cache.json 并按比赛日缓存。

支持的数据源：API-Football（api-sports.io，免费层 100 次/天）
  注册地址：https://dashboard.api-football.com/register
  配置方式（二选一）：
    1) 环境变量：export BSD_API_KEY="你的Key"
    2) 本目录 bsd_config.json：{"api_key": "你的Key", "enabled": true}

对外接口：
    fetch_and_match(matches, cache=True) -> dict
        返回 {比赛编号(num): {...增强数据...}}，未命中或无 Key 时返回 {}

数据契约（供 fetch_daily.py evaluate* 系列函数消费）：
    {
      "injury_count_home": int,      # 主队伤停人数
      "injury_count_away": int,      # 客队伤停人数
      "ai_preview": str,             # 含比分样式的预测文本，如 "预测比分 2-1"
      "home_coach": {"name","formation","styles":[]},
      "away_coach": {"name","formation","styles":[]},
      "weather": {"temperature_c": float, "wind_speed": float},
      "referee": {"name","yellow_cards":int,"career_games":int},
      "expected_lineups": {"home":[...], "away":[...]},
      "fun_facts": str
    }
"""
import os
import re
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "bsd_cache.json")
CONFIG_PATH = os.path.join(HERE, "bsd_config.json")

API_BASE = "https://v3.football.api-sports.io"
HEADERS_TPL = {"x-apisports-key": ""}

# 竞彩联赛名 -> API-Football league_id（常用联赛，未收录时退化为队名模糊匹配）
LEAGUE_MAP = {
    "英超": 39, "英冠": 40, "英甲": 41,
    "西甲": 140, "西乙": 141,
    "德甲": 78, "德乙": 79,
    "意甲": 135, "意乙": 136,
    "法甲": 61, "法乙": 62,
    "荷甲": 88, "荷乙": 89,
    "葡超": 94, "葡甲": 95,
    "苏超": 179, "比甲": 144,
    "挪超": 103, "瑞典超": 113, "丹超": 119,
    "俄超": 235, "乌超": 332, "土超": 203,
    "巴甲": 71, "阿甲": 128,
    "日职": 98, "日乙": 99, "韩职": 292,
    "澳超": 188, "美职": 253,
}


def _load_config():
    cfg = {"api_key": "", "enabled": True}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("BSD_API_KEY", "") or os.environ.get("API_FOOTBALL_KEY", "")
    return cfg


def _api_get(path, api_key, timeout=20):
    """调用 API-Football，返回 (ok, data_or_error)"""
    if not api_key:
        return False, "no_api_key"
    req = urllib.request.Request(
        API_BASE + path,
        headers={"x-apisports-key": api_key, "User-Agent": "jc-football/1.0"},
    )
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        data = json.loads(raw)
        if data.get("errors"):
            return False, str(data["errors"])[:200]
        return True, data.get("response", [])
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:120]


def _norm(s):
    """队名归一化，便于跨源匹配"""
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    # 去掉常见前后缀
    for suf in ("足球俱乐部", "footballclub", "fc", "cf", "sc", "united", "utd", "ac", "as"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
    return s


def _similar(a, b):
    """简单相似度：包含关系或字符集重合度"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    return len(sa & sb) / max(len(sa | sb), 1) >= 0.6


def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _build_index(api_key, matches):
    """按比赛日拉取 fixtures，建立 (队名) -> fixture_id 索引"""
    dates = sorted({(m.get("date") or m.get("kickoff", "")[:10]) for m in matches if m.get("date") or m.get("kickoff")})
    index = []  # [(league_id, fixture_id, home, away, kickoff)]
    for d in dates:
        if not d:
            continue
        ok, data = _api_get(f"/fixtures?date={d}&timezone=Asia/Shanghai", api_key)
        if not ok:
            continue
        for it in data:
            fx = it.get("fixture", {}) or {}
            tm = it.get("teams", {}) or {}
            lg = it.get("league", {}) or {}
            index.append({
                "fid": fx.get("id"),
                "home": ((tm.get("home") or {}).get("name") or ""),
                "away": ((tm.get("away") or {}).get("name") or ""),
                "league_id": lg.get("id"),
                "kickoff": fx.get("date", ""),
            })
    return index


def _match_fixture(index, m):
    """用队名+联赛把竞彩比赛映射到 API-Football 的 fixture_id"""
    home, away = m.get("home", ""), m.get("away", "")
    want_lid = LEAGUE_MAP.get(m.get("league", ""))
    best, best_score = None, 0
    for it in index:
        if not _similar(it["home"], home) or not _similar(it["away"], away):
            continue
        score = 2
        if want_lid and it.get("league_id") == want_lid:
            score += 2
        if score > best_score:
            best, best_score = it, score
    return best


def _fetch_one(api_key, fid):
    """拉取单场比赛的增强维度"""
    out = {}
    ok, inj = _api_get(f"/injuries?fixture={fid}", api_key)
    if ok and isinstance(inj, list):
        h = a = 0
        h_list, a_list = [], []
        for x in inj:
            t = (x.get("team") or {}).get("name", "")
            p = (x.get("player") or {}).get("name", "")
            r = (x.get("player") or {}).get("reason", "") or "伤停"
            side = None
            # 用 fixtures 里的主客顺序判定：这里退化为按返回顺序分组不可靠，改用 team 名
            out.setdefault("_inj_teams", {})
            out["_inj_teams"].setdefault(t, []).append(f"{p}({r})")
        if out.get("_inj_teams"):
            keys = list(out["_inj_teams"].keys())
            if keys:
                out["_inj_home_key"] = keys[0]
                out["_inj_away_key"] = keys[1] if len(keys) > 1 else ""
    ok, pred = _api_get(f"/predictions?fixture={fid}", api_key)
    if ok and isinstance(pred, list) and pred:
        p0 = pred[0] or {}
        goals = ((p0.get("goals") or {}).get("home")), ((p0.get("goals") or {}).get("away"))
        if goals[0] is not None and goals[1] is not None:
            out["ai_preview"] = f"AI预测比分 {goals[0]}-{goals[1]}"
        wc = p0.get("comparison") or {}
        out["fun_facts"] = (p0.get("advice") or "")[:300]
    ok, lu = _api_get(f"/lineups?fixture={fid}", api_key)
    if ok and isinstance(lu, list) and len(lu) >= 2:
        def pack(x):
            t = x or {}
            return {
                "name": ((t.get("team") or {}).get("name") or ""),
                "formation": t.get("formation") or "",
                "styles": [],
                "players": [p.get("player", {}).get("name", "") for p in (t.get("startXI") or [])][:11],
            }
        out["_lineup_home"] = pack(lu[0])
        out["_lineup_away"] = pack(lu[1])
    return out


def fetch_and_match(matches, cache=True):
    """主入口：返回 {比赛编号: 增强数据}；无 Key 或失败时返回 {}"""
    cfg = _load_config()
    if not cfg.get("enabled"):
        return {}
    api_key = cfg.get("api_key", "")
    if not api_key:
        # 未配置 Key：静默降级，不打印噪音（调用方已 try/except）
        return {}

    cache_data = _load_cache() if cache else {}
    today = datetime.now().strftime("%Y-%m-%d")
    result = {}
    need = []
    for m in matches:
        num = m.get("num", "")
        if not num:
            continue
        c = cache_data.get(num)
        if c and c.get("_date") == today:
            result[num] = c
        else:
            need.append(m)

    if need:
        index = _build_index(api_key, need)
        if not index:
            return result
        for m in need:
            fx = _match_fixture(index, m)
            if not fx:
                continue
            raw = _fetch_one(api_key, fx["fid"])
            if not raw:
                continue
            rec = {
                "_date": today,
                "_fid": fx["fid"],
                "injury_count_home": 0,
                "injury_count_away": 0,
                "home_injuries": [],
                "away_injuries": [],
                "ai_preview": raw.get("ai_preview", ""),
                "fun_facts": raw.get("fun_facts", ""),
                "home_coach": {"name": "", "formation": "", "styles": []},
                "away_coach": {"name": "", "formation": "", "styles": []},
                "weather": {},
                "referee": {},
                "expected_lineups": {"home": [], "away": []},
            }
            # 伤停按主客队名归属
            teams = raw.get("_inj_teams", {})
            hk, ak = raw.get("_inj_home_key", ""), raw.get("_inj_away_key", "")
            if _similar(hk, m.get("home", "")):
                rec["home_injuries"] = teams.get(hk, [])
            elif _similar(hk, m.get("away", "")):
                rec["away_injuries"] = teams.get(hk, [])
            if _similar(ak, m.get("away", "")):
                rec["away_injuries"] = teams.get(ak, [])
            elif _similar(ak, m.get("home", "")):
                rec["home_injuries"] = teams.get(ak, [])
            rec["injury_count_home"] = len(rec["home_injuries"])
            rec["injury_count_away"] = len(rec["away_injuries"])
            # 首发
            lh = raw.get("_lineup_home") or {}
            la = raw.get("_lineup_away") or {}
            if _similar(lh.get("name", ""), m.get("home", "")):
                rec["expected_lineups"]["home"] = lh.get("players", [])
                rec["home_coach"]["formation"] = lh.get("formation", "")
                rec["expected_lineups"]["away"] = la.get("players", [])
                rec["away_coach"]["formation"] = la.get("formation", "")
            else:
                rec["expected_lineups"]["home"] = la.get("players", [])
                rec["home_coach"]["formation"] = la.get("formation", "")
                rec["expected_lineups"]["away"] = lh.get("players", [])
                rec["away_coach"]["formation"] = lh.get("formation", "")
            result[m["num"]] = rec
            cache_data[m["num"]] = rec
            time.sleep(0.3)  # 温柔限速
        if cache:
            _save_cache(cache_data)
    return result


if __name__ == "__main__":
    cfg = _load_config()
    if not cfg.get("api_key"):
        print("未配置 API Key：请设置环境变量 BSD_API_KEY 或创建 bsd_config.json")
        print("本模块将静默降级，主流程不受影响。")
    else:
        print("API Key 已配置，模块已启用。")
