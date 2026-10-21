# -*- coding: utf-8 -*-
"""restore_003_records.py —— 补回 2026-10-21 被当日修订删除的三轮推荐并按官方赛果判定
背景：12:31/15:32 定时任务生成的三档全部含周三003（大宫松鼠RB vs 富山胜利），
19:45 regen 剔除停售腿时删除了这三条 pending 记录——用户实际跟过的推荐
在历史里查无此组。官方赛果确认 003 终局 0:3 主负（HAD=负），三轮全部判错。

本脚本：
1. 从 /tmp/online_ph.json（19:50 线上快照）提取三条原始记录（含完整 snapshot/legs）
2. 按官方赛果判定 result=miss（003 主胜错；精锐另含 001 主胜错：东京FC 0:0 平）
3. append 回 parlay_history（不覆盖 19:45 新记录），check_parlay_history 重算统计
4. 回写 matches.json 的 parlayHistory
"""
import json, time as _time, importlib.util

BASE = "/workspace/jc-football"

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys_modules = __import__("sys").modules
sys_modules["fd"] = fd
spec.loader.exec_module(fd)

# 官方赛果（webapi getFixedBonusV1 实测）
OFFICIAL = {
    "周三003": {"score": "0:3", "had": "A"},   # 大宫松鼠RB 0:3 富山胜利 → 主负
    "周三001": {"score": "0:0", "had": "D"},   # 东京FC 0:0 柏太阳神 → 平
    "周三002": {"score": "2:1", "had": "H"},   # 川崎前锋 2:1 神户胜利船 → 主胜
}
# 各档推荐腿的方向（label 主胜/客胜等 → had 的 H/A）
LABEL2HAD = {"主胜": "H", "客胜": "A", "平": "D"}

old = json.load(open("/tmp/online_ph.json", encoding="utf-8"))
old_today = [r for r in old["records"] if r.get("date") == "2026-10-21"]
print(f"从线上快照提取 10-21 旧记录 {len(old_today)} 条：",
      [(r["type"], r["totalOdds"], r.get("savedAt")) for r in old_today])

hist = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
records = hist.get("records", [])
existing_sigs = set()
for r in records:
    if r.get("date") == "2026-10-21":
        existing_sigs.add(tuple(l.get("num") for l in (r.get("legs") or [])))
        print(f"  当前 10-21 记录：{r['type']} {r['totalOdds']} {r.get('result')} legs={[l['num'] for l in r.get('legs',[])]}")

restored = 0
for r in old_today:
    sig = tuple(l.get("num") for l in (r.get("legs") or []))
    if sig in existing_sigs:
        print(f"  跳过（已在历史）：{r['type']} {r['totalOdds']}")
        continue
    # 判定：逐腿核对官方赛果
    wrong = []
    for l in (r.get("legs") or []):
        num = l.get("num")
        off = OFFICIAL.get(num)
        if not off:
            wrong.append(f"{num}(无赛果)")
            continue
        want = LABEL2HAD.get(l.get("label"), "")
        if want and off["had"] != want:
            wrong.append(f"{num}(荐{l.get('label')}/实际{off['score']})")
    r["result"] = "miss" if wrong else "hit"
    r["resultAt"] = _time.strftime("%Y-%m-%d %H:%M:%S")
    r["restoredNote"] = (f"当日修订删除后按官方赛果补判：{'; '.join(wrong)}" if wrong
                         else "当日修订删除后补判：全部命中")
    records.append(r)
    restored += 1
    print(f"  补回并判定：{r['type']} {r['totalOdds']} → {r['result']}（{r['restoredNote']}）")

hist["records"] = records
json.dump(hist, open(f"{BASE}/parlay_history.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\nparlay_history.json 更新：补回 {restored} 条，总记录 {len(records)}")

# 重算统计（check_parlay_history 会读 history.json 赛果——003 的 evaluated 尚未回填，
# 但我们已手动置 result，check 对已有 result 的记录不会覆盖，只统计）
stats = fd.check_parlay_history()
print(f"统计：total={stats['total']} hit={stats['hit']} miss={stats['miss']} "
      f"pending={stats['pending']} hitRate={stats['hitRate']}%")

# 回写 matches.json
m = json.load(open(f"{BASE}/matches.json", encoding="utf-8"))
m["parlayHistory"] = stats
json.dump(m, open(f"{BASE}/matches.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("matches.json parlayHistory 已更新")

today = [r for r in records if r.get("date") == "2026-10-21"]
print("\n=== 10-21 完整轨迹（按发布序）===")
for r in sorted(today, key=lambda x: x.get("savedAt", "")):
    legs = " + ".join(f"{l['num']}{l['label']}" for l in (r.get("legs") or []))
    print(f"  {r.get('savedAt')} {r['type']} {r['totalOdds']}倍 [{r.get('result')}] {legs}")
