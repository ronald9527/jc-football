#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROI 回测：检验「命中率」能否转化为「盈利」。
对每条已判定记录，按其推荐方向与赛果模拟 1 单位下注：
    命中 -> 收益 odds-1 ；错误 -> -1
分别按 判定类型 / 星级 / 联赛 / 规则组 汇总 ROI。

用法: python3 backtest_roi.py
"""
import json, os
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


def odds_of(rec, d):
    """取该方向的胜平负赔率"""
    o = rec.get("odds") or {}
    if isinstance(o, dict):
        v = o.get(d)
        if isinstance(v, (int, float)) and v > 1:
            return float(v)
        # 兼容嵌套结构 {"had": {...}}
        had = o.get("had") or {}
        if isinstance(had, dict):
            v = had.get(d)
            if isinstance(v, (int, float)) and v > 1:
                return float(v)
    return None


def main():
    hist = load("history.json", {"records": []})
    rs = hist.get("records", [])

    rows = []  # (rec, stake_return)
    for r in rs:
        f = r.get("hitFlag")
        if f not in ("hit", "miss"):
            continue
        d = (r.get("verdict") or {}).get("dir")
        o = odds_of(r, d)
        if not o:
            continue
        ret = (o - 1) if f == "hit" else -1.0
        rows.append((r, ret, o))

    if not rows:
        print("没有可用的赔率数据，无法回测")
        return

    def summary(items, key_fn, title, min_n=1):
        print()
        print("=" * 72)
        print(title)
        print("=" * 72)
        g = defaultdict(list)
        for r, ret, o in items:
            g[key_fn(r)].append(ret)
        print(f"{'分组':<14}{'场次':>6}{'命中率':>9}{'ROI':>10}{'平均赔率':>10}")
        out = []
        for k, rets in sorted(g.items(), key=lambda x: -len(x[1])):
            n = len(rets)
            if n < min_n:
                continue
            wins = sum(1 for x in rets if x > 0)
            roi = sum(rets) / n * 100
            avg_o = sum(o for r, ret, o in items if key_fn(r) == k) / n
            print(f"{str(k):<14}{n:>6}{wins / n * 100:>8.1f}%{roi:>9.1f}%{avg_o:>10.2f}")
            out.append({"key": str(k), "n": n, "hit": wins / n * 100, "roi": roi, "avgOdds": avg_o})
        return out

    tot = sum(x[1] for x in rows)
    n = len(rows)
    wins = sum(1 for x in rows if x[1] > 0)
    print(f"可回测样本: {n} 场")
    print(f"总体命中率: {wins / n * 100:.1f}%")
    print(f"【总体 ROI】 {tot / n * 100:+.1f}%   (累计 {tot:+.1f} 单位 / {n} 场)")
    print(f"平均赔率: {sum(x[2] for x in rows) / n:.2f}")
    breakeven = n / wins if wins else 0
    print(f"盈亏平衡所需赔率: {breakeven:.2f}   （低于此值长期必亏）")

    summary(rows, lambda r: (r.get("verdict") or {}).get("t") or "-", "按判定类型 ROI")
    summary(rows, lambda r: f"{int((r.get('verdict') or {}).get('stars') or 0)}★", "按信心星级 ROI")
    summary(rows, lambda r: r.get("league") or "-", "按联赛 ROI（样本>=6）", min_n=6)
    summary(rows, lambda r: {"h": "主胜", "d": "平局", "a": "客胜"}.get(
        (r.get("verdict") or {}).get("dir") or "-", "?"), "按推荐方向 ROI")

    # 按规则组
    print()
    print("=" * 72)
    print("按规则组 ROI（触发即下注，样本>=15）")
    print("=" * 72)
    grp = defaultdict(list)
    for r, ret, o in rows:
        for x in (r.get("hits") or []):
            if isinstance(x, dict) and x.get("g"):
                grp[x["g"]].append(ret)
    print(f"{'规则组':<16}{'场次':>6}{'命中率':>9}{'ROI':>10}")
    for g, rets in sorted(grp.items(), key=lambda x: -len(x[1])):
        if len(rets) < 15:
            continue
        nn = len(rets)
        w = sum(1 for x in rets if x > 0)
        print(f"{g:<16}{nn:>6}{w / nn * 100:>8.1f}%{sum(rets) / nn * 100:>9.1f}%")

    # 关键：星级>=3 过滤后的表现
    print()
    print("=" * 72)
    print("策略对比：不同过滤条件下的 ROI")
    print("=" * 72)
    def stat(sub, label):
        if not sub:
            print(f"{label:<28}{'0':>6}{'—':>9}{'—':>10}")
            return
        nn = len(sub)
        w = sum(1 for x in sub if x[1] > 0)
        roi = sum(x[1] for x in sub) / nn * 100
        print(f"{label:<28}{nn:>6}{w / nn * 100:>8.1f}%{roi:>9.1f}%")

    stat(rows, "全部下注")
    stat([x for x in rows if int((x[0].get("verdict") or {}).get("stars") or 0) >= 3], "仅星级>=3★")
    stat([x for x in rows if int((x[0].get("verdict") or {}).get("stars") or 0) >= 4], "仅星级>=4★")
    stat([x for x in rows if (x[0].get("verdict") or {}).get("t") == "bad"], "仅反指信号(bad)")
    stat([x for x in rows if (x[0].get("verdict") or {}).get("t") == "good"], "仅正路信号(good)")
    stat([x for x in rows if x[2] >= 2.0], "仅赔率>=2.0")
    stat([x for x in rows if x[2] < 1.6], "仅赔率<1.6")


if __name__ == "__main__":
    main()
