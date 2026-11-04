#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比分预测（WhoScored 人工预测）赛后结果「全量离线回溯」——v1.7.111

背景：无人值守流水线跑在海外，WhoScored /livescores 赛果页常被 Cloudflare
软限流，赛果回填会静默饿死，导致 whoscored_history.json 的 pending（有赛前
预测、无赛果）不断堆积。本脚本用**本地已有的权威赛果源**做一次性全量补齐，
不依赖任何外网：

  赛果源（按优先级）：
  1. history.json         竞彩官方赛果 754 场（中文队名 + 竞彩编号），按
                          「竞彩编号 + 日期±2天」匹配（README v1.7.23 口径；
                          该窗口内编号不重复，实测 0 多义）
  2. whoscored_data.history / score_predict.history / score_predict.matches
     / fd_data.json        英文队名赛果池，按「队名归一 + 日期」匹配，
                          并对 "Atalanta" vs "Atalanta BC" 这类别名差异做
                          包含式兜底（要求双方队名互相包含 + 日期±2天 + 赛果唯一）

匹配到赛果的 pending 按 fetch_whoscored.py 既有规则转成 records：
  exactHit   = predH==actH and predA==actA
  outcomeHit = _outcome(predH,predA) == _outcome(actH,actA)
  preConf    = pending.conf 的副本（赛前可信度快照，赛后逐场可验证）
并同步 whoscored_data.json 的 history（页面「WhoScored 赛后回溯」数据源）、
historyDateList、reliable 命中率统计。

幂等：records 里已有赛果的记录不覆盖；可重复执行；未来日期（未开赛）保留
pending。用法：
  python3 backfill_ws_results.py --dry-run   # 只预览不写盘
  python3 backfill_ws_results.py             # 实际回填
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DRY = "--dry-run" in sys.argv

HIST_PATH = os.path.join(HERE, "whoscored_history.json")
WS_PATH = os.path.join(HERE, "whoscored_data.json")
JC_PATH = os.path.join(HERE, "history.json")
SP_PATH = os.path.join(HERE, "score_predict.json")
FD_PATH = os.path.join(HERE, "fd_data.json")

