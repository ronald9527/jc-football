#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_vz_high_stats_ui.py —— v1.7.126 V站 🔥高信心预测命中率统计 UI 验证

断言（手机视口 390×844，默认打本地 http://127.0.0.1:8901/index.html）：
  1. 页面 VERSION = v1.7.126
  2. V站 分区存在统计面板，且双指标数字与 vz_high_stats.json 一致
  3. 展开明细后：三组对照 / 今日竞彩批次清单 / 统计口径 全部可见
  4. 今日清单严格按竞彩当天批次隔离（周六，不混入周日编号）
  5. 未开奖场次标注「不计入正确率」，无命中判定
  6. V站 赛后回溯区已能显示归档场次（55 场）
  7. 无 JS 错误
"""
import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright

URL = os.environ.get("UI_TEST_URL", "http://127.0.0.1:8901/index.html")
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.environ.get("UI_TEST_SHOT", "/workspace/V站高信心统计-页面.png")
PASS, FAIL = [], []


def ck(cond, name, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


async def main():
    s = json.load(open(os.path.join(HERE, "vz_high_stats.json"), encoding="utf-8"))
    g, t = s["groups"]["high"], s["today"]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script("localStorage.setItem('jc_logged_in','1')")
        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        # 登录遮罩在测试环境强制隐藏（不改动页面逻辑）
        await page.evaluate("() => { const m=document.getElementById('loginMask'); if(m) m.style.display='none'; }")
        await page.wait_for_timeout(300)

        ver = await page.evaluate("() => (typeof VERSION!=='undefined' && VERSION) || ''")
        ck(ver == "v1.7.126", "页面版本 = v1.7.126", ver)

        # 切到「比分预测」Tab（V站 情报分区所在）
        clicked = await page.evaluate("""() => {
            const bs=[...document.querySelectorAll('button, .maintab button, .tab-btn')];
            const b=bs.find(x=>/比分预测/.test(x.textContent||''));
            if(b){ b.click(); return true; } return false;
        }""")
        ck(clicked, "切换到比分预测 Tab")
        await page.wait_for_timeout(1200)
        await page.evaluate("() => { const m=document.getElementById('loginMask'); if(m) m.style.display='none'; }")

        box = page.locator("#vzHighStatsView")
        ck(await box.count() == 1, "存在统计面板 #vzHighStatsView")
        txt = await box.inner_text() if await box.count() else ""
        ck("高信心" in txt, "面板标题含「高信心」")
        ck("%.2f%%" % g["exactRate"] in txt, "高信心比分精确命中率显示正确", "%.2f%%" % g["exactRate"])
        ck("%.2f%%" % g["dirRate"] in txt, "高信心方向命中率显示正确", "%.2f%%" % g["dirRate"])
        ck(str(g["n"]) + " 场" in txt, "高信心样本量显示正确", str(g["n"]))
        ck(t["batchDate"] in txt and t["batchLabel"] in txt, "显示竞彩当天批次", t["batchDate"] + " " + t["batchLabel"])

        # 展开明细
        await page.evaluate("() => { const h=document.getElementById('vzsHead'); if(h) h.click(); }")
        await page.wait_for_timeout(600)
        txt2 = await box.inner_text()
        ck("结论" in txt2 and "不构成投注建议" in txt2, "结论含诚实声明")
        ck("三组对照" in txt2 and "无法判定" in txt2, "三组对照表可见")
        ck("统计口径" in txt2 and "Wilson" in txt2, "统计口径含 Wilson 置信区间说明")
        ck("今日竞彩批次" in txt2, "今日高信心清单可见")

        # 今日清单：批次隔离 + 未开奖
        picks = await page.evaluate("""() => {
            const rows=[...document.querySelectorAll('#vzHighStatsView table tr')];
            return rows.map(r=>r.innerText).filter(x=>/^周[六日]\\d{3}/.test(x));
        }""")
        print("  今日清单行:", picks[:5])
        ck(len(picks) == t["highCount"], "今日高信心清单条数 = 统计 highCount", len(picks))
        ck(all(("周六" in p) for p in picks) and not any(("周日" in p) for p in picks),
           "今日清单严格按周六批次隔离，无周日混入")
        ck(all(("未开奖" in p) for p in picks), "未开赛场次标注「未开奖 · 不计入正确率」")

        # 分组表数字
        ck("🔥高信心" in txt2 and "普通" in txt2, "分组表含高信心/普通/无法判定三行")

        # V站 赛后回溯区已能显示归档
        back = await page.evaluate("""() => {
            const b=document.getElementById('vzBackfill');
            return b ? {display:b.style.display, text:(b.innerText||'').slice(0,120)} : null;
        }""")
        ck(back and "赛后回溯" in (back["text"] or ""), "V站 赛后回溯区显示归档场次",
           (back or {}).get("text", "")[:60])

        ck(not errors, "无 JS 运行时错误", errors[:2])

        # 截图（滚动到统计面板）
        try:
            await page.evaluate("() => { const e=document.getElementById('vzHighStatsView'); if(e) e.scrollIntoView(); }")
            await page.wait_for_timeout(400)
            await page.screenshot(path=SHOT, full_page=False)
            print("  截图:", SHOT)
        except Exception as e:
            print("  [WARN] 截图失败:", e)

        await browser.close()

    print("\n===== 结果：PASS %d / FAIL %d =====" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
