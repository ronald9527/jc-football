#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比分预测赛果：500体育补充 + 统一规范化（v1.7.113）
=================================================

问题背景
--------
「比分预测」板块按 `status==="FINISHED"` 判定「已结束」，且只有
`finished && actH!=null` 才显示实际比分。但 fetch_scores.py 从竞彩历史档案
(history.json) 补全历史场次时，把 `scoreActual`（如 "1:2"）同时塞进 actH/actA，
status 留空、evaluated=False —— 于是这些场次**明明有赛果却显示「未开赛」、
分组统计「已结束 0」**。

同时竞彩官方接口 webapi.sporttery.cn 封锁海外 IP（HTTP 567），导致 history.json
近几日的 result 迟迟不回填；而 500体育 trade.500.com/jczq/?date= 页面海外可达，
且服务端渲染带已完赛场次的比分 → 用它补齐近几日赛果。

本模块做两件事
--------------
1) fetch_500(days)：抓 500体育近几日已结束场次比分，累积写入 jc_results_500.json
   （纯增量、幂等；网络失败静默降级，绝不阻断流水线）。
2) normalize_sp(sp)：把 score_predict.json 的 matches 就地规范化——
   · "1:2" 字符串拆成 actH/actA 整数
   · 从本地赛果池（history.json 官方赛果 + jc_results_500.json）补缺失赛果
   · 有赛果即设 status=FINISHED、evaluated=True、exactHit/outcomeHit/mae
   · 无赛果保持原样（诚实保留，不瞎编）
   normalize_sp 是纯本地函数，被 build_panel.py 调用作为「最后一道关」：
   无论 fetch_scores.py 在流水线的哪个环节重跑，页面数据一定是对的。

用法
----
  python3 sp_results.py                 # 抓500 + 规范化 + 写盘
  python3 sp_results.py --no-net       # 只用本地缓存（离线/CI二次调用）
  python3 sp_results.py --dry-run      # 只预览，不写盘
  python3 sp_results.py --quiet        # 一行汇总（定时任务用）

设计纪律
--------
· 幂等零噪声：任何文件无实质变化都不写盘，不刷新 updatedAt。
· 宁缺毋滥：队名匹配需两队同时命中；日期允许 ±1 天（竞彩批次日 vs 实际比赛日）。
· 零耦合：只读写 score_predict.json / jc_results_500.json，不改 history.json
  （核心赛果档案由 fetch_daily.py 主流程维护，避免越权与合并冲突）。
