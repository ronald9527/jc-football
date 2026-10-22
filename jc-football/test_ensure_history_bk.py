#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ensure_history_bk.py —— 「时间戳优先」恢复判定单测（v1.7.38 / v1.7.39）

背景一（2026-10-22 篮球事故）：篮球抓取静默失败 44 小时，自举包里的旧快照被定时任务
每轮解包后当自有数据部署。按「条数」对账判不出这种冷冻——本地 21 条 vs 线上
21 条，条数相同，时间戳却差一天。改为时间戳优先后，旧快照必须在每轮步骤0
现形并被线上新副本覆盖。

背景二（2026-10-21 比分预测事故）：任务沙箱丢失 fd_config.json，无密钥路径把
score_predict.json 天天抹成空结构。时间戳判定 + Pages 副本让最后一份好数据
在每轮步骤0 被拉回任务沙箱续命。

判定规则（ensure_history.ts_should_restore，滚动型文件）：
  1. 本地缺失/损坏，线上可解析        -> 恢复
  2. 线上 updatedAt 严格晚于本地      -> 恢复（本地是旧快照/被抹空的老数据）
  3. 时间戳相同/缺失，线上条数更多     -> 恢复
  4. 本地更新更晚（抓取成功但部署被节流跳过）-> 保持本地

判定规则（ensure_history.count_should_restore，累积型文件）：
  1. 本地缺失/损坏，线上可解析        -> 恢复
  2. 线上条数更多                     -> 恢复
  3. 条数相同，线上时间戳严格更晚      -> 恢复（v1.7.39 混合规则）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ensure_history as eh


def _bk(upd, n):
    """构造 load_json 形态的三元组：(对象, 条数, updatedAt)"""
    obj = {"updatedAt": upd, "matches": [{"id": i} for i in range(n)]}
    return (obj, n, upd)


def _hist(upd, n):
    """构造累积型文件（history.json 形态）的三元组"""
    obj = {"updatedAt": upd, "total": n, "records": [{"id": i} for i in range(n)]}
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
    ("比分预测事故型：本地被无密钥路径抹空（0 场, 时间戳=抹空时间 10-22 09:01），"
     "线上是今天稍晚从 Pages 恢复的好数据（74 场, 10-22 17:55）-> 线上更晚，恢复",
     _bk("2026-10-22 09:01:38", 0), _bk("2026-10-22 17:55:00", 74), True),
    ("已知边界：本地空但时间戳更新（旧版抹空行为留下）、线上好数据更早 -> 纯时间戳保持本地。"
     "故恢复必须发生在抹空之前（run_daily 步骤0 保证），且 v1.7.39 起不再抹空",
     _bk("2026-10-22 09:01:38", 0), _bk("2026-10-17 15:01:53", 74), False),
]

# count_should_restore 用例（累积型文件的混合规则）
COUNT_CASES = [
    # (说明, local, remote, 期望恢复?)
    ("本地缺失、线上有数据 -> 恢复",
     (None, None, ""), _hist("2026-10-22 17:33:33", 597), True),
    ("经典丢失：线上条数更多 -> 恢复",
     _hist("2026-10-22 17:33:33", 590), _hist("2026-10-22 17:33:33", 597), True),
    ("混合规则：条数相同(597=597)、线上晚 3.5 小时 -> 恢复"
     "（2026-10-22 实测：任务侧刚回填赛果，本地旧快照同条数）",
     _hist("2026-10-22 14:03:00", 597), _hist("2026-10-22 17:33:33", 597), True),
    ("条数相同、时间戳也相同 -> 保持（同一份数据）",
     _hist("2026-10-22 17:33:33", 597), _hist("2026-10-22 17:33:33", 597), False),
    ("条数相同、本地时间戳更晚 -> 保持（本地是回填后的新数据）",
     _hist("2026-10-22 18:00:00", 597), _hist("2026-10-22 17:33:33", 597), False),
    ("本地条数更多 -> 保持（沙箱累积超过线上副本）",
     _hist("2026-10-22 17:33:33", 600), _hist("2026-10-22 17:33:33", 597), False),
    ("线上不可用 -> 无从恢复",
     _hist("2026-10-22 17:33:33", 597), (None, None, ""), False),
]


def main():
    fails = 0
    total = 0
    for desc, local, remote, expect in CASES:
        got = eh.ts_should_restore(local, remote)
        ok = got == expect
        fails += 0 if ok else 1
        total += 1
        print("  [%s] %s -> %s" % ("PASS" if ok else "FAIL", desc,
                                   "恢复" if got else "保持"))
    for desc, local, remote, expect in COUNT_CASES:
        got = eh.count_should_restore(local, remote)
        ok = got == expect
        fails += 0 if ok else 1
        total += 1
        print("  [%s] %s -> %s" % ("PASS" if ok else "FAIL", desc,
                                   "恢复" if got else "保持"))
    # 时间戳判定集 + 保护清单必须覆盖三类文件
    for fn in ("basketball.json", "basketball_history.json", "score_predict.json",
               "matches.json", "hkmo_cache.json", "odds_bsd.json", "fd_data.json",
               "whoscored_data.json", "expert_recommendations.json"):
        assert fn in eh.TS_FILES, "%s 未纳入时间戳判定" % fn
        assert fn in eh.HIST_FILES, "%s 未纳入 HIST_FILES 保护清单" % fn
        total += 2
        print("  [PASS] %s 已在 TS_FILES + HIST_FILES" % fn)
    assert "score_predict_cache.json" in eh.HIST_FILES, "球队缓存未纳入保护清单"
    print("  [PASS] score_predict_cache.json 已在 HIST_FILES")
    total += 1
    # 专用计数器：matches 长度，不是顶层键数
    obj = {"updatedAt": "2026-10-22 14:04:45", "matches": [1, 2, 3],
           "history": [1] * 500, "dateIndex": {}, "dateList": []}
    assert eh._count_score_predict(obj) == 3, "score_predict 计数应取 matches 长度"
    cache = {"1": {"matches": [1]}, "2": {"matches": []}, "3": {}}
    assert eh._count_team_cache(cache) == 1, "球队缓存应只数有记录的队"
    ws = {"updatedAt": "2026-10-22 09:33:49", "matches": [1, 2],
          "history": [1] * 242, "reliable": {}}
    assert eh._count_whoscored_data(ws) == 2, "whoscored_data 计数应取 matches 长度"
    ex = {"updatedAt": "2026-10-22 17:33:48", "articles": [1, 2, 3],
          "recommendations": [1], "expertStats": {"a": 1}}
    assert eh._count_expert_recommendations(ex) == 3, \
        "expert_recommendations 计数应取 articles 长度"
    print("  [PASS] 专用计数器：score_predict=3 场 / 缓存=1 队 / "
          "whoscored=2 场 / expert=3 篇")
    total += 4
    print("=" * 60)
    print("FAILED %d / %d" % (fails, total))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
