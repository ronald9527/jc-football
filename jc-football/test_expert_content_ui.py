#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_expert_content_ui.py —— v1.7.127 高手推荐文章内容 UI 验证

断言（手机视口 390×844，默认本地 http://127.0.0.1:8901/index.html）：
  1. 页面 VERSION = v1.7.127
  2. 专家文章流 23 篇，摘要含「方向与比分均附原文依据」
  3. 冒险王：方向「主胜/平局」、比分「比分1比1、2比1」、原文依据「本场比赛看好欧塞尔不败」
  4. 不再出现旧的错误方向（周六026 不显示「客胜」），无「未知专家」空卡
  5. 「未给出明确胜平负方向」的无方向文章有诚实说明（刘建宏）
  6. 历史战绩：3 条「原文方向与当时标注不一致」存疑提示
  7. 今日推荐：周六026 显示修正后的「主胜/平局」
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

        # 进入高手推荐
        await page.click('button[data-tab="expert"]')
        await page.wait_for_timeout(500)
        today_txt = await page.inner_text("#expertView")
        ck("主胜/平局" in today_txt, "今日推荐出现修正后的「主胜/平局」")
        ck("比分1比1、2比1" in today_txt, "今日推荐出现完整比分建议 1:1、2:1")
        # 周六026 区块内不能还是「客胜」
        seg = today_txt.split("周六026")[-1].split("周六027")[0] if "周六026" in today_txt else ""
        ck("客胜" not in seg, "周六026 不再显示错误方向「客胜」", seg[:80].replace("\n", " "))

        # 专家文章流
        await page.click('button[data-expert-view="articles"]')
        await page.wait_for_timeout(600)
        art_txt = await page.inner_text("#expertArticlesWrap")
        summary = await page.inner_text("#expertArticlesSummary")
        ck(("23" in summary and "篇" in summary), "专家文章流共 23 篇", summary[:90])
        ck("方向与比分均附原文依据" in summary, "摘要标注可逐条核对")
        ck("本场比赛看好欧塞尔不败" in art_txt, "冒险王原文结论依据可见")
        ck("内容已核对" in art_txt, "文章显示「✅ 内容已核对」")
        ck("未知专家" not in art_txt, "无「未知专家」空壳卡片")
        ck("比分1比1、2比1" in art_txt, "文章流比分为完整 1:1、2:1")
        # 逐页找刘建宏那篇（无明确方向）
        pages = await page.locator("#expertArticlesPager button").count()
        found_nodir = "未给出明确胜平负方向" in art_txt
        if not found_nodir:
            for i in range(2, 8):
                btns = page.locator("#expertArticlesPager button", has_text=str(i))
                if await btns.count() == 0:
                    break
                await btns.first.click()
                await page.wait_for_timeout(300)
                art_txt = await page.inner_text("#expertArticlesWrap")
                if "未给出明确胜平负方向" in art_txt:
                    found_nodir = True
                    break
        ck(found_nodir, "无明确方向文章有诚实说明（不硬猜）", "页数=%d" % pages)

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
