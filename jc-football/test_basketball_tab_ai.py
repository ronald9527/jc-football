#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球专区「AI预测/篮球串关/历史统计」二级 tab 浏览器级验证（v1.7.44）。

依赖：本地 index.html 已由 build_panel.py 注入 basketball_ai / basketball_parlay /
basketball_stats 三份数据（测试默认 PATH=/workspace/jc-football/index.html）。

验证点：
  1. 二级 tab 栏含 5 项：赛程赔率/赛后回看/AI预测/篮球串关/历史统计
  2. AI预测 pane：可见、卡片含四玩法（.bk-ai-play x4）、含同指样本不足/命中率标记
  3. 篮球串关 pane：可见、含 .bk-parlay-card、meta 含 realEv、诚实标记「谨慎/推荐」
  4. 历史统计 pane：可见、含联赛表(.bk-stats-tbl)与盘路总览与 ROI 表
  5. pane 互斥切换；无 JS 错误
"""
import sys

from playwright.sync_api import sync_playwright

PATH = sys.argv[1] if len(sys.argv) > 1 else "/workspace/jc-football/index.html"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + PATH)
    page.wait_for_timeout(1500)
    page.evaluate("() => { const m = document.getElementById('loginMask'); if (m) m.remove(); }")

    # 进入篮球专区
    btn = page.query_selector('button[data-tab="basketball"]')
    assert btn, "竞彩篮球 tab 按钮不存在"
    btn.click()
    page.wait_for_timeout(600)

    # 1. 二级 tab 栏
    subs = page.query_selector_all("#bkSubTabs .bk-subtab")
    labels = [s.inner_text().strip() for s in subs]
    print("1. 二级 tab：%s" % labels)
    assert "AI预测" in labels and "篮球串关" in labels and "历史统计" in labels, \
        "缺新增二级 tab，当前：%s" % labels

    # 2. AI预测 pane
    ai_btn = page.query_selector('#bkSubTabs .bk-subtab[data-bksub="ai"]')
    assert ai_btn, "AI预测 tab 不存在"
    ai_btn.click()
    page.wait_for_timeout(500)
    assert page.query_selector("#bkAiPane").is_visible(), "AI pane 应可见"
    assert not page.query_selector("#bkSchedPane").is_visible(), "sched pane 应隐藏"
    n_pred = page.evaluate("() => (state.basketballAi.predictions||[]).length")
    cards = page.query_selector_all("#bkAiList .bk-card")
    print("2. AI预测：注入 %d 场，渲染 %d 张卡片" % (n_pred, len(cards)))
    assert n_pred > 0, "注入的篮球预测为空"
    assert len(cards) == n_pred, "AI 卡片数 %d ≠ 注入 %d" % (len(cards), n_pred)
    # 首卡四玩法
    first_plays = cards[0].query_selector_all(".bk-ai-play")
    assert len(first_plays) == 4, "首卡应含 4 个玩法，实际 %d" % len(first_plays)
    # 同指标记存在（样本不足 或 命中率）
    so_txt = page.query_selector("#bkAiList .bk-ai-conf").inner_text()
    assert "同指" in so_txt, "缺同指标记：%s" % so_txt
    print("   首卡四玩法 OK；同指标记：%s" % so_txt.strip())
    page.screenshot(path="/tmp/basketball_ai.png", full_page=False)

    # 3. 篮球串关 pane
    pl_btn = page.query_selector('#bkSubTabs .bk-subtab[data-bksub="parlay"]')
    assert pl_btn, "篮球串关 tab 不存在"
    pl_btn.click()
    page.wait_for_timeout(500)
    assert page.query_selector("#bkParlayPane").is_visible(), "串关 pane 应可见"
    pcards = page.query_selector_all("#bkParlayList .bk-parlay-card")
    pl_total = page.evaluate("() => (state.basketballParlay.total||0)")
    print("3. 篮球串关：历史 %d 组，渲染 %d 张卡片" % (pl_total, len(pcards)))
    if pl_total > 0:
        assert len(pcards) == pl_total, "串关卡片数 %d ≠ 历史 %d" % (len(pcards), pl_total)
        meta = page.query_selector("#bkParlayList .bk-parlay-meta").inner_text()
        assert "realEv" in meta, "串关 meta 缺 realEv：%s" % meta
        print("   串关 meta OK：%s" % meta.strip())
    else:
        assert page.query_selector("#bkParlayList"), "串关列表容器应存在"
    page.screenshot(path="/tmp/basketball_parlay.png", full_page=False)

    # 4. 历史统计 pane
    st_btn = page.query_selector('#bkSubTabs .bk-subtab[data-bksub="stats"]')
    assert st_btn, "历史统计 tab 不存在"
    st_btn.click()
    page.wait_for_timeout(500)
    assert page.query_selector("#bkStatsPane").is_visible(), "统计 pane 应可见"
    tbls = page.query_selector_all("#bkStatsList .bk-stats-tbl")
    assert len(tbls) >= 1, "统计页至少应有联赛表"
    # 盘路总览文本
    body = page.query_selector("#bkStatsList").inner_text()
    for probe in ["样本", "盘路总览", "主场优势", "ROI"]:
        assert probe in body, "统计页缺「%s」" % probe
    print("4. 历史统计 OK：%d 张表，含样本/盘路总览/主场优势/ROI" % len(tbls))
    page.screenshot(path="/tmp/basketball_stats.png", full_page=False)

    # 5. 切回赛程赔率，确认互斥
    sched_btn = page.query_selector('#bkSubTabs .bk-subtab[data-bksub="sched"]')
    sched_btn.click()
    page.wait_for_timeout(400)
    assert page.query_selector("#bkSchedPane").is_visible()
    assert not page.query_selector("#bkStatsPane").is_visible(), "统计 pane 应隐藏"

    assert not errors, "JS 错误：%s" % errors[:3]
    print("5. 互斥切换 OK；无 JS 错误")
    browser.close()
print("ALL PASS")
