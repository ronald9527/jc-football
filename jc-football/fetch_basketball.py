#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球数据抓取（v1.7.119 双源根治停更）。

数据源与足球完全隔离——只写 basketball.json / basketball_history.json，
绝不触碰足球的 matches.json / history.json / rules_config.json。

双源设计（v1.7.119）
--------------------
  A. 官方 webapi.sporttery.cn/gateway/uniform/basketball/getMatchCalculatorV1.qry
     在售赛程 + 四玩法精确 SP 赔率。本地（中国网络）HTTP 200，
     但 GitHub Actions 海外 IP 长期 HTTP 567 —— 这正是篮球从 2026-11-02
     停更 4 天、AI 与串关反复拿 11-01 旧比赛造假推荐的根因。
  B. 500.com 备用源 fetch_basketball_500.py（trade.500.com/jclq/?date=）
     海外实测 HTTP 200，按销售批次日返回整批赛程 + 让分/大小分盘口 +
     胜分差 12 档赔率 + 已结束场次比分。官方不通/过期时无条件接管，
     保证每天都有真实当日赛程与可回溯赛果。

合并规则：500 全量赛程打底，官方在售 3~N 场的精确赔率按 (批次日,编号) 覆盖；
官方独有的字段标 _oddsApprox=[]，其余标 500.com 参考赔率。

玩法（篮球四玩法）
  - mnl  胜负        {h: 主胜赔, a: 客胜赔}
  - hdc  让分胜负    {goalLine: 让分(代数值, 主队加), h, a}
  - hilo 大小分      {goalLine: 总分线, h: 大分赔, l: 小分赔}
  - wnm  胜分差      {l1..l6, w1..w6: 各胜分区间赔率}

编号空间：篮球从 301 起（周二301…），与足球 001 起并行，互不冲突。

设计原则
  - 抓取失败时保留旧数据并 exit 0，不拖垮 run_daily.sh 主流程
  - 官方空响应（0 场但不抛异常）不再抹空在售赛程
  - 赛果回填以 500 页面比分为主（已用官方档案 10-31 火箭98:119雷霆校准为主:客），
    官方开奖接口作补充；单轮回填设上限保护定时任务预算
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

try:
    from fetch_daily import fetch          # 复用足球同款 WAF 重试（同网关）
except Exception:                          # 独立运行兜底
    fetch = None

try:
    from fetch_basketball_500 import fetch_recent as fetch_500_recent
except Exception:
    fetch_500_recent = None

HERE = os.path.dirname(os.path.abspath(__file__))
CALC_URL = ("https://webapi.sporttery.cn/gateway/uniform/basketball/"
            "getMatchCalculatorV1.qry?channel=c&clientCode=3001")
BONUS_URL = ("https://webapi.sporttery.cn/gateway/uniform/basketball/"
             "getFixedBonusV1.qry")
BK_PATH = os.path.join(HERE, "basketball.json")
BKH_PATH = os.path.join(HERE, "basketball_history.json")

KEEP_DAYS = 8            # 赛程/档案保留天数
BACKFILL_DELAY_H = 2.0   # 开赛多久后才查赛果
MAX_BONUS = 12           # 官方开奖接口单轮最多查询次数（WAF 概率拦截，别浪费预算）
QUIET = False


def log(msg):
    if not QUIET:
        print(msg, flush=True)


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _odds(raw):
    """官方玩法 dict → 精简赔率 dict（只留数字与盘口）。"""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k, v in raw.items():
        if k in ("h", "a", "l") or re.fullmatch(r"[lw]\d+", k):
            # 统一成 float：500 备用源给数值、官方给字符串，混用类型会让
            # 前端 toFixed / 数值比较出错
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = v
        elif k == "goalLine":
            out[k] = v
    return out or None


