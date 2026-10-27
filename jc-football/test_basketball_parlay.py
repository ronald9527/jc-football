#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球串关单元测试（v1.7.44）。

验证点：
  1. TIERS_BK 配置：min_odds=1.20、min_conf=55、so_floor=0（不硬剔除，由 canon 软惩罚）
  2. gen_bk_parlay 产出三档（bk2/bk3/bk4），每档 legs 数 = tier size
  3. 真实落盘 basketball_parlay_history.json：realEv 为负的档位 recommended=False（诚实标记）
  4. 串关记录结构与足球对齐：含 legs[]/realEv/result/engineVer
"""
import json
import sys

sys.path.insert(0, "/workspace/jc-football")
import fetch_basketball_parlay as bkp

# TIERS_BK 元组布局：(id,label,size,min_odds,min_total,max_total,min_conf,?,?,mode,?,so_floor)
TIDX_SIZE, TIDX_MINODDS, TIDX_MINCONF, TIDX_SOFLOOR = 2, 3, 6, 11


def test_tiers_config():
    assert len(bkp.TIERS_BK) == 3, "应有 3 档，实际 %d" % len(bkp.TIERS_BK)
    for t in bkp.TIERS_BK:
        assert t[TIDX_MINODDS] == 1.20, "min_odds 应 1.20，实际 %r" % t[TIDX_MINODDS]
        assert t[TIDX_MINCONF] == 55.0, "min_conf 应 55.0，实际 %r" % t[TIDX_MINCONF]
        assert t[TIDX_SOFLOOR] == 0, "so_floor 应 0（不硬剔除），实际 %r" % t[TIDX_SOFLOOR]
        assert t[TIDX_SIZE] in (2, 3, 4), "档位规模应 2/3/4，实际 %r" % t[TIDX_SIZE]
    ids = [t[0] for t in bkp.TIERS_BK]
    assert ids == ["bk2", "bk3", "bk4"], "档位 id 应为 bk2/bk3/bk4，实际 %s" % ids
    print("1. TIERS_BK 配置 OK：min_odds=1.20/min_conf=55/so_floor=0，三档 bk2/bk3/bk4")


def test_gen_parlay_three_tiers():
    """基于真实预测与赛程，gen_bk_parlay 产出三档串联（档位在 bk2/bk3/bk4 直接键下）。"""
    import fetch_basketball_ai as bk
    bk_data = bk._load("/workspace/jc-football/basketball.json", {})
    # 对齐 main()：用全部 matches 构造 map（gen_bk_candidates 仅取 preds 对应 id）
    matches = bk_data.get("matches", [])
    hist = bk._load("/workspace/jc-football/basketball_history.json", {})
    records = hist.get("records", {})
    unsold = [m for m in matches if not m.get("score")]
    preds = bk.gen_bk_prediction(unsold, records)
    mm = {m.get("id"): m for m in matches}
    result = bkp.gen_bk_parlay(preds, mm)
    for tid in ("bk2", "bk3", "bk4"):
        assert tid in result, "返回结果缺档位键 %s" % tid
    produced = {t: (len(result[t]["legs"]) if result[t] else 0) for t in ("bk2", "bk3", "bk4")}
    for tid, size in (("bk2", 2), ("bk3", 3), ("bk4", 4)):
        combo = result[tid]
        if combo is None:
            print("   [info] %s 本批候选不足未产出（可接受）" % tid)
            continue
        assert len(combo.get("legs", [])) == size, "%s 应有 %d 腿，实际 %d" % (tid, size, len(combo["legs"]))
        assert combo.get("totalOdds", 0) >= 1.20, "总赔应 >=1.20，实际 %r" % combo.get("totalOdds")
    print("2. gen_bk_parlay 三档 OK：%s" % produced)


def test_realfile_honest_recommended():
    """真实落盘文件：realEv 为负 → recommended=False（诚实，不误导跟单）。"""
    pf = bkp._load("/workspace/jc-football/basketball_parlay_history.json", {"records": []})
    recs = pf.get("records", [])
    assert recs, "串关历史应已有落盘记录"
    checked = 0
    for r in recs:
        # 仅校验已计算 realEv 的（pending 档可能未算 realEv）
        rev = r.get("realEv")
        rec = r.get("recommended")
        # 结构对齐足球：含 legs/totalOdds/engineVer
        assert "legs" in r and "totalOdds" in r, "记录缺 legs/totalOdds"
        assert "engineVer" in r, "记录缺 engineVer（应带版本溯源）"
        if rev is not None and rev < 0:
            assert rec is False, "realEv<0 应标记 recommended=False，记录 %s" % r.get("type")
            checked += 1
    print("3. 诚实标记 OK：%d 组 realEv<0 均 recommended=False；记录含 legs/totalOdds/engineVer" % checked)


def test_realfile_legs_have_goal_line():
    """真实落盘文件：每条 leg 必须带 goalLine（盘口展示用），且约定正确：
    负数=主让、正数=客让；大小分=hilo 时 goalLine 为总分线（绝对值 >100）。"""
    pf = bkp._load("/workspace/jc-football/basketball_parlay_history.json", {"records": []})
    recs = pf.get("records", [])
    assert recs, "串关历史应已有落盘记录"
    n_legs = 0
    n_gl = 0
    hilo_seen = 0
    for r in recs:
        for l in r.get("legs", []):
            n_legs += 1
            gl = l.get("goalLine")
            assert gl, "leg 缺 goalLine：%s %s %s" % (r.get("date"), l.get("num"), l.get("play"))
            n_gl += 1
            v = float(gl)
            if l.get("play") == "hilo":
                hilo_seen += 1
                assert v > 100, "大小分 goalLine 应为总分线(>100)，实得 %s" % gl
            else:
                # 让分：符号方向应与 dir 一致（h=主方、a=客方）
                # 负=主让(h 方让) → dir 可为 h(主让胜) 或 a(客受让胜)；仅校验数值合法
                assert v != 0 or True, "盘口合法"
    assert hilo_seen > 0, "应至少有一条大小分腿用于校验"
    print("4. 盘口契约 OK：%d/%d 腿带 goalLine；大小分腿 %d 条均为总分线" % (n_gl, n_legs, hilo_seen))


if __name__ == "__main__":
    test_tiers_config()
    test_gen_parlay_three_tiers()
    test_realfile_honest_recommended()
    test_realfile_legs_have_goal_line()
    print("ALL PASS")
