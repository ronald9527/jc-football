#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_expert_content_ui.py —— v1.7.127 高手推荐文章内容 UI 验证

断言（手机视口 390×844，默认本地 http://127.0.0.1:8901/index.html）：
  v1.7.129 起全面数据驱动，不硬编码某一天的文章数/专家名/比分/版本号，
  源站每天刷新后测试仍可复用：
  1. 页面 VERSION 跟随 template.html 真相源
  2. 专家文章流篇数 = expert_recommendations.json 的 articles 数，摘要含「原文依据」
  3. 遍历全部分页：数据里真实存在的「原文结论依据」文本在页面可见
  4. 无「未知专家」空壳卡片，文章显示「内容已核对」标记
  5. 数据里存在无明确方向文章时，页面有「未给出明确胜平负方向」诚实说明
  6. 历史战绩「原文方向与当时标注不一致」存疑提示数 ≥ 数据里的存疑数
  7. 今日推荐区有内容、无空卡
  8. 无 JS 错误
"""
import asyncio
import json
import os
import re
import sys
from playwright.async_api import async_playwright

URL = os.environ.get("UI_TEST_URL", "http://127.0.0.1:8901/index.html")
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.environ.get("UI_TEST_SHOT", "/workspace/高手推荐内容核对-页面.png")
PASS, FAIL = [], []


def ck(cond, name, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


async def main():
    ed = json.load(open(os.path.join(HERE, "expert_recommendations.json"), encoding="utf-8"))
    eh = json.load(open(os.path.join(HERE, "expert_history.json"), encoding="utf-8"))
    n_disp = sum(1 for v in eh.values() for r in v if r.get("directionDisputed"))
    # v1.7.129：断言全面数据驱动，不再硬编码某一天的文章数/专家名/比分。
    # 源站每天的文章与对阵都不同，写死会在每次刷新后误报（11-08 实测 23→19 篇）。
    arts = ed.get("articles", [])
    n_art = len(arts)
    # 取一条真实的「原文结论依据 / 正文」文本，用于验证页面确实渲染了原文依据。
    # v1.7.130：源站文章有的当天只有短依据（如「方向：胜」5 字），旧逻辑 len>=6
    # 会把样本全过滤掉，进而在「无样本可验」时误报 FAIL。改为多字段取样本，
    # 并把阈值降到 2；同时补一个页面标记断言，保证即使无样本也能验结构。
    sample_ev = ""
    for a in arts:
        for r in a.get("recommendations", []):
            e = (r.get("evidence") or r.get("directionEvidence")
                 or r.get("scoreEvidence") or "").strip()
            if e and len(e) >= 2:
                sample_ev = e[:12]
                break
        if not sample_ev:
            cs = (a.get("contentSummary") or "").strip()
            if cs and len(cs) >= 2:
                sample_ev = cs[:12]
        if sample_ev:
            break
    has_nodir = any(not a.get("recommendations") for a in arts)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script("localStorage.setItem('jc_logged_in','1')")
        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        await page.evaluate("() => { const m=document.getElementById('loginMask'); if(m) m.style.display='none'; }")
        await page.wait_for_timeout(300)

        html = await page.content()
        # 版本跟随 template.html 真相源，避免每次升版都要改测试
        tpl_ver = re.search(r'const VERSION = "([^"]+)"',
                            open(os.path.join(HERE, "template.html"), encoding="utf-8").read()).group(1)
        ck('const VERSION = "%s"' % tpl_ver in html, "页面版本 %s" % tpl_ver)

        # 进入高手推荐：今日推荐区有内容、无未知专家空卡（结构性断言）
        await page.click('button[data-tab="expert"]')
        await page.wait_for_timeout(500)
        today_txt = await page.inner_text("#expertView")
        n_today = len(ed.get("recommendations", []))
        ck(n_today > 0 and len(today_txt) > 100, "今日推荐区有内容（数据 %d 条）" % n_today)
        ck("未知专家" not in today_txt, "今日推荐无「未知专家」空卡")

        # 专家文章流：遍历全部分页收集文本（分页，第一页只有部分文章）
        await page.click('button[data-expert-view="articles"]')
        await page.wait_for_timeout(600)
        summary = await page.inner_text("#expertArticlesSummary")
        ck((str(n_art) in summary and "篇" in summary), "专家文章流共 %d 篇（数据驱动）" % n_art, summary[:90])
        ck("方向与比分均附原文依据" in summary, "摘要标注可逐条核对")

        pages = await page.locator("#expertArticlesPager button").count()
        all_art = await page.inner_text("#expertArticlesWrap")
        for i in range(2, 10):
            btns = page.locator("#expertArticlesPager button", has_text=str(i))
            if await btns.count() == 0:
                break
            await btns.first.click()
            await page.wait_for_timeout(280)
            all_art += "\n" + await page.inner_text("#expertArticlesWrap")

        # 原文结论依据：用数据里的真实文本验证页面确实渲染（不再写死「欧塞尔不败」）。
        # 无样本可验时按「跳过」处理（记为通过并注明），不能把「今天没有长依据」
        # 误判成「页面坏了」；同时用页面标记断言兜底保证结构仍正确。
        if sample_ev:
            ck(sample_ev in all_art, "原文结论依据可见（数据样本「%s」）" % sample_ev)
        else:
            ck("原文结论：" in all_art or "正文：" in all_art,
               "原文依据区块可见（当日无长样本，按结构断言）", "已跳过内容比对")
        ck("原文结论：" in all_art or "正文：" in all_art,
           "文章流渲染原文依据区块（原文结论 / 正文）")
        ck("内容已核对" in all_art, "文章显示「内容已核对」标记")
        ck("未知专家" not in all_art, "无「未知专家」空壳卡片")
        # 无明确方向文章：数据里有才要求页面诚实说明，没有则跳过（避免每年误报）
        found_nodir = "未给出明确胜平负方向" in all_art
        ck(found_nodir if has_nodir else True,
           "无明确方向文章有诚实说明（数据存在=%s）" % has_nodir, "页数=%d" % pages)

        # 历史战绩存疑提示
        await page.click('button[data-expert-view="history"]')
        await page.wait_for_timeout(600)
        hist_txt = await page.inner_text("#expertView")
        cnt = hist_txt.count("原文方向与当时标注不一致")
        ck(cnt >= n_disp, "历史存疑提示可见（应≥%d，实际%d）" % (n_disp, cnt))
        ck("保留当时标注以保证命中统计可追溯" in hist_txt, "存疑提示说明不改已结算命中")

        ck(not errors, "无 JS 运行错误", errors[:2])
        await page.screenshot(path=SHOT, full_page=False)
        await browser.close()
    print("\n=== PASS %d / FAIL %d ===" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
