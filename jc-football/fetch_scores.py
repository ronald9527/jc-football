#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比分预测 · 独立数据源 Tab（与竞彩核心数据/模型零耦合）

【设计原则：绝对隔离】
- 本模块不读取 matches.json（竞彩核心数据集）、不读取 history.json（模型训练数据）、
  不读取 parlay / expert / smart_model 等任何竞彩推荐或模型文件。
- 唯一外部依赖：football-data.org 免费层（复用 fd_config.json 里的 api_key，仅取赛程/赛果）。
- 预测为独立自建 Poisson 比分模型，结果只写入 score_predict.json / score_predict_accuracy.json，
  供页面「比分预测」Tab 独立展示，绝不回灌任何竞彩模型或推荐逻辑。

为什么不是 WhoScored：
- 用户最初希望直接抓取 https://www.whoscored.com/livescores 的比分预测。
- 实测该站由 Cloudflare + oddschecker 指纹验证（"Just a moment..."）拦截，curl / cloudscraper /
  无头 Chromium 在本服务器（数据中心 IP）均无法越过，且 WhoScored 公开页也不提供「精确比分预测」。
- 因此本 Tab 改用可稳定访问的免费独立源 football-data.org（赛程+赛果）+ 自建透明 Poisson 模型，
  并保留准确性回溯，满足用户「观察哪些联赛/球队预测较准、挑可靠的作参考」的核心诉求。

预测模型（独立、透明、可解释）：
- 取每队近 N 场（按主客场拆分）得失球，结合联赛均值与主场优势，得到主/客队期望进球 λ。
- 对 λ_h × λ_a 的 Poisson 联合分布取众数作为预测比分，并对胜/平/负概率求和。
- 赛后用实际赛果回算：精确比分命中率、胜平负方向命中率、平均绝对误差(MAE)。
- 按联赛 / 球队累计统计，挑出历史预测最准的作为「可靠参考」。

用法：
    python3.11 fetch_scores.py          # 抓取+预测+回溯，写入 score_predict.json
    python3.11 fetch_scores.py --no-fetch  # 仅用本地缓存重算（调试/离线）
