#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命中率归因分析：从 history.json 反推各规则组 / 星级 / 联赛 / 方向的实际预测力。

用法: python3 analyze.py
输出: 控制台报告 + analysis_report.json
"""
import json, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def rate(h, m):
    return (h / (h + m) * 100) if (h + m) else None


def main():
    hist = load("history.json", {"records": []})
    rs = hist.get("records", [])
    print(f"历史记录总数: {len(rs)}")

    judged = [r for r in rs if r.get("hitFlag") in ("hit", "miss")]
    pending = [r for r in rs if not r.get("result")]
    na = [r for r in rs if r.get("hitFlag") == "n-a" and r.get("result")]
    hit = sum(1 for r in judged if r["hitFlag"] == "hit")
    miss = len(judged) - hit
    base = rate(hit, miss)
    print(f"  已开赛有赛果: {len(rs) - len(pending)}")
    print(f"  可判定(hit/miss): {len(judged)}   不参与判定(n-a): {len(na)}   待开奖: {len(pending)}")
    print(f"  【总体命中率】 {hit}/{hit + miss} = {base:.1f}%\n")

    # ---------- B. 按 verdict 类型 ----------
    print("=" * 66)
    print("B. 按判定类型(t)分布")
    print("=" * 66)
    by_t = defaultdict(lambda: {"n": 0, "h": 0, "m": 0, "na": 0})
    for r in rs:
        t = (r.get("verdict") or {}).get("t") or "-"
        by_t[t]["n"] += 1
        f = r.get("hitFlag")
        if f == "hit":
            by_t[t]["h"] += 1
        elif f == "miss":
            by_t[t]["m"] += 1
        elif f == "n-a":
            by_t[t]["na"] += 1
    print(f"{'类型':<8}{'场次':>6}{'命中':>6}{'错误':>6}{'命中率':>9}")
    for t, v in sorted(by_t.items(), key=lambda x: -x[1]["n"]):
        r_ = rate(v["h"], v["m"])
        print(f"{t:<8}{v['n']:>6}{v['h']:>6}{v['m']:>6}{(f'{r_:.1f}%' if r_ else '—'):>9}")

    # ---------- C. 星级 vs 命中率（关键） ----------
    print()
    print("=" * 66)
    print("C. 信心星级 vs 实际命中率（检验星级标定是否可信）")
    print("=" * 66)
    by_s = defaultdict(lambda: {"h": 0, "m": 0})
    for r in judged:
        s = (r.get("verdict") or {}).get("stars")
        s = 0 if s is None else int(s)
        if r["hitFlag"] == "hit":
            by_s[s]["h"] += 1
        else:
            by_s[s]["m"] += 1
    print(f"{'星级':<8}{'场次':>6}{'命中':>6}{'命中率':>9}   相对基线")
    for s in sorted(by_s):
        v = by_s[s]
        r_ = rate(v["h"], v["m"])
        n = v["h"] + v["m"]
        delta = (r_ - base) if r_ is not None else 0
        print(f"{s}★{'':<6}{n:>6}{v['h']:>6}{(f'{r_:.1f}%' if r_ else '—'):>9}   {delta:+.1f}pt")

    # ---------- D. 各规则组预测力 ----------
    print()
    print("=" * 66)
    print("D. 各规则组实际预测力（lift = 该组命中率 - 总体基线）")
    print("=" * 66)
    grp = defaultdict(lambda: {"h": 0, "m": 0, "na": 0, "all": 0})
    for r in rs:
        gs = {x.get("g") for x in (r.get("hits") or []) if isinstance(x, dict) and x.get("g")}
        f = r.get("hitFlag")
        for g in gs:
            grp[g]["all"] += 1
            if f == "hit":
                grp[g]["h"] += 1
            elif f == "miss":
                grp[g]["m"] += 1
            else:
                grp[g]["na"] += 1
    print(f"{'规则组':<16}{'触发':>6}{'判定':>6}{'命中':>6}{'命中率':>9}{'lift':>9}  可信度")
    rows = []
    for g, v in sorted(grp.items(), key=lambda x: -(x[1]["h"] + x[1]["m"])):
        r_ = rate(v["h"], v["m"])
        n = v["h"] + v["m"]
        lift = (r_ - base) if r_ is not None else 0
        if n >= 100:
            conf = "★★★ 高"
        elif n >= 30:
            conf = "★★ 中"
        elif n >= 10:
            conf = "★ 低"
        else:
            conf = "⚠ 样本不足"
        print(f"{g:<16}{v['all']:>6}{n:>6}{v['h']:>6}{(f'{r_:.1f}%' if r_ else '—'):>9}{lift:>+8.1f}pt  {conf}")
        rows.append({"组": g, "触发": v["all"], "判定": n, "命中": v["h"],
                     "命中率": round(r_, 1) if r_ else None,
                     "lift": round(lift, 1), "可信度": conf})

    # ---------- E. 按联赛 ----------
    print()
    print("=" * 66)
    print("E. 按联赛命中率")
    print("=" * 66)
    lg = defaultdict(lambda: {"h": 0, "m": 0})
    for r in judged:
        k = r.get("league") or "-"
        if r["hitFlag"] == "hit":
            lg[k]["h"] += 1
        else:
            lg[k]["m"] += 1
    print(f"{'联赛':<12}{'场次':>6}{'命中率':>9}   相对基线")
    for k, v in sorted(lg.items(), key=lambda x: -(x[1]["h"] + x[1]["m"]))[:14]:
        r_ = rate(v["h"], v["m"])
        n = v["h"] + v["m"]
        print(f"{k:<12}{n:>6}{(f'{r_:.1f}%' if r_ else '—'):>9}   {(r_ - base):+.1f}pt")

    # ---------- F. 按方向 ----------
    print()
    print("=" * 66)
    print("F. 按推荐方向命中率")
    print("=" * 66)
    dr = defaultdict(lambda: {"h": 0, "m": 0})
    for r in judged:
        d = (r.get("verdict") or {}).get("dir") or "-"
        if r["hitFlag"] == "hit":
            dr[d]["h"] += 1
        else:
            dr[d]["m"] += 1
    name = {"h": "主胜", "d": "平局", "a": "客胜"}
    for d, v in sorted(dr.items(), key=lambda x: -(x[1]["h"] + x[1]["m"])):
        r_ = rate(v["h"], v["m"])
        print(f"{name.get(d, d):<10}{v['h'] + v['m']:>6}{(f'{r_:.1f}%' if r_ else '—'):>9}   基线{base:.1f}%")

    # ---------- G. 让球盘 ----------
    print()
    print("=" * 66)
    print("G. 让球胜平负(hhad) / 亚指 命中率")
    print("=" * 66)
    for key, label in (("hhadHit", "让球胜平负"), ("asianHit", "亚指"), ("scoreHit", "比分")):
        h = sum(1 for r in rs if r.get(key) == "hit")
        m = sum(1 for r in rs if r.get(key) == "miss")
        r_ = rate(h, m)
        print(f"{label:<12}{h + m:>6}{(f'{r_:.1f}%' if r_ else '—'):>9}   (命中{h}/错误{m})")

    # ---------- H. 串关 ----------
    print()
    print("=" * 66)
    print("H. 串关历史表现")
    print("=" * 66)
    ph = load("parlay_history.json")
    if ph:
        recs = ph.get("records", []) if isinstance(ph, dict) else ph
        print(f"串关记录: {len(recs)} 条")
        for r in recs[-8:]:
            print("  ", json.dumps({k: r.get(k) for k in ("date", "type", "odds", "hit", "result")},
                                   ensure_ascii=False))
    else:
        print("  无串关历史")

    out = {
        "总体": {"可判定": len(judged), "命中": hit, "错误": miss, "命中率": round(base, 1)},
        "规则组": rows,
        "基线": round(base, 1),
    }
    with open(os.path.join(HERE, "analysis_report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n报告已保存: analysis_report.json")


if __name__ == "__main__":
    main()
