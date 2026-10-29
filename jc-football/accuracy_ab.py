#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
accuracy_ab.py — 模型(Poisson) vs WhoScored 预测命中率 A/B 对比引擎
===================================================================

为什么需要它：
    原先模型命中率(score_predict_accuracy.json)与 WhoScored 命中率
    (whoscored_history.json)是两套互相独立、且比赛 id 体系不同的历史，
    无法直接回答「同一场比赛，谁更准」。本引擎把三路数据按
    「日期 + 规范化队名」对齐到同一比赛键，做真正同场头对头对比。

三路数据源（统一规范键合并，自动累加、可重复运行）：
    1) matches.json        每日流程产物，同一场比赛同时带 WS(predH/predA)
                            与 模型(modelPred) 双预测 —— 保证未来场次 100% 同场对齐
    2) score_predict_accuracy.json  模型历史（football-data id，英文队名，带赛果）
    3) whoscored_history.json       WhoScored 历史（WS id，英文队名，带赛果/竞彩号）

规范键：mkey(date, home, away)
    -> (date, zh_norm(EN2ZH.get(home, home)), zh_norm(EN2ZH.get(away, away)))
    英文队名经 EN2ZH 翻成中文再 zh_norm；中文队名直过。两套历史与每日中文
    队名因此落在同一键空间，自动合并。

统计严谨性：
    - 各数据源独立命中率（含 Wilson 95% 置信区间）
    - 同场配对头对头：两者都对 / 都错 / 仅模型对 / 仅WS对
    - 配对符号检验(binomtest 双侧) 判断差异是否显著(p<0.05)

用法：
    python3.11 accuracy_ab.py            # 更新三源 + 计算 + 写 ab_compare.json + 报告
    python3.11 accuracy_ab.py --report   # 仅打印当前 ab_compare.json 的统计
    python3.11 accuracy_ab.py --reset    # 清空累加库后重建
"""
import os
import sys
import json
import math
import time
import argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from team_map import TEAM_EN2ZH, zh_norm  # noqa: E402

STORE = os.path.join(HERE, "ab_compare.json")
MODEL_HIST = os.path.join(HERE, "score_predict_accuracy.json")
WS_HIST = os.path.join(HERE, "whoscored_history.json")
MATCHES = os.path.join(HERE, "matches.json")


# ----------------------------- 规范键 -----------------------------
def canon(t):
    """队名->规范化中文键：英文经 EN2ZH 翻译后 zh_norm；中文直过。"""
    if not t:
        return ""
    return zh_norm(TEAM_EN2ZH.get(t, t)) or ""


def mkey(date, h, a):
    return (date or "", canon(h), canon(a))


def kstr(date, h, a):
    return json.dumps(list(mkey(date, h, a)), ensure_ascii=False)


def _outcome(h, a):
    if h is None or a is None:
        return None
    return "H" if h > a else ("A" if a > h else "D")


def _mae(ph, pa, ah, aa):
    if None in (ph, pa, ah, aa):
        return None
    return abs(ph - ah) + abs(pa - aa)


# ----------------------------- 统计辅助 -----------------------------
def wilson(k, n, z=1.96):
    """Wilson 95% 置信区间(百分比)。 corrected: 分母应为 1 + z²/n。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n))) / denom
    return (max(0.0, center - half) * 100, min(1.0, center + half) * 100)


def sign_test_p(k_more, k_less):
    """配对符号检验(双侧)：H0 两者无差异。返回 p 值。"""
    try:
        from scipy.stats import binomtest
    except Exception:
        return None
    n_dec = k_more + k_less
    if n_dec == 0:
        return 1.0
    return float(binomtest(max(k_more, k_less), n_dec, 0.5,
                           alternative="two-sided").pvalue)


# ----------------------------- 库读写 -----------------------------
def load_store():
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except Exception:
        return {"updatedAt": "", "records": {}, "stats": {}}


