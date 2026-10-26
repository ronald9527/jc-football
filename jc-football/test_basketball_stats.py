#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球历史统计单元测试（v1.7.44）。

验证点：
  1. compute_bk_stats 返回 sample/leagues/hdc_overall/hilo_overall/wnm_dist/home_advantage/roi
  2. 样本口径：sample.scored 与真实有赛果条数一致，含样本薄 note
  3. 联赛胜率/盘路/均总分数值合理（0-100% / 正数）
  4. 胜分差分布六区间求和 == 主胜场数（逻辑自洽）
  5. ROI 含四玩法，字段完整（bet/profit/roi）
"""
import sys

sys.path.insert(0, "/workspace/jc-football")
import fetch_basketball_ai as bk

HIST = bk._load("/workspace/jc-football/basketball_history.json", {})
RECORDS = HIST.get("records", {})
ST = bk.compute_bk_stats(RECORDS)


def test_structure():
    for k in ("sample", "leagues", "hdc_overall", "hilo_overall", "wnm_dist", "home_advantage", "roi"):
        assert k in ST, "stats 缺字段 %s" % k
    print("1. stats 结构 OK：含 sample/leagues/hdc_overall/hilo_overall/wnm_dist/home_advantage/roi")


def test_sample_口径():
    scored_real = sum(1 for r in RECORDS.values()
                      if isinstance(r.get("score"), list) and len(r["score"]) == 2)
    assert ST["sample"]["scored"] == scored_real, "sample.scored 应=%d，实际 %d" % (scored_real, ST["sample"]["scored"])
    assert ST["sample"]["total"] == len(RECORDS)
    assert "样本" in ST["sample"]["note"]
    print("2. 样本口径 OK：total=%d/scored=%d，含样本薄提示"
          % (ST["sample"]["total"], ST["sample"]["scored"]))


def test_leagues_rates():
    lg = ST["leagues"]
    assert lg, "应有联赛统计"
    for name, d in lg.items():
        assert 0 <= d["home_win_rate"] <= 100, "主胜率越界 %s: %r" % (name, d["home_win_rate"])
        if d["avg_total"] is not None:
            assert d["avg_total"] > 0, "均总分应>0，%s: %r" % (name, d["avg_total"])
        if d["hdc_home_hit_rate"] is not None:
            assert 0 <= d["hdc_home_hit_rate"] <= 100
        if d["hilo_over_rate"] is not None:
            assert 0 <= d["hilo_over_rate"] <= 100
    print("3. 联赛盘路 OK：%d 个联赛，主胜率/让分主胜/大分率均在合理区间"
          % len(lg))


def test_wnm_dist_consistency():
    # 主胜场数 = 胜分差分布六区间之和（胜分差仅统计主胜场）
    home_wins = sum(1 for r in RECORDS.values()
                    if isinstance(r.get("score"), list) and len(r["score"]) == 2
                    and r["score"][0] > r["score"][1])
    wnm_sum = sum(ST["wnm_dist"].values())
    assert wnm_sum == home_wins, "胜分差分布和 %d ≠ 主胜场数 %d" % (wnm_sum, home_wins)
    assert set(ST["wnm_dist"].keys()) == {"l1", "l2", "l3", "l4", "l5", "l6"}
    print("4. 胜分差分布自洽 OK：六区间和 %d == 主胜 %d" % (wnm_sum, home_wins))


def test_roi_fields():
    roi = ST["roi"]
    assert roi, "应有 ROI 数据"
    for k, d in roi.items():
        assert "bet" in d and "profit" in d and "roi" in d, "ROI 缺字段 %s" % k
        assert d["bet"] >= 0
    print("5. ROI OK：玩法 %s，字段完整（bet/profit/roi）" % list(roi.keys()))


if __name__ == "__main__":
    test_structure()
    test_sample_口径()
    test_leagues_rates()
    test_wnm_dist_consistency()
    test_roi_fields()
    print("ALL PASS")
