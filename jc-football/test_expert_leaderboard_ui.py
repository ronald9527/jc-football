#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_expert_leaderboard_ui.py —— v1.7.128 专家命中率 / 排行榜 UI 回归

断言（手机视口 390×844，默认本地 http://127.0.0.1:8901/index.html）：
  1. 页面版本 v1.7.128
  2. 命中率排行榜默认展开、标题为命中率口径、写明赛后回溯自动重算
  3. 榜单行数与 leaderboard 一致，榜首为 66.7%（不是按推荐分/场次排序）
  4. 榜单标注样本层级与 Wilson 95%CI
  5. 今日推荐【每个】专家（主推 + 同场其他）都带 .exp-rate 徽章
  6. 专家文章流【每条】推荐都带 .exp-rate 徽章
  7. 首页比赛卡里的专家推荐也带徽章
  8. 无 JS 运行错误
"""
import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright

URL = os.environ.get("UI_TEST_URL", "http://127.0.0.1:8901/index.html")
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.environ.get("UI_TEST_SHOT", "/workspace/专家命中率排行榜-页面.png")
PASS, FAIL = [], []


def ck(cond, name, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


async def main():
    ed = json.load(open(os.path.join(HERE, "expert_recommendations.json"), encoding="utf-8"))
    es = json.load(open(os.path.join(HERE, "expert_stats.json"), encoding="utf-8"))
    main_n = len(ed.get("recommendations", []))
    oth_n = sum(len(r.get("otherExperts", [])) for r in ed.get("recommendations", []))
    art_n = sum(len(a.get("recommendations", [])) for a in ed.get("articles", []))
    board_n = len(es.get("leaderboard", []))
    print("数据：主推 %d / 同场其他 %d / 文章流 %d / 榜 %d 行" %
          (main_n, oth_n, art_n, board_n))

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
        ck('const VERSION = "v1.7.128"' in html, "页面版本 v1.7.128")

        await page.click('button[data-tab="expert"]')
        await page.wait_for_timeout(600)

        body_disp = await page.eval_on_selector("#expertRankBody", "e=>getComputedStyle(e).display")
        ck(body_disp != "none", "命中率排行榜默认展开", body_disp)
        rank_txt = await page.inner_text("#expertRankWrap")
        ck("命中率排行榜" in rank_txt and "按命中率排序" in rank_txt, "榜单标题改为命中率口径")
        ck("赛果回溯后自动重算" in rank_txt, "榜单写明赛后回溯自动重算")
        ck(("样本不足" in rank_txt or "样本偏少" in rank_txt), "榜单标注样本层级")
        ck("95%CI" in rank_txt, "榜单显示 Wilson 95%CI")
        rows = await page.locator("#expertRankList > div").count()
        ck(rows - 1 == board_n, "榜单行数与 leaderboard 一致", "%d 行" % (rows - 1))
        first_rate = await page.locator("#expertRankList > div").nth(1).inner_text()
        ck("66.7%" in first_rate, "榜首为 66.7%（非按推荐分排序）",
           first_rate[:70].replace("\n", " "))

        today_badges = await page.locator("#expertList .exp-rate").count()
        ck(today_badges >= main_n + oth_n,
           "今日推荐每个专家都带命中率徽章（%d ≥ %d）" % (today_badges, main_n + oth_n),
           "主推%d + 同场其他%d" % (main_n, oth_n))
        today_txt = await page.inner_text("#expertList")
        ck("命中率" in today_txt or "暂无往绩" in today_txt,
           "徽章文案为命中率或如实「暂无往绩」")

        await page.screenshot(path=SHOT, full_page=False)

        card_ok = await page.evaluate("""() => {
          const recs = (state.expert && state.expert.recommendations) || [];
          const m = (state.data || []).find(x => recs.some(r => r.num && r.num === x.num));
          if(!m) return false;
          try { return renderExpertForMatch(m).indexOf('exp-rate') >= 0; }
          catch(e) { return false; }
        }""")
        ck(card_ok, "首页比赛卡专家推荐也带命中率徽章")

        await page.click('button[data-expert-view="articles"]')
        await page.wait_for_timeout(700)
        # 文章流按发布时间分页，必须翻完所有页再统计，否则会误判「缺徽章」
        art_badges = await page.locator("#expertArticlesList .exp-rate").count()
        visited = 1
        for i in range(2, 10):
            btns = page.locator("#expertArticlesPager button", has_text=str(i))
            if await btns.count() == 0:
                break
            await btns.first.click()
            await page.wait_for_timeout(300)
            art_badges += await page.locator("#expertArticlesList .exp-rate").count()
            visited += 1
        ck(art_badges >= art_n,
           "文章流每条推荐都带命中率徽章（%d ≥ %d，共 %d 页）" %
           (art_badges, art_n, visited))

        ck(not errors, "无 JS 运行错误", errors[:2])
        await browser.close()
    print("\n=== PASS %d / FAIL %d ===" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
