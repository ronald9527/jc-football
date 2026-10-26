# -*- coding: utf-8 -*-
"""backtest_parlay_1743.py —— v1.7.43 选腿信号回调 · 历史回测
用 parlay_history.json 72 组已落盘推荐（含腿级 sameOddsHit 与 legHit）验证：
1. 时代分层：旧引擎/过渡期/新引擎 的组级与腿级命中率（回调前断崖复现）
2. 同指分段实证：<45 / 45-60 / ≥65 / 无样本 四档腿级实际命中 vs 市场期望
3. 新规则过滤模拟：10-20 后 14 组中，按 so_floor（2/3 串 60、精锐 65、
   4 串零容忍）有几组会被剔除；幸存组的实际命中如何
4. 信心信号回归验证：confidence≥75 腿的实际命中（回调把信心请回主导的依据）
"""
import json
from math import erfc, sqrt

BASE = "/workspace/jc-football"
d = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
recs = d["records"]

def era(date):
    if date < "2026-10-14":
        return "旧引擎(<10-14)"
    if date < "2026-10-20":
        return "过渡期(10-14~19)"
    return "新引擎(≥10-20)"

def band(leg):
    so, n = leg.get("sameOddsHit"), leg.get("sameOddsN") or 0
    if so is None or n < 10:
        return "无样本"
    if so < 45:
        return "<45"
    if so < 60:
        return "45-60"
    if so < 65:
        return "60-65"
    return "≥65"

def binom_p(k, n, p, side):
    """单侧二项检验（正态近似 + 连续性修正）：
    side='less'    → P(X≤k)（实际命中低于期望时用，验「反向信号」）
    side='greater' → P(X≥k)（实际命中高于期望时用，验「正向信号」）
    """
    if n == 0:
        return 1.0
    mu = n * p
    sd = sqrt(n * p * (1 - p))
    if sd == 0:
        return 1.0
    z = (k + (0.5 if side == "less" else -0.5) - mu) / sd
    return 0.5 * erfc((-z if side == "less" else z) / sqrt(2))

# ===== 1. 时代分层 =====
print("=" * 74)
print("1. 时代分层命中率（回调前断崖复现）")
eras = {}
for r in recs:
    e = era(r.get("date", ""))
    st = eras.setdefault(e, {"groups": 0, "hit": 0, "decided": 0,
                             "legs": 0, "leg_hit": 0})
    st["groups"] += 1
    if r.get("result") == "hit":
        st["hit"] += 1
    if r.get("result") in ("hit", "miss"):
        st["decided"] += 1
        for l in r.get("legs", []):
            if l.get("legHit") is None:
                continue
            st["legs"] += 1
            if l.get("legHit"):
                st["leg_hit"] += 1
for e, st in eras.items():
    gr = st["hit"] / st["decided"] * 100 if st["decided"] else 0
    lr = st["leg_hit"] / st["legs"] * 100 if st["legs"] else 0
    print(f"  {e:18s} 组 {st['hit']:2d}/{st['decided']:2d} = {gr:5.1f}%   "
          f"腿 {st['leg_hit']:3d}/{st['legs']:3d} = {lr:5.1f}%"
          f"（另 {st['groups']-st['decided']} 组 pending 不计）")

# ===== 2. 同指分段腿级实证（已判定记录）=====
print("\n2. 同指分段腿级实证（实际命中 vs 市场期望）")
bands = {}
for r in recs:
    if r.get("result") not in ("hit", "miss"):
        continue
    for l in r.get("legs", []):
        if l.get("legHit") is None:
            continue
        b = band(l)
        st = bands.setdefault(b, {"legs": 0, "hit": 0, "mkt": 0.0})
        st["legs"] += 1
        st["mkt"] += 0.886 / (l.get("odds") or 2.0)
        if l.get("legHit"):
            st["hit"] += 1
for b in ("<45", "45-60", "60-65", "≥65", "无样本"):
    st = bands.get(b)
    if not st:
        continue
    lr = st["hit"] / st["legs"] * 100
    mr = st["mkt"] / st["legs"] * 100
    side = "less" if lr < mr else "greater"
    p = binom_p(st["hit"], st["legs"], mr / 100, side) if st["legs"] >= 5 else 1.0
    if p < 0.05 and lr < mr:
        flag = "  ← 显著低于市场（反向信号，不能当地面真相）"
    elif p < 0.05 and lr > mr:
        flag = "  ← 显著高于市场（正向信号，可采信）"
    else:
        flag = ""
    print(f"  同指 {b:6s} 腿 {st['hit']:3d}/{st['legs']:3d} = {lr:5.1f}%  "
          f"市场期望 {mr:5.1f}%  单侧p={p:.4f}{flag}")

