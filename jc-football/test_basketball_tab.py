#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球专区（二级 tab 结构）浏览器级验证（v1.7.30）。

验证点：
  1. 进入「竞彩篮球」呈现独立专区（头部 + 二级 tab 栏）
  2. 二级 tab：赛程赔率 / 赛后回看
  3. 赛后回看渲染（比分/让分/大小分判定/日期分组/日期筛选）
  4. 切回赛程 + 切回足球 tab 回归
  5. 无 JS 错误
"""
import sys

from playwright.sync_api import sync_playwright

PATH = sys.argv[1] if len(sys.argv) > 1 else "/workspace/jc-football/index.html"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + PATH)
    page.wait_for_timeout(1500)
    # 登录遮罩会拦截点击（本地验证无需登录），直接移除
    page.evaluate("() => { const m = document.getElementById('loginMask'); if (m) m.remove(); }")

    # 1. 进入篮球专区
    btn = page.query_selector('button[data-tab="basketball"]')
    assert btn, "竞彩篮球 tab 按钮不存在"
    btn.click()
    page.wait_for_timeout(600)
    head = page.query_selector("#basketballView .bk-head")
    assert head, "专区头部不存在"
    assert "竞彩篮球" in head.inner_text()
    print("1. 专区头部 OK：%s" % head.inner_text().split("\n")[0])

    # 2. 二级 tab 栏
    subs = page.query_selector_all("#bkSubTabs .bk-subtab")
    labels = [s.inner_text().strip() for s in subs]
    print("2. 二级 tab：%s" % labels)
    assert "赛程赔率" in labels[0] and "赛后回看" in labels[1]

    # 3. 赛程赔率 pane（默认）
    assert page.query_selector("#bkSchedPane").is_visible()
    cards = page.query_selector_all("#bkList .bk-card")
    print("3. 赛程赔率：%d 张卡片" % len(cards))
    assert len(cards) > 0
    txt = cards[0].inner_text()
    for probe in ["301", "底特律活塞", "波士顿凯尔特人", "让分胜负", "大小分"]:
        assert probe in txt, "首卡片缺「%s」" % probe
    print("   首卡 OK：%s" % " | ".join(txt.split("\n")[:5]))

    # 4. 切到赛后回看
    page.click('#bkSubTabs .bk-subtab[data-bksub="hist"]')
    page.wait_for_timeout(600)
    assert page.query_selector("#bkHistPane").is_visible()
    assert not page.query_selector("#bkSchedPane").is_visible(), "sched pane 应隐藏"
    hcards = page.query_selector_all("#bkHistList .bk-card")
    groups = page.query_selector_all("#bkHistList .bk-group-title")
    print("4. 赛后回看：%d 张卡片，日期分组 %s" % (len(hcards), [g.inner_text() for g in groups]))
    assert len(hcards) == 2, "应有 2 张回看卡片（mock 数据），实际 %d" % len(hcards)
    assert len(groups) == 1
    # 回看按开球时间倒序（最近的在前）：03:00 的周一303 排最前
    txt = hcards[0].inner_text()
    for probe in ["周一303", "特内里费", "77 : 82", "已完赛", "让分客胜", "大分"]:
        assert probe in txt, "回看卡片缺「%s」：%s" % (probe, txt[:200])
    print("   首卡 OK：%s" % " | ".join(txt.split("\n")[:5]))
    # 第二张（02:30 的周一302）
    txt2 = hcards[1].inner_text()
    for probe in ["周一302", "巴伦西亚", "93 : 116"]:
        assert probe in txt2, "第二张回看卡片缺「%s」" % probe
    print("   次卡 OK：%s" % " | ".join(txt2.split("\n")[:5]))

    # 5. 回看日期筛选
    chips = page.query_selector_all("#bkHistDateBar button[data-bkhdate]")
    print("5. 回看日期筛选：%d 个" % len(chips))
    assert len(chips) >= 2

    # 5.5 篮球专区状态截图（赛后回看视角）
    page.screenshot(path="/tmp/basketball_zone_hist.png", full_page=False)

    # 6. 切回赛程 + 切回足球 tab（回归）
    page.click('#bkSubTabs .bk-subtab[data-bksub="sched"]')
    page.wait_for_timeout(400)
    assert page.query_selector("#bkSchedPane").is_visible()
    page.screenshot(path="/tmp/basketball_zone_sched.png", full_page=False)
    page.click('button[data-tab="today"]')
    page.wait_for_timeout(600)
    assert page.query_selector("#cardsView").is_visible(), "足球今日 tab 回归失败"
    print("6. 切回赛程 OK + 足球 tab 回归 OK")

    assert not errors, "JS 错误：%s" % errors[:3]
    print("7. 无 JS 错误")
    browser.close()
print("ALL PASS")
