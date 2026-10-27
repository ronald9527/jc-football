#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比分预测本地兜底的回归测试（纯函数，不写盘、不碰网络）。

锁定 v1.7.45 的核心行为：
1. _poisson_grid 返回合法 schema；
2. _lambda_from_jc 主路用 hadProb+goalsExp，与盘口方向一致（客队大热→客队 λ 更高）；
3. _lambda_from_jc 退路用 scoreRec.expHome/expAway。
"""
import fetch_scores as fs


def test_poisson_grid_schema():
    r = fs._poisson_grid(1.8, 1.5)
    assert set(r.keys()) == {"predH", "predA", "pHome", "pDraw", "pAway"}, r
    assert 0 <= r["predH"] <= 8 and 0 <= r["predA"] <= 8
    # 概率和≈100
    assert abs(r["pHome"] + r["pDraw"] + r["pAway"] - 100) < 1.0, r
    print("1. _poisson_grid schema OK: %r" % r)


def test_lambda_market_consistent():
    # 圣克拉拉(主) vs 本菲卡(客)：hadProb h10/d18/a72，客队大热
    m = {"hadProb": {"h": 10, "d": 18, "a": 72}, "goalsExp": 2.92}
    lh, la = fs._lambda_from_jc(m)
    assert lh is not None and la is not None, (lh, la)
    # 客队（away）更强 → λ_a 明显高于 λ_h
    assert la > lh, "客队大热时应有 la>lh，实得 lh=%.2f la=%.2f" % (lh, la)
    # 总期望≈goalsExp
    assert abs((lh + la) - 2.92) < 0.4, (lh, la)
    print("2. _lambda_from_jc 盘口一致 OK: lh=%.2f la=%.2f（客热→la>lh）" % (lh, la))


def test_lambda_scorerec_fallback():
    # 无 hadProb/goalsExp → 退路 scoreRec
    m = {"scoreRec": {"expHome": 1.8, "expAway": 1.5}}
    lh, la = fs._lambda_from_jc(m)
    assert abs(lh - 1.8) < 1e-6 and abs(la - 1.5) < 1e-6, (lh, la)
    # 两者皆缺 → None
    assert fs._lambda_from_jc({}) == (None, None)
    print("3. _lambda_from_jc scoreRec 退路 OK: %r" % ((lh, la),))


if __name__ == "__main__":
    test_poisson_grid_schema()
    test_lambda_market_consistent()
    test_lambda_scorerec_fallback()
    print("\n✅ test_score_predict_fallback 全过")
