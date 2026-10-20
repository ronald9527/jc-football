# -*- coding: utf-8 -*-
"""隔离测试：复现 2026-10-19 销售日的三档串关生成（不依赖真实当前时间）。
用法：python3 test_parlay_canon.py [--fix]
"""
import sys, json, time as _time

# ---- 时间补丁：回到 2026-10-19 12:00（销售日当天、所有比赛未开赛）----
_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-19"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-19 12:00:00", "%Y-%m-%d %H:%M:%S"))

def _fake_strftime(fmt, *a, **k):
    if fmt == "%Y-%m-%d":
        return FAKE_TODAY
    return _real_strftime(fmt, *a, **k)

_time.strftime = _fake_strftime
_time.time = lambda: FAKE_NOW

sys.path.insert(0, "/workspace/jc-football")
import fetch_daily as fd

data = json.load(open("/workspace/jc-football/matches.json"))
matches = data["matches"]

res = fd.gen_safe_parlay(matches)

print("=" * 70)
print(f"候选数: {res.get('candidateCount')} | 生成: {res.get('generatedAt')}")
print("=" * 70)

# 一致性断言：同一场比赛在三档中必须只有一个说法
opinions = {}
ok = True
for key in ["parlay2", "parlay3", "parlay4"]:
    p = res.get(key)
    if not p:
        print(f"[{key}] None（今日无满足条件的组合）")
        continue
    print(f"\n[{p['tier']}] {p['type']} | 总赔{p['totalOdds']} | EV{p['ev']:+.1f}% | "
          f"真实同指命中{p.get('realHit')}% | 推荐={p['recommended']} | 仓位{p['suggestStake']}%")
    for l in p["legs"]:
        print(f"   {l['num']} {l['home']}vs{l['away']} {l['play']}{l['label']} @{l['odds']} "
              f"信心{l['confidence']} 同指{l.get('sameOddsHit')}%(n={l.get('sameOddsN')}) 五维{l.get('fiveDim')}")
        op = (l["play"], l["label"], l["odds"])
        if l["num"] in opinions and opinions[l["num"]] != op:
            ok = False
            print(f"   *** 矛盾：{l['num']} 在另一档为 {opinions[l['num']]}，本档为 {op} ***")
        opinions[l["num"]] = op

print("\n" + "=" * 70)
print("一致性检查：" + ("PASS——同一场比赛全天只有一个说法" if ok else "FAIL——存在同场不同说法"))
sys.exit(0 if ok else 1)