def fetch_calc():
    """抓官方在售赛程+四玩法精确赔率。"""
    if fetch is None:
        raise RuntimeError("fetch_daily.fetch 不可用")
    fb = fetch(CALC_URL, must_contain="matchInfoList")
    if not fb.get("success"):
        raise RuntimeError("篮球计算器接口失败: " + str(fb.get("errorMessage", "")))
    blocks = (fb.get("value") or {}).get("matchInfoList") or []
    matches = []
    for block in blocks:
        date = block.get("businessDate", "")
        for m in block.get("subMatchList") or []:
            mid = str(m.get("matchId", ""))
            if not mid:
                continue
            match_date = m.get("matchDate") or date
            match_time = (m.get("matchTime") or "")[:5]
            rec = {
                "id": mid,
                "num": m.get("matchNumStr", ""),
                "week": m.get("matchWeek", ""),
                "date": date,
                "kickoff": "%s %s" % (match_date, match_time),
                "time": match_time,
                "league": m.get("leagueAbbName", ""),
                "home": m.get("homeTeamAllName", ""),
                "away": m.get("awayTeamAllName", ""),
                "hr": (m.get("homeRank") or "").strip("[]"),
                "ar": (m.get("awayRank") or "").strip("[]"),
                "mnl": _odds(m.get("mnl")),
                "hdc": _odds(m.get("hdc")),
                "hilo": _odds(m.get("hilo")),
                "wnm": _odds(m.get("wnm")),
                "hasOdds": bool(m.get("mnl") or m.get("hdc") or m.get("hilo")),
                "_src": "sporttery",
                "_oddsApprox": [],
            }
            matches.append(rec)
    return matches


def fetch_bonus(mid):
    """按 matchId 查官方开奖结果（补充源，WAF 拦截时静默返回 None）。"""
    if fetch is None:
        return None
    try:
        fb = fetch(BONUS_URL + "?clientCode=3001&matchId=" + str(mid))
        v = fb.get("value") or {}
        mr = v.get("matchResult") or {}
        if (mr.get("poolStatus") or "") != "Payout":
            return None
        sec = mr.get("sectionsNo999") or ""
        g = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", sec)
        if not g:
            return None
        # 返回 dict（沿用 v1.7.29 起的既有约定，test_basketball_fetch.py 依赖）
        return {"home": int(g.group(1)), "away": int(g.group(2))}
    except Exception:
        return None


def _merge(official, fb500):
    """500 全量赛程打底 + 官方精确赔率覆盖（按 批次日+编号 匹配）。"""
    merged = {}
    for m in fb500:
        merged[(m.get("date", ""), m.get("num", ""))] = dict(m)
    covered = 0
    for m in official:
        key = (m.get("date", ""), m.get("num", ""))
        base = merged.get(key)
        if base is None:
            merged[key] = dict(m)
            covered += 1
            continue
        for pk in ("mnl", "hdc", "hilo", "wnm"):
            if m.get(pk):
                base[pk] = m[pk]
        base["id"] = m.get("id") or base.get("id")
        base["_src"] = "sporttery+500"
        base["_oddsApprox"] = []
        base["hasOdds"] = True
        covered += 1
    return list(merged.values()), covered


def _apply_result(rec, score, now):
    """写入赛果并判定让分/大小分结果（幂等）。"""
    rec["score"] = [int(score[0]), int(score[1])]
    rec["evaluated"] = True
    rec["resultAt"] = now.strftime("%Y-%m-%d %H:%M")
    hdc = rec.get("hdc") or {}
    gl = _line_num(hdc.get("goalLine"))
    if gl is not None:
        adj = score[0] + gl
        rec["hdcResult"] = "h" if adj > score[1] else ("a" if adj < score[1] else "d")
    hilo = rec.get("hilo") or {}
    tl = _line_num(hilo.get("goalLine"))
    if tl is not None:
        rec["hiloResult"] = "h" if (score[0] + score[1]) > tl else "l"


