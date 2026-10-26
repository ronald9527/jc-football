#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球 AI 预测 + 历史统计（v1.7.44 新增，独立源，与足球零耦合）。

数据源（由 fetch_basketball.py 维护，绝不碰足球的 matches.json/history.json）：
  - basketball.json          在售赛程 + 四玩法赔率
  - basketball_history.json  赛后累积档案（含 score / hdcResult / hiloResult）

产出：
  - basketball_ai.json       当日四玩法预测（供 fetch_basketball_parlay.py 串关用）
  - basketball_stats.json    历史统计（联赛胜率 / 盘路命中 / 胜分差分布 / ROI）

设计原则（对齐足球独立源接入模式）：
  - 失败只跳过、exit 0，不拖垮 run_daily.sh 主流程（篮球隔离设计）
  - 与足球数据/模型零耦合：不读 matches.json / history.json / BUILT_DATA
  - 同指命中率沿用足球 n<5 护栏 → 样本薄时自动退化为市场共识（防 v1.7.43 反向虚高）
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BK_PATH = os.path.join(HERE, "basketball.json")
BKH_PATH = os.path.join(HERE, "basketball_history.json")
AI_PATH = os.path.join(HERE, "basketball_ai.json")
STATS_PATH = os.path.join(HERE, "basketball_stats.json")

# 市场共识参数（对标 fetch_daily._leg_prob：竞彩综合返奖率经验值；篮球首版沿用，待实测校准）
PAYBACK = 0.886
ALPHA_SHRINK = 0.60

# 胜分差区间边界（主胜分差，单位分）：l1[1,5] l2[6,10] l3[11,15] l4[16,20] l5[21,25] l6[26,+)
WNM_BOUNDS = [("l1", 1, 5), ("l2", 6, 10), ("l3", 11, 15),
              ("l4", 16, 20), ("l5", 21, 25), ("l6", 26, 10 ** 9)]

SAMPLE_WARN = "样本薄（仅数十场历史赛果），同指命中率统计仅供参考，预测以市场共识为主"


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _num(s):
    """解析排名串数字：'德篮联13' -> 13；'' -> None。"""
    if not s:
        return None
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else None


def _play_sides(play):
    """返回玩法支持的方向键（h/a/l/l1..l6）。"""
    if not isinstance(play, dict):
        return []
    sides = [s for s in ("h", "a", "l") if s in play]
    sides += [k for k in play if re.fullmatch(r"l\d+", k)]
    return sides


def _market_prob(play, side):
    """给定玩法赔率 dict 与 side，返回该边归一化市场隐含概率。"""
    sides = _play_sides(play)
    if side not in sides:
        return None
    inv = []
    for s in sides:
        o = _f(play.get(s))
        if o > 1:
            inv.append(1.0 / o)
    tot = sum(inv)
    if tot <= 0:
        return None
    o = _f(play.get(side))
    return (1.0 / o) / tot if o > 1 else None


def _leg_prob(o, pt=None):
    """市场共识 + alpha 收缩口径（对标 fetch_daily._leg_prob）。"""
    if not o or o <= 1:
        return None
    p_market = PAYBACK / o
    if pt is not None:
        p = p_market + (pt / 100.0 - p_market) * ALPHA_SHRINK
        p = min(max(p, p_market - 0.06), p_market + 0.06)
    else:
        p = p_market
    return min(max(p, 0.01), 0.95)


