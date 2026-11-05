#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""篮球 AI 预测 + 历史统计（v1.7.44 新增，独立源，与足球零耦合）。

数据源（由 fetch_basketball.py 维护，绝不碰足球的 matches.json/history.json）：
  - basketball.json          在售赛程 + 四玩法赔率
  - basketball_history.json  赛后累积档案（含 score / hdcResult / hiloResult）

产出：
  - basketball_ai.json       当日四玩法预测（供 fetch_basketball_parlay.py 串关用）
  - basketball_stats.json    历史统计（联赛胜率 / 盘路命中 / 胜分差分布 / ROI）

设计原则（对齐足球独立源接入模式）：
  - 失败只跳过、exit 0，不拖垮 run_daily.sh 主流程（篮球隔离设计）
  - 与足球数据/模型零耦合：不读 matches.json / history.json / BUILT_DATA
  - 同指命中率沿用足球 n<5 护栏 → 样本薄时自动退化为市场共识（防 v1.7.43 反向虚高）
"""

import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BK_PATH = os.path.join(HERE, "basketball.json")
BKH_PATH = os.path.join(HERE, "basketball_history.json")
AI_PATH = os.path.join(HERE, "basketball_ai.json")
STATS_PATH = os.path.join(HERE, "basketball_stats.json")
# 预测回溯库（对齐足球 score_predict_accuracy.json）：记录每日四玩法预测方向，
# 赛后用 basketball_history 赛果结算命中率（v1.7.46 新增）。
ACC_PATH = os.path.join(HERE, "basketball_ai_accuracy.json")

# 市场共识参数（对标 fetch_daily._leg_prob：竞彩综合返奖率经验值；篮球首版沿用，待实测校准）
PAYBACK = 0.886
ALPHA_SHRINK = 0.60

# 胜分差区间边界（主胜分差，单位分）：l1[1,5] l2[6,10] l3[11,15] l4[16,20] l5[21,25] l6[26,+)
WNM_BOUNDS = [("l1", 1, 5), ("l2", 6, 10), ("l3", 11, 15),
              ("l4", 16, 20), ("l5", 21, 25), ("l6", 26, 10 ** 9)]

SAMPLE_WARN = "样本薄（仅数十场历史赛果），同指命中率统计仅供参考，预测以市场共识为主"
FAKE_FROM = "2026-11-02"   # 篮球停更起始日：此后用 11-01 旧比赛重复生成的预测一律剔除


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _num(s):
    """解析排名串数字：'德篮联13' -> 13；'' -> None。"""
    if not s:
        return None
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else None


def _play_sides(play):
    """返回玩法支持的方向键（h/a/l/l1..l6）。"""
    if not isinstance(play, dict):
        return []
    sides = [s for s in ("h", "a", "l") if s in play]
    sides += [k for k in play if re.fullmatch(r"l\d+", k)]
    return sides


def _market_prob(play, side):
    """给定玩法赔率 dict 与 side，返回该边归一化市场隐含概率。"""
    sides = _play_sides(play)
    if side not in sides:
        return None
    inv = []
    for s in sides:
        o = _f(play.get(s))
        if o > 1:
            inv.append(1.0 / o)
    tot = sum(inv)
    if tot <= 0:
        return None
    o = _f(play.get(side))
    return (1.0 / o) / tot if o > 1 else None


def _leg_prob(o, pt=None):
    """市场共识 + alpha 收缩口径（对标 fetch_daily._leg_prob）。"""
    if not o or o <= 1:
        return None
    p_market = PAYBACK / o
    if pt is not None:
        p = p_market + (pt / 100.0 - p_market) * ALPHA_SHRINK
        p = min(max(p, p_market - 0.06), p_market + 0.06)
    else:
        p = p_market
    return min(max(p, 0.01), 0.95)


# ----------------------------------------------------------------------------
# 历史同指命中率（四玩法通用，n<5 护栏）
# ----------------------------------------------------------------------------
def calc_bk_same_odds_signal(records, play_key, direction, goal=None, tol=0.12):
    """篮球历史同指命中率。

    records   : basketball_history.json 的 records（dict: id->rec）
    play_key  : 'mnl' | 'hdc' | 'hilo' | 'wnm'
    direction : mnl/hdc -> 'h'/'a'；hilo -> 'h'(大)/'l'(小)；wnm -> 'l1'..'l6'
    goal      : hdc/hilo 需匹配盘口（goalLine）；mnl/wnm 传 None
    返回 (hit_rate_or_None, n)：n<5 返回 (None, n) —— 薄样本护栏
    """
    hits = 0
    n = 0
    for rec in records.values():
        sc = rec.get("score")
        if not isinstance(sc, list) or len(sc) != 2:
            continue
        home, away = sc[0], sc[1]
        play = rec.get(play_key)
        if not isinstance(play, dict):
            continue
        if play_key in ("hdc", "hilo"):
            gl = str(play.get("goalLine", "")).strip()
            if goal is None or gl != str(goal).strip():
                continue
        if play_key == "mnl":
            actual = "h" if home > away else "a"
            hit = (actual == direction)
        elif play_key == "hdc":
            hr = rec.get("hdcResult")
            if hr not in ("h", "a", "d"):
                adj = home + _f(play.get("goalLine"))
                hr = "h" if adj > away else ("a" if adj < away else "d")
            if hr == "d":
                continue  # 走水不计
            hit = (hr == direction)
        elif play_key == "hilo":
            ir = rec.get("hiloResult")
            if ir not in ("h", "l"):
                ir = "h" if (home + away) > _f(play.get("goalLine")) else "l"
            hit = (ir == direction)
        elif play_key == "wnm":
            diff = home - away
            if diff <= 0:
                continue  # 主负，所有胜分差区间不中，不入桶
            actual_lx = next((lx for lx, lo, hi in WNM_BOUNDS if lo <= diff <= hi), None)
            if actual_lx is None:
                continue
            hit = (actual_lx == direction)
        else:
            continue
        n += 1
        if hit:
            hits += 1
    if n < 5:
        return (None, n)
    return (round(hits / n * 100, 1), n)


# ----------------------------------------------------------------------------
# 选腿评分（对标足球 parlay_canon_score：信心主导 + 同指分段修正）
# ----------------------------------------------------------------------------
def bk_canon_score(opt):
    """篮球选腿评分：信心度主导；同指仅 ≥65 加成，45-60 反向惩罚，<45 重罚。"""
    conf = opt.get("confidence") or 0
    so, n_ = opt.get("sameOddsHit"), opt.get("sameOddsN") or 0
    base = conf / 100.0
    so_adj = 0.0
    if so is not None and n_ >= 10:
        if so >= 65:
            so_adj = min(0.08, (so - 65) / 100.0)
        elif so >= 45:
            so_adj = -0.10
        else:
            so_adj = -0.15
    o = opt.get("odds") or 0
    ev = 0.0
    if o > 1:
        p = _leg_prob(o, opt.get("pTrue"))
        if p:
            ev = min(max(p, 0.01), 0.95) * o - 1.0
    return base + so_adj + 0.10 * ev + 0.001 * (opt.get("fiveDim") or 0)


def _rule_confidence(play_key, side, odds, p_market):
    """简化规则信心：以市场隐含概率为主，胜分差区间玩法天然压低。"""
    if not p_market:
        base = 60.0
    else:
        base = p_market * 100 * 0.95
    if play_key == "wnm":
        base = min(base, 70.0)  # 胜分差区间博弈，信心上限更低
    return round(min(88.0, max(55.0, base)), 1)


# ----------------------------------------------------------------------------
# 球队评分模型（v1.7.99 升级）：Elo 评分 + 近期状态 + 联赛主场优势
# 用 basketball_history.json 的历史赛果建每队实力，替代原来「仅市场概率」的弱信号。
# ----------------------------------------------------------------------------
def build_team_model(records):
    """返回 {rating:{队:分}, home_adv:{联赛:评分差}, form:{队:{n,recent_margin,recent_wr}}}。"""
    games = []
    for r in records.values():
        sc = r.get("score")
        if isinstance(sc, list) and len(sc) == 2:
            h, a = sc[0], sc[1]
            games.append({"home": r.get("home"), "away": r.get("away"),
                           "league": r.get("league"), "margin": h - a})
    rating = {}
    def get(t):
        return rating.setdefault(t, 1500.0)
    # 联赛主场优势（历史主胜率 -> Elo 评分差）
    lg_home = defaultdict(lambda: [0, 0])
    for g in games:
        lg_home[g["league"]][0] += 1
        if g["margin"] > 0:
            lg_home[g["league"]][1] += 1
    HOME_ADV = {}
    for lg, (n, w) in lg_home.items():
        if n >= 3 and 0 < w < n:
            p = w / n
            HOME_ADV[lg] = 400.0 * math.log10(p / (1.0 - p))
        else:
            HOME_ADV[lg] = 40.0  # 默认约55%主场
    # Elo 更新（margin-aware）
    for g in games:
        rh = get(g["home"]); ra = get(g["away"])
        H = HOME_ADV.get(g["league"], 40.0)
        eh = 1.0 / (1.0 + 10 ** ((ra - (rh + H)) / 400.0))
        margin = g["margin"]
        K = 24.0 + min(36.0, abs(margin))
        sh = 1.0 if margin > 0 else 0.0
        rh += K * (sh - eh)
        ra += K * ((1.0 - sh) - (1.0 - eh))
        rating[g["home"]] = rh; rating[g["away"]] = ra
    # 近期状态（近5场净胜分/胜率）
    form = defaultdict(list)
    for g in games:
        form[g["home"]].append(g["margin"])
        form[g["away"]].append(-g["margin"])
    form_stat = {}
    for t, ms in form.items():
        last = ms[-5:]
        form_stat[t] = {
            "n": len(ms),
            "recent_margin": round(sum(last) / len(last), 1) if last else 0.0,
            "recent_wr": round(sum(1 for x in last if x > 0) / len(last) * 100, 1) if last else 50.0,
        }
    return {"rating": rating, "home_adv": HOME_ADV, "form": form_stat}


def _blend(market_p, model_p, w_mkt=0.6):
    """市场通常更准，权重0.6；模型提供独立信号。"""
    if market_p is None:
        return model_p
    return w_mkt * market_p + (1.0 - w_mkt) * model_p


def model_mnl_prob(home, away, league, model, market_p):
    """胜负：返回 (纯模型主胜率, 融合市场后的校准主胜率)。"""
    R = model["rating"]; H = model["home_adv"].get(league, 40.0)
    rh = R.get(home, 1500.0); ra = R.get(away, 1500.0)
    fh = (model["form"].get(home, {})).get("recent_margin", 0) or 0.0
    fa = (model["form"].get(away, {})).get("recent_margin", 0) or 0.0
    adj = (fh - fa) * 0.8
    eh = 1.0 / (1.0 + 10 ** ((ra - (rh + H + adj)) / 400.0))
    return eh, _blend(market_p, eh)


def model_hdc_prob(home, away, league, model, line, side, market_p):
    """让分：模型预测主净胜分 vs 盘口 line，返回该 side(h/a)覆盖概率(校准)。"""
    if line is None:
        return market_p
    R = model["rating"]; H = model["home_adv"].get(league, 40.0)
    rh = R.get(home, 1500.0); ra = R.get(away, 1500.0)
    fh = (model["form"].get(home, {})).get("recent_margin", 0) or 0.0
    fa = (model["form"].get(away, {})).get("recent_margin", 0) or 0.0
    pred_margin = (rh + H + fh * 0.8 - (ra + fa * 0.8)) / 8.0
    p_home_cover = 1.0 / (1.0 + math.exp(-(pred_margin - line) / 7.0))
    model_p = p_home_cover if side == "h" else (1.0 - p_home_cover)
    return _blend(market_p, model_p)


def model_hilo_prob(home, away, league, model, line, side, market_p, base_total=200.0):
    """大小分：模型预测总分 vs 盘口 line，返回该 side(h=大/l=小)概率(校准)。"""
    if line is None:
        return market_p
    R = model["rating"]; H = model["home_adv"].get(league, 40.0)
    rh = R.get(home, 1500.0); ra = R.get(away, 1500.0)
    fh = (model["form"].get(home, {})).get("recent_margin", 0) or 0.0
    fa = (model["form"].get(away, {})).get("recent_margin", 0) or 0.0
    pred_margin = (rh + H + fh * 0.8 - (ra + fa * 0.8)) / 8.0
    pred_total = base_total + pred_margin * 0.55
    p_over = 1.0 / (1.0 + math.exp(-(pred_total - line) / 13.0))
    model_p = p_over if side == "h" else (1.0 - p_over)
    return _blend(market_p, model_p)


# ----------------------------------------------------------------------------
# 单场单玩法预测
# ----------------------------------------------------------------------------
_LEAGUE_BASE_TOTAL = {"美职篮": 225.0, "美职女篮": 155.0, "欧篮联": 168.0,
                      "德篮联": 165.0, "澳篮联": 175.0, "西篮联": 165.0,
                      "土篮超": 165.0, "以篮超": 165.0, "韩篮甲": 160.0,
                      "NBL": 175.0, "ACB": 165.0, "EuroLeague": 168.0, "NBA": 225.0}


def _predict_play(play_key, play, records, model, home, away, league):
    if not isinstance(play, dict):
        return None
    sides = {"mnl": ["h", "a"], "hdc": ["h", "a"],
             "hilo": ["h", "l"], "wnm": ["l1", "l2", "l3", "l4", "l5", "l6"]}.get(play_key)
    if not sides:
        return None
    goal = play.get("goalLine") if play_key in ("hdc", "hilo") else None
    try:
        line = float(goal) if goal not in (None, "") else None
    except (TypeError, ValueError):
        line = None
    opts = []
    for s in sides:
        o = _f(play.get(s))
        if o <= 1:
            continue
        pm = _market_prob(play, s)            # 市场隐含该边概率
        so, sn = calc_bk_same_odds_signal(records, play_key, s, goal=goal)
        # 模型校准概率（胜负/让分/大小分接评分模型；胜分差区间沿用市场）
        if play_key == "mnl":
            _, cal = model_mnl_prob(home, away, league, model, pm)
        elif play_key == "hdc":
            cal = model_hdc_prob(home, away, league, model, line, s, pm)
        elif play_key == "hilo":
            cal = model_hilo_prob(home, away, league, model, line, s, pm,
                                  _LEAGUE_BASE_TOTAL.get(league, 200.0))
        else:
            cal = pm
        cal = None if cal is None else min(max(cal, 0.02), 0.98)
        pTrue = round(cal * 100, 1) if cal is not None else None
        base_p = cal if cal is not None else pm
        conf = round(min(92.0, max(50.0, (base_p or 0.55) * 100)), 1)
        edge = (cal - pm) if (cal is not None and pm is not None) else 0.0
        opts.append({"dir": s, "odds": o, "pMarket": pm, "confidence": conf,
                     "sameOddsHit": so, "sameOddsN": sn, "pTrue": pTrue,
                     "edge": round(edge * 100, 1)})
    if not opts:
        return None
    for op in opts:
        op["canon"] = bk_canon_score(op) + 0.02 * (op.get("edge") or 0) * 100
    best = max(opts, key=lambda x: x["canon"])
    alts = [{"dir": o["dir"], "odds": o["odds"], "confidence": o["confidence"],
             "sameOddsHit": o["sameOddsHit"], "sameOddsN": o["sameOddsN"]}
            for o in opts if o is not best]
    return {
        "dir": best["dir"], "odds": best["odds"], "confidence": best["confidence"],
        "sameOddsHit": best["sameOddsHit"], "sameOddsN": best["sameOddsN"],
        "pTrue": best["pTrue"], "canon": round(best["canon"], 3),
        "edge": best.get("edge"), "goalLine": play.get("goalLine"),
        "alts": alts,
    }


def gen_bk_prediction(matches, records):
    """对每场在售比赛产出四玩法预测；每场取 edge(模型相对市场的优势)最大的玩法方向。

    v1.7.99 升级：胜负/让分/大小分接入球队 Elo 评分+近期状态+主场优势模型，
    只输出模型概率真高于市场（正 edge=正 EV）的方向，信心按校准胜率给，不再恒 55%。
    """
    model = build_team_model(records)
    preds = []
    for m in matches:
        home = m.get("home"); away = m.get("away"); league = m.get("league")
        hr_n = _num(m.get("hr")); ar_n = _num(m.get("ar"))
        rank_bonus = 0.0
        if hr_n is not None and ar_n is not None and hr_n > 0 and ar_n > 0:
            diff = ar_n - hr_n  # 正=主排名更前（更强）
            rank_bonus = min(8.0, max(-8.0, diff * 0.6))
        plays = {}
        for pk in ("mnl", "hdc", "hilo", "wnm"):
            p = _predict_play(pk, m.get(pk), records, model, home, away, league)
            if p:
                p["confidence"] = round(min(92.0, max(50.0, p["confidence"] + rank_bonus)), 1)
                p["canon"] = round(bk_canon_score(p) + 0.02 * (p.get("edge") or 0) * 100, 3)
                plays[pk] = p
        if not plays:
            continue
        # 单场单意见：取校准胜率最高（模型+市场共识的最强热门）的玩法方向。
        # 回测已证：竞彩篮球热门被高估（隐含65%强队真实仅中31%），模型无法跑赢市场，
        # 故只取共识最强侧作为最优选，不追模型反向 edge（那会主动选到更差的冷门）。
        # v1.7.119：胜分差六区间回测命中率仅约 5%，绝不作为主推（串关因此不再
        # 把极难命中的长盘腿塞进推荐）；四个玩法预测仍完整展示供参考。
        stable = {k: v for k, v in plays.items() if k != "wnm"} or plays
        top_key = max(stable, key=lambda k: (stable[k].get("confidence") or 0))
        preds.append({
            "id": m.get("id"), "num": m.get("num"), "league": m.get("league"),
            "home": m.get("home"), "away": m.get("away"), "kickoff": m.get("kickoff"),
            "hr": m.get("hr"), "ar": m.get("ar"),
            "plays": plays, "topPlay": top_key,
        })
    return preds


# ----------------------------------------------------------------------------
# 历史统计
# ----------------------------------------------------------------------------
def compute_bk_stats(records):
    """基于赛后档案计算历史统计（联赛胜率/盘路/胜分差分布/ROI）。"""
    scored = [r for r in records.values()
              if isinstance(r.get("score"), list) and len(r["score"]) == 2]
    total = len(records)
    m = len(scored)

    leagues = {}
    hdc_counter = Counter()
    hilo_counter = Counter()
    wnm_dist = Counter()
    home_wins = 0
    roi = defaultdict(lambda: {"bet": 0, "profit": 0.0})

    for r in scored:
        home, away = r["score"][0], r["score"][1]
        lg = r.get("league", "未知")
        lg_d = leagues.setdefault(lg, {"n": 0, "home_win": 0, "hdc_h": 0, "hdc_n": 0,
                                       "hilo_over": 0, "hilo_n": 0, "totals": []})
        lg_d["n"] += 1
        if home > away:
            home_wins += 1
            lg_d["home_win"] += 1
        lg_d["totals"].append(home + away)

        # 让分盘路
        hdc = r.get("hdc")
        hr = r.get("hdcResult")
        if isinstance(hdc, dict) and hr in ("h", "a", "d"):
            hdc_counter[hr] += 1
            if hr != "d":
                lg_d["hdc_n"] += 1
                if hr == "h":
                    lg_d["hdc_h"] += 1
            # ROI：买市场概率高的一方
            _roi_accum(roi, "hdc", hdc, hr)

        # 大小分盘路
        hilo = r.get("hilo")
        ir = r.get("hiloResult")
        if isinstance(hilo, dict) and ir in ("h", "l"):
            hilo_counter[ir] += 1
            lg_d["hilo_n"] += 1
            if ir == "h":
                lg_d["hilo_over"] += 1
            _roi_accum(roi, "hilo", hilo, ir)

        # 胜负 ROI
        mnl = r.get("mnl")
        if isinstance(mnl, dict):
            res = "h" if home > away else "a"
            _roi_accum(roi, "mnl", mnl, res)

        # 胜分差分布
        diff = home - away
        if diff > 0:
            lx = next((x for x, lo, hi in WNM_BOUNDS if lo <= diff <= hi), None)
            if lx:
                wnm_dist[lx] += 1
                _roi_accum_wnm(roi, r.get("wnm"), lx)

    # 整理 league 统计
    league_out = {}
    for lg, d in leagues.items():
        n = d["n"]
        league_out[lg] = {
            "n": n,
            "home_win_rate": round(d["home_win"] / n * 100, 1) if n else 0,
            "hdc_home_hit_rate": round(d["hdc_h"] / d["hdc_n"] * 100, 1) if d["hdc_n"] else None,
            "hilo_over_rate": round(d["hilo_over"] / d["hilo_n"] * 100, 1) if d["hilo_n"] else None,
            "avg_total": round(sum(d["totals"]) / len(d["totals"]), 1) if d["totals"] else None,
        }

    hdc_total = sum(hdc_counter.values())
    hilo_total = sum(hilo_counter.values())
    roi_out = {}
    for k, v in roi.items():
        bet = v["bet"]
        roi_out[k] = {
            "bet": bet,
            "profit": round(v["profit"], 1),
            "roi": round(v["profit"] / bet * 100, 1) if bet else 0,
        }

    return {
        "sample": {"total": total, "scored": m, "note": SAMPLE_WARN},
        "leagues": league_out,
        "hdc_overall": {
            "h": hdc_counter.get("h", 0), "a": hdc_counter.get("a", 0),
            "d": hdc_counter.get("d", 0), "home_hit_rate":
                round(hdc_counter.get("h", 0) / (hdc_counter.get("h", 0) + hdc_counter.get("a", 0)) * 100, 1)
                if (hdc_counter.get("h", 0) + hdc_counter.get("a", 0)) else None,
        },
        "hilo_overall": {
            "over": hilo_counter.get("h", 0), "under": hilo_counter.get("l", 0),
            "over_rate":
                round(hilo_counter.get("h", 0) / (hilo_counter.get("h", 0) + hilo_counter.get("l", 0)) * 100, 1)
                if (hilo_counter.get("h", 0) + hilo_counter.get("l", 0)) else None,
        },
        "wnm_dist": {lx: wnm_dist.get(lx, 0) for lx, _, _ in WNM_BOUNDS},
        "home_advantage": {
            "home_win_rate": round(home_wins / m * 100, 1) if m else 0,
        },
        "roi": roi_out,
    }


def _roi_accum(roi, key, play, result):
    """买该玩法「市场概率最高方」，命中 +odds-1，不中 -1。"""
    if not isinstance(play, dict):
        return
    sides = _play_sides(play)
    best, best_p = None, -1
    for s in sides:
        p = _market_prob(play, s)
        if p is not None and p > best_p:
            best, best_p = s, p
    if best is None:
        return
    o = _f(play.get(best))
    roi[key]["bet"] += 1
    roi[key]["profit"] += (o - 1) if result == best else -1.0


def _roi_accum_wnm(roi, wnm, actual_lx):
    if not isinstance(wnm, dict):
        return
    best, best_p = None, -1
    for s in ("l1", "l2", "l3", "l4", "l5", "l6"):
        p = _market_prob(wnm, s)
        if p is not None and p > best_p:
            best, best_p = s, p
    if best is None:
        return
    o = _f(wnm.get(best))
    roi["wnm"]["bet"] += 1
    roi["wnm"]["profit"] += (o - 1) if actual_lx == best else -1.0


# ----------------------------------------------------------------------------
# 预测回溯（历史命中率，对齐足球 score_predict_accuracy）
# ----------------------------------------------------------------------------
def _play_hit(play_key, direction, goal_line, score):
    """单玩法预测方向 vs 实际赛果：返回命中布尔；走水/无法判定返回 None。

    score = [home, away]；goal_line 为预测时的盘口（hdc/hilo 需要）。
    """
    home, away = score[0], score[1]
    try:
        gl = float(goal_line) if goal_line not in (None, "") else None
    except (TypeError, ValueError):
        gl = None
    if play_key == "mnl":
        actual = "h" if home > away else "a"
        return actual == direction
    if play_key == "hdc":
        if gl is None:
            return None
        adj = home + gl
        if adj > away:
            actual = "h"
        elif adj < away:
            actual = "a"
        else:
            return None  # 走水不计
        return actual == direction
    if play_key == "hilo":
        if gl is None:
            return None
        actual = "h" if (home + away) > gl else "l"
        return actual == direction
    if play_key == "wnm":
        diff = home - away
        if diff <= 0:
            return False  # 主负，所有胜分差区间不中
        lx = next((lx for lx, lo, hi in WNM_BOUNDS if lo <= diff <= hi), None)
        return (lx == direction) if lx else False
    return None


def record_bk_accuracy(preds, date):
    """把当日预测写入 basketball_ai_accuracy.json（按 date+id 去重，避免每日重跑重复落盘）。"""
    acc = _load(ACC_PATH, {"history": []})
    hist = acc.get("history") or []
    seen = {(h.get("date"), h.get("id")) for h in hist}
    added = 0
    for p in preds:
        key = (date, p.get("id"))
        if key in seen:
            continue
        plays = {}
        for pk, pl in (p.get("plays") or {}).items():
            plays[pk] = {
                "dir": pl.get("dir"),
                "conf": pl.get("confidence"),
                "gl": pl.get("goalLine"),
                "hit": None,
            }
        tp = p.get("topPlay")
        tpl = (p.get("plays") or {}).get(tp, {})
        hist.append({
            "date": date, "id": p.get("id"), "num": p.get("num"),
            "league": p.get("league"), "home": p.get("home"), "away": p.get("away"),
            "topPlay": tp, "topDir": tpl.get("dir"), "topConf": tpl.get("confidence"),
            "plays": plays, "topHit": None, "score": None,
        })
        seen.add(key)
        added += 1
    acc["history"] = hist
    acc["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save(ACC_PATH, acc)
    return added


def settle_bk_accuracy(bk_records):
    """用赛后档案结算回溯记录（回填每个玩法的 hit / topHit / score）。"""
    acc = _load(ACC_PATH, {"history": []})
    hist = acc.get("history") or []
    changed = 0
    for h in hist:
        if h.get("score") is not None:
            continue
        rec = bk_records.get(h.get("id"))
        if not rec or not isinstance(rec.get("score"), list) or len(rec["score"]) != 2:
            continue
        score = rec["score"]
        h["score"] = "%d:%d" % (score[0], score[1])
        for pk, pl in (h.get("plays") or {}).items():
            pl["hit"] = _play_hit(pk, pl.get("dir"), pl.get("gl"), score)
        tp = h.get("topPlay")
        tpl = (h.get("plays") or {}).get(tp)
        h["topHit"] = tpl.get("hit") if tpl else None
        changed += 1
    acc["history"] = hist
    acc["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save(ACC_PATH, acc)
    return changed


def seed_bk_accuracy(bk_records):
    """回测播种回溯库：对每场已赛档案按当前模型跑预测并结算（档案本身含当时赔率+赛果）。
    仅处理 date 早于今日的真实历史，按 (date,id) 去重、幂等；让回溯库上线即有覆盖，
    对齐项目既有 backfill 惯例（backfill_*.py / ensure_history.py）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    matches = [r for r in bk_records.values()
               if isinstance(r.get("score"), list) and len(r["score"]) == 2
               and (r.get("date") or "") < today]
    preds = gen_bk_prediction(matches, bk_records)
    added = 0
    for p in preds:
        d = (bk_records.get(p.get("id")) or {}).get("date") or p.get("date")
        added += record_bk_accuracy([p], d)
    return added


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def _purge_stale_accuracy(bk_records):
    """剔除停更期「拿 11-01 已结束比赛反复冒充当日预测」的重复回测样本。

    判定：记录日期 >= 停更日，但该场比赛所属销售批次早于停更日。
    幂等：seed_bk_accuracy 之后按真实批次日补回的样本不会被误删。
    """
    acc = _load(ACC_PATH, {"history": []})
    h = acc.get("history") or []
    keep, removed = [], 0
    for x in h:
        d = x.get("date") or ""
        bd = (bk_records.get(x.get("id")) or {}).get("date") or ""
        if d >= FAKE_FROM and bd and bd < FAKE_FROM:
            removed += 1
            continue
        keep.append(x)
    if removed:
        acc["history"] = keep
        acc["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save(ACC_PATH, acc)
    return removed


def main():
    bk = _load(BK_PATH, {"matches": []})
    hist = _load(BKH_PATH, {"records": {}})
    records = hist.get("records") or {}
    all_matches = bk.get("matches") or []
    purged = _purge_stale_accuracy(records)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # ---- 新鲜度门禁（v1.7.119）----
    # 2026-11-02~11-05 篮球官方接口海外 567，流水线连续 4 天拿 11-01 的
    # 5 场旧比赛反复生成「今日预测」和串关，页面上看着天天有推荐，其实全是
    # 已经打完 3 天的比赛。门禁：数据批次必须新鲜、且只预测尚未开赛的比赛；
    # 无有效比赛时保留上次 AI 文件、不再写新预测，也绝不往回溯库塞假样本。
    data_date = bk.get("dataDate") or ""
    data_fresh = (bool(data_date)
                  and data_date >= (now - timedelta(days=1)).strftime("%Y-%m-%d"))
    now_s = now.strftime("%Y-%m-%d %H:%M")

    def _upcoming(m):
        if m.get("score"):
            return False
        k = (m.get("kickoff") or "")[:16]
        return bool(k) and k > now_s

    matches = [m for m in all_matches if _upcoming(m)] if data_fresh else []

    if not matches:
        stats = compute_bk_stats(records)
        stats["updatedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _save(STATS_PATH, stats)
        seeded = seed_bk_accuracy(records)
        settled = settle_bk_accuracy(records)
        print("[篮球AI] 门禁生效：批次 %s（新鲜=%s）/ 在售未开赛 0 场，"
              "保留上次预测，不生成假推荐 | 统计更新 | 清理假样本 %d 条 | 回溯播种 %d / 结算 %d"
              % (data_date or "无", data_fresh, purged, seeded, settled))
        return 0

    preds = gen_bk_prediction(matches, records)
    ai = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "dataDate": data_date,
        "source": bk.get("source") or "",
        "upcomingCount": len(matches),
        "note": SAMPLE_WARN,
        "predictions": preds,
    }
    _save(AI_PATH, ai)

    stats = compute_bk_stats(records)
    stats["updatedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    _save(STATS_PATH, stats)

    # 预测回溯：记录当日预测 + 回测播种历史 + 用赛果档案结算（v1.7.46）
    added = record_bk_accuracy(preds, today)
    seeded = seed_bk_accuracy(records)
    settled = settle_bk_accuracy(records)

    print("[篮球AI] 预测 %d 场（四玩法，未开赛）/ 批次 %s / 源 %s | 统计基于 %d 条档案（%d 有赛果）"
          % (len(preds), data_date, ai["source"], len(records),
             sum(1 for r in records.values() if r.get("score"))))
    print("[篮球AI回溯] 当日新增 %d / 清理假样本 %d / 历史播种 %d / 结算 %d（累计 %d 条）"
          % (added, purged, seeded, settled,
             len((_load(ACC_PATH, {"history": []})).get("history") or [])))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[篮球AI] 异常跳过（隔离设计）:", e, file=sys.stderr)
        sys.exit(0)
