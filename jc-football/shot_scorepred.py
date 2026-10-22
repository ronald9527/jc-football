#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图线上页面「比分预测」Tab，验证恢复效果（v1.7.39）。"""
import asyncio, sys
from playwright.async_api import async_playwright

URL = "https://jc-football-1eo.pages.dev/"
OUT = "/root/Desktop/比分预测已恢复-20261022.png"
OUT2 = "/workspace/比分预测已恢复-20261022.png"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 1600})
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(3000)
        # 移除登录遮罩
        await page.evaluate("""() => {
            const m = document.querySelector('#loginMask');
            if (m) { m.classList.remove('show'); m.style.display = 'none'; }
        }""")
        # 找「比分预测」Tab 并点击（Tab 文字带场次计数，用前缀匹配）
        clicked = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('.tab, [class*=tab], button, a, div, span, li'));
            for (const el of els) {
                const t = (el.textContent || '').trim();
                if (t.indexOf('比分预测') === 0 && el.offsetParent) {
                    el.click(); return t;
                }
            }
            return false;
        }""")
        print("点击比分预测 Tab:", clicked)
        await page.wait_for_timeout(2500)
        # 滚动到「自建 Poisson 模型参考」区（football-data.org 泊松预测）
        found = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,section'));
            for (const el of els) {
                const t = (el.textContent || '').trim();
                if (t.indexOf('自建 Poisson 模型参考') >= 0 && el.offsetParent
                    && el.getBoundingClientRect().height < 200) {
                    el.scrollIntoView({block: 'start'});
                    window.scrollBy(0, -90);
                    return t.slice(0, 40);
                }
            }
            return false;
        }""")
        print("滚动到泊松模型区:", found)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=OUT, full_page=False)
        await page.screenshot(path=OUT2, full_page=False)
        info = await page.evaluate("""() => {
            const body = document.body.innerText;
            const i = body.indexOf('Poisson 模型参考');
            return body.slice(Math.max(0, i - 50), i + 800);
        }""")
        print("---- 泊松模型区文本 ----")
        print(info[:900])
        await browser.close()
        print("截图已存:", OUT)

asyncio.run(main())
