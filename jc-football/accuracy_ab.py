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
VZ_HIST = os.path.join(HERE, "vzhan_history.json")
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


def _pairwise(recs, sa, sb):
    """两源同场头对头：两源都有预测且已完赛的场次，统计谁更准。"""
    paired = [r for r in recs.values()
              if r.get(sa) and r.get(sb) and r.get("act")
              and r["act"].get("h") is not None
              and r[sa].get("exactHit") is not None
              and r[sb].get("exactHit") is not None]
    n = len(paired)
    a_ex = a_oc = b_ex = b_oc = 0
    both_right = both_wrong = a_only_dir = b_only_dir = 0
    a_ex_only = b_ex_only = 0
    for r in paired:
        ah = r[sa].get("outcomeHit"); bh = r[sb].get("outcomeHit")
        ae = r[sa].get("exactHit"); be = r[sb].get("exactHit")
        a_oc += 1 if ah else 0
        b_oc += 1 if bh else 0
        a_ex += 1 if ae else 0
        b_ex += 1 if be else 0
        if ah and bh:
            both_right += 1
        elif (not ah) and (not bh):
            both_wrong += 1
        elif ah and not bh:
            a_only_dir += 1
        elif bh and not ah:
            b_only_dir += 1
        if ae and not be:
            a_ex_only += 1
        elif be and not ae:
            b_ex_only += 1
    p_dir = sign_test_p(a_only_dir, b_only_dir)
    p_ex = sign_test_p(a_ex_only, b_ex_only)
    lab = {"model": "模型", "ws": "WhoScored", "vz": "V站"}
    return {
        "a": lab.get(sa, sa), "b": lab.get(sb, sb),
        "n": n,
        "a_dir": round(100 * a_oc / n, 1) if n else 0,
        "b_dir": round(100 * b_oc / n, 1) if n else 0,
        "a_exact": round(100 * a_ex / n, 1) if n else 0,
        "b_exact": round(100 * b_ex / n, 1) if n else 0,
        "both_right": both_right, "both_wrong": both_wrong,
        "a_only_dir": a_only_dir, "b_only_dir": b_only_dir,
        "a_only_exact": a_ex_only, "b_only_exact": b_ex_only,
        "sign_p_dir": round(p_dir, 4) if p_dir is not None else None,
        "sign_p_exact": round(p_ex, 4) if p_ex is not None else None,
    }


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
        for src in ("ws", "model", "vz"):
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

    # --- V站·呼死狗 历史（v1.7.77 新增第三源）---
    try:
        vh = json.load(open(VZ_HIST, encoding="utf-8")).get("matches", {})
    except Exception:
        vh = {}
    for x in vh.values():
        if x.get("finalH") is None:
            continue
        k = kstr(x.get("date"), x.get("home"), x.get("away"))
        r = recs.get(k) or {"date": x.get("date"), "league": x.get("league"),
                            "home": x.get("home"), "away": x.get("away"),
                            "jcNum": x.get("jcNum"), "ws": None, "model": None,
                            "vz": None, "act": None, "sources": []}
        r["vz"] = {"h": x.get("predH"), "a": x.get("predA"),
                   "exactHit": x.get("exactHit"), "outcomeHit": x.get("outcomeHit")}
        if x.get("jcNum"):
            r["jcNum"] = x.get("jcNum")
        _merge_act(r, x.get("finalH"), x.get("finalA"))
        if "vz_hist" not in r["sources"]:
            r["sources"].append("vz_hist")
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

    model = per_source("model")
    ws = per_source("ws")
    vz = per_source("vz")

    # 同场配对（模型 vs WhoScored，保留向后兼容）
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

    # 三源两两配对（v1.7.77 新增 V站）
    pair_mw = _pairwise(recs, "model", "ws")
    pair_mv = _pairwise(recs, "model", "vz")
    pair_wv = _pairwise(recs, "ws", "vz")
    # 三源同场（三者皆有预测且完赛）
    trip = [r for r in recs.values()
            if r.get("model") and r.get("ws") and r.get("vz") and r.get("act")
            and r["act"].get("h") is not None
            and all(r[s].get("exactHit") is not None for s in ("model", "ws", "vz"))]
    trip_n = len(trip)

    # 联赛维度（三源方向命中累加，v1.7.77 含 V站）
    by_league = defaultdict(lambda: {"n": 0, "m_oc": 0, "w_oc": 0, "v_oc": 0})
    for r in recs.values():
        if not (r.get("act") and r["act"].get("h") is not None):
            continue
        lg = r.get("league") or "未知"
        d = by_league[lg]
        d["n"] += 1
        if r.get("model") and r["model"].get("outcomeHit"):
            d["m_oc"] += 1
        if r.get("ws") and r["ws"].get("outcomeHit"):
            d["w_oc"] += 1
        if r.get("vz") and r["vz"].get("outcomeHit"):
            d["v_oc"] += 1

    # 结论（三源方向命中率对比，配对符号检验判定显著性）
    rates = [("模型", model["dir"], model["n"]),
             ("WhoScored", ws["dir"], ws["n"]),
             ("V站", vz["dir"], vz["n"])]
    valid = [x for x in rates if x[2] >= 10]
    if not valid:
        verdict = "样本不足（各源需≥10场完赛对比才能下结论）"
    else:
        best = max(valid, key=lambda x: x[1])
        others = " / ".join("%s %.1f%%" % (o[0], o[1])
                            for o in valid if o[0] != best[0])
        verdict = ("%s 方向命中率最高（%.1f%%，N=%d）；其余 %s。"
                   "显著性以配对符号检验 p 值判定，详见下方三两配对。"
                   % (best[0], best[1], best[2], others))

    return {
        "paired_n": n,
        "model": model,
        "ws": ws,
        "vz": vz,
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
        "pair_mw": pair_mw, "pair_mv": pair_mv, "pair_wv": pair_wv,
        "three_way_n": trip_n,
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
    print("       模型 vs WhoScored vs V站  命中率 A/B/C 对比")
    print("       更新时间 %s | 累加库总场次 %d" % (store.get("updatedAt"),
                                                  s.get("total_records", 0)))
    print(line)
    m, w, v = s["model"], s["ws"], s["vz"]
    print("\n【各源独立命中率】（带赛果场次）")
    print("  %-10s %8s %10s %10s %12s" % ("数据源", "样本N", "方向命中", "精确命中", "平均MAE"))
    print("  %-10s %8d %9.1f%% %9.1f%% %12s" %
          ("模型", m["n"], m["dir"], m["exact"], (str(m["mae"]) if m["mae"] else "-")))
    print("  %-10s %8d %9.1f%% %9.1f%% %12s" %
          ("WhoScored", w["n"], w["dir"], w["exact"], "-"))
    print("  %-10s %8d %9.1f%% %9.1f%% %12s" %
          ("V站·呼死狗", v["n"], v["dir"], v["exact"], "-"))
    print("  方向95%%CI  模型[%s]  WS[%s]  V站[%s]" %
          (",".join(map(str, m["dir_ci"])), ",".join(map(str, w["dir_ci"])),
           ",".join(map(str, v["dir_ci"]))))

    pd = s["paired_dir"]
    print("\n【同场头对头 模型 vs WhoScored】（两源都有预测且已完赛，N=%d）" % s["paired_n"])
    print("  模型方向命中 %.1f%% | WhoScored方向命中 %.1f%%" %
          (pd["model_dir"], pd["ws_dir"]))
    print("  两者都对 %d | 都错 %d | 仅模型对 %d | 仅WS对 %d" %
          (pd["both_right"], pd["both_wrong"], pd["model_only"], pd["ws_only"]))
    print("  方向差异符号检验 p=%s" % pd["sign_p_dir"])
    pe = s["paired_exact"]
    print("  精确命中：模型 %.1f%% vs WS %.1f%% | 仅模型精中 %d / 仅WS精中 %d | p=%s" %
          (pe["model_exact"], pe["ws_exact"], pe["model_only"], pe["ws_only"],
           pe["sign_p_exact"]))

    def _pp(title, p):
        if not p.get("n"):
            print("\n【%s】 样本不足（N=0）" % title)
            return
        print("\n【%s】（N=%d）" % (title, p["n"]))
        print("  %s方向命中 %.1f%% | %s方向命中 %.1f%%" %
              (p["a"], p["a_dir"], p["b"], p["b_dir"]))
        print("  两者都对 %d | 都错 %d | 仅%s对 %d | 仅%s对 %d" %
              (p["both_right"], p["both_wrong"], p["a"], p["a_only_dir"],
               p["b"], p["b_only_dir"]))
        print("  方向差异符号检验 p=%s" % p["sign_p_dir"])

    _pp("同场头对头 模型 vs V站", s["pair_mv"])
    _pp("同场头对头 WhoScored vs V站", s["pair_wv"])
    if s.get("three_way_n"):
        print("  三源同场都有预测且完赛：%d 场" % s["three_way_n"])

    print("\n【联赛维度（方向命中）】")
    for lg, d in s.get("by_league", {}).items():
        if d["n"] < 3:
            continue
        print("  %-8s N=%-3d 模型%.0f%% WS%.0f%% V站%.0f%%" %
              (lg, d["n"], 100 * d["m_oc"] / d["n"], 100 * d["w_oc"] / d["n"],
               100 * d["v_oc"] / d["n"]))

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
