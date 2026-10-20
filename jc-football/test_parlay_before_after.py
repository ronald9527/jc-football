# -*- coding: utf-8 -*-
"""前后对照实验：用 2026-10-19 发布时的赔率场景，对比旧/新代码下
「周一004 罗德兹vs蒙彼利埃」在三档中是否出现不同说法。
"""
import sys, json, time as _time, importlib.util

_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-19"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-19 12:00:00", "%Y-%m-%d %H:%M:%S"))
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else _real_strftime(fmt, *a, **k)
_time.time = lambda: FAKE_NOW

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def scenario_matches():
    """matches.json + 补丁为 10-19 发布时赔率"""
    data = json.load(open("/workspace/jc-football/matches.json"))
    matches = json.loads(json.dumps(data["matches"]))  # deep copy
    for m in matches:
        if m.get("num") == "周一004":
            m["had"]["a"] = 2.2            # 发布时：客胜 2.2
            m["hhad"]["h"] = 1.51          # 发布时：让球+1 让胜 1.51
        elif m.get("num") == "周一008":
            m["had"]["h"] = 1.53           # 发布时：主胜 1.53
        elif m.get("num") == "周一005":
            m["hhad"]["h"] = 2.1           # 发布时：让球-1 让胜 2.1
    return matches

def run(mod_path, mod_name):
    mod = load_module(mod_path, mod_name)
    res = mod.gen_safe_parlay(scenario_matches())
    opinions = {}
    detail = []
    for key in ["parlay2", "parlay3", "parlay4"]:
        p = res.get(key)
        if not p:
            detail.append(f"[{key}] None")
            continue
        legs = " + ".join(f"{l['num']}{l['play']}{l['label']}@{l['odds']}" for l in p["legs"])
        detail.append(f"[{p['tier']}] {p['type']} 总赔{p['totalOdds']} EV{p['ev']:+.1f}% :: {legs}")
        for l in p["legs"]:
            opinions.setdefault(l["num"], set()).add((l["play"], l["label"]))
    conflict = {n: v for n, v in opinions.items() if len(v) > 1}
    return detail, conflict

print("########## 旧代码（修复前，git 653d9d8）##########")
d1, c1 = run("/tmp/old_fetch_daily.py", "old_fd")
for line in d1:
    print(" ", line)
print("  → 同场不同说法：", "、".join(f"{n}:{v}" for n, v in c1.items()) if c1 else "无")

print()
print("########## 新代码（v1.7.24 单场单意见）##########")
d2, c2 = run("/workspace/jc-football/fetch_daily.py", "new_fd")
for line in d2:
    print(" ", line)
print("  → 同场不同说法：", "、".join(f"{n}:{v}" for n, v in c2.items()) if c2 else "无")

print()
print("结论：", "修复生效——旧代码自相矛盾，新代码全场一个说法" if (c1 and not c2) else "需人工检查")
sys.exit(0 if (c1 and not c2) else 1)
