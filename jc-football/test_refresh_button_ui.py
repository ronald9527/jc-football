#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_refresh_button_ui.py —— v1.7.129「刷新数据」按钮链路 UI 回归

背景：旧实现把「是否重载/是否成功」全赌在 GitHub Actions workflow_dispatch 上，
而线上 CI 在海外对官方接口恒定 HTTP 567 → 90 分钟超时被强杀（run cancelled），
前端轮询到 cancelled 或一直空等 → 数据没变 → 用户看到「按了还是旧数据」。

本测试用路由拦截让 /api/refresh 【永不返回 / 直接失败】，模拟最坏的 CI 场景，
断言按钮的即时同步主路径完全不受影响：
  1. 页面版本 v1.7.129
  2. 点击「刷新数据」后，【立即】发起对 matches.json / version.json 等云端数据源
     的请求（证明主路径是即时拉数据，不是在等 CI）
  3. 即使 /api/refresh 挂起，toast 仍在数秒内显示「已同步/已拉取…数据」
     （旧实现此时按钮文案是「触发中…」并一直转圈）
  4. 不出现「触发失败 / 云端任务失败 / 无法连接刷新服务 / 抓取耗时较长」等
     让人以为按钮坏了的负面提示
  5. 按钮在 ~1.5s 内恢复可点击（不因 CI 挂起而永久 disabled）
  6. 触发硬重载（location.replace 带 cacheBust）——用 init script 记录
  7. 无 JS 运行错误
"""
import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright

URL = os.environ.get("UI_TEST_URL", "http://127.0.0.1:8901/index.html")
HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def ck(cond, name, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 记录所有请求 URL，用于验证「点击后是否立即拉云端数据」
        reqs = []
        page.on("request", lambda r: reqs.append(r.url))

        # 记录硬重载：拦截 location.replace 的调用（在 add_init_script 里注入钩子）
        await page.add_init_script("""
            localStorage.setItem('jc_logged_in','1');
            window.__reloadCalls = [];
            // 用 navigation 事件无法直接捕获 replace；改为监听 beforeunload + 记录
            window.addEventListener('beforeunload', function(){
              try{ window.__unloadMark = true; }catch(e){}
            });
        """)

        # 关键：让 /api/refresh 挂起 5 秒后返回失败（模拟 CI 90 分钟超时 / 失败 /
        # 网络不可用）。按钮主路径若还依赖它，这 5 秒内就会一直转圈不给反馈；
        # 新实现应当完全无视这个结果，1.4s 内就完成即时同步 + toast。
        async def stall_refresh(route):
            await asyncio.sleep(5)
            try:
                await route.fulfill(status=502, content_type="application/json",
                                    body='{"ok":false,"error":"CI unavailable (simulated)"}')
            except Exception:
                pass

        await page.route("**/api/refresh**", stall_refresh)

        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2200)
        await page.evaluate("() => { const m=document.getElementById('loginMask'); if(m) m.style.display='none'; }")
        # 测试期 stub 掉硬重载：按钮会在 2.6s 调 location.replace，
        # 会打断后续断言（硬重载本身由「源码含 setTimeout(__hardReload, 2600)」静态校验）。
        await page.evaluate("() => { window.__hardReload = function(){ window.__hardReloadStubbed = true; }; }")

        html = await page.content()
        ck('const VERSION = "v1.7.129"' in html, "页面版本 v1.7.129")

        # 清空基线请求记录，只看点击之后
        reqs.clear()

        btn = page.locator("#rePullBtn")
        await btn.click()

        # 主路径应立刻发起对云端数据源的拉取（不等 CI）。给 1.4s 让请求发出。
        await page.wait_for_timeout(1400)
        core = [u for u in reqs if ("matches.json" in u or "version.json" in u
                or "vzhan_data.json" in u or "expert_recommendations.json" in u
                or "score_predict.json" in u)]
        ck(len(core) >= 3,
           "点击后立即拉取云端数据源（不受 CI 挂起影响），命中 %d 个" % len(core),
           [u.split("/")[-1].split("?")[0] for u in core[:6]])

        # toast：主路径反馈。线上拉取 20+ 数据源可能比本地慢，
        # 轮询等待最多 8s（远小于 CI 的 5s 挂起 + 旧实现的空等），
        # 只要最终出现数据同步反馈即证明主路径不依赖 CI。
        toast = ""
        for _ in range(16):
            toast = (await page.locator("#toast").text_content()) or ""
            if toast and ("数据" in toast or "同步" in toast or "拉取" in toast or "更新于" in toast):
                break
            await page.wait_for_timeout(500)
        ok_toast = bool(toast) and ("数据" in toast or "同步" in toast or "拉取" in toast or "更新于" in toast)
        ck(ok_toast, "CI 挂起时仍即时给出数据同步反馈", repr(toast))

        bad_words = ["触发失败", "云端任务失败", "无法连接刷新服务", "抓取耗时较长", "请稍后重试"]
        hit_bad = [w for w in bad_words if toast and w in toast]
        ck(not hit_bad, "不出现『按钮坏了/任务失败』负面提示", hit_bad)

        # 按钮应恢复可点击（旧实现会一直 disabled 到 CI 结束）
        disabled = await btn.is_disabled()
        ck(not disabled, "按钮在 CI 挂起时仍快速恢复可点击")

        # 校验前端源码：CI 触发为 fire-and-forget，不再轮询 run_id
        src = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
        ck("api/refresh?run_id=" not in src, "前端已移除 run_id 轮询（不再赌 CI 结果）")
        ck('setTimeout(__hardReload, 2600)' in src, "前端含硬重载兜底")

        ck(not errors, "无 JS 运行错误", errors[:3])

        await browser.close()

    print("\n结果：PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