# ----------------------------------------------------------------------------
# 历史同指命中率（四玩法通用，n<5 护栏）
# ----------------------------------------------------------------------------
def calc_bk_same_odds_signal(records, play_key, direction, goal=None, tol=0.12):
    """篮球历史同指命中率。

    records   : basketball_history.json 的 records（dict: id->rec）
    play_key  : 'mnl' | 'hdc' | 'hilo' | 'wnm'
    direction : mnl/hdc -> 'h'/'a'；hilo -> 'h'(大)/'l'(小)；wnm -> 'l1'..'l6'
    goal      : hdc/hilo 需匹配盘口（goalLine）；mnl/wnm 传 None
    返回 (hit_rate_or_None, n)：n<5 返回 (None, n) —— 薄样本护栏
    """
    hits = 0
    n = 0
    for rec in records.values():
        sc = rec.get("score")
        if not isinstance(sc, list) or len(sc) != 2:
            continue
        home, away = sc[0], sc[1]
        play = rec.get(play_key)
        if not isinstance(play, dict):
            continue
        if play_key in ("hdc", "hilo"):
            gl = str(play.get("goalLine", "")).strip()
            if goal is None or gl != str(goal).strip():
                continue
        if play_key == "mnl":
            actual = "h" if home > away else "a"
            hit = (actual == direction)
        elif play_key == "hdc":
            hr = rec.get("hdcResult")
            if hr not in ("h", "a", "d"):
                adj = home + _f(play.get("goalLine"))
                hr = "h" if adj > away else ("a" if adj < away else "d")
            if hr == "d":
                continue  # 走水不计
            hit = (hr == direction)
        elif play_key == "hilo":
            ir = rec.get("hiloResult")
            if ir not in ("h", "l"):
                ir = "h" if (home + away) > _f(play.get("goalLine")) else "l"
            hit = (ir == direction)
        elif play_key == "wnm":
            diff = home - away
            if diff <= 0:
                continue  # 主负，所有胜分差区间不中，不入桶
            actual_lx = next((lx for lx, lo, hi in WNM_BOUNDS if lo <= diff <= hi), None)
            if actual_lx is None:
                continue
            hit = (actual_lx == direction)
        else:
            continue
        n += 1
        if hit:
            hits += 1
    if n < 5:
        return (None, n)
    return (round(hits / n * 100, 1), n)


# ----------------------------------------------------------------------------
# 选腿评分（对标足球 parlay_canon_score：信心主导 + 同指分段修正）
# ----------------------------------------------------------------------------
def bk_canon_score(opt):
    """篮球选腿评分：信心度主导；同指仅 ≥65 加成，45-60 反向惩罚，<45 重罚。"""
    conf = opt.get("confidence") or 0
    so, n_ = opt.get("sameOddsHit"), opt.get("sameOddsN") or 0
    base = conf / 100.0
    so_adj = 0.0
    if so is not None and n_ >= 10:
        if so >= 65:
            so_adj = min(0.08, (so - 65) / 100.0)
        elif so >= 45:
            so_adj = -0.10
        else:
            so_adj = -0.15
    o = opt.get("odds") or 0
    ev = 0.0
    if o > 1:
        p = _leg_prob(o, opt.get("pTrue"))
        if p:
            ev = min(max(p, 0.01), 0.95) * o - 1.0
    return base + so_adj + 0.10 * ev + 0.001 * (opt.get("fiveDim") or 0)


def _rule_confidence(play_key, side, odds, p_market):
    """简化规则信心：以市场隐含概率为主，胜分差区间玩法天然压低。"""
    if not p_market:
        base = 60.0
    else:
        base = p_market * 100 * 0.95
    if play_key == "wnm":
        base = min(base, 70.0)  # 胜分差区间博弈，信心上限更低
    return round(min(88.0, max(55.0, base)), 1)


# ----------------------------------------------------------------------------
# 单场单玩法预测
# ----------------------------------------------------------------------------
def _predict_play(play_key, play, records):
    if not isinstance(play, dict):
        return None
    sides = {"mnl": ["h", "a"], "hdc": ["h", "a"],
             "hilo": ["h", "l"], "wnm": ["l1", "l2", "l3", "l4", "l5", "l6"]}.get(play_key)
    if not sides:
        return None
    goal = play.get("goalLine") if play_key in ("hdc", "hilo") else None
    opts = []
    for s in sides:
        o = _f(play.get(s))
        if o <= 1:
            continue
        pm = _market_prob(play, s)
        so, sn = calc_bk_same_odds_signal(records, play_key, s, goal=goal)
        pTrue = round(pm * 100, 1) if pm else None
        conf = _rule_confidence(play_key, s, o, pm)
        opts.append({"dir": s, "odds": o, "pMarket": pm, "confidence": conf,
                     "sameOddsHit": so, "sameOddsN": sn, "pTrue": pTrue})
    if not opts:
        return None
    for op in opts:
        op["canon"] = bk_canon_score(op)
    best = max(opts, key=lambda x: x["canon"])
    alts = [{"dir": o["dir"], "odds": o["odds"], "confidence": o["confidence"],
             "sameOddsHit": o["sameOddsHit"], "sameOddsN": o["sameOddsN"]}
            for o in opts if o is not best]
    return {
        "dir": best["dir"], "odds": best["odds"], "confidence": best["confidence"],
        "sameOddsHit": best["sameOddsHit"], "sameOddsN": best["sameOddsN"],
        "pTrue": best["pTrue"], "canon": round(best["canon"], 3),
        "alts": alts,
    }


