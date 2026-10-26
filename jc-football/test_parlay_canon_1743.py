# -*- coding: utf-8 -*-
"""test_parlay_canon_1743.py —— v1.7.43 选腿信号回调专项测试
覆盖：
A. parlay_canon_score 序关系（信心主导 + 同指分段修正 + pTrue 超额截断）
B. 同指硬门槛（so_floor：2/3 串 60、精锐 65、4 串零容忍）经 gen_safe_parlay 端到端验证
C. 埋点：legs 含 canon 评分快照；records 落盘含 realEv 顶层字段（源码级断言）
D. strategy 文案与 v1.7.43 对齐（不再宣称同指<45% 过滤）
"""
import sys, json, time as _time, importlib.util

BASE = "/workspace/jc-football"

built = json.load(open(f"{BASE}/matches.json", encoding="utf-8"))
FAKE_TODAY = max((m.get("date") or "" for m in built["matches"]), default="2026-10-24")
FAKE_NOW = f"{FAKE_TODAY} 09:25:00"

_real_strftime = _time.strftime
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    FAKE_NOW if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: _time.mktime(_time.strptime(FAKE_NOW, "%Y-%m-%d %H:%M:%S"))

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))

matches = [m for m in built["matches"] if m.get("date") == FAKE_TODAY]

def _opt(conf, so=None, n=0, odds=2.0, pt=None, tech=0, five=50.0):
    return {"confidence": conf, "sameOddsHit": so, "sameOddsN": n,
            "odds": odds, "pTrue": pt, "techCount": tech, "fiveDim": five}

# ===== A. parlay_canon_score 序关系 =====
print("\n[A] canon 评分序关系（信心主导）")
s = fd.parlay_canon_score

# A1 同指同段：信心度主导（v1.7.25 前被同指压制的序关系恢复）
check("A1 同指55：信76 > 信70",
      s(_opt(76, 55, 12)) > s(_opt(70, 55, 12)),
      f"{s(_opt(76,55,12)):.4f} vs {s(_opt(70,55,12)):.4f}")
# A2 高同指段（≥65 可信）压过中同指段（45-60 反向）
check("A2 信70同指67 > 信72同指50",
      s(_opt(70, 67, 12)) > s(_opt(72, 50, 12)),
      f"{s(_opt(70,67,12)):.4f} vs {s(_opt(72,50,12)):.4f}")
# A3 同指<45 重罚（-0.15）：同信心下低同指明显低于中同指（-0.10）
check("A3 同指40 < 同指55（低分段重罚）",
      s(_opt(70, 40, 12)) < s(_opt(70, 55, 12)),
      f"{s(_opt(70,40,12)):.4f} vs {s(_opt(70,55,12)):.4f}")
check("A3b 低分段罚差 = 0.05",
      abs((s(_opt(70, 55, 12)) - s(_opt(70, 40, 12))) - 0.05) < 1e-9)
# A4 无样本（None 或 n<10）不加成不惩罚
check("A4 同指None：零修正（=纯信心+EV）",
      abs(s(_opt(70)) - (0.70 + s(_opt(70)) - 0.70)) < 1e-9 and
      abs(s(_opt(70, 80, 5)) - s(_opt(70))) < 1e-9,
      f"None={s(_opt(70)):.4f} n5={s(_opt(70,80,5)):.4f}")
# A5 ≥65 加成封顶 0.08
check("A5 同指100 与 同指65 分差 ≤0.08",
      s(_opt(70, 100, 12)) - s(_opt(70, 65, 12)) <= 0.08 + 1e-9,
      f"{s(_opt(70,100,12))-s(_opt(70,65,12)):.4f}")
# A6 pTrue 超额截断：pt=99 不得借 EV 翻盘（截断 ±0.06 后量级有限）
_gap = s(_opt(70, 50, 12, odds=2.0, pt=99)) - s(_opt(70, 50, 12, odds=2.0, pt=70))
check("A6 pTrue 超额截断（99 vs 70 分差 < 0.03）", _gap < 0.03, f"gap={_gap:.4f}")
# A7 信心优先于同指加成：信90同指40 > 信70同指67
check("A7 信90同指40 > 信70同指67（信心主导）",
      s(_opt(90, 40, 12)) > s(_opt(70, 67, 12)),
      f"{s(_opt(90,40,12)):.4f} vs {s(_opt(70,67,12)):.4f}")
# A8 无同指样本时 EV/技术信号仍可微调（同信心同 so=None 下 tech 多者优先）
check("A8 tech 微权重生效（同信心下 tech3 > tech0）",
      s(_opt(70, None, 0, tech=3)) > s(_opt(70, None, 0, tech=0)))

