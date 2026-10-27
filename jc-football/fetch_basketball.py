#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球数据抓取（v1.7.29 新增「竞彩篮球」Tab）。

数据源与足球完全隔离——只写 basketball.json / basketball_history.json，
绝不触碰足球的 matches.json / history.json / rules_config.json。

数据源（竞彩官方 webapi，与足球同网关、basketball 路径）：
  - 赛程+赔率：basketball/getMatchCalculatorV1.qry（在售比赛，含四种玩法赔率）
  - 赛果回填：basketball/getFixedBonusV1.qry?matchId=（开奖后 matchResult.poolStatus
    = "Payout"，sectionsNo999 形如 "93:116" = 主队:客队比分）

玩法（与足球的胜平负/让球/总进球/半全场不同，篮球是四玩法）：
  - mnl  胜负        {h: 主胜赔, a: 客胜赔}
  - hdc  让分胜负    {goalLine: 让分(代数值, 主队加), h: 让分主胜赔, a: 让分客胜赔}
  - hilo 大小分      {goalLine: 总分线, h: 大分赔, l: 小分赔}
  - wnm  胜分差      {l1..l6: 各胜分区间赔率（1-5/6-10/11-15/16-20/21-25/26+ 分）}

编号空间：篮球从 301 起（周二301…），与足球 001 起并行，互不冲突。

设计原则（对齐足球独立源接入模式，参考 fetch_scores.py / fetch_football_data.py）：
  - 抓取失败时保留旧数据并 exit 0，不拖垮 run_daily.sh 主流程（足球不受影响）
  - WAF 重试直接复用 fetch_daily.fetch()（同一网关、同一套反爬特征）
  - 单轮回填设上限，保护定时任务 600s 总预算
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

try:
    from fetch_daily import fetch          # 复用足球同款 WAF 重试（同网关）
except Exception:                          # 独立运行兜底（理论上不会发生）
    fetch = None

HERE = os.path.dirname(os.path.abspath(__file__))
CALC_URL = ("https://webapi.sporttery.cn/gateway/uniform/basketball/"
            "getMatchCalculatorV1.qry?channel=c&clientCode=3001")
BONUS_URL = ("https://webapi.sporttery.cn/gateway/uniform/basketball/"
             "getFixedBonusV1.qry")
BK_PATH = os.path.join(HERE, "basketball.json")           # 当日+近期赛程赔率
BKH_PATH = os.path.join(HERE, "basketball_history.json")  # 赛后累积（含赛果）

KEEP_DAYS = 7            # basketball.json 保留天数（控制注入页面体积）
BACKFILL_DELAY_H = 2.5   # 开赛多久后才查赛果（等官方开奖）
MAX_BACKFILL = 30        # 单轮赛果回填上限（保护时间预算）


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
        if k in ("h", "a", "l") or re.fullmatch(r"l\d+", k):
            out[k] = v
        elif k == "goalLine":
            out[k] = v
    return out or None


def fetch_calc():
    """抓在售赛程+四玩法赔率 → matches 列表（结构与足球 matches.json 对齐的轻量版）。"""
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
            }
            matches.append(rec)
    return matches


def fetch_bonus(mid):
    """按 matchId 查官方开奖结果。已开奖返回 {"home": 主队分, "away": 客队分}，否则 None。"""
    if fetch is None:
        return None
    try:
        fb = fetch(BONUS_URL + "?clientCode=3001&matchId=" + str(mid))
        v = fb.get("value") or {}
        mr = v.get("matchResult") or {}
        if (mr.get("poolStatus") or "") != "Payout":
            return None                      # 未开奖 / 已取消
        sec = mr.get("sectionsNo999") or ""  # "93:116"
        g = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", sec)
        if not g:
            return None
        return {"home": int(g.group(1)), "away": int(g.group(2))}
    except Exception:
        return None


