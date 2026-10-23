#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_whoscored_preconf.py —— 「赛前高可信」赛后回溯标记单测（v1.7.40）

需求（用户原话）：「足球比分预测，赛前有提示高可信的，赛后回溯也要标明白」——
赛前标注过「🔥高可信」的场次，赛后回溯区要能明确标记出来，让用户验证
高可信预测的实际表现。

实现链路（fetch_whoscored.py）：
  1. 可信度 conf 只对未开赛场次计算，本轮算完写入 pending 存档
     {mid: {date, league, home, away, jcNum, predH, predA, conf}}；
  2. 比赛评估重建历史记录时，_merge_preconf 把 pending 里的赛前 conf
     搬进记录的 preConf 字段（评估后永存，记录创建后不可变）；
  3. 赛后回溯（页面）读 preConf 渲染「🔥 赛前高可信」徽章与当日汇总。

顺带修复（同一处代码，根因相同）：conf 循环守卫只看 x["evaluated"]，而
records 从不持久化 evaluated 键 → 守卫恒真 → 线上 83 场已完赛比赛仍带
可信度建议（事后诸葛）。_should_score_conf 补上 records.actH 硬标志。

本测试覆盖两个纯函数 + 一轮「pending→preConf」往返仿真。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_whoscored as fw
from fetch_whoscored import _merge_preconf, _should_score_conf, _prune_pending


def _conf(exact_level="high", dir_level="mid", sample=30):
    return {"dir": 62.5, "exact": 12.3, "dirLevel": dir_level,
            "exactLevel": exact_level, "leagueN": 18, "teamN": 12,
            "sample": sample, "outcome": "主胜",
            "tip": "比分可信度高，可跟注比分 2:1"}


def _pend(mid="1001", conf=None):
    return {"date": "2026-10-22", "league": "英超", "home": "阿森纳", "away": "切尔西",
            "jcNum": "周三005", "predH": 2, "predA": 1,
            "conf": _conf() if conf is None else conf}


PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s\n    期望：%r\n    实际：%r" % (name, want, got))


def test_merge_preconf():
    print("· _merge_preconf：赛前快照并入赛后记录")
    conf = _conf()

    # 1. 正常路径：pending 有 conf + 新记录有赛果 -> 采用赛前快照
    check("pending 优先（旧记录 even 有 preConf 也被赛前快照覆盖）",
          _merge_preconf({"preConf": {"dirLevel": "low", "sample": 5}}, _pend(conf=conf), True),
          conf)

    # 2. pending 缺失 -> 保留旧记录的 preConf（防御：pending 被清理/丢失）
    old_pre = {"dirLevel": "low", "exactLevel": "low", "sample": 7}
    check("pending 缺失 → 保留旧记录 preConf",
          _merge_preconf({"preConf": old_pre}, None, True), old_pre)

    # 3. 两者皆无 -> None
    check("pending 缺失且旧记录无 preConf → None",
          _merge_preconf({}, None, True), None)
    check("old_rec 为 None 不炸",
          _merge_preconf(None, None, True), None)

    # 4. 脏记录防御：新记录没有赛果（actH 空）-> 不挂赛前快照
    check("新记录无赛果 → 不挂 pending 快照（回退旧值）",
          _merge_preconf({"preConf": old_pre}, _pend(conf=conf), False), old_pre)
    check("新记录无赛果且无旧值 → None",
          _merge_preconf({}, _pend(conf=conf), False), None)

    # 5. pending 条目存在但 conf 为空 -> 回退旧值
    check("pending.conf 为空 → 回退旧 preConf",
          _merge_preconf({"preConf": old_pre}, _pend(conf={}), True), old_pre)
    check("pending.conf 为空且无旧值 → None",
          _merge_preconf({}, _pend(conf={}), True), None)


def test_should_score_conf():
    print("· _should_score_conf：可信度守卫（事后诸葛回归）")
    # 未开赛 + 有预测 + records 无该 mid -> 正常打分
    check("未开赛新场次 → 打分",
          _should_score_conf({"id": "2001", "predH": 2, "predA": 1}, {}), True)
    # 本轮刚回填赛果（evaluated=True）-> 不打分
    check("本轮刚评估（evaluated=True）→ 不打分",
          _should_score_conf({"id": "2002", "predH": 2, "predA": 1, "evaluated": True}, {}), False)
    # 无预测 -> 不打分
    check("无预测比分 → 不打分",
          _should_score_conf({"id": "2003", "predH": None}, {}), False)
    # 核心回归：往轮已评估、本轮仍在列表里（evaluated 恒 False，但 records 有 actH）
    # ——2026-10-23 线上实测 83 场此类比赛仍带可信度建议（赛后诸葛）
    check("往轮已评估（evaluated=False 但 records.actH 有值）→ 不打分",
          _should_score_conf({"id": "2004", "predH": 2, "predA": 1, "evaluated": False},
                             {"2004": {"actH": 3, "actA": 1}}), False)
    # records 有 mid 但尚未回填（actH=None）-> 仍打分
    check("records 有 mid 但无赛果 → 打分",
          _should_score_conf({"id": "2005", "predH": 1, "predA": 1},
                             {"2005": {"predH": 1, "predA": 1}}), True)


