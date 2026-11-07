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

# 强制北京时间（CI 容器为 UTC，时间戳与完赛判定必须用东八区）
import os as _os
_os.environ.setdefault("TZ", "Asia/Shanghai")
time.tzset()
import urllib.parse

BASE = "https://mapi.shemen365.com"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
HEADERS = {"User-Agent": UA, "Accept": "application/json"}
OUT = "vzhan_data.json"
HIST = "vzhan_history.json"
DELAY = 0.3   # v1.7.118：26 场双请求原 0.7s 间隔需 45s，压缩到 ~20s 以免被预算门槛跳过


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


def _wonderful(v):
    """解析 V站 wonderful_report：对无比分预测的场也给出中文情报标题
    （伤停/阵容/状态）。belong_team: '1'=主队 '2'=客队 其余=综合。
    注意 content 是「请下载最新版」引流文案，无实质，仅取 title。"""
    out = []
    for it in (v or []):
        if not isinstance(it, dict):
            continue
        t = (it.get("title") or "").strip()
        if not t:
            continue
        side = it.get("belong_team")
        s = "home" if side == "1" else ("away" if side == "2" else "mid")
        out.append({"side": s, "title": t})
    return out


def _outcome(h, a):
    """比分方向：主胜 H / 平 D / 客胜 A。"""
    if h is None or a is None:
        return None
    return "H" if h > a else ("A" if a > h else "D")


HISTORY_PATH = "history.json"
# 竞彩官方开奖回溯索引（v1.7.126）：(date, num) -> (finalH, finalA, record)。
# V站 抓取的是「当天在售赛程」，快照时刻比赛多未开踢，等踢完这批场次已滚出
# 销售窗口再也抓不到 —— 原生 match_info 完赛回填因此长期 0 场。改用竞彩官方
# 开奖档案按「日期 + 竞彩编号」严格对齐，赛果一开奖即可归档，零跨文件猜测。
_OFFICIAL = None


def _load_official():
    global _OFFICIAL
    if _OFFICIAL is not None:
        return _OFFICIAL
    idx = {}
    try:
        with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                               HISTORY_PATH), encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("records", []):
            res = r.get("result") or {}
            if res.get("cancel") or res.get("home") is None or res.get("away") is None:
                continue
            idx.setdefault((r.get("date"), r.get("num")), (int(res["home"]), int(res["away"]), r))
    except Exception as e:
        print("  [warn] 读取 history.json 官方赛果失败: %s" % e)
    _OFFICIAL = idx
    return idx


def _name_sim(a, b):
    """队名字符重合度：V站简称 vs 竞彩全称（巴黎圣曼 / 巴黎圣日尔曼）。"""
    sa, sb = set(str(a or "").replace(" ", "")), set(str(b or "").replace(" ", ""))
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


# 高信心口径（与前端 vzConfidence 完全一致）：V站 预测方向 == 模型(Poisson) 预测方向。
# V站接口本身无「信心/星级」字段，故采用多源共识；该标记在「抓取时」即固化到
# vzhan_data.json，赛果回填时原样归档 —— 否则等比赛踢完，该场的模型预测快照
# 早已随竞彩销售窗口滚动消失，事后根本无法还原「当时是否高信心」。
_MODEL_PREDS = None  # 懒加载缓存：[(jcNum, home, away, (h,a))]


def _load_model_preds():
    """从竞彩 matches.json 读取模型预测，供高信心判定（fetch_vzhan 在 fetch_daily
    之后运行，matches.json 已含 modelPred）。"""
    global _MODEL_PREDS
    if _MODEL_PREDS is not None:
        return _MODEL_PREDS
    out = []
    try:
        with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                               "matches.json"), encoding="utf-8") as f:
            data = json.load(f)
        for m in data.get("matches", []):
            mp = m.get("modelPred") or {}
            if mp.get("h") is None or mp.get("a") is None:
                continue
            out.append((m.get("num"), m.get("home"), m.get("away"),
                        (int(mp["h"]), int(mp["a"]))))
    except Exception as e:
        print("  [warn] 读取 matches.json 模型预测失败: %s" % e)
    _MODEL_PREDS = out
    return out


