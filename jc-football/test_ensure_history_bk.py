#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ensure_history_bk.py —— 篮球两文件「时间戳优先」恢复判定单测（v1.7.38）

背景（2026-10-22 事故）：篮球抓取静默失败 44 小时，自举包里的旧快照被定时任务
每轮解包后当自有数据部署。按「条数」对账判不出这种冷冻——本地 21 条 vs 线上
21 条，条数相同，时间戳却差一天。改为时间戳优先后，旧快照必须在每轮步骤0
现形并被线上新副本覆盖。

判定规则（ensure_history.bk_should_restore）：
  1. 本地缺失/损坏，线上可解析        -> 恢复
  2. 线上 updatedAt 严格晚于本地      -> 恢复（本地是旧快照）
  3. 时间戳相同/缺失，线上条数更多     -> 恢复
  4. 本地更新更晚（抓取成功但部署被节流跳过）-> 保持本地
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ensure_history as eh


def _bk(upd, n):
    """构造 load_json 形态的三元组：(对象, 条数, updatedAt)"""
    obj = {"updatedAt": upd, "matches": [{"id": i} for i in range(n)]}
    return (obj, n, upd)


CASES = [
    # (说明, local, remote, 期望恢复?)
    ("本地缺失、线上有数据 -> 恢复（冷启动/沙箱重置）",
     (None, None, ""), _bk("2026-10-22 14:04:45", 7), True),
    ("本地损坏 -> 恢复",
     (None, None, ""), _bk("2026-10-22 14:04:45", 7), True),
    ("事故同型：条数相同(21=21)、线上晚 44 小时 -> 恢复（旧快照现形）",
     _bk("2026-10-20 17:51:00", 21), _bk("2026-10-22 14:04:45", 21), True),
    ("线上晚 1 秒也恢复",
     _bk("2026-10-22 14:04:44", 7), _bk("2026-10-22 14:04:45", 7), True),
    ("本地更新更晚（抓取成功、部署被节流跳过）-> 保持本地",
     _bk("2026-10-22 15:30:00", 7), _bk("2026-10-22 14:04:45", 7), False),
    ("同刻、线上条数更多 -> 恢复",
     _bk("2026-10-22 14:04:45", 14), _bk("2026-10-22 14:04:45", 21), True),
    ("同刻、条数也相同 -> 保持（都新鲜）",
     _bk("2026-10-22 14:04:45", 21), _bk("2026-10-22 14:04:45", 21), False),
    ("本地时间戳缺失、线上有条数更多 -> 恢复",
     _bk("", 3), _bk("2026-10-22 14:04:45", 7), True),
    ("本地时间戳缺失、线上条数不更多 -> 保持",
     _bk("", 7), _bk("2026-10-22 14:04:45", 7), False),
    ("线上不可用 -> 无从恢复，保持本地",
     _bk("2026-10-22 14:04:45", 7), (None, None, ""), False),
    ("两侧时间戳都缺失、线上条数更多 -> 恢复",
     _bk("", 2), _bk("", 5), True),
]


def main():
    fails = 0
    for desc, local, remote, expect in CASES:
        got = eh.bk_should_restore(local, remote)
        ok = got == expect
        fails += 0 if ok else 1
        print("  [%s] %s -> %s" % ("PASS" if ok else "FAIL", desc,
                                   "恢复" if got else "保持"))
    # 篮球两文件必须都在时间戳判定集里
    for fn in ("basketball.json", "basketball_history.json"):
        assert fn in eh.BK_TS_FILES, "%s 未纳入时间戳判定" % fn
        assert fn in eh.HIST_FILES, "%s 未纳入 HIST_FILES 保护清单" % fn
        print("  [PASS] %s 已在 BK_TS_FILES + HIST_FILES" % fn)
    print("=" * 60)
    print("FAILED %d / %d" % (fails, len(CASES) + 2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
