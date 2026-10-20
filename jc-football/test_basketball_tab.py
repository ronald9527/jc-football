#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球专区浏览器级验证（v1.7.31 整页化版）。

验证点：
  1. 整页化：进入「竞彩篮球」后足球 tab 栏(.maintab)与统计条(#statBar)整体隐藏
  2. 专区呈现：返回按钮 + 头部 + 二级 tab 栏 + 赛程赔率卡片
  3. 赛后回看：空态提示 → 注入两条真实赛果 → 倒序/比分/让分与大小分判定/日期分组/日期筛选
  4. 返回足球：点「← 返回足球」恢复足球 tab 栏与统计条，回到进来前的足球 tab
  5. 无 JS 错误
"""
import sys

from playwright.sync_api import sync_playwright

PATH = sys.argv[1] if len(sys.argv) > 1 else "/workspace/jc-football/index.html"

# 浏览器内注入的两条 mock 赛果（用生产档案里的真实比赛，判定逻辑与 fetch_basketball.py 一致：
#   hdcResult: adj = home + goalLine(代数值)，adj>away → h 主胜，adj<away → a 客胜
#   hiloResult: home+away > 总分线 → h 大分，否则 l 小分）
MOCK = '''
() => {
  const recs = state.basketballHist.records;
  // 周二301 活塞vs凯尔特人：hdc +1.5 / hilo 223.5；108:112 → 让分客胜 + 小分
  recs["2042132"].score = [108, 112];
  recs["2042132"].hdcResult = "a";
  recs["2042132"].hiloResult = "l";
  // 周二302 尼克斯vs76人：hdc -4.5 / hilo 233.5；125:112 → 让分主胜 + 大分
  recs["2042133"].score = [125, 112];
  recs["2042133"].hdcResult = "h";
  recs["2042133"].hiloResult = "h";
  renderBasketball();
}
'''

with sync_playwright() as p:
    browser = p.chromium.launch()
    # 手机视口（对应用户实际使用场景：整页化主要解决手机上被足球 tab 栏遮挡的问题）
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + PATH)
    page.wait_for_timeout(1500)
    # 登录遮罩会拦截点击（本地验证无需登录），直接移除
    page.evaluate("() => { const m = document.getElementById('loginMask'); if (m) m.remove(); }")

    # 0. 足球态：tab 栏与统计条可见
    assert page.query_selector(".maintab").is_visible(), "足球 tab 栏初始应可见"
    assert page.query_selector("#statBar").is_visible(), "统计条初始应可见"

    # 1. 进入篮球专区 → 整页化断言
    btn = page.query_selector('button[data-tab="basketball"]')
    assert btn, "竞彩篮球 tab 按钮不存在"
    btn.click()
    page.wait_for_timeout(600)
    assert not page.query_selector(".maintab").is_visible(), "整页化失败：足球 tab 栏应隐藏"
    assert not page.query_selector("#statBar").is_visible(), "整页化失败：统计条应隐藏"
    print("1. 整页化 OK：足球 tab 栏与统计条已隐藏，专区独占内容区")
    head = page.query_selector("#basketballView .bk-head")
    assert head, "专区头部不存在"
    assert "竞彩篮球" in head.inner_text()
    print("   专区头部 OK：%s" % head.inner_text().split("\n")[0])
    # 返回按钮
    back = page.query_selector("#bkBackBtn")
    assert back and back.is_visible(), "返回按钮不存在或不可见"
    print("   返回按钮 OK：%s" % back.inner_text().strip())

    # 2. 二级 tab 栏
    subs = page.query_selector_all("#bkSubTabs .bk-subtab")
    labels = [s.inner_text().strip() for s in subs]
    print("2. 二级 tab：%s" % labels)
    assert "赛程赔率" in labels[0] and "赛后回看" in labels[1]

    # 3. 赛程赔率 pane（默认，未开赛 14 场）
    assert page.query_selector("#bkSchedPane").is_visible()
    cards = page.query_selector_all("#bkList .bk-card")
    print("3. 赛程赔率：%d 张卡片" % len(cards))
    assert len(cards) == 14, "应有 14 张未完赛卡片，实际 %d" % len(cards)
    txt = cards[0].inner_text()
    for probe in ["301", "底特律活塞", "波士顿凯尔特人", "让分胜负", "大小分"]:
        assert probe in txt, "首卡片缺「%s」" % probe
    print("   首卡 OK：%s" % " | ".join(txt.split("\n")[:5]))
    page.screenshot(path="/tmp/basketball_zone_sched.png", full_page=False)

    # 4. 切到赛后回看：生产数据无赛果 → 空态提示
    page.click('#bkSubTabs .bk-subtab[data-bksub="hist"]')
    page.wait_for_timeout(500)
    assert page.query_selector("#bkHistPane").is_visible()
    assert not page.query_selector("#bkSchedPane").is_visible(), "sched pane 应隐藏"
    hist_txt = page.query_selector("#bkHistList").inner_text()
    assert "暂无已回填赛果" in hist_txt, "空态提示缺失：%s" % hist_txt[:100]
    print("4. 赛后回看空态 OK：%s" % hist_txt.split("\n")[0])

    # 5. 注入两条 mock 赛果 → 回看渲染
    page.evaluate(MOCK)
    page.wait_for_timeout(500)
    hcards = page.query_selector_all("#bkHistList .bk-card")
    groups = page.query_selector_all("#bkHistList .bk-group-title")
    print("5. 回看渲染：%d 张卡片，日期分组 %s" % (len(hcards), [g.inner_text() for g in groups]))
    assert len(hcards) == 2, "应有 2 张回看卡片，实际 %d" % len(hcards)
    assert len(groups) == 1 and "2026-10-21" in groups[0].inner_text()
    # 倒序：07:00 的周二302 排最前
    txt = hcards[0].inner_text()
    for probe in ["周二302", "纽约尼克斯", "费城76人", "125 : 112", "已完赛", "让分主胜", "大分"]:
        assert probe in txt, "首张回看卡缺「%s」：%s" % (probe, txt[:200])
    print("   首卡 OK：%s" % " | ".join(txt.split("\n")[:6]))
    # 次卡：03:00 的周二301，让分客胜 + 小分
    txt2 = hcards[1].inner_text()
    for probe in ["周二301", "底特律活塞", "波士顿凯尔特人", "108 : 112", "让分客胜", "小分"]:
        assert probe in txt2, "次张回看卡缺「%s」" % probe
    print("   次卡 OK：%s" % " | ".join(txt2.split("\n")[:6]))
    page.screenshot(path="/tmp/basketball_zone_hist.png", full_page=False)

    # 6. 回看日期筛选（全部 2 + 10-21 2）
    chips = page.query_selector_all("#bkHistDateBar button[data-bkhdate]")
    print("6. 回看日期筛选：%s" % [c.inner_text().strip() for c in chips])
    assert len(chips) == 2

    # 7. 返回足球：tab 栏与统计条恢复，回到进来前的足球 tab（today）
    page.click("#bkBackBtn")
    page.wait_for_timeout(600)
    assert page.query_selector(".maintab").is_visible(), "返回后足球 tab 栏应恢复"
    assert page.query_selector("#statBar").is_visible(), "返回后统计条应恢复"
    assert page.query_selector("#cardsView").is_visible(), "应回到今日赛程 tab"
    on_tab = page.evaluate("() => { const b = document.querySelector('.maintab button.on'); return b ? b.dataset.tab : null; }")
    assert on_tab == "today", "返回后高亮应为 today，实际 %s" % on_tab
    print("7. 返回足球 OK：tab 栏/统计条恢复，回到「今日赛程」")

    # 8. 从其他足球 tab 进篮球再返回，应回到那个 tab（lastFbTab 记忆）
    page.click('button[data-tab="history"]')
    page.wait_for_timeout(500)
    page.click('button[data-tab="basketball"]')
    page.wait_for_timeout(500)
    assert not page.query_selector(".maintab").is_visible()
    page.click("#bkBackBtn")
    page.wait_for_timeout(500)
    on_tab = page.evaluate("() => { const b = document.querySelector('.maintab button.on'); return b ? b.dataset.tab : null; }")
    assert on_tab == "history", "从历史战绩进篮球应返回历史战绩，实际 %s" % on_tab
    print("8. 返回记忆 OK：从「历史战绩」进入，返回仍落在「历史战绩」")

    assert not errors, "JS 错误：%s" % errors[:3]
    print("9. 无 JS 错误")
    browser.close()
print("ALL PASS")
