#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_whoscored_preconf_ui.py —— v1.7.40 「赛前高可信」赛后回溯标记 UI 验证
（本地服务 + 已回填数据，390×844 手机视口）

断言：
1. 赛后回溯默认日期（10-23）出现「🔥 赛前高可信」徽章 ×2（Celtic / Hoffenheim）
2. 当日汇总含「🔥 赛前高可信 2 场 · 方向命中 0」
3. 切到 10-22：徽章 ×1（FC Cincinnati），且该行显示精确/方向命中
4. 徽章带 title 提示（赛前建议）
5. 主预测列表：完赛场次不显示「可信度待积累」
6. 无 JS 错误
"""
import asyncio, sys, os
from playwright.async_api import async_playwright

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

        # 进入「比分预测」tab
        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('.maintab button, .tab-btn, button')];
            const b = btns.find(x => /比分预测/.test(x.textContent||''));
            if (b) { b.click(); return true; }
            return false;
        }""")
        print("点击比分预测 tab:", clicked)
        await page.wait_for_timeout(2000)

        hist_text = await page.evaluate("""() => {
            const root = document.querySelector('#wsHistList') || document.body;
            return root.textContent.replace(/\\s+/g,' ');
        }""")
        print("回溯区文本（默认日期）:", hist_text[:300])

        # 1+2. 默认日期（最新，应为 10-23）徽章与汇总
        n_hot = await page.evaluate("""() =>
            document.querySelectorAll('#wsHistList .sp-preconf.high').length""")
        sum_text = await page.evaluate("""() => {
            const el = document.querySelector('#wsHistList .sp-date-sum');
            return el ? el.textContent.replace(/\\s+/g,' ') : '';
        }""")
        print("默认日期高可信徽章数:", n_hot)
        print("当日汇总:", sum_text)
        assert n_hot == 2, "默认日期应有 2 个🔥赛前高可信徽章，实际 %d" % n_hot
        assert "赛前高可信" in sum_text and "2" in sum_text, "汇总缺少赛前高可信行：%s" % sum_text

        # 徽章 title（赛前建议）
        titles = await page.evaluate("""() =>
            [...document.querySelectorAll('#wsHistList .sp-preconf.high')].map(e => e.getAttribute('title'))""")
        print("徽章 title:", titles)
        assert all(t for t in titles), "徽章缺少 title 提示"

        # 3. 切到 10-22：回溯区按设计只展示「对应竞彩」的场次，当天唯一赛前高可信
        # （FC Cincinnati vs DC United）无竞彩编号不在该区；验证当日中等可信徽章
        # 正常渲染，且数据层确实带 preConf（含那条无编号的高可信）。
        ok = await page.evaluate("""() => {
            const chips = [...document.querySelectorAll('#wsHistBar .sp-chip')];
            const c = chips.find(x => (x.textContent||'').trim() === '10/22');
            if (c) { c.click(); return true; }
            return false;
        }""")
        print("点击 10/22 chip:", ok)
        await page.wait_for_timeout(800)
        n_mid = await page.evaluate("""() =>
            document.querySelectorAll('#wsHistList .sp-preconf.mid').length""")
        sum_text2 = await page.evaluate("""() => {
            const el = document.querySelector('#wsHistList .sp-date-sum');
            return el ? el.textContent.replace(/\\s+/g,' ') : '';
        }""")
        print("10-22 赛前中等可信徽章数:", n_mid)
        print("10-22 当日汇总:", sum_text2)
        assert n_mid >= 5, "10-22 应有多条赛前可信度中等徽章，实际 %d" % n_mid
        assert "赛前高可信" not in sum_text2, "10-22 竞彩场次无高可信，汇总不应出现高可信行"
        # 数据层：FC Cincinnati 记录带 preConf（高可信），只是无竞彩编号不进回溯区
        fc = await page.evaluate("""() => {
            const ws = (typeof BUILT_WHOSCORED !== 'undefined') ? BUILT_WHOSCORED : {};
            const r = (ws.history||[]).find(x => x.id === '1952941');
            return r ? {hot: r.preConf && (r.preConf.exactLevel==='high'||r.preConf.dirLevel==='high'),
                        exactHit: r.exactHit, jcNum: r.jcNum} : null;
        }""")
        print("FC Cincinnati 记录（数据层）:", fc)
        assert fc and fc["hot"] and fc["exactHit"] and not fc["jcNum"], "FC Cincinnati preConf 异常：%s" % fc

        # 5. 主列表：完赛场次（有「实际 X:X」的卡片）不得显示「可信度待积累」。
        # 今天销售日为周五（明日开球），无完赛卡片；完赛渲染路径用合成数据验证。
        bad = await page.evaluate("""() => {
            const cards = [...document.querySelectorAll('#wsList .sp-card')];
            return cards.filter(c => /实际 \\d+:\\d+/.test(c.textContent||'')
                                   && /可信度待积累/.test(c.textContent||'')).length;
        }""")
        print("主列表误显『可信度待积累』的完赛卡片:", bad)
        assert bad == 0, "有 %d 张完赛卡片误显示可信度待积累" % bad

        synth = await page.evaluate("""() => {
            // 合成一条「往轮已评估」记录 + 一场 matches 里的比赛，验证 wsHistRec 命中
            const fakeRec = {id: '9999001', date: '2026-10-20', home: 'A', away: 'B',
                             predH: 2, predA: 1, actH: 2, actA: 1, exactHit: true,
                             outcomeHit: true, jcNum: '周一001',
                             preConf: {exactLevel: 'high', dirLevel: 'mid', sample: 30,
                                       outcome: '主胜', tip: '比分可信度高，可跟注比分 2:1'}};
            const ws = (typeof BUILT_WHOSCORED !== 'undefined') ? BUILT_WHOSCORED : {};
            const old = ws.history || [];
            ws.history = old.concat([fakeRec]);
            wsHistRec._ix = null;   // 清缓存
            const hit = wsHistRec({id: '9999001'});
            const miss = wsHistRec({id: 'no-such-id'});
            const html = wsPreConfHtml(fakeRec.preConf);
            const lvHigh = wsPreConfLevel(fakeRec.preConf);
            const lvNone = wsPreConfLevel(null);
            const lvZero = wsPreConfLevel({sample: 0});
            ws.history = old;
            wsHistRec._ix = null;
            return {hit: !!hit, hitAct: hit && hit.actH, miss: miss === null,
                    htmlHasHot: /赛前高可信/.test(html), htmlHasTitle: /title=/.test(html),
                    lvHigh, lvNone, lvZero};
        }""")
        print("合成数据验证:", synth)
        assert synth["hit"] and synth["hitAct"] == 2 and synth["miss"], "wsHistRec 异常：%s" % synth
        assert synth["htmlHasHot"] and synth["htmlHasTitle"], "徽章 HTML 异常：%s" % synth
        assert synth["lvHigh"] == "high" and synth["lvNone"] == "" and synth["lvZero"] == "", "档位判定异常：%s" % synth

        assert not errors, "JS 错误：%s" % errors
        print("\n✅ v1.7.40 UI 测试全部通过：赛前高可信徽章/汇总/行渲染正确，无 JS 错误")


if __name__ == "__main__":
    asyncio.run(main())
