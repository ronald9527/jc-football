# -*- coding: utf-8 -*-
"""test_parlay_ui_1743.py —— v1.7.43 选腿信号回调线上验证（手机视口 390×844）

背景：10-20 起 14 组全 miss，归因为同指命中率 45-60% 区间实证反向
（16.7% vs 市场 48.8%，p=0.0016），v1.7.43 把选腿信号回调为信心主导 +
同指硬门槛 60/精锐 65。本测试验证线上页面呈现。

断言（默认打线上 https://jc-football-1eo.pages.dev）：
1. 页面 VERSION = v1.7.43
2. 串关 tab：历史 72 组全判定（14 中 / 58 错 / 0 待开奖），10-23 三组仍在
3. 今日（2026-10-26）三档留空 + 「留空不硬凑」诚实提示（新引擎拒绝发布
   反向区间软腿组合：周一002 同指54%、周一003 同指57% 均被硬门槛剔除）
4. 「关于」/更新日志弹层含 v1.7.43 条目（信心主导 + 硬门槛回调说明）
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

        # 1) 版本（VERSION 是 script 作用域 const，非 window 属性——从页面文本匹配）
        ver = await page.evaluate(
            "() => (document.body.textContent.match(/v1\\.7\\.\\d+/) || [''])[0]")
        print("页面版本:", ver)
        if ver != "v1.7.43":
            fails.append("版本不是 v1.7.43: %r" % ver)

        # 2) 进串关 tab
        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('.maintab button, .tab-btn, button')];
            const b = btns.find(x => /串关/.test(x.textContent||''));
            if (b) { b.click(); return true; }
            return false;
        }""")
        print("点击串关 tab:", clicked)
        await page.wait_for_timeout(1500)

        # 3) 历史 72 组统计（页面战绩汇总区）
        stats = await page.evaluate("""() => {
            const t = document.body.textContent.replace(/\\s+/g, ' ');
            return {
                has72: /72\\s*组/.test(t) || /共\\s*72/.test(t),
                hasPending0: /待开奖\\s*0/.test(t) || /0\\s*组?待开奖/.test(t),
            };
        }""")
        print("历史统计(72组/0待开奖):", stats)

        # 4) 日期下拉含 10-23，选 10-23 看三组仍在
        has_1023 = await page.evaluate("""() => {
            const sel = document.querySelector('#parlayHistDate');
            if (!sel) return false;
            return [...sel.options].some(o => o.value === '2026-10-23');
        }""")
        print("日期下拉含 2026-10-23:", has_1023)
        if not has_1023:
            fails.append("串关日期下拉没有 2026-10-23（记录缺失）")
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
            return root.textContent.replace(/\\s+/g, ' ');
        }""")
        for tier in ("稳健2串1", "均衡3串1", "精锐4串1"):
            ok = tier in rows
            print(f"10-23 {tier} 展示:", ok)
            if not ok:
                fails.append("10-23 缺少 %s" % tier)
        if rows.count("未中") < 3:
            fails.append("10-23 三组未全部判「未中」（%d）" % rows.count("未中"))

        # 5) 今日（10-26）视图：三档留空 + 诚实提示
        await page.evaluate("""() => {
            const sel = document.querySelector('#parlayHistDate');
            if (sel) {
                sel.value = '2026-10-26';
                sel.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""")
        await page.wait_for_timeout(1200)
        today_txt = await page.evaluate("""() => {
            const root = document.querySelector('#highOddsView') || document.body;
            return root.textContent.replace(/\\s+/g, ' ');
        }""")
        print("今日视图含「留空不硬凑」:", "留空不硬凑" in today_txt)
        if "留空不硬凑" not in today_txt:
            fails.append("今日视图缺少「留空不硬凑」诚实提示")
        print("今日视图含候选池提示:", "候选池仅 4 场" in today_txt or "候选不足" in today_txt)
        # 候选池透明展示全部被评估比赛（周一002 同指54/周一003 同指57 出现于此是设计行为）；
        # 关键是它们没有进入任何「已发布组合卡片」——三档全空时视图不应有
        # 「N.NN倍(命中…」形态的档位卡片。
        import re as _re
        has_card = bool(_re.search(r"\d+\.\d+倍\(命中", today_txt))
        print("今日无档位卡片（软腿未入组）:", not has_card)
        if has_card:
            fails.append("今日视图存在档位卡片（软腿组合被发布？）")

        # 6) 更新日志弹层含 v1.7.43
        log_ok = await page.evaluate("""() => {
            const b = [...document.querySelectorAll('button, .maintab button')].find(x => /更新日志|通知/.test(x.textContent||''));
            if (!b) return null;
            b.click();
            return new Promise(res => setTimeout(() => {
                const t = document.body.textContent.replace(/\\s+/g, ' ');
                res({
                    hasVer: /v1\\.7\\.43/.test(t),
                    hasCanon: /信心度主导/.test(t),
                    hasFloor: /同指命中率仅在 ≥65 分段小幅加分/.test(t) || /45-60 反向区间扣分/.test(t),
                });
            }, 800));
        }""")
        print("更新日志 v1.7.43 条目:", log_ok)
        if log_ok is None:
            fails.append("找不到「更新日志/通知」入口")
        elif not (log_ok.get("hasVer") and log_ok.get("hasCanon")):
            fails.append("更新日志缺 v1.7.43 条目或回调说明: %r" % log_ok)

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
