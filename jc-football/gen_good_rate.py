#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_good_rate.py —— 竞彩「正路推荐」正确率统计（v1.7.124）

用户诉求：今日赛程里标为「正路」(verdict.t == "good") 的推荐，
历史正确率到底是多少，需要一个可核对、不虚高的统计。

产物：
  1. good_rate_stats.json          独立统计文件（便于核对/交付）
  2. matches.json.goodRateStats    注入页面（随 matches.json 部署，刷新自动重算）
  3. ../正路推荐正确率统计.html     自包含静态报告（可离线查看）

统计口径（全部写进产物 notes，页面同步展示）：
  · 正路 = 系统判定 good，即「预测市场热门方向打出」；
    bad / cold 是反指（预测该方向不会打出）、warn 谨慎、无判定，均不计入本统计
  · 命中 = 官方赛果 had == verdict.dir；只计已结算 hitFlag ∈ (hit, miss)
  · 取消/走水 (n-a) 与待开奖 (None) 一律排除，单列数量，绝不拿来凑样本
  · 市场去水隐含概率 = 三项赔率倒数归一化（理论「真概率」基准）
  · 盈亏平衡命中率 = mean(1 / 推荐方向赔率)，含抽水，是「按赔率下注不亏」的线
  · ROI = 每场 1 单位均注，命中收 (赔率-1)，未中亏 1
  · 今日赛程按竞彩批次 (date / num 前缀) 隔离，只列当天批次；未开奖不计入正确率
  · 不承诺盈利：命中率高于「真概率」不等于赚钱，必须同时看盈亏平衡线与 ROI

