# -*- coding: utf-8 -*-
"""test_parlay_ui_1024.py —— v1.7.42 串关历史恢复 UI 验证（线上页面）

背景：用户报「昨天的串关推荐历史记录又跑哪里去了」。10-23 的 3 组推荐
此前只存在于本地，被对账规则整体覆盖后线上缺失；v1.7.42 已按签名并集
恢复（本地 ∪ 线上 = 72 条），赛果回填判定。

断言（手机视口 390×844，默认打线上 https://jc-football-1eo.pages.dev）：
1. 页面 VERSION = v1.7.42
2. 串关 tab 日期下拉含 2026-10-23（10-23 不再是「消失的一天」）
3. 选 10-23：展示 3 组（稳健2串1/均衡3串1/精锐4串1），均判「未中」，
   腿级赛果可见（吉马良斯 5:0、博尔顿 1:5、都灵 3:2 中；阿拉维斯 1:1、波鸿 1:1 错）
4. 「关于」弹层串关历史文案为云端保存表述（不再误导 localStorage）
5. 无 JS 错误
"""
import asyncio, os, sys
from playwright.async_api import async_playwright

URL = os.environ.get("UI_TEST_URL", "https://jc-football-1eo.pages.dev")


async def main():
    errors = []
    fails = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.add_init_script("localStorage.setItem('jc_logged_in','1')")
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(3000)

        # 1) 版本
        ver = await page.evaluate("() => (window.VERSION || document.body.textContent.match(/v1\\.7\\.\\d+/) || [''])[0]")
        print("页面版本:", ver)
        if ver != "v1.7.42":
            fails.append("版本不是 v1.7.42: %r" % ver)

        # 2) 进串关 tab
        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('.maintab button, .tab-btn, button')];
            const b = btns.find(x => /串关/.test(x.textContent||''));
            if (b) { b.click(); return true; }
            return false;
        }""")
        print("点击串关 tab:", clicked)
        await page.wait_for_timeout(1500)

        # 3) 日期下拉含 10-23
        has_1023 = await page.evaluate("""() => {
            const sel = document.querySelector('#parlayHistDate');
            if (!sel) return false;
            return [...sel.options].some(o => o.value === '2026-10-23');
        }""")
        print("日期下拉含 2026-10-23:", has_1023)
        if not has_1023:
            fails.append("串关日期下拉没有 2026-10-23（记录仍缺失）")

        # 4) 选 10-23，看 3 组与判定
        await page.evaluate("""() => {
            const sel = document.querySelector('#parlayHistDate');
            if (sel) {
                sel.value = '2026-10-23';
                sel.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""")
        await page.wait_for_timeout(1200)
        rows = await page.evaluate("""() => {
            const root = document.querySelector('#highOddsView') || document.body;
            const t = root.textContent.replace(/\\s+/g, ' ');
            return t;
        }""")
        for tier in ("稳健2串1", "均衡3串1", "精锐4串1"):
            ok = tier in rows
            print(f"10-23 {tier} 展示:", ok)
            if not ok:
                fails.append("10-23 缺少 %s" % tier)
        miss_n = rows.count("未中")
        print("10-23 视图「未中」标记数:", miss_n)
        if miss_n < 3:
            fails.append("10-23 三组未全部判「未中」（%d）" % miss_n)
        for frag in ("吉马良斯", "5:0", "博尔顿", "1:5", "都灵", "3:2",
                     "阿拉维斯", "1:1", "波鸿"):
            ok = frag in rows
            print(f"  含 {frag!r}:", ok)
            if not ok:
                fails.append("10-23 视图缺少赛果片段 %r" % frag)

        # 5) 关于弹层文案
        about_ok = await page.evaluate("""() => {
            const b = [...document.querySelectorAll('button')].find(x => /关于/.test(x.textContent||''));
            if (!b) return null;
            b.click();
            return new Promise(res => setTimeout(() => {
                const t = document.body.textContent.replace(/\\s+/g, ' ');
                res({
                    cloud: /串关历史.{0,40}云端服务器永久保存/.test(t),
                    storage: /所有推荐与历史数据均保存在云端服务器/.test(t),
                    oldCopy: /串关历史.{0,20}localStorage永久保存/.test(t),
                });
            }, 600));
        }""")
        print("关于弹层文案:", about_ok)
        if about_ok is None:
            fails.append("找不到「关于」按钮")
        elif not (about_ok.get("cloud") and about_ok.get("storage")) or about_ok.get("oldCopy"):
            fails.append("关于页文案不符合云端保存表述: %r" % about_ok)

        print("\nJS 错误:", errors if errors else "无")
        if errors:
            fails.append("页面 JS 错误: %s" % errors)

    print("=" * 60)
    if fails:
        print("FAILED %d 项:" % len(fails))
        for f in fails:
            print(" -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
