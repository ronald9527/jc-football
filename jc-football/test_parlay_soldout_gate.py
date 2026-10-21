# -*- coding: utf-8 -*-
"""test_parlay_soldout_gate.py —— v1.7.34 停售腿门测试
场景：
A. pending + 当前引擎 + 腿停售        → 该档解锁（返回 None），记录删除
B. pending + 当前引擎 + 全部在售      → 正常锁定回放
C. 已判定(hit) + 腿停售               → 保留在历史，但不回放为当前推荐（v1.7.34 rebuild 只回放 pending）
C2. 已判定 + pending 并存             → 只回放 pending，已判定组不占推荐位
D. today_matches 为空/None            → 不删任何记录（防抓取异常误删）
E. pending + 旧引擎 + 腿停售(已开球)  → 删除（修复版本门漏洞：旧逻辑因「已开球」保留）
"""
import sys, os, json, time as _time, importlib.util, shutil

BASE = "/workspace/jc-football"
PH = f"{BASE}/parlay_history.json"

# 时间补丁回今日 20:00（003 已停售场景）
_real_strftime = _time.strftime
FAKE_TODAY = "2026-10-21"
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-21 20:00:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: _time.mktime(_time.strptime("2026-10-21 20:00:00", "%Y-%m-%d %H:%M:%S"))

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

CUR_VER = fd._parlay_engine_version()
# 本测试覆盖 v1.7.33+ 的锁定语义；引擎升版后自动跟随当前版本
def _ver_tuple(v):
    m = __import__("re").match(r'v?(\d+)\.(\d+)\.(\d+)', v or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
assert _ver_tuple(CUR_VER) >= (1, 7, 33), f"引擎版本 {CUR_VER} 过低（预期 ≥ v1.7.33）"

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))

# 在售清单（19:01 快照：001/002/003 已停售）
SOLD = [{"num": n} for n in
        ["周三004", "周三005", "周三006", "周三007", "周三008",
         "周三009", "周三010", "周三011", "周三012", "周三013"]]

def _rec(tier, n_type, legs, engine, result="pending", extra=None):
    r = {"date": FAKE_TODAY, "type": f"{tier}{n_type}",
         "totalOdds": 3.0, "avgConfidence": 66.0, "legs": legs,
         "recommended": True, "ev": -10.0, "result": result,
         "savedAt": f"{FAKE_TODAY} 12:00:00", "engineVer": engine,
         "snapshot": {"tier": tier, "type": n_type, "totalOdds": 3.0,
                      "legs": legs, "realEv": 5.0, "realHit": 30.0,
                      "beHit": 28.0, "recommended": True, "rake": 21.5}}
    if extra:
        r.update(extra)
    return r

def _legs(*nums):
    return [{"num": n, "play": "胜平负", "label": "主胜", "odds": 1.7,
             "playKey": "had", "dir": "h", "kickoff": "2026-10-22 00:45"}
            for n in nums]