BJ_NOW = datetime.utcnow() + timedelta(hours=8)
TODAY = BJ_NOW.date()
STAMP = BJ_NOW.strftime("%Y-%m-%d %H:%M:%S")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save(p, obj):
    """与 fetch_whoscored.py::_save 完全一致的落盘格式（indent=1，原子替换）。"""
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def norm(s):
    """队名归一：去空格/标点、小写，仅留字母数字，用于英文队名精确匹配。"""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def d(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def within(a, b, tol):
    da, db = d(a), d(b)
    return bool(da and db and abs((da - db).days) <= tol)


def outcome(h, a):
    return "H" if h > a else ("D" if h == a else "A")


# ---------------- 1) 构建赛果索引 ----------------
def build_indexes(ws):
    # (a) 竞彩官方赛果：num -> [(date, league, actH, actA, home_zh, away_zh)]
    jc = defaultdict(list)
    jc_total = 0
    for r in _load(JC_PATH).get("records", []):
        res = r.get("result")
        if not (r.get("num") and isinstance(res, dict)
                and res.get("home") is not None and res.get("away") is not None):
            continue
        jc[r["num"]].append({
            "date": r.get("date"), "league": r.get("league"),
            "actH": res["home"], "actA": res["away"],
            "home": r.get("home"), "away": r.get("away"),
        })
        jc_total += 1

    # (b) 英文队名赛果池：(normHome, normAway, date) -> {(actH, actA)}
    eng = defaultdict(set)
    eng_total = 0
    for r in ws.get("history", []):
        if r.get("actH") is not None and r.get("actA") is not None:
            eng[(norm(r.get("home")), norm(r.get("away")), r.get("date"))].add(
                (r["actH"], r["actA"]))
            eng_total += 1
    for r in _load(SP_PATH).get("history", []):
        if r.get("actH") is not None and r.get("actA") is not None:
            eng[(norm(r.get("home")), norm(r.get("away")), r.get("date"))].add(
                (r["actH"], r["actA"]))
            eng_total += 1
    for r in _load(SP_PATH).get("matches", []):
        # 数据驱动侧本轮已评估场次（英文 FD 队名 + actH/actA，UTC 日期）
        if r.get("actH") is not None and r.get("actA") is not None:
            eng[(norm(r.get("home")), norm(r.get("away")), (r.get("utcDate") or "")[:10])].add(
                (r["actH"], r["actA"]))
            eng_total += 1
    for r in _load(FD_PATH).get("matches", []):
        sc = r.get("score")
        if r.get("status") == "FINISHED" and isinstance(sc, dict) \
                and sc.get("home") is not None and sc.get("away") is not None:
            eng[(norm(r.get("home")), norm(r.get("away")), (r.get("utcDate") or "")[:10])].add(
                (sc["home"], sc["away"]))
            eng_total += 1

    # 包含式兜底索引：(normH, normA) -> [(date, actH, actA)]
    loose = defaultdict(list)
    for (nh, na, ds), scores in eng.items():
        for ah, aa in scores:
            loose[(nh, na)].append((ds, ah, aa))
    return jc, jc_total, eng, eng_total, loose


def loose_result(v, loose):
    """队名别名兜底：Atalanta ↔ Atalanta BC。要求双方互相包含、日期±2天、赛果唯一。"""
    nh, na = norm(v.get("home")), norm(v.get("away"))
    dt = d(v.get("date"))
    if not dt or len(nh) < 4 or len(na) < 4:
        return None
    scores = set()
    for (h, a), items in loose.items():
        if not (h and a and (h in nh or nh in h) and (a in na or na in a)):
            continue
        for ds, ah, aa in items:
            dd = d(ds)
            if dd and abs((dd - dt).days) <= 2:
                scores.add((ah, aa))
    return next(iter(scores)) if len(scores) == 1 else None


def find_result(v, jc, eng, loose):
    """返回 (actH, actA, source) 或 (None, None, 原因)。宁缺毋滥，不瞎配。"""
    dt = v.get("date")
    if not dt or not d(dt):
        return None, None, "无有效日期"
    if d(dt) > TODAY:
        return None, None, "未开赛(未来)"

    # 优先：竞彩编号 + 日期±2天（0 多义；联赛一致额外加分但不作硬门槛，
    # 因为队名/联赛存在同队异写，编号窗口内唯一即足够可靠）
    num = v.get("jcNum")
    if num:
        cands = [c for c in jc.get(num, []) if within(dt, c["date"], 2)]
        scores = {(c["actH"], c["actA"]) for c in cands}
        if len(scores) == 1:
            ah, aa = next(iter(scores))
            return ah, aa, "竞彩编号+日期"
        if len(scores) > 1:
            return None, None, "竞彩编号多义(跳过)"

    # 兜底：英文队名归一 + 日期（精确，再 ±1 天）
    key = (norm(v.get("home")), norm(v.get("away")), dt)
    for k, tol in ((key, 0),):
        sc = eng.get(k, set())
        if len(sc) == 1:
            ah, aa = next(iter(sc))
            return ah, aa, "英文队名+日期"
        if len(sc) > 1:
            return None, None, "英文队名赛果冲突(跳过)"
    # ±1 天兜底（唯一才采用）
    dd = d(dt)
    near = set()
    for delta in (-1, 1):
        k2 = (key[0], key[1], (dd + timedelta(days=delta)).isoformat())
        near |= eng.get(k2, set())
    if len(near) == 1:
        ah, aa = next(iter(near))
        return ah, aa, "英文队名+日期±1天"
    if len(near) > 1:
        return None, None, "英文队名±1天赛果冲突(跳过)"
    # 队名别名兜底（Atalanta ↔ Atalanta BC），要求赛果唯一
    lr = loose_result(v, loose)
    if lr:
        return lr[0], lr[1], "队名别名兜底"
    return None, None, "本地无赛果"


# ---------------- 2) 重算 reliable（复刻 fetch_whoscored.py 口径） ----------------
def recompute_reliable(eva):
    n = len(eva)
    overall = {"n": n}
    if n:
        overall["exactHit"] = round(sum(1 for x in eva if x.get("exactHit")) / n * 100, 1)
        overall["outcomeHit"] = round(sum(1 for x in eva if x.get("outcomeHit")) / n * 100, 1)

    def agg(keyfn, minn, topn, namekey):
        g = defaultdict(list)
        for r in eva:
            k = keyfn(r)
            if k:
                g[k].append(r)
        rows = [{"%s" % namekey: k, "n": len(rs),
                 "exactHit": round(sum(1 for x in rs if x.get("exactHit")) / len(rs) * 100, 1),
                 "outcomeHit": round(sum(1 for x in rs if x.get("outcomeHit")) / len(rs) * 100, 1)}
                for k, rs in g.items() if len(rs) >= minn]
        rows.sort(key=lambda x: (-x["exactHit"], -x["outcomeHit"]))
        return rows[:topn]

    leagues = agg(lambda r: r.get("league"), 3, 20, "league")
    tg = defaultdict(list)
    for r in eva:
        for side in ("home", "away"):
            if r.get(side):
                tg[r[side]].append(r)
    trows = [{"team": k, "n": len(rs),
              "exactHit": round(sum(1 for x in rs if x.get("exactHit")) / len(rs) * 100, 1),
              "outcomeHit": round(sum(1 for x in rs if x.get("outcomeHit")) / len(rs) * 100, 1)}
             for k, rs in tg.items() if len(rs) >= 3]
    trows.sort(key=lambda x: (-x["exactHit"], -x["outcomeHit"]))
    teams = trows[:20]
    base_dir = overall.get("outcomeHit", 45.0)
    base_exact = overall.get("exactHit", 8.0)
    return {"overall": overall, "leagues": leagues, "teams": teams,
            "baseline": {"dir": round(base_dir, 1), "exact": round(base_exact, 1)}}


# ---------------- 3) 主流程 ----------------
def main():
    hist = _load(HIST_PATH)
    ws = _load(WS_PATH)
    records = hist.get("records", {})
    pending = hist.get("pending", {})
    ws_history = ws.get("history", [])

    jc, jc_total, eng, eng_total, loose = build_indexes(ws)
    print("赛果池：竞彩官方 %d 场 / 英文队名 %d 条（索引键 %d）"
          % (jc_total, eng_total, len(eng)))
    print("起始：records %d 条，pending %d 条，ws.history %d 条"
          % (len(records), len(pending), len(ws_history)))
    print("北京日期：%s%s" % (TODAY.isoformat(), "  [DRY-RUN 不写盘]" if DRY else ""))
    print("-" * 60)

    added, skipped = 0, defaultdict(int)
    by_source = defaultdict(int)
    new_jc_visible = 0
    new_records = []
    done_ids = set()

    for mid, v in list(pending.items()):
        # 幂等：已有赛果记录不重复处理
        old = records.get(mid)
        if isinstance(old, dict) and old.get("actH") is not None:
            done_ids.add(mid)
            skipped["已有赛果记录"] += 1
            continue
        ah, aa, src = find_result(v, jc, eng, loose)
        if ah is None:
            skipped[src] += 1
            continue
        rec = {
            "id": mid, "date": v.get("date"), "league": v.get("league"),
            "home": v.get("home"), "away": v.get("away"),
            "predH": v.get("predH"), "predA": v.get("predA"),
            "actH": ah, "actA": aa,
            "exactHit": (v.get("predH") == ah and v.get("predA") == aa),
            "outcomeHit": outcome(v.get("predH"), v.get("predA")) == outcome(ah, aa),
            "jcNum": v.get("jcNum"),
        }
        if isinstance(v.get("conf"), dict):
            rec["preConf"] = dict(v["conf"])
        records[mid] = rec
        new_records.append(rec)
        done_ids.add(mid)
        added += 1
        by_source[src] += 1
        if v.get("jcNum"):
            new_jc_visible += 1

    # 同步 ws.history：以 records 为准重建（去重、按日期倒序、截 800，与抓取端一致）
    all_eva = [r for r in records.values() if isinstance(r, dict) and r.get("actH") is not None]
    all_eva.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    ws["history"] = all_eva[:800]
    ws["historyDateList"] = sorted({x.get("date") for x in all_eva if x.get("date")}, reverse=True)
    ws["reliable"] = recompute_reliable(all_eva)
    # 不改 ws.updatedAt：它是「源站最后成功抓取时间」，回填历史赛果不代表源站恢复，
    # 保持原值以免误导 selftest / 用户。台账 hist 的 updatedAt 如实更新。
    hist["records"] = records
    hist["pending"] = {k: v for k, v in pending.items() if k not in done_ids}
    hist["updatedAt"] = STAMP

    print("回填结果：新增 records %d 条（其中带竞彩编号、页面可见 %d 条）"
          % (added, new_jc_visible))
    print("  来源分布：", dict(by_source))
    print("  保留 pending %d 条，原因：" % len(hist["pending"]))
    for k, c in sorted(skipped.items(), key=lambda x: -x[1]):
        print("    %-24s %d" % (k, c))
    ov = ws["reliable"]["overall"]
    print("  重算命中率：样本 %d 场 | 精确命中 %.1f%% | 方向命中 %.1f%%"
          % (ov["n"], ov.get("exactHit", 0), ov.get("outcomeHit", 0)))
    print("  ws.history: %d 条 | historyDateList %d 个日期"
          % (len(ws["history"]), len(ws["historyDateList"])))
    if new_records:
        print("  样例(前3)：")
        for r in new_records[:3]:
            print("    %s %s %s vs %s 预测%s:%s 实际%s:%s 精确%s 方向%s 来源已记"
                  % (r["date"], r.get("league"), r["home"], r["away"],
                     r["predH"], r["predA"], r["actH"], r["actA"],
                     r["exactHit"], r["outcomeHit"]))
    print("-" * 60)

    if DRY:
        print("DRY-RUN 结束，未写盘。")
        return
    _save(HIST_PATH, hist)
    _save(WS_PATH, ws)
    print("已写盘：whoscored_history.json / whoscored_data.json")


if __name__ == "__main__":
    main()
