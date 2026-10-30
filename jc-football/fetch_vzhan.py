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


def _outcome(h, a):
    """比分方向：主胜 H / 平 D / 客胜 A。"""
    if h is None or a is None:
        return None
    return "H" if h > a else ("A" if a > h else "D")


def _final_from_mi(mi, kickoff):
    """从 report/info 的 match_info 读真实完赛比分；未完赛返回 (None, None)。

    判定「已完赛」：开球时间须已过至少 3 小时(比赛必结束)，避免把未开赛
    初始化的 0-0 误当赛果。完赛后 V站 的 home_team_score/away_team_score
    即为真实结果，零跨文件对齐。
    """
    if not isinstance(mi, dict) or not kickoff:
        return None, None
    try:
        kt = datetime.datetime.strptime(str(kickoff)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None, None
    if (datetime.datetime.now() - kt).total_seconds() < 3 * 3600:
        return None, None
    hs, aw = mi.get("home_team_score"), mi.get("away_team_score")
    if hs in (None, "") or aw in (None, ""):
        return None, None
    if not str(hs).strip().lstrip("-").isdigit() or not str(aw).strip().lstrip("-").isdigit():
        return None, None
    try:
        return int(hs), int(aw)
    except Exception:
        return None, None


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
                # v1.7.77：原生赛果回填——从 report/info 的 match_info 读真实比分
                fH, fA = _final_from_mi(rep.get("match_info"), m.get("match_time"))
                if fH is not None:
                    m["finalH"], m["finalA"] = fH, fA
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
    """完赛且有预测的场次归档(含精确命中/方向命中), 供 accuracy_ab 三源对比"""
    hist = load_json(HIST, {"updatedAt": "", "matches": {}})
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    for m in matches:
        if not (m.get("hasPred") and m.get("finalH") is not None):
            continue
        key = "%s|%s|%s" % (m.get("jcNum") or m.get("date"), m["home"], m["away"])
        if key in hist["matches"]:
            continue
        fh, fa = m["finalH"], m["finalA"]
        exact = (m["predH"] == fh and m["predA"] == fa)
        oc = _outcome(m["predH"], m["predA"]) == _outcome(fh, fa)
        hist["matches"][key] = {
            "jcNum": m.get("jcNum"), "date": m.get("date"),
            "league": m.get("league"),
            "home": m["home"], "away": m["away"],
            "predH": m["predH"], "predA": m["predA"],
            "finalH": fh, "finalA": fa,
            "exactHit": bool(exact), "outcomeHit": bool(oc),
        }
        added += 1
    hist["updatedAt"] = now
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    if added:
        nh = sum(1 for v in hist["matches"].values() if v.get("exactHit"))
        print("  历史归档 +%d 场 (共 %d，精确命中 %d)" % (added, len(hist["matches"]), nh))


def main():
    t0 = time.time()
    print("== V站(呼死狗) 竞彩情报抓取 ==")
    matches, total = fetch_all()
    withpred = sum(1 for m in matches if m["hasPred"])
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