def test_prune_pending():
    print("· _prune_pending：过期存档清理")
    today = "2026-10-23"
    # 10-22（1 天前）保留；10-13（10 天前）保留（>= 边界）；10-12（11 天前）清理
    p = {"a": {"date": "2026-10-22"}, "b": {"date": "2026-10-13"},
         "c": {"date": "2026-10-12"}, "d": {}}
    got = _prune_pending(p, today)
    check("10 天边界：保留近 10 天，清理更早", sorted(got.keys()), ["a", "b", "d"])
    check("缺 date 的脏条目保守保留", "d" in got, True)


def test_backfill_table():
    print("· _apply_preconf_backfill：上线前历史记录一次性回填")
    conf = _conf()
    table = {"_note": "test", "records": {
        "3001": {"predH": 2, "predA": 1, "conf": conf},          # 正常：补挂
        "3002": {"predH": 1, "predA": 1, "conf": _conf(dir_level="low")},  # 已有 preConf：不动
        "3003": {"predH": 3, "predA": 0, "conf": conf},          # 预测比分不一致：跳过
        "3004": {"predH": 0, "predA": 0, "conf": {"sample": 0}}, # 样本 0：跳过
        "3005": {"predH": 2, "predA": 2, "conf": None},          # 无 conf：跳过
        "3006": {"predH": 5, "predA": 0, "conf": conf},          # records 无此 mid：跳过
    }}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False)
        tf_path = f.name
    old_path = fw.BACKFILL_PATH
    fw.BACKFILL_PATH = tf_path
    try:
        records = {
            "3001": {"id": "3001", "predH": 2, "predA": 1, "actH": 2, "actA": 1},
            "3002": {"id": "3002", "predH": 1, "predA": 1, "actH": 0, "actA": 0,
                     "preConf": {"dirLevel": "mid", "sample": 9}},
            "3003": {"id": "3003", "predH": 1, "predA": 0, "actH": 3, "actA": 0},
            "3004": {"id": "3004", "predH": 0, "predA": 0, "actH": 1, "actA": 1},
            "3005": {"id": "3005", "predH": 2, "predA": 2, "actH": 2, "actA": 2},
        }
        n = fw._apply_preconf_backfill(records)
        check("只补挂 1 条（3001）", n, 1)
        check("3001 带上赛前快照", records["3001"].get("preConf"), conf)
        check("3002 旧 preConf 不被覆盖", records["3002"]["preConf"]["sample"], 9)
        check("3003 错配不挂", "preConf" not in records["3003"], True)
        check("3004 样本 0 不挂", "preConf" not in records["3004"], True)
        check("3005 无 conf 不挂", "preConf" not in records["3005"], True)
        # 幂等：再跑一次不再补挂
        check("幂等（重跑 0 条）", fw._apply_preconf_backfill(records), 0)
    finally:
        fw.BACKFILL_PATH = old_path
        os.unlink(tf_path)
    # 回填表缺失/损坏 -> 0，不炸
    fw.BACKFILL_PATH = "/tmp/__no_such_preconf_table__.json"
    try:
        check("回填表缺失 → 0", fw._apply_preconf_backfill({"3001": {"actH": 1}}), 0)
    finally:
        fw.BACKFILL_PATH = old_path


def test_roundtrip():
    print("· 往返仿真：pending →（比赛评估）→ 记录 preConf")
    # 第 1 轮：比赛未开赛，conf 循环写入 pending（复刻主流程写入结构）
    mid = "1989199"
    pending = {mid: _pend(mid=mid)}
    records = {}

    # 第 2 轮：比赛已完赛回填（livescores 直接给比分），进入记录重建
    x = {"id": mid, "date": "2026-10-22", "league": "英超", "home": "阿森纳", "away": "切尔西",
         "predH": 2, "predA": 1, "actH": 2, "actA": 1, "evaluated": True,
         "exactHit": True, "outcomeHit": True, "jcNum": "周三005"}
    old = records.get(mid) or {}
    rec = dict(x)
    pre = _merge_preconf(old, pending.get(mid), x.get("actH") is not None)
    if pre:
        rec["preConf"] = pre
    records[mid] = rec

    check("评估后记录带 preConf（赛前快照）",
          rec.get("preConf"), _conf())
    check("preConf 是快照副本（与 pending 条目同值但独立对象）",
          rec["preConf"] is pending[mid]["conf"], False)
    check("评估后该场不再打可信度（守卫）",
          _should_score_conf(x, records), False)

    # 回溯区读取面：history 输出即 records.values()，preConf 随记录一起出栈
    check("回溯数据源（records）可读到赛前标记",
          any(r.get("preConf", {}).get("exactLevel") == "high" for r in records.values()), True)


def main():
    print("=" * 64)
    print("v1.7.40 赛前高可信 → 赛后回溯标记：单测")
    print("=" * 64)
    test_merge_preconf()
    test_should_score_conf()
    test_prune_pending()
    test_backfill_table()
    test_roundtrip()
    print("-" * 64)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    if FAIL:
        sys.exit(1)
    print("✅ 全部通过：赛前可信度可存档、可并入记录、守卫已修")


if __name__ == "__main__":
    main()