def run_case(name, records, matches):
    """写入测试记录 → 跑 load_locked_parlay → 返回 (safe, hist_after)"""
    shutil.copy(PH, f"{PH}.testbak")
    try:
        json.dump({"updatedAt": "", "records": records},
                  open(PH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        safe, _high = fd.load_locked_parlay(matches)
        after = json.load(open(PH, encoding="utf-8"))["records"]
        return safe, after
    finally:
        shutil.move(f"{PH}.testbak", PH)

print(f"引擎版本：{CUR_VER}")

# ===== A. 当前引擎 + 腿停售 → 该档解锁，其余在售档位照常锁定 =====
print("\n[A] 当前引擎 + 腿停售（013在售/003停售）")
recs = [_rec("稳健", "2串1", _legs("周三013", "周三003"), CUR_VER),
        _rec("均衡", "3串1", _legs("周三013", "周三005", "周三006"), CUR_VER)]
safe, after = run_case("A", recs, SOLD)
check("稳健档（含停售腿003）解锁为 None", safe is not None and safe.get("parlay2") is None,
      f"safe={safe}")
check("均衡档（全在售）仍锁定回放", safe is not None and safe.get("parlay3") is not None)
check("均衡回放腿正确（013+005+006）",
      safe and safe.get("parlay3") and
      [l["num"] for l in safe["parlay3"]["legs"]] == ["周三013", "周三005", "周三006"])
check("停售腿记录已删除（无双轨）", len(after) == 1 and after[0]["type"] == "均衡3串1",
      f"after={[r['type'] for r in after]}")

# ===== B. 当前引擎 + 全部在售 → 锁定 =====
print("\n[B] 当前引擎 + 全部在售")
recs = [_rec("稳健", "2串1", _legs("周三012", "周三013"), CUR_VER)]
safe, after = run_case("B", recs, SOLD)
check("稳健档正常锁定回放", safe is not None and safe.get("parlay2") is not None)
check("记录保留不动", len(after) == 1)

# ===== C. 已判定 + 腿停售 → 保留在历史，不回放为当前推荐 =====
print("\n[C] 已判定(hit) + 腿停售（战绩凭证保留，但不占推荐位）")
recs = [_rec("稳健", "2串1", _legs("周三001", "周三002"), CUR_VER, result="hit",
             extra={"resultAt": f"{FAKE_TODAY} 20:30:00"})]
safe, after = run_case("C", recs, SOLD)
check("已判定记录保留", len(after) == 1)
check("已判定档位不作为当前推荐回放（safe=None）", safe is None,
      f"safe={safe}")

# ===== C2. 已判定 + pending 并存 → 只回放 pending =====
print("\n[C2] 已判定(miss) + pending 并存 —— 用户跟过的旧组不能占推荐位")
recs = [_rec("稳健", "2串1", _legs("周三013", "周三003"), CUR_VER, result="miss",
             extra={"resultAt": f"{FAKE_TODAY} 20:30:00"}),   # 含已停售的003，已判错
        _rec("稳健", "2串1", _legs("周三012", "周三013"), CUR_VER)]  # 19:45 重生，pending
safe, after = run_case("C2", recs, SOLD)
check("已判定旧组保留在历史", any(r["result"] == "miss" for r in after))
check("pending 新组保留在历史", any(r["result"] == "pending" for r in after))
check("回放的是 pending 新组（012+013），非已判错旧组",
      safe and safe.get("parlay2") and
      [l["num"] for l in safe["parlay2"]["legs"]] == ["周三012", "周三013"],
      f"safe={safe}")

# ===== D. matches 为空 → 不删 =====
print("\n[D] today_matches 为空/None（防抓取异常误删）")
recs = [_rec("稳健", "2串1", _legs("周三013", "周三003"), CUR_VER)]
safe, after = run_case("D1", recs, [])
check("空 matches：记录不删", len(after) == 1)
check("空 matches：正常锁定", safe is not None and safe.get("parlay2") is not None)
safe, after = run_case("D2", recs, None)
check("None matches：记录不删", len(after) == 1)

# ===== E. 旧引擎 + 腿停售（已开球）→ 删（修复版本门漏洞）=====
print("\n[E] 旧引擎(v1.7.32) + 腿停售(003 已开球) —— 旧逻辑会因「已开球」保留")
recs = [_rec("稳健", "2串1", _legs("周三013", "周三003"), "v1.7.32"),
        _rec("均衡", "3串1", _legs("周三005", "周三006", "周三007"), "v1.7.32")]
safe, after = run_case("E", recs, SOLD)
check("旧引擎+停售腿记录删除（漏洞已修）",
      all(r["type"] != "稳健2串1" for r in after),
      f"after={[r['type'] for r in after]}")
check("旧引擎+在售腿+未开球由版本门解锁", all(r["type"] != "均衡3串1" for r in after))
check("全部解锁后 safe=None（主流程全新生成）", safe is None)

# ===== F. 回归：无今日记录 =====
print("\n[F] 回归：无今日记录 → (None, None)")
safe, after = run_case("F", [_rec("稳健", "2串1", _legs("周三012", "周三013"),
                                  CUR_VER, extra={"date": "2026-10-20"})], SOLD)
check("无今日记录返回 None", safe is None)

print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
