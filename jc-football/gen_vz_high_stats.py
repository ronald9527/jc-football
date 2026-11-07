#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_vz_high_stats.py —— V站「🔥高信心」预测比分 / 方向命中率统计（v1.7.126）

用户诉求：V站 带「🔥高信心」的预测到底准不准？要能核对、不虚高的统计。

产物：
  1. vz_high_stats.json            独立统计文件（可核对 / 交付）
  2. matches.json.vzHighStats      注入页面（随 matches.json 部署，刷新自动重算）
  3. ../V站高信心预测命中率统计.html  自包含静态报告（可离线查看）

统计口径（全部写进 notes，页面同步展示）：
  · 高信心 = V站 预测方向 == 模型(modelPred) 预测方向（多源共识），与前端
    vzConfidence() 完全同源；赛前固化、赛后不再重算，避免用了赛后信息
  · 无模型快照的场次标「无法判定」，单列一组，绝不塞进高信心或普通组凑样本
  · 比分精确命中 = 预测比分 predH:predA == 官方赛果 finalH:finalA
  · 方向命中     = 预测胜平负方向 == 实际胜平负方向
  · 赛果来源：竞彩官方开奖回溯 history.json，按 (date, jcNum) 严格对齐
  · 基线不是拍脑袋的 1/3：按「该组预测分布 × 全量实际分布」算独立随机期望，
    另给朴素 1/3 作参照；命中率高于基线 ≠ 能赚钱，本统计不含赔率/ROI
  · 显著性：小样本用二项精确检验 + Fisher 精确检验，并给出 Wilson 95% CI
  · 今日清单严格按竞彩当天开售批次隔离，未开奖场次不计入任何命中率

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
VER = "v1.7.126"
DIR_NAME = {"H": "主胜", "D": "平局", "A": "客胜"}


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


def outcome(h, a):
    if h is None or a is None:
        return None
    return "H" if h > a else ("A" if a > h else "D")


def wilson(h, n, z=1.96):
    if n <= 0:
        return [0.0, 0.0]
    p = h / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    d = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round((c - d) * 100, 2), round((c + d) * 100, 2)]


def _comb(n, k):
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def binom_p_ge(h, n, p0):
    """单侧二项精确检验：P(X >= h | n, p0)。小样本比正态近似可靠。"""
    if n <= 0 or not (0 < p0 < 1):
        return None
    p = sum(_comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(h, n + 1))
    return round(min(1.0, max(0.0, p)), 6)


def fisher_two_sided(a, b, c, d):
    """2x2 Fisher 精确检验（双侧）。a,b = 组1 命中/未中；c,d = 组2 命中/未中。"""
    n = a + b + c + d
    if n <= 0:
        return None
    r1, r2, c1 = a + b, c + d, a + c

    def prob(x):
        return (_comb(r1, x) * _comb(r2, c1 - x)) / _comb(n, c1) if _comb(n, c1) else 0.0

    p_obs = prob(a)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    p = sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p_obs + 1e-12)
    return round(min(1.0, p), 6)


def two_prop_z(h1, n1, h2, n2):
    """两比例 z 检验（双侧），返回 z / p / 差值(pt)。"""
    if n1 <= 0 or n2 <= 0:
        return None
    p1, p2 = h1 / n1, h2 / n2
    pool = (h1 + h2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2)) if 0 < pool < 1 else 0
    if se <= 0:
        return {"diffPt": round((p1 - p2) * 100, 2), "z": 0.0, "pTwoSided": 1.0,
                "significant": False}
    z = (p1 - p2) / se
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"diffPt": round((p1 - p2) * 100, 2), "z": round(z, 3),
            "pTwoSided": round(p, 6), "significant": bool(p < 0.05)}


def pct(h, n):
    return round(100.0 * h / n, 2) if n else None


