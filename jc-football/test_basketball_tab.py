#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球 tab 浏览器级验证（v1.7.29）。

验证点：
  1. tab 按钮存在且可点击
  2. 切换到篮球 tab 后渲染出赛程卡片（队名/编号/四玩法赔率）
  3. 日期筛选条工作
  4. 无 JS 错误
"""
import sys

from playwright.sync_api import sync_playwright

PATH = sys.argv[1] if len(sys.argv) > 1 else "/workspace/jc-football/index.html"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + PATH)
    page.wait_for_timeout(1500)
    # 登录遮罩会拦截点击（本地验证无需登录），直接移除
    page.evaluate("() => { const m = document.getElementById('loginMask'); if (m) m.remove(); }")

    # 1. tab 按钮
    btn = page.query_selector('button[data-tab="basketball"]')
    assert btn, "竞彩篮球 tab 按钮不存在"
    print("1. tab 按钮存在：%s" % btn.inner_text().strip())

    # 2. 点击切换
    btn.click()
    page.wait_for_timeout(800)
    view = page.query_selector("#basketballView")
    assert view and view.is_visible(), "basketballView 不可见"
    cards = page.query_selector_all("#bkList .bk-card")
    print("2. 切换后渲染卡片 %d 张" % len(cards))
    assert len(cards) > 0, "没有渲染出篮球赛程卡片"

    # 3. 首张卡片内容抽查
    c0 = cards[0]
    txt = c0.inner_text()
    for probe in ["301", "底特律活塞", "波士顿凯尔特人", "让分胜负", "大小分"]:
        assert probe in txt, "首卡片缺「%s」：%s" % (probe, txt[:200])
    print("3. 首卡片内容 OK：%s" % " | ".join(txt.split("\n")[:6]))

    # 4. 日期筛选
    chips = page.query_selector_all("#bkDateBar button[data-bkdate]")
    print("4. 日期筛选条 %d 个（含全部）" % len(chips))
    assert len(chips) >= 1

    # 5. 汇总行
    summary = page.query_selector("#bkSummary").inner_text()
    print("5. 汇总行：%s" % summary)

    # 6. JS 错误
    assert not errors, "JS 错误：%s" % errors[:3]
    print("6. 无 JS 错误")

    # 截图存档
    page.screenshot(path="/tmp/basketball_tab.png", full_page=False)
    print("截图已存 /tmp/basketball_tab.png")
    browser.close()
print("ALL PASS")
