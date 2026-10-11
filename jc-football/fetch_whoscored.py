#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WhoScored 比分预测（独立数据源，与竞彩核心数据/模型零耦合）。

数据流：
  1) GET /previews                 → 日期分组 + 联赛 + UTC 开球时间 + 比赛链接
  2) GET /matches/{id}/preview     → #preview-prediction .predicted-score（WhoScored 人工预测比分）
  3) GET /livescores               → 当日赛果（homeScore/awayScore，status 6=完场）
     已滚出当日列表的完赛场次，用 GET /matches/{id}/live 的 <title> 解析赛果
  4) 所有时间统一转北京时间（UTC+8），与竞彩赛程日期口径一致（竞彩编号对照在前端完成）

输出 whoscored_data.json，供「比分预测」Tab 的 WhoScored 区块使用。
反爬：curl_cffi impersonate=chrome124（python 默认 TLS 指纹会被 Cloudflare 403）。
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "whoscored_data.json")
PREVIEW_CACHE = os.path.join(HERE, "whoscored_preview_cache.json")   # 预测比分不可变 → 永久缓存
HISTORY_PATH = os.path.join(HERE, "whoscored_history.json")          # 累积历史（按比赛 id 去重）

BASE = "https://www.whoscored.com"
IMPS = ["chrome124", "chrome131", "chrome120", "safari184"]  # 指纹降级链
DELAY = 0.4                    # 每次页面抓取间隔（秒）
MAX_PREVIEW_FETCH = 220        # 单次运行最多抓多少个 preview 页（首_run 全量约 140）
MAX_RESULT_FETCH = 60          # 单次运行最多补多少个历史赛果（按 title）
BJ_OFFSET = 8 * 3600           # 北京时间 = UTC + 8h

# 联赛中文名（竞彩习惯叫法；未收录的保留英文）
LEAGUE_ZH = {
    "Premier League": "英超", "Championship": "英冠", "League One": "英甲", "League Two": "英乙",
    "LaLiga": "西甲", "LaLiga2": "西乙", "Bundesliga": "德甲", "2. Bundesliga": "德乙",
    "Serie A": "意甲", "Serie B": "意乙", "Ligue 1": "法甲", "Ligue 2": "法乙",
    "Eredivisie": "荷甲", "Eerste Divisie": "荷乙", "Liga Portugal": "葡超",
    "Liga Portugal 2": "葡甲", "Jupiler Pro League": "比甲", "Challenger Pro League": "比乙",
    "Süper Lig": "土超", "Super Lig": "土超", "Allsvenskan": "瑞超", "Eliteserien": "挪超",
    "Superliga": "丹麦超", "Veikkausliiga": "芬超", "Brasileirão": "巴甲", "Brasileirao": "巴甲",
    "Serie A Betano": "巴甲", "Major League Soccer": "美职联", "Primera División": "阿甲",
    "Liga Profesional Argentina": "阿甲", "Categoria Primera A": "哥伦比亚甲",
    "K League 1": "韩K联", "K League 2": "韩K2", "J1 League": "日职", "J2 League": "日职乙",
    "Chinese Super League": "中超", "A-League": "澳超", "Scottish Premiership": "苏超",
    "Austrian Bundesliga": "奥甲", "Swiss Super League": "瑞士超", "Greek Super League": "希腊超",
    "Russian Premier League": "俄超", "Ekstraklasa": "波兰超", "Chance Liga": "捷克甲",
    "NB I": "匈牙利甲", "Divizia Nationala": "摩尔多瓦甲", "Prva Liga": "斯洛文尼亚甲",
    "UEFA Champions League": "欧冠", "UEFA Europa League": "欧联", "UEFA Conference League": "欧会杯",
    "UEFA Nations League": "欧国联", "World Cup": "世界杯", "WC Qualification South America": "世预赛南美区",
    "WC Qualification Europe": "世预赛欧洲区", "WC Qualification Asia": "世预赛亚洲区",
    "Copa Libertadores": "解放者杯", "Copa Sudamericana": "南美杯", "Saudi Pro League": "沙特联",
}


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f) or default
        except Exception:
            return default
    return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------- 抓取（curl_cffi 指纹伪装） ----------------