# 新引擎时代切片：断崖期内的同指分段（回调决策的核心证据）
print("  └ 新引擎时代（≥10-20）切片：")
_bands_ne = {}
for r in recs:
    if r.get("date", "") < "2026-10-20" or r.get("result") not in ("hit", "miss"):
        continue
    for l in r.get("legs", []):
        if l.get("legHit") is None:
            continue
        b = band(l)
        st = _bands_ne.setdefault(b, {"legs": 0, "hit": 0, "mkt": 0.0})
        st["legs"] += 1
        st["mkt"] += 0.886 / (l.get("odds") or 2.0)
        if l.get("legHit"):
            st["hit"] += 1
for b in ("<45", "45-60", "60-65", "≥65", "无样本"):
    st = _bands_ne.get(b)
    if not st:
        continue
    lr = st["hit"] / st["legs"] * 100
    mr = st["mkt"] / st["legs"] * 100
    side = "less" if lr < mr else "greater"
    p = binom_p(st["hit"], st["legs"], mr / 100, side) if st["legs"] >= 5 else 1.0
    if p < 0.05 and lr < mr:
        flag = "  ← 显著低于市场（反向信号，v1.7.25/33 误当真相的根源）"
    elif p < 0.05 and lr > mr:
        flag = "  ← 显著高于市场（正向信号，可采信）"
    else:
        flag = ""
    print(f"     同指 {b:6s} 腿 {st['hit']:3d}/{st['legs']:3d} = {lr:5.1f}%  "
          f"市场期望 {mr:5.1f}%  单侧p={p:.4f}{flag}")

# ===== 3. 新规则过滤模拟（10-20 后已判定 14 组）=====
print("\n3. 新规则过滤模拟（v1.7.43 so_floor 硬门槛，10-20 后已判定组）")
new_decided = [r for r in recs if r.get("date", "") >= "2026-10-20"
               and r.get("result") in ("hit", "miss")]
kept, filtered = [], []
for r in new_decided:
    floor = 65 if "精锐" in (r.get("type") or "") else 60
    soft = [l for l in r.get("legs", [])
            if l.get("sameOddsHit") is not None
            and (l.get("sameOddsN") or 0) >= 10
            and l.get("sameOddsHit") < floor]
    (filtered if soft else kept).append((r, soft))
print(f"  10-20 后已判定 {len(new_decided)} 组："
      f"新规则剔除 {len(filtered)} 组（含反向区间软腿），幸存 {len(kept)} 组")
for r, soft in filtered:
    so_desc = ",".join(f"{l['sameOddsHit']:.0f}%(n={l['sameOddsN']})" for l in soft)
    print(f"    [剔除] {r['date']} {r['type']} 结果={r['result']} 软腿同指: {so_desc}")
for r, _ in kept:
    print(f"    [幸存] {r['date']} {r['type']} 结果={r['result']} "
          f"(全腿同指≥门槛或无样本)")
kh = sum(1 for r, _ in kept if r.get("result") == "hit")
print(f"  幸存组实际命中：{kh}/{len(kept)}"
      + (f" = {kh/len(kept)*100:.0f}%" if kept else ""))

# ===== 4. 信心信号回归验证 =====
print("\n4. 信心信号验证（回调把信心请回主导的依据）")
for lo, hi in ((75, 200), (70, 75), (60, 70)):
    legs = [l for r in recs if r.get("result") in ("hit", "miss")
            for l in r.get("legs", [])
            if l.get("legHit") is not None and lo <= (l.get("confidence") or 0) < hi]
    if not legs:
        continue
    h = sum(1 for l in legs if l.get("legHit"))
    print(f"  confidence [{lo},{hi}) 腿 {h}/{len(legs)} = {h/len(legs)*100:.1f}%")
# 新引擎时代单独看（断崖期内信心信号是否仍有效）
for lo, hi in ((75, 200), (70, 75)):
    legs = [l for r in recs if r.get("date", "") >= "2026-10-20"
            and r.get("result") in ("hit", "miss")
            for l in r.get("legs", [])
            if l.get("legHit") is not None and lo <= (l.get("confidence") or 0) < hi]
    if legs:
        h = sum(1 for l in legs if l.get("legHit"))
        print(f"  └ 新引擎时代 confidence [{lo},{hi}) 腿 {h}/{len(legs)} = {h/len(legs)*100:.1f}%")
print("=" * 74)