def _match_model_pred(jc_num, home, away):
    """按竞彩编号优先、队名兜底匹配模型预测，返回 (h,a) 或 None。"""
    preds = _load_model_preds()
    for jn, h, a, pa in preds:
        if jc_num and jn and jn == jc_num:
            return pa
    for jn, h, a, pa in preds:
        if h and a and home and away and (h == home and a == away):
            return pa
    return None


def vz_conf(pred_h, pred_a, jc_num, home, away):
    """判定该 V站 预测「是否高信心」（赛前口径，与前端 vzConfidence 同源）。

    返回 (highConf, why, confAvailable)：
      · 有模型预测且方向一致 -> (True, 共识, True)
      · 有模型预测但方向分歧 -> (False, 分歧, True)
      · 无模型预测快照        -> (False, 无法判定, False)，单列不混入任一组
    方向与赛果无关，未开赛也可判定，因此抓取时即可固化。"""
    vo = _outcome(pred_h, pred_a)
    if vo is None:
        return False, "", False
    mp = _match_model_pred(jc_num, home, away)
    if not mp:
        return False, "赛前无模型预测快照，无法判定", False
    mo = _outcome(mp[0], mp[1])
    if mo and mo == vo:
        return True, "V站·模型 方向共识", True
    return False, "V站·模型 方向分歧", True


def vz_high_conf(pred_h, pred_a, final_h, final_a, jc_num, home, away):
    """兼容旧签名（accuracy_ab 等外部调用）：返回 (bool_high, why)。"""
    hc, why, _ = vz_conf(pred_h, pred_a, jc_num, home, away)
    return hc, why


def attach_conf(matches):
    """抓取时把高信心判定固化进每条 V站 预测（赛前快照，赛后不重算）。"""
    n = 0
    for m in matches:
        if not (m.get("hasPred") and m.get("predH") is not None and m.get("predA") is not None):
            m["highConf"] = False
            m["confWhy"] = ""
            m["confAvailable"] = False
            continue
        hc, why, ok = vz_conf(m["predH"], m["predA"], m.get("jcNum"),
                              m.get("home"), m.get("away"))
        m["highConf"] = bool(hc)
        m["confWhy"] = why
        m["confAvailable"] = bool(ok)
        mp = _match_model_pred(m.get("jcNum"), m.get("home"), m.get("away"))
        m["modelPred"] = list(mp) if mp else None
        n += int(ok)
    print("  赛前高信心固化：%d 场可判定（共 %d 场有比分预测）"
          % (n, sum(1 for m in matches if m.get("hasPred"))), flush=True)
    return matches


