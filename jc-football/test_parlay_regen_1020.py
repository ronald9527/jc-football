# -*- coding: utf-8 -*-
"""v1.7.25 重生实验：用 2026-10-20 线上真实数据（BUILT_DATA 快照）跑新代码，
验证三档串关：
  1. 赔率单调：精锐4串1 总赔 > 均衡3串1 > 稳健2串1（修复 6.03 < 6.27 倒挂）
  2. 单场单意见：同一场比赛在三档中只有一个 (玩法, 选项)
  3. 与 09:06 旧推荐对比
"""
import sys, json, time as _time, importlib.util

_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-20"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-20 12:30:00", "%Y-%m-%d %H:%M:%S"))
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else _real_strftime(fmt, *a, **k)
_time.time = lambda: FAKE_NOW


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_1020_matches():
    """从线上 BUILT_DATA 快照提取 10-20 当日 10 场完整数据"""
    d = json.load(open("/tmp/built_1020.json", encoding="utf-8"))
    matches = json.loads(json.dumps(d["matches"]))
    matches = [m for m in matches if m.get("date") == FAKE_TODAY]
    return matches


def run(mod_path, mod_name, matches):
    mod = load_module(mod_path, mod_name)
    res = mod.gen_safe_parlay(matches)
    tiers = {}
    opinions = {}
    for key in ["parlay2", "parlay3", "parlay4"]:
        p = res.get(key)
        if not p:
            tiers[key] = None
            continue
        tiers[key] = p
        for l in p["legs"]:
            opinions.setdefault(l["num"], set()).add((l["play"], l["label"]))
    return res, tiers, opinions


def show(res, tiers, opinions, tag):
    print(f"########## {tag} ##########")
    print(f"  candidateCount={res.get('candidateCount')} poolNotes={res.get('poolNotes')}")
    conflict = {n: v for n, v in opinions.items() if len(v) > 1}
    for key in ["parlay2", "parlay3", "parlay4"]:
        p = tiers[key]
        if not p:
            print(f"  [{key}] 无达标组合（留空）")
            continue
        legs = " + ".join(
            f"{l['num']}{l['play']}{l['label']}@{l['odds']}"
            f"(同指{l.get('sameOddsHit')}%/n={l.get('sameOddsN')} 信心{l.get('confidence')})"
            for l in p["legs"])
        print(f"  [{p['tier']}] {p['type']} 总赔{p['totalOdds']} (门{ p.get('oddsGate')}) "
              f"联合{p['jointProb']}% 真实命中{p.get('realHit')}% EV{p['ev']:+.1f}%")
        print(f"      {legs}")
    print(f"  → 同场不同说法：{'、'.join(f'{n}:{v}' for n, v in conflict.items()) if conflict else '无'}")
    odds = [tiers[k]["totalOdds"] for k in ["parlay2", "parlay3", "parlay4"] if tiers.get(k)]
    print(f"  → 总赔序列：{odds}  单调递增：{all(odds[i] < odds[i+1] for i in range(len(odds)-1))}")
    return conflict, odds


matches = load_1020_matches()
print(f"载入 10-20 比赛 {len(matches)} 场：{[m['num'] for m in matches]}")
print()

res_new, tiers_new, op_new = run("/workspace/jc-football/fetch_daily.py", "new_fd_725", matches)
conflict_new, odds_new = show(res_new, tiers_new, op_new, "新代码 v1.7.25（赔率单调+均衡档调整）")
print()

print("########## 对照：09:06 旧推荐（线上 locked 版本）##########")
old = json.load(open("/tmp/built_1020.json", encoding="utf-8"))["safeParlay"]
for key in ["parlay2", "parlay3", "parlay4"]:
    p = old.get(key)
    if p:
        legs = " + ".join(f"{l['num']}{l['play']}{l['label']}@{l['odds']}" for l in p["legs"])
        print(f"  [{p['tier']}] {p['type']} 总赔{p['totalOdds']} EV{p['ev']:+.1f}% :: {legs}")
print(f"  → 倒挂：精锐4串1 {old['parlay4']['totalOdds']} < 均衡3串1 {old['parlay3']['totalOdds']}")
print()

ok_mono = len(odds_new) == len(set(odds_new)) and all(
    odds_new[i] < odds_new[i + 1] for i in range(len(odds_new) - 1))
ok_canon = not conflict_new
print("结论：",
      "v1.7.25 修复生效——三档赔率严格单调递增且同场单意见" if (ok_mono and ok_canon)
      else f"需人工检查（单调={ok_mono} 单意见={ok_canon}）")
sys.exit(0 if (ok_mono and ok_canon) else 1)