def stat(rows, baseline_exact=None, baseline_dir=None):
    """统一统计块：比分精确 + 方向，含 CI 与相对基线检验。"""
    n = len(rows)
    eh = sum(1 for r in rows if r["exactHit"])
    dh = sum(1 for r in rows if r["outcomeHit"])
    maes = [abs(r["predH"] - r["finalH"]) + abs(r["predA"] - r["finalA"]) for r in rows]
    block = {
        "n": n,
        "exactHit": eh, "exactRate": pct(eh, n), "exactWilson95": wilson(eh, n),
        "dirHit": dh, "dirRate": pct(dh, n), "dirWilson95": wilson(dh, n),
        "mae": round(sum(maes) / n, 2) if n else None,
        "avgPredGoals": round(sum(r["predH"] + r["predA"] for r in rows) / n, 2) if n else None,
        "avgActGoals": round(sum(r["finalH"] + r["finalA"] for r in rows) / n, 2) if n else None,
    }
    if baseline_exact is not None:
        block["exactBaseline"] = baseline_exact
        block["exactVsBaseline"] = round(block["exactRate"] - baseline_exact, 2) if n else None
        block["exactTest"] = {"pOneSided": binom_p_ge(eh, n, baseline_exact / 100.0),
                              "significant": bool(n and baseline_exact > 0 and
                                                 (binom_p_ge(eh, n, baseline_exact / 100.0) or 1) < 0.05
                                                 and block["exactRate"] > baseline_exact)}
    if baseline_dir is not None:
        block["dirBaseline"] = baseline_dir
        block["dirVsBaseline"] = round(block["dirRate"] - baseline_dir, 2) if n else None
        block["dirTest"] = {"pOneSided": binom_p_ge(dh, n, baseline_dir / 100.0),
                            "significant": bool(n and baseline_dir > 0 and
                                               (binom_p_ge(dh, n, baseline_dir / 100.0) or 1) < 0.05
                                               and block["dirRate"] > baseline_dir)}
    return block


def baseline_exact(rows, all_rows):
    if not rows or not all_rows:
        return None
    p = collections.Counter(r["predScore"] for r in rows)
    a = collections.Counter(r["actScore"] for r in all_rows)
    return round(sum(p[s] / len(rows) * (a.get(s, 0) / len(all_rows)) for s in p) * 100, 2)


def baseline_dir(rows, all_rows):
    if not rows or not all_rows:
        return None
    p = collections.Counter(r["predDir"] for r in rows)
    a = collections.Counter(r["actDir"] for r in all_rows)
    return round(sum(p[d] / len(rows) * (a.get(d, 0) / len(all_rows)) for d in p) * 100, 2)


def group_stat(rows, keyfn, min_n=1, all_rows=None):
    g = collections.defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    out = []
    for k, rs in sorted(g.items(), key=lambda kv: str(kv[0])):
        if len(rs) < min_n:
            continue
        s = stat(rs, baseline_exact(rs, all_rows or rows), baseline_dir(rs, all_rows or rows))
        s["key"] = str(k)
        out.append(s)
    return out


def build_rows(vhist):
    rows = []
    for key, r in (vhist.get("matches") or {}).items():
        if not isinstance(r, dict):
            continue
        if r.get("finalH") is None or r.get("finalA") is None:
            continue
        if r.get("predH") is None or r.get("predA") is None:
            continue
        ph, pa = int(r["predH"]), int(r["predA"])
        fh, fa = int(r["finalH"]), int(r["finalA"])
        rows.append({
            "key": key, "jcNum": r.get("jcNum"), "date": r.get("date"),
            "league": r.get("league") or "-", "home": r.get("home") or "",
            "away": r.get("away") or "",
            "predH": ph, "predA": pa, "finalH": fh, "finalA": fa,
            "predScore": "%d:%d" % (ph, pa), "actScore": "%d:%d" % (fh, fa),
            "predDir": outcome(ph, pa), "actDir": outcome(fh, fa),
            "exactHit": bool(ph == fh and pa == fa),
            "outcomeHit": bool(outcome(ph, pa) == outcome(fh, fa)),
            "highConf": bool(r.get("highConf")),
            "confAvailable": bool(r.get("confAvailable", True)),
            "confWhy": r.get("confWhy") or "",
            "modelPred": r.get("modelPred"),
        })
    return rows


