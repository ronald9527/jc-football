# -*- coding: utf-8 -*-
"""test_parlay_real_ev.py —— v1.7.33 串关真实口径改革专项测试
覆盖：
1. 字段完备性：realHit/realEv/beHit/recommended/rake 全部落盘
2. 口径自洽：realEv ≈ realHit × totalOdds − 1；beHit = 100/totalOdds（四舍五入误差内）
3. 推荐边界：recommended ⇔ realEv ≥ −2%（噪声容差）
4. evNote 文案与真实口径数字匹配（含命中率与平衡线）
5. 历史回归：test_parlay_canon.py 同款候选/单调门断言不破
6. 前端契约：template.html 档位卡引用字段全部存在于后端产出
"""
import sys, json, time as _time, importlib.util, re

BASE = "/workspace/jc-football"

# 时间补丁回今日 09:25（与 regen_1021.py 同环境）
_real_strftime = _time.strftime
FAKE_TODAY = "2026-10-21"
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-21 09:25:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))

built = json.load(open(f"{BASE}/matches.json", encoding="utf-8"))
matches = [m for m in built["matches"] if m.get("date") == FAKE_TODAY]
safe = fd.gen_safe_parlay(matches)

print(f"候选 {safe['candidateCount']} 个 | 三档："
      f"{ {k: (v['totalOdds'] if v else None) for k, v in [('p2', safe.get('parlay2')), ('p3', safe.get('parlay3')), ('p4', safe.get('parlay4'))]} }")

# ===== 1. 字段完备性 =====
print("\n[1] 字段完备性")
for key in ("parlay2", "parlay3", "parlay4"):
    p = safe.get(key)
    if not p:
        continue
    for f in ("realHit", "realEv", "beHit", "recommended", "rake", "evNote", "suggestStake", "ev"):
        check(f"{key}.{f}", f in p and p[f] is not None, f"缺失或为 null")

# ===== 2. 口径自洽 =====
print("\n[2] 口径自洽（真实口径）")
for key in ("parlay2", "parlay3", "parlay4"):
    p = safe.get(key)
    if not p:
        continue
    # realEv ≈ realHit × totalOdds − 1（存储为四舍五入到 0.1 的百分数）
    expect_real = p["realHit"] * p["totalOdds"] - 100.0
    check(f"{key}.realEv 口径自洽",
          abs(p["realEv"] - expect_real) < 0.6,
          f"存储 {p['realEv']}% vs 计算 {expect_real:.1f}%")
    # beHit = 100/totalOdds
    check(f"{key}.beHit 口径自洽",
          abs(p["beHit"] - 100.0 / p["totalOdds"]) < 0.06,
          f"存储 {p['beHit']}% vs 计算 {100.0/p['totalOdds']:.1f}%")
    # rake = (1 − 0.886^n) × 100
    n = len(p["legs"])
    check(f"{key}.rake 口径自洽",
          abs(p["rake"] - (1.0 - 0.886 ** n) * 100) < 0.1,
          f"存储 {p['rake']}% vs 计算 {(1.0-0.886**n)*100:.1f}%")

# ===== 3. 推荐边界 =====
print("\n[3] 推荐边界（realEv ≥ −2% ⇔ recommended）")
for key in ("parlay2", "parlay3", "parlay4"):
    p = safe.get(key)
    if not p:
        continue
    check(f"{key}.recommended 边界",
          p["recommended"] == (p["realEv"] >= -2.0),
          f"realEv={p['realEv']}% recommended={p['recommended']}")
    # 凯利仓位按真实口径：realEv>0 时仓位 >0；realEv≤0 时凯利为负、截断为 0（数学正确）
    if p["realEv"] > 0:
        check(f"{key}.realEv>0 时仓位>0", p["suggestStake"] > 0, f"stake={p['suggestStake']}")
    else:
        check(f"{key}.realEv≤0 时仓位=0", p["suggestStake"] == 0, f"stake={p['suggestStake']}")

# ===== 4. evNote 文案匹配 =====
print("\n[4] evNote 文案与真实口径匹配")
for key in ("parlay2", "parlay3", "parlay4"):
    p = safe.get(key)
    if not p:
        continue
    note = p["evNote"]
    # 文案用四舍五入整数（后端 f"{x*100:.0f}%"），与存储字段值允许四舍五入差异
    rh = f"{round(p['realHit'])}%"
    be = f"{round(p['beHit'])}%"
    check(f"{key}.evNote 含真实命中率", rh in note, f"期望 {rh} in: {note}")
    check(f"{key}.evNote 含平衡线", be in note, f"期望 {be} in: {note}")
    # 措辞按 realEv 分档推导（与后端 ev_note 四档一致：10%/-2%/-10%，字段为百分数）
    if p["realEv"] >= 10.0:
        expect_prefix = "正期望"
    elif p["realEv"] >= -2.0:
        expect_prefix = "达标"
    elif p["realEv"] >= -10.0:
        expect_prefix = "轻度负期望"
    else:
        expect_prefix = "明显负期望"
    check(f"{key}.evNote 措辞（realEv {p['realEv']}% → {expect_prefix}）",
          note.startswith(expect_prefix), note)

# ===== 5. 历史回归（canon 同款：候选非空 + 赔率单调） =====
print("\n[5] 历史回归：候选与赔率单调")
check("候选池非空", safe["candidateCount"] > 0)
prev = 0.0
for key, name in [("parlay2", "稳健"), ("parlay3", "均衡"), ("parlay4", "精锐")]:
    p = safe.get(key)
    if not p:
        continue
    check(f"{name} 总赔高于上一档", p["totalOdds"] > prev,
          f"{p['totalOdds']} vs prev {prev}")
    prev = max(prev, p["totalOdds"])

# ===== 6. 前端契约：template.html 引用的字段全部存在 =====
print("\n[6] 前端契约（template.html 档位卡字段引用）")
tpl = open(f"{BASE}/template.html", encoding="utf-8").read()
front_refs = ["p.realHit", "p.realEv", "p.beHit", "p.recommended", "p.ev",
              "p.suggestStake", "p.rake", "p.evNote"]
for ref in front_refs:
    check(f"template 引用 {ref}", ref in tpl)
# 前端已不再用 ev 做推荐判定（_pos 只认 recommended）
_pos_ok = re.search(r"const _pos\s*=\s*\([^)]*\)\s*=>\s*\{[^}]*recommended[^}]*\}", tpl)
check("_pos 只认 recommended（不再看 ev）", bool(_pos_ok))

# ===== 7. parlay_history 一致性（落盘记录与推荐同口径） =====
print("\n[7] parlay_history 落盘一致性")
hist = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
today_recs = [r for r in hist["records"] if r.get("date") == FAKE_TODAY]
check("今日记录数 = 非空档位数",
      len(today_recs) == sum(1 for k in ("parlay2", "parlay3", "parlay4") if safe.get(k)),
      f"records={len(today_recs)}")
for r in today_recs:
    snap = r.get("snapshot") or {}
    check(f"{r['type']} engineVer=v1.7.33", r.get("engineVer") == "v1.7.33")
    check(f"{r['type']} 快照含 realEv", "realEv" in snap)
    check(f"{r['type']} recommended 与快照一致",
          r.get("recommended") == snap.get("recommended"))

print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
