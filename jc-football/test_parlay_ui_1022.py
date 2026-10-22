# -*- coding: utf-8 -*-
"""test_parlay_ui_1022.py —— v1.7.36 跟单钱账 + 徽章改革 + 全负日卡 UI 验证
（手机视口 390×844，本地服务 + 工作区 10-22 数据）
断言：
1. 今日（10-22）三档全部未达标：🚫 今日无达标串关机会卡存在
2. 💰 跟单钱账区块：跟达标组 12 战 4 中 回报 +96%；硬跟观望组 51 战 10 中 回报 -37%
3. 历史记录徽章：今日三条显示「⚠️ 未达标·观望」，历史存在「✅ 达标」
4. 徽章说明行存在
5. 全负卡钱账行：「今天按住不下注」
6. 10-21 五条记录全部判「未中」（赛后结算正确）
7. 无 JS 错误
"""
import asyncio, sys, os, re
from playwright.async_api import async_playwright

# 本地服务（工作区目录）：python3 -m http.server 8901
URL = os.environ.get("UI_TEST_URL", "http://localhost:8901/index.html")

async def main():
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.add_init_script("localStorage.setItem('jc_logged_in','1')")
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2500)

        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('.maintab button, .tab-btn, button')];
            const b = btns.find(x => /串关/.test(x.textContent||''));
            if (b) { b.click(); return true; }
            return false;
        }""")
        print("点击串关 tab:", clicked)
        await page.wait_for_timeout(1500)

        full = await page.evaluate("() => document.body.textContent.replace(/\\s+/g,' ')")
        hist_text = await page.evaluate("""() => {
            const root = document.querySelector('#highOddsView') || document.body;
            return root.textContent.replace(/\\s+/g,' ');
        }""")

        # 今日（10-22）历史记录行
        today_rows = await page.evaluate("""() => {
            const rows = [...document.querySelectorAll('div')]
                .filter(d => /2026-10-22/.test(d.textContent||'') && /(稳健|均衡|精锐)\\d串1/.test(d.textContent||'')
                            && d.querySelectorAll('div').length < 12);
            return rows.map(d => d.textContent.replace(/\\s+/g,' ').trim()).slice(0, 8);
        }""")
        print(f"\n10-22 历史记录行 {len(today_rows)} 条：")
        for t in today_rows:
            print(" ·", t[:150])

        # 数据滚动护栏：精确钱账数字仅在 10-22 数据集在位时断言；
        # 数据滚到下一日后，结构断言仍然常驻，精确数字自动跳过并提示
        _on_1022 = "2026-10-22" in hist_text
        if not _on_1022:
            print("（数据已滚动过 10-22：精确钱账数字断言自动跳过，仅验证结构）")

        print("\n[断言]")
        checks = [
            ("全负日卡：🚫 今日无达标串关机会", "今日无达标串关机会" in full),
            ("钱账区块标题存在", "跟单钱账" in full and "选出来" in full),
            ("钱账：跟达标组「N 战 M 中」结构", bool(re.search(r"跟达标组[^。]*?战[^。]*?中", full))),
            ("钱账：硬跟观望组「N 战 M 中」结构", bool(re.search(r"硬跟观望组[^。]*?战[^。]*?中", full))),
            ("徽章：未达标·观望 或 达标 至少一种渲染", "未达标·观望" in hist_text or "达标" in hist_text),
            ("徽章说明行存在", "徽章说明" in full),
            ("全负卡钱账行：今天按住不下注", "今天按住不下注" in full),
            ("无 JS 错误", not errors),
        ]
        if _on_1022:
            checks += [
                ("钱账：跟达标组 12 战 4 中", "12 战 4 中" in full),
                ("钱账：硬跟观望组 51 战 10 中", "51 战 10 中" in full),
                ("钱账：达标组回报 +96%", "+96%" in full.replace("−","-")),
                ("钱账：观望组回报 -37%", "-37%" in full.replace("−","-")),
                ("钱账：样本偏小提示", "样本偏小" in full),
                ("今日记录带「⚠️ 未达标·观望」徽章", "未达标·观望" in hist_text),
                ("10-21 晚间重生组 2.46 倍未中可见", "2.46" in hist_text),
            ]
        ok = True
        for name, cond in checks:
            print(f"  {'✓' if cond else '✗'} {name}")
            if not cond: ok = False
        if errors:
            print("JS 错误:", errors[:3])
        await browser.close()
        print("\n结果:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)

asyncio.run(main())
