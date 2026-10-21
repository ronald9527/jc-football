# -*- coding: utf-8 -*-
"""线上 v1.7.33 串关真实口径 UI 验证（手机视口 390×844）
断言：
1. 均衡档卡：推荐标签（非观望）、真实期望 +15.7% 主显示、本档历史战绩行
2. 稳健档卡：观望标签、真实命中 vs 平衡线文案
3. 精锐档：诚实留空说明（今日仅 xx 达标卡）
4. 科普卡① 含 EV 双口径说明
5. 无 JS 错误
"""
import asyncio, sys
from playwright.async_api import async_playwright

URL = "https://jc-football-1eo.pages.dev/"

async def main():
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2500)

        # 打开「串关推荐」tab（找到并点击）
        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('.maintab button, .tab-btn, button')];
            const b = btns.find(x => /串关/.test(x.textContent||''));
            if (b) { b.click(); return true; }
            return false;
        }""")
        print("点击串关 tab:", clicked)
        await page.wait_for_timeout(1500)

        # 提取串关区文本
        text = await page.evaluate("""() => {
            const cards = [...document.querySelectorAll('.ho-card')];
            return cards.map(c => c.textContent.replace(/\\s+/g,' ').trim()).filter(t => /稳健|均衡|精锐|串关|观望|达标/.test(t));
        }""")
        print(f"\n串关区卡片 {len(text)} 张：")
        for t in text[:8]:
            print(" ·", t[:150])

        full = await page.evaluate("() => document.body.textContent.replace(/\\s+/g,' ')")

        # 断言 1：均衡档推荐
        print("\n[断言]")
        checks = [
            ("均衡卡含「真实期望 +15.7%」或正期望文案", "正期望" in full and "+15.7%" in full.replace("−","-") or "+15.7%" in full),
            ("均衡档位卡显示推荐（⚖️ 图标、无观望标签）", "⚖️ 均衡 · 3串1" in full and "均衡 · 3串1 · 观望" not in full),
            ("稳健卡显示观望标签", "稳健 · 2串1 · 观望" in full or "⚠️ 稳健" in full),
            ("稳健卡命中率 vs 平衡线文案", "25" in full and "28" in full and "平衡线" in full),
            ("本档历史战绩行存在", "本档历史战绩" in full),
            ("精锐留空说明存在", "精锐4串1 今日无达标组合" in full or "今日仅" in full),
            ("科普卡①含 EV 双口径", "EV 有两个口径" in full),
            ("科普卡①含真实口径判定说明", "推荐判定只看历史口径" in full or "推荐判定以历史口径为准" in full),
            ("档位卡含历史同指命中率与平衡线小字", "平衡线 12.8%" in full or "平衡线 28.3%" in full),
            ("模型期望降级为参考展示", "模型期望" in full),
        ]
        ok = True
        for name, cond in checks:
            print(f"  {'✓' if cond else '✗'} {name}")
            if not cond: ok = False
        if errors:
            print("JS 错误:", errors[:3]); ok = False
        else:
            print("  ✓ 无 JS 错误")
        await browser.close()
        print("\n结果:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)

asyncio.run(main())
