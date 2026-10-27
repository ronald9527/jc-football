#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球串关（v1.7.44 新增，独立源，与足球零耦合）。

候选池 = 当日 basketball_ai.json 预测（每场 topPlay 玩法方向，单场单意见）
三档   = 稳健2串 / 均衡3串 / 精锐4串（参数对标 v1.7.43 足球，so_floor 60/精锐65）
落盘   = basketball_parlay_history.json（结构对标 parlay_history.json，含 realEv/engineVer/snapshot）
结算   = check 已开赛 pending，回填 legHit/result（补 wnmResult 胜分差区间判定）
"""

import json
import os
import re
import sys
from datetime import datetime
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
AI_PATH = os.path.join(HERE, "basketball_ai.json")
BK_PATH = os.path.join(HERE, "basketball.json")
BKH_PATH = os.path.join(HERE, "basketball_history.json")
PARLAY_PATH = os.path.join(HERE, "basketball_parlay_history.json")

PAYBACK = 0.886
ALPHA_SHRINK = 0.60
ENGINE_VERSION = "v1.7.44"

# 篮球串关参数（适配篮球赔率尺度：赔率天然比足球低、信心体系简化、同指率样本薄）。
# so_floor=0：样本薄时同指统计不可信，不在 build_tier 硬剔除，改由 canon 软惩罚处理。
# (key, 档名, ns, min_odds, min_total, max_total, min_conf, min_joint, min_ev, mode, max_leg, so_floor)
TIERS_BK = [
    ("bk2", "稳健2串1", 2, 1.20, 1.60, 3.50, 55.0, 0.18, -0.10, "hit", 4.0, 0),
    ("bk3", "均衡3串1", 3, 1.20, 3.00, 9.00, 55.0, 0.08, -0.15, "ev", 4.0, 0),
    ("bk4", "精锐4串1", 4, 1.20, 4.50, 18.0, 55.0, 0.06, -0.10, "hit", 3.2, 0),
]

PLAY_NAME = {"mnl": "胜负", "hdc": "让分胜负", "hilo": "大小分", "wnm": "胜分差"}
WNM_BOUNDS = [("l1", 1, 5), ("l2", 6, 10), ("l3", 11, 15),
              ("l4", 16, 20), ("l5", 21, 25), ("l6", 26, 10 ** 9)]


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


def _leg_prob(o, pt=None):
    if not o or o <= 1:
        return None
    p_market = PAYBACK / o
    if pt is not None:
        p = p_market + (pt / 100.0 - p_market) * ALPHA_SHRINK
        p = min(max(p, p_market - 0.06), p_market + 0.06)
    else:
        p = p_market
    return min(max(p, 0.01), 0.95)


def _kickoff_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M")
        except Exception:
            return None


def _leg_label(play_key, play, goal_line):
    d = play["dir"]
    if play_key == "mnl":
        return "主胜" if d == "h" else "客胜"
    if play_key == "hdc":
        return "让分%s胜" % ("主" if d == "h" else "客")
    if play_key == "hilo":
        return "大分" if d == "h" else "小分"
    if play_key == "wnm":
        return "主胜%s区间" % d
    return d


def _leg_play_desc(play_key, goal_line):
    if play_key == "hdc":
        return "让分(%s)" % goal_line
    if play_key == "hilo":
        return "总分线(%s)" % goal_line
    return PLAY_NAME.get(play_key, play_key)


# ----------------------------------------------------------------------------
def gen_bk_candidates(preds, matches_map):
    """每场取 topPlay 玩法方向作为该场唯一候选（单场单意见）。"""
    cands = []
    for p in preds:
        tp = p.get("topPlay")
        pl = (p.get("plays") or {}).get(tp)
        if not pl:
            continue
        m = matches_map.get(p.get("id")) or {}
        goal = m.get(tp, {}).get("goalLine") if isinstance(m.get(tp), dict) else None
        cands.append({
            "id": p.get("id"), "num": p.get("num"), "league": p.get("league"),
            "home": p.get("home"), "away": p.get("away"), "kickoff": p.get("kickoff"),
            "play": tp, "playKey": tp, "dir": pl["dir"],
            "label": _leg_label(tp, pl, goal),
            "playDesc": _leg_play_desc(tp, goal),
            "goalLine": goal,
            "odds": pl["odds"], "confidence": pl["confidence"],
            "sameOddsHit": pl.get("sameOddsHit"), "sameOddsN": pl.get("sameOddsN"),
            "pTrue": pl.get("pTrue"), "canon": pl.get("canon"),
        })
    return cands


def _combo_hit(combo):
    """同指仅 ≥65 采信地面值，否则回退市场共识口径。"""
    ch = 1.0
    for c in combo:
        so, n_ = c.get("sameOddsHit"), c.get("sameOddsN") or 0
        leg_p = (so / 100.0) if (so is not None and n_ >= 10 and so >= 65) else _leg_prob(c["odds"], c.get("pTrue"))
        ch *= leg_p if leg_p else 0.01
    return ch


def _build_tier(tier, candidates):
    (key, name, ns, min_odds, min_total, max_total, min_conf,
     min_joint, min_ev, mode, max_leg, so_floor) = tier
    best_pass, best_fb = None, None
    for combo in combinations(candidates, ns):
        total_odds = 1.0
        joint_p = 1.0
        ok = True
        for c in combo:
            o = c["odds"]
            total_odds *= o
            lp = _leg_prob(o, c.get("pTrue"))
            if lp:
                joint_p *= lp
            # 单腿约束
            if o < min_odds or o > max_leg:
                ok = False
                break
            if (c.get("confidence") or 0) < min_conf:
                ok = False
                break
        if not ok:
            continue
        if total_odds < min_total or total_odds > max_total:
            continue
        if joint_p < min_joint:
            continue
        # 同指硬门槛（软腿剔除，全档零容忍）
        soft = sum(1 for c in combo
                   if c.get("sameOddsHit") is not None
                   and (c.get("sameOddsN") or 0) >= 10
                   and c.get("sameOddsHit") < so_floor)
        if soft > 0:
            continue
        combo_hit = _combo_hit(combo)
        real_ev = combo_hit * total_odds - 1.0
        ev = joint_p * total_odds - 1.0
        score = combo_hit * 100 + 0.01 * real_ev
        tier_obj = {
            "type": name, "tier": key, "tierDesc": name,
            "legs": [dict(c) for c in combo],
            "totalOdds": round(total_odds, 2),
            "avgConfidence": round(sum(c["confidence"] for c in combo) / ns, 1),
            "jointProb": round(joint_p, 4),
            "realHit": round(combo_hit, 4),
            "ev": round(ev, 4),
            "realEv": round(real_ev, 4),
            "recommended": real_ev >= -0.02,
            "score": round(score, 3),
        }
        if real_ev >= min_ev:
            if best_pass is None or score > best_pass["score"]:
                best_pass = tier_obj
        if best_fb is None or score > best_fb["score"]:
            best_fb = tier_obj
    return best_pass or best_fb


def gen_bk_parlay(preds, matches_map):
    candidates = gen_bk_candidates(preds, matches_map)
    safe = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candidateCount": len(candidates),
        "candidatePool": candidates,
        "bk2": None, "bk3": None, "bk4": None,
        "strategy": (
            "篮球分层串关三档（竞彩篮球混合过关：胜负/让分胜负/大小分/胜分差，"
            "每场只选一种玩法，符合竞彩规则）：单场单意见（v1.7.44）——每场比赛先用统一评分"
            "（信心度主导，同指命中率仅≥65%才在评分中加成，45-60区间反向软惩罚）选出唯一玩法方向，"
            "三档只在该候选池内组合；样本薄（仅数十场历史赛果），同指统计仅供参考，"
            "不硬过滤，预测以市场共识+alpha收缩为主；各档拒绝极端赔率（单腿≤4.0，精锐≤3.2）。"
            "注意：EV 为负的档位长期必亏，凑不齐的档位留空不硬凑"
        ),
    }
    for tier in TIERS_BK:
        key = tier[0]
        safe[key] = _build_tier(tier, candidates)
    return safe


# ----------------------------------------------------------------------------
def _leg_hit(leg, score):
    """返回 (legHit_bool_or_None_for_走水, hit_str)。"""
    home, away = score[0], score[1]
    pk, d = leg.get("play"), leg.get("dir")
    if pk == "mnl":
        actual = "h" if home > away else "a"
        return (actual == d, "hit" if actual == d else "miss")
    if pk == "hdc":
        m = re.search(r"([-+]?\d+(?:\.\d+)?)", leg.get("playDesc", ""))
        gl = float(m.group(1)) if m else 0.0
        adj = home + gl
        if adj > away:
            actual = "h"
        elif adj < away:
            actual = "a"
        else:
            return (None, "draw")  # 走水
        return (actual == d, "hit" if actual == d else "miss")
    if pk == "hilo":
        m = re.search(r"([-+]?\d+(?:\.\d+)?)", leg.get("playDesc", ""))
        line = float(m.group(1)) if m else 0.0
        over = (home + away) > line
        actual = "h" if over else "l"
        return (actual == d, "hit" if actual == d else "miss")
    if pk == "wnm":
        diff = home - away
        if diff <= 0:
            return (False, "miss")  # 主负，所有区间不中
        lx = next((x for x, lo, hi in WNM_BOUNDS if lo <= diff <= hi), None)
        return (lx == d, "hit" if lx == d else "miss")
    return (False, "miss")


def save_parlay_history(safe, today):
    """落盘 basketball_parlay_history.json，结构对标 parlay_history.json。"""
    hist = _load(PARLAY_PATH, {"records": []})
    records = hist.get("records") or []
    # 今日已有 pending 同 type 的，移除（待重生）
    existing_today = [r for r in records
                      if r.get("date") == today and r.get("result") == "pending"]
    keep = [r for r in records if not (r.get("date") == today and r.get("result") == "pending")]

    new_recs = []
    for key in ("bk2", "bk3", "bk4"):
        t = safe.get(key)
        if not t:
            continue  # 留空不硬凑
        legs = []
        for c in t["legs"]:
            legs.append({
                "id": c.get("id"), "num": c["num"], "league": c["league"],
                "home": c["home"], "away": c["away"],
                "play": c["play"], "playKey": c["playKey"], "dir": c["dir"],
                "label": c["label"], "playDesc": c["playDesc"],
                "goalLine": c.get("goalLine"), "odds": c["odds"],
                "kickoff": c.get("kickoff"),
                "confidence": c["confidence"], "sameOddsHit": c.get("sameOddsHit"),
                "sameOddsN": c.get("sameOddsN"), "pTrue": c.get("pTrue"),
                "canon": c.get("canon"),
            })
        rec = {
            "date": today, "type": t["type"], "tier": key,
            "totalOdds": t["totalOdds"], "avgConfidence": t["avgConfidence"],
            "legs": legs, "recommended": bool(t["recommended"]),
            "ev": t["ev"], "realEv": t["realEv"], "result": "pending",
            "engineVer": ENGINE_VERSION,
            "savedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "snapshot": t,
        }
        new_recs.append(rec)

    records = keep + new_recs
    _recalc(hist, records)
    _save(PARLAY_PATH, hist)
    return hist


def _recalc(hist, records):
    hit = sum(1 for r in records if r.get("result") == "hit")
    miss = sum(1 for r in records if r.get("result") == "miss")
    pend = sum(1 for r in records if r.get("result") == "pending")
    judged = hit + miss
    hist["records"] = records
    hist["total"] = len(records)
    hist["hit"] = hit
    hist["miss"] = miss
    hist["pending"] = pend
    hist["hitRate"] = round(hit / judged * 100, 1) if judged else 0
    # 近 7 日（按 date）命中率
    dates = sorted({r.get("date") for r in records if r.get("date")}, reverse=True)[:7]
    recent = [r for r in records if r.get("date") in dates and r.get("result") in ("hit", "miss")]
    rj = len(recent)
    rh = sum(1 for r in recent if r.get("result") == "hit")
    hist["recent7Rate"] = round(rh / rj * 100, 1) if rj else 0
    hist["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _backfill_ids(hist, bk_records):
    """回填历史记录 leg 缺失的 id/kickoff（早期版本 save_parlay_history 漏写，
    导致 check_basketball_parlay 永远按 id=None 查不到赛果、也按 kickoff=None 被门控跳过）。
    按 (竞彩编号, 主, 客, 联赛) 唯一匹配 basketball_history 档案。"""
    by_key = {}
    for rid, r in bk_records.items():
        key = (r.get("num"), r.get("home"), r.get("away"), r.get("league"))
        by_key.setdefault(key, rid)
    changed = 0
    for rec in hist.get("records", []):
        for leg in rec.get("legs", []):
            if leg.get("id"):
                continue
            key = (leg.get("num"), leg.get("home"), leg.get("away"), leg.get("league"))
            rid = by_key.get(key)
            if rid and rid in bk_records:
                src = bk_records[rid]
                leg["id"] = rid
                leg["kickoff"] = src.get("kickoff")
                changed += 1
    return changed


def _backfill_goal_lines(hist):
    """早期记录 leg 无 goalLine，从 playDesc 解析补写（home 视角盘口，
    与候选 goal 同口径：负=主让/正=客让），让前端统一用 leg.goalLine 渲染盘口。"""
    import re as _re
    changed = 0
    for rec in hist.get("records", []):
        for leg in rec.get("legs", []):
            if leg.get("goalLine"):
                continue
            m = _re.search(r"([-+]?\d+(?:\.\d+)?)", leg.get("playDesc", ""))
            if m:
                leg["goalLine"] = m.group(1)
                changed += 1
    return changed


def check_basketball_parlay():
    """结算已开赛、仍 pending 的记录，回填 legHit/result。"""
    hist = _load(PARLAY_PATH, {"records": []})
    records = hist.get("records") or []
    bkh = _load(BKH_PATH, {"records": {}})
    bk_records = bkh.get("records") or {}
    # 先回填早期记录缺失的 id/kickoff，否则永远无法结算（v1.7.46 修复）
    _backfill_ids(hist, bk_records)
    # 回填早期记录缺失的 goalLine（盘口展示用，v1.7.49）
    gl_changed = _backfill_goal_lines(hist)
    now = datetime.now()
    changed = 0
    for r in records:
        if r.get("result") != "pending":
            continue
        legs = r.get("legs") or []
        kd = _kickoff_dt(legs[0].get("kickoff")) if legs else None
        # 用首腿 kickoff 判断（记录本身无顶层 kickoff，早期版本因此恒被跳过）
        if kd is None or kd > now:
            continue
        # 取该记录任一腿的比赛 score（同场同 id）
        score = None
        for leg in r["legs"]:
            rec = bk_records.get(leg.get("id"))
            if rec and isinstance(rec.get("score"), list) and len(rec["score"]) == 2:
                score = rec["score"]
                break
        if score is None:
            continue
        all_hit = True
        for leg in r["legs"]:
            leg_hit, leg_str = _leg_hit(leg, score)
            if leg_hit is None:
                leg_hit = False  # 走水按不中处理（简化）
            leg["legHit"] = leg_hit
            leg["hit"] = leg_str
            leg["actualScore"] = "%d:%d" % (score[0], score[1])
            if not leg_hit:
                all_hit = False
        r["result"] = "hit" if all_hit else "miss"
        r["checkedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        changed += 1
    if changed or gl_changed:
        _recalc(hist, records)
        _save(PARLAY_PATH, hist)
    return changed


# ----------------------------------------------------------------------------
def main():
    ai = _load(AI_PATH, {"predictions": []})
    preds = ai.get("predictions") or []
    bk = _load(BK_PATH, {"matches": []})
    matches_map = {m.get("id"): m for m in (bk.get("matches") or [])}

    safe = gen_bk_parlay(preds, matches_map)
    today = (safe["generatedAt"])[:10]
    hist = save_parlay_history(safe, today)
    checked = check_basketball_parlay()

    print("[篮球串关] 候选 %d 场 | 三档: %s | 历史 %d 组(中%d/错%d/待%d) | 结算 %d 组"
          % (safe["candidateCount"],
             "/".join("%s%s" % (k, "✓" if safe[k] else "空")
                      for k in ("bk2", "bk3", "bk4")),
             hist["total"], hist["hit"], hist["miss"], hist["pending"], checked))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[篮球串关] 异常跳过（隔离设计）:", e, file=sys.stderr)
        sys.exit(0)
