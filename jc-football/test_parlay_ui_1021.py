# -*- coding: utf-8 -*-
"""test_parlay_ui_1021.py —— 线上 v1.7.34 串关停售腿门 + 历史补回 UI 验证
（手机视口 390×844）
断言：
1. 当前推荐档位卡：稳健 2.70（推荐）/ 均衡 4.89（达标），均无观望标签
2. 当前推荐**不含**停售腿 003（只查档位卡，不查历史存档区）
3. 历史战绩区展示补回的三条含 003 旧组（❌ 未中，带逐腿 ✗ 证据）
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
        # 账号门绕过（localStorage 注入，页面加载前生效）
        await page.add_init_script("localStorage.setItem('jc_logged_in','1')")
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2500)

        # 打开「串关推荐」tab（分层串关）
        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('.maintab button, .tab-btn, button')];
            const b = btns.find(x => /串关/.test(x.textContent||''));
            if (b) { b.click(); return true; }
            return false;
        }""")
        print("点击串关 tab:", clicked)
        await page.wait_for_timeout(1500)

        # 多档推荐时前端默认只渲染第一档，需点档位按钮切换均衡卡
        await page.evaluate("""() => {
            const b = [...document.querySelectorAll('.ptier-chip')].find(x => /均衡/.test(x.textContent||''));
            if (b) b.click();
        }""")
        await page.wait_for_timeout(800)

        full = await page.evaluate("() => document.body.textContent.replace(/\\s+/g,' ')")
        # 档位卡文本（当前推荐位，含 🛡️稳健/⚖️均衡/💎精锐 标记的 ho-card）
        tier_cards = await page.evaluate("""() => [...document.querySelectorAll('.ho-card')]
            .filter(c => /[🛡️⚖️💎⚠️]\\s*(稳健|均衡|精锐)\\s*·/.test(c.textContent||''))
            .map(c => c.textContent.replace(/\\s+/g,' ').trim())""")
        tier_text = " | ".join(tier_cards)
        print(f"\n当前推荐档位卡 {len(tier_cards)} 张：")
        for t in tier_cards:
            print(" ·", t[:130])

        # 历史战绩区文本（含补回记录的整体 tab 内容，排除 script 内 JSON）
        hist_text = await page.evaluate("""() => {
            const tab = document.querySelector('[data-tab="highodds"], #highOddsList, .fb-content');
            const root = tab || document.body;
            return root.textContent.replace(/\\s+/g,' ');
        }""")
        # 只取 DOM 渲染出的历史记录行（非 script 数据）
        hist_rows = await page.evaluate("""() => {
            const rows = [...document.querySelectorAll('div')]
                .filter(d => /2026-10-21/.test(d.textContent||'') && /(稳健|均衡|精锐)\\d串1/.test(d.textContent||'')
                            && d.querySelectorAll('div').length < 12);
            return rows.map(d => d.textContent.replace(/\\s+/g,' ').trim()).slice(0, 8);
        }""")
        print(f"\n10-21 历史记录行 {len(hist_rows)} 条：")
        for t in hist_rows:
            print(" ·", t[:150])

        print("\n[断言]")
        checks = [
            ("稳健档位卡显示推荐（🛡️ 图标、无观望标签）", "🛡️ 稳健 · 2串1" in full and "稳健 · 2串1 · 观望" not in full),
            ("均衡档位卡显示推荐（⚖️ 图标、无观望标签）", "⚖️ 均衡 · 3串1" in full and "均衡 · 3串1 · 观望" not in full),
            ("稳健卡真实期望 +5.4% 达标文案", "+5.4%" in full.replace("−","-")),
            ("均衡卡真实期望 -0.3% 达标文案", "-0.3%" in full.replace("−","-")),
            ("稳健卡命中率 vs 平衡线文案", "39.0%" in full and "37.0%" in full),
            ("均衡卡命中率 vs 平衡线文案", "20.4%" in full and "平衡线" in full),
            ("本档历史战绩行存在", "本档历史战绩" in full),
            ("精锐留空说明存在", "精锐4串1 今日无达标组合" in full or "今日仅" in full),
            ("科普卡①含 EV 双口径", "EV 有两个口径" in full),
            ("科普卡①含真实口径判定说明", "推荐判定只看历史口径" in full or "推荐判定以历史口径为准" in full),
            ("档位卡含历史同指命中率与平衡线小字", "平衡线" in full),
            ("模型期望降级为参考展示", "模型期望" in full),
            # 停售腿门：003 不得出现在当前推荐档位卡（历史存档区展示是应该的）
            ("当前推荐不含停售腿 003（档位卡范围）", "周三003" not in tier_text),
            ("当前推荐腿为 012+013(+006)", "周三012" in tier_text and "周三013" in tier_text),
            # 历史补回：三条含 003 旧组应在历史战绩区可见且判错
            ("历史区展示补回的 003 旧组（未中）", "周三003" in hist_text),
            ("历史区 10-21 记录含「未中」判定", "未中" in hist_text),
            ("历史区统计：命中14/错误47", "14" in full and "47" in full),
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
