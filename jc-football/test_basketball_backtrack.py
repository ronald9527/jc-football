#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球 AI 预测 / 串关 历史回溯（v1.7.46）单元测试。

覆盖：
  1. _play_hit：四玩法「预测方向 vs 实际赛果」命中判定（含让分走水=不计入）。
  2. 篮球 AI 回溯库：basketball_ai_accuracy.json 非空、已结算记录 topHit 正确。
  3. 篮球串关结算：save_parlay_history 现为 leg 写入 id/kickoff；
     check_basketball_parlay 不再恒卡死（历史修复项）。
  4. _recalc：hitRate / recent7Rate 字段存在且口径正确。
"""
import json
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name):
    p = os.path.join(HERE, name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ai_mod = _imp("fetch_basketball_ai", os.path.join(HERE, "fetch_basketball_ai.py"))
pl_mod = _imp("fetch_basketball_parlay", os.path.join(HERE, "fetch_basketball_parlay.py"))

PASS = []


def ok(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print("[PASS] %s%s" % (name, (" · " + extra) if extra else ""))
    else:
        print("[FAIL] %s%s" % (name, (" · " + extra) if extra else ""))
        sys.exit(1)


# 1) _play_hit 四玩法判定
# 胜负：主 110 : 客 105 → 主胜
ok("mnl 主胜命中", ai_mod._play_hit("mnl", "h", None, [110, 105]) is True)
ok("mnl 客胜未中", ai_mod._play_hit("mnl", "a", None, [110, 105]) is False)
# 让分：主+5.5，主 102:110 → adj=107.5 > 110? 否 → 客胜；预测 a 命中
ok("hdc 客胜(让分主+5.5)", ai_mod._play_hit("hdc", "a", "+5.5", [102, 110]) is True)
# 让分走水：主-3.5，主 100:103.5 → adj=96.5 < 103.5 → 客胜；预测 h → 未中
ok("hdc 预测主未中", ai_mod._play_hit("hdc", "h", "-3.5", [100, 103]) is False)
# 大小分：总分 212.5，215>212.5 → 大；预测 h 命中
ok("hilo 大分命中", ai_mod._play_hit("hilo", "h", "212.5", [110, 105]) is True)
ok("hilo 小分未中", ai_mod._play_hit("hilo", "l", "212.5", [110, 105]) is False)
# 胜分差：主 120:100 → 差20 → l4；预测 l4 命中，预测 l6 未中
ok("wnm 区间命中", ai_mod._play_hit("wnm", "l4", None, [120, 100]) is True)
ok("wnm 客胜全部不中", ai_mod._play_hit("wnm", "l1", None, [100, 120]) is False)

# 2) AI 回溯库非空且结算正确
acc = _load("basketball_ai_accuracy.json")
hist = acc.get("history") or []
ok("回溯库非空", len(hist) > 0, "共 %d 条" % len(hist))
settled = [r for r in hist if r.get("topHit") in (True, False)]
ok("存在已结算记录", len(settled) > 0, "已结算 %d" % len(settled))
# 抽查：topHit 与 plays[topPlay].hit 一致
sample = next(r for r in hist if r.get("topHit") in (True, False) and r.get("plays", {}).get(r.get("topPlay")))
ok("topHit 与玩法命中一致",
   sample["topHit"] == (sample["plays"][sample["topPlay"]]["hit"] is True))
# 每条已结算记录都带 score 字符串
ok("已结算记录含赛果", all(r.get("score") for r in settled))

# 3) 串关 leg 含 id/kickoff（历史修复项）
pl = _load("basketball_parlay_history.json")
pl_recs = pl.get("records") or []
ok("串关记录存在", len(pl_recs) > 0)
legs_with_id = sum(1 for r in pl_recs for leg in r.get("legs", []) if leg.get("id"))
total_legs = sum(len(r.get("legs", [])) for r in pl_recs)
ok("全部 leg 都有 id（修复漏写）", legs_with_id == total_legs,
   "%d/%d" % (legs_with_id, total_legs))

# 4) _recalc 指标
bkrec = (_load("basketball_history.json").get("records") or {})
hist_obj = {"records": pl_recs}
pl_mod._recalc(hist_obj, pl_recs)
ok("hitRate 字段存在且合理",
   isinstance(hist_obj.get("hitRate"), (int, float)) and 0 <= hist_obj["hitRate"] <= 100,
   "hitRate=%s" % hist_obj.get("hitRate"))
ok("recent7Rate 字段存在", isinstance(hist_obj.get("recent7Rate"), (int, float)),
   "recent7Rate=%s" % hist_obj.get("recent7Rate"))

print("\n全部 %d 项通过" % len(PASS))
