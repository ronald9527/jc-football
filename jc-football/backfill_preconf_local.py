#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_preconf_local.py —— v1.7.40 上线时的本地数据回填（一次性）

把「赛前高可信」回溯标记所需的数据在本地备好，供 build_panel 构建页面：

  1) whoscored_history.json：按 preconf_backfill.json 回填表给 30 条
     「上线前已评估」的记录补 preConf（与 fetch_whoscored._apply_preconf_backfill
     同一逻辑，本地先跑一遍，让构建出的页面立即有徽章）；
  2) whoscored_data.json：history 数组同步为回填后的 records；并按新守卫
     剥离「已评估比赛」在 matches 上的 conf（事后诸葛数据清理——新代码
     不再给完赛场次算 conf，旧快照里的要擦掉）。

任务侧无需此脚本：回填表随自举代码包下发，任务每轮加载时自愈。
"""
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch_whoscored import _apply_preconf_backfill, BJ_OFFSET

HIST = os.path.join(HERE, "whoscored_history.json")
DATA = os.path.join(HERE, "whoscored_data.json")


def main():
    hist = json.load(open(HIST, encoding="utf-8"))
    records = hist.get("records") or {}
    n0 = len(records)
    n = _apply_preconf_backfill(records)
    print("whoscored_history.json：%d 条记录，本次回填 preConf %d 条" % (n0, n))
    if n:
        hist["records"] = records
        now = datetime.utcnow() + timedelta(seconds=BJ_OFFSET)
        hist["updatedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
        with open(HIST, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
        print("  已写回（updatedAt=%s）" % hist["updatedAt"])

    # whoscored_data.json：history 同步 + 已评估比赛剥离 conf
    data = json.load(open(DATA, encoding="utf-8"))
    his = sorted(records.values(), key=lambda x: (x.get("date") or ""), reverse=True)
    old_hist = data.get("history") or []
    data["history"] = his[:800]
    stripped = 0
    eva_ids = {r["id"] for r in records.values() if r.get("actH") is not None}
    for m in data.get("matches") or []:
        if m.get("id") in eva_ids and m.get("conf"):
            m.pop("conf", None)
            stripped += 1
    n_pre = sum(1 for r in data["history"] if r.get("preConf"))
    n_hot = sum(1 for r in data["history"]
                if (r.get("preConf") or {}).get("exactLevel") == "high"
                or (r.get("preConf") or {}).get("dirLevel") == "high")
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("whoscored_data.json：history %d -> %d 条（带 preConf %d，其中赛前高可信 %d）；"
          "剥离已评估比赛残留 conf %d 条" % (len(old_hist), len(data["history"]), n_pre, n_hot, stripped))
    hot = [(r["date"], r["home"], r["away"], r["predH"], r["predA"], r["actH"], r["actA"],
            r["exactHit"], r["outcomeHit"]) for r in data["history"]
           if (r.get("preConf") or {}).get("exactLevel") == "high"
           or (r.get("preConf") or {}).get("dirLevel") == "high"]
    for d, hm, aw, ph, pa, ah, aa, eh, oh in sorted(hot):
        print("  🔥 %s %s vs %s 预测 %s:%s 实际 %s:%s 精确%s 方向%s"
              % (d, hm, aw, ph, pa, ah, aa, "命中" if eh else "未中", "命中" if oh else "未中"))
    print("完成。")


if __name__ == "__main__":
    main()
