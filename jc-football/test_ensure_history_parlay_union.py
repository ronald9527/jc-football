#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ensure_history_parlay_union.py —— parlay_history.json 签名并集对账单测（v1.7.41）

背景（2026-10-24 事故，用户报「昨天的串关推荐历史记录又跑哪里去了」）：
  本地 10-23 09:07 生成 10-23 的 3 组串关推荐，未能在线上条数追平前完成
  合并部署；随后 ensure_history 对账两边均为 69 条，按「条数相同、线上
  时间戳更晚」用线上版本整体覆盖本地，本地独有的 10-23 组被当旧数据吞掉。
  线上 Pages 的 parlayHistory 从此缺 10-23（只到 10-22 + 10-24）。

修复：parlay_union 按 (date, legs签名) 取并集——
  1. 签名只在一方出现 -> 无条件保留（任一端独有记录都不丢）
  2. 同签名两边都有 -> 取 savedAt 更晚的版本（赛后结算更新的胜出）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ensure_history as eh


def _rec(date, saved_at, legs):
    return {"date": date, "savedAt": saved_at, "type": "稳健2串1",
            "legs": [{"num": n, "playKey": "spf", "dir": d} for n, d in legs]}


def _wrap(records, upd="2026-10-24 12:00:00"):
    return {"updatedAt": upd, "total": len(records), "records": records}


# 事故复刻：本地独有 10-23 组，线上独有 10-24 组，两边同为 69 条的等价场景
LOCAL = _wrap([
    _rec("2026-10-22", "2026-10-22 19:32:09", [("周五012", "h"), ("周五011", "h")]),
    _rec("2026-10-23", "2026-10-23 09:07:14", [("周五012", "h"), ("周五011", "h")]),
    _rec("2026-10-23", "2026-10-23 09:07:14",
         [("周五012", "h"), ("周五011", "h"), ("周五002", "a")]),
], upd="2026-10-23 17:05:11")
ONLINE = _wrap([
    _rec("2026-10-22", "2026-10-22 19:32:09", [("周五012", "h"), ("周五011", "h")]),
    _rec("2026-10-24", "2026-10-24 09:13:55", [("周六020", "h"), ("周六012", "a")]),
    _rec("2026-10-24", "2026-10-24 09:13:55",
         [("周六020", "h"), ("周六012", "a"), ("周六025", "h")]),
], upd="2026-10-24 12:02:55")


def main():
    fails = 0
    total = 0

    def check(desc, got, expect):
        nonlocal fails, total
        total += 1
        ok = got == expect
        if not ok:
            fails += 1
        print("  [%s] %s -> %r" % ("PASS" if ok else "FAIL", desc, got))

    # ---- 签名函数 ----
    check("_parlay_rec_sig: 同组腿不同顺序=同签名",
          eh._parlay_rec_sig(_rec("2026-10-23", "x", [("a", "h"), ("b", "d")])),
          eh._parlay_rec_sig(_rec("2026-10-23", "x", [("b", "d"), ("a", "h")])))
    check("_parlay_rec_sig: 不同日期=不同签名",
          eh._parlay_rec_sig(_rec("2026-10-23", "x", [("a", "h")])) !=
          eh._parlay_rec_sig(_rec("2026-10-24", "x", [("a", "h")])), True)
    check("_parlay_leg_sig: 空 legs 不炸",
          eh._parlay_leg_sig(None), ())
    total += 3
    print("  [PASS] 签名函数 3 例")

    # ---- 并集核心规则 ----
    merged, n_added = eh.parlay_union(LOCAL, ONLINE)
    check("事故复刻：并集保留双端独有记录（共享1+本地独有2+线上独有2=5）", len(merged), 5)
    check("本地独有 10-23 组不被吞（回归本事故）",
          sum(1 for r in merged if r.get("date") == "2026-10-23"), 2)
    check("线上独有 10-24 组被补入本地", n_added, 2)
    check("并集按 date 排序",
          [r["date"] for r in merged],
          ["2026-10-22", "2026-10-23", "2026-10-23", "2026-10-24", "2026-10-24"])

    # 同签名取 savedAt 更晚
    l2 = _wrap([_rec("2026-10-20", "2026-10-20 09:00:00", [("a", "h")], )])
    o2 = _wrap([_rec("2026-10-20", "2026-10-20 21:00:00", [("a", "h")])])
    merged2, n_add2 = eh.parlay_union(l2, o2)
    check("同签名两边都有：取 savedAt 更晚（21:00 胜）",
          merged2[0]["savedAt"], "2026-10-20 21:00:00")
    check("同签名不重复计入 n_added", n_add2, 0)

    # 本地缺线上独有的组（任务侧沙箱丢数据场景；10-22 共享记录腿数须一致）
    l3 = _wrap([_rec("2026-10-22", "2026-10-22 19:32:09",
                     [("周五012", "h"), ("周五011", "h")])])
    merged3, n_add3 = eh.parlay_union(l3, ONLINE)
    check("本地缺 2 条线上独有 -> n_added=2", n_add3, 2)
    check("本地缺线上独有 -> 并集=3", len(merged3), 3)

    # 本地比线上全（本地生成后未部署，线上还没追上）
    l4 = _wrap(LOCAL["records"] + [
        _rec("2026-10-25", "2026-10-25 09:00:00", [("周日001", "h")])])
    merged4, n_add4 = eh.parlay_union(l4, ONLINE)
    check("本地更全 -> 并集=本地4+线上独有2=6（部署时推上线）", len(merged4), 6)
    check("本地更全 -> n_added=2（线上独有仍要补）", n_add4, 2)

    # 边界
    check("线上不可用 -> (None, 0)", eh.parlay_union(LOCAL, None), (None, 0))
    m5, n5 = eh.parlay_union(None, ONLINE)
    check("本地缺失 -> 整体恢复线上（3 条）", (len(m5), n5), (3, 3))
    m6, n6 = eh.parlay_union(LOCAL, _wrap([]))
    check("线上空 -> 保留本地全部", len(m6), 3)
    m7, n7 = eh.parlay_union(_wrap([]), ONLINE)
    check("本地空 -> 补入线上全部", (len(m7), n7), (3, 3))

    # 旧判定语法的盲区回归：count_should_restore 在同条数时会用线上覆盖本地
    l_tri = (LOCAL, 3, "2026-10-23 17:05:11")
    r_tri = (ONLINE, 3, "2026-10-24 12:02:55")
    check("旧 count_should_restore 在同条数下确实会覆盖本地（证明事故成因）",
          eh.count_should_restore(l_tri, r_tri), True)
    merged8, _ = eh.parlay_union(LOCAL, ONLINE)
    check("新 parlay_union 在同条数下保留本地独有记录",
          sum(1 for r in merged8 if r.get("date") == "2026-10-23"), 2)

    # 集成：HIST_FILES 覆盖
    assert "parlay_history.json" in eh.HIST_FILES, "parlay_history.json 必须在保护清单"
    print("  [PASS] parlay_history.json 在 HIST_FILES 保护清单")
    total += 1

    print("=" * 60)
    print("FAILED %d / %d" % (fails, total))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