def make_stats():
    M = rd("matches.json", {}) or {}
    VZ = rd("vzhan_data.json", {}) or {}
    VH = rd("vzhan_history.json", {}) or {}
    H = rd("history.json", {}) or {}
    hist_recs = H.get("records", []) if isinstance(H, dict) else []
    bj_today = datetime.now(BJ).date().isoformat()

    # ---- 竞彩当天批次（与 gen_good_rate.py / jcTodayBatch() 同口径，严格隔离）----
    ms = M.get("matches", []) if isinstance(M, dict) else []
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

    # ---- 官方赛果索引（今日清单结算状态）----
    off = {}
    for r in hist_recs:
        res = r.get("result") or {}
        if res.get("cancel") or res.get("home") is None:
            continue
        off.setdefault((r.get("date"), r.get("num")), res)

    # ---- 今日高信心清单（只取当天批次，未开奖不计入命中率）----
    vz_all = VZ.get("matches", []) if isinstance(VZ, dict) else []
    vz_today = [m for m in vz_all if m.get("date") == main_date]
    picks = []
    for m in vz_today:
        if not (m.get("hasPred") and m.get("highConf")):
            continue
        jn = m.get("jcNum")
        res = off.get((main_date, jn))
        settled = bool(res)
        ph, pa = m.get("predH"), m.get("predA")
        fh = int(res["home"]) if settled else None
        fa = int(res["away"]) if settled else None
        d = outcome(ph, pa)
        picks.append({
            "num": jn, "league": m.get("league") or "-",
            "home": m.get("home") or "", "away": m.get("away") or "",
            "kickoff": m.get("match_time") or "",
            "predH": ph, "predA": pa, "predScore": "%s:%s" % (ph, pa),
            "dir": d, "dirName": DIR_NAME.get(d, "-"),
            "modelPred": m.get("modelPred"),
            "confWhy": m.get("confWhy") or "V站·模型 方向共识",
            "settled": settled,
            "score": "%d:%d" % (fh, fa) if settled else None,
            "exactHit": (ph == fh and pa == fa) if settled else None,
            "dirHit": (d == outcome(fh, fa)) if settled else None,
            "note": "已开奖" if settled else "未开奖 · 不计入正确率",
        })
    picks.sort(key=lambda x: str(x["num"] or ""))

    # ---- 已结算归档 ----
    rows = build_rows(VH)
    hot = [r for r in rows if r["confAvailable"] and r["highConf"]]
    normal = [r for r in rows if r["confAvailable"] and not r["highConf"]]
    unknown = [r for r in rows if not r["confAvailable"]]
    be_all = baseline_exact(rows, rows)
    bd_all = baseline_dir(rows, rows)
    be_hot = baseline_exact(hot, rows)
    bd_hot = baseline_dir(hot, rows)
    be_normal = baseline_exact(normal, rows)
    bd_normal = baseline_dir(normal, rows)

    overall = stat(rows, be_all, bd_all)
    g_hot = stat(hot, be_hot, bd_hot)
    g_normal = stat(normal, be_normal, bd_normal)
    g_unknown = stat(unknown, baseline_exact(unknown, rows), baseline_dir(unknown, rows))

    comparison = {
        "exactHotVsNormal": two_prop_z(g_hot["exactHit"], g_hot["n"],
                                       g_normal["exactHit"], g_normal["n"]),
        "dirHotVsNormal": two_prop_z(g_hot["dirHit"], g_hot["n"],
                                     g_normal["dirHit"], g_normal["n"]),
        "exactFisher": fisher_two_sided(g_hot["exactHit"], g_hot["n"] - g_hot["exactHit"],
                                         g_normal["exactHit"], g_normal["n"] - g_normal["exactHit"]),
        "dirFisher": fisher_two_sided(g_hot["dirHit"], g_hot["n"] - g_hot["dirHit"],
                                      g_normal["dirHit"], g_normal["n"] - g_normal["dirHit"]),
        "hotShare": pct(len(hot), len(hot) + len(normal)),
        "naiveDirBaseline": 33.33,
    }

    # ---- 分层 ----
    by_date = group_stat(rows, lambda r: r["date"], min_n=1, all_rows=rows)[-30:]
    by_league = group_stat(rows, lambda r: r["league"], min_n=3, all_rows=rows)
    by_league.sort(key=lambda s: (-s["n"], s["key"]))
    by_group = []
    for name, rs in (("🔥高信心", hot), ("普通（方向分歧）", normal), ("无法判定", unknown)):
        s = stat(rs, baseline_exact(rs, rows), baseline_dir(rs, rows))
        s["key"] = name
        by_group.append(s)
    windows = []
    for days, name in ((7, "近7日"), (30, "近30日"), (60, "近60日"), (9999, "全量")):
        cut = (date.fromisoformat(bj_today) - timedelta(days=days - 1)).isoformat()
        rs = [r for r in rows if r["date"] and cut <= r["date"] <= bj_today]
        if rs:
            s = stat(rs, baseline_exact(rs, rows), baseline_dir(rs, rows))
            s["key"] = name
            windows.append(s)

    # ---- 结论（诚实口径：样本小、CI 宽、不承诺盈利）----
    sig_e = (g_hot.get("exactTest") or {}).get("significant")
    sig_d = (g_hot.get("dirTest") or {}).get("significant")
    cmp_d = comparison["dirHotVsNormal"] or {}
    conclusion = (
        "V站 🔥高信心（V站·模型 方向共识）历史 %d 场：预测比分精确命中 %d 场（%.2f%%，"
        "Wilson 95%%CI %.2f%%~%.2f%%），方向命中 %d 场（%.2f%%，Wilson 95%%CI %.2f%%~%.2f%%）。"
        "独立随机基线：比分 %.2f%%、方向 %.2f%%；高信心相对基线的比分超额 %+.2fpt、方向超额 %+.2fpt"
        "（二项精确单侧 p=%.4f / %.4f，%s）。"
        "与普通组（n=%d，比分 %.2f%%、方向 %.2f%%）相比，方向差 %+.2fpt（Fisher 双侧 p=%.4f，%s）。"
        "可判定高信心口径的场次仅 %d 场，样本有限、置信区间宽，%s；另有 %d 场因赛前无模型快照"
        "无法判定，已单列不参与对比。结论：仅作回溯描述，不构成稳定优势证明，不构成投注建议。"
    ) % (
        g_hot["n"], g_hot["exactHit"], g_hot["exactRate"] or 0,
        g_hot["exactWilson95"][0], g_hot["exactWilson95"][1],
        g_hot["dirHit"], g_hot["dirRate"] or 0,
        g_hot["dirWilson95"][0], g_hot["dirWilson95"][1],
        be_hot or 0, bd_hot or 0,
        g_hot.get("exactVsBaseline") or 0, g_hot.get("dirVsBaseline") or 0,
        (g_hot.get("exactTest") or {}).get("pOneSided") or 1,
        (g_hot.get("dirTest") or {}).get("pOneSided") or 1,
        "至少一项达到统计显著" if (sig_e or sig_d) else "均未达统计显著",
        g_normal["n"], g_normal["exactRate"] or 0, g_normal["dirRate"] or 0,
        cmp_d.get("diffPt") or 0, comparison["dirFisher"] or 1,
        "差异显著" if cmp_d.get("significant") else "差异不显著",
        len(hot) + len(normal),
        "方向表现%s" % ("显著优于随机基线" if sig_d else "未显著优于随机基线"),
        len(unknown),
    )

    notes = [
        "高信心口径：V站 预测比分推导的胜平负方向 == 模型(modelPred) 预测方向，与页面 vzConfidence() 同一函数口径；判定在赛前抓取时固化，赛后不重算，杜绝使用赛后信息。",
        "V站 数据源本身不提供「信心/星级」字段，故采用多源共识；赛前无模型预测快照的场次标为「无法判定」单列，既不进高信心组也不进普通组，绝不凑样本。",
        "比分精确命中 = 预测 predH:predA 与官方赛果 finalH:finalA 完全一致；方向命中 = 预测主胜/平局/客胜方向与实际方向一致（平局也算方向命中）。",
        "赛果来源：竞彩官方开奖回溯 history.json，按「日期 + 竞彩编号」严格对齐并做队名相似度校验；取消/走水/待开奖一律排除。",
        "独立随机基线 = 该组预测方向（比分）分布 × 全量实际方向（比分）分布之积求和，代表「预测与赛果完全独立」时的期望命中率；另列朴素 1/3 仅作参照，不作为判定标准。",
        "显著性：小样本采用二项精确单侧检验（vs 基线）与 Fisher 精确检验 / 两比例 z 检验（组间），并给出 Wilson 95% 置信区间；p<0.05 才称显著，样本不足时如实标注。",
        "本统计不含赔率、返奖率与 ROI：命中率高于基线只说明方向判断可能优于随机，不代表可盈利；竞彩抽水决定长期期望为负。",
        "今日清单严格按竞彩当天开售批次 %s（%s）隔离，只列当天 %d 场里的高信心预测；未开奖 %d 场不计入任何命中率，赛后自动回填纳入统计。" % (
            main_date, label or prefix, len(today_ms),
            sum(1 for p in picks if not p["settled"])),
        "归档档案由 fetch_vzhan.py 每日增量累积、backfill_vzhan_history.py 一次性全量回溯重建，随部署上云跨轮保留；统计每次刷新重算。",
        "本统计只做可核对的回溯描述，不承诺盈利、不构成投注建议。",
    ]

    return {
        "version": VER,
        "generatedAt": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "V站·呼死狗 🔥高信心预测 · 比分精确命中 / 胜平负方向命中",
        "source": {
            "predictions": "vzhan_data.json（V站 report/info 赛前预测比分）",
            "confidence": "matches.json modelPred（自建 Poisson 模型方向共识）",
            "results": "history.json 竞彩官方开奖回溯",
            "archive": "vzhan_history.json（赛前固化 highConf + 赛果归档）",
        },
        "today": {
            "batchDate": main_date, "batchLabel": label or prefix, "batchPrefix": prefix,
            "isToday": bool(main_date == bj_today),
            "totalMatches": len(today_ms),
            "vzPredCount": sum(1 for m in vz_today if m.get("hasPred")),
            "highCount": len(picks),
            "settledCount": sum(1 for p in picks if p["settled"]),
            "pendingCount": sum(1 for p in picks if not p["settled"]),
            "batchesAvailable": [
                {"date": d, "label": (v[0].get("week") or ""), "count": len(v),
                 "highCount": sum(1 for m in vz_all
                                  if m.get("date") == d and m.get("highConf"))}
                for d, v in sorted(batches.items())
            ],
            "picks": picks,
        },
        "overall": overall,
        "groups": {"high": g_hot, "normal": g_normal, "unknown": g_unknown},
        "comparison": comparison,
        "excluded": {
            "unknownConf": len(unknown),
            "pending": sum(1 for m in vz_all if m.get("hasPred") and m.get("finalH") is None),
            "noResult": sum(1 for m in vz_all if not m.get("hasPred")),
        },
        "conclusion": conclusion,
        "byGroup": by_group,
        "byDate": by_date,
        "byLeague": by_league,
        "windows": windows,
        "notes": notes,
    }