"""

import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SP_PATH = os.path.join(HERE, "score_predict.json")
HIST_PATH = os.path.join(HERE, "history.json")
CACHE_PATH = os.path.join(HERE, "jc_results_500.json")

JCZQ_URL = "https://trade.500.com/jczq/?date=%s"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ROW_RE = re.compile(r'<tr[^>]*class="[^"]*bet-tb-tr[^"]*"[^>]*>.*?</tr>', re.S)
ATTR_RE = re.compile(r'data-([a-z]+)="([^"]*)"')
SCORE_RE = re.compile(r'<a class="score"[^>]*>\s*(\d+)\s*:\s*(\d+)\s*</a>')
SPLIT_SCORE_RE = re.compile(r"^\s*(\d+)\s*[:：\-]\s*(\d+)\s*$")

DRY = "--dry-run" in sys.argv
QUIET = "--quiet" in sys.argv
NO_NET = "--no-net" in sys.argv


def _load(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)


def _save_if_changed(p, obj, old):
    """无实质变化不写盘（幂等零噪声）。返回是否写盘。"""
    if _dump(obj) == _dump(old):
        return False
    if DRY:
        return False
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return True


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _name_hit(a, b):
    """两队名是否同一队：完全相等，或较短名是较长名的子串（防 "亚特兰大" vs "亚特兰大BC"）。"""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 2 and short in long


def _score_str(v):
    """把 '1:2' / 1 等转成 (h, a) 整数对；失败返回 None。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return None
    m = SPLIT_SCORE_RE.match(str(v or ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _date_shift(d, delta):
    try:
        return (date.fromisoformat(d) + timedelta(days=delta)).isoformat()
    except Exception:
        return ""


def _sign(h, a):
    try:
        h, a = int(h), int(a)
    except (TypeError, ValueError):
        return None
    return 1 if h > a else (0 if h == a else -1)


# ---------------------------------------------------------------- 500体育赛果
def parse_500(html, batch_date):
    """解析 500体育竞彩页 -> [{num,date,matchDate,home,away,h,a}]（仅带比分的行）。"""
    out = []
    for row in ROW_RE.findall(html or ""):
        attr = dict(ATTR_RE.findall(row))
        score = SCORE_RE.search(row)
        num = (attr.get("matchnum") or "").strip()
        if not num or not score:
            continue
        out.append({
            "num": num,
            "date": (attr.get("processdate") or batch_date).strip(),
            "matchDate": (attr.get("matchdate") or "").strip()[:10],
            "home": (attr.get("homesxname") or "").strip(),
            "away": (attr.get("awaysxname") or "").strip(),
            "h": int(score.group(1)),
            "a": int(score.group(2)),
        })
    return out


def _http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.500.com/",
        "Accept": "text/html,*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", errors="ignore")


def _need_dates(hist, sp, today, span=10, cap=8):
    """近 span 天内「有场次但缺赛果」的竞彩批次日（最多 cap 个），按升序。"""
    ds = set()
    t = today.isoformat() if hasattr(today, "isoformat") else str(today)
    lo = (date.fromisoformat(t) - timedelta(days=span)).isoformat()
    hi = (date.fromisoformat(t) + timedelta(days=1)).isoformat()

    def _add(d):
        d = (d or "")[:10]
        if d and lo <= d <= hi:
            ds.add(d)

    recs = hist.get("records", hist)
    if isinstance(recs, dict):
        recs = list(recs.values())
    for r in recs or []:
        if not isinstance(r, dict):
            continue
        d = (r.get("date") or "")[:10]
        res = r.get("result")
        has = isinstance(res, dict) and res.get("home") is not None and res.get("away") is not None
        if d and not has:
            _add(d)
    for m in (sp.get("matches") or []):
        if not isinstance(m, dict) or m.get("actH") is not None:
            continue
        d = (m.get("utcDate") or m.get("date") or "")[:10]
        _add(d)
    return sorted(ds)[-cap:]


def fetch_500(hist, sp, today):
    """抓近几日 500体育赛果并合并进缓存。返回 (cache, 新增条数, 抓取日期数, 失败数)。"""
    cache = _load(CACHE_PATH) or {"updatedAt": "", "results": []}
    results = cache.get("results") or []
    seen = {(x.get("num"), x.get("date"), x.get("home"), x.get("away")) for x in results}
    dates = _need_dates(hist, sp, today)
    added = 0
    failed = 0
    for d in dates:
        try:
            html = _http_get(JCZQ_URL % d)
            rows = parse_500(html, d)
            for x in rows:
                k = (x["num"], x["date"], x["home"], x["away"])
                if k in seen:
                    continue
                seen.add(k)
                results.append(x)
                added += 1
        except Exception:
            failed += 1
        time.sleep(0.2)
    cache = {
        "updatedAt": (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "500.com trade/jczq",
        "results": results,
    }
    return cache, added, len(dates), failed


# ---------------------------------------------------------------- 赛果池
def build_pool(hist, cache):
    """
    返回 (by_pair, stats)：
      by_pair[(date, normHome, normAway)] = (h, a, source)
    来源优先级：竞彩官方 result > 500体育缓存。
    500 缓存额外按「竞彩编号 -> history.json 规范队名」挂载，解决 500 简称
    与赛程中文全名不一致的问题。
    """
    recs = hist.get("records", hist)
    if isinstance(recs, dict):
        recs = list(recs.values())
    recs = [r for r in (recs or []) if isinstance(r, dict)]

    by_pair = {}
    num2rec = defaultdict(list)
    for r in recs:
        d = (r.get("date") or "")[:10]
        home, away = r.get("home"), r.get("away")
        if not (d and home and away):
            continue
        num = r.get("num")
        if num:
            num2rec[num].append((d, home, away))
        res = r.get("result")
        if isinstance(res, dict) and res.get("home") is not None and res.get("away") is not None:
            by_pair[(d, _norm(home), _norm(away))] = (int(res["home"]), int(res["away"]), "竞彩官方")

    five = 0
    for x in (cache.get("results") or []):
        d = (x.get("date") or "")[:10]
        home, away = x.get("home"), x.get("away")
        h, a = x.get("h"), x.get("a")
        if not (d and home and away and h is not None and a is not None):
            continue
        pair = (int(h), int(a), "500体育")
        by_pair.setdefault((d, _norm(home), _norm(away)), pair)
        md = (x.get("matchDate") or "")[:10]
        if md and md != d:
            by_pair.setdefault((md, _norm(home), _norm(away)), pair)
        # 用竞彩编号把 500 比分挂到 history.json 的规范中文队名下。
        # 竞彩编号每周循环（如「周日025」每周都有），必须限制批次日接近，
        # 否则会把本周比分挂到上周同编号的另一场比赛上（严重错配）。
        for (rd, rh, ra) in num2rec.get(x.get("num"), []):
            try:
                if abs((date.fromisoformat(rd) - date.fromisoformat(d)).days) > 2:
                    continue
            except Exception:
                continue
            by_pair.setdefault((rd, _norm(rh), _norm(ra)), pair)
            for delta in (-1, 1):
                ds = _date_shift(rd, delta)
                if ds:
                    by_pair.setdefault((ds, _norm(rh), _norm(ra)), pair)
        five += 1
    return by_pair, {"竞彩官方": sum(1 for v in by_pair.values() if v[2] == "竞彩官方"),
                     "500体育条数": five, "索引键": len(by_pair)}


def _find_score(m, by_pair):
    """按 (日期±1, 两队名) 找赛果；两队必须同时命中，避免错配。返回 (h,a,src)。"""
    d = (m.get("utcDate") or m.get("date") or "")[:10]
    home, away = m.get("home"), m.get("away")
    if not (d and home and away):
        return None
    dates = [d]
    for delta in (-1, 0, 1):
        ds = _date_shift(d, delta)
        if ds and ds not in dates:
            dates.append(ds)
    # 精确键优先
    for dd in dates:
        v = by_pair.get((dd, _norm(home), _norm(away)))
        if v:
            return v[0], v[1], v[2]
    # 包含式兜底（同队异写）
    hits = set()
    for (dd, nh, na), v in by_pair.items():
        if dd not in dates:
            continue
        if _name_hit(home, nh) and _name_hit(away, na):
            hits.add((v[0], v[1], v[2]))
    if len(hits) == 1:
        return next(iter(hits))
    return None


# ---------------------------------------------------------------- 历史日期兜底
def ensure_history_matches(sp, hist, today, span=14):
    """把竞彩档案近 span 天有赛果、但展示窗缺失的场次补回 matches。

    fetch_scores.py 每天按自己的滚动窗口重写 matches，曾把 10-24/10-25 两个
    历史竞彩日整段挤出展示窗（页面日期分组缺日）。这里做兜底，保证
    「全部回溯、一个不漏」：从 history.json 的赛前预测 + 赔率反推概率 + 官方赛果
    构造规范卡片，缺失的实际比分/命中由随后的 normalize 统一补齐。
    按键 (日期, 主队, 客队) 去重，重复调用零新增。
    """
    recs = hist.get("records", hist)
    if isinstance(recs, dict):
        recs = list(recs.values())
    ms = sp.setdefault("matches", [])
    have = {((m.get("utcDate") or m.get("date") or "")[:10], m.get("home"), m.get("away"))
            for m in ms if isinstance(m, dict)}
    t = today.isoformat() if hasattr(today, "isoformat") else str(today)
    lo = (date.fromisoformat(t) - timedelta(days=span)).isoformat()
    added = 0
    for r in recs or []:
        if not isinstance(r, dict):
            continue
        d = (r.get("date") or "")[:10]
        if not (lo <= d <= t):
            continue
        res = r.get("result")
        if not (isinstance(res, dict) and res.get("home") is not None and res.get("away") is not None):
            continue
        home, away = r.get("home"), r.get("away")
        if not (home and away) or (d, home, away) in have:
            continue
        # 赛前预测比分（scoreRec.predicted），缺失退路 1:1
        predH, predA = 1, 1
        ps = ((r.get("scoreRec") or {}).get("predicted") or "").strip()
        pm = SPLIT_SCORE_RE.match(ps)
        if pm:
            predH, predA = int(pm.group(1)), int(pm.group(2))
        # 概率条：由赔率倒数归一化反推，缺失则均分
        pH, pD, pA = 33.3, 33.3, 33.4
        try:
            odds = r.get("odds") or {}
            ih, idd, ia = 1 / float(odds["h"]), 1 / float(odds["d"]), 1 / float(odds["a"])
            s = ih + idd + ia
            pH, pD, pA = round(ih / s * 100, 1), round(idd / s * 100, 1), round(ia / s * 100, 1)
        except Exception:
            pass
        out = "H" if predH > predA else ("A" if predH < predA else "D")
        ms.append({
            "id": r.get("id"), "competition": "", "league": r.get("league", ""),
            "home": home, "away": away, "utcDate": d + "T00:00:00", "date": d,
            "status": "", "predH": predH, "predA": predA,
            "pHome": pH, "pDraw": pD, "pAway": pA, "predOutcome": out,
            "actH": None, "actA": None, "evaluated": False,
            "source": "history-archive",
        })
        have.add((d, home, away))
        added += 1
    return added


# ---------------------------------------------------------------- 规范化
def normalize_sp(sp, hist=None, cache=None, today=None):
    """就地规范化 sp['matches']，返回统计 dict。纯本地、无副作用（不写盘）。"""
    if hist is None:
        hist = _load(HIST_PATH) or {}
    if cache is None:
        cache = _load(CACHE_PATH) or {"results": []}
    if today is None:
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    ensured = ensure_history_matches(sp, hist, today)
    by_pair, pool_stat = build_pool(hist, cache)
    matches = sp.get("matches") or []
    stat = {"total": len(matches), "ensured": ensured, "fixed": 0, "already": 0, "no_result": 0,
            "by_src": defaultdict(int), "by_date": defaultdict(int), "pool": pool_stat}

    for m in matches:
        if not isinstance(m, dict):
            continue
        d = (m.get("utcDate") or m.get("date") or "")[:10]
        st = str(m.get("status") or "").upper()
        ah, aa = m.get("actH"), m.get("actA")
        src = None
        h = a = None

        # ⓪ 权威优先：精确(日期±1 + 两队名)键查竞彩官方 / 500体育赛果。
        #    fetch_scores.py 曾把 scoreActual 整串塞进 actH/actA，此处可纠正
        #    任何不一致（审计时 97 场与官方 100% 一致，此处是防御性兜底）。
        exact = None
        d0 = (m.get("utcDate") or m.get("date") or "")[:10]
        for dd in (d0, _date_shift(d0, -1), _date_shift(d0, 1)):
            if not dd:
                continue
            v = by_pair.get((dd, _norm(m.get("home")), _norm(m.get("away"))))
            if v:
                exact = v
                break
        if exact:
            h, a, src = exact[0], exact[1], exact[2] + "(权威)"
        # ① 已有整数比分（football-data 标准格式，池内无对应中文键时）
        elif isinstance(ah, int) and isinstance(aa, int):
            h, a, src = ah, aa, "原有整数比分"
        # ② 脏字符串 "1:2"（fetch_scores.py 从 scoreActual 塞进来的）
        elif ah is not None or aa is not None:
            pair = _score_str(ah) or _score_str(aa)
            if pair:
                h, a, src = pair[0], pair[1], "拆分脏字符串"
        # ③ 从赛果池模糊补（两队名包含式兜底，仅用于原本无赛果的场次）
        if h is None:
            found = _find_score(m, by_pair)
            if found:
                h, a, src = found[0], found[1], found[2] + "(补)"

        if h is None:
            stat["no_result"] += 1
            continue

        # 命中判定（复用 fetch_scores._evaluate 口径）
        predH, predA = m.get("predH"), m.get("predA")
        exact = (predH == h and predA == a)
        outcome = (_sign(predH, predA) is not None and _sign(predH, predA) == _sign(h, a))
        try:
            mae = (abs(float(predH) - h) + abs(float(predA) - a)) / 2.0
        except (TypeError, ValueError):
            mae = None
        new = {
            "actH": h, "actA": a, "status": "FINISHED", "evaluated": True,
            "exactHit": bool(exact), "outcomeHit": bool(outcome), "mae": mae,
        }
        changed = any(m.get(k) != v for k, v in new.items())
        if changed:
            m.update(new)
            stat["fixed"] += 1
            stat["by_src"][src] += 1
            stat["by_date"][d] += 1
        elif st == "FINISHED":
            stat["already"] += 1
    return stat


# ---------------------------------------------------------------- CLI
def main():
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    sp = _load(SP_PATH)
    if not isinstance(sp, dict) or not isinstance(sp.get("matches"), list):
        print("sp_results: score_predict.json 缺失或结构异常，跳过")
        return 0
    hist = _load(HIST_PATH) or {}
    cache_old = _load(CACHE_PATH) or {"updatedAt": "", "results": []}

    added = fetch_dates = failed = 0
    cache = cache_old
    if not NO_NET:
        cache, added, fetch_dates, failed = fetch_500(hist, sp, today)
        _save_if_changed(CACHE_PATH, cache, cache_old)

    stat = normalize_sp(sp, hist, cache, today)
    wrote = _save_if_changed(SP_PATH, sp, _load(SP_PATH) or {})

    if QUIET:
        print("sp_results: 补历史场次%d | 规范化 %d 场(已有%d/缺赛果%d) | 500体育新增 %d 条(抓%d日/失败%d) | 写盘:%s%s"
              % (stat["ensured"], stat["fixed"], stat["already"], stat["no_result"],
                 added, fetch_dates, failed, "是" if wrote else "否", " [DRY-RUN]" if DRY else ""))
        return 0

    print("=" * 64)
    print("比分预测赛果规范化  北京日期 %s%s" % (today.isoformat(), "  [DRY-RUN 不写盘]" if DRY else ""))
    print("-" * 64)
    print("500体育：抓取 %d 个日期 / 新增 %d 条赛果 / 失败 %d 日 / 缓存累计 %d 条"
          % (fetch_dates, added, failed, len(cache.get("results") or [])))
    print("赛果池：", stat["pool"])
    print("matches 共 %d 场（补回历史场次 %d） | 规范化 %d | 原本已规范 %d | 仍无赛果 %d"
          % (stat["total"], stat["ensured"], stat["fixed"], stat["already"], stat["no_result"]))
    print("  来源分布：", dict(stat["by_src"]))
    print("  按日期（规范化场数）：")
    for d in sorted(stat["by_date"], reverse=True):
        print("    %s : %d" % (d, stat["by_date"][d]))
    print("  写盘：", "是" if wrote else "否（无变化）")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