def gen_bk_prediction(matches, records):
    """对每场在售比赛产出四玩法预测；每场取 canon 最高的玩法方向作为该场唯一候选。"""
    preds = []
    for m in matches:
        hr_n = _num(m.get("hr"))
        ar_n = _num(m.get("ar"))
        rank_bonus = 0.0
        if hr_n is not None and ar_n is not None and hr_n > 0 and ar_n > 0:
            diff = ar_n - hr_n  # 正=主排名更前（更强）
            rank_bonus = min(8.0, max(-8.0, diff * 0.6))
        plays = {}
        for pk in ("mnl", "hdc", "hilo", "wnm"):
            p = _predict_play(pk, m.get(pk), records)
            if p:
                p["confidence"] = round(min(88.0, max(55.0, p["confidence"] + rank_bonus)), 1)
                p["canon"] = round(bk_canon_score(p), 3)
                plays[pk] = p
        if not plays:
            continue
        # 单场单意见：取 canon 最高的玩法方向
        top_key = max(plays, key=lambda k: plays[k]["canon"])
        preds.append({
            "id": m.get("id"), "num": m.get("num"), "league": m.get("league"),
            "home": m.get("home"), "away": m.get("away"), "kickoff": m.get("kickoff"),
            "hr": m.get("hr"), "ar": m.get("ar"),
            "plays": plays, "topPlay": top_key,
        })
    return preds


