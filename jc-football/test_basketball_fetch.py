#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球板块回归测试（v1.7.29）。

核心断言：
  1. fetch_calc 返回结构完整（四玩法赔率、编号、中文队名）
  2. fetch_bonus 赛果回填行为正确（已开奖返回比分 / 未开奖 None）
  3. **数据隔离**：跑篮球 main() 前后，足球核心文件
     （matches.json / history.json / rules_config.json / whoscored_data.json）
     的 sha256 完全不变——证明篮球板块对足球数据零影响
"""

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


def main():
    import fetch_basketball as bk

    # ---- 1. 赛程结构 ----
    matches = bk.fetch_calc()
    assert matches, "在售赛程为空"
    m = matches[0]
    for k in ("id", "num", "week", "date", "kickoff", "time",
              "league", "home", "away", "mnl", "hdc", "hilo"):
        assert k in m, "缺字段 %s" % k
    assert any(x.get("num", "").endswith("301") or "301" in x.get("num", "")
               for x in matches), "未找到 301 编号（篮球编号空间错误？）"
    assert m["home"] and m["away"], "队名为空（应为竞彩官方中文名）"
    n_odds = sum(1 for x in matches if x.get("hasOdds"))
    print("1. 赛程结构 OK：%d 场（%d 场含赔率），编号 %s~%s，联赛 %s"
          % (len(matches), n_odds, matches[0]["num"], matches[-1]["num"],
             m["league"]))

    # ---- 2. 赛果回填 ----
    r = bk.fetch_bonus("2042092")   # 已开奖（西篮 巴伦西亚 93:116 穆尔西亚天主大学）
    assert r == {"home": 93, "away": 116}, "已开赛场次比分解析错误: %s" % r
    r2 = bk.fetch_bonus("2042132")  # 未开赛
    assert r2 is None, "未开赛场次应返回 None: %s" % r2
    assert bk._line_num("+1.5") == 1.5 and bk._line_num("223.5") == 223.5
    print("2. 赛果回填 OK：已开奖 93:116 / 未开赛 None / 盘口解析正确")

    # ---- 3. 数据隔离（足球文件哈希不变）----
    before = {f: sha(os.path.join(HERE, f)) for f in FOOTBALL_FILES}
    # 在临时目录跑一份 main()，避免污染真实篮球数据
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
            bk.main()
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    after = {f: sha(os.path.join(HERE, f)) for f in FOOTBALL_FILES}
    for f in FOOTBALL_FILES:
        assert before[f] == after[f], "足球文件 %s 被篮球流程改动！" % f
    changed = [f for f in FOOTBALL_FILES if before[f] is not None]
    print("3. 数据隔离 OK：%d 个足球核心文件哈希全程不变" % len(changed))
    print("ALL PASS")


if __name__ == "__main__":
    main()
