# -*- coding: utf-8 -*-
"""backfill_1021_results.py —— 补回 2026-10-21 已完赛但滚出在售清单的比赛官方赛果

背景：12:31/15:32 三轮推荐含周三001/003（18:00 开球），19:01 前这两场已停售
并从 matches 滚出——每日抓取的 backfill 只覆盖在售清单内的比赛，导致这两场的
官方赛果从未进入 history.json，parlay_history 里含它们的组永远「待开奖」。

本脚本：
1. 对 history.json 中 10-21 已开球（kickoff+2.5h 已过）的记录调官方开奖接口
   （getFixedBonusV1，项目自带 WAF 重试的 fd.fetch）回填赛果
2. check_parlay_history 按新判定语义（v1.7.34：任一腿已开奖且错误 → 整组立即
  判 miss）重算——含 003（0:3 主负）的三轮补回记录自然翻成 miss
3. 回写 matches.json 的 parlayHistory 统计
"""
import json, importlib.util, sys

BASE = "/workspace/jc-football"
spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

hist = json.load(open(f"{BASE}/history.json", encoding="utf-8"))
records = hist.get("records", [])

# 回填前状态
before = [r for r in records if r.get("date") == "2026-10-21" and r.get("num", "").startswith("周三")]
print(f"10-21 周三比赛 {len(before)} 场，回填前有赛果的：",
      [r["num"] for r in before if (r.get("result") or {}).get("score")])

fd.backfill_results(records)
json.dump(hist, open(f"{BASE}/history.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

after = [r for r in records if r.get("date") == "2026-10-21" and r.get("num", "").startswith("周三")]
print("回填后有赛果的：",
      [(r["num"], (r.get("result") or {}).get("score")) for r in after
       if (r.get("result") or {}).get("score")])

# 重算串关历史（新语义：一腿错 → 整组 miss）
stats = fd.check_parlay_history()
print(f"\n统计：total={stats['total']} hit={stats['hit']} miss={stats['miss']} "
      f"pending={stats['pending']} hitRate={stats['hitRate']}%")

# 10-21 轨迹复核
ph = json.load(open(f"{BASE}/parlay_history.json", encoding="utf-8"))
today = [r for r in ph["records"] if r.get("date") == "2026-10-21"]
print("\n=== 10-21 轨迹（重判后） ===")
for r in sorted(today, key=lambda x: x.get("savedAt", "")):
    legs = " + ".join(
        f"{l['num']}{l.get('label','')}({l.get('hit') or '未开'})" for l in r.get("legs") or [])
    print(f"  {r.get('savedAt')} {r['type']} {r['totalOdds']}倍 [{r.get('result')}] {legs}")

m = json.load(open(f"{BASE}/matches.json", encoding="utf-8"))
m["parlayHistory"] = stats
json.dump(m, open(f"{BASE}/matches.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nmatches.json parlayHistory 已更新")