def _get(url, referer=BASE + "/"):
    """chrome124 指纹请求，失败按指纹链降级重试。返回 (status, text)。"""
    try:
        from curl_cffi import requests as cr
    except Exception:
        return 0, ""
    last = (0, "")
    for imp in IMPS:
        for attempt in range(2):
            try:
                r = cr.get(url, impersonate=imp, timeout=30,
                           headers={"Referer": referer,
                                    "Accept-Language": "en-US,en;q=0.9",
                                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"})
                txt = r.text or ""
                challenged = ("Just a moment" in txt) or ("Attention Required" in txt)
                if r.status_code == 200 and not challenged:
                    return 200, txt
                last = (r.status_code, txt[:120])
            except Exception as e:
                last = (0, repr(e)[:120])
            time.sleep(0.6)
    return last


def fetch_previews():
    """解析 /previews 列表 → [{id, utcDate, leagueEn, home, away, url}]（时间为 UTC）。"""
    st, html = _get(BASE + "/previews", BASE + "/")
    if st != 200 or not html:
        return [], f"previews 抓取失败 HTTP {st}"
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return [], "缺少 bs4"
    soup = BeautifulSoup(html, "html.parser")
    cur_date = cur_league = None
    items, seen = [], set()
    for tr in soup.select("table tr"):
        d = tr.select_one("td.previews-date")
        if d:
            m = re.search(r"(\d{2})-(\d{2})-(\d{4})", d.get_text(strip=True))
            cur_date = (f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None)  # → YYYY-MM-DD (UTC)
            continue
        a_lg = tr.select_one("a[href*='/tournaments/']")
        if a_lg and len(tr.select("td")) == 1:
            cur_league = a_lg.get_text(strip=True)
            continue
        a_m = tr.select_one("a[href*='/preview']")
        if not a_m:
            continue
        href = a_m.get("href", "")
        mid_m = re.search(r"/matches/(\d+)/", href)
        title = a_m.get_text(strip=True)
        vs = title.split(" vs ")
        if not mid_m or len(vs) != 2:
            continue
        td_time = tr.select_one("td.time")
        hh = td_time.get_text(strip=True) if td_time else "00:00"
        utc_dt = f"{cur_date}T{hh}:00:00Z" if cur_date else None
        mid = mid_m.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        items.append({
            "id": mid, "utcDate": utc_dt, "leagueEn": cur_league or "",
            "home": vs[0].strip(), "away": vs[1].strip(),
            "url": BASE + href,
        })
    return items, ""


def parse_prediction(html):
    """从 preview 页提取预测比分 → (predH, predA) 或 (None, None)。
    结构：#preview-prediction → div.home .predicted-score / div.away .predicted-score
    """
    if not html:
        return None, None
    i = html.find('id="preview-prediction"')
    seg = html[i:i + 4000] if i >= 0 else html
    scores = re.findall(r'class="predicted-score">\s*(\d+)\s*<', seg)
    if len(scores) >= 2:
        return int(scores[0]), int(scores[1])
    return None, None


def fetch_results_today():
    """livescores 内嵌 JSON → {matchId: {homeScore, awayScore, finished}}（仅当日/近期）。"""
    st, html = _get(BASE + "/livescores", BASE + "/")
    out = {}
    if st != 200 or not html:
        return out
    m = re.search(r'<script[^>]*data-hypernova-key="livescores"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return out
    raw = re.sub(r"^<!--|-->$", "", m.group(1).strip()).strip()
    try:
        d = json.loads(raw)
    except Exception:
        return out
    for t in d.get("tournaments") or []:
        for ms in t.get("matches") or []:
            try:
                mid = str(ms.get("id"))
                fin = ms.get("status") == 6
                hs, asc = ms.get("homeScore"), ms.get("awayScore")
                if hs is None or asc is None:
                    continue
                out[mid] = {"homeScore": int(hs), "awayScore": int(asc), "finished": bool(fin)}
            except Exception:
                continue
    return out


def fetch_result_by_title(mid):
    """已完赛但不在当日列表的比赛 → 抓 /matches/{id}/live 用 <title> 解析赛果。"""
    st, html = _get(f"{BASE}/matches/{mid}/live", BASE + "/livescores")
    if st != 200 or not html:
        return None
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    if not t:
        return None
    m = re.search(r"^(.*?)\s+(\d+)\s*-\s*(\d+)\s+(.*?)\s+-\s+.+(Live|Highlights)", t.group(1).strip())
    if not m:
        return None
    return {"homeName": m.group(1).strip(), "homeScore": int(m.group(2)),
            "awayScore": int(m.group(3)), "awayName": m.group(4).strip()}


# ---------------- 主流程 ----------------
def _outcome(h, a):
    return "H" if h > a else ("D" if h == a else "A")


def fetch_whoscored(max_preview=MAX_PREVIEW_FETCH, max_result=MAX_RESULT_FETCH):
    now = datetime.utcnow()
    pc = _load(PREVIEW_CACHE, {})          # {mid: {predH, predA, ts}}
    hist = _load(HISTORY_PATH, {"records": {}})  # {records: {mid: {...}}}
    records = hist.get("records") or {}

    items, err = fetch_previews()
    matches = []
    if items:
        for it in items:
            lg = it.get("leagueEn") or ""
            m = {
                "id": it["id"], "utcDate": it.get("utcDate"),
                "league": LEAGUE_ZH.get(lg, lg), "leagueEn": lg,
                "home": it["home"], "away": it["away"], "url": it.get("url"),
            }
            # 北京时间（UTC+8）
            try:
                u = datetime.strptime(it["utcDate"][:19], "%Y-%m-%dT%H:%M:%S")
                bj = u + timedelta(seconds=BJ_OFFSET)
                m["date"] = bj.strftime("%Y-%m-%d")
                m["bjTime"] = bj.strftime("%H:%M")
            except Exception:
                m["date"], m["bjTime"] = "", ""
            matches.append(m)

        # 抓预测比分（带永久缓存；只补缺失的）
        # 未缓存 或 上次解析失败（predH 为空）→ 需重抓
        need = [x for x in matches
                if x["id"] not in pc or pc[x["id"]].get("predH") is None]
        n_need = len(need)
        fetched = 0
        for x in need:
            if fetched >= max_preview:
                break
            st, html = _get(x["url"], BASE + "/previews")
            fetched += 1
            time.sleep(DELAY)
            ph, pa = parse_prediction(html) if st == 200 else (None, None)
            # 解析失败不写缓存，下轮自动重试
            if ph is not None:
                pc[x["id"]] = {"predH": ph, "predA": pa, "ts": int(now.timestamp())}
            else:
                pc.pop(x["id"], None)
        if fetched:
            _save(PREVIEW_CACHE, pc)

        for x in matches:
            c = pc.get(x["id"]) or {}
            x["predH"], x["predA"] = c.get("predH"), c.get("predA")

    # ---- 赛果回填 ----
    results = fetch_results_today()
    eval_fetch = 0
    for x in matches:
        mid = x["id"]
        r = results.get(mid)
        if r is None and (mid not in records or not records[mid].get("evaluated")):
            # 判断是否大概率已开赛（UTC 开球 + 2.2h）
            try:
                u = datetime.strptime(x["utcDate"][:19], "%Y-%m-%dT%H:%M:%S")
                if (now - u).total_seconds() > 2.2 * 3600 and eval_fetch < max_result:
                    r = fetch_result_by_title(mid)
                    eval_fetch += 1
                    time.sleep(DELAY)
            except Exception:
                r = None
        if r:
            x["actH"], x["actA"], x["evaluated"] = r["homeScore"], r["awayScore"], True
            x["exactHit"] = (x.get("predH") == r["homeScore"] and x.get("predA") == r["awayScore"])
            x["outcomeHit"] = (x.get("predH") is not None and
                               _outcome(x["predH"], x["predA"]) == _outcome(r["homeScore"], r["awayScore"]))
        else:
            x["actH"] = x.get("actH")
            x["evaluated"] = bool(records.get(mid, {}).get("evaluated"))

    # ---- 历史累积（按 mid 去重，含本轮新评估） ----
    for x in matches:
        if x.get("evaluated") and x.get("predH") is not None:
            records[x["id"]] = {
                "id": x["id"], "date": x.get("date"), "league": x["league"],
                "home": x["home"], "away": x["away"],
                "predH": x["predH"], "predA": x["predA"],
                "actH": x["actH"], "actA": x["actA"],
                "exactHit": x["exactHit"], "outcomeHit": x["outcomeHit"],
            }
    hist["records"] = records
    hist["updatedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    _save(HISTORY_PATH, hist)

    # ---- 可靠参考（按联赛/球队命中率，来自 WhoScored 预测的真实回测） ----
    eva = [r for r in records.values() if r.get("date") and r.get("predH") is not None]

    def _agg(keyfn, minn, topn, namekey):
        g = {}
        for r in eva:
            k = keyfn(r)
            if not k:
                continue
            g.setdefault(k, []).append(r)
        rows = []
        for k, rs in g.items():
            n = len(rs)
            if n < minn:
                continue
            rows.append({
                namekey: k, "n": n,
                "exactHit": round(sum(1 for x in rs if x.get("exactHit")) / n * 100, 1),
                "outcomeHit": round(sum(1 for x in rs if x.get("outcomeHit")) / n * 100, 1),
            })
        rows.sort(key=lambda x: (-x["exactHit"], -x["outcomeHit"]))
        return rows[:topn]

    leagues = _agg(lambda r: r.get("league"), 3, 20, "league")
    teams = _agg(lambda r: None, 6, 8, "team")
    tg = {}
    for r in eva:
        for side in ("home", "away"):
            k = r.get(side)
            if k:
                tg.setdefault(k, []).append(r)
    trows = []
    for k, rs in tg.items():
        n = len(rs)
        if n < 3:
            continue
        trows.append({"team": k, "n": n,
                      "exactHit": round(sum(1 for x in rs if x.get("exactHit")) / n * 100, 1),
                      "outcomeHit": round(sum(1 for x in rs if x.get("outcomeHit")) / n * 100, 1)})
    trows.sort(key=lambda x: (-x["exactHit"], -x["outcomeHit"]))
    teams = trows[:20]

    n = len(eva)
    overall = {"n": n}
    if n:
        overall["exactHit"] = round(sum(1 for x in eva if x.get("exactHit")) / n * 100, 1)
        overall["outcomeHit"] = round(sum(1 for x in eva if x.get("outcomeHit")) / n * 100, 1)

    # ---- 可信度评分：联赛/球队历史命中率 → 该场预测的可信度（小样本向全局基线收缩） ----
    # 说明：仅对「未开赛」场次给建议；已完赛场次不给（避免事后诸葛）
    K = 6.0                                  # 收缩强度：样本越少越向全局基线靠
    base_dir = (overall.get("outcomeHit", 45.0) / 100) if n else 0.45
    base_exact = (overall.get("exactHit", 8.0) / 100) if n else 0.08

    lg_stat, tm_stat = {}, {}
    for r in eva:
        lg = r.get("league") or ""
        if lg:
            s = lg_stat.setdefault(lg, {"n": 0, "e": 0, "o": 0})
            s["n"] += 1
            s["e"] += 1 if r.get("exactHit") else 0
            s["o"] += 1 if r.get("outcomeHit") else 0
        for side in ("home", "away"):
            k = r.get(side)
            if k:
                s = tm_stat.setdefault(k, {"n": 0, "e": 0, "o": 0})
                s["n"] += 1
                s["e"] += 1 if r.get("exactHit") else 0
                s["o"] += 1 if r.get("outcomeHit") else 0

    def _shrink(hits, cnt, base):
        if cnt <= 0:
            return base
        return (hits + K * base) / (cnt + K)

    def _level(v, base, hi, lo):
        if v >= base * hi:
            return "high"
        if v >= base * lo:
            return "mid"
        return "low"

    OUTCOME_ZH = {"H": "主胜", "D": "平局", "A": "客胜"}

    for x in matches:
        if x.get("evaluated") or x.get("predH") is None:
            continue
        lg = lg_stat.get(x.get("league") or "", {"n": 0, "e": 0, "o": 0})
        hs = tm_stat.get(x.get("home") or "", {"n": 0, "e": 0, "o": 0})
        as_ = tm_stat.get(x.get("away") or "", {"n": 0, "e": 0, "o": 0})
        tn = hs["n"] + as_["n"]
        wl = lg["n"] / (lg["n"] + K) if lg["n"] else 0.0
        wt = tn / (tn + K) if tn else 0.0
        tot = wl + wt + 1.0                      # +1 为全局基线保底权重
        ld, le = _shrink(lg["o"], lg["n"], base_dir), _shrink(lg["e"], lg["n"], base_exact)
        if tn:
            td = (_shrink(hs["o"], hs["n"], base_dir) * hs["n"]
                  + _shrink(as_["o"], as_["n"], base_dir) * as_["n"]) / tn
            te = (_shrink(hs["e"], hs["n"], base_exact) * hs["n"]
                  + _shrink(as_["e"], as_["n"], base_exact) * as_["n"]) / tn
        else:
            td, te = base_dir, base_exact
        cd = (ld * wl + td * wt + base_dir) / tot
        ce = (le * wl + te * wt + base_exact) / tot
        dl, el = _level(cd, base_dir, 1.18, 0.95), _level(ce, base_exact, 1.35, 0.90)
        if (lg["n"] + tn) < 6:                   # 样本太少一律不给高可信
            dl = "high" if (dl == "high" and (lg["n"] + tn) >= 3) else ("mid" if dl == "high" else dl)
            el = "mid" if el == "high" else el
        oc = OUTCOME_ZH.get(_outcome(x["predH"], x["predA"]), "")
        tip = ""
        if el == "high":
            tip = f"比分可信度高，可跟注比分 {x['predH']}:{x['predA']}"
        elif dl == "high":
            tip = f"方向可信度高，可跟注方向「{oc}」"
        elif el == "mid":
            tip = f"比分中等可信，比分 {x['predH']}:{x['predA']} 可作小注参考"
        elif dl == "mid":
            tip = f"方向中等可信，方向「{oc}」可作小注参考"
        else:
            tip = f"该联赛/球队历史命中偏低，建议谨慎（方向预测 {oc}）"
        x["conf"] = {
            "dir": round(cd * 100, 1), "exact": round(ce * 100, 1),
            "dirLevel": dl, "exactLevel": el,
            "leagueN": lg["n"], "teamN": tn, "sample": lg["n"] + tn,
            "outcome": oc, "tip": tip,
        }

    # ---- 输出 ----
    matches.sort(key=lambda x: (x.get("date") or "", x.get("bjTime") or ""), reverse=True)
    # 日期升序（前端默认选中"最近的下一个比赛日"）
    date_list = sorted({x["date"] for x in matches if x.get("date")})
    his = sorted(eva, key=lambda x: (x.get("date") or ""), reverse=True)
    hdate_list = sorted({x["date"] for x in his}, reverse=True)

    out = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "WhoScored Previews（官方人工预测比分）",
        "isolation": True,
        "note": err or ("抓取自 whoscored.com/previews，人工撰写赛前预测；时间为北京时间；竞彩编号对照仅作标签"),
        "matches": matches,
        "dateList": date_list,
        "history": his[:800],
        "historyDateList": hdate_list,
        "reliable": {"overall": overall, "leagues": leagues, "teams": teams,
                     "baseline": {"dir": round(base_dir * 100, 1), "exact": round(base_exact * 100, 1)}},
    }
    _save(OUT_PATH, out)
    print(f"WhoScored 预测：{len(matches)} 场（含预测比分 {sum(1 for x in matches if x.get('predH') is not None)} 场），"
          f"历史回测 {n} 场（精确命中 {overall.get('exactHit', '-')}%、方向 {overall.get('outcomeHit', '-')}%），"
          f"新抓 preview {min(n_need, max_preview)} 页 / 补赛果 {eval_fetch} 场 → {OUT_PATH}")
    return out


if __name__ == "__main__":
    fetch_whoscored()
