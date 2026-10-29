#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V站(呼死狗) 竞彩情报抓取器
数据源: mapi.shemen365.com (V站 H5 API, 免鉴权)
  - GET /choose/match/football                       竞彩完整列表(含竞彩编号/中文队名/match_id)
  - GET /new-article/report/info?sport_id=1&match_id=X  呼死狗情报(预测比分+主客队新闻+赛事预测)
输出: vzhan_data.json
  matches: [{jcNum, match_id, date, match_time, home, away, league,
             predH, predA, home_news[], away_news[], forecasts[], hasPred}]
"""
import json
import time
import datetime
import urllib.request
import urllib.parse

BASE = "https://mapi.shemen365.com"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
HEADERS = {"User-Agent": UA, "Accept": "application/json"}
OUT = "vzhan_data.json"
HIST = "vzhan_history.json"
DELAY = 0.7


def _get(path, params=None, retries=3):
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = BASE + path + qs
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read().decode("utf-8"))
            if isinstance(j, dict) and j.get("code") == 0:
                return j.get("data")
            last_err = "code=%s" % (j.get("code") if isinstance(j, dict) else j)
        except Exception as e:
            last_err = str(e)[:80]
        time.sleep(1.2 * (i + 1))
    print("  [warn] GET %s 失败: %s" % (path, last_err))
    return None


def _txt_list(v):
    out = []
    for it in (v or []):
        if isinstance(it, str):
            t = it.strip()
        elif isinstance(it, dict):
            t = str(it.get("content") or it.get("text") or it.get("desc") or "").strip()
        else:
            t = str(it).strip()
        if t:
            out.append(t)
    return out


def fetch_all():
    days = _get("/choose/match/football") or []
    matches = []
    total = 0
    for day in days:
        dstr = day.get("date") or ""
        for it in (day.get("match_list") or []):
            total += 1
            mi = it.get("match_info") or {}
            ti = it.get("team_info") or {}
            lo = it.get("lottery_info") or {}
            tour = it.get("tournament_info") or {}
            mid = str(mi.get("match_id") or "")
            m = {
                "jcNum": lo.get("round") or "",
                "issue": lo.get("issue") or "",
                "match_id": mid,
                "date": dstr,
                "match_time": mi.get("match_time") or "",
                "home": ti.get("home_team_name") or "",
                "away": ti.get("away_team_name") or "",
                "league": tour.get("tournament_name") or "",
                "predH": None,
                "predA": None,
                "home_news": [],
                "away_news": [],
                "forecasts": [],
                "injury_note": "",
                "hasPred": False,
            }
            rep = _get("/new-article/report/info", {"sport_id": 1, "match_id": mid})
            if rep:
                hs = rep.get("home_score_forecast")
                aw = rep.get("away_score_forecast")
                try:
                    if hs is not None and aw is not None and str(hs) != "" and str(aw) != "":
                        m["predH"] = int(hs)
                        m["predA"] = int(aw)
                        m["hasPred"] = True
                except (TypeError, ValueError):
                    pass
                m["home_news"] = _txt_list(rep.get("home_news_list"))
                m["away_news"] = _txt_list(rep.get("away_news_list"))
                m["forecasts"] = _txt_list(rep.get("match_forecast_list"))
                pi = rep.get("player_injury")
                if isinstance(pi, dict):
                    note = pi.get("analysis") or pi.get("analysi") or ""
                    if note:
                        m["injury_note"] = str(note).strip()[:400]
            tag = "V站有预测" if m["hasPred"] else "V站无预测"
            print("  %s %s vs %s | %s" % (m["jcNum"], m["home"], m["away"], tag))
            matches.append(m)
            time.sleep(DELAY)
    return matches, total


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def update_history(matches):
    """完赛且有预测的场次归档, 供 A/B 命中率统计"""
    hist = load_json(HIST, {"updatedAt": "", "matches": {}})
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    for m in matches:
        if not (m.get("hasPred") and m.get("finalH") is not None):
            continue
        key = "%s|%s|%s" % (m.get("jcNum") or m.get("date"), m["home"], m["away"])
        if key in hist["matches"]:
            continue
        hist["matches"][key] = {
            "jcNum": m.get("jcNum"), "date": m.get("date"),
            "home": m["home"], "away": m["away"],
            "predH": m["predH"], "predA": m["predA"],
            "finalH": m.get("finalH"), "finalA": m.get("finalA"),
        }
        added += 1
    hist["updatedAt"] = now
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    if added:
        print("  历史归档 +%d 场 (共 %d)" % (added, len(hist["matches"])))


def merge_final_scores(matches):
    """从 matches.json 读完赛比分, 标记到本场列表(供历史归档)"""
    try:
        with open("matches.json", encoding="utf-8") as f:
            mj = json.load(f)
        ms = mj.get("matches") if isinstance(mj, dict) else mj
        idx = {}
        for it in (ms or []):
            jc = str(it.get("jcNum") or it.get("num") or "")
            h = str(it.get("home") or it.get("homeName") or "")
            a = str(it.get("away") or it.get("awayName") or "")
            idx[(jc, h, a)] = it
            idx[(jc,)] = it
        for m in matches:
            it = idx.get((m["jcNum"], m["home"], m["away"])) or idx.get((m["jcNum"],))
            if not it:
                continue
            for fh, fa, st in (("finalH", "finalA", "status"), ("homeScore", "awayScore", None)):
                hv, av = it.get(fh), it.get(fa)
                if hv is not None and av is not None:
                    if st is None or str(it.get(st) or "").lower() in ("finished", "end", "完场", "已完赛"):
                        m["finalH"], m["finalA"] = int(hv), int(av)
                        break
    except Exception as e:
        print("  [warn] 合并完赛比分失败:", str(e)[:80])


def main():
    t0 = time.time()
    print("== V站(呼死狗) 竞彩情报抓取 ==")
    matches, total = fetch_all()
    withpred = sum(1 for m in matches if m["hasPred"])
    merge_final_scores(matches)
    update_history(matches)
    out = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "V站/呼死狗 (mapi.shemen365.com)",
        "total": total,
        "withPred": withpred,
        "matches": matches,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("== 完成: 竞彩 %d 场 | 有预测 %d 场 | 用时 %.1fs -> %s ==" % (total, withpred, time.time() - t0, OUT))


if __name__ == "__main__":
    main()
