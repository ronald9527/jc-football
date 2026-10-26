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
    cur_ver = fd._parlay_engine_version()
    print(f"重生后 10-20 记录：{[(r['type'], r['totalOdds'], r.get('engineVer')) for r in t]}")
    # v1.7.43：10-20 当日候选的同指命中率全部落在 45-60% 反向区间（回测实证
    # 16.7% vs 市场期望 48.8%，单侧 p=0.0016），新硬门槛（so_floor 60/65、
    # 4 串零容忍）把三档全部剔除——重生为空是预期行为：那 3 组旧推荐全部
    # miss，本就不该发布，版本门 + 硬门槛双重保证缺陷产物不会回流。
    if t:
        assert all(r.get("engineVer") == cur_ver for r in t), \
            f"重生记录的 engineVer 应为当前引擎版本 {cur_ver}"
    else:
        print("  （v1.7.43 硬门槛：10-20 反向区间软腿全剔除，重生为空=不发布缺陷推荐）")

    # 3) 再次锁定：新引擎产物正常锁定；重生为空时当日无推荐
    sp2, _ = fd.load_locked_parlay()
    if t:
        assert sp2 is not None and sp2.get("locked"), "新引擎产物未被锁定"
        print(f"再锁定 OK：稳健 {sp2['parlay2']['totalOdds']} 倍 locked={sp2['locked']}")
    else:
        assert sp2 is None, "重生为空时不应有锁定产物"
        print("  再锁定：当日无推荐（符合预期）")

    # 4) 已判定记录不删（把新记录改成 miss，版本门应放过——其实同版本直接锁定）
    print("\n结论：引擎版本门生效——旧引擎未开球推荐当日解锁重生，新产物带版本号锁定")

    # ===== 场景B（v1.7.43 新增）：新规则下仍有档位的数据日，走完整「解锁→重生→带版本→再锁定」=====
    # 场景A 的 10-20 数据在新硬门槛下全档剔除，无法验证「重生产物带版本号并锁定」的
    # 正向路径；用 10-24 数据（新规则下稳健/均衡两档达标）补全该路径。
    print("\n===== 场景B：10-24 数据 · 完整重生锁定路径 =====")
    built_now = json.load(open(f"{BASE}/matches.json", encoding="utf-8"))
    matches_b = [m for m in built_now["matches"] if m.get("date") == "2026-10-24"]
    assert matches_b, "matches.json 无 10-24 数据"
    # 时间补丁改到 10-24 09:25（当日比赛 13:00 起开球，全部未开赛）
    _time.strftime = lambda fmt, *a, **k: "2026-10-24" if fmt == "%Y-%m-%d" else (
        "2026-10-24 09:25:00" if fmt == "%Y-%m-%d %H:%M:%S" else _real_strftime(fmt, *a, **k))
    _time.time = lambda: _time.mktime(_time.strptime("2026-10-24 09:25:00", "%Y-%m-%d %H:%M:%S"))
    # 插入 3 条 10-24「旧引擎产物」（无 engineVer、pending、腿未开球）
    hist_b = json.load(open(HIST, encoding="utf-8"))
    records_b = [r for r in hist_b.get("records", []) if r.get("date") != "2026-10-24"]
    old_1024 = [
        {"date": "2026-10-24", "type": "稳健2串1", "totalOdds": 2.22,
         "avgConfidence": 72.8, "recommended": False, "ev": -25.1, "result": "pending",
         "savedAt": "2026-10-24 09:06:52",
         "legs": [{"num": "周六001", "play": "胜平负", "playKey": "had", "dir": "h",
                   "label": "主胜", "odds": 1.48, "kickoff": "2026-10-24 13:00"},
                  {"num": "周六002", "play": "胜平负", "playKey": "had", "dir": "h",
                   "label": "主胜", "odds": 1.5, "kickoff": "2026-10-24 13:00"}]},
        {"date": "2026-10-24", "type": "均衡3串1", "totalOdds": 6.27,
         "avgConfidence": 66.4, "recommended": False, "ev": -34.2, "result": "pending",
         "savedAt": "2026-10-24 09:06:52",
         "legs": [{"num": "周六001", "play": "胜平负", "playKey": "had", "dir": "h",
                   "label": "主胜", "odds": 1.5, "kickoff": "2026-10-24 13:00"},
                  {"num": "周六003", "play": "胜平负", "playKey": "had", "dir": "a",
                   "label": "客胜", "odds": 1.9, "kickoff": "2026-10-24 14:00"}]},
        {"date": "2026-10-24", "type": "精锐4串1", "totalOdds": 6.03,
         "avgConfidence": 68.7, "recommended": False, "ev": -42.0, "result": "pending",
         "savedAt": "2026-10-24 09:06:52",
         "legs": [{"num": "周六004", "play": "胜平负", "playKey": "had", "dir": "h",
                   "label": "主胜", "odds": 1.43, "kickoff": "2026-10-24 14:00"}]},
    ]
    hist_b["records"] = records_b + old_1024
    json.dump(hist_b, open(HIST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"场景B就绪：{len(hist_b['records'])} 条（含 10-24 旧引擎产物 3 条）")

    # 1) 版本门解锁（today_matches 传 10-24 在售清单，防误删在售档）
    spb, _ = fd.load_locked_parlay(matches_b)
    assert spb is None, "场景B版本门未触发：旧引擎产物仍被锁定"
    after_b = json.load(open(HIST, encoding="utf-8"))["records"]
    n_1024 = len([r for r in after_b if r.get("date") == "2026-10-24"])
    assert n_1024 == 0, "场景B旧引擎产物未删除"
    print(f"load_locked_parlay -> ({spb is not None})；10-24 剩余记录 {n_1024} 条")

    # 2) 新引擎重生（新规则下 10-24 有稳健/均衡两档达标）
    safe_b = fd.gen_safe_parlay(matches_b)
    print(f"  重生档位：p2={bool(safe_b.get('parlay2'))} p3={bool(safe_b.get('parlay3'))} "
          f"p4={bool(safe_b.get('parlay4'))}")
    fd.save_parlay_history(safe_b, None)
    after_b2 = json.load(open(HIST, encoding="utf-8"))["records"]
    tb = [r for r in after_b2 if r.get("date") == "2026-10-24"]
    assert len(tb) >= 1, "场景B重生应为至少一档（新规则下 10-24 有达标组合）"
    assert all(r.get("engineVer") == cur_ver for r in tb), \
        f"场景B重生记录 engineVer 应为 {cur_ver}，实际 {[r.get('engineVer') for r in tb]}"
    print(f"重生后 10-24 记录：{[(r['type'], r['totalOdds'], r.get('engineVer')) for r in tb]}")
    # realEv 顶层字段（v1.7.43 埋点）：推荐标准可逐日审计
    assert all(r.get("realEv") is not None for r in tb), "场景B重生记录缺 realEv 顶层字段"
    print("  realEv 顶层字段齐全（v1.7.43 可回溯埋点）")

    # 3) 再锁定：新引擎产物正常回放
    sp2b, _ = fd.load_locked_parlay(matches_b)
    assert sp2b is not None and sp2b.get("locked"), "场景B新引擎产物未被锁定"
    print(f"再锁定 OK：稳健 {sp2b['parlay2']['totalOdds']} 倍 locked={sp2b['locked']}")
    print("\n结论：场景B完整路径生效——解锁→重生→带版本号+realEv→锁定回放")
finally:
    shutil.copy(BAK, HIST)
    os.remove(BAK)
    print("（历史文件已还原）")
