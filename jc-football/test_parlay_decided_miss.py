# -*- coding: utf-8 -*-
"""test_parlay_decided_miss.py —— v1.7.34 串关判定语义测试
「任一腿已开奖且错误 → 整组立即判 miss」，不再因其余腿未开赛挂成 pending。

场景：
1. 混合态：一条腿已赛果判错 + 其余腿未开赛        → 整组 miss（不等其余腿）
2. 混合态：一条腿已赛果判对 + 其余腿未开赛          → 整组 pending（仍活着）
3. 全部腿已赛果且全中                              → hit
4. 全部腿已赛果且有错                              → miss
5. 全部腿未开赛                                    → pending
6. 判错腿的 actualScore/legHit 落到 leg 上（页面追溯用）
7. 回归：已判定记录不被误改（miss 记录重跑仍 miss）
"""
import sys, json, time as _time, importlib.util, shutil

BASE = "/workspace/jc-football"
PH = f"{BASE}/parlay_history.json"
HF = f"{BASE}/history.json"

_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-21"
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-21 21:30:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: _time.mktime(_time.strptime("2026-10-21 21:30:00", "%Y-%m-%d %H:%M:%S"))

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))

def run_case(name, parlay_recs, history_recs):
    """写入测试用 parlay_history + history.json → 跑 check_parlay_history → 返回 (stats, recs_after)"""
    shutil.copy(PH, f"{PH}.testbak")
    shutil.copy(HF, f"{HF}.testbak")
    try:
        json.dump({"updatedAt": "", "records": parlay_recs},
                  open(PH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        hd = json.load(open(HF, encoding="utf-8"))
        hd["records"] = history_recs + [r for r in hd["records"]
                                        if r.get("date") != FAKE_TODAY or not str(r.get("num", "")).startswith("周三")]
        json.dump(hd, open(HF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        stats = fd.check_parlay_history()
        after = json.load(open(PH, encoding="utf-8"))["records"]
        return stats, after
    finally:
        shutil.move(f"{PH}.testbak", PH)
        shutil.move(f"{HF}.testbak", HF)

def _leg(num, label, dir_, kickoff="2026-10-22 00:45"):
    return {"num": num, "play": "胜平负", "label": label, "odds": 1.8,
            "playKey": "spf", "dir": dir_, "kickoff": kickoff}

def _prec(num, score, had, date=FAKE_TODAY):
    """history.json 里已开奖的比赛记录"""
    hs, as_ = score.split(":")
    return {"id": f"t{num}", "num": num, "date": date, "goal": 0,
            "result": {"score": score, "had": had,
                       "home": int(hs), "away": int(as_), "cancel": False}}

# ===== 1. 一腿错 + 其余未开 → 整组 miss =====
print("[1] 003 主胜已败(0:3) + 013 未开赛 → 整组立即 miss")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 3.39,
          "legs": [_leg("周三013", "主胜", "h"), _leg("周三003", "主胜", "h", "2026-10-21 18:00")],
          "result": "pending", "savedAt": f"{FAKE_TODAY} 12:31:36"}]
hist = [_prec("周三003", "0:3", "a")]
stats, after = run_case("1", recs, hist)
check("整组判 miss（不等 013 开赛）", after[0]["result"] == "miss", f"result={after[0]['result']}")
check("统计 miss+1 pending=0", stats["miss"] == 1 and stats["pending"] == 0,
      f"miss={stats['miss']} pending={stats['pending']}")
check("判错腿带 actualScore/legHit 证据",
      after[0]["legs"][1].get("actualScore") == "0:3" and after[0]["legs"][1].get("legHit") is False)
check("未开腿不带赛果字段", "actualScore" not in after[0]["legs"][0])

# ===== 2. 一腿对 + 其余未开 → pending =====
print("\n[2] 003 主胜已中 + 013 未开赛 → 整组仍 pending（活着）")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 3.39,
          "legs": [_leg("周三013", "主胜", "h"), _leg("周三003", "主胜", "h", "2026-10-21 18:00")],
          "result": "pending", "savedAt": f"{FAKE_TODAY} 12:31:36"}]
hist = [_prec("周三003", "2:0", "h")]
stats, after = run_case("2", recs, hist)
check("整组保持 pending", after[0]["result"] == "pending", f"result={after[0]['result']}")
check("已中腿带 legHit=True", after[0]["legs"][1].get("legHit") is True)

# ===== 3. 全部已赛果全中 → hit =====
print("\n[3] 两腿全部已赛果且全中 → hit")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 3.39,
          "legs": [_leg("周三012", "主胜", "h", "2026-10-21 18:00"),
                   _leg("周三013", "主胜", "h", "2026-10-21 18:00")],
          "result": "pending", "savedAt": f"{FAKE_TODAY} 12:31:36"}]
hist = [_prec("周三012", "1:0", "h"), _prec("周三013", "2:1", "h")]
stats, after = run_case("3", recs, hist)
check("整组判 hit", after[0]["result"] == "hit", f"result={after[0]['result']}")

# ===== 4. 全部已赛果有错 → miss =====
print("\n[4] 两腿全部已赛果、一腿错 → miss")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 3.39,
          "legs": [_leg("周三012", "主胜", "h", "2026-10-21 18:00"),
                   _leg("周三013", "主胜", "h", "2026-10-21 18:00")],
          "result": "pending", "savedAt": f"{FAKE_TODAY} 12:31:36"}]
hist = [_prec("周三012", "0:0", "d"), _prec("周三013", "2:1", "h")]
stats, after = run_case("4", recs, hist)
check("整组判 miss", after[0]["result"] == "miss", f"result={after[0]['result']}")

# ===== 5. 全部未开 → pending =====
print("\n[5] 两腿全部未开赛 → pending")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 2.7,
          "legs": [_leg("周三012", "主胜", "h"), _leg("周三013", "主胜", "h")],
          "result": "pending", "savedAt": f"{FAKE_TODAY} 19:45:00"}]
stats, after = run_case("5", recs, [])
check("整组保持 pending", after[0]["result"] == "pending", f"result={after[0]['result']}")

# ===== 6. 回归：已判定 miss 记录重跑不变 =====
print("\n[6] 回归：已判定 miss 的重跑仍 miss（判定稳定）")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 3.39,
          "legs": [_leg("周三013", "主胜", "h"), _leg("周三003", "主胜", "h", "2026-10-21 18:00")],
          "result": "miss", "savedAt": f"{FAKE_TODAY} 12:31:36",
          "restoredNote": "当日修订删除后按官方赛果补判"}]
hist = [_prec("周三003", "0:3", "a")]
stats, after = run_case("6", recs, hist)
check("仍为 miss（幂等）", after[0]["result"] == "miss", f"result={after[0]['result']}")
check("补判备注保留", after[0].get("restoredNote", "").startswith("当日修订删除后"))

# ===== 7. 回归：已判定 hit 记录重跑不变 =====
print("\n[7] 回归：已判定 hit 的重跑仍 hit")
recs = [{"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 2.5,
          "legs": [_leg("周三012", "主胜", "h", "2026-10-21 18:00"),
                   _leg("周三013", "主胜", "h", "2026-10-21 18:00")],
          "result": "hit", "savedAt": f"{FAKE_TODAY} 12:31:36"}]
hist = [_prec("周三012", "1:0", "h"), _prec("周三013", "2:1", "h")]
stats, after = run_case("7", recs, hist)
check("仍为 hit（幂等）", after[0]["result"] == "hit", f"result={after[0]['result']}")

print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