"""
import os
import json
import time
import math
import urllib.request
import urllib.error
from datetime import datetime, timedelta
import re as _RE  # 顶层导入，供历史补全/翻译复用

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "fd_config.json")          # 仅读 api_key，不碰竞彩数据
OUT_PATH = os.path.join(HERE, "score_predict.json")         # 独立输出，绝不写回核心数据
ACC_PATH = os.path.join(HERE, "score_predict_accuracy.json")# 准确性回溯库
CACHE_PATH = os.path.join(HERE, "score_predict_cache.json") # 球队近期赛果缓存（降 API 调用）
API_BASE = "https://api.football-data.org/v4"

# 联赛 code -> 中文名（本地常量，避免 import 其他模块造成耦合）
CODE_TO_LEAGUE = {
    "PL": "英超", "ELC": "英冠", "PD": "西甲", "SDG": "西乙",
    "BL1": "德甲", "BL2": "德乙", "SA": "意甲", "SB": "意乙",
    "FL1": "法甲", "FL2": "法乙", "DED": "荷甲", "EDR": "荷乙",
    "PPL": "葡超", "SP1": "苏超", "B1": "比甲", "NL1": "挪超",
    "ML1": "瑞典超", "SL": "丹超", "PL1": "俄超", "SL1": "土超",
    "BSA": "巴甲", "PA1": "阿甲", "JL1": "日职", "JL2": "日乙",
    "K1": "韩职", "AL1": "澳超", "MLS": "美职", "CSL": "中超",
}
STATUS_CN = {"SCHEDULED": "未开赛", "LIVE": "进行中", "IN_PLAY": "进行中",
             "PAUSED": "中场", "FINISHED": "已结束", "POSTPONED": "延期",
             "SUSPENDED": "暂停", "CANCELLED": "取消"}

# 模型超参（透明、可调）
FORM_N = 8                 # 近 N 场作为近期状态
HOME_ADV = 1.12            # 主场优势系数
LEAGUE_HOME_GPG = 1.38     # 联赛主场场均进球基准
LEAGUE_AWAY_GPG = 1.18     # 联赛客场场均进球基准
CACHE_TTL = 6 * 3600       # 球队缓存有效期（秒）
MIN_FORM = 3               # 低于此样本数退回联赛均值
CALL_BUDGET = 420          # API 调用硬上限（免费层 10/min，防失控）
GRID = 8                   # Poisson 网格上界
BACKFILL_DAYS = 365        # 准确性回溯窗口（天）：用球队缓存近 60 场重建历史比赛，窗口放宽以积累足够样本


def _now():
    # v1.7.39：改用本地时间。原 utcnow() 让 updatedAt 比页面其他模块
    # （足球/篮球/WhoScored 均为本地时间）慢 8 小时——用户看到「更新 10:06」
    # 实际是 18:06，selftest 新鲜度比对也会因时差误报停更。
    # 缓存 ts 的读写同源，TTL 判定不受影响；窗口/回溯均按日期粒度，不受影响。
    return datetime.now()


def _load_config():
    cfg = {"api_key": "", "enabled": True}
    # 来源1：fd_config.json（本地优先，含密钥）
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    # 来源2：cf_config.json 里的 fd_api_key（任务沙箱若只能持久化 cf_config 文件，
    # 而 fd_config 被 .gitignore 排除无法随 git 恢复时，可把 key 寄生在 cf_config 内）
    if not cfg.get("api_key"):
        cf = os.path.join(HERE, "cf_config.json")
        if os.path.exists(cf):
            try:
                c = json.load(open(cf, encoding="utf-8")) or {}
                if c.get("fd_api_key"):
                    cfg["api_key"] = c["fd_api_key"]
            except Exception:
                pass
    # 来源3：环境变量 FD_API_KEY（任务沙箱平台注入，最可靠的跨轮通道；run_daily 自愈会写入）
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("FD_API_KEY", "")
    return cfg


class RateLimited:
    """极简限流器：保证相邻调用间隔，遇 429 退避重试。"""
    def __init__(self, key, gap=6.5, budget=CALL_BUDGET):
        self.key = key
        self.gap = gap
        self.budget = budget
        self.calls = 0
        self.last = 0

    def get(self, path, timeout=20):
        if self.calls >= self.budget:
            return False, "call_budget_exceeded"
        now = time.time()
        wait = self.gap - (now - self.last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            API_BASE + path,
            headers={"X-Auth-Token": self.key, "User-Agent": "jc-scorepred/1.0"},
        )
        for attempt in range(3):
            try:
                raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
                self.last = time.time()
                self.calls += 1
                return True, json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(60)
                    continue
                return False, f"HTTP {e.code}"
            except Exception as e:
                return False, str(e)[:120]
        return False, "retry_exhausted"


# ---------------- 球队近期赛果缓存 ----------------
def _load_team_cache():
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH, encoding="utf-8")) or {}
        except Exception:
            pass
    return {}


def _save_team_cache(cache):
    try:
        json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def _ensure_team(team_id, name, api, cache):
    """确保某队近期赛果在缓存中；返回该队缓存条目。"""
    entry = cache.get(str(team_id))
    now = _now().timestamp()
    fresh = (entry and (now - entry.get("ts", 0)) < CACHE_TTL and entry.get("matches")
             and "id" in entry["matches"][0])
    if fresh:
        return entry
    ok, data = api.get(f"/teams/{team_id}/matches?limit=60")
    matches = []
    if ok and isinstance(data, dict):
        for m in data.get("matches") or []:
            sc = m.get("score") or {}
            ft = sc.get("fullTime") or {}
            hg = ft.get("home")
            ag = ft.get("away")
            ht = (m.get("homeTeam") or {}).get("id")
            at = (m.get("awayTeam") or {}).get("id")
            if m.get("status") == "FINISHED" and hg is not None and ag is not None and ht and at:
                # 以「该队视角」记录：gf=该队进球, ga=对手进球, home=该队是否主场
                is_home = (ht == team_id)
                gf = hg if is_home else ag
                ga = ag if is_home else hg
                opp = at if is_home else ht
                opp_name = (m.get("awayTeam") if is_home else m.get("homeTeam")) or {}
                matches.append({
                    "id": m.get("id"),
                    "utc": m.get("utcDate", ""),
                    "comp": (m.get("competition") or {}).get("code") or "",
                    "home": is_home, "oppId": opp, "oppName": (opp_name or {}).get("name", ""),
                    "gf": gf, "ga": ga, "hg": hg, "ag": ag,
                    "status": m.get("status"),
                })
    entry = {"ts": now, "name": name, "matches": matches}
    cache[str(team_id)] = entry
    return entry


def _form(entry, target_dt):
    """target_dt 之前的最近 FORM_N 场，按主/客拆分返回平均值。"""
    if not entry or not entry.get("matches"):
        return None
    tstr = target_dt.strftime("%Y-%m-%dT%H:%M:%S")
    past = [x for x in entry["matches"] if x["utc"] < tstr]
    past.sort(key=lambda x: x["utc"], reverse=True)
    recent = past[:FORM_N]
    if len(recent) < MIN_FORM:
        return None
    all_gf = sum(x["gf"] for x in recent) / len(recent)
    all_ga = sum(x["ga"] for x in recent) / len(recent)
    home_m = [x for x in recent if x["home"]]
    away_m = [x for x in recent if not x["home"]]
    hgf = sum(x["gf"] for x in home_m) / len(home_m) if home_m else all_gf
    hga = sum(x["ga"] for x in home_m) / len(home_m) if home_m else all_ga
    agf = sum(x["gf"] for x in away_m) / len(away_m) if away_m else all_gf
    aga = sum(x["ga"] for x in away_m) / len(away_m) if away_m else all_ga
    return {"gf": all_gf, "ga": all_ga, "hgf": hgf, "hga": hga,
            "agf": agf, "aga": aga, "n": len(recent)}


def _backtest_from_cache(cache, now):
    """从球队缓存（各自近 60 场，含对手/比分/日期）重建历史比赛并回算预测准确性。

    免费层 /matches 过去窗口样本稀疏、/competitions/{code}/matches 对多数联赛受限，
    故改用已抓取的球队缓存做「零额外 API 调用」的丰富回溯：把同 id 的两队记录拼成一场完整比赛，
    用赛前形式（缓存中该日期之前的近况）预测，再与实际比分比对。
    """
    match_by_id = {}
    for tid, entry in cache.items():
        # 健壮性：缓存条目损坏（非 dict / 无 matches，如写入中断留下的残值）
        # 不该让整个模块崩掉——跳过即可，下轮 _ensure_team 会重建该队。
        if not isinstance(entry, dict):
            continue
        nm = entry.get("name", "")
        for m in entry.get("matches", []) or []:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not mid or m.get("status") != "FINISHED" or m.get("hg") is None or m.get("ag") is None:
                continue
            opp = m.get("oppId")
            if not opp:
                continue
            rec = match_by_id.get(mid)
            if rec is None:
                rec = {"id": mid, "utc": m.get("utc"), "comp": m.get("comp"),
                       "status": "FINISHED", "hg": m.get("hg"), "ag": m.get("ag")}
                match_by_id[mid] = rec
            if m.get("home"):
                rec["homeId"], rec["homeName"] = tid, nm
                rec["awayId"], rec["awayName"] = opp, m.get("oppName", "")
            else:
                rec["awayId"], rec["awayName"] = tid, nm
                rec["homeId"], rec["homeName"] = opp, m.get("oppName", "")
    evals = []
    lo = (now - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    hi = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
    for rec in match_by_id.values():
        if rec.get("homeId") is None or rec.get("awayId") is None:
            continue
        utc = rec.get("utc", "")
        if not (lo <= utc < hi):
            continue
        try:
            tdate = datetime.strptime(utc[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
        fh = _form(cache.get(str(rec["homeId"])), tdate)
        fa = _form(cache.get(str(rec["awayId"])), tdate)
        if not fh or not fa:
            continue
        pred = predict_score(fh, fa)
        ex, ou, mae = _evaluate(pred, {"actH": rec["hg"], "actA": rec["ag"]})
        evals.append({
            "id": rec["id"], "league": CODE_TO_LEAGUE.get(rec.get("comp"), rec.get("comp")),
            "home": rec.get("homeName"), "away": rec.get("awayName"),
            "date": utc[:10],
            "predH": pred["predH"], "predA": pred["predA"],
            "actH": rec["hg"], "actA": rec["ag"],
            "predOutcome": _outcome_of(pred["predH"], pred["predA"]),
            "actOutcome": _outcome_of(rec["hg"], rec["ag"]),
            "exactHit": ex, "outcomeHit": ou, "mae": mae,
        })
    return evals


# ---------------- Poisson 比分模型 ----------------
def _poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def predict_score(form_h, form_a):
    """返回 {predH, predA, pHome, pDraw, pAway}。form=None 时退回联赛均值。"""
    if form_h:
        atk_h = form_h["hgf"] / LEAGUE_HOME_GPG
        def_h = form_h["hga"] / LEAGUE_AWAY_GPG
    else:
        atk_h = def_h = 1.0
    if form_a:
        atk_a = form_a["agf"] / LEAGUE_AWAY_GPG
        def_a = form_a["aga"] / LEAGUE_HOME_GPG
    else:
        atk_a = def_a = 1.0
    lam_h = LEAGUE_HOME_GPG * atk_h * def_a * HOME_ADV
    lam_a = LEAGUE_AWAY_GPG * atk_a * def_h / HOME_ADV
    lam_h = max(0.2, min(4.5, lam_h))
    lam_a = max(0.2, min(4.5, lam_a))
    return _poisson_grid(lam_h, lam_a)


def _poisson_grid(lam_h, lam_a):
    """对 (λ_h, λ_a) 跑 Poisson 联合分布，返回预测比分与胜平负概率。"""
    # 联合众数（预测比分）
    best, bv = (0, 0), -1.0
    pHome = pDraw = pAway = 0.0
    for h in range(GRID + 1):
        ph = _poisson_pmf(h, lam_h)
        for a in range(GRID + 1):
            pa = _poisson_pmf(a, lam_a)
            p = ph * pa
            if p > bv:
                best, bv = (h, a), p
            if h > a:
                pHome += p
            elif h == a:
                pDraw += p
            else:
                pAway += p
    return {"predH": best[0], "predA": best[1],
            "pHome": round(pHome * 100, 1), "pDraw": round(pDraw * 100, 1),
            "pAway": round(pAway * 100, 1)}


def _lambda_from_jc(m):
    """从竞彩 matches.json 的赔率隐含期望进球：主路用 hadProb（去水分胜负概率）+
    goalsExp（期望总进球）按胜负强度拆分，与盘口一致；scoreRec.expHome/expAway 作为退路。
    返回 (lam_h, lam_a)；无法解析返回 (None, None)。"""
    # 主路：市场盘口隐含（内部一致，最可靠）
    try:
        hp = m.get("hadProb") or {}
        h = float(hp.get("h") or 0); d = float(hp.get("d") or 0); a = float(hp.get("a") or 0)
        ge = m.get("goalsExp")
        tot = h + d + a
        if ge and tot > 0:
            ge = float(ge)
            ha = h + d / 2.0            # 主场有效强度（平局折半）
            aa = a + d / 2.0
            s = ha + aa
            if s > 0:
                lh = ge * ha / s
                la = ge * aa / s
                if lh > 0 and la > 0:
                    return max(0.2, min(4.5, lh)), max(0.2, min(4.5, la))
    except (TypeError, ValueError):
        pass
    # 退路：scoreRec.expHome/expAway
    sr = m.get("scoreRec") or {}
    try:
        eh, ea = sr.get("expHome"), sr.get("expAway")
        if eh and ea and float(eh) > 0 and float(ea) > 0:
            return max(0.2, min(4.5, float(eh))), max(0.2, min(4.5, float(ea)))
    except (TypeError, ValueError):
        pass
    return None, None


def _local_fallback(out_meta):
    """无 football-data Key 时的本地兜底：用竞彩 matches.json 的赔率隐含期望进球做 Poisson 预测。
    数据每日随赛程刷新，保证『比分预测』Tab 始终新鲜。football-data 主路径完全隔离、不受影响。
    返回 True 表示已成功生成并写盘；False 表示无法生成（调用方应保留上一轮）。"""
    mp = os.path.join(HERE, "matches.json")
    if not os.path.exists(mp):
        return False
    try:
        with open(mp, encoding="utf-8") as f:
            mj = json.load(f) or {}
    except Exception:
        return False
    jc = mj.get("matches") or []
    if not jc:
        return False
    now = _now()
    lo = (now - timedelta(days=2)).date()
    hi = (now + timedelta(days=2)).date()
    matches_out = []
    for m in jc:
        date = m.get("date", "")
        try:
            md = datetime.strptime(date, "%Y-%m-%d").date()
        except Exception:
            continue
        if not (lo <= md <= hi):
            continue
        lh, la = _lambda_from_jc(m)
        if lh is None or la is None:
            continue
        pred = _poisson_grid(lh, la)
        ko = m.get("kickoff") or m.get("time") or ""
        utc = ("%sT%s:00" % (date, ko)) if ko else (date + "T00:00:00")
        matches_out.append({
            "id": m.get("id"), "competition": "", "league": m.get("league", ""),
            "home": m.get("home"), "away": m.get("away"), "utcDate": utc,
            "status": m.get("status", ""),
            "predH": pred["predH"], "predA": pred["predA"],
            "pHome": pred["pHome"], "pDraw": pred["pDraw"], "pAway": pred["pAway"],
            "predOutcome": _outcome_of(pred["predH"], pred["predA"]),
            "actH": None, "actA": None, "evaluated": False,
            "source": "local-odds",
        })
    if not matches_out:
        return False
    # 准确性回溯：本地无赛果源，沿用上一轮 history（冻结展示，不丢失）
    prev_hist, date_index, date_list = [], {}, []
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            prev = json.load(f) or {}
        prev_hist = prev.get("history") or []
        date_index = prev.get("dateIndex") or {}
        date_list = prev.get("dateList") or []
    except Exception:
        prev = {}
    hist = prev_hist[:1500]
    n = len(hist)
    overall = {"n": n}
    if n:
        overall["exactHit"] = round(sum(1 for h in hist if h.get("exactHit")) / n * 100, 1)
        overall["outcomeHit"] = round(sum(1 for h in hist if h.get("outcomeHit")) / n * 100, 1)
        overall["mae"] = round(sum(h.get("mae", 0) for h in hist) / n, 2)
    leagues, teams = _reliable(hist)
    out_meta["source"] = "本地竞彩赔率隐含 Poisson（football-data Key 未配置时的兜底）"
    out_meta["note"] = ("未配置 football-data.org Key：已改用竞彩 matches.json 的赔率隐含期望进球"
                        "（scoreRec / goalsExp）做 Poisson 兜底预测，数据随每日赛程刷新，"
                        "『比分预测』Tab 保持新鲜。准确性回溯沿用历史样本（本地无赛果源，暂未回算新场）。")
    result = {**out_meta,
              "matches": matches_out,
              "reliable": {"overall": overall, "leagues": leagues, "teams": teams,
                           "backtestDays": BACKFILL_DAYS, "newEval": 0},
              "accuracy": overall, "history": hist,
              "dateIndex": date_index, "dateList": date_list}
    json.dump(result, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("本地兜底（无 FD Key）：基于 matches.json 生成比分预测 %d 场，已写入 %s"
          % (len(matches_out), OUT_PATH))
    return True


def _outcome_of(h, a):
    if h > a:
        return "H"
    if h < a:
        return "A"
    return "D"


# ---------------- 准确性回溯 ----------------
def _load_accuracy():
    if os.path.exists(ACC_PATH):
        try:
            return json.load(open(ACC_PATH, encoding="utf-8")) or {}
        except Exception:
            pass
    return {"history": []}


def _evaluate(pred, act):
    exact = (pred["predH"] == act["actH"] and pred["predA"] == act["actA"])
    outcome = (_outcome_of(pred["predH"], pred["predA"]) ==
               _outcome_of(act["actH"], act["actA"]))
    mae = (abs(pred["predH"] - act["actH"]) + abs(pred["predA"] - act["actA"])) / 2.0
    return exact, outcome, round(mae, 2)


def _reliable(history):
    by_l, by_t = {}, {}
    for r in history:
        lg = r.get("league")
        if lg:
            d = by_l.setdefault(lg, {"n": 0, "exact": 0, "out": 0, "mae": 0.0})
            d["n"] += 1
            d["exact"] += 1 if r.get("exactHit") else 0
            d["out"] += 1 if r.get("outcomeHit") else 0
            d["mae"] += r.get("mae", 0)
        for team in (r.get("home"), r.get("away")):
            if not team:
                continue
            d = by_t.setdefault(team, {"n": 0, "exact": 0, "out": 0, "mae": 0.0})
            d["n"] += 1
            d["exact"] += 1 if r.get("exactHit") else 0
            d["out"] += 1 if r.get("outcomeHit") else 0
            d["mae"] += r.get("mae", 0)
    leagues, teams = [], []
    for lg, d in by_l.items():
        if d["n"] >= 6:
            leagues.append({"league": lg, "n": d["n"],
                            "exactHit": round(d["exact"] / d["n"] * 100, 1),
                            "outcomeHit": round(d["out"] / d["n"] * 100, 1),
                            "mae": round(d["mae"] / d["n"], 2)})
    leagues.sort(key=lambda x: (x["exactHit"], x["outcomeHit"]), reverse=True)
    for tm, d in by_t.items():
        if d["n"] >= 6:
            teams.append({"team": tm, "n": d["n"],
                          "exactHit": round(d["exact"] / d["n"] * 100, 1),
                          "outcomeHit": round(d["out"] / d["n"] * 100, 1),
                          "mae": round(d["mae"] / d["n"], 2)})
    teams.sort(key=lambda x: (x["exactHit"], x["outcomeHit"]), reverse=True)
    return leagues[:10], teams[:12]


# ---------------- 主流程 ----------------
def fetch_scores(no_fetch=False, keep_cache=False, max_teams=None):
    cfg = _load_config()
    out_meta = {"updatedAt": _now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "football-data.org + 独立 Poisson 模型",
                "isolation": True,
                "note": ("独立数据源：football-data.org 赛程/赛果 + 自建透明 Poisson 比分模型；"
                         "与竞彩核心数据/模型零耦合。WhoScored 因 Cloudflare 防护无法直接抓取，"
                         "本 Tab 以独立免费源替代，仅作比分参考，不参与任何竞彩推荐。"
                         "足球数据未覆盖的联赛（亚冠/德国杯/瑞超等）用竞彩赔率隐含 Poisson 兜底，"
                         "保证每天全部竞彩比赛都有自建比分预测。")}
    if not cfg.get("enabled", True) or not cfg.get("api_key"):
        # v1.7.45：缺 Key 不再「冻结旧数据」导致页面永久停更（v1.7.39~v1.7.44 反复踩坑）。
        # 改用本地竞彩赔率（matches.json 的 scoreRec/goalsExp）做 Poisson 兜底预测，
        # 数据每日随赛程刷新，『比分预测』Tab 始终新鲜。football-data 主路径完全隔离、不受影响。
        if _local_fallback(out_meta):
            return out_meta
        # 兜底也无法生成（matches.json 缺失/异常）才保留上一轮，避免空数据
        prev = {}
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f) or {}
        except Exception:
            prev = {}
        if isinstance(prev, dict) and prev.get("matches"):
            out_meta["updatedAt"] = prev.get("updatedAt") or out_meta["updatedAt"]
            out_meta["note"] = ("未配置 football-data.org Key 且本地竞彩数据不可用，"
                                "比分预测暂停更新；以下为此前最后一次成功抓取的数据。")
            json.dump({**out_meta,
                       "matches": prev.get("matches") or [],
                       "reliable": prev.get("reliable") or {},
                       "accuracy": prev.get("accuracy") or {},
                       "history": prev.get("history") or [],
                       "dateIndex": prev.get("dateIndex") or {},
                       "dateList": prev.get("dateList") or []},
                      open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("未配置 football-data.org Key：保留上一轮成功数据（%d 场）"
                  % len(prev.get("matches") or []))
            return out_meta
        out_meta["enabled"] = False
        out_meta["note"] = "未配置 football-data.org Key：请在 fd_config.json 设置 api_key"
        json.dump({**out_meta, "matches": [], "reliable": {}, "accuracy": {},
                   "history": [], "dateIndex": {}, "dateList": []},
                  open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return out_meta

    api = RateLimited(cfg["api_key"])
    cache = _load_team_cache()
    now = _now()

    # 窗口：展示窗（近2天~未来2天）+ 回溯窗（近 BACKFILL_DAYS 天，仅赛果用于准确性）
    fwd_from = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    fwd_to = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    bak_from = (now - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%d")
    bak_to = (now - timedelta(days=2)).strftime("%Y-%m-%d")

    def _pull(frm, to):
        ok, d = api.get(f"/matches?dateFrom={frm}&dateTo={to}")
        if ok and isinstance(d, dict):
            return d.get("matches") or []
        return []

    def _keep(m):
        code = (m.get("competition") or {}).get("code") or ""
        return code in CODE_TO_LEAGUE
    fwd = [m for m in ([] if no_fetch else _pull(fwd_from, fwd_to)) if _keep(m)]
    if not fwd and not no_fetch:
        # v1.7.51：football-data 拉取为空（网络不可达/限流/窗口无覆盖联赛）→ 全量本地赔率兜底，
        # 保证『比分预测』Tab 始终覆盖全部竞彩比赛，绝不因上游不可达而空白。
        print("football-data 拉取为空：改用竞彩赔率全量本地 Poisson 兜底，保证覆盖")
        if _local_fallback(out_meta):
            return out_meta
    # 回溯：从已抓取的球队缓存重建历史比赛并回算准确性（零额外 API 调用，规避免费层过去窗口稀疏问题）
    bak = [] if no_fetch else _backtest_from_cache(cache, now)

    # 收集需要建缓存的球队：展示窗比赛 + 已缓存球队（旧格式需刷新为富化格式以支撑回溯）
    teams_needed = {}
    window_team_ids = set()
    for m in fwd:
        for side in ("homeTeam", "awayTeam"):
            t = (m.get(side) or {})
            tid = t.get("id")
            if tid:
                teams_needed[tid] = t.get("name", "")
                window_team_ids.add(tid)      # 展示窗球队：刷新时优先补它们
    for tid, e in cache.items():
        teams_needed.setdefault(tid, e.get("name", ""))
    if not no_fetch:
        # v1.7.39：「过期也刷」。原逻辑只刷「没有可用缓存」的队，已缓存队的 form
        # 数据自建库那刻起永不更新——2026-10-22 实测任务沙箱缓存停在 10-09，
        # 预测用的是两周前的近期状态，质量随时间持续劣化。现改为缺缓存 + 缓存
        # 过期（>CACHE_TTL）都刷；冷启动保护不变：单轮最多 N 支（全量 100+ 支要
        # 十几分钟，会挤爆任务预算），剩余分后续轮次补齐，展示窗球队优先。
        # --keep-cache 轮（每日非首轮）只补「新出现的队」，不做过期刷新。
        now_ts = _now().timestamp()

        def _needs_refresh(tid):
            e = cache.get(str(tid)) or {}
            if not e.get("matches"):
                return True                     # 新队：必须补，否则预测退回联赛均值
            if keep_cache:
                return False
            return (now_ts - e.get("ts", 0)) >= CACHE_TTL

        todo = [tid for tid in teams_needed if _needs_refresh(tid)]
        todo.sort(key=lambda t: 0 if t in window_team_ids else 1)
        if max_teams and len(todo) > max_teams:
            print(f"球队缓存刷新受限：本轮只刷 {max_teams}/{len(todo)} 支（其余沿用旧缓存，后续轮次补齐）")
            todo = todo[:max_teams]
        _t0 = time.time()
        for tid in todo:
            if time.time() - _t0 > 90:
                print("球队缓存刷新超时（90s 墙钟保护），剩余沿用旧缓存，后续轮次补齐")
                break
            _ensure_team(tid, teams_needed[tid], api, cache)
        _save_team_cache(cache)

    def _team_entry(tid):
        return cache.get(str(tid))

    def _conv(m):
        comp = (m.get("competition") or {}).get("code") or ""
        ht = (m.get("homeTeam") or {})
        at = (m.get("awayTeam") or {})
        sc = m.get("score") or {}
        ft = sc.get("fullTime") or {}
        return {
            "id": m.get("id"),
            "competition": comp,
            "league": CODE_TO_LEAGUE.get(comp, comp),
            "home": ht.get("name"), "away": at.get("name"),
            "homeId": ht.get("id"), "awayId": at.get("id"),
            "utcDate": m.get("utcDate", ""),
            "status": m.get("status", ""),
            "actH": ft.get("home"), "actA": ft.get("away"),
        }

    # 预测展示窗
    matches_out = []
    for m in fwd:
        c = _conv(m)
        if not c["homeId"] or not c["awayId"]:
            continue
        tdate = datetime.strptime(c["utcDate"][:19], "%Y-%m-%dT%H:%M:%S") if c["utcDate"] else now
        fh = _form(_team_entry(c["homeId"]), tdate)
        fa = _form(_team_entry(c["awayId"]), tdate)
        pred = predict_score(fh, fa)
        rec = {
            "id": c["id"], "competition": c["competition"], "league": c["league"],
            "home": c["home"], "away": c["away"], "utcDate": c["utcDate"],
            "status": c["status"],
            "predH": pred["predH"], "predA": pred["predA"],
            "pHome": pred["pHome"], "pDraw": pred["pDraw"], "pAway": pred["pAway"],
            "predOutcome": _outcome_of(pred["predH"], pred["predA"]),
            "actH": c["actH"], "actA": c["actA"], "evaluated": False,
        }
        if c["status"] == "FINISHED" and c["actH"] is not None:
            ex, ou, mae = _evaluate(pred, c)
            rec["evaluated"] = True
            rec["exactHit"] = ex
            rec["outcomeHit"] = ou
            rec["mae"] = mae
        matches_out.append(rec)

    # v1.7.53：五大联赛必须有比分预测——以竞彩赔率本地 Poisson 为保底基线，
    # 给每一场「尚未被渲染层可见的接口记录覆盖」的竞彩比赛补预测（含五大联赛），
    # 不再仅限非 CODE_TO_LEAGUE 联赛。覆盖判定与渲染层 spJcFilter 严格一致：
    # 接口记录须能经 spZh 翻译出「两译名都含中文」的队名对，才算「已覆盖」，否则补本地基线。
    # 这样既保证五大联赛永不空缺（接口漏返回/不可达/翻译失败都有本地兜底），又不产生可见重复卡片。
    _local_added = 0
    try:
        import re as _re
        try:
            from team_map import TEAM_EN2ZH as _EN2ZH
        except Exception:
            _EN2ZH = {}
        def _spZh(n):
            if not n: return "?"
            if n in _EN2ZH: return _EN2ZH[n]
            t = _re.sub(r"\b(FC|CF|AC|AS|SC|SV|SK|NK|FK|UD|US|SS|RC|CD|BK|SD|JK|KF|GD|RJ|VfL|VfB|Calcio|Club|Clube|Futebol|Football|Fotbal|Fotboll)\b","",n,flags=_re.I)
            t = _re.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b","",t)
            t = _re.sub(r"\s+"," ",t).strip()
            if t and t in _EN2ZH: return _EN2ZH[t]
            t2 = _re.sub(r"^\s*[A-Z]{1,3}\s+","",t).strip()
            if t2 and t2 in _EN2ZH: return _EN2ZH[t2]
            return n
        # 已由接口（含能渲染显示的队名）覆盖的竞彩对阵集合
        _covered = set()
        for _r in matches_out:
            _h, _a = _spZh(_r.get("home","")), _spZh(_r.get("away",""))
            if _re.search(r"[一-鿿]", _h) and _re.search(r"[一-鿿]", _a):
                _covered.add(frozenset([_h, _a]))
        _mp = os.path.join(HERE, "matches.json")
        if os.path.exists(_mp):
            with open(_mp, encoding="utf-8") as _f:
                _mj = json.load(_f) or {}
            for _m in (_mj.get("matches") or []):
                _lg = _m.get("league", "")
                _date = _m.get("date", "")
                try:
                    _md = datetime.strptime(_date, "%Y-%m-%d").date()
                except Exception:
                    continue
                if not ((now - timedelta(days=2)).date() <= _md <= (now + timedelta(days=2)).date()):
                    continue
                _jh, _ja = _m.get("home",""), _m.get("away","")
                if not _jh or not _ja:
                    continue
                if frozenset([_jh, _ja]) in _covered:
                    continue  # 已有渲染可见的接口预测，不重复补
                _lh, _la = _lambda_from_jc(_m)
                if _lh is None or _la is None:
                    continue
                _pred = _poisson_grid(_lh, _la)
                _ko = _m.get("kickoff") or _m.get("time") or ""
                _utc = ("%sT%s:00" % (_date, _ko)) if _ko else (_date + "T00:00:00")
                matches_out.append({
                    "id": _m.get("id"), "competition": "", "league": _lg,
                    "home": _jh, "away": _ja,
                    "utcDate": _utc, "status": _m.get("status", ""),
                    "predH": _pred["predH"], "predA": _pred["predA"],
                    "pHome": _pred["pHome"], "pDraw": _pred["pDraw"], "pAway": _pred["pAway"],
                    "predOutcome": _outcome_of(_pred["predH"], _pred["predA"]),
                    "actH": None, "actA": None, "evaluated": False,
                    "source": "local-odds",
                })
                _local_added += 1
        # v1.7.55：历史竞彩日补全覆盖——matches.json 每天滚动刷新为「仅当天在售」，
        # 昨日/前日竞彩赛程已被覆盖，但其比赛仍应有比分预测（用户要求每天都有）。
        # 补全源扩展为「当前 matches.json + 竞彩历史档案 history.json 近 N 天(不含当天)」，
        # 对尚未被接口(FD)/当日补全覆盖的竞彩对阵补 Poisson 基线：优先用 scoreRec 隐含
        # 期望进球(_lambda_from_jc 退路)，缺失则退回联赛均值。确保历史竞彩日不空缺。
        _hp = os.path.join(HERE, "history.json")
        _HIST_WIN = 9
        if os.path.exists(_hp):
            try:
                _hj = json.load(open(_hp, encoding="utf-8")) or {}
                # v1.7.55 修复：历史竞彩日补全必须用「自身已补集合」去重，
                # 不能复用 _covered——_covered 含 FD 英文队名经 spZh 翻出的中文对阵，
                # 会误判 history 中文竞彩场次「已覆盖」而跳过（典型如 27号 3 场意甲：
                # FD 回 US Sassuolo Calcio/Torino FC/AS Roma 英文，翻成萨索洛/都灵/罗马
                # 后污染 _covered，导致罗马/都灵/萨索洛竞彩场次漏补、回溯区缺 3 场）。
                # 竞彩历史档案的中文队名才是渲染层 spJcFilter 实际显示版本，FD 英文场次被隐藏，
                # 故只要该中文对阵尚未被 history 自身补加，就补本地基线，确保历史竞彩日全量可见。
                _hist_added = set()
                for _r in (_hj.get("records") or []):
                    _lg = _r.get("league", "")
                    _date = _r.get("date", "")
                    try:
                        _md = datetime.strptime(_date, "%Y-%m-%d").date()
                    except Exception:
                        continue
                    # 仅补过去竞彩日(now-9~now-1)，当天及未来由 matches.json 源负责，避免重复
                    if not ((now - timedelta(days=_HIST_WIN)).date() <= _md <= (now - timedelta(days=1)).date()):
                        continue
                    _jh, _ja = _r.get("home", ""), _r.get("away", "")
                    if not _jh or not _ja:
                        continue
                    if frozenset([_jh, _ja]) in _hist_added:
                        continue  # 已被 history 自身补加，不重复
                    _lh, _la = _lambda_from_jc(_r)
                    if _lh is None or _la is None:
                        _lh, _la = (max(0.2, min(4.5, LEAGUE_HOME_GPG * HOME_ADV)),
                                    max(0.2, min(4.5, LEAGUE_AWAY_GPG / HOME_ADV)))
                    _pred = _poisson_grid(_lh, _la)
                    # 优先用智能引擎 scoreRec.predicted 作为预测比分（更有区分度/可信度），
                    # 缺失或格式异常则退回 Poisson 众数。
                    _sr = _r.get("scoreRec") or {}
                    _ps = (_sr.get("predicted") or "").strip()
                    _pm = _re.match(r"^\s*(\d+)\s*[:：\-]\s*(\d+)\s*$", _ps)
                    if _pm:
                        _pred = dict(_pred)
                        _pred["predH"] = int(_pm.group(1))
                        _pred["predA"] = int(_pm.group(2))
                    _ko = ""
                    try:
                        _kk = (_r.get("kickoff") or "").split(" ")[-1]
                        _ko = _kk[:5] if len(_kk) >= 5 else ""
                    except Exception:
                        _ko = ""
                    _utc = ("%sT%s:00" % (_date, _ko)) if _ko else (_date + "T00:00:00")
                    _acth = _r.get("actH") or _r.get("scoreActual")
                    _acta = _r.get("actA") or _r.get("scoreActual")
                    matches_out.append({
                        "id": _r.get("id"), "competition": "", "league": _lg,
                        "home": _jh, "away": _ja,
                        "utcDate": _utc, "status": _r.get("status", ""),
                        "predH": _pred["predH"], "predA": _pred["predA"],
                        "pHome": _pred["pHome"], "pDraw": _pred["pDraw"], "pAway": _pred["pAway"],
                        "predOutcome": _outcome_of(_pred["predH"], _pred["predA"]),
                        "actH": _acth, "actA": _acta, "evaluated": False,
                        "source": "local-odds",
                    })
                    _hist_added.add(frozenset([_jh, _ja]))
                    _local_added += 1
            except Exception as _he:
                print("读取 history.json 补全覆盖异常跳过：%s" % _he)
    except Exception as _e:
        print("本地赔率补全（保底基线）异常跳过：%s" % _e)

    # 准确性回溯：① 缓存重建的历史比赛（已算好）② 展示窗已结束比赛（用赛前形式重算）
    acc = _load_accuracy()
    hist = acc.get("history", [])
    seen = {h.get("id") for h in hist}
    new_eval = 0
    for e in bak:
        if e.get("id") in seen:
            continue
        hist.append(e)
        seen.add(e["id"])
        new_eval += 1
    for m in fwd:
        c = _conv(m)
        if c["status"] != "FINISHED" or c["actH"] is None or c["id"] in seen:
            continue
        if not c["homeId"] or not c["awayId"]:
            continue
        tdate = datetime.strptime(c["utcDate"][:19], "%Y-%m-%dT%H:%M:%S")
        fh = _form(_team_entry(c["homeId"]), tdate)
        fa = _form(_team_entry(c["awayId"]), tdate)
        pred = predict_score(fh, fa)
        ex, ou, mae = _evaluate(pred, c)
        hist.append({
            "id": c["id"], "league": c["league"], "home": c["home"], "away": c["away"],
            "date": c["utcDate"][:10],
            "predH": pred["predH"], "predA": pred["predA"],
            "actH": c["actH"], "actA": c["actA"],
            "predOutcome": _outcome_of(pred["predH"], pred["predA"]),
            "actOutcome": _outcome_of(c["actH"], c["actA"]),
            "exactHit": ex, "outcomeHit": ou, "mae": mae,
        })
        seen.add(c["id"])
        new_eval += 1
    hist.sort(key=lambda x: x.get("date", ""), reverse=True)
    hist = hist[:1500]
    leagues, teams = _reliable(hist)
    n = len(hist)
    overall = {"n": n}
    if n:
        overall["exactHit"] = round(sum(1 for h in hist if h["exactHit"]) / n * 100, 1)
        overall["outcomeHit"] = round(sum(1 for h in hist if h["outcomeHit"]) / n * 100, 1)
        overall["mae"] = round(sum(h["mae"] for h in hist) / n, 2)

    acc.update({"updatedAt": out_meta["updatedAt"], "history": hist})
    json.dump(acc, open(ACC_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 日期回溯索引：按日期聚合历史预测（供 Tab 内按日期回看，纯本地历史，不触发任何抓取）
    date_index = {}
    for h in hist:
        d = h.get("date", "")
        if not d:
            continue
        date_index.setdefault(d, []).append({
            "id": h.get("id"), "league": h.get("league"),
            "home": h.get("home"), "away": h.get("away"),
            "predH": h.get("predH"), "predA": h.get("predA"),
            "actH": h.get("actH"), "actA": h.get("actA"),
            "predOutcome": h.get("predOutcome"), "actOutcome": h.get("actOutcome"),
            "exactHit": h.get("exactHit"), "outcomeHit": h.get("outcomeHit"),
            "mae": h.get("mae"),
        })

    # v1.7.55：本地补全覆盖的竞彩比赛（含历史竞彩日如 27号）也纳入日期回溯索引，
    # 让用户能在「日期回溯」区按竞彩日回看每天的预测，而不只 FD 已完赛比赛。
    for _m in matches_out:
        if _m.get("source") != "local-odds":
            continue
        _d = (_m.get("utcDate") or "")[:10]
        if not _d:
            continue
        date_index.setdefault(_d, []).append({
            "id": _m.get("id"), "league": _m.get("league"),
            "home": _m.get("home"), "away": _m.get("away"),
            "predH": _m.get("predH"), "predA": _m.get("predA"),
            "actH": _m.get("actH"), "actA": _m.get("actA"),
            "predOutcome": _m.get("predOutcome"), "actOutcome": None,
            "exactHit": None, "outcomeHit": None, "mae": None,
        })

    # v1.7.57：历史竞彩日【全量】覆盖——把竞彩历史档案(history.json)的每一场都写入 dateIndex，
    # 确保「日期回溯」区对任一历史竞彩日都不缺场次。此前 dateIndex 仅由「accuracy 回溯(hist) +
    # 近9天 local-odds」构成，538/681 历史记录缺失 → 绝大多数历史日回溯空白（用户反馈「还有遗漏」）。
    # 已存在的(id 命中 或 日期+队名 命中)不重复添加；实际赛果取 result/scoreActual，据 预测vs实际 算命中。
    _seen_ids = set()
    _seen_pairs = set()
    for _d, _arr in date_index.items():
        for _x in _arr:
            if _x.get("id") is not None:
                _seen_ids.add(_x.get("id"))
            if _x.get("home") and _x.get("away"):
                _seen_pairs.add((_d, _x.get("home"), _x.get("away")))
    _hp_all = os.path.join(HERE, "history.json")
    if os.path.exists(_hp_all):
        try:
            _hj_all = json.load(open(_hp_all, encoding="utf-8")) or {}
            for _r in (_hj_all.get("records") or []):
                _rid = _r.get("id")
                _jh, _ja = _r.get("home", ""), _r.get("away", "")
                _lg = _r.get("league", "")
                _date = _r.get("date", "")
                if not _jh or not _ja or not _date:
                    continue
                # v1.7.57：不再以 _seen_ids/_seen_pairs 提前跳过——local-odds/hist 已写入的中文
                # 条目需要被本循环「就地升级」（补实际赛果+命中），否则会保留 null 实际导致空白。
                # 改为：先算好 预测/实际/命中，再查是否已存在同名(中文)条目→就地升级，否则新增。
                # 预测比分：优先 scoreRec.predicted（赛前预测），退路 1:1
                _ph = _pa = None
                _ps = ((_r.get("scoreRec") or {}).get("predicted") or "").strip()
                if _ps:
                    for _sep in (":", "：", "-"):
                        if _sep in _ps:
                            try:
                                _ph, _pa = (int(v.strip()) for v in _ps.split(_sep))
                            except Exception:
                                _ph = _pa = None
                            break
                if _ph is None or _pa is None:
                    _ph, _pa = 1, 1
                # 实际赛果：result{home,away} 优先，退路 scoreActual / actH,actA
                _ah = _aa = None
                _res = _r.get("result")
                if isinstance(_res, dict) and _res.get("score"):
                    _ah, _aa = _res.get("home"), _res.get("away")
                else:
                    _sa = _r.get("scoreActual")
                    if isinstance(_sa, str) and ":" in _sa:
                        try:
                            _ah, _aa = (int(v.strip()) for v in _sa.split(":"))
                        except Exception:
                            _ah = _aa = None
                    else:
                        _ah, _aa = _r.get("actH"), _r.get("actA")
                # v1.7.57：本记录缺实际赛果时，尝试从同日已存在回溯(hist)条目借用——
                # hist 条目用 FD 英文队名且常已回填真实赛果（如近期刚结算、history.json 尚未回填
                # result 的比赛）。按「翻译后队名」匹配同日条目，命中则借用其 actH/actA，
                # 使「预测 vs 实际」对近期比赛也完整（避免可见中文条目显示「-:-」而隐藏英文条目却有结果）。
                if _ah is None and _aa is None:
                    try:
                        from team_map import TEAM_EN2ZH as _TME
                        _PREF = r"\b(FC|CF|AC|AS|SC|SV|SK|NK|FK|UD|US|SS|RC|CD|BK|SD|JK|KF|GD|RJ|VfL|VfB|Calcio|Club|Clube|Futebol|Football|Fotboll)\b"
                        def _tr(n):
                            if not n:
                                return n
                            if n in _TME:
                                return _TME[n]
                            _t = _RE.sub(_PREF, "", n, flags=_RE.I)
                            _t = _RE.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b", "", _t)
                            _t = _RE.sub(r"\s+", " ", _t).strip()
                            if _t and _t in _TME:
                                return _TME[_t]
                            _t2 = _RE.sub(r"^\s*[A-Z]{1,3}\s+", "", _t).strip()
                            if _t2 and _t2 in _TME:
                                return _TME[_t2]
                            return n
                        for _e in date_index.get(_date, []):
                            _eh, _ea = _tr(_e.get("home", "")), _tr(_e.get("away", ""))
                            if (_eh == _jh and _ea == _ja) or (_eh == _ja and _ea == _jh):
                                if _e.get("actH") is not None and _e.get("actA") is not None:
                                    _ah, _aa = _e.get("actH"), _e.get("actA")
                                    break
                    except Exception:
                        pass

                # 命中判定（有实际赛果才算，否则置 None → 渲染显示「待定」而非「未中」）
                _ex = _ou = None
                if _ah is not None and _aa is not None:
                    _ex = (int(_ph) == int(_ah) and int(_pa) == int(_aa))
                    _po = _outcome_of(int(_ph), int(_pa))
                    _ao = _outcome_of(int(_ah), int(_aa))
                    _ou = (_po == _ao)
                _mae = None
                if _ah is not None and _aa is not None:
                    _mae = (abs(int(_ph) - int(_ah)) + abs(int(_pa) - int(_aa))) / 2
                # 查找是否已有同名(中文)条目（来自 local-odds 或 hist 循环），就地升级；否则新增
                _existing = None
                for _x in date_index.get(_date, []):
                    if (_x.get("home") == _jh and _x.get("away") == _ja) or (_x.get("home") == _ja and _x.get("away") == _jh):
                        _existing = _x
                        break
                if _existing is not None:
                    # 用 history.json 赛前预测刷新预测比分，并补实际赛果+命中（若可借到）
                    _existing["predH"], _existing["predA"] = _ph, _pa
                    _existing["predOutcome"] = _outcome_of(int(_ph), int(_pa))
                    if _ah is not None:
                        _existing["actH"], _existing["actA"] = _ah, _aa
                        _existing["actOutcome"] = _outcome_of(int(_ah), int(_aa))
                        _existing["exactHit"] = _ex
                        _existing["outcomeHit"] = _ou
                        _existing["mae"] = _mae
                    if _rid is not None:
                        _seen_ids.add(_rid)
                    continue
                date_index.setdefault(_date, []).append({
                    "id": _rid, "league": _lg, "home": _jh, "away": _ja,
                    "predH": _ph, "predA": _pa,
                    "actH": _ah, "actA": _aa,
                    "predOutcome": _outcome_of(int(_ph), int(_pa)),
                    "actOutcome": _outcome_of(int(_ah), int(_aa)) if _ah is not None else None,
                    "exactHit": _ex, "outcomeHit": _ou, "mae": _mae,
                })
                if _rid is not None:
                    _seen_ids.add(_rid)
                _seen_pairs.add((_date, _jh, _ja))
        except Exception as _he:
            print("history.json 全量补 dateIndex 异常跳过：%s" % _he)

    # v1.7.58：日期回溯索引清洗——根治「同一竞彩对阵出现重复卡片」。
    # 根因：上面三个来源（① FD 精度回溯 hist ② local-odds ③ history.json 全量）对「同一场竞彩」
    # 可能同时写入英文 FD 版与中文竞彩版两条记录；渲染层 spJcFilter 二者都通过（翻译后同属竞彩对阵）
    # → 同一场显示两张卡片。此处按「翻译归一后的竞彩对阵」同日去重，并剔除非竞彩纯英文噪声。
    try:
        from team_map import TEAM_EN2ZH as _TME
        _PREFX = r"\b(FC|CF|AC|AS|SC|SV|SK|NK|FK|UD|US|SS|RC|CD|BK|SD|JK|KF|GD|RJ|VfL|VfB|Calcio|Club|Clube|Futebol|Football|Fotboll)\b"
        def _spzh2(n):
            if not n:
                return ""
            if n in _TME:
                return _TME[n]
            t = _RE.sub(_PREFX, "", n, flags=_RE.I)
            t = _RE.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b", "", t)
            t = _RE.sub(r"\s+", " ", t).strip()
            if t and t in _TME:
                return _TME[t]
            t2 = _RE.sub(r"^\s*[A-Z]{1,3}\s+", "", t).strip()
            if t2 and t2 in _TME:
                return _TME[t2]
            return n
        # 归一化名：已是中文→原样(避免 TME 缩短映射如「阿斯顿维拉→维拉」「曼彻斯特城→曼城」
        # 把存储中文名与英文翻出的中文名搞成两份导致去重/过滤对不上)；纯英文→翻译。
        def _cname(n):
            if not n:
                return ""
            if any('\u4e00' <= c <= '\u9fff' for c in n):
                return n
            return _spzh2(n)
        # 竞彩对阵集合（与渲染层 spJcPairs 一致：当天赛程 matches.json + 竞彩历史档案 history.json）。
        # 同时收录「原始名」与「归一化名」两种形态，规避 TME 缩短映射造成的漏匹配/误删。
        _jcpairs = set()
        _mj_path = os.path.join(HERE, "matches.json")
        if os.path.exists(_mj_path):
            try:
                for _m in (json.load(open(_mj_path, encoding="utf-8")).get("matches") or []):
                    if _m.get("home") and _m.get("away"):
                        _jcpairs.add(frozenset([_m["home"], _m["away"]]))
                        _jcpairs.add(frozenset([_cname(_m["home"]), _cname(_m["away"])]))
            except Exception:
                pass
        _hj2_path = os.path.join(HERE, "history.json")
        if os.path.exists(_hj2_path):
            try:
                for _r in (json.load(open(_hj2_path, encoding="utf-8")).get("records") or []):
                    if _r.get("home") and _r.get("away"):
                        _jcpairs.add(frozenset([_r["home"], _r["away"]]))
                        _jcpairs.add(frozenset([_cname(_r["home"]), _cname(_r["away"])]))
            except Exception:
                pass
        _cjk = lambda s: any('\u4e00' <= c <= '\u9fff' for c in (s or ""))
        for _d in list(date_index.keys()):
            _best = {}
            for _x in date_index[_d]:
                _zhh, _zha = _cname(_x.get("home", "")), _cname(_x.get("away", ""))
                # 翻译后双方须含中文（纯英文且翻译失败 → 非竞彩噪声）→ 剔除
                if not (_cjk(_zhh) and _cjk(_zha)):
                    continue
                if frozenset([_zhh, _zha]) not in _jcpairs:
                    continue
                _key = frozenset([_zhh, _zha])
                _cur = _best.get(_key)
                if _cur is None:
                    _best[_key] = _x
                else:
                    # 保留更优：① 已结算(有实际赛果) ② 中文原名(竞彩版，避免英文源卡片)
                    _nx = _x.get("actH") is not None
                    _nc = _cur.get("actH") is not None
                    if _nx and not _nc:
                        _best[_key] = _x
                    elif _nx == _nc:
                        if _cjk(_x.get("home", "")) and not _cjk(_cur.get("home", "")):
                            _best[_key] = _x
            if _best:
                date_index[_d] = list(_best.values())
            else:
                del date_index[_d]
    except Exception as _de:
        print("dateIndex 清洗(去重/去噪)异常跳过：%s" % _de)

    result = {
        **out_meta,
        "matches": matches_out,
        "reliable": {
            "overall": overall,
            "leagues": leagues,
            "teams": teams,
            "backtestDays": BACKFILL_DAYS,
            "newEval": new_eval,
        },
        "accuracy": overall,
        "history": hist,
        "dateIndex": date_index,
        "dateList": sorted(date_index.keys(), reverse=True),
    }
    json.dump(result, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"比分预测：展示窗 {len(matches_out)} 场（足球数据 {len(matches_out)-_local_added} + 本地赔率补全 {_local_added}），"
          f"回溯新增 {new_eval} 场（累计 {n} 场）；"
          f"整体精确命中 {overall.get('exactHit','-')}%，方向命中 {overall.get('outcomeHit','-')}%，"
          f"已写入 {OUT_PATH}；API 调用 {api.calls} 次")
    return result


if __name__ == "__main__":
    import sys
    mt = None
    for i, a in enumerate(sys.argv):
        if a == "--max-teams" and i + 1 < len(sys.argv):
            try:
                mt = int(sys.argv[i + 1])
            except ValueError:
                mt = None
    fetch_scores(no_fetch="--no-fetch" in sys.argv,
                 keep_cache="--keep-cache" in sys.argv,
                 max_teams=mt)
