# -*- coding: utf-8 -*-
"""regen_1021_v2.py —— v1.7.33 今日（10-21）串关第二轮重生（剔除停售腿）
背景：12:31 定时任务用新赔率重生的三档全部包含周三003（18:00 开球），
19:50 时 003 已停售——用户看到的推荐实际已无法下单。本轮用线上 19:01
快照的 10 场在售比赛重生成，保证「推荐即可买」。

流程同 regen_1021.py：时间补丁 → gen_safe_parlay → 删当日旧记录 →
save_parlay_history → check_parlay_history → 回写 matches.json。
"""
import sys, json, time as _time, importlib.util, shutil

_real_strftime = _time.strftime
FAKE_TODAY = "2026-10-21"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-21 19:45:00", "%Y-%m-%d %H:%M:%S"))
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-21 19:45:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: FAKE_NOW

BASE = "/workspace/jc-football"

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

cur_ver = fd._parlay_engine_version()
assert cur_ver == "v1.7.33", f"引擎版本异常：{cur_ver}"
print(f"引擎版本确认：{cur_ver}")

# 基底：线上 19:01 快照（仅剩 10 场在售，停售场已滚出）
built = json.load(open(f"{BASE}/matches_online_1021.json", encoding="utf-8"))
matches = [m for m in json.loads(json.dumps(built["matches"])) if m.get("date") == FAKE_TODAY]
print(f"载入 10-21 在售比赛 {len(matches)} 场：{[m['num'] for m in matches]}")

safe = fd.gen_safe_parlay(matches)
print(f"生成：候选{safe['candidateCount']} | "
      f"稳健 {safe['parlay2']['totalOdds'] if safe.get('parlay2') else '空'} | "
      f"均衡 {safe['parlay3']['totalOdds'] if safe.get('parlay3') else '空'} | "
      f"精锐 {safe['parlay4']['totalOdds'] if safe.get('parlay4') else '空'}")
print("poolNotes:", safe.get("poolNotes"))

# 停售腿校验：任何一档不得包含已停售编号
sold_out = {"周三001", "周三002", "周三003"}
for key in ("parlay2", "parlay3", "parlay4"):
    p = safe.get(key)
    if not p:
        continue
    bad = [l["num"] for l in p["legs"] if l["num"] in sold_out]
    assert not bad, f"{key} 仍含停售腿：{bad}"

shutil.copy(f"{BASE}/parlay_history.json", f"{BASE}/parlay_history.json.bak_before_1021_regen_v2")
hist = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
records = hist.get("records", [])
before = len(records)
records = [r for r in records if r.get("date") != FAKE_TODAY]
print(f"历史记录：{before} -> {len(records)}（删除 {before - len(records)} 条 10-21 旧记录）")
hist["records"] = records
json.dump(hist, open(f"{BASE}/parlay_history.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

fd.save_parlay_history(safe, None)
hist2 = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
today_recs = [r for r in hist2["records"] if r.get("date") == FAKE_TODAY]
print(f"重生后 10-21 记录 {len(today_recs)} 条：",
      [(r["type"], r["totalOdds"], r.get("engineVer"), r.get("recommended")) for r in today_recs])

stats = fd.check_parlay_history()
print(f"统计：total={stats['total']} hit={stats['hit']} miss={stats['miss']} "
      f"pending={stats['pending']} hitRate={stats['hitRate']}%")

built["safeParlay"] = safe
built["parlayHistory"] = stats
built["updateTime"] = "2026-10-21 19:45:00"
built["count"] = len(matches)
json.dump(built, open(f"{BASE}/matches.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("matches.json 已更新（10 场在售 + 新口径串关 + 新统计）")

print("\n=== 第二轮重生后的三档（无停售腿）===")
for key, name in [("parlay2", "稳健"), ("parlay3", "均衡"), ("parlay4", "精锐")]:
    p = safe.get(key)
    if not p:
        print(f"  {name}：留空（无达标组合）")
        continue
    legs = " + ".join(f"{l['num']}{l['play']}{l['label']}@{l['odds']}" for l in p["legs"])
    print(f"  {name} {p['type']} 总赔{p['totalOdds']}")
    print(f"    模型EV {p['ev']:+.1f}% | 真实命中 {p['realHit']}% vs 平衡线 {p['beHit']}% | "
          f"真实EV {p['realEv']:+.1f}% | 推荐 {p['recommended']} | 仓位 {p['suggestStake']}%")
    print(f"    腿：{legs}")
    print(f"    说明：{p['evNote']}")