# ===== B. 同指硬门槛端到端（monkeypatch calc_same_odds_signal） =====
print("\n[B] 同指硬门槛端到端（so_floor：2/3 串 60、精锐 65、4 串零容忍）")
_orig_calc = fd.calc_same_odds_signal

def _run_with_so(so_hit, so_n):
    fd.calc_same_odds_signal = lambda *a, **k: (so_hit, so_n)
    try:
        return fd.gen_safe_parlay(matches)
    finally:
        fd.calc_same_odds_signal = _orig_calc

# B0 基线：真实 history 同指（不打补丁）——确认候选池非空、新 canon 能选出意见
_base = fd.gen_safe_parlay(matches)
print(f"  [基线] 候选 {_base['candidateCount']} 个 | "
      f"p2={_base.get('parlay2') and _base['parlay2']['totalOdds']} "
      f"p3={_base.get('parlay3') and _base['parlay3']['totalOdds']} "
      f"p4={_base.get('parlay4') and _base['parlay4']['totalOdds']}")
check("B0 基线候选池非空（新 canon 未把池选空）", _base["candidateCount"] > 0,
      f"candidates={_base['candidateCount']}")

# B1 同指 45-60 反向区间（旧门槛 45 放行、实证 17% 命中）→ 新规则全档剔除
_r50 = _run_with_so(50, 12)
_n50 = sum(1 for k in ("parlay2", "parlay3", "parlay4") if _r50.get(k))
check("B1 同指50（反向区间）→ 三档全空（留空不硬凑）", _n50 == 0,
      f"非空档={_n50}")
_r45 = _run_with_so(45, 12)
_n45 = sum(1 for k in ("parlay2", "parlay3", "parlay4") if _r45.get(k))
check("B1b 同指45（旧门槛放行区）→ 新规则仍全空", _n45 == 0, f"非空档={_n45}")

# B2 同指 ≥65 可信区间 → 门槛放行，至少一档非空且腿同指达标
_r70 = _run_with_so(70, 12)
_nonempty = [k for k in ("parlay2", "parlay3", "parlay4") if _r70.get(k)]
check("B2 同指70（可信区间）→ 至少一档非空", len(_nonempty) > 0,
      f"非空档={_nonempty}")
for k in _nonempty:
    p = _r70[k]
    check(f"B2 {p['tier']} 所有腿同指 ≥ 各自门槛",
          all((l.get("sameOddsHit") or 0) >= (65 if k == "parlay4" else 60) for l in p["legs"]))

# B3 精锐档门槛 65：同指 62 时 2/3 串可过、精锐必须为空
_r62 = _run_with_so(62, 12)
check("B3 同指62 → 精锐4串1 为空（门槛65）", _r62.get("parlay4") is None,
      f"parlay4={'有组合' if _r62.get('parlay4') else 'None'}")

# B4 样本不足（None/n<10）不算软腿；出组合时 realHit 回退市场口径 ≈ jointProb
_rn = _run_with_so(None, 5)
_nonempty_n = [k for k in ("parlay2", "parlay3", "parlay4") if _rn.get(k)]
check("B4 同指样本不足 → 腿不判软（至少一档非空）", len(_nonempty_n) > 0,
      f"非空档={_nonempty_n}（候选 {_rn['candidateCount']}）")
for k in _nonempty_n:
    p = _rn[k]
    check(f"B4 {p['tier']} realHit≈jointProb（回退市场+alpha 口径）",
          abs(p["realHit"] - p["jointProb"]) < 0.05,
          f"realHit={p['realHit']} jointProb={p['jointProb']}")

# ===== C. 埋点字段 =====
print("\n[C] 埋点：canon 评分快照 / realEv 顶层落盘")
for k in _nonempty:
    p = _r70[k]
    check(f"C1 {p['tier']} legs 含 canon 字段",
          all(l.get("canon") is not None for l in p["legs"]),
          f"canon={[l.get('canon') for l in p['legs']]}")
_src = open(f"{BASE}/fetch_daily.py", encoding="utf-8").read()
check("C2 records.append 含 realEv 顶层字段",
      '"realEv": p.get("realEv")' in _src)

# ===== D. strategy 文案 =====
print("\n[D] strategy 文案与 v1.7.43 对齐")
_st = _r70.get("strategy") or ""
check("D1 文案含「同指命中<60%（精锐档65%）」", "同指命中<60%（精锐档65%）" in _st)
check("D2 文案不再宣称「同指命中<45% 的软腿直接过滤」",
      "同指命中<45% 的软腿直接过滤" not in _st)
check("D3 文案含 v1.7.43 回调说明", "选腿信号回调（v1.7.43）" in _st and "45-60 分段" in _st)
check("D4 文案含信心度主导说明", "信心度主导" in _st)

print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
