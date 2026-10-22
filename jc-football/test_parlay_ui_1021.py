# -*- coding: utf-8 -*-
"""test_parlay_ui_1021.py —— v1.7.35 停售腿门 + 003 历史补回的 UI 持久回归
（手机视口 390×844；默认打本地服务，UI_TEST_URL 可覆盖）

数据每日滚动，本测试只断言**不随日期失效**的持久特性：
1. 历史战绩区展示补回的含 003 旧组（❌ 未中，带逐腿 ✗ 证据）——v1.7.35「先判定后门禁」的
   用户可对账承诺：被停售腿门删过的推荐，必须能在历史里查到并按官方赛果判错
2. 历史记录徽章：「⚠️ 未达标·观望」/「✅ 达标」+ 徽章说明行（v1.7.36）
3. 当前推荐档位卡（若有）不含停售腿 003
4. 无 JS 错误

10-21 当日的档位卡数值（2.70/4.89/+5.4%）随数据滚动失效，由函数级测试
（test_parlay_judge_before_gate / test_parlay_soldout_gate / test_parlay_decided_miss）
与当日数据测试（test_parlay_ui_1022.py）覆盖，本文件不再断言。
"""
import asyncio, sys, os
from playwright.async_api import async_playwright

# 默认打本地服务（工作区目录）：python3 -m http.server 8901
# 线上验证：UI_TEST_URL=https://jc-football-1eo.pages.dev/
URL = os.environ.get("UI_TEST_URL", "http://localhost:8901/index.html")

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

        full = await page.evaluate("() => document.body.textContent.replace(/\\s+/g,' ')")
        hist_text = await page.evaluate("""() => {
            const root = document.querySelector('#highOddsView') || document.body;
            return root.textContent.replace(/\\s+/g,' ');
        }""")
        # 当前推荐档位卡（含 🛡️稳健/⚖️均衡/💎精锐 标记的 ho-card）
        tier_cards = await page.evaluate("""() => [...document.querySelectorAll('.ho-card')]
            .filter(c => /[🛡️⚖️💎⚠️]\\s*(稳健|均衡|精锐)\\s*·/.test(c.textContent||''))
            .map(c => c.textContent.replace(/\\s+/g,' ').trim())""")
        tier_text = " | ".join(tier_cards)
        print(f"当前推荐档位卡 {len(tier_cards)} 张（全负日时为 0 张，属正常）")

        print("\n[断言]")
        checks = [
            ("历史战绩区标题存在", "串关历史战绩" in full),
            # v1.7.35 核心承诺：被停售腿门删过的含 003 推荐，历史可查、按官方赛果判错
            ("历史区展示补回的 003 旧组", "周三003" in hist_text),
            ("003 旧组判「未中」", "未中" in hist_text),
            ("逐腿 ✗ 证据存在", "✗" in hist_text),
            # v1.7.36 徽章改革
            ("徽章：未达标·观望 或 达标 至少一种渲染", "未达标·观望" in hist_text or "达标" in hist_text),
            ("徽章说明行存在", "徽章说明" in full),
            # 停售腿门：003 不得出现在当前推荐档位卡（历史存档区展示是应该的）
            ("当前推荐不含停售腿 003（档位卡范围）", "周三003" not in tier_text),
            ("无 JS 错误", not errors),
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
