# -*- coding: utf-8 -*-
"""引擎版本门测试（v1.7.25）：模拟定时任务沙箱场景——
历史文件里 10-20 是 v1.7.23 旧引擎产物（无 engineVer、pending、未开球），
新代码（v1.7.25）跑 load_locked_parlay 应：解锁并删除旧记录 → 当日重生 →
save_parlay_history 写入带 engineVer 的新记录 → 再次调用正常锁定新推荐。
"""
import sys, json, time as _time, importlib.util, shutil, os

BASE = "/workspace/jc-football"
_real_strftime = _time.strftime
_real_time = _time.time
FAKE_TODAY = "2026-10-20"
FAKE_NOW = _time.mktime(_time.strptime("2026-10-20 13:10:00", "%Y-%m-%d %H:%M:%S"))
_time.strftime = lambda fmt, *a, **k: FAKE_TODAY if fmt == "%Y-%m-%d" else (
    "2026-10-20 13:10:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
_time.time = lambda: FAKE_NOW

HIST = f"{BASE}/parlay_history.json"
BAK = f"{BASE}/parlay_history.json.bak_vgate_test"
shutil.copy(HIST, BAK)

spec = importlib.util.spec_from_file_location("fd", f"{BASE}/fetch_daily.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fd"] = fd
spec.loader.exec_module(fd)

# 构造场景 A：本地历史 + 3 条 10-20 旧引擎产物（仿任务沙箱 58 条状态）
hist = json.load(open(HIST, encoding="utf-8"))
records = hist.get("records", [])
records = [r for r in records if r.get("date") != FAKE_TODAY]
old_1020 = [
    {"date": FAKE_TODAY, "type": "稳健2串1", "totalOdds": 2.22,
     "avgConfidence": 72.8, "recommended": False, "ev": -25.1, "result": "pending",
     "savedAt": "2026-10-20 09:06:52",
     "legs": [{"num": "周二004", "play": "胜平负", "playKey": "had", "dir": "h",
               "label": "主胜", "odds": 1.48, "kickoff": "2026-10-21 00:45"},
              {"num": "周二001", "play": "胜平负", "playKey": "had", "dir": "h",
               "label": "主胜", "odds": 1.5, "kickoff": "2026-10-21 00:45"}]},
    {"date": FAKE_TODAY, "type": "均衡3串1", "totalOdds": 6.27,
     "avgConfidence": 66.4, "recommended": False, "ev": -34.2, "result": "pending",
     "savedAt": "2026-10-20 09:06:52",
     "legs": [{"num": "周二001", "play": "胜平负", "playKey": "had", "dir": "h",
               "label": "主胜", "odds": 1.5, "kickoff": "2026-10-21 00:45"},
              {"num": "周二009", "play": "胜平负", "playKey": "had", "dir": "a",
               "label": "客胜", "odds": 1.9, "kickoff": "2026-10-21 00:45"},
              {"num": "周二006", "play": "胜平负", "playKey": "had", "dir": "a",
               "label": "客胜", "odds": 2.2, "kickoff": "2026-10-21 03:00"}]},
    {"date": FAKE_TODAY, "type": "精锐4串1", "totalOdds": 6.03,
     "avgConfidence": 68.7, "recommended": False, "ev": -42.0, "result": "pending",
     "savedAt": "2026-10-20 09:06:52",
     "legs": [{"num": "周二010", "play": "胜平负", "playKey": "had", "dir": "h",
               "label": "主胜", "odds": 1.43, "kickoff": "2026-10-21 00:45"},
              {"num": "周二004", "play": "胜平负", "playKey": "had", "dir": "h",
               "label": "主胜", "odds": 1.48, "kickoff": "2026-10-21 00:45"},
              {"num": "周二001", "play": "胜平负", "playKey": "had", "dir": "h",
               "label": "主胜", "odds": 1.5, "kickoff": "2026-10-21 00:45"},
              {"num": "周二009", "play": "胜平负", "playKey": "had", "dir": "a",
               "label": "客胜", "odds": 1.9, "kickoff": "2026-10-21 00:45"}]},
]
hist["records"] = records + old_1020
json.dump(hist, open(HIST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
n_before = len(hist["records"])
print(f"场景A就绪：{n_before} 条（含 10-20 旧引擎产物 3 条）")

try:
    # 1) 版本门应解锁（返回 None）并删除旧记录
    sp, hp = fd.load_locked_parlay()
    after = json.load(open(HIST, encoding="utf-8"))["records"]
    n_1020 = len([r for r in after if r.get("date") == FAKE_TODAY])
    print(f"load_locked_parlay -> ({sp is not None})；10-20 剩余记录 {n_1020} 条")
    assert sp is None, "版本门未触发：旧引擎产物仍被锁定"
    assert n_1020 == 0, "旧引擎产物未删除"

    # 2) 新引擎当日重生
    built = json.load(open("/tmp/built_1020.json", encoding="utf-8"))
    matches = [m for m in json.loads(json.dumps(built["matches"])) if m.get("date") == FAKE_TODAY]
    safe = fd.gen_safe_parlay(matches)
    fd.save_parlay_history(safe, None)
    after2 = json.load(open(HIST, encoding="utf-8"))["records"]
    t = [r for r in after2 if r.get("date") == FAKE_TODAY]
    print(f"重生后 10-20 记录：{[(r['type'], r['totalOdds'], r.get('engineVer')) for r in t]}")
    assert len(t) == 1 and t[0]["type"] == "稳健2串1" and t[0].get("engineVer") == "v1.7.25"

    # 3) 再次锁定：新引擎产物正常锁定
    sp2, _ = fd.load_locked_parlay()
    assert sp2 is not None and sp2.get("locked"), "新引擎产物未被锁定"
    print(f"再锁定 OK：稳健 {sp2['parlay2']['totalOdds']} 倍 locked={sp2['locked']}")

    # 4) 已判定记录不删（把新记录改成 miss，版本门应放过——其实同版本直接锁定）
    print("\n结论：引擎版本门生效——旧引擎未开球推荐当日解锁重生，新产物带版本号锁定")
finally:
    shutil.copy(BAK, HIST)
    os.remove(BAK)
    print("（历史文件已还原）")