def main():
    global QUIET
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=6,
                    help="回溯最近 N 个销售批次日（默认 6）")
    ap.add_argument("--no-net", action="store_true",
                    help="不访问网络，仅用现有 basketball.json 重算档案")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_known_args()[0]
    QUIET = args.quiet
    t0 = time.time()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # ---- 1. 官方在售赛程（失败/过期都不致命）----
    official = []
    try:
        official = fetch_calc()
    except Exception as e:
        log("[篮球] 官方接口不可用（%s），切换 500.com 备用源" % str(e)[:80])
    off_dates = sorted({m.get("date", "") for m in official if m.get("date")})
    off_fresh = bool(off_dates) and off_dates[-1] >= (now - timedelta(days=1)).strftime("%Y-%m-%d")
    log("[篮球] 官方在售 %d 场（批次 %s，新鲜=%s）"
        % (len(official), ",".join(off_dates) or "无", off_fresh))

    # ---- 2. 500.com 备用源：无条件抓最近 N 批次（赛程 + 赛果）----
    fb500, failed_days = [], []
    if not args.no_net and fetch_500_recent is not None:
        try:
            fb500, failed_days = fetch_500_recent(args.backfill_days, now)
        except Exception as e:
            log("[篮球] 500 备用源异常: %r" % e)
    elif not args.no_net:
        log("[篮球] 500 备用源模块不可用")
    fb_scored = sum(1 for x in fb500 if x.get("score"))
    log("[篮球] 500 备用源近 %d 批次 %d 场（含赛果 %d 场，失败日期 %s）"
        % (args.backfill_days, len(fb500), fb_scored, ",".join(failed_days) or "无"))

    # 双源全空 → 保留旧数据，绝不抹空
    old = _load(BK_PATH, {})
    old_matches = old.get("matches") or []
    if not official and not fb500:
        log("[篮球] 双源均无数据，沿用上一轮 %d 场（不覆盖）" % len(old_matches))
        if not old_matches:
            return
        return

    matches, covered = _merge(official, fb500)
    if not matches and old_matches:
        log("[篮球] 合并后 0 场（疑似空响应），保留上一轮 %d 场" % len(old_matches))
        matches = old_matches

    # ---- 3. 合并历史档案 ----
    hist = _load(BKH_PATH, {})
    records = hist.get("records") or {}
    keep_from = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    fields = ("id", "num", "week", "date", "kickoff", "time", "league",
              "home", "away", "hr", "ar", "batchDate", "_src", "_oddsApprox")
    for x in matches:
        rid = x.get("id") or (x.get("date", "") + x.get("num", "") + x.get("home", ""))
        rec = records.get(rid) or {}
        rec.update({k: x[k] for k in fields if x.get(k) is not None})
        for pk in ("mnl", "hdc", "hilo", "wnm"):
            if x.get(pk):
                rec[pk] = x[pk]
        if x.get("score") and not rec.get("score"):
            _apply_result(rec, x["score"], now)
        records[rid] = rec
    records = {k: v for k, v in records.items()
               if (v.get("date") or "9999") >= keep_from}

    # ---- 4. 赛果补充：500 未出比分的已开赛场次，试官方开奖接口 ----
    bonus_done = 0
    for rec in records.values():
        if bonus_done >= MAX_BONUS:
            break
        if rec.get("score"):
            continue
        try:
            kick = datetime.strptime((rec.get("kickoff") or "")[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if (now - kick).total_seconds() < BACKFILL_DELAY_H * 3600:
            continue
        s = fetch_bonus(rec.get("id"))
        if s:
            _apply_result(rec, [s["home"], s["away"]], now)
            bonus_done += 1
        time.sleep(0.25)

    n_eval = sum(1 for r in records.values() if r.get("score"))
    n_upcoming = sum(1 for x in matches
                     if (x.get("kickoff") or "")[:16] > now.strftime("%Y-%m-%d %H:%M")
                     and not x.get("score"))

    # ---- 5. 落盘 ----
    batch_dates = sorted({x.get("date", "") for x in matches if x.get("date")})
    source = ("sporttery+500" if official and fb500 else
              ("sporttery" if official else "500.com"))
    _save(BKH_PATH, {"records": records,
                     "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S")})
    out = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "dataDate": batch_dates[-1] if batch_dates else "",
        "batchDates": batch_dates,
        "source": source,
        "officialCovered": covered,
        "upcomingCount": n_upcoming,
        "scoredCount": n_eval,
        "stale": batch_dates[-1] < (now - timedelta(days=1)).strftime("%Y-%m-%d")
                 if batch_dates else True,
        "matches": [x for x in matches
                    if (x.get("date") or "9999") >= keep_from],
    }
    _save(BK_PATH, out)
    log("[篮球] 写入 %d 场（未开赛 %d，批次 %s，源 %s，官方覆盖 %d）/ 档案 %d 条含赛果 %d 条，耗时 %.0fs"
        % (len(out["matches"]), n_upcoming, out["dataDate"], source, covered,
           len(records), n_eval, time.time() - t0))


def _line_num(s):
    """'+1.5' / '-3.5' / '223.5' → float。篮球盘口带正负号（主队受让为正）。"""
    if s is None:
        return None
    try:
        return float(str(s).replace("+", ""))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 隔离原则：篮球抓取失败绝不影响足球主流程——保留旧数据，exit 0
        print("[篮球] 抓取失败（保留旧数据，下轮重试）: %r" % e, file=sys.stderr)
        sys.exit(0)
