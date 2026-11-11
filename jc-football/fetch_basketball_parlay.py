#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球串关（v1.7.44 新增，v1.7.119 质量重构，独立源，与足球零耦合）。

候选池 = 当日 basketball_ai.json 预测中「尚未开赛」的比赛（单场单意见）
三档   = 稳健2串 / 均衡3串 / 精锐4串
落盘   = basketball_parlay_history.json（结构对标 parlay_history.json）
结算   = check 已开赛 pending，回填 legHit/result（补 wnmResult 胜分差区间判定）

v1.7.119 修复（篮球停更期假推荐 + 门槛过硬天天没推荐）
  - 门禁：数据批次不新鲜 / 无未开赛比赛时，绝不生成新批次，也不覆盖旧推荐
  - 清洗：移除 2026-11-02~11-05 停更期用 11-01 已结束比赛生成的 5 组假记录
  - 校准：用 basketball_ai_accuracy.json 的实证命中率（按玩法+信心分桶、贝叶斯收缩）
          替代「拍脑袋的市场共识」，组合按实证 EV 择优
  - 软惩罚：同指命中率低不再一刀切剔除，改为评分扣分；信心门槛从 58 放宽到 50
  - 不蚊子肉：单腿赔率 ≥1.55；排除胜分差六区间（回测命中率仅约 5%）
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
AI_PATH = os.path.join(HERE, "basketball_ai.json")
BK_PATH = os.path.join(HERE, "basketball.json")
BKH_PATH = os.path.join(HERE, "basketball_history.json")
PARLAY_PATH = os.path.join(HERE, "basketball_parlay_history.json")
ACC_PATH = os.path.join(HERE, "basketball_ai_accuracy.json")

PAYBACK = 0.886
ALPHA_SHRINK = 0.60
ENGINE_VERSION = "v1.7.119"
FAKE_FROM = "2026-11-02"      # 篮球官方接口海外 567 停更起始日
FAKE_TO   = "2026-11-05"      # 停更期结束日（2026-11-11：清洗必须是有限窗口）

