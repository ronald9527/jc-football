# -*- coding: utf-8 -*-
"""v1.7.25 今日（2026-10-20）串关外科手术式重生：
1. 时间补丁回 2026-10-20 12:30（跑当天代码路径）
2. 用线上 BUILT_DATA 快照的 10-20 真实比赛数据跑新 gen_safe_parlay
3. save_parlay_history 落档（替换 09:06 旧推荐——当日修订，历史不留双轨）
4. check_parlay_history 重算统计
5. 用快照作基底更新 matches.json 的 safeParlay/parlayHistory，保留其余板块不动
"""
import sys, json, time as _time, importlib.util, shutil

_real_strftime = _time.strftime
_real_time = _time.time
_real_localtime = _time.localtime
FAKE_TODAY = "2026-10-20"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-20 12:35:00", "%Y-%m-%d %H:%M:%S"))
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-20 12:35:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: FAKE_NOW

BASE = "/workspace/jc-football"

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

# 1) 线上快照数据（10-20 12:02 赔率的 10 场完整比赛）
built = json.load(open("/tmp/built_1020.json", encoding="utf-8"))
matches = [m for m in json.loads(json.dumps(built["matches"])) if m.get("date") == FAKE_TODAY]
print(f"载入 10-20 比赛 {len(matches)} 场")

# 2) 新代码生成三档
safe = fd.gen_safe_parlay(matches)
print(f"生成：候选{safe['candidateCount']} | "
      f"稳健 {safe['parlay2']['totalOdds'] if safe.get('parlay2') else '空'} | "
      f"均衡 {safe['parlay3']['totalOdds'] if safe.get('parlay3') else '空'} | "
      f"精锐 {safe['parlay4']['totalOdds'] if safe.get('parlay4') else '空'}")
print("poolNotes:", safe.get("poolNotes"))

# 3) 落档：先删当日旧记录（本地一般没有，防御性删除），再按 save 格式写入
shutil.copy(f"{BASE}/parlay_history.json", f"{BASE}/parlay_history.json.bak_before_1020_regen")
hist = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
records = hist.get("records", [])
before = len(records)
records = [r for r in records if r.get("date") != FAKE_TODAY]
print(f"历史记录：{before} -> {len(records)}（删除 {before - len(records)} 条 10-20 旧记录）")
hist["records"] = records
json.dump(hist, open(f"{BASE}/parlay_history.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

fd.save_parlay_history(safe, None)
hist2 = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
today_recs = [r for r in hist2["records"] if r.get("date") == FAKE_TODAY]
print(f"重生后 10-20 记录 {len(today_recs)} 条：",
      [(r["type"], r["totalOdds"], r["savedAt"]) for r in today_recs])

# 4) 重算统计（回写 parlay_history 的统计字段由 check_parlay_history 返回值提供）
stats = fd.check_parlay_history()
print(f"统计：total={stats['total']} hit={stats['hit']} miss={stats['miss']} "
      f"pending={stats['pending']} hitRate={stats['hitRate']}%")

# 5) 更新 matches.json：以线上快照为基底，只换 safeParlay/parlayHistory/updateTime
built["safeParlay"] = safe
built["parlayHistory"] = stats
built["updateTime"] = "2026-10-20 12:35:00"
built["count"] = len(matches)
json.dump(built, open(f"{BASE}/matches.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("matches.json 已更新（10 场 + 新串关 + 新统计）")
print("\n=== 重生后的三档 ===")
for key, name in [("parlay2", "稳健"), ("parlay3", "均衡"), ("parlay4", "精锐")]:
    p = safe.get(key)
    if not p:
        print(f"  {name}：留空（无达标组合）")
        continue
    legs = " + ".join(f"{l['num']}{l['play']}{l['label']}@{l['odds']}" for l in p["legs"])
    print(f"  {name} {p['type']} 总赔{p['totalOdds']}（单调门{p.get('oddsGate')}）EV{p['ev']:+.1f}% :: {legs}")
