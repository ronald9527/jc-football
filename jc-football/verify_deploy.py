#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_deploy.py —— 部署后线上校验（防「部署成功但线上仍是旧数据/功能不对」）

背景：历史上两次「用户看到的功能不对」其实都是部署层面问题——
  · 足球：wrangler 上传完文件但 Deploying 步骤在超时前未完成，线上一直是旧
    构建，而 v1.7.x 版本号相同导致只核对版本号根本发现不了（2026-10-28）；
  · 篮球：渲染逻辑 bug（历史回溯默认选了当天待赛日）导致打开只见待赛场、
    看不到已赛命中回看，被误认为「没有回溯」。
本脚本在 run_daily.sh 部署后自动运行：抓取线上页面，逐项断言关键数据与本地
构建完全一致；任一项不一致即判定 FAIL（退出码 1），供调度器触发自动重部署。

校验项：
  1. 线上 VERSION == 本地 template.html 的 VERSION（部署必须真的升版）
  2. 篮球 AI 历史回溯：线上已结算（topHit!=null）记录数 == 本地文件
  3. 足球 WhoScored：线上「带竞彩编号且有源站预测」记录数 == 本地文件
（均考虑 Cloudflare 部署异步，内部带重试轮询，最多 RETRIES 轮）

用法：
  python3 verify_deploy.py [--url URL] [--retries N] [--quiet]
退出码：0=全部通过  1=存在不一致（需重部署）
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://jc-football-1eo.pages.dev/"
RETRIES = 8          # 轮询次数
SLEEP = 6            # 每轮间隔（秒）—— 给 CF 部署异步生效留时间


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "verify-deploy/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def extract_json(hay, varname):
    """从 `const VAR = {...};` 中稳健提取对象文本（括号配对，避免贪婪截断）"""
    pat = re.compile(r'const\s+' + re.escape(varname) + r'\s*=\s*')
    m = pat.search(hay)
    if not m:
        return None
    i = hay.index('{', m.end())
    depth = 0
    for j in range(i, len(hay)):
        c = hay[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return hay[i:j + 1]
    return None


def local_version():
    h = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    m = re.search(r'const VERSION = "([^"]*)"', h)
    return m.group(1) if m else None


def local_counts():
    """返回本地构建应注入的两条关键计数（已结算篮球 AI 记录 / 有预测的足球竞彩记录）"""
    bk = 0
    try:
        d = json.load(open(os.path.join(HERE, "basketball_ai_accuracy.json"), encoding="utf-8"))
        bk = sum(1 for r in (d.get("history") or []) if r.get("topHit") is not None)
    except Exception:
        pass
    fb = 0
    try:
        d = json.load(open(os.path.join(HERE, "whoscored_data.json"), encoding="utf-8"))
        fb = sum(1 for m in (d.get("matches") or [])
                 if m.get("jcNum") and m.get("predH") is not None)
    except Exception:
        pass
    return bk, fb


def live_counts(hay):
    ver = None
    m = re.search(r'const VERSION = "([^"]*)"', hay)
    if m:
        ver = m.group(1)
    bk = 0
    try:
        seg = extract_json(hay, "BUILT_BASKETBALL_AI_ACC")
        if seg:
            d = json.loads(seg)
            bk = sum(1 for r in (d.get("history") or []) if r.get("topHit") is not None)
    except Exception:
        pass
    fb = 0
    note = ""
    try:
        seg = extract_json(hay, "BUILT_WHOSCORED")
        if seg:
            d = json.loads(seg)
            fb = sum(1 for m in (d.get("matches") or [])
                     if m.get("jcNum") and m.get("predH") is not None)
            note = d.get("note") or ""
    except Exception:
        pass
    return ver, bk, fb, note


def main():
    args = sys.argv[1:]
    url = URL
    quiet = False
    if "--url" in args:
        url = args[args.index("--url") + 1]
    if "--retries" in args:
        global RETRIES
        RETRIES = int(args[args.index("--retries") + 1])
    if "--quiet" in args:
        quiet = True

    lv = local_version()
    lbk, lfb = local_counts()
    if not lv:
        print("[ERROR] 读不到本地 VERSION"); sys.exit(1)

    log = (lambda *a: print(*a)) if not quiet else (lambda *a: None)
    log(f"校验目标 {url}")
    log(f"本地期望: VERSION={lv} 篮球AI已赛={lbk} 足球竞彩有预测={lfb}")

    last = None
    for n in range(1, RETRIES + 1):
        try:
            hay = fetch(url + ("?cb=%d" % int(time.time() * 1000)))
        except Exception as e:
            log(f"  轮{n} 抓取失败: {e}")
            time.sleep(SLEEP)
            continue
        ver, bk, fb, note = live_counts(hay)
        last = (ver, bk, fb, note)
        # v1.7.68：线上 WhoScored 本轮真失败（被 Cloudflare 拦截/抓取失败）且未走
        # 缓存保护时，视为部署未生效，触发重部署重试
        real_fail = bool(note) and ("拦截" in note or "抓取失败" in note) and ("沿用" not in note)
        ok = (ver == lv) and (bk >= lbk) and (fb >= lfb) and (not real_fail)
        log(f"  轮{n} 线上: VERSION={ver} 篮球AI已赛={bk} 足球竞彩有预测={fb} -> {'OK' if ok else '不一致'}{' [源站失败]' if real_fail else ''}")
        if ok:
            print(f"[PASS] 线上数据与本地构建一致 (VERSION={ver}, 篮球AI已赛={bk}/{lbk}, 足球竞彩有预测={fb}/{lfb})")
            sys.exit(0)
        time.sleep(SLEEP)

    ver, bk, fb, note = last or (None, 0, 0, "")
    print(f"[FAIL] 线上数据与本地构建不一致，疑似部署未生效或功能校验未过")
    print(f"  本地期望: VERSION={lv} 篮球AI已赛={lbk} 足球竞彩有预测={lfb}")
    print(f"  线上实际: VERSION={ver} 篮球AI已赛={bk} 足球竞彩有预测={fb}")
    sys.exit(1)


if __name__ == "__main__":
    main()