def attach_official_result(matches):
    """用竞彩官方开奖档案补全 finalH/finalA（V站 原生赛果优先，官方兜底）。"""
    off = _load_official()
    n = 0
    for m in matches:
        if m.get("finalH") is not None and m.get("finalA") is not None:
            continue
        hit = off.get((m.get("date"), m.get("jcNum")))
        if not hit:
            continue
        fh, fa, r = hit
        # 主键已是 (date, jcNum)，队名相似度只用于拦截明显错配
        if min(_name_sim(m.get("home"), r.get("home")),
               _name_sim(m.get("away"), r.get("away"))) < 0.25:
            continue
        m["finalH"], m["finalA"] = fh, fa
        m["resultSource"] = "竞彩官方开奖回溯 (history.json)"
        n += 1
    if n:
        print("  官方赛果回填 %d 场（history.json）" % n, flush=True)
    return matches


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
                "vz_reports": [],
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
                # v1.7.98：wonderful_report 是 V站 对"无比分预测场"也给的
                # 中文情报标题（伤停/阵容/状态），此前完全漏抓，导致大量
                # 有情报的场在网页被判成"未给预测"。补抓并随前端渲染。
                m["vz_reports"] = _wonderful(rep.get("wonderful_report"))
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
    """完赛且有预测的场次归档（含精确命中/方向命中/高信心），供统计与三源对比。

    v1.7.126：赛果来源改为「V站 原生 match_info 优先 + 竞彩官方开奖兜底」；
    高信心沿用抓取时固化的赛前判定，绝不在赛后用已滚动消失的模型快照重算。
    """
    hist = load_json(HIST, {"updatedAt": "", "matches": {}})
    if not isinstance(hist.get("matches"), dict):
        hist["matches"] = {}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = upgraded = 0
    for m in matches:
        if not (m.get("hasPred") and m.get("finalH") is not None and m.get("finalA") is not None):
            continue
        key = "%s|%s|%s" % (m.get("jcNum") or m.get("date"), m["home"], m["away"])
        old = hist["matches"].get(key)
        if old and (old.get("confAvailable") or not m.get("confAvailable")):
            continue  # 幂等：已有归档不覆盖；仅当新记录可判定、旧的不可判定时升级
        fh, fa = m["finalH"], m["finalA"]
        vo = _outcome(m["predH"], m["predA"])
        if m.get("confAvailable"):
            hc, why, ok = bool(m.get("highConf")), m.get("confWhy") or "", True
        else:
            hc, why, ok = vz_conf(m["predH"], m["predA"], m.get("jcNum"),
                                  m["home"], m["away"])
        rec = {
            "jcNum": m.get("jcNum"), "date": m.get("date"),
            "league": m.get("league"),
            "home": m["home"], "away": m["away"],
            "predH": m["predH"], "predA": m["predA"],
            "finalH": fh, "finalA": fa,
            "exactHit": bool(m["predH"] == fh and m["predA"] == fa),
            "outcomeHit": bool(vo == _outcome(fh, fa)),
            "highConf": bool(hc), "confWhy": why, "confAvailable": bool(ok),
            "modelPred": m.get("modelPred"),
            "resultSource": m.get("resultSource") or "V站 report/info match_info",
            "source": "fetch_vzhan.py 完赛归档",
        }
        hist["matches"][key] = rec
        if old:
            upgraded += 1
        else:
            added += 1
    hist["updatedAt"] = now
    hist.setdefault("source", "V站 赛前预测 + 竞彩官方开奖回溯")
    hist.setdefault("meta", {})["version"] = "v1.7.126"
    hist["meta"]["highConfRule"] = "V站预测方向 == 模型(modelPred)预测方向；与前端 vzConfidence() 同口径"
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    if added or upgraded:
        nh = sum(1 for v in hist["matches"].values() if v.get("exactHit"))
        hot = sum(1 for v in hist["matches"].values() if v.get("highConf"))
        print("  历史归档 +%d 场 / 升级 %d 场 (共 %d，精确命中 %d，🔥高信心 %d)"
              % (added, upgraded, len(hist["matches"]), nh, hot), flush=True)


def main():
    t0 = time.time()
    print("== V站(呼死狗) 竞彩情报抓取 ==")
    matches, total = fetch_all()
    # v1.7.118：接口临时返回空时绝不能用空数组覆盖掉上一轮完整情报，
    # 否则「抓到了却把数据抹成 0」比不抓更糟（用户看到 V站 空空如也）。
    if total == 0 or not matches:
        print("!! 接口返回 0 场（临时不可用/限流），保留上次 vzhan_data.json，本次不写盘", flush=True)
        return 1
    # v1.7.126：赛前固化高信心 + 官方赛果回填，二者都在写盘/归档之前完成
    attach_conf(matches)
    attach_official_result(matches)
    withpred = sum(1 for m in matches if m["hasPred"])
    withhot = sum(1 for m in matches if m.get("highConf"))
    withfinal = sum(1 for m in matches if m.get("finalH") is not None)
    update_history(matches)
    out = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "V站/呼死狗 (mapi.shemen365.com)",
        "total": total,
        "withPred": withpred,
        "withHighConf": withhot,
        "withResult": withfinal,
        "matches": matches,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("== 完成: 竞彩 %d 场 | 有预测 %d 场 | 🔥高信心 %d 场 | 有赛果 %d 场 | 用时 %.1fs -> %s =="
          % (total, withpred, withhot, withfinal, time.time() - t0, OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
