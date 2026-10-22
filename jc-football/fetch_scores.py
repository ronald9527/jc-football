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
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
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
                         "本 Tab 以独立免费源替代，仅作比分参考，不参与任何竞彩推荐。")}
    if not cfg.get("enabled", True) or not cfg.get("api_key"):
        # v1.7.39：缺 Key 时不再抹掉已有数据。
        # 事故（2026-10-21~22）：任务沙箱丢失 fd_config.json（密钥文件被自举包排除、
        # 沙箱重置后不回），无密钥路径直接把 score_predict.json 覆盖成空结构，
        # 92 天回溯索引 + 566 条历史天天被抹，用户侧「比分预测几天没更新」且历史归零。
        # 现改为：保留上一轮成功数据，仅标记未配置 + enabled=False；
        # updatedAt 保持原数据的时间——数据年龄不失真，selftest 新鲜度检查才能识别停更。
        prev = {}
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f) or {}
        except Exception:
            prev = {}
        if isinstance(prev, dict) and prev.get("matches"):
            out_meta["updatedAt"] = prev.get("updatedAt") or out_meta["updatedAt"]
            out_meta["note"] = ("未配置 football-data.org Key（fd_config.json 或环境变量 "
                                "FD_API_KEY），比分预测暂停更新；以下为此前最后一次成功抓取的数据。")
            json.dump({**out_meta,
                       "matches": prev.get("matches") or [],
                       "reliable": prev.get("reliable") or {},
                       "accuracy": prev.get("accuracy") or {},
                       "history": prev.get("history") or [],
                       "dateIndex": prev.get("dateIndex") or {},
                       "dateList": prev.get("dateList") or []},
                      open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("未配置 football-data.org Key：保留上一轮成功数据（%d 场 / %d 天回溯），不覆盖"
                  % (len(prev.get("matches") or []), len(prev.get("dateList") or [])))
            return out_meta
        out_meta["enabled"] = False
        out_meta["note"] = "未配置 football-data.org Key：请在 fd_config.json 设置 api_key"
        json.dump({**out_meta, "matches": [], "reliable": {}, "accuracy": {}},
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
        for tid in todo:
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
    print(f"比分预测：展示窗 {len(matches_out)} 场，回溯新增 {new_eval} 场（累计 {n} 场）；"
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