# (key, 档名, 腿数, 单腿下限, 总赔下限, 总赔上限, 最低信心,
#  最低联合实证概率, 最低实证EV, mode, 单腿上限, 同指软惩罚线)
# 说明：不再设「同指硬门槛」（样本薄时它把候选全砍光，导致三档天天留空）；
# 单腿下限 1.55 挡掉 1.29 一类蚊子肉，上限 2.8 挡掉冷门长盘。
TIERS_BK = [
    ("bk2", "稳健2串1", 2, 1.55, 2.40, 4.60, 50.0, 0.16, -0.30, "ev", 2.80, 50),
    ("bk3", "均衡3串1", 3, 1.55, 3.80, 9.00, 50.0, 0.08, -0.38, "ev", 2.80, 50),
    ("bk4", "精锐4串1", 4, 1.55, 6.00, 18.0, 50.0, 0.04, -0.45, "ev", 2.80, 50),
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
    """市场共识口径（仅在实证样本不足时兜底）。"""
    if not o or o <= 1:
        return None
    p_market = PAYBACK / o
    if pt is not None:
        p = p_market + (pt / 100.0 - p_market) * ALPHA_SHRINK
        p = min(max(p, p_market - 0.06), p_market + 0.06)
    else:
        p = p_market
    return min(max(p, 0.01), 0.95)


def load_empirical():
    """从篮球 AI 回溯库统计实证命中率。

    按 (玩法, 信心5分桶) 与 (玩法, 全部) 两档统计，样本不足时逐级回退，
    再用市场隐含概率作先验做贝叶斯收缩，避免小样本虚高/虚低。
    """
    acc = _load(ACC_PATH, {"history": []})
    buckets, overall, total = {}, {}, [0, 0]
    for h in acc.get("history") or []:
        for pk, pl in (h.get("plays") or {}).items():
            hit = pl.get("hit")
            if hit is None:
                continue
            conf = _f(pl.get("conf")) or 55.0
            b = int(conf // 5) * 5
            v = 1 if hit is True else 0
            for store, key in ((buckets, (pk, b)), (overall, pk)):
                s = store.setdefault(key, [0, 0])
                s[0] += v
                s[1] += 1
            total[0] += v
            total[1] += 1
    return {"buckets": buckets, "overall": overall, "total": total}


def _emp_prob(c, emp):
    """单腿实证命中概率：分桶 → 玩法整体 → 市场共识，逐级贝叶斯收缩。"""
    o = _f(c.get("odds"))
    market = (PAYBACK / o) if o > 1 else 0.50
    pk = c.get("playKey") or c.get("play")
    conf = _f(c.get("confidence")) or 55.0
    b = int(conf // 5) * 5
    stats = None
    for key in ((pk, b), (pk, b - 5), (pk, b + 5)):
        s = emp["buckets"].get(key)
        if s and s[1] >= 6:
            stats = s
            break
    if stats is None:
        s = emp["overall"].get(pk)
        if s and s[1] >= 8:
            stats = s
    if stats is None:
        s = emp["total"]
        if s[1] >= 20:
            stats = s
    if stats is None:
        return _leg_prob(o, c.get("pTrue")) or 0.50
    hits, n = stats
    k = 10.0                       # 先验强度：小样本不敢偏离市场太多
    p = (hits + market * k) / (n + k)
    return min(max(p, 0.05), 0.92)


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
def gen_bk_candidates(preds, matches_map, now=None):
    """每场取 topPlay 玩法方向作为该场唯一候选（单场单意见）。

    v1.7.119 三重过滤：
      - 已结束/已开赛的比赛绝不进候选（停更期拿旧比赛造假推荐的直接根因）
      - 胜分差六区间不进串关（回测命中率约 5%，与「高质量」目标相悖）
      - 赔率过低（蚊子肉）或过高（冷门长盘）的腿不进候选
    """
    now = now or datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M")
    cands = []
    for p in preds:
        tp = p.get("topPlay")
        pl = (p.get("plays") or {}).get(tp)
        if not pl or tp == "wnm":
            continue
        m = matches_map.get(p.get("id")) or {}
        if m.get("score") or not p.get("kickoff"):
            continue
        if (p.get("kickoff") or "")[:16] <= now_s:
            continue
        odds = _f(pl.get("odds"))
        if odds < 1.55 or odds > 2.80:
            continue
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


def _combo_emp(combo, emp):
    """组合联合实证命中率（各腿实证概率相乘）。"""
    ch = 1.0
    for c in combo:
        ch *= _emp_prob(c, emp)
    return ch


def _build_tier(tier, candidates, emp):
    """按「实证 EV 择优」挑组合，同指低改为软扣分而非一刀切剔除。"""
    (key, name, ns, min_odds, min_total, max_total, min_conf,
     min_joint, min_ev, mode, max_leg, so_floor) = tier
    best = None
    for combo in combinations(candidates, ns):
        total_odds = 1.0
        ok = True
        for c in combo:
            o = _f(c.get("odds"))
            total_odds *= o if o > 1 else 1.0
            if o < min_odds or o > max_leg:
                ok = False
                break
            if (_f(c.get("confidence")) or 0) < min_conf:
                ok = False
                break
        if not ok:
            continue
        if total_odds < min_total or total_odds > max_total:
            continue
        combo_hit = _combo_emp(combo, emp)
        if combo_hit < min_joint:
            continue
        real_ev = combo_hit * total_odds - 1.0
        if real_ev < min_ev:
            continue
        # 同指命中率低 → 评分扣分（样本薄时不再让整档留空）
        soft = sum(1 for c in combo
                   if c.get("sameOddsHit") is not None
                   and (c.get("sameOddsN") or 0) >= 10
                   and c.get("sameOddsHit") < so_floor)
        score = combo_hit * 100 + real_ev * 12 - soft * 2.5
        tier_obj = {
            "type": name, "tier": key, "tierDesc": name,
            "legs": [dict(c) for c in combo],
            "totalOdds": round(total_odds, 2),
            "avgConfidence": round(sum(_f(c["confidence"]) for c in combo) / ns, 1),
            "jointProb": round(combo_hit, 4),
            "realHit": round(combo_hit, 4),
            "ev": round(combo_hit * total_odds - 1.0, 4),
            "realEv": round(real_ev, 4),
            "empirical": True,
            "recommended": real_ev >= 0,
            "score": round(score, 3),
        }
        if best is None or score > best["score"]:
            best = tier_obj
    return best


def gen_bk_parlay(preds, matches_map, emp=None, meta=None):
    emp = emp if emp is not None else load_empirical()
    candidates = gen_bk_candidates(preds, matches_map)
    safe = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candidateCount": len(candidates),
        "candidatePool": candidates,
        "dataDate": (meta or {}).get("dataDate", ""),
        "upcomingCount": (meta or {}).get("upcomingCount", 0),
        "empiricalSamples": emp["total"][1],
        "bk2": None, "bk3": None, "bk4": None,
        "strategy": (
            "篮球分层串关（v1.7.119）：只取当日尚未开赛的比赛，每场只选一种玩法方向"
            "（符合竞彩混合过关同场不可串规则）；胜分差六区间回测命中率仅约 5%，"
            "不再进入串关。命中概率由 basketball_ai_accuracy.json 的实证命中率按"
            "「玩法+信心分桶」统计并贝叶斯收缩校准，组合按实证 EV 择优；"
            "同指命中率低改为评分软扣分而非硬剔除，避免三档天天留空。"
            "单腿赔率 1.55~2.80（挡掉蚊子肉与冷门长盘）。"
            "凑不齐真实比赛时该档留空，绝不拿已结束的比赛硬凑。"
        ),
    }
    for tier in TIERS_BK:
        safe[tier[0]] = _build_tier(tier, candidates, emp)
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
def _purge_fake(hist):
    """只清理 2026-11-02~11-05 停更期的假批次（一次性历史清洗，幂等）。

    2026-11-11 修复：此前用 ``date >= FAKE_FROM`` 的无限窗口，本意是清掉停更期
    5 组假记录，实际把 11-06 之后每天的真实推荐在下一轮一并删掉——篮球串关
    历史永远只剩「10-26~11-01 真实历史 + 当天一组」，用户看到「没有历史数据」。
    现改为有限窗口 FAKE_FROM..FAKE_TO，清洗完就不再干预新增的每日记录，
    save_parlay_history 自身只替换当日 pending 旧组，历史得以按天累积。
    """
    records = hist.get("records") or []
    keep = [r for r in records
            if not (FAKE_FROM <= (r.get("date") or "") <= FAKE_TO)]
    removed = len(records) - len(keep)
    if removed:
        hist["records"] = keep
        _recalc(hist, keep)
        _save(PARLAY_PATH, hist)
    return removed


def main():
    ai = _load(AI_PATH, {"predictions": []})
    bk = _load(BK_PATH, {"matches": []})
    hist0 = _load(PARLAY_PATH, {"records": []})
    removed = _purge_fake(hist0)

    now = datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M")
    today = now.strftime("%Y-%m-%d")
    data_date = bk.get("dataDate") or ""
    fresh = (bool(data_date)
             and data_date >= (now - timedelta(days=1)).strftime("%Y-%m-%d"))
    preds = [p for p in (ai.get("predictions") or [])
             if (p.get("kickoff") or "")[:16] > now_s]
    matches_map = {m.get("id"): m for m in (bk.get("matches") or [])}

    # 门禁：数据陈旧 / 无未开赛比赛 / 候选为空 → 不生成新批次，也不覆盖旧推荐
    emp = load_empirical()
    safe = None
    if fresh and preds:
        safe = gen_bk_parlay(preds, matches_map, emp,
                            {"dataDate": data_date, "upcomingCount": len(preds)})
        if safe["candidateCount"] == 0:
            safe = None
    if safe is None:
        checked = check_basketball_parlay()
        hist = _load(PARLAY_PATH, {"records": []})
        print("[篮球串关] 门禁生效：批次 %s（新鲜=%s）/ 未开赛预测 %d 场 / "
              "清理假批次 %d 组 / 结算 %d 组，本轮不新增推荐 | 历史 %d 组(中%d/错%d/待%d)"
              % (data_date or "无", fresh, len(preds), removed, checked,
                 hist.get("total", 0), hist.get("hit", 0), hist.get("miss", 0),
                 hist.get("pending", 0)))
        return 0

    hist = save_parlay_history(safe, today)
    checked = check_basketball_parlay()

    print("[篮球串关] 候选 %d 场 | 三档: %s | 实证样本 %d 条 | 清理假批次 %d 组 | "
          "历史 %d 组(中%d/错%d/待%d) | 结算 %d 组"
          % (safe["candidateCount"],
             "/".join("%s%s" % (k, "✓" if safe[k] else "空")
                      for k in ("bk2", "bk3", "bk4")),
             emp["total"][1], removed,
             hist["total"], hist["hit"], hist["miss"], hist["pending"], checked))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[篮球串关] 异常跳过（隔离设计）:", e, file=sys.stderr)
        sys.exit(0)