def signature(s):
    d = json.loads(json.dumps(s))
    d.pop("generatedAt", None)
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def write_report(s, path):
    """自包含静态 HTML 报告（可离线查看 / 交付）。"""
    g = s["groups"]
    o, t = s["overall"], s["today"]
    hot, normal, unknown = g["high"], g["normal"], g["unknown"]

    def P(v):
        return "%.2f%%" % v if isinstance(v, (int, float)) else "—"

    def SG(v):
        return ("%+.2f" % v) if isinstance(v, (int, float)) else "—"

    def CC(v):
        return "" if not isinstance(v, (int, float)) else ("up" if v > 0 else ("down" if v < 0 else ""))

    def block_table(items, keytitle):
        h = []
        for x in items:
            h.append(
                "<tr><td>%s</td><td>%d</td><td>%d</td><td><b>%s</b></td><td>%s</td>"
                "<td>%d</td><td><b>%s</b></td><td>%s</td><td class='%s'>%s</td></tr>"
                % (x["key"], x["n"], x["exactHit"], P(x["exactRate"]),
                   "%s ~ %s" % (P(x["exactWilson95"][0]), P(x["exactWilson95"][1])),
                   x["dirHit"], P(x["dirRate"]),
                   "%s ~ %s" % (P(x["dirWilson95"][0]), P(x["dirWilson95"][1])),
                   CC(x.get("dirVsBaseline")), SG(x.get("dirVsBaseline")) + "pt"))
        return ("<table><thead><tr><th>%s</th><th>场次</th><th>比分精确</th><th>精确率</th>"
                "<th>精确CI</th><th>方向命中</th><th>方向率</th><th>方向CI</th><th>方向超额</th>"
                "</tr></thead><tbody>%s</tbody></table>" % (keytitle, "".join(h)))

    picks = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s vs %s</td><td><b>%s</b></td><td>%s</td>"
        "<td>%s</td><td>%s</td><td>%s</td></tr>"
        % (p["num"], p["league"], p["home"], p["away"], p["predScore"],
           p["dirName"],
           ("%s:%s" % (p["modelPred"][0], p["modelPred"][1])) if p.get("modelPred") else "—",
           p["confWhy"],
           "<span class='pend'>%s</span>" % p["note"] if not p["settled"]
           else ("<span class='hit'>✓%s %s</span>" % ("精确" if p["exactHit"] else ("方向" if p["dirHit"] else "未中"), p["score"])
                 if p["exactHit"] or p["dirHit"] else "<span class='miss'>✗未中 %s</span>" % p["score"]))
        for p in t["picks"]) or "<tr><td colspan='8'>今日批次暂无高信心预测</td></tr>"

    notes = "".join("<li>%s</li>" % n for n in s["notes"])
    cmpn = s["comparison"]
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>V站高信心预测命中率统计 · %(date)s</title>
<style>
:root{--bg:#0b0f14;--panel:#11161d;--panel2:#151c26;--line:#242e3b;--text:#e6edf5;--sub:#8a99ab;--accent:#4da3ff;--up:#f0554d;--down:#28b26b;--gold:#e8b339}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;padding:18px;max-width:1040px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;color:var(--accent);border-left:3px solid var(--accent);padding-left:9px}
.meta{color:var(--sub);font-size:12px;margin-bottom:18px}
.hero{background:linear-gradient(135deg,#2a2113,#11161d);border:1px solid rgba(232,179,57,.35);border-radius:14px;padding:20px;display:flex;flex-wrap:wrap;gap:24px;align-items:center}
.big{font-size:42px;font-weight:800;letter-spacing:-1px;line-height:1;color:var(--gold)}.big small{font-size:14px;color:var(--sub);font-weight:500}
.kv{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--sub)}.kv b{font-size:18px;color:var(--text)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:14px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card .l{font-size:11px;color:var(--sub)}.card .v{font-size:19px;font-weight:700;margin-top:3px}
.card .c{font-size:11px;color:var(--sub);margin-top:3px}
.concl{background:rgba(232,179,57,.08);border:1px solid rgba(232,179,57,.3);border-radius:10px;padding:14px 16px;margin:14px 0;font-size:13px;line-height:1.85}
table{width:100%%;border-collapse:collapse;font-size:12px;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:8px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{background:var(--panel2);color:var(--sub);font-weight:600}tr:last-child td{border-bottom:none}
.up{color:var(--up)}.down{color:var(--down)}.pend{color:var(--sub)}.hit{color:var(--up);font-weight:700}.miss{color:var(--down);font-weight:700}
ul{font-size:12px;color:var(--sub);padding-left:20px}li{margin:5px 0}
footer{margin-top:26px;color:var(--sub);font-size:11px;border-top:1px solid var(--line);padding-top:12px}
.tag{display:inline-block;background:rgba(232,179,57,.15);color:var(--gold);border:1px solid rgba(232,179,57,.4);border-radius:6px;padding:1px 7px;font-size:11px;margin-left:6px}
</style></head><body>
<h1>V站 🔥高信心预测 · 比分与方向命中率统计</h1>
<div class="meta">版本 %(ver)s ｜ 生成 %(gen)s（北京时间）｜ 数据源：vzhan_history.json 赛前固化归档 + history.json 竞彩官方开奖</div>
<div class="hero"><div><div class="big">%(hotExact)s <small>高信心比分精确命中率</small></div>
<div class="kv" style="margin-top:8px">%(hotN)d 场 · 精确 %(hotExactH)d · 方向 %(hotDirH)d（%(hotDir)s）　|　方向 Wilson 95%%CI %(hotDirCI)s</div></div>
<div class="kv"><b>%(hotExactCI)s</b><span>比分精确 Wilson 95%% CI</span></div>
<div class="kv"><b>%(hotDirCI)s</b><span>方向命中 Wilson 95%% CI</span></div>
<div class="kv"><b class="%(hotVsCls)s">%(hotVsDir)s pt</b><span>方向相对独立随机基线</span></div></div>
<div class="grid">
<div class="card"><div class="l">高信心样本</div><div class="v">%(hotN)d 场</div><div class="c">可判定 %(canN)d 场中占 %(hotShare)s</div></div>
<div class="card"><div class="l">高信心比分精确</div><div class="v">%(hotExact)s</div><div class="c">基线 %(beHot)s ｜ 超额 %(hotVsExact)s pt</div></div>
<div class="card"><div class="l">高信心方向命中</div><div class="v">%(hotDir)s</div><div class="c">基线 %(bdHot)s ｜ 超额 %(hotVsDir)s pt</div></div>
<div class="card"><div class="l">普通组方向命中</div><div class="v">%(nDir)s</div><div class="c">n=%(nN)d ｜ 组间 Fisher p=%(fisher)s</div></div>
<div class="card"><div class="l">全量方向命中</div><div class="v">%(allDir)s</div><div class="c">%(allN)d 场已结算</div></div>
<div class="card"><div class="l">平均比分误差</div><div class="v">%(mae)s</div><div class="c">高信心组 主+客 绝对误差和</div></div>
</div>
<div class="concl"><b>结论</b><br>%(concl)s</div>
<h2>今日竞彩批次：<span class="tag">%(batchDate)s %(batchLabel)s 当天开售</span></h2>
<div class="meta">当天开出 %(todayTotal)d 场，V站 有比分预测 %(vzPred)d 场，其中 🔥高信心 %(highCount)d 场；已开奖 %(settled)d 场、未开奖 %(pending)d 场。严格按竞彩批次隔离，不与周六/周日混算。</div>
<table><thead><tr><th>编号</th><th>联赛</th><th>对阵</th><th>V站预测比分</th><th>预测方向</th><th>模型预测</th><th>高信心依据</th><th>结算状态</th></tr></thead><tbody>%(picks)s</tbody></table>
<h2>三组对照（高信心 / 普通 / 无法判定）</h2>%(groupTbl)s
<h2>时间窗口</h2>%(winTbl)s
<h2>按联赛分层（样本≥3）</h2>%(leagueTbl)s
<h2>近 30 日逐日</h2>%(dateTbl)s
<h2>统计口径（可逐条核对）</h2><ul>%(notes)s</ul>
<footer>本统计为历史回溯描述，不承诺盈利、不构成投注建议。页面端同源展示于「V站·呼死狗情报」分区。</footer>
</body></html>""" % {
        "date": t["batchDate"], "ver": s["version"], "gen": s["generatedAt"],
        "hotExact": P(hot["exactRate"]), "hotExactH": hot["exactHit"],
        "hotExactCI": "%s ~ %s" % (P(hot["exactWilson95"][0]), P(hot["exactWilson95"][1])),
        "hotN": hot["n"], "hotDirH": hot["dirHit"], "hotDir": P(hot["dirRate"]),
        "hotDirCI": "%s ~ %s" % (P(hot["dirWilson95"][0]), P(hot["dirWilson95"][1])),
        "hotVsCls": CC(hot.get("dirVsBaseline")), "hotVsDir": SG(hot.get("dirVsBaseline")),
        "hotVsExact": SG(hot.get("exactVsBaseline")),
        "canN": hot["n"] + normal["n"], "hotShare": P(cmpn.get("hotShare")),
        "beHot": P(hot.get("exactBaseline")), "bdHot": P(hot.get("dirBaseline")),
        "nDir": P(normal["dirRate"]), "nN": normal["n"],
        "fisher": ("%.4f" % cmpn["dirFisher"]) if isinstance(cmpn.get("dirFisher"), (int, float)) else "—",
        "allDir": P(o["dirRate"]), "allN": o["n"], "mae": ("%.2f" % hot["mae"]) if hot["mae"] is not None else "—",
        "concl": s["conclusion"], "batchDate": t["batchDate"], "batchLabel": t["batchLabel"],
        "todayTotal": t["totalMatches"], "vzPred": t["vzPredCount"], "highCount": t["highCount"],
        "settled": t["settledCount"], "pending": t["pendingCount"], "picks": picks,
        "groupTbl": block_table(s["byGroup"], "分组"), "winTbl": block_table(s["windows"], "窗口"),
        "leagueTbl": block_table(s["byLeague"], "联赛") if s["byLeague"] else "<div class='meta'>暂无样本≥3的联赛</div>",
        "dateTbl": block_table(s["byDate"], "日期"), "notes": notes,
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    VH = rd("vzhan_history.json")
    if not isinstance(VH, dict) or not (VH.get("matches") or {}):
        print("! vzhan_history.json 无归档记录，跳过统计（保留旧数据）", flush=True)
        return 0
    s = make_stats()
    if s["overall"]["n"] == 0:
        print("! 无已结算 V站 样本，跳过统计", flush=True)
        return 0

    p = os.path.join(HERE, "vz_high_stats.json")
    prev = rd("vz_high_stats.json")
    if not prev or signature(prev) != signature(s):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        print("✓ vz_high_stats.json 已更新", flush=True)
    else:
        print("· 统计无变化，vz_high_stats.json 未改写", flush=True)

    M = rd("matches.json")
    if isinstance(M, dict):
        old = M.get("vzHighStats")
        if not old or signature(old) != signature(s):
            M["vzHighStats"] = s
            tmp = os.path.join(HERE, "matches.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(M, f, ensure_ascii=False, indent=1)
            os.replace(tmp, os.path.join(HERE, "matches.json"))
            print("✓ matches.json.vzHighStats 已更新", flush=True)
        else:
            print("· 统计无变化，matches.json 未改写", flush=True)

    rp = os.path.join(HERE, "..", "V站高信心预测命中率统计.html")
    try:
        write_report(s, rp)
        print("✓ 报告：%s" % os.path.abspath(rp), flush=True)
    except Exception as e:
        print("! 报告生成失败（不影响数据）：%s" % e, flush=True)

    g = s["groups"]["high"]
    print("  🔥高信心 %d 场：比分精确 %.2f%%（%d）| 方向命中 %.2f%%（%d）| 基线 比分 %.2f%% / 方向 %.2f%%"
          % (g["n"], g["exactRate"] or 0, g["exactHit"], g["dirRate"] or 0, g["dirHit"],
             g.get("exactBaseline") or 0, g.get("dirBaseline") or 0), flush=True)
    print("  今日 %s 批次：竞彩 %d 场 / V站预测 %d 场 / 高信心 %d 场（已结算 %d）"
          % (s["today"]["batchDate"], s["today"]["totalMatches"],
             s["today"]["vzPredCount"], s["today"]["highCount"],
             s["today"]["settledCount"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
