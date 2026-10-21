# -*- coding: utf-8 -*-
"""v1.7.33 今日（2026-10-21）串关重生成：真实口径改革落地
背景：用户强烈反馈「每天的串关都是不推荐，哪我要你选那些串关有什么用」——
根因是旧引擎用模型口径 EV 判定推荐（系统性偏保守，恒负），58 期 50 期挂不推荐。
v1.7.33 改为地面真实口径（历史同条件命中率 × 总赔率 − 1）判定，今日三档按新标准重生。

流程（沿用 regen_1020.py 先例，当日修订、历史不留双轨）：
1. 时间补丁回 2026-10-21 09:25（与线上 09:20 生成时间对齐，跑当天代码路径）
2. 用当前 matches.json（线上 11:01 快照）的 10-21 真实比赛跑新 gen_safe_parlay
3. 删 parlay_history.json 当日 v1.7.32 旧记录 → save_parlay_history 落新档
4. check_parlay_history 重算统计
5. 回写 matches.json 的 safeParlay/parlayHistory，其余板块不动
"""
import sys, json, time as _time, importlib.util, shutil

_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-21"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-21 09:25:00", "%Y-%m-%d %H:%M:%S"))
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-21 09:25:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: FAKE_NOW

BASE = "/workspace/jc-football"

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

# 引擎版本校验：必须是 v1.7.33（新口径）才允许重生成
cur_ver = fd._parlay_engine_version()
assert cur_ver == "v1.7.33", f"引擎版本异常：{cur_ver}（应为 v1.7.33，先跑 bump_version.py）"
print(f"引擎版本确认：{cur_ver}")

# 1) 今日比赛（当前 matches.json = 线上 11:01 快照）
built = json.load(open(f"{BASE}/matches.json", encoding="utf-8"))
matches = [m for m in json.loads(json.dumps(built["matches"])) if m.get("date") == FAKE_TODAY]
print(f"载入 10-21 比赛 {len(matches)} 场")

# 2) 新口径生成三档
safe = fd.gen_safe_parlay(matches)
print(f"生成：候选{safe['candidateCount']} | "
      f"稳健 {safe['parlay2']['totalOdds'] if safe.get('parlay2') else '空'} | "
      f"均衡 {safe['parlay3']['totalOdds'] if safe.get('parlay3') else '空'} | "
      f"精锐 {safe['parlay4']['totalOdds'] if safe.get('parlay4') else '空'}")
print("poolNotes:", safe.get("poolNotes"))

# 3) 落档：先删当日旧记录（v1.7.32 旧口径产物），再按新格式写入
shutil.copy(f"{BASE}/parlay_history.json", f"{BASE}/parlay_history.json.bak_before_1021_regen")
hist = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
records = hist.get("records", [])
before = len(records)
records = [r for r in records if r.get("date") != FAKE_TODAY]
print(f"历史记录：{before} -> {len(records)}（删除 {before - len(records)} 条 10-21 旧口径记录）")
hist["records"] = records
json.dump(hist, open(f"{BASE}/parlay_history.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

fd.save_parlay_history(safe, None)
hist2 = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
today_recs = [r for r in hist2["records"] if r.get("date") == FAKE_TODAY]
print(f"重生后 10-21 记录 {len(today_recs)} 条：",
      [(r["type"], r["totalOdds"], r.get("engineVer"), r.get("recommended")) for r in today_recs])

# 4) 重算统计
stats = fd.check_parlay_history()
print(f"统计：total={stats['total']} hit={stats['hit']} miss={stats['miss']} "
      f"pending={stats['pending']} hitRate={stats['hitRate']}%")

# 5) 更新 matches.json：只换 safeParlay/parlayHistory/updateTime
built["safeParlay"] = safe
built["parlayHistory"] = stats
built["updateTime"] = "2026-10-21 09:25:00"
built["count"] = len(matches)
json.dump(built, open(f"{BASE}/matches.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("matches.json 已更新（今日比赛 + 新口径串关 + 新统计）")

# 6) 明细：新旧口径对照 + 达标判定
print("\n=== 重生后的三档（v1.7.33 真实口径）===")
for key, name in [("parlay2", "稳健"), ("parlay3", "均衡"), ("parlay4", "精锐")]:
    p = safe.get(key)
    if not p:
        print(f"  {name}：留空（无达标组合）")
        continue
    legs = " + ".join(f"{l['num']}{l['play']}{l['label']}@{l['odds']}" for l in p["legs"])
    print(f"  {name} {p['type']} 总赔{p['totalOdds']}（单调门{p.get('oddsGate')}）")
    print(f"    模型EV {p['ev']:+.1f}% | 真实命中 {p['realHit']}% vs 平衡线 {p['beHit']}% | "
          f"真实EV {p['realEv']:+.1f}% | 推荐 {p['recommended']} | 仓位 {p['suggestStake']}%")
    print(f"    腿：{legs}")
    print(f"    说明：{p['evNote']}")
