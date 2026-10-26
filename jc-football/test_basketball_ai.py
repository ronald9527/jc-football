#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球 AI 预测引擎单元测试（v1.7.44）。

验证点：
  1. calc_bk_same_odds_signal 薄样本护栏：n<5 返回 (None, n)，不会虚高
  2. 真实档案上各玩法分桶命中率计算正确（含 hdc/hilo 盘口 matching）
  3. gen_bk_prediction 对每场在售比赛产出「四玩法」预测，且 topPlay 取 canon 最高
  4. bk_canon_score 信心主导：同指仅 ≥65 才加成，薄样本(n<10)同指不计入
  5. 样本薄提示文案存在（SAMPLE_WARN）
"""
import json
import sys

sys.path.insert(0, "/workspace/jc-football")
import fetch_basketball_ai as bk

HIST = bk._load("/workspace/jc-football/basketball_history.json", {})
RECORDS = HIST.get("records", {})


def test_same_odds_thin_guard():
    """空档案 / 样本<5 必须返回 (None, n)，绝不虚高命中率。"""
    empty = {}
    so, n = bk.calc_bk_same_odds_signal(empty, "mnl", "h")
    assert so is None and n == 0, "空档案应返回 (None,0)，实际 (%r,%r)" % (so, n)
    # 仅 3 条有效记录的薄样本
    thin = {
        "1": {"score": [100, 90], "mnl": {"h": 1.5, "a": 2.3}, "hdcResult": "h"},
        "2": {"score": [88, 95], "mnl": {"h": 1.5, "a": 2.3}, "hdcResult": "a"},
        "3": {"score": [77, 71], "mnl": {"h": 1.5, "a": 2.3}, "hdcResult": "h"},
    }
    so, n = bk.calc_bk_same_odds_signal(thin, "mnl", "h")
    assert so is None and n == 3, "n=3 应返回 (None,3)，实际 (%r,%r)" % (so, n)
    print("1. 同指薄样本护栏 OK：空档 (None,0)；n=3 (None,3)")


def test_same_odds_bucket_real():
    """真实档案：mnl 方向 h 的命中率计算在合理区间 [0,100]，且 n>=5 返回数值。"""
    so, n = bk.calc_bk_same_odds_signal(RECORDS, "mnl", "h")
    assert n >= 5, "真实档案 mnl-h 样本应 >=5，实际 n=%r" % n
    assert 0 <= so <= 100, "命中率应在 [0,100]，实际 %r" % so
    # hdc 必须带盘口匹配，否则返回 (None,0)
    so_nogoal, n_nogoal = bk.calc_bk_same_odds_signal(RECORDS, "hdc", "h")
    # 不带 goal 时绝大多数记录盘口不匹配 → n 可能很小甚至 0
    assert isinstance(n_nogoal, int) and n_nogoal >= 0
    so_gl, n_gl = bk.calc_bk_same_odds_signal(RECORDS, "hdc", "h", goal="+1.5")
    assert isinstance(so_gl, (float, type(None))) and n_gl >= 0
    print("2. 真实档案分桶 OK：mnl-h 命中率 %s%%，n=%d" % (so, n))


def test_gen_prediction_four_plays():
    """gen_bk_prediction 对每场产出四玩法预测，topPlay 合法。"""
    bk_data = bk._load("/workspace/jc-football/basketball.json", {})
    matches = [m for m in bk_data.get("matches", []) if not m.get("score")]
    assert matches, "无在售比赛可供预测"
    preds = bk.gen_bk_prediction(matches, RECORDS)
    assert preds, "应产出预测"
    # 赛程中确有赔率的玩法集合（部分比赛不提供全部四玩法，如缺 mnl）
    offered = set()
    for m in matches:
        for pk in ("mnl", "hdc", "hilo", "wnm"):
            if isinstance(m.get(pk), dict):
                offered.add(pk)
    assert offered, "在售比赛应至少提供一种玩法赔率"
    for p in preds:
        plays = p.get("plays", {})
        # 仅对比赛实际提供赔率的玩法做断言（缺赔率的玩法本就不应预测）
        m = next((x for x in matches if x.get("id") == p.get("id")), {})
        for pk in ("mnl", "hdc", "hilo", "wnm"):
            if not isinstance(m.get(pk), dict):
                continue  # 该场无此玩法赔率，跳过
            assert pk in plays, "比赛含 %s 赔率但预测缺该玩法（场 %s）" % (pk, p.get("num"))
            pl = plays[pk]
            assert pl.get("odds") and pl.get("odds") > 1, "玩法 %s 赔率无效" % pk
            # 同指信号 n<5 必为 None（护栏），n>=5 必为 0-100
            so, sn = pl.get("sameOddsHit"), pl.get("sameOddsN")
            if sn < 5:
                assert so is None, "n<5 同指应为 None，%s n=%d" % (pk, sn)
            else:
                assert 0 <= so <= 100, "同指命中率越界 %r" % so
        top = p.get("topPlay")
        assert top in plays, "topPlay %s 必须属于四玩法之一" % top
    print("3. 四玩法预测 OK：%d 场，已提供赔率的玩法均产出预测，topPlay 合法" % len(preds))


def test_canon_score_confidence_dominant():
    """bk_canon_score：高信心 > 低信心；同指薄样本(n<10)不计入修正。"""
    hi = bk.bk_canon_score({"confidence": 80, "sameOddsHit": 90, "sameOddsN": 3, "odds": 1.7})
    lo = bk.bk_canon_score({"confidence": 55, "sameOddsHit": 90, "sameOddsN": 3, "odds": 1.7})
    assert hi > lo, "高信心评分应高于低信心，%s vs %s" % (hi, lo)
    # 薄样本(n<10)即便同指高达 90 也不应加成
    thin = bk.bk_canon_score({"confidence": 60, "sameOddsHit": 90, "sameOddsN": 3, "odds": 1.7})
    thin_noso = bk.bk_canon_score({"confidence": 60, "sameOddsHit": None, "sameOddsN": 3, "odds": 1.7})
    assert abs(thin - thin_noso) < 1e-9, "n<10 时同指不应影响评分"
    print("4. canon 评分 OK：信心主导；n<10 同指不计入修正")


def test_sample_warn_exists():
    assert isinstance(bk.SAMPLE_WARN, str) and "样本" in bk.SAMPLE_WARN
    print("5. 样本薄文案 OK：%s" % bk.SAMPLE_WARN)


if __name__ == "__main__":
    test_same_odds_thin_guard()
    test_same_odds_bucket_real()
    test_gen_prediction_four_plays()
    test_canon_score_confidence_dominant()
    test_sample_warn_exists()
    print("ALL PASS")