def main():
    t0 = time.time()
    now = datetime.now()

    # ---- 1. 在售赛程+赔率 ----
    matches = fetch_calc()
    # v1.7.46 加固：接口偶发空响应（网关波动/限流）会返回 0 场但不抛异常，
    # 若直接落盘会把在售赛程抹空。旧数据非空时退回上一轮在售，避免「误删」。
    old = _load(BK_PATH, {})
    old_matches = old.get("matches") or []
    if not matches and old_matches:
        print("[篮球] 接口返回 0 场（疑似空响应/网关波动），保留上一轮 %d 场在售，不覆盖"
              % len(old_matches), flush=True)
        matches = old_matches
    print("[篮球] 在售赛程 %d 场（含赔率 %d 场）"
          % (len(matches), sum(1 for x in matches if x.get("hasOdds"))), flush=True)

    # ---- 2. 合并历史档案（已滚出在售列表的近期比赛，含赛果）----
    hist = _load(BKH_PATH, {})
    records = hist.get("records") or {}
    keep_from = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    # 在售比赛先进档案（无赛果的待回填记录也要在，否则回填后无处写）
    for x in matches:
        rec = records.get(x["id"]) or {}
        rec.update({k: x[k] for k in ("id", "num", "week", "date", "kickoff",
                                      "time", "league", "home", "away", "hr", "ar")})
        rec.setdefault("mnl", x.get("mnl"))
        rec.setdefault("hdc", x.get("hdc"))
        rec.setdefault("hilo", x.get("hilo"))
        rec.setdefault("wnm", x.get("wnm"))
        records[x["id"]] = rec
    # 清理过期
    records = {k: v for k, v in records.items()
               if (v.get("date") or "9999") >= keep_from}
    hist = {"records": records, "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S")}

    # ---- 3. 赛果回填（开赛 2.5h 后查官方开奖接口；已回填跳过）----
    cands = []
    for rec in records.values():
        if rec.get("score"):
            continue
        try:
            kick = datetime.strptime((rec.get("kickoff") or "")[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if (now - kick).total_seconds() > BACKFILL_DELAY_H * 3600:
            cands.append((kick, rec))
    cands.sort(key=lambda t: t[0])          # 开球最早的先回填（防饿死，同足球 v1.7.28 设计）
    done = 0
    for kick, rec in cands[:MAX_BACKFILL]:
        r = fetch_bonus(rec["id"])
        if r:
            rec["score"] = [r["home"], r["away"]]
            rec["evaluated"] = True
            rec["resultAt"] = now.strftime("%Y-%m-%d %H:%M")
            # 让分胜负结果判定（goalLine 为代数值：主队实际分 + goalLine 后比较）
            hdc = rec.get("hdc") or {}
            gl = _line_num(hdc.get("goalLine"))
            if gl is not None:
                adj = r["home"] + gl
                rec["hdcResult"] = "h" if adj > r["away"] else ("a" if adj < r["away"] else "d")
            # 大小分结果判定
            hilo = rec.get("hilo") or {}
            tl = _line_num(hilo.get("goalLine"))
            if tl is not None:
                rec["hiloResult"] = "h" if (r["home"] + r["away"]) > tl else "l"
            done += 1
        time.sleep(0.4)
    if cands:
        print("[篮球] 赛果回填候选 %d 场，本轮补抓 %d 场，剩余 %d 场下轮继续"
              % (len(cands), done, len(cands) - done), flush=True)

    # ---- 4. 落盘 ----
    _save(BKH_PATH, hist)
    out = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "matches": [x for x in matches
                    if (x.get("date") or "9999") >= keep_from],
    }
    _save(BK_PATH, out)
    n_eval = sum(1 for r in records.values() if r.get("score"))
    print("[篮球] 已写入 %s（%d 场）/ %s（档案 %d 条，含赛果 %d 条），耗时 %.0fs"
          % (os.path.basename(BK_PATH), len(out["matches"]),
             os.path.basename(BKH_PATH), len(records), n_eval, time.time() - t0))


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