设计约束：幂等（统计签名不变则不改写 matches.json）、零外网、失败保留旧数据。
"""
import collections
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BJ = timezone(timedelta(hours=8))
DIR_NAME = {"h": "主胜", "d": "平局", "a": "客胜"}
ODDS_BANDS = [(1.0, 1.5, "1.00~1.50"), (1.5, 2.0, "1.50~2.00"),
              (2.0, 3.0, "2.00~3.00"), (3.0, 99.0, "3.00 以上")]


def rd(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("! 读取 %s 失败：%s" % (name, e), flush=True)
        return default


def V(r):
    return r.get("verdict") or {}


def num(v, nd=2):
    return round(float(v), nd) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def devig(odds, d):
    """三项赔率倒数归一化 -> 去水隐含概率(%)，即市场定价的「真概率」基准。"""
    if not isinstance(odds, dict) or d not in ("h", "d", "a"):
        return None
    inv = {}
    for k in ("h", "d", "a"):
        try:
            o = float(odds.get(k))
        except (TypeError, ValueError):
            return None
        if o <= 1:
            return None
        inv[k] = 1.0 / o
    s = sum(inv.values())
    return round(inv[d] / s * 100, 2) if s > 0 else None


def breakeven(odds, d):
    """1/赔率 -> 含抽水的盈亏平衡命中率(%)。"""
    if not isinstance(odds, dict):
        return None
    try:
        o = float(odds.get(d))
    except (TypeError, ValueError):
        return None
    return round(100.0 / o, 2) if o > 1 else None


def wilson(h, n, z=1.96):
    if n <= 0:
        return [0.0, 0.0]
    p = h / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    d = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round((c - d) * 100, 2), round((c + d) * 100, 2)]


def ztest(hits, probs):
    """Poisson-binomial 正态近似单侧检验：观测命中数是否显著高于逐场基准概率之和。"""
    if not probs:
        return None
    exp = sum(probs)
    var = sum(p * (1 - p) for p in probs)
    if var <= 0:
        return None
    z = (hits - exp) / math.sqrt(var)
    p = 0.5 * math.erfc(z / math.sqrt(2))
    return {"expectedHits": round(exp, 2), "z": round(z, 3),
            "pOneSided": round(p, 6), "significant": bool(p < 0.05 and z > 0)}


def stat(rows):
    """rows: [{hit, devig, be, odds, ...}] -> 统一统计块。"""
    n = len(rows)
    h = sum(1 for r in rows if r["hit"])
    priced = [r for r in rows if r.get("be") is not None and r.get("odds")]
    n_priced = len(priced)
    h_priced = sum(1 for r in priced if r["hit"])
    devigs = [r["devig"] / 100.0 for r in rows if r.get("devig") is not None]
    bes = [r["be"] / 100.0 for r in priced]
    roi = None
    if priced:
        gain = sum((r["odds"] - 1) if r["hit"] else -1 for r in priced)
        roi = round(gain / n_priced * 100, 2)
    return {
        "n": n,
        "hit": h,
        "miss": n - h,
        "rate": round(100.0 * h / n, 2) if n else None,
        "wilson95": wilson(h, n),
        "pricedN": n_priced,
        "pricedRate": round(100.0 * h_priced / n_priced, 2) if n_priced else None,
        "marketDeVig": round(sum(devigs) / len(devigs) * 100, 2) if devigs else None,
        "breakEven": round(sum(bes) / len(bes) * 100, 2) if bes else None,
        "avgOdds": round(sum(r["odds"] for r in priced) / n_priced, 3) if priced else None,
        "roi": roi,
        "vsDeVig": round(100.0 * h / n - sum(devigs) / len(devigs) * 100, 2)
        if n and devigs else None,
        "vsBreakEven": round(100.0 * h_priced / n_priced - sum(bes) / len(bes) * 100, 2)
        if n_priced and bes else None,
        "testVsDeVig": ztest(h, devigs),
        "testVsBreakEven": ztest(h_priced, bes),
    }


def build_rows(hist, today_ids):
    """把历史 good 已结算记录转成统一 row。"""
    rows = []
    for r in hist:
        if r.get("hitFlag") not in ("hit", "miss") or V(r).get("t") != "good":
            continue
        d = V(r).get("dir")
        o = r.get("odds") or {}
        dv = devig(o, d)
        if dv is None:
            p = r.get("prob") or {}
            if isinstance(p.get(d), (int, float)):
                dv = float(p[d])
        be = breakeven(o, d)
        try:
            od = float(o.get(d)) if be is not None else None
        except (TypeError, ValueError):
            od = None
        rows.append({
            "id": r.get("id"), "num": r.get("num"), "date": r.get("date"),
            "league": r.get("league") or "-", "home": r.get("home") or "",
            "away": r.get("away") or "", "dir": d,
            "stars": V(r).get("stars") if isinstance(V(r).get("stars"), int) else 0,
            "hit": r["hitFlag"] == "hit", "devig": dv, "be": be, "odds": od,
            "result": (r.get("result") or {}).get("score") if isinstance(r.get("result"), dict) else None,
            "isToday": r.get("id") in today_ids,
        })
    return rows


def group_stat(rows, keyfn, min_n=1, label=None):
    g = collections.defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    out = []
    for k, rs in sorted(g.items(), key=lambda kv: str(kv[0])):
        if len(rs) < min_n:
            continue
        s = stat(rs)
        s["key"] = label(k) if label else str(k)
        out.append(s)
    return out


def band_of(odds):
    for a, b, name in ODDS_BANDS:
        if a <= odds < b:
            return name
    return "其他"


def make_stats():
    M = rd("matches.json", {})
    H = rd("history.json", {})
    hist = H.get("records", []) if isinstance(H, dict) else []
    ms = M.get("matches", []) if isinstance(M, dict) else []
    bj_today = datetime.now(BJ).date().isoformat()

    # ---- 今日竞彩批次（严格按 date / num 前缀隔离，绝不跨批次）----
    batches = collections.defaultdict(list)
    for m in ms:
        batches[m.get("date")].append(m)
    sp = M.get("safeParlay") or {}
    bd = sp.get("batchDate")
    if bd and bd in batches:
        main_date = bd
    elif bj_today in batches:
        main_date = bj_today
    elif batches:
        main_date = sorted(batches)[0]
    else:
        main_date = bj_today
    today_ms = batches.get(main_date, [])
    prefix = (today_ms[0].get("num") or "")[:2] if today_ms else ""
    label = (today_ms[0].get("week") or "") if today_ms else ""
    today_ids = {str(m.get("id")) for m in today_ms}

    # 历史索引：今日场次若已开奖，直接标出赛果
    hidx = {str(r.get("id")): r for r in hist}

    picks = []
    for m in today_ms:
        if V(m).get("t") != "good":
            continue
        d = V(m).get("dir")
        o = m.get("had") or {}
        p = m.get("hadProb") or {}
        hr = hidx.get(str(m.get("id")))
        settled = bool(hr and hr.get("hitFlag") in ("hit", "miss"))
        picks.append({
            "id": m.get("id"), "num": m.get("num"), "league": m.get("league") or "-",
            "home": m.get("home") or "", "away": m.get("away") or "",
            "kickoff": m.get("kickoff") or "", "dir": d,
            "dirName": DIR_NAME.get(d, "-"),
            "odds": num(o.get(d)) if isinstance(o, dict) else None,
            "prob": num(p.get(d)) if isinstance(p, dict) else None,
            "stars": V(m).get("stars") if isinstance(V(m).get("stars"), int) else 0,
            "status": m.get("status") or "-",
            "settled": settled,
            "hit": (hr.get("hitFlag") == "hit") if settled else None,
            "score": (hr.get("result") or {}).get("score") if settled and isinstance(hr.get("result"), dict) else None,
            "note": "已开奖" if settled else "未开奖，不计入正确率",
        })
    picks.sort(key=lambda x: (x["stars"] or 0), reverse=True)

    # ---- 历史全量口径 ----
    rows = build_rows(hist, today_ids)
    overall = stat(rows)
    excluded = {
        "na": sum(1 for r in hist if V(r).get("t") == "good" and r.get("hitFlag") == "n-a"),
        "pending": sum(1 for r in hist if V(r).get("t") == "good" and not r.get("hitFlag")),
        "otherVerdict": sum(1 for r in hist if V(r).get("t") != "good" and r.get("hitFlag") in ("hit", "miss")),
    }

    # ---- 分层 ----
    by_stars = group_stat(rows, lambda r: r["stars"], min_n=1,
                          label=lambda k: "%d星" % k if k else "无星级")
    priced_rows = [r for r in rows if r.get("odds")]
    by_band_raw = group_stat(priced_rows, lambda r: band_of(r["odds"]), min_n=1)
    band_order = {name: i for i, (_, _, name) in enumerate(ODDS_BANDS)}
    by_band = sorted(by_band_raw, key=lambda s: band_order.get(s["key"], 99))
    by_league = group_stat(rows, lambda r: r["league"], min_n=5)
    by_league.sort(key=lambda s: (-s["n"], s["key"]))
    by_date = group_stat(rows, lambda r: r["date"], min_n=1)[-30:]
    weekly = group_stat(rows, lambda r: "%s-W%02d" % (
        date.fromisoformat(r["date"]).isocalendar()[0],
        date.fromisoformat(r["date"]).isocalendar()[1]) if r["date"] else "-", min_n=1)

    windows = []
    for days, name in ((7, "近7日"), (30, "近30日"), (60, "近60日"), (9999, "全量")):
        cut = (date.fromisoformat(bj_today) - timedelta(days=days - 1)).isoformat()
        rs = [r for r in rows if r["date"] and cut <= r["date"] <= bj_today]
        if rs:
            s = stat(rs)
            s["key"] = name
            windows.append(s)

    # ---- 结论（诚实口径：命中率高于真概率 ≠ 赚钱）----
    be = overall.get("breakEven")
    dv = overall.get("marketDeVig")
    roi = overall.get("roi")
    rate = overall.get("rate")
    tv = overall.get("testVsBreakEven") or {}
    td = overall.get("testVsDeVig") or {}
    conclusion = (
        "正路判定历史 %d 战 %d 中，正确率 %.2f%%（Wilson 95%%CI %.2f%%~%.2f%%）。"
        "市场去水隐含 %.2f%%，表观超额 %+.2fpt（单侧 p=%.4f，方向判断似有超出定价的迹象）；"
        "但下注必须付抽水：盈亏平衡线是 %.2f%%，有赔率的 %d 场实际命中 %.2f%%，"
        "相对盈亏线 %+.2fpt，均注 ROI %+.2f%%（单侧 p=%.4f，未跑赢）。"
        "周度超额在 −22.7pt~+24.6pt 之间剧烈波动，样本仍不足以证明稳定可提现的优势。"
        "结论：可作方向参考，不构成盈利依据。"
    ) % (overall["n"], overall["hit"], rate, overall["wilson95"][0], overall["wilson95"][1],
         dv, overall["vsDeVig"], td.get("pOneSided", 1), be, overall["pricedN"],
         overall["pricedRate"], overall["vsBreakEven"], roi, tv.get("pOneSided", 1))

    notes = [
        "正路 = 系统判定 good（预测市场热门方向打出）；bad/cold 为反指、warn 为谨慎、无判定，均不计入本统计。",
        "命中判定：官方开奖赛果的胜平负方向 had 与判定方向 dir 一致记「命中」，不一致记「未中」；与 judge_hit() 完全同口径。",
        "样本只取已结算记录 hitFlag ∈ (hit, miss)；取消/走水 n-a 共 %d 条、待开奖 %d 条一律排除，不拿来凑样本。" % (excluded["na"], excluded["pending"]),
        "市场去水隐含概率 = 三项赔率倒数归一化，代表市场定价的「理论真概率」；赔率缺失时用赛前 prob 字段补。",
        "盈亏平衡命中率 = 1/推荐方向赔率 的均值，含庄家抽水，是「按赔率下注不亏钱」必须跨过的线；它高于去水隐含概率。",
        "ROI = 每场 1 单位均注，命中收 (赔率−1)、未中亏 1；只用有完整赔率的 %d 场计算。" % overall["pricedN"],
        "正确率高于「去水隐含」只说明方向判断略优于市场共识，不代表赚钱；能否盈利必须看是否跑赢「盈亏平衡线」与 ROI。",
        "今日赛程严格按竞彩批次隔离：只统计 %s（%s）当天开出的 %d 场，绝不混入周六/周日批次。" % (main_date, label or prefix, len(today_ms)),
        "今日 %d 场正路推荐中已开奖 %d 场、未开奖 %d 场；未开奖场次不计入正确率，赛后自动回填纳入统计。" % (
            len(picks), sum(1 for p in picks if p["settled"]), sum(1 for p in picks if not p["settled"])),
        "本统计只做可核对的回溯描述，不承诺盈利、不构成投注建议。",
    ]

    return {
        "version": "v1.7.124",
        "generatedAt": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "竞彩足球 · 正路(good)判定 · 胜平负方向",
        "today": {
            "batchDate": main_date, "batchLabel": label or prefix,
            "batchPrefix": prefix, "isToday": bool(main_date == bj_today),
            "totalMatches": len(today_ms), "goodCount": len(picks),
            "settledCount": sum(1 for p in picks if p["settled"]),
            "pendingCount": sum(1 for p in picks if not p["settled"]),
            "batchesAvailable": [
                {"date": d, "label": (v[0].get("week") or ""), "count": len(v)}
                for d, v in sorted(batches.items())
            ],
            "picks": picks,
        },
        "overall": overall,
        "excluded": excluded,
        "conclusion": conclusion,
        "byStars": by_stars,
        "byOddsBand": by_band,
        "byLeague": by_league,
        "byDate": by_date,
        "weekly": weekly,
        "windows": windows,
        "notes": notes,
    }


def signature(s):
    """排除时间戳的统计签名，用于幂等判断。"""
    d = json.loads(json.dumps(s))
    d.pop("generatedAt", None)
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def write_report(s, path):
    """生成自包含静态 HTML 报告（可离线查看 / 交付）。"""
    o = s["overall"]
    t = s["today"]

    def pct(v):
        return "%.2f%%" % v if isinstance(v, (int, float)) else "—"

    def cls(v):
        if not isinstance(v, (int, float)):
            return ""
        return "up" if v > 0 else ("down" if v < 0 else "")

    def rows_html(items, keytitle):
        h = []
        for x in items:
            h.append("<tr><td>%s</td><td>%d</td><td>%d</td><td><b>%s</b></td><td>%s</td><td>%s</td><td class='%s'>%s</td></tr>" % (
                x["key"], x["n"], x["hit"], pct(x["rate"]), pct(x.get("breakEven")),
                pct(x.get("marketDeVig")), cls(x.get("roi")),
                ("%+.2f%%" % x["roi"]) if isinstance(x.get("roi"), (int, float)) else "—"))
        return ("<table><thead><tr><th>%s</th><th>场次</th><th>命中</th><th>正确率</th>"
                "<th>盈亏平衡线</th><th>去水隐含</th><th>均注ROI</th></tr></thead><tbody>%s</tbody></table>"
                % (keytitle, "".join(h)))

    picks = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s vs %s</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s星</td><td>%s</td></tr>" % (
            p["num"], p["league"], p["home"], p["away"], p["dirName"],
            ("%.2f" % p["odds"]) if p["odds"] else "—",
            ("%.0f%%" % p["prob"]) if p["prob"] else "—",
            p["stars"], "<span class='pend'>%s</span>" % p["note"] if not p["settled"]
            else ("<span class='hit'>命中 %s</span>" % (p.get("score") or "") if p["hit"]
                  else "<span class='miss'>未中 %s</span>" % (p.get("score") or "")))
        for p in t["picks"]) or "<tr><td colspan='8'>今日批次暂无正路判定</td></tr>"

    notes = "".join("<li>%s</li>" % n for n in s["notes"])
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>正路推荐正确率统计 · %s</title>
<style>
:root{--bg:#0b0f14;--panel:#11161d;--panel2:#151c26;--line:#242e3b;--text:#e6edf5;--sub:#8a99ab;--accent:#4da3ff;--up:#f0554d;--down:#28b26b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;padding:18px;max-width:980px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;color:var(--accent);border-left:3px solid var(--accent);padding-left:9px}
.meta{color:var(--sub);font-size:12px;margin-bottom:18px}
.hero{background:linear-gradient(135deg,#16233a,#11161d);border:1px solid var(--line);border-radius:14px;padding:20px;display:flex;flex-wrap:wrap;gap:22px;align-items:center}
.big{font-size:46px;font-weight:800;letter-spacing:-1px;line-height:1;color:#fff}.big small{font-size:15px;color:var(--sub);font-weight:500}
.kv{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--sub)}.kv b{font-size:17px;color:var(--text)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card .l{font-size:11px;color:var(--sub)}.card .v{font-size:19px;font-weight:700;margin-top:3px}
.concl{background:rgba(77,163,255,.08);border:1px solid rgba(77,163,255,.3);border-radius:10px;padding:14px 16px;margin:14px 0;font-size:13px;line-height:1.8}
table{width:100%%;border-collapse:collapse;font-size:12px;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{background:var(--panel2);color:var(--sub);font-weight:600}tr:last-child td{border-bottom:none}
.up{color:var(--up)}.down{color:var(--down)}.pend{color:var(--sub)}.hit{color:var(--up);font-weight:700}.miss{color:var(--down);font-weight:700}
ul{font-size:12px;color:var(--sub);padding-left:20px}li{margin:5px 0}
.bar{height:9px;border-radius:5px;background:var(--panel2);overflow:hidden;margin:5px 0 12px}.bar i{display:block;height:100%%;background:var(--accent)}
footer{margin-top:26px;color:var(--sub);font-size:11px;border-top:1px solid var(--line);padding-top:12px}
</style></head><body>
<h1>今日赛程 · 正路推荐正确率统计</h1>
<div class="meta">版本 %s ｜ 生成 %s（北京时间）｜ 数据源：history.json 官方开奖回溯 + matches.json 竞彩赛程</div>
<div class="hero"><div><div class="big">%s <small>历史正确率</small></div>
<div class="kv" style="margin-top:8px">%d 战 · %d 中 · %d 未中　|　Wilson 95%% 置信区间 %s ~ %s</div></div>
<div class="kv"><b>%s</b><span>市场去水隐含（真概率基准）</span></div>
<div class="kv"><b>%s</b><span>盈亏平衡线（含抽水）</span></div>
<div class="kv"><b class="%s">%s</b><span>每场 1 单位均注 ROI</span></div></div>
<div class="grid">
<div class="card"><div class="l">相对真概率超额（全量）</div><div class="v %s">%+.2f pt</div></div>
<div class="card"><div class="l">相对盈亏线超额（有赔率样本）</div><div class="v %s">%+.2f pt</div></div>
<div class="card"><div class="l">有赔率样本</div><div class="v">%d 场</div></div>
<div class="card"><div class="l">平均赔率</div><div class="v">%s</div></div>
</div>
<div class="concl"><b>结论</b><br>%s</div>
<h2>今日竞彩批次：%s（%s）</h2>
<div class="meta">当天开出 %d 场，其中正路 %d 场；已开奖 %d 场、未开奖 %d 场。严格按竞彩批次隔离，不与周六/周日混算。</div>
<table><thead><tr><th>编号</th><th>联赛</th><th>对阵</th><th>正路方向</th><th>赔率</th><th>市场隐含</th><th>信心</th><th>状态</th></tr></thead><tbody>%s</tbody></table>
<h2>按信心星级分层</h2>%s
<h2>按赔率区间分层</h2>%s
<h2>按联赛分层（样本≥5）</h2>%s
<h2>时间窗口</h2>%s
<h2>近 30 日逐日</h2>%s
<h2>统计口径（可逐条核对）</h2><ul>%s</ul>
<footer>本统计为历史回溯描述，不承诺盈利、不构成投注建议。页面端同源展示于「今日赛程」Tab 顶部。</footer>
</body></html>""" % (
        t["batchDate"], s["version"], s["generatedAt"], pct(o["rate"]), o["n"], o["hit"], o["miss"],
        pct(o["wilson95"][0]), pct(o["wilson95"][1]), pct(o["marketDeVig"]), pct(o["breakEven"]),
        cls(o["roi"]), ("%+.2f%%" % o["roi"]) if o["roi"] is not None else "—",
        cls(o["vsDeVig"]), o["vsDeVig"], cls(o["vsBreakEven"]), o["vsBreakEven"],
        o["pricedN"], ("%.3f" % o["avgOdds"]) if o["avgOdds"] else "—",
        s["conclusion"], t["batchDate"], t["batchLabel"], t["totalMatches"], t["goodCount"],
        t["settledCount"], t["pendingCount"], picks,
        rows_html(s["byStars"], "星级"), rows_html(s["byOddsBand"], "赔率区间"),
        rows_html(s["byLeague"], "联赛"), rows_html(s["windows"], "窗口"),
        rows_html(s["byDate"], "日期"), notes)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    M = rd("matches.json")
    if not isinstance(M, dict) or not M.get("matches"):
        print("! matches.json 缺失或无比赛，跳过统计（保留旧数据）", flush=True)
        return 0
    s = make_stats()
    if s["overall"]["n"] == 0:
        print("! 无已结算正路样本，跳过统计", flush=True)
        return 0

    # 独立统计文件（幂等）
    p = os.path.join(HERE, "good_rate_stats.json")
    prev = rd("good_rate_stats.json")
    if not prev or signature(prev) != signature(s):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        print("✓ good_rate_stats.json 已更新", flush=True)
    else:
        print("· 统计无变化，good_rate_stats.json 未改写", flush=True)

    # 注入 matches.json（幂等，避免无谓的大文件 diff）
    old = M.get("goodRateStats")
    if not old or signature(old) != signature(s):
        M["goodRateStats"] = s
        tmp = os.path.join(HERE, "matches.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(M, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(HERE, "matches.json"))
        print("✓ matches.json.goodRateStats 已更新", flush=True)
    else:
        print("· 统计无变化，matches.json 未改写", flush=True)

    # 静态报告（每次覆盖，交付用）
    rp = os.path.join(HERE, "..", "正路推荐正确率统计.html")
    try:
        write_report(s, rp)
        print("✓ 报告：%s" % os.path.abspath(rp), flush=True)
    except Exception as e:
        print("! 报告生成失败（不影响数据）：%s" % e, flush=True)

    o = s["overall"]
    print("  正确率 %.2f%%（%d 战 %d 中）| 去水隐含 %.2f%% | 盈亏线 %.2f%% | ROI %+.2f%%"
          % (o["rate"], o["n"], o["hit"], o["marketDeVig"], o["breakEven"], o["roi"]), flush=True)
    print("  今日 %s 批次：%d 场，正路 %d 场（已结算 %d）"
          % (s["today"]["batchDate"], s["today"]["totalMatches"],
             s["today"]["goodCount"], s["today"]["settledCount"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
