#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球板块回归测试（v1.7.119 改为完全离线）。

核心断言：
  1. 篮球赛程结构完整（四玩法赔率、编号、中文队名、双源标记）
  2. fetch_bonus 赛果回填行为正确（已开奖返回主客比分 / 未开奖 None）
  3. 500.com 备用源解析结果结构可用（无需联网）
  4. **数据隔离**：跑篮球 main() 前后，足球核心文件的 sha256 完全不变

v1.7.119 说明：原测试直接打官方 webapi + 开奖接口，海外 CI 上 HTTP 567/403
会让回归测试随机失败。现改用本地数据夹具 + mock 网络层，测试只验证代码逻辑，
网络可达性由 fetch_basketball.py 的双源降级在流水线中保证。
"""

import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FOOTBALL_FILES = ["matches.json", "history.json", "rules_config.json",
                  "whoscored_data.json", "parlay_history.json"]
BK_FILES = ["basketball.json", "basketball_history.json"]


def sha(p):
    try:
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    import fetch_basketball as bk

    bk_data = _load("basketball.json")
    hist = _load("basketball_history.json")
    matches = bk_data.get("matches") or []
    records = hist.get("records") or {}

    # ---- 1. 赛程结构（官方+500 合并后的真实产物）----
    assert matches, "篮球赛程为空"
    m = matches[0]
    for k in ("id", "num", "week", "date", "kickoff", "time",
              "league", "home", "away", "mnl", "hdc", "hilo"):
        assert k in m, "缺字段 %s" % k
    assert any("301" in (x.get("num") or "") for x in matches), \
        "未找到 301 编号（篮球编号空间错误？）"
    assert m["home"] and m["away"], "队名为空（应为中文名）"
    assert bk_data.get("dataDate"), "缺 dataDate（新鲜度门禁依赖）"
    assert bk_data.get("source"), "缺 source（备用源提示依赖）"
    n_odds = sum(1 for x in matches if x.get("hasOdds"))
    print("1. 赛程结构 OK：%d 场（%d 场含赔率），批次 %s，源 %s"
          % (len(matches), n_odds, bk_data.get("dataDate"), bk_data.get("source")))

    # ---- 2. 赛果回填（mock 网络，避免海外 WAF 拦截导致随机失败）----
    scored = next((r for r in records.values()
                   if r.get("score") and r.get("id")), None)
    assert scored, "本地篮球档案没有已回填赛果的记录"

    def fake_fetch(url, must_contain=None, tries=8):
        import re
        g = re.search(r"matchId=(\d+)", url)
        sc = (records.get(g.group(1)) or {}).get("score") if g else None
        if sc:
            return {"success": True, "value": {"matchResult": {
                "poolStatus": "Payout",
                "sectionsNo999": "%d:%d" % (sc[0], sc[1])}}}
        return {"success": True, "value": {"matchResult": {"poolStatus": "Selling"}}}

    orig_fetch = bk.fetch
    bk.fetch = fake_fetch
    try:
        r = bk.fetch_bonus(scored["id"])
        assert r == {"home": scored["score"][0], "away": scored["score"][1]}, \
            "已开赛场次比分与档案不一致: %s vs %s" % (r, scored["score"])
        future = next((x for x in records.values()
                       if not x.get("score") and x.get("id")), None)
        if future:
            assert bk.fetch_bonus(future["id"]) is None, "未开奖应返回 None"
    finally:
        bk.fetch = orig_fetch
    assert bk._line_num("+1.5") == 1.5 and bk._line_num("223.5") == 223.5
    print("2. 赛果回填 OK：%s %d:%d / 未开奖返回 None / 盘口解析正确"
          % (scored.get("num"), scored["score"][0], scored["score"][1]))

    # ---- 3. 500 备用源离线解析（用仓库内已抓页面的等价结构断言）----
    import fetch_basketball_500 as fb500
    html = ('<tr class="bet-tb-tr" data-homesxname="火箭" data-awaysxname="雷霆" '
            'data-matchdate="2026-10-31" data-matchtime="08:00" data-rangfen="-4.5" '
            'data-yszf="219.5" data-simpleleague="美职篮" data-id="9001" '
            'data-matchnum="周六316" data-processdate="2026-10-31">'
            '<td class="td td-team"><i class="team-vs team-bf">'
            '<a class="score" href="#">98:119</a></i></td>'
            '<td class="td td-pei"><span>1.90</span> <span>1.80</span></td></tr>'
            '<tr class="bet-more-wrap hide"><td><table><tbody>'
            '<tr><th>客胜</th><td><p data-sp="4.50"></p></td><td><p data-sp="4.80"></p></td>'
            '<td><p data-sp="8.40"></p></td><td><p data-sp="13.0"></p></td>'
            '<td><p data-sp="22.0"></p></td><td><p data-sp="28.0"></p></td></tr>'
            '<tr><th>主胜</th><td><p data-sp="4.25"></p></td><td><p data-sp="4.50"></p></td>'
            '<td><p data-sp="7.70"></p></td><td><p data-sp="11.0"></p></td>'
            '<td><p data-sp="18.0"></p></td><td><p data-sp="19.0"></p></td></tr>'
            '</tbody></table></td></tr>')
    parsed = fb500.parse_jclq(html, "2026-10-31")
    assert len(parsed) == 1, "500 解析应得 1 场"
    pm = parsed[0]
    assert pm["score"] == [98, 119], "500 比分主客方向错误（应为主:客）"
    assert pm["hdc"]["goalLine"] == "-4.5" and pm["hdc"]["h"] == 1.90
    assert pm["hilo"]["goalLine"] == "219.5"
    assert pm["mnl"] and pm["wnm"].get("l1") == 4.50 and pm["wnm"].get("w1") == 4.25
    print("3. 500 备用源离线解析 OK：主客比分/让分/总分/胜负反推/胜分差 12 档")

    # ---- 4. 数据隔离（足球文件哈希不变）----
    before = {f: sha(os.path.join(HERE, f)) for f in FOOTBALL_FILES}
    tmp = tempfile.mkdtemp(prefix="bk_test_")
    try:
        for f in BK_FILES:
            src = os.path.join(HERE, f)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(tmp, f))
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            bk.BK_PATH = os.path.join(tmp, "basketball.json")
            bk.BKH_PATH = os.path.join(tmp, "basketball_history.json")
            # 双源全部 mock：官方失败 → 500 接管，验证降级链路也不碰足球
            bk.fetch = lambda url, must_contain=None, tries=8: {
                "success": False, "errorMessage": "offline fixture"}
            bk.fetch_500_recent = lambda days, today=None, tries=3: (list(matches), [])
            bk.main()
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    after = {f: sha(os.path.join(HERE, f)) for f in FOOTBALL_FILES}
    for f in FOOTBALL_FILES:
        assert before[f] == after[f], "足球文件 %s 被篮球流程改动！" % f
    print("4. 数据隔离 OK：%d 个足球核心文件哈希全程不变（含 500 降级链路）"
          % len([f for f in FOOTBALL_FILES if before[f] is not None]))
    print("ALL PASS")


if __name__ == "__main__":
    main()
