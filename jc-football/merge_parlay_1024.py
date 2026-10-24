# -*- coding: utf-8 -*-
"""一次性合并（2026-10-24）：恢复 10-23 丢失的串关历史记录。

背景（用户报「昨天的串关推荐历史记录又跑哪里去了」）：
  本地 parlay_history.json 10-23 09:07 生成了 10-23 的 3 组推荐，但未能
  在线上条数追平前完成合并部署；之后 ensure_history 对账时两边均为 69 条，
  按「条数相同、线上时间戳更晚」规则用线上版本覆盖本地，10-23 的 3 组
  （本地独有）被当旧数据吞掉。线上 Pages 现存 69 条只到 10-22 + 10-24。

本脚本按记录签名 (date, legs签名) 取本地 ∪ 线上并集（72 条），跑
check_parlay_history 结算，并同步 matches.json 的 parlayHistory
（以线上 matches.json 的新赛程为基底，防止赛程回退）。
"""
import io, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_daily as fd

LOCAL_PARLAY = os.path.join(HERE, "parlay_history.json")
ONLINE_PARLAY = "/tmp/ph_online.json"      # Pages 静态副本（已下载）
LOCAL_MATCHES = os.path.join(HERE, "matches.json")
ONLINE_MATCHES = "/tmp/m_online.json"      # Pages matches.json（已下载）


def _leg_sig(legs):
    """与 fetch_daily.save_parlay_history._leg_sig 同口径的记录签名。"""
    return tuple(sorted((l.get("num"), l.get("playKey"), l.get("dir"))
                        for l in (legs or [])))


def _rec_sig(rec):
    return (rec.get("date", ""), _leg_sig(rec.get("legs")))


def main():
    loc = json.load(open(LOCAL_PARLAY, encoding="utf-8"))
    on = json.load(open(ONLINE_PARLAY, encoding="utf-8"))
    l_recs = loc.get("records") or []
    o_recs = on.get("records") or []
    print("本地 %d 条 | 线上 %d 条" % (len(l_recs), len(o_recs)))

    # ---- 签名并集：任一方独有的记录都保留；同签名取 savedAt 更晚的版本 ----
    by_sig = {}
    for r in o_recs:
        by_sig[_rec_sig(r)] = r
    added_from_local = []
    for r in l_recs:
        s = _rec_sig(r)
        if s not in by_sig:
            by_sig[s] = r
            added_from_local.append(r)
        elif (r.get("savedAt") or "") > (by_sig[s].get("savedAt") or ""):
            by_sig[s] = r
    merged = sorted(by_sig.values(),
                    key=lambda r: (r.get("date") or "", r.get("savedAt") or ""))
    print("并集 %d 条；本地独有（线上缺失，本次恢复）：%d 条"
          % (len(merged), len(added_from_local)))
    for r in added_from_local:
        print("   + %s %s savedAt=%s legs=%s"
              % (r.get("date"), r.get("type"), r.get("savedAt"),
                 [(l.get("num"), l.get("home"), l.get("away")) for l in r.get("legs") or []]))

    # ---- 写本地 parlay_history.json（保留顶层其他键，updatedAt 取较晚者） ----
    shutil.copy2(LOCAL_PARLAY, LOCAL_PARLAY + ".bak_before_1024_merge")
    out = dict(loc)
    out["records"] = merged
    out["updatedAt"] = max(loc.get("updatedAt") or "", on.get("updatedAt") or "")
    with io.open(LOCAL_PARLAY, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("已写本地 parlay_history.json（备份 .bak_before_1024_merge）")

    # ---- 结算：check_parlay_history 读 history.json 赛果回填 hit/miss/pending ----
    stats = fd.check_parlay_history()
    print("结算后：total=%s hit=%s miss=%s pending=%s hitRate=%s"
          % (stats.get("total"), stats.get("hit"), stats.get("miss"),
             stats.get("pending"), stats.get("hitRate")))

    # ---- matches.json：以线上新赛程为基底，替换 parlayHistory ----
    shutil.copy2(LOCAL_MATCHES, LOCAL_MATCHES + ".bak_before_1024_merge")
    m = json.load(open(ONLINE_MATCHES, encoding="utf-8"))
    old_ph = m.get("parlayHistory") or {}
    m["parlayHistory"] = stats
    print("matches.json：parlayHistory %d -> %d 条（赛程等字段保留线上 10-24 版，updateTime=%s）"
          % (len(old_ph.get("records") or []), len(stats.get("records") or []),
             m.get("updateTime")))
    with io.open(LOCAL_MATCHES, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    print("已写本地 matches.json（备份 .bak_before_1024_merge）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