def save_store(store):
    store["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


def _eval_source(rec, src):
    """若 src 有预测且比赛已有赛果(act)，重算该源命中字段。"""
    s = rec.get(src)
    act = rec.get("act")
    if not s or not act or act.get("h") is None:
        return
    ph, pa = s.get("h"), s.get("a")
    ah, aa = act.get("h"), act.get("a")
    if None in (ph, pa, ah, aa):
        return
    s["exactHit"] = (ph == ah and pa == aa)
    s["outcomeHit"] = _outcome(ph, pa) == _outcome(ah, aa)
    if src == "model":
        s["mae"] = _mae(ph, pa, ah, aa)


def _merge_act(rec, ah, aa):
    act = rec.get("act") or {}
    if act.get("h") is None and ah is not None:
        rec["act"] = {"h": ah, "a": aa}
        for src in ("ws", "model"):
            _eval_source(rec, src)


# ----------------------------- 三源摄入 -----------------------------
def ingest_histories(store):
    """摄入两套历史（英文队名 -> 中文规范键），带赛果的场次入库。"""
    recs = store.setdefault("records", {})
    added = 0

    # --- 模型历史 ---
    try:
        mh = json.load(open(MODEL_HIST, encoding="utf-8")).get("history", [])
    except Exception:
        mh = []
    for x in mh:
        if x.get("actH") is None:
            continue
        k = kstr(x.get("date"), x.get("home"), x.get("away"))
        r = recs.get(k) or {"date": x.get("date"), "league": x.get("league"),
                            "home": x.get("home"), "away": x.get("away"),
                            "jcNum": x.get("jcNum"), "ws": None, "model": None,
                            "act": None, "sources": []}
        r["model"] = {"h": x.get("predH"), "a": x.get("predA"),
                      "exactHit": x.get("exactHit"), "outcomeHit": x.get("outcomeHit"),
                      "mae": x.get("mae")}
        _merge_act(r, x.get("actH"), x.get("actA"))
        if "model_hist" not in r["sources"]:
            r["sources"].append("model_hist")
        recs[k] = r
        added += 1

    # --- WhoScored 历史 ---
    try:
        wh = json.load(open(WS_HIST, encoding="utf-8")).get("records", {})
    except Exception:
        wh = {}
    for x in wh.values():
        if x.get("actH") is None:
            continue
        k = kstr(x.get("date"), x.get("home"), x.get("away"))
        r = recs.get(k) or {"date": x.get("date"), "league": x.get("league"),
                            "home": x.get("home"), "away": x.get("away"),
                            "jcNum": x.get("jcNum"), "ws": None, "model": None,
                            "act": None, "sources": []}
        r["ws"] = {"h": x.get("predH"), "a": x.get("predA"),
                   "exactHit": x.get("exactHit"), "outcomeHit": x.get("outcomeHit")}
        if x.get("jcNum"):
            r["jcNum"] = x.get("jcNum")
        _merge_act(r, x.get("actH"), x.get("actA"))
        if "ws_hist" not in r["sources"]:
            r["sources"].append("ws_hist")
        recs[k] = r
        added += 1
    return added


def record_daily(store):
    """摄入每日 matches.json：同一场比赛同时带 WS + 模型双预测，保证同场对齐。"""
    try:
        ms = json.load(open(MATCHES, encoding="utf-8")).get("matches", [])
    except Exception:
        return 0
    recs = store.setdefault("records", {})
    added = 0
    for m in ms:
        ph, pa = m.get("predH"), m.get("predA")
        mp = m.get("modelPred") or {}
        if ph is None and (mp.get("h") is None):
            continue
        k = kstr(m.get("date"), m.get("home"), m.get("away"))
        r = recs.get(k) or {"date": m.get("date"), "league": m.get("league"),
                            "home": m.get("home"), "away": m.get("away"),
                            "jcNum": m.get("jcNum"), "ws": None, "model": None,
                            "act": None, "sources": []}
        if ph is not None:
            r["ws"] = {"h": ph, "a": pa, "exactHit": None, "outcomeHit": None}
        if mp.get("h") is not None:
            r["model"] = {"h": mp.get("h"), "a": mp.get("a"),
                          "exactHit": None, "outcomeHit": None, "mae": None}
        if m.get("jcNum"):
            r["jcNum"] = m.get("jcNum")
        _merge_act(r, m.get("actH"), m.get("actA"))
        if "daily" not in r["sources"]:
            r["sources"].append("daily")
        recs[k] = r
        added += 1
    return added


# ----------------------------- 统计 -----------------------------
def compute(store):
    recs = store.get("records", {})
    # 各源独立命中率
    def per_source(src):
        rows = [r for r in recs.values()
                if r.get(src) and r.get("act") and r["act"].get("h") is not None]
        n = len(rows)
        ex = sum(1 for r in rows if r[src].get("exactHit"))
        oc = sum(1 for r in rows if r[src].get("outcomeHit"))
        maes = [r[src]["mae"] for r in rows
                if isinstance(r[src].get("mae"), (int, float))]
        lo_o, hi_o = wilson(oc, n)
        lo_e, hi_e = wilson(ex, n)
        return {"n": n, "exact": round(100 * ex / n, 1) if n else 0,
                "dir": round(100 * oc / n, 1) if n else 0,
                "dir_ci": [round(lo_o, 1), round(hi_o, 1)],
                "exact_ci": [round(lo_e, 1), round(hi_e, 1)],
                "mae": round(sum(maes) / len(maes), 2) if maes else None}

    # 同场配对
    paired = [r for r in recs.values()
              if r.get("ws") and r.get("model") and r.get("act")
              and r["act"].get("h") is not None]
    n = len(paired)
    m_ex = m_oc = w_ex = w_oc = 0
    both_right = both_wrong = model_only = ws_only = 0
    m_ex_only = w_ex_only = 0
    for r in paired:
        mh = r["model"].get("outcomeHit"); wh_ = r["ws"].get("outcomeHit")
        me = r["model"].get("exactHit"); we = r["ws"].get("exactHit")
        if r["model"].get("exactHit"): m_ex += 1
        if r["model"].get("outcomeHit"): m_oc += 1
        if r["ws"].get("exactHit"): w_ex += 1
        if r["ws"].get("outcomeHit"): w_oc += 1
        if mh and wh_:
            both_right += 1
        elif (not mh) and (not wh_):
            both_wrong += 1
        elif mh and not wh_:
            model_only += 1
        elif wh_ and not mh:
            ws_only += 1
        if me and not we:
            m_ex_only += 1
        elif we and not me:
            w_ex_only += 1
    p_dir = sign_test_p(model_only, ws_only)
    p_ex = sign_test_p(m_ex_only, w_ex_only)

    # 联赛维度（配对子集）
    by_league = defaultdict(lambda: {"n": 0, "m_oc": 0, "w_oc": 0,
                                     "both": 0, "m_only": 0, "w_only": 0})
    for r in paired:
        lg = r.get("league") or "未知"
        d = by_league[lg]
        d["n"] += 1
        mh = r["model"].get("outcomeHit"); wh_ = r["ws"].get("outcomeHit")
        d["m_oc"] += 1 if mh else 0
        d["w_oc"] += 1 if wh_ else 0
        if mh and wh_:
            d["both"] += 1
        elif mh and not wh_:
            d["m_only"] += 1
        elif wh_ and not mh:
            d["w_only"] += 1

    # 结论
    if n and p_dir is not None:
        if model_only > ws_only and p_dir < 0.05:
            verdict = "模型显著更优"
        elif ws_only > model_only and p_dir < 0.05:
            verdict = "WhoScored 显著更优"
        else:
            verdict = "无显著差异（打平）"
    else:
        verdict = "样本不足"

    return {
        "paired_n": n,
        "model": per_source("model"),
        "ws": per_source("ws"),
        "paired_dir": {
            "both_right": both_right, "both_wrong": both_wrong,
            "model_only": model_only, "ws_only": ws_only,
            "model_dir": round(100 * m_oc / n, 1) if n else 0,
            "ws_dir": round(100 * w_oc / n, 1) if n else 0,
            "sign_p_dir": round(p_dir, 4) if p_dir is not None else None,
        },
        "paired_exact": {
            "model_exact": round(100 * m_ex / n, 1) if n else 0,
            "ws_exact": round(100 * w_ex / n, 1) if n else 0,
            "model_only": m_ex_only, "ws_only": w_ex_only,
            "sign_p_exact": round(p_ex, 4) if p_ex is not None else None,
        },
        "by_league": {k: v for k, v in sorted(
            by_league.items(), key=lambda kv: -kv[1]["n"])[:12]},
        "verdict": verdict,
        "total_records": len(recs),
    }


# ----------------------------- 报告 -----------------------------
def report(store):
    s = store.get("stats", {})
    if not s:
        print("（无统计，先运行更新）")
        return
    line = "=" * 64
    print(line)
    print("       模型 vs WhoScored  命中率 A/B 对比")
    print("       更新时间 %s | 累加库总场次 %d" % (store.get("updatedAt"),
                                                  s.get("total_records", 0)))
    print(line)
    m, w = s["model"], s["ws"]
    print("\n【各源独立命中率】（带赛果场次）")
    print("  %-10s %8s %10s %10s %12s" % ("数据源", "样本N", "方向命中", "精确命中", "平均MAE"))
    print("  %-10s %8d %9.1f%% %9.1f%% %12s" %
          ("模型", m["n"], m["dir"], m["exact"], (str(m["mae"]) if m["mae"] else "-")))
    print("  %-10s %8d %9.1f%% %9.1f%% %12s" %
          ("WhoScored", w["n"], w["dir"], w["exact"], "-"))
    print("  方向95%%CI  模型[%s]  WS[%s]" %
          (",".join(map(str, m["dir_ci"])), ",".join(map(str, w["dir_ci"]))))

    pd = s["paired_dir"]
    print("\n【同场头对头】（两源都有预测且已完赛，N=%d）" % s["paired_n"])
    print("  模型方向命中 %.1f%% | WhoScored方向命中 %.1f%%" %
          (pd["model_dir"], pd["ws_dir"]))
    print("  两者都对 %d | 都错 %d | 仅模型对 %d | 仅WS对 %d" %
          (pd["both_right"], pd["both_wrong"], pd["model_only"], pd["ws_only"]))
    print("  方向差异符号检验 p=%s" % pd["sign_p_dir"])
    pe = s["paired_exact"]
    print("  精确命中：模型 %.1f%% vs WS %.1f%% | 仅模型精中 %d / 仅WS精中 %d | p=%s" %
          (pe["model_exact"], pe["ws_exact"], pe["model_only"], pe["ws_only"],
           pe["sign_p_exact"]))

    print("\n【联赛维度（配对方向命中）】")
    for lg, d in s.get("by_league", {}).items():
        if d["n"] < 3:
            continue
        print("  %-8s N=%-3d 模型%.0f%% WS%.0f%%  都对%d/仅模%d/仅WS%d" %
              (lg, d["n"], 100 * d["m_oc"] / d["n"], 100 * d["w_oc"] / d["n"],
               d["both"], d["m_only"], d["w_only"]))

    print("\n【结论】 %s" % s["verdict"])
    print(line)


# ----------------------------- 主流程 -----------------------------
def update(reset=False):
    store = load_store()
    if reset:
        store = {"updatedAt": "", "records": {}, "stats": {}}
    a = ingest_histories(store)
    b = record_daily(store)
    store["stats"] = compute(store)
    save_store(store)
    print("[A/B] 摄入历史 %d 场、每日 %d 场；累加库 %d 场" %
          (a, b, len(store["records"])))
    report(store)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="仅打印现有统计")
    ap.add_argument("--reset", action="store_true", help="清空累加库后重建")
    args = ap.parse_args()
    if args.report:
        report(load_store())
        return
    update(reset=args.reset)


if __name__ == "__main__":
    main()
