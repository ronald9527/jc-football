# -*- coding: utf-8 -*-
"""test_parlay_judge_before_gate.py —— v1.7.34「先判定后门禁」顺序回归测试

事故还原（2026-10-21 定时任务 20:32 批次）：
  backfill 把 003 的官方赛果（0:3 主负）写进 history.json 的同一秒，
  load_locked_parlay 的停售腿门先把含 003 的三条 pending 记录删掉，
  之后的 check_parlay_history 已无记录可判——用户跟过的推荐从历史里
  凭空消失，无法对账。

修复：主流程把 check_parlay_history() 前移到 load_locked_parlay() 之前。
本测试锁定该行为：
1. 先 check 后 load：003 记录被判 miss 且保留（门禁不删已判定记录）
2. 反证（旧顺序）：先 load 后 check：记录被删、历史里查无此组
3. 判定证据落盘：leg 上带 actualScore/legHit
4. 语义不回归：腿已判错但其余腿未开赛 → miss（v1.7.34 decided_miss）
"""
import sys, json, time as _time, importlib.util, shutil

BASE = "/workspace/jc-football"
PH = f"{BASE}/parlay_history.json"
HF = f"{BASE}/history.json"

_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-21"
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-21 20:32:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: _time.mktime(_time.strptime("2026-10-21 20:32:00", "%Y-%m-%d %H:%M:%S"))

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

CUR_VER = fd._parlay_engine_version()

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))

# 在售清单（20:32 时 001/002/003 已停售）
SOLD = [{"num": n} for n in
        ["周三004", "周三005", "周三006", "周三007", "周三008",
         "周三009", "周三010", "周三011", "周三012", "周三013"]]

def _leg(num, label, dir_, kickoff):
    return {"num": num, "play": "胜平负", "label": label, "odds": 1.7,
            "playKey": "spf", "dir": dir_, "kickoff": kickoff}

def _prec(num, score, had, date=FAKE_TODAY):
    hs, as_ = score.split(":")
    return {"id": f"t{num}", "num": num, "date": date, "goal": 0,
            "result": {"score": score, "had": had,
                       "home": int(hs), "away": int(as_), "cancel": False}}

# 事故现场：12:31 生成的稳健 3.39（013 未开 + 003 已开已负），引擎旧版本
ACCIDENT_REC = {
    "date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 3.39,
    "avgConfidence": 70.0, "recommended": False, "ev": -12.0,
    "result": "pending", "savedAt": f"{FAKE_TODAY} 12:31:36",
    "engineVer": "v1.7.33",
    "legs": [_leg("周三013", "主胜", "h", "2026-10-22 08:30"),
             _leg("周三003", "主胜", "h", "2026-10-21 18:00")],
    "snapshot": {"tier": "稳健", "type": "2串1", "totalOdds": 3.39,
                 "legs": [], "realEv": -12.0, "realHit": 20.0,
                 "beHit": 29.5, "recommended": False, "rake": 21.5},
}
HIST_RECS = [_prec("周三003", "0:3", "a"), _prec("周三001", "0:0", "d")]

def run(order):
    """order='check_first' 复现修复后的顺序；'load_first' 复现旧顺序。
    返回 (records_after, safe)"""
    shutil.copy(PH, f"{PH}.testbak")
    shutil.copy(HF, f"{HF}.testbak")
    try:
        json.dump({"updatedAt": "", "records": [json.loads(json.dumps(ACCIDENT_REC))]},
                  open(PH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        hd = json.load(open(HF, encoding="utf-8"))
        hd["records"] = HIST_RECS + [r for r in hd["records"]
                                     if not (r.get("date") == FAKE_TODAY and str(r.get("num", "")).startswith("周三"))]
        json.dump(hd, open(HF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if order == "check_first":
            fd.check_parlay_history()
            safe, _ = fd.load_locked_parlay(SOLD)
        else:
            safe, _ = fd.load_locked_parlay(SOLD)
            fd.check_parlay_history()
        after = json.load(open(PH, encoding="utf-8"))["records"]
        return after, safe
    finally:
        shutil.move(f"{PH}.testbak", PH)
        shutil.move(f"{HF}.testbak", HF)

# ===== 1. 修复后顺序：先判定后门禁 =====
print("[1] 修复后：check → load（003 赛果已在 history.json）")
after, safe = run("check_first")
rec = after[0] if after else None
check("记录保留（未被停售腿门删掉）", len(after) == 1, f"after={len(after)}")
check("记录被判 miss（003 主胜 0:3 主负）", rec and rec.get("result") == "miss",
      f"result={rec.get('result') if rec else None}")
check("判定证据落盘：003 腿 actualScore=0:3 且 legHit=False",
      rec and rec["legs"][1].get("actualScore") == "0:3" and rec["legs"][1].get("legHit") is False)
check("已判定档位不回放为当前推荐（safe=None，等主流程用可下单比赛重生）",
      safe is None, f"safe={safe}")

# ===== 2. 反证：旧顺序（先门禁后判定）会丢失记录 =====
print("\n[2] 反证：旧顺序 load → check（事故复现）")
after, safe = run("load_first")
check("记录被停售腿门删除（历史里查无此组——这正是要修的事故）",
      len(after) == 0, f"after={len(after)}")
check("当前推荐为空（无在售档位可回放）", safe is None)

# ===== 3. 语义不回归：混合态一腿错即 miss =====
print("\n[3] 回归：decided_miss 语义（013 未开 + 003 已错 → miss）")
after, safe = run("check_first")
rec = after[0] if after else None
check("仍为 miss（不等 013 开赛）", rec and rec.get("result") == "miss")

print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