# ----------------------------------------------------------------------------
# 历史统计
# ----------------------------------------------------------------------------
def compute_bk_stats(records):
    """基于赛后档案计算历史统计（联赛胜率/盘路/胜分差分布/ROI）。"""
    scored = [r for r in records.values()
              if isinstance(r.get("score"), list) and len(r["score"]) == 2]
    total = len(records)
    m = len(scored)

    leagues = {}
    hdc_counter = Counter()
    hilo_counter = Counter()
    wnm_dist = Counter()
    home_wins = 0
    roi = defaultdict(lambda: {"bet": 0, "profit": 0.0})

    for r in scored:
        home, away = r["score"][0], r["score"][1]
        lg = r.get("league", "未知")
        lg_d = leagues.setdefault(lg, {"n": 0, "home_win": 0, "hdc_h": 0, "hdc_n": 0,
                                       "hilo_over": 0, "hilo_n": 0, "totals": []})
        lg_d["n"] += 1
        if home > away:
            home_wins += 1
            lg_d["home_win"] += 1
        lg_d["totals"].append(home + away)

        # 让分盘路
        hdc = r.get("hdc")
        hr = r.get("hdcResult")
        if isinstance(hdc, dict) and hr in ("h", "a", "d"):
            hdc_counter[hr] += 1
            if hr != "d":
                lg_d["hdc_n"] += 1
                if hr == "h":
                    lg_d["hdc_h"] += 1
            # ROI：买市场概率高的一方
            _roi_accum(roi, "hdc", hdc, hr)

        # 大小分盘路
        hilo = r.get("hilo")
        ir = r.get("hiloResult")
        if isinstance(hilo, dict) and ir in ("h", "l"):
            hilo_counter[ir] += 1
            lg_d["hilo_n"] += 1
            if ir == "h":
                lg_d["hilo_over"] += 1
            _roi_accum(roi, "hilo", hilo, ir)

        # 胜负 ROI
        mnl = r.get("mnl")
        if isinstance(mnl, dict):
            res = "h" if home > away else "a"
            _roi_accum(roi, "mnl", mnl, res)

        # 胜分差分布
        diff = home - away
        if diff > 0:
            lx = next((x for x, lo, hi in WNM_BOUNDS if lo <= diff <= hi), None)
            if lx:
                wnm_dist[lx] += 1
                _roi_accum_wnm(roi, r.get("wnm"), lx)

    # 整理 league 统计
    league_out = {}
    for lg, d in leagues.items():
        n = d["n"]
        league_out[lg] = {
            "n": n,
            "home_win_rate": round(d["home_win"] / n * 100, 1) if n else 0,
            "hdc_home_hit_rate": round(d["hdc_h"] / d["hdc_n"] * 100, 1) if d["hdc_n"] else None,
            "hilo_over_rate": round(d["hilo_over"] / d["hilo_n"] * 100, 1) if d["hilo_n"] else None,
            "avg_total": round(sum(d["totals"]) / len(d["totals"]), 1) if d["totals"] else None,
        }

    hdc_total = sum(hdc_counter.values())
    hilo_total = sum(hilo_counter.values())
    roi_out = {}
    for k, v in roi.items():
        bet = v["bet"]
        roi_out[k] = {
            "bet": bet,
            "profit": round(v["profit"], 1),
            "roi": round(v["profit"] / bet * 100, 1) if bet else 0,
        }

    return {
        "sample": {"total": total, "scored": m, "note": SAMPLE_WARN},
        "leagues": league_out,
        "hdc_overall": {
            "h": hdc_counter.get("h", 0), "a": hdc_counter.get("a", 0),
            "d": hdc_counter.get("d", 0), "home_hit_rate":
                round(hdc_counter.get("h", 0) / (hdc_counter.get("h", 0) + hdc_counter.get("a", 0)) * 100, 1)
                if (hdc_counter.get("h", 0) + hdc_counter.get("a", 0)) else None,
        },
        "hilo_overall": {
            "over": hilo_counter.get("h", 0), "under": hilo_counter.get("l", 0),
            "over_rate":
                round(hilo_counter.get("h", 0) / (hilo_counter.get("h", 0) + hilo_counter.get("l", 0)) * 100, 1)
                if (hilo_counter.get("h", 0) + hilo_counter.get("l", 0)) else None,
        },
        "wnm_dist": {lx: wnm_dist.get(lx, 0) for lx, _, _ in WNM_BOUNDS},
        "home_advantage": {
            "home_win_rate": round(home_wins / m * 100, 1) if m else 0,
        },
        "roi": roi_out,
    }


def _roi_accum(roi, key, play, result):
    """买该玩法「市场概率最高方」，命中 +odds-1，不中 -1。"""
    if not isinstance(play, dict):
        return
    sides = _play_sides(play)
    best, best_p = None, -1
    for s in sides:
        p = _market_prob(play, s)
        if p is not None and p > best_p:
            best, best_p = s, p
    if best is None:
        return
    o = _f(play.get(best))
    roi[key]["bet"] += 1
    roi[key]["profit"] += (o - 1) if result == best else -1.0


def _roi_accum_wnm(roi, wnm, actual_lx):
    if not isinstance(wnm, dict):
        return
    best, best_p = None, -1
    for s in ("l1", "l2", "l3", "l4", "l5", "l6"):
        p = _market_prob(wnm, s)
        if p is not None and p > best_p:
            best, best_p = s, p
    if best is None:
        return
    o = _f(wnm.get(best))
    roi["wnm"]["bet"] += 1
    roi["wnm"]["profit"] += (o - 1) if actual_lx == best else -1.0


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    bk = _load(BK_PATH, {"matches": []})
    hist = _load(BKH_PATH, {"records": {}})
    records = hist.get("records") or {}
    matches = bk.get("matches") or []

    preds = gen_bk_prediction(matches, records)
    ai = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": SAMPLE_WARN,
        "predictions": preds,
    }
    _save(AI_PATH, ai)

    stats = compute_bk_stats(records)
    stats["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save(STATS_PATH, stats)

    print("[篮球AI] 预测 %d 场（四玩法）/ 历史统计基于 %d 条档案（%d 有赛果）"
          % (len(preds), len(records), sum(1 for r in records.values() if r.get("score"))))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[篮球AI] 异常跳过（隔离设计）:", e, file=sys.stderr)
        sys.exit(0)
