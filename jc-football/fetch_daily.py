#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞彩足球每日数据管道
抓取官方接口 -> 解析 -> 规则判定 -> 输出 matches.json + history.json
数据源:
  - 赛事/赔率: webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry
  - 概率/支持率: webapi.sporttery.cn/gateway/jc/common/getSupportRateV1.qry
  - 单场开奖回填: webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry
history.json 持久化每次判定的记录，比赛结束后自动回填赛果并标注命中/错误。
"""
import json, time, urllib.request, urllib.parse, sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fetch_hkmo import fetch_and_match as fetch_hkmo_data
except Exception:
    fetch_hkmo_data = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
CALC_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&clientCode=3001"
SUPPORT_URL = "https://webapi.sporttery.cn/gateway/jc/common/getSupportRateV1.qry"
BONUS_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "rules_config.json")

# 规则组基础配置（threshold 可被回测动态调整）
DEFAULT_CONFIG = {
    "组1热度深负":  {"base": 15, "threshold": 15, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组6深度过热":  {"base": 22, "threshold": 22, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组2正路确认":  {"base": 8,  "threshold": 8,  "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组3冷门监控":  {"base": 10, "threshold": 10, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组4异常水位":  {"base": 87.0, "threshold": 87.0, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组7资金流入":  {"base": 3.0, "threshold": 3.0, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组8资金背离":  {"base": 3.0, "threshold": 3.0, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组9凯利价值":  {"base": 1.5, "threshold": 1.5, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组10排名压制": {"base": 8,  "threshold": 8,  "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组11进球预期": {"base": 3.0, "threshold": 3.0, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组12半场掌控": {"base": 52.0, "threshold": 52.0, "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
    "组13攻防压制": {"base": 1.0,  "threshold": 1.0,  "samples": 0, "hit": 0, "miss": 0, "hitRate": None},
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, dict(v))
            return cfg
        except Exception as e:
            print("  规则配置读取失败，用默认值:", e, flush=True)
    return {k: dict(v) for k, v in DEFAULT_CONFIG.items()}

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)

def backtest_rules(cfg, records):
    """用已回填的历史记录统计各规则组命中率，样本充足时动态调参"""
    stats = {}
    for r in records:
        if r.get("hitFlag") not in ("hit", "miss"):
            continue
        for h in r.get("hits") or []:
            g = h.get("g")
            if not g:
                continue
            s = stats.setdefault(g, {"hit": 0, "miss": 0})
            if r["hitFlag"] == "hit":
                s["hit"] += 1
            else:
                s["miss"] += 1
    changed = []
    for g, s in stats.items():
        if g not in cfg:
            continue
        n = s["hit"] + s["miss"]
        rate = round(s["hit"] / n * 100, 1)
        cfg[g]["samples"] = n
        cfg[g]["hit"] = s["hit"]
        cfg[g]["miss"] = s["miss"]
        cfg[g]["hitRate"] = rate
        # 样本不足 20 场不调参，避免噪声
        if n >= 20:
            if rate < 40:
                new_t = round(cfg[g]["threshold"] + 2, 1)
                if new_t != cfg[g]["threshold"]:
                    changed.append(f"{g}: 命中率{rate}%<40%，阈值 {cfg[g]['threshold']}→{new_t}")
                cfg[g]["threshold"] = new_t
            elif rate > 65:
                new_t = round(max(cfg[g]["base"] - 1, cfg[g]["threshold"] - 1), 1)
                if new_t != cfg[g]["threshold"]:
                    changed.append(f"{g}: 命中率{rate}%>65%，阈值 {cfg[g]['threshold']}→{new_t}")
                cfg[g]["threshold"] = new_t
    if changed:
        print("  回测调参: " + "；".join(changed), flush=True)
    return cfg

def fetch_odds_history(mid):
    """抓取单场赔率历史，返回各方向开盘→当前变动百分比和初盘赔率

    带 3 小时 TTL 缓存：赔率历史属慢变数据，每 30 分钟刷新一次无必要，
    缓存后每天外部请求从 ~360 次降到 ~60 次，显著降低 sporttery 反爬风险。
    """
    from webcache import ttl_fetch

    def _go():
        url = BONUS_URL + "?clientCode=3001&matchId=" + str(mid)
        fb = fetch(url)
        oh = (fb.get("value") or {}).get("oddsHistory") or {}
        had_list = oh.get("hadList") or []
        hhad_list = oh.get("hhadList") or []
        mov = {"h": None, "d": None, "a": None}
        had_open = None
        hhad_open = None
        if len(had_list) >= 2:
            first, last = had_list[0], had_list[-1]
            had_open = {"h": to_float(first.get("h")), "d": to_float(first.get("d")), "a": to_float(first.get("a"))}
            for d in ("h", "d", "a"):
                try:
                    f, l = float(first[d]), float(last[d])
                    if f > 0:
                        mov[d] = round((l - f) / f * 100, 1)
                except (TypeError, ValueError, KeyError):
                    pass
        if len(hhad_list) >= 2:
            first_h = hhad_list[0]
            hhad_open = {"h": to_float(first_h.get("h")), "d": to_float(first_h.get("d")), "a": to_float(first_h.get("a")), "goal": first_h.get("goal")}
        return {"mov": mov, "hadOpen": had_open, "hhadOpen": hhad_open}

    try:
        return ttl_fetch("odds_hist:" + str(mid), _go, ttl_hours=3)
    except Exception as e:
        print(f"  赔率历史失败 {mid}: {e}", flush=True)
        return {"mov": {"h": None, "d": None, "a": None}, "hadOpen": None, "hhadOpen": None}

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def fmt_rate(dic, key):
    v = dic.get(key) if dic else None
    return to_float(v)

def load_history():
    p = os.path.join(HERE, "history.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("records", [])
        except Exception as e:
            print("  history.json 读取失败，重新开始:", e, flush=True)
    return []

def load_bd():
    """读取北单赛程+开奖（与北单管道联动：近况/交锋/交叉验证/亚盘）"""
    bd = {"matches": [], "records": []}
    for key, p in (("matches", "bd_matches.json"), ("records", "bd_history.json")):
        fp = os.path.join(HERE, p)
        if os.path.exists(fp):
            try:
                with open(fp, encoding="utf-8") as f:
                    d = json.load(f)
                bd[key] = d.get("matches" if key == "matches" else "records", []) or d.get("records", [])
            except Exception:
                pass
    return bd

def team_form(records, team, goal_side=None, limit=6):
    """从历史开奖库统计某队近期战绩（主客合计）：返回 {n,w,d,l,wr,form,home_wr,away_wr,hdp_wr}
    goal_side: 'h'/'a' 表示该队是主/客时如何算盘路"""
    rows = []
    for r in records:
        sc = r.get("score") or ""
        if not sc or "-" not in str(sc) and ":" not in str(sc):
            continue
        parts = re.split(r"[-:]", str(sc))
        if len(parts) != 2:
            continue
        try:
            hs, as_ = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if r.get("home") == team:
            rows.append(("h", hs, as_, r.get("hgoal") or 0, str(r.get("time") or r.get("date") or "")[-11:]))
        elif r.get("away") == team:
            rows.append(("a", hs, as_, r.get("agoal") or 0, str(r.get("time") or r.get("date") or "")[-11:]))
    rows.sort(key=lambda x: x[4], reverse=True)
    rows = rows[:limit]
    if not rows:
        return None
    w = d = l = 0
    hw = aw = hn = an = 0
    hdp_w = hdp_n = 0
    form = []
    for side, hs, as_, g, _ in rows:
        if hs > as_:
            w += 1; form.append("胜")
            if side == "h": hw += 1
            else: aw += 1
        elif hs == as_:
            d += 1; form.append("平")
        else:
            l += 1; form.append("负")
        if side == "h": hn += 1
        else: an += 1
        # 盘路（让球后）
        if side == "h":
            hdp_n += 1
            if hs + g > as_: hdp_w += 1
        else:
            hdp_n += 1
            if as_ + g > hs: hdp_w += 1
    return {
        "n": len(rows), "w": w, "d": d, "l": l,
        "wr": round(w / len(rows) * 100),
        "form": "".join(form),
        "home_wr": round(hw / hn * 100) if hn else None,
        "away_wr": round(aw / an * 100) if an else None,
        "hdp_wr": round(hdp_w / hdp_n * 100) if hdp_n else None,
    }

def attack_stats(records, team, limit=6):
    """从历史开奖库统计某队近期场均进球/失球（近 limit 场有比分的比赛）。
    返回 {n, gf, ga}；样本不足返回 None"""
    rows = []
    for r in records:
        sc = str(r.get("score") or "")
        if not sc or ("-" not in sc and ":" not in sc):
            continue
        parts = re.split(r"[-:]", sc)
        if len(parts) != 2:
            continue
        try:
            hs, as_ = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        tm = str(r.get("time") or r.get("date") or "")[-11:]
        if r.get("home") == team:
            rows.append((hs, as_, tm))
        elif r.get("away") == team:
            rows.append((as_, hs, tm))
    rows.sort(key=lambda x: x[2], reverse=True)
    rows = rows[:limit]
    if not rows:
        return None
    gf = sum(x[0] for x in rows) / len(rows)
    ga = sum(x[1] for x in rows) / len(rows)
    return {"n": len(rows), "gf": round(gf, 2), "ga": round(ga, 2)}

def team_h2h(records, home, away, limit=6):
    """从历史开奖库匹配两队直接交手（方向不限），按时间倒序取 limit 场：
    返回 {n, hw, dd, aw, last:[比分...]}，以 home 视角"""
    rows = []
    for r in records:
        sc = str(r.get("score") or "")
        parts = re.split(r"[-:]", sc)
        if len(parts) != 2:
            continue
        try:
            hs, as_ = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        h, a = r.get("home"), r.get("away")
        if h == home and a == away:
            rows.append(("h", hs, as_, str(r.get("time") or "")[-11:]))
        elif h == away and a == home:
            rows.append(("a", as_, hs, str(r.get("time") or "")[-11:]))
    rows.sort(key=lambda x: x[3], reverse=True)
    rows = rows[:limit]
    if not rows:
        return None
    hw = dd = aw = 0
    last = []
    for side, hs, as_, _ in rows:
        last.append(f"{hs}-{as_}")
        if side == "h":
            if hs > as_: hw += 1
            elif hs == as_: dd += 1
            else: aw += 1
        else:
            if hs > as_: aw += 1
            elif hs == as_: dd += 1
            else: hw += 1
    return {"n": len(rows), "hw": hw, "dd": dd, "aw": aw, "last": " ".join(last)}

def merge_match_records():
    """合并竞彩history为统一赛果库（按队名+日期去重），供近况/H2H使用"""
    recs = []
    for r in load_history():
        if not r.get("result"):
            continue
        res = r["result"]
        if res.get("cancel") or not res.get("score"):
            continue
        sc = re.split(r"[-:]", str(res.get("score", "")))
        if len(sc) != 2:
            continue
        recs.append({"home": r["home"], "away": r["away"], "score": res["score"],
                     "hgoal": int(r.get("goal") or 0), "agoal": 0,
                     "time": r.get("kickoff") or ""})
    # 去重：同一队对同一队同一天只留一条
    seen = set()
    out = []
    for r in recs:
        key = (r["home"], r["away"], str(r["time"])[:10])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

# ---------------- 500 球队页近况（服务端渲染，含近期比分/盘路） ----------------
def fetch_html(url, ref, decode="utf-8", t=15):
    req = urllib.request.Request(url, headers={
        "Referer": ref, "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9"})
    return urllib.request.urlopen(req, timeout=t).read().decode(decode, "ignore")

def parse_team_form_html(html, tid, team_name, limit=6):
    """解析 500 球队页"近期战绩"表：每行一场比赛，按 球队ID 归属主/客，返回与 team_form 同结构"""
    i = html.find("近期战绩")
    if i < 0:
        return None
    seg = html[i:i+40000]
    rows = re.findall(r"<tr>.*?</tr>", seg, re.S)
    out = []
    for tr in rows[1:]:  # 跳过表头行
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 8:
            continue
        def txt(s):
            s = re.sub(r"<[^>]+>", "", s)
            return s.replace("&nbsp;", " ").strip()
        dt = txt(tds[1])
        hm = re.search(r'team/(\d+)/', tds[2])
        am = re.search(r'team/(\d+)/', tds[4])
        if not hm or not am:
            continue
        # 比分：形如 3:0 或 2:1，数字可能带/不带 span
        scm = re.search(r'>(\d+)</span>\s*:\s*<span[^>]*>(\d+)<', tds[3]) or re.search(r'>(\d+)</span>\s*:\s*(\d+)', tds[3])
        if not scm:
            continue
        hs, as_ = int(scm.group(1)), int(scm.group(2))
        res = txt(tds[5])
        res = "胜" if res.endswith("胜") else ("平" if res.endswith("平") else ("负" if res.endswith("负") else ""))
        hdp = txt(tds[6])
        hdp = hdp if hdp in ("赢", "输", "走") else ""
        if hm.group(1) == str(tid):
            side = "h"
        elif am.group(1) == str(tid):
            side = "a"
        else:
            continue
        out.append({"side": side, "hs": hs, "as": as_, "res": res, "hdp": hdp,
                    "league": txt(tds[0]), "date": dt})
    out = out[:limit]
    if not out:
        return None
    w = d = l = hw = aw = hn = an = hdp_w = hdp_n = 0
    form = []
    gf_sum = ga_sum = 0
    for r in out:
        if r["hs"] > r["as"]:
            w += 1; form.append("胜")
            if r["side"] == "h": hw += 1
            else: aw += 1
        elif r["hs"] == r["as"]:
            d += 1; form.append("平")
        else:
            l += 1; form.append("负")
        if r["side"] == "h": hn += 1
        else: an += 1
        if r["hdp"]:
            hdp_n += 1
            if r["hdp"] == "赢":
                hdp_w += 1
        gf_sum += r["hs"]; ga_sum += r["as"]
    return {
        "n": len(out), "w": w, "d": d, "l": l,
        "wr": round(w / len(out) * 100),
        "form": "".join(form),
        "home_wr": round(hw / hn * 100) if hn else None,
        "away_wr": round(aw / an * 100) if an else None,
        "hdp_wr": round(hdp_w / hdp_n * 100) if hdp_n else None,
        "gf": round(gf_sum / len(out), 2) if out else None,
        "ga": round(ga_sum / len(out), 2) if out else None,
        "src": "500球队页",
    }

def fetch_team_form(tid, team_name):
    """抓 500 球队页近况（带 6 小时 TTL 缓存，跨天复用）

    原实现按「自然日」缓存，跨天会重抓 12 队；改为统一 web_cache 的 6 小时 TTL，
    跨天边界也复用上一份近况（球队近况 6 小时变一次对分析无实质影响），
    进一步把 500 球队页请求压到约 48 次/天。
    """
    from webcache import ttl_fetch

    def _go():
        html = fetch_html(f"https://liansai.500.com/team/{tid}/", "https://liansai.500.com/", "gbk")
        time.sleep(0.25)
        return parse_team_form_html(html, tid, team_name)

    try:
        return ttl_fetch("team_form:" + str(tid), _go, ttl_hours=6)
    except Exception as e:
        print(f"  球队页失败 {tid}: {e}", flush=True)
        return None

def fetch_jczq_teamids():
    """抓 500 竞彩页的 场次编号->主客球队500ID 映射（用于抓球队页近况）

    带含日期的 6 小时 TTL 缓存：当天映射不变，30 次/天运行只用 ~4 次真实抓取。
    """
    from webcache import ttl_fetch

    today = time.strftime("%Y-%m-%d")

    def _go():
        html = fetch_html("https://trade.500.com/jczq/", "https://trade.500.com/", "gbk")
        out = {}
        for tr in re.findall(r"<tr[^>]*data-matchnum=\"[^\"]+\"[^>]*>", html):
            g = lambda k: (re.search(k + r'="([^"]*)"', tr) or [None, ""])[1]
            num = g("data-matchnum")
            if not num:
                continue
            out[num] = {"home_id": g("data-homeid"), "away_id": g("data-awayid"),
                        "home": g("data-homesxname"), "away": g("data-awaysxname")}
        return out

    try:
        return ttl_fetch("jczq_teamids:" + today, _go, ttl_hours=6)
    except Exception as e:
        print("  竞彩页抓取失败:", e, flush=True)
        return {}

def cross_check(m, bd_matches):
    """竞彩×北单交叉验证：同一场（队名+日期±1天）两边概率对比。
    北单概率为平均赔率反推（参考模型）。返回 {matched, bdProb, diff, agree}"""
    date = m.get("date") or ""
    # 日期±1天内匹配（北单 scheduleDate 可能与竞彩 businessDate 差一天）
    def near(d):
        try:
            from datetime import datetime, timedelta
            t = datetime.strptime(date[:10], "%Y-%m-%d")
            for off in (-1, 0, 1):
                if str(t + timedelta(days=off))[:10] == str(d)[:10]:
                    return True
        except Exception:
            pass
        return date[:10] == str(d)[:10]
    for bm in bd_matches:
        if bm.get("home") != m.get("home") or bm.get("away") != m.get("away"):
            continue
        if not near(bm.get("date") or ""):
            continue
        p = bm.get("prob")
        if not p or not m.get("hadProb") or not any(v is not None for v in m["hadProb"].values()):
            break
        bd_goal = bm.get("goal", 0)
        # 根据北单盘口类型选择对应赔率：goal=0胜平负用odds，goal≠0让球用hhadOdds
        if bd_goal != 0 and bm.get("hhadOdds"):
            bd_odds = bm["hhadOdds"]
        else:
            bd_odds = bm.get("odds") or {}
        bd_best = max(p, key=p.get)
        jc_had = {k: v for k, v in m["hadProb"].items() if v is not None}
        if not jc_had:
            break
        jc_best = max(jc_had, key=jc_had.get)
        agree = bd_best == jc_best
        diff = round(p[jc_best] - (m["hadProb"][jc_best] or 0), 1)
        return {"matched": True, "bdProb": p, "bdOdds": bd_odds,
                "bdGoal": bd_goal, "jcBest": jc_best, "bdBest": bd_best,
                "agree": agree, "diff": diff,
                "asian": bm.get("asian") or ""}
    return None

def save_history(records):
    p = os.path.join(HERE, "history.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "total": len(records), "records": records}, f, ensure_ascii=False, indent=1)
    return p

def judge_hit(rec):
    """根据判定方向与赛果判断命中/错误。返回 hit/miss/n-a；未回填返回 None（待开奖）"""
    result = rec.get("result")
    if not result:
        return None  # 尚无赛果，保持待回填
    if result.get("cancel"):
        return "n-a"
    verdict = rec.get("verdict") or {}
    t, d = verdict.get("t"), verdict.get("dir")
    had = result.get("had")
    if not t or not d or not had:
        return "n-a"
    if t == "good":
        return "hit" if had == d else "miss"
    if t in ("bad", "cold"):
        # 危险/冷门 = 预测该方向不会打出
        return "hit" if had != d else "miss"
    return "n-a"

def parse_score(s):
    """把官方比分文本转成 (home, away, had结果) 三元组"""
    if not s:
        return None
    s = str(s).strip()
    if s in ("取消", "延期", "中断"):
        return "cancel"
    for sep in (":", "：", "-"):
        if sep in s:
            parts = s.split(sep)
            if len(parts) == 2:
                try:
                    h, a = int(parts[0]), int(parts[1])
                    had = "h" if h > a else ("a" if a > h else "d")
                    return {"home": h, "away": a, "had": had}
                except ValueError:
                    pass
    return None

def backfill_results(records):
    """对已开赛且未回填的记录调用官方开奖接口回填赛果"""
    now = time.time()
    done = 0
    for rec in records:
        if rec.get("result") is not None and rec.get("result") != {}:
            # 已有赛果但缺半全场结果，补查一次
            if rec.get("halfFullResult"):
                continue
        kick = rec.get("kickoff") or ""
        if not kick:
            continue
        try:
            kick_ts = time.mktime(time.strptime(kick, "%Y-%m-%d %H:%M"))
        except ValueError:
            continue
        # 开赛 2.5 小时后才查询，等待官方发布开奖结果
        if now < kick_ts + 2.5 * 3600:
            continue
        url = BONUS_URL + "?clientCode=3001&matchId=" + str(rec.get("id", ""))
        try:
            fb = fetch(url)
            v = fb.get("value") or {}
            is_cancel = int(v.get("isCancel") or 0)
            sec = v.get("sectionsNo999") or ""
            score = parse_score(sec)
            if is_cancel:
                rec["result"] = {"score": sec or "取消", "had": None, "cancel": True}
            elif score == "cancel":
                rec["result"] = {"score": sec, "had": None, "cancel": True}
            elif score is not None:
                rec["result"] = {"score": sec, "had": score["had"],
                                 "home": score["home"], "away": score["away"], "cancel": False}
            else:
                # 开奖未发布，保持待回填
                continue
            # 解析半全场官方结果（matchResultList中code=HAFU）
            try:
                mrl = v.get("matchResultList") or []
                for item in mrl:
                    if item.get("code") == "HAFU":
                        comb = item.get("combination", "")  # 如"H:D"=半场主胜全场平
                        parts = comb.split(":")
                        if len(parts) == 2:
                            _map = {"H": "h", "D": "d", "A": "a"}
                            ht = _map.get(parts[0].strip(), "")
                            ft = _map.get(parts[1].strip(), "")
                            if ht and ft:
                                rec["halfFullResult"] = ht + ft  # 如"hd"
                                rec["halfFullDesc"] = item.get("combinationDesc", "")
                        break
            except Exception:
                pass
            rec["hitFlag"] = judge_hit(rec)
            rec["resultAt"] = time.strftime("%Y-%m-%d %H:%M")
            # 让球盘推荐命中判定（竞彩让球胜平负每场都有结果：让胜/让平/让负）
            hv = rec.get("hhadVerdict") or {}
            if hv and hv.get("dir") and score is not None and not is_cancel:
                try:
                    gv = int(hv.get("goal") or rec.get("goal") or 0)
                except (TypeError, ValueError):
                    gv = 0
                adj_home = score["home"] + gv  # 主队+让球数
                hhad_res = "h" if adj_home > score["away"] else ("a" if adj_home < score["away"] else "d")
                rec["hhadResult"] = hhad_res
                # 统一正向判定：推荐方向==实际结果则命中，否则错误
                rec["hhadHit"] = "hit" if hhad_res == hv["dir"] else "miss"
            # 亚指推荐命中判定（基于亚盘盘口准确计算赢盘/输盘/走水）
            av = rec.get("asianVerdict") or {}
            if av and av.get("dir") and score is not None and not is_cancel:
                asian_dir = av["dir"]  # h=上盘, a=下盘
                asian_line_text = av.get("asianLine", "")
                # 亚盘文字转数值（主队让球为正，客队让球为负）
                try:
                    from fetch_hkmo import asian_to_num
                    line_num = asian_to_num(asian_line_text)
                except Exception:
                    line_num = None
                if line_num is not None and line_num != 0:
                    # 确定让球方和盘口绝对值
                    if line_num > 0:
                        # 主队让球，主队=上盘
                        favorite_goals = score["home"]
                        underdog_goals = score["away"]
                    else:
                        # 客队让球，客队=上盘
                        favorite_goals = score["away"]
                        underdog_goals = score["home"]
                    line_abs = abs(line_num)
                    goal_diff = favorite_goals - underdog_goals  # 让球方净胜球
                    # 赢盘判定：净胜球>盘口=上盘赢，净胜球<盘口=下盘赢，净胜球=盘口=走水
                    if goal_diff > line_abs + 0.01:
                        upper_win = True  # 上盘赢
                        push = False
                    elif goal_diff < line_abs - 0.01:
                        upper_win = False  # 下盘赢
                        push = False
                    else:
                        push = True  # 走水
                    if push:
                        rec["asianHit"] = "n-a"  # 走水退本金
                    elif asian_dir == "h":
                        rec["asianHit"] = "hit" if upper_win else "miss"
                    elif asian_dir == "a":
                        rec["asianHit"] = "hit" if not upper_win else "miss"
                    else:
                        rec["asianHit"] = "n-a"
                else:
                    # 平手盘或无法解析盘口，简化为胜负判定
                    if asian_dir == "h":
                        rec["asianHit"] = "hit" if score["home"] > score["away"] else ("n-a" if score["home"] == score["away"] else "miss")
                    elif asian_dir == "a":
                        rec["asianHit"] = "hit" if score["away"] > score["home"] else ("n-a" if score["home"] == score["away"] else "miss")
                    else:
                        rec["asianHit"] = "hit" if score["home"] == score["away"] else "miss"
            # 比分推荐命中判定
            sr = rec.get("scoreRec") or {}
            if sr and sr.get("predicted") and score is not None and not is_cancel:
                actual = f"{score['home']}:{score['away']}"
                rec["scoreActual"] = actual
                if actual == sr["predicted"]:
                    rec["scoreHit"] = "hit"
                elif actual in (sr.get("alternatives") or []):
                    rec["scoreHit"] = "partial"
                else:
                    rec["scoreHit"] = "miss"
            done += 1
            print(f"  回填: {rec.get('num','')} {rec.get('home','')}vs{rec.get('away','')} {sec} -> {rec['hitFlag']}", flush=True)
        except Exception as e:
            print(f"  回填失败 {rec.get('num','')}: {e}", flush=True)
        time.sleep(0.4)
    print(f"  本轮回填 {done} 场", flush=True)
    return records

def backfill_hkmo_history(matches):
    """回填港澳赔率历史记录的赛果和命中判定
    命中逻辑：双方同时降水的方向 = 推荐方向，比赛结果匹配则命中
    赛果来源：今日matches + history.json（竞彩官方赛果）
    """
    history_path = os.path.join(HERE, "hkmo_history.json")
    if not os.path.exists(history_path):
        return
    try:
        with open(history_path, encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return
    records = history.get("records", [])
    if not records:
        return
    # 构建比赛赛果映射（按 date|队名 匹配，避免今天未开赛比赛匹配到历史同名比赛赛果）
    score_map = {}
    # 来源1：今日matches
    for m in matches:
        if m.get("result") and m["result"].get("score"):
            mdate = m.get("date", "")
            key = f"{mdate}|{m.get('home','')}vs{m.get('away','')}"
            score_map[key] = m["result"]
    # 来源2：history.json（已完场比赛的赛果 + 普通胜平负推荐方向）
    hist_path = os.path.join(HERE, "history.json")
    verdict_map = {}  # date|队名→普通胜平负推荐方向，用于无港澳降水信号时推导让球盘
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                hd = json.load(f)
            for r in hd.get("records", []):
                rdate = r.get("date", "")
                if r.get("result") and r["result"].get("score"):
                    key = f"{rdate}|{r.get('home','')}vs{r.get('away','')}"
                    if key not in score_map:
                        score_map[key] = r["result"]
                v = r.get("verdict") or {}
                if v.get("dir"):
                    key = f"{rdate}|{r.get('home','')}vs{r.get('away','')}"
                    verdict_map[key] = v["dir"]
        except Exception:
            pass
    now = time.time()
    done = 0
    for rec in records:
        # 已回填且让球盘/亚指命中也已计算的跳过
        if rec.get("hitFlag") and rec.get("hhadHit") and rec.get("asianHit"):
            continue
        rec_date = rec.get("date", "")
        key = f"{rec_date}|{rec.get('home','')}vs{rec.get('away','')}"
        # 安全检查：比赛未结束（当前时间 < 开赛时间+2.5小时）不回填赛果
        kickoff = rec.get("kickoff", "")
        if kickoff:
            try:
                # kickoff可能包含完整日期(如"2026-09-25 14:00")或仅时间(如"14:00")
                if len(kickoff) >= 10 and kickoff[4] == '-':
                    kt = time.mktime(time.strptime(kickoff, "%Y-%m-%d %H:%M"))
                else:
                    kt = time.mktime(time.strptime(f"{rec_date} {kickoff}", "%Y-%m-%d %H:%M"))
                if now < kt + 2.5 * 3600:
                    continue  # 比赛未结束，跳过回填
            except Exception:
                pass
        # 从bd_history或matches中找赛果
        result = score_map.get(key)
        if not result:
            continue
        score = result.get("score", "")
        if not score or result.get("cancel"):
            rec["result"] = score or "取消"
            rec["hitFlag"] = "n-a"
            done += 1
            continue
        # 解析比分
        try:
            parts = score.replace("：", ":").split(":")
            home_score = int(parts[0])
            away_score = int(parts[1])
        except Exception:
            continue
        # 实际赛果方向
        if home_score > away_score:
            actual = "h"
        elif home_score == away_score:
            actual = "d"
        else:
            actual = "a"
        # 推荐方向 = 双方同时降水的方向
        rec_dirs = rec.get("common_drop_dirs", [])
        if not rec_dirs:
            rec["result"] = score
            if not rec.get("hitFlag"):
                rec["hitFlag"] = "n-a"
                done += 1
        else:
            # 取第一个降水方向作为推荐方向
            pick_dir = rec_dirs[0]
            rec["pickDir"] = pick_dir
            rec["result"] = score
            if not rec.get("hitFlag"):
                rec["hitFlag"] = "hit" if actual == pick_dir else "miss"
                done += 1
        # 让球盘推荐：旧版数据或明显不合理时，根据核心方向+让球数推导最有把握方向
        core_dir = rec.get("pickDir") or (rec_dirs[0] if rec_dirs else None)
        if not core_dir:  # 无港澳降水信号时，用普通胜平负推荐方向作为替代核心方向
            core_dir = verdict_map.get(key)
        hhad_v = rec.get("hhadVerdict") or {}
        if hhad_v and core_dir:
            try:
                gv = int(hhad_v.get("goal", 0) or 0)
            except (TypeError, ValueError):
                gv = 0
            hdir = hhad_v.get("dir")
            # 旧版残留判定：txt含"旧版"或"方向一致"（上一版简单映射的标记）
            is_old = ("旧版" in (hhad_v.get("txt") or "")) or ("方向一致" in (hhad_v.get("txt") or ""))
            # 明显不合理判定：与核心方向+让球数的基本逻辑冲突（不可能出现的结果）
            is_unreasonable = False
            if core_dir == "h":  # 主胜
                if gv > 0 and hdir != "h":  # 主队受让还赢球→必让胜
                    is_unreasonable = True
                elif gv == -1 and hdir == "a":  # 让1球主胜→不可能让负（赢球让1球至少平）
                    is_unreasonable = True
            elif core_dir == "a":  # 客胜
                if gv < 0 and hdir != "a":  # 主队让球还输球→必让负
                    is_unreasonable = True
                elif gv == 1 and hdir == "h":  # 受让1球客胜→不可能让胜
                    is_unreasonable = True
            elif core_dir == "d":  # 平局
                if gv < 0 and hdir == "h":  # 主队让球+平局→让球后主负，不可能让胜
                    is_unreasonable = True
                elif gv > 0 and hdir == "a":  # 主队受让+平局→让球后主胜，不可能让负
                    is_unreasonable = True
            if is_old or is_unreasonable:
                # 根据核心方向+让球数推导最有把握的让球盘方向（条件概率最高）
                if core_dir == "h":  # 主胜
                    if gv > 0:
                        derived_dir, derived_txt = "h", "推荐让胜（主胜+主队受让，必让胜）"
                    elif gv == -1:
                        derived_dir, derived_txt = "h", "推荐让胜（主胜+让1球，赢2球+概率最高约55%）"
                    elif gv == -2:
                        derived_dir, derived_txt = "d", "推荐让平（主胜+让2球，赢2球走让平概率最高）"
                    else:
                        derived_dir, derived_txt = "a", f"推荐让负（主胜+让{abs(gv)}球，穿盘难度大）"
                elif core_dir == "a":  # 客胜
                    if gv < 0:
                        derived_dir, derived_txt = "a", "推荐让负（客胜+主队让球，必让负）"
                    elif gv == 1:
                        derived_dir, derived_txt = "a", "推荐让负（客胜+受让1球，客赢1球概率最高约55%）"
                    elif gv == 2:
                        derived_dir, derived_txt = "d", "推荐让平（客胜+受让2球，客赢2球走让平概率最高）"
                    else:
                        derived_dir, derived_txt = "h", f"推荐让胜（客胜+受让{gv}球，客穿盘难度大）"
                else:  # 平局
                    if gv < 0:
                        derived_dir, derived_txt = "a", "推荐让负（平局+主队让球，让球后主负）"
                    else:
                        derived_dir, derived_txt = "h", "推荐让胜（平局+主队受让，让球后主胜）"
                rec["hhadVerdict"] = {
                    "t": "good", "txt": derived_txt, "dir": derived_dir,
                    "stars": 3, "score": 1.0,
                    "reasons": [f"核心{ {'h':'主胜','d':'平局','a':'客胜'}.get(core_dir, core_dir)}+让球{gv}→{ {'h':'让胜','d':'让平','a':'让负'}.get(derived_dir, derived_dir)}（条件概率最高）"],
                    "goal": hhad_v.get("goal", "0")
                }
                hhad_v = rec["hhadVerdict"]
                if rec.get("hhadHit"):
                    del rec["hhadHit"]
        # 让球盘推荐命中判定
        if hhad_v and hhad_v.get("dir") and not rec.get("hhadHit"):
            try:
                gv = int(hhad_v.get("goal", 0) or 0)
            except (TypeError, ValueError):
                gv = 0
            adj_home = home_score + gv
            if adj_home > away_score:
                hhad_res = "h"
            elif adj_home < away_score:
                hhad_res = "a"
            else:
                hhad_res = "d"
            hhad_dir = hhad_v["dir"]
            # 竞彩让球胜平负每场都有结果，统一正向判定：推荐方向==实际结果则命中
            rec["hhadHit"] = "hit" if hhad_res == hhad_dir else "miss"
        # 亚指推荐命中判定（基于亚盘盘口，比赛结果走水时n-a）
        asian_v = rec.get("asianVerdict") or {}
        if asian_v and asian_v.get("dir") and not rec.get("asianHit"):
            asian_dir = asian_v["dir"]
            asian_line_text = asian_v.get("asianLine", "")
            try:
                from fetch_hkmo import asian_to_num
                line_num = asian_to_num(asian_line_text)
            except Exception:
                line_num = None
            if line_num is not None and line_num != 0:
                if line_num > 0:
                    favorite_goals = home_score
                    underdog_goals = away_score
                else:
                    favorite_goals = away_score
                    underdog_goals = home_score
                line_abs = abs(line_num)
                goal_diff = favorite_goals - underdog_goals
                if goal_diff > line_abs + 0.01:
                    upper_win = True
                    push = False
                elif goal_diff < line_abs - 0.01:
                    upper_win = False
                    push = False
                else:
                    push = True
                if push:
                    rec["asianHit"] = "n-a"
                elif asian_dir == "h":
                    rec["asianHit"] = "hit" if upper_win else "miss"
                elif asian_dir == "a":
                    rec["asianHit"] = "hit" if not upper_win else "miss"
                else:
                    rec["asianHit"] = "n-a"
            else:
                if asian_dir == "h":
                    rec["asianHit"] = "hit" if home_score > away_score else ("n-a" if home_score == away_score else "miss")
                elif asian_dir == "a":
                    rec["asianHit"] = "hit" if away_score > home_score else ("n-a" if home_score == away_score else "miss")
                else:
                    rec["asianHit"] = "hit" if home_score == away_score else "miss"
        done += 1
    history["records"] = records
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    print(f"  港澳历史回填 {done} 场", flush=True)

def main():
    print("[1/5] 抓取赛事与赔率...", flush=True)
    calc = fetch(CALC_URL)
    if not calc.get("success"):
        raise RuntimeError("计算器接口失败: " + calc.get("errorMessage", ""))
    blocks = calc.get("value", {}).get("matchInfoList", []) or []
    if not blocks:
        print("  官方暂无在售比赛（可能全部已开赛/停售），仅执行历史回填...", flush=True)
    matches = []
    match_ids = []
    for block in blocks:
        date = block.get("businessDate", "")
        for m in block.get("subMatchList", []):
            mid = str(m.get("matchId", ""))
            had = m.get("had") or {}
            hhad = m.get("hhad") or {}
            # 强弱悬殊比赛可能只开让球盘(hhad)不开胜平负(had)，只要有一个就保留
            if not had and not hhad:
                continue
            match_date = m.get("matchDate") or date
            match_time = (m.get("matchTime") or "")[:5]
            # 官方排名形如 "[日职3]" / "[日职14]"，提取纯数字
            def rank_num(rk):
                s = re.sub(r"\D", "", rk or "")
                return int(s) if s else None
            # 总进球玩法赔率 s0..s7（进0~7球），算期望进球
            ttg = m.get("ttg") or {}
            ttg_odds = {i: to_float(ttg.get(f"s{i}")) for i in range(8)}
            inv = [1.0/o for o in ttg_odds.values() if o and o > 0]
            goals_exp = None
            if inv:
                tot = sum(inv)
                probs = {i: (1.0/o)/tot for i, o in ttg_odds.items() if o and o > 0}
                goals_exp = round(sum(i*p for i, p in probs.items()), 2)
            # 半全场赔率：hh=主胜/主胜, aa=客胜/客胜, dd=平/平
            hafu = m.get("hafu") or {}
            hh_prob = aa_prob = None
            def imp_prob(od):
                if od and to_float(od) and od != "0":
                    return round(100.0 / to_float(od), 1)
                return None
            hh_prob = imp_prob(hafu.get("hh"))
            aa_prob = imp_prob(hafu.get("aa"))
            matches.append({
                "id": mid,
                "num": m.get("matchNumStr", ""),
                "week": m.get("matchWeek", ""),
                "date": date,
                "kickoff": f"{match_date} {match_time}",
                "time": match_time,
                "league": m.get("leagueAbbName", ""),
                "home": m.get("homeTeamAllName", ""),
                "away": m.get("awayTeamAllName", ""),
                "hr": (m.get("homeRank") or "").strip("[]"),
                "ar": (m.get("awayRank") or "").strip("[]"),
                "hrn": rank_num(m.get("homeRank")),
                "arn": rank_num(m.get("awayRank")),
                "status": m.get("matchStatus", ""),
                "had": {"h": fmt_rate(had, "h"), "d": fmt_rate(had, "d"), "a": fmt_rate(had, "a")},
                "hhad": {"h": fmt_rate(hhad, "h"), "d": fmt_rate(hhad, "d"), "a": fmt_rate(hhad, "a"),
                         "goal": (hhad.get("goalLine") or "")},
                "ttg": ttg_odds,
                "goalsExp": goals_exp,
                "hhProb": hh_prob,
                "aaProb": aa_prob,
            })
            match_ids.append(mid)
    print(f"  共 {len(matches)} 场", flush=True)
    if not matches:
        print("  官方暂无在售比赛，仅执行历史/港澳赛果回填...", flush=True)
        # 无在售比赛时不能直接退出：已开赛比赛（如当天晚场比赛）仍需回填赛果
        try:
            hist_path = os.path.join(HERE, "history.json")
            if os.path.exists(hist_path):
                with open(hist_path, encoding="utf-8") as f:
                    hd = json.load(f)
                hist_records = hd.get("records", [])
                no_result_before = sum(1 for r in hist_records if not r.get("result"))
                hist_backfilled = backfill_results(hist_records)
                for r in hist_backfilled:
                    if r.get("result"):
                        r["hitFlag"] = judge_hit(r)
                save_history(hist_backfilled)
                no_result_after = sum(1 for r in hist_backfilled if not r.get("result"))
                print(f"  历史库回填：{len(hist_records)}条，回填前无赛果{no_result_before}条，回填后无赛果{no_result_after}条", flush=True)
        except Exception as e:
            print(f"  历史库回填失败: {e}", flush=True)
        # 回填港澳赔率历史（函数内部从hkmo_history.json读取并匹配history赛果）
        try:
            backfill_hkmo_history([])
        except Exception as e:
            print(f"  港澳历史回填失败: {e}", flush=True)
        # 同步当天已完场比赛赛果到matches.json（官方停售不返回，页面需显示赛果）
        try:
            m_path = os.path.join(HERE, "matches.json")
            if os.path.exists(m_path):
                with open(m_path, encoding="utf-8") as f:
                    mdata = json.load(f)
                with open(hist_path, encoding="utf-8") as f:
                    hdata = json.load(f)
                h_by_key = {}
                for hr in hdata.get("records", []):
                    h_by_key[f"{hr.get('date')}|{hr.get('home','')}vs{hr.get('away','')}"] = hr
                msynced = 0
                for mx in mdata.get("matches", []):
                    key = f"{mx.get('date')}|{mx.get('home','')}vs{mx.get('away','')}"
                    hr = h_by_key.get(key)
                    if hr and hr.get("result") and not mx.get("result"):
                        mx["result"] = hr["result"]
                        mx["hitFlag"] = hr.get("hitFlag")
                        hkmo = mx.get("hkmo")
                        if hkmo and not hkmo.get("result"):
                            hkmo["result"] = hr["result"].get("score", "")
                            hkmo["hitFlag"] = hr.get("hitFlag") or ""
                        msynced += 1
                if msynced > 0:
                    with open(m_path, "w", encoding="utf-8") as f:
                        json.dump(mdata, f, ensure_ascii=False, indent=2)
                    print(f"  已同步 {msynced} 场当天赛果到matches.json", flush=True)
        except Exception as e:
            print(f"  matches赛果同步失败: {e}", flush=True)
        return []

    print("[2/5] 抓取概率与支持率...", flush=True)
    support = {}
    # 分批请求，避免URL过长
    for i in range(0, len(match_ids), 60):
        chunk = ",".join(match_ids[i:i+60])
        url = SUPPORT_URL + "?matchIds=" + chunk + "&poolCode=hhad,had&sportType=1"
        try:
            sr = fetch(url)
            if sr.get("success") and sr.get("value"):
                support.update(sr["value"])
        except Exception as e:
            print("  支持率请求失败:", e, flush=True)
        time.sleep(0.5)

    print("[3/6] 计算指标与规则判定...", flush=True)
    cfg = load_config()
    for m in matches:
        s = support.get("_" + m["id"]) or {}
        had_s, hhad_s = s.get("HAD") or {}, s.get("HHAD") or {}
        def prob_sup(dic, hk, dk, ak):
            def p(key, k2):
                v = dic.get(key) if dic else None
                if isinstance(v, str) and v.endswith("%"):
                    try: return float(v[:-1])
                    except: return None
                return v
            return {"h": p(hk, None), "d": p(dk, None), "a": p(ak, None)}
        m["hadProb"] = prob_sup(had_s, "hProbability", "dProbability", "aProbability")
        m["hadSup"] = prob_sup(had_s, "hSupportRate", "dSupportRate", "aSupportRate")
        m["hhadProb"] = prob_sup(hhad_s, "hProbability", "dProbability", "aProbability")
        m["hhadSup"] = prob_sup(hhad_s, "hSupportRate", "dSupportRate", "aSupportRate")
        m["votes"] = {"h": had_s.get("win"), "d": had_s.get("draw"), "a": had_s.get("lose")}
        m["eValue"] = had_s.get("eValue")
        m["eKey"] = had_s.get("eKey")
        m["absValue"] = had_s.get("absValue")
        # 返还率
        h, d, a = m["had"]["h"], m["had"]["d"], m["had"]["a"]
        if h and d and a:
            m["returnRate"] = round(1.0 / (1.0/h + 1.0/d + 1.0/a) * 100, 1)
        else:
            m["returnRate"] = None
        m["verdict"], m["hits"], m["score"] = evaluate(m, cfg)

    print("[3.5/6] 抓取赔率历史（资金流向）...", flush=True)
    for m in matches:
        odds_hist = fetch_odds_history(m["id"])
        m["mov"] = odds_hist["mov"]
        m["hadOpen"] = odds_hist["hadOpen"]
        m["hhadOpen"] = odds_hist["hhadOpen"]
        time.sleep(0.4)
    # 赔率变动参与评分后重新判定一次
    for m in matches:
        m["verdict"], m["hits"], m["score"] = evaluate(m, cfg)
    # ===== 智能增强引擎：EV 校准 + 负期望降级（异常时静默跳过，不影响主流程）=====
    try:
        from smart_engine import SmartEngine
        _eng = SmartEngine()
        _n_smart = 0
        _n_down = 0
        for m in matches:
            _r = _eng.predict(m)
            if not _r:
                continue
            m["smart"] = _r
            _n_smart += 1
            _v = m.get("verdict") or {}
            _rs = _v.get("reasons")
            if isinstance(_rs, list):
                _rs.append("智能引擎·校准%.0f%%·EV%+.0f%%·%s" % (
                    _r["p_cal"] * 100, _r["ev"] * 100, _r["advice"]))
            # 正路信号但期望值明显为负 -> 降级为警示，避免误导下注
            if _r.get("type") == "good" and _r.get("ev", 0) < -0.08:
                _v["t"] = "warn"
                _v["stars"] = 0
                _v["txt"] = (_v.get("txt") or "") + "｜智能引擎判定为负期望，已降级为警示"
                _n_down += 1
        print(f"  智能引擎：{_n_smart}/{len(matches)} 场完成 EV 校准，降级 {_n_down} 场", flush=True)
    except Exception as e:
        print(f"  智能引擎跳过: {e}", flush=True)

    # 生成让球盘推荐、亚指推荐、比分推荐
    print("[3.8/6] 生成让球盘/亚指/比分推荐...", flush=True)
    for m in matches:
        core_dir, core_src = get_core_direction(m)
        m["hhadVerdict"] = evaluate_hhad(m, cfg, core_dir)
        m["asianVerdict"] = evaluate_asian(m, cfg, core_dir)
        m["scoreRec"] = gen_score_predict(m)

    print("[4/6] 更新历史记录库...", flush=True)
    records = load_history()
    by_id = {r["id"]: r for r in records}
    now_str = time.strftime("%Y-%m-%d %H:%M")
    new_count = 0
    for m in matches:
        mid = m["id"]
        if mid in by_id:
            # 已存在：若判定为空则用最新判定补充；旧记录补齐推荐方向/星级
            rec = by_id[mid]
            if not rec.get("verdict"):
                rec["verdict"] = m["verdict"]
                rec["hits"] = m["hits"]
                rec["score"] = m["score"]
                rec["prob"] = m["hadProb"]
                rec["sup"] = m["hadSup"]
                rec["hitFlag"] = judge_hit(rec)
            elif rec["verdict"] and not rec["verdict"].get("dir"):
                rec["verdict"]["dir"] = m["score"]["dir"]
                rec["verdict"]["stars"] = m["score"]["stars"]
                rec["verdict"]["score"] = m["score"]["score"]
                rec["verdict"]["reasons"] = m["score"]["reasons"]
                rec["score"] = m["score"]
                rec["hitFlag"] = judge_hit(rec)
            # 补充新推荐字段（若旧记录没有）
            if not rec.get("hhadVerdict"):
                rec["hhadVerdict"] = m.get("hhadVerdict")
            if not rec.get("asianVerdict"):
                rec["asianVerdict"] = m.get("asianVerdict")
            if not rec.get("scoreRec"):
                rec["scoreRec"] = m.get("scoreRec")
        else:
            by_id[mid] = {
                "id": mid, "num": m["num"], "date": m["date"], "kickoff": m["kickoff"],
                "league": m["league"], "home": m["home"], "away": m["away"],
                "goal": m["hhad"]["goal"], "odds": m["had"],
                "prob": m["hadProb"], "sup": m["hadSup"],
                "hr": m.get("hr"), "ar": m.get("ar"), "hrn": m.get("hrn"), "arn": m.get("arn"),
                "goalsExp": m.get("goalsExp"), "hhProb": m.get("hhProb"), "aaProb": m.get("aaProb"),
                "verdict": m["verdict"], "hits": m["hits"], "score": m["score"],
                "hhadVerdict": m.get("hhadVerdict"), "asianVerdict": m.get("asianVerdict"),
                "scoreRec": m.get("scoreRec"),
                "predAt": now_str, "result": None, "hitFlag": None,
                "hhadHit": None, "asianHit": None, "scoreHit": None,
            }
            new_count += 1
    records = list(by_id.values())
    # 排序：按开赛时间倒序（新→旧）
    records.sort(key=lambda r: r.get("kickoff") or "", reverse=True)
    # 历史永久保存（不设时间窗口），每日追加积累；文件随时间增长，每年约1万条/几MB
    print(f"  历史库共 {len(records)} 条，新增 {new_count} 条（永久保存）", flush=True)

    # 统一修正 hitFlag：无赛果=待回填(None)，有赛果=按规则重算
    for r in records:
        if not r.get("result"):
            r["hitFlag"] = None
        else:
            r["hitFlag"] = judge_hit(r)

    print("[4.5/6] 回测各规则组命中率并调参...", flush=True)
    cfg = backtest_rules(cfg, records)
    save_config(cfg)

    print("[5/6] 回填赛果...", flush=True)
    records = backfill_results(records)
    # 回填历史记录库中未回填的旧记录
    print("[5.05/6] 回填历史记录库未回填赛果...", flush=True)
    try:
        hist_records = load_history()
        hist_backfilled = backfill_results(hist_records)
        save_history(hist_backfilled)
        no_result_before = sum(1 for r in hist_records if not r.get("result"))
        no_result_after = sum(1 for r in hist_backfilled if not r.get("result"))
        print(f"  历史记录库: {len(hist_records)}条, 回填前无赛果{no_result_before}条, 回填后无赛果{no_result_after}条", flush=True)
    except Exception as e:
        print(f"  历史记录库回填失败: {e}", flush=True)

    # 回填高手推荐历史赛果（从history.json按date+num匹配）
    print("[5.06/6] 回填高手推荐历史赛果...", flush=True)
    try:
        expert_hist_path = os.path.join(HERE, "expert_history.json")
        if os.path.exists(expert_hist_path):
            with open(expert_hist_path, encoding="utf-8") as f:
                expert_hist = json.load(f)
            # 建立history.json的date+num -> result映射
            hist_map = {}
            for r in hist_backfilled:
                d = r.get("date", "")
                n = r.get("num", "")
                if d and n:
                    hist_map[f"{d}|{n}"] = r.get("result")
            expert_backfilled = 0
            for expert, recs in expert_hist.items():
                for rec in recs:
                    if rec.get("result") or rec.get("actualScore"):
                        continue
                    d = rec.get("date", "")
                    n = rec.get("num", "")
                    key = f"{d}|{n}"
                    if key in hist_map and hist_map[key]:
                        res = hist_map[key]
                        rec["result"] = res.get("score", "")
                        rec["actualScore"] = res.get("score", "")
                        # 判定命中
                        rec_dir = rec.get("recommendKey", "")
                        had = res.get("had")
                        if had and rec_dir:
                            rec["hit"] = (had == rec_dir)
                        expert_backfilled += 1
            if expert_backfilled > 0:
                with open(expert_hist_path, "w", encoding="utf-8") as f:
                    json.dump(expert_hist, f, ensure_ascii=False, indent=2)
            print(f"  高手推荐历史: 回填{expert_backfilled}条赛果", flush=True)
    except Exception as e:
        print(f"  高手推荐历史回填失败: {e}", flush=True)

    # 注意：save_history移到最后（asianVerdict和BSD赔率更新之后），确保新字段被保存

    # 回填港澳赔率历史记录
    print("[5.1/6] 回填港澳赔率历史...", flush=True)
    try:
        backfill_hkmo_history(matches)
    except Exception as e:
        print(f"  港澳历史回填失败: {e}", flush=True)

    print("[5.5/6] 球队近况 / H2H...", flush=True)
    pool = merge_match_records()
    # 500 球队页近况：只对"有判定/高价值"场次抓（节约请求），其余用自建库
    jc_map = {}
    try:
        jc_map = fetch_jczq_teamids()
    except Exception as e:
        print("  竞彩球队映射失败:", e, flush=True)
    for m in matches:
        m["form"] = {"home": team_form(pool, m["home"], limit=6),
                     "away": team_form(pool, m["away"], limit=6)}
        m["attack"] = {"home": attack_stats(pool, m["home"], limit=6),
                       "away": attack_stats(pool, m["away"], limit=6)}
        # 500 球队页近况（缓存一天）补充：近况 + 攻防（含比分）
        jc = jc_map.get(m["num"])
        if jc:
            fh = fetch_team_form(jc["home_id"], jc["home"])
            fa = fetch_team_form(jc["away_id"], jc["away"])
            if fh:
                m["form"]["home"] = fh
                m["attack"]["home"] = {"n": fh.get("n"), "gf": fh.get("gf"), "ga": fh.get("ga")}
            if fa:
                m["form"]["away"] = fa
                m["attack"]["away"] = {"n": fa.get("n"), "gf": fa.get("gf"), "ga": fa.get("ga")}
        m["h2h"] = team_h2h(pool, m["home"], m["away"], limit=6)
    print(f"  近况/H2H 完成", flush=True)

    # BSD Sports API：伤停/首发/教练/xG/天气/裁判/AI预测
    # 隔离原则（继承接管规范）：结果只写入「独立文件」bsd_data.json，绝不写回核心 matches.json，
    # 避免免费额度耗尽导致部分比赛写入、部分未写，污染核心数据集与模型。
    print("[5.8/6] BSD伤停首发数据...", flush=True)
    try:
        from fetch_bsd import fetch_and_match
        bsd_data = fetch_and_match(matches, cache=True)
        # 独立落盘：核心 matches 列表保持纯净，不被污染
        _bsd_out = {
            "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "API-Football (伤停/首发/教练/天气/裁判/AI预测)",
            "independent": True,
            "byNum": bsd_data,
        }
        with open(os.path.join(HERE, "bsd_data.json"), "w", encoding="utf-8") as _f:
            json.dump(_bsd_out, _f, ensure_ascii=False, indent=1)
        bsd_n = len(bsd_data)
        print(f"  BSD匹配 {bsd_n}/{len(matches)} 场（独立文件 bsd_data.json，未污染核心数据）", flush=True)
    except Exception as e:
        print(f"  BSD数据抓取失败: {e}", flush=True)

    # [5.9/6] 香港马会+澳门彩票 赔率（初盘/当前/双方同时降水监控）
    print("[5.9/6] 港澳赔率（香港马会+澳门彩票）...", flush=True)
    hkmo_both_drop = 0
    try:
        if fetch_hkmo_data:
            # 从500彩票网竞彩足球页面获取当日竞彩fid
            try:
                from fetch_hkmo import fetch_jc_fids, name_similar
                jc_fids = fetch_jc_fids()
                print(f"  500竞彩页面获取 {len(jc_fids)} 场fid", flush=True)
                target_bd = []
                for m in matches:
                    key = f"{m.get('home','')}vs{m.get('away','')}"
                    fid_info = jc_fids.get(key)
                    if not fid_info:
                        # 模糊匹配队名（500页面常用简称）
                        for jk, jv in jc_fids.items():
                            jh = jv["home"]
                            ja = jv["away"]
                            mh = m.get('home','')
                            ma = m.get('away','')
                            if name_similar(jh, mh) and name_similar(ja, ma):
                                fid_info = jv
                                break
                    if fid_info:
                        target_bd.append({
                            "fid": fid_info["fid"], "home": m.get('home',''), "away": m.get('away',''),
                            "num": m.get('num',''), "kickoff": m.get('kickoff',''),
                            "league": fid_info.get("league",""), "asian": "",
                        })
                print(f"  竞彩匹配500页面 {len(target_bd)} 场，开始抓取港澳赔率...", flush=True)
                hkmo_data = fetch_hkmo_data(target_bd)
                hkmo_matches = hkmo_data.get("matches", [])
                hkmo_both_drop = hkmo_data.get("both_drop_count", 0)
                # 按队名匹配到竞彩比赛
                hkmo_by_team = {}
                for hm in hkmo_matches:
                    key = f"{hm.get('home','')}vs{hm.get('away','')}"
                    hkmo_by_team[key] = hm
                for m in matches:
                    key = f"{m.get('home','')}vs{m.get('away','')}"
                    if key in hkmo_by_team:
                        m["hkmo"] = hkmo_by_team[key]
                    else:
                        # 模糊匹配
                        for hm in hkmo_matches:
                            if name_similar(hm.get('home',''), m.get('home','')) and name_similar(hm.get('away',''), m.get('away','')):
                                m["hkmo"] = hm
                                break
                print(f"  港澳赔率匹配 {len([m for m in matches if m.get('hkmo')])}/{len(matches)} 场，双方同时降水 {hkmo_both_drop} 场", flush=True)
            except Exception as e:
                print(f"  500竞彩页面港澳抓取失败: {e}", flush=True)
        else:
            print("  港澳赔率模块不可用，跳过", flush=True)
    except Exception as e:
        print(f"  港澳赔率抓取失败: {e}", flush=True)

    # 港澳赔率抓取后重新生成让球盘和亚指推荐（依赖hkmo数据，核心方向可能变化）
    for m in matches:
        core_dir, core_src = get_core_direction(m)
        m["hhadVerdict"] = evaluate_hhad(m, cfg, core_dir)
        m["asianVerdict"] = evaluate_asian(m, cfg, core_dir)
    # 同步更新历史记录中的hhadVerdict和asianVerdict（若旧记录为null）
    for m in matches:
        mid = m["id"]
        if mid in by_id:
            if not by_id[mid].get("hhadVerdict"):
                by_id[mid]["hhadVerdict"] = m.get("hhadVerdict")
            if not by_id[mid].get("asianVerdict"):
                by_id[mid]["asianVerdict"] = m.get("asianVerdict")

    # 同步更新港澳赔率历史记录中的hhadVerdict和asianVerdict
    try:
        hkmo_hist_path = os.path.join(HERE, "hkmo_history.json")
        if os.path.exists(hkmo_hist_path):
            with open(hkmo_hist_path, encoding="utf-8") as f:
                hkmo_hist = json.load(f)
            hkmo_records = hkmo_hist.get("records", [])
            # 按队名匹配
            match_by_team = {}
            for m in matches:
                key = f"{m.get('home','')}vs{m.get('away','')}"
                match_by_team[key] = m
            updated = 0
            for rec in hkmo_records:
                key = f"{rec.get('home','')}vs{rec.get('away','')}"
                if key in match_by_team:
                    m = match_by_team[key]
                    if not rec.get("hhadVerdict") and m.get("hhadVerdict"):
                        rec["hhadVerdict"] = m["hhadVerdict"]
                        updated += 1
                    if not rec.get("asianVerdict") and m.get("asianVerdict"):
                        rec["asianVerdict"] = m["asianVerdict"]
                        updated += 1
            if updated > 0:
                hkmo_hist["records"] = hkmo_records
                with open(hkmo_hist_path, "w", encoding="utf-8") as f:
                    json.dump(hkmo_hist, f, ensure_ascii=False, indent=1)
                print(f"  港澳历史同步推荐字段 {updated} 项", flush=True)
    except Exception as e:
        print(f"  港澳历史同步推荐失败: {e}", flush=True)

    # 同步推荐字段后再次回填，计算让球盘和亚指推荐的命中
    try:
        backfill_hkmo_history(matches)
    except Exception as e:
        print(f"  港澳历史二次回填失败: {e}", flush=True)

    # [5.10/6] BSD多博彩公司实时赔率（Pinnacle/Bet365/1xBet等权威博彩公司+水位变动）
    # 隔离原则（继承接管规范）：赔率结果只写入「独立文件」odds_bsd.json，
    # 绝不写回核心 matches 列表（matches.json），也不进入 history.json（模型训练数据）。
    # 前端从独立全局 ODDS_BSD 读取，本源与模型/核心数据零耦合。
    print("[5.10/6] BSD多博彩公司实时赔率（Pinnacle/Bet365等，独立源）...", flush=True)
    try:
        from fetch_odds_bsd import fetch_and_match as fetch_odds_bsd_data
        odds_bsd_data = fetch_odds_bsd_data(matches)
        odds_bsd_matches = odds_bsd_data.get("matches", [])
        # 独立落盘：核心 matches 列表保持纯净，不被污染
        _obsd_out = {
            "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "The Odds API (Pinnacle/Bet365 等)",
            "independent": True,
            "matches": odds_bsd_matches,
            "total_movements": odds_bsd_data.get("total_movements", 0),
        }
        with open(os.path.join(HERE, "odds_bsd.json"), "w", encoding="utf-8") as _f:
            json.dump(_obsd_out, _f, ensure_ascii=False, indent=1)
        odds_bsd_n = len(odds_bsd_matches)
        print(f"  BSD多博彩赔率匹配 {odds_bsd_n}/{len(matches)} 场（独立文件 odds_bsd.json），"
              f"赔率变动 {odds_bsd_data.get('total_movements', 0)} 次", flush=True)
    except Exception as e:
        print(f"  BSD多博彩赔率抓取失败: {e}", flush=True)

    # 串关推荐：每天只在第一次运行时生成并锁定，后续批次直接读取锁定的推荐，不再变动
    locked_safe, locked_high = load_locked_parlay()
    if locked_safe or locked_high:
        # 只要今天生成过任何一条串关记录，就整体锁定不再变动
        safe_parlay = locked_safe if locked_safe else None
        high_odds_parlay = None  # 高倍串关已移除
        gen_at = (locked_safe or locked_high).get('generatedAt', '')
        # 如果当天没生成稳胆（只有高倍），补生成稳胆，避免中倍稳胆tab空白
        if not safe_parlay:
            safe_parlay = gen_safe_parlay(matches)
            if safe_parlay:
                print(f"  中倍稳胆补生成：2串1/3串1", flush=True)
        print(f"  串关推荐：今日已锁定（{gen_at}），不再变动", flush=True)
    else:
        # 稳胆串关：低赔高信心，追求高命中率（高倍串关已按用户要求永久移除）
        safe_parlay = gen_safe_parlay(matches)
        high_odds_parlay = None
        # 保存当天串关推荐到历史记录（仅第一次生成时保存，锁定当天推荐）
        try:
            save_parlay_history(safe_parlay, high_odds_parlay)
        except Exception as e:
            print(f"  串关历史保存失败: {e}", flush=True)
    # 串关历史战绩回查
    parlay_history = check_parlay_history()

    # 强弱对战分析：筛选实力差距大、得分射手多的比赛
    print("[5.11/6] 强弱对战分析...", flush=True)
    strong_weak = gen_strong_weak_matches(matches)
    
    # 保存强弱对战历史推荐记录（用于赛后追溯命中/错误）
    strongweak_history = save_strongweak_history(strong_weak, matches)

    # 半全场胜平负推荐
    print("[5.12/6] 半全场胜平负推荐...", flush=True)
    half_full = gen_half_full(matches)
    half_full_history = save_half_full_history(half_full, matches)

    # 保存历史记录（在asianVerdict和BSD赔率更新之后，确保新字段被保存）
    hist_path = save_history(records)

    print("[6/6] 输出...", flush=True)
    # 只保留当天开售的比赛，明天/后天的不展示
    _today = time.strftime("%Y-%m-%d")
    _before = len(matches)
    matches = [m for m in matches if m.get("date") == _today]
    if _before != len(matches):
        print(f"  日期过滤：{_before}场 -> {len(matches)}场（只保留{_today}当天）", flush=True)
    out = os.path.join(HERE, "matches.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"updateTime": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(matches),
                   "matches": matches, "highOddsParlay": high_odds_parlay,
                   "safeParlay": safe_parlay, "parlayHistory": parlay_history,
                   "strongWeak": strong_weak, "strongWeakHistory": strongweak_history,
                   "halfFull": half_full, "halfFullHistory": half_full_history},
                  f, ensure_ascii=False, indent=1)
    print(f"  已写入 {out}（{len(matches)} 场）与 {hist_path}（{len(records)} 条）", flush=True)

    # [7/6] 高手推荐生成（仅16:00-21:00抓取，其他时间跳过）
    _now_hour = time.localtime().tm_hour
    if 16 <= _now_hour <= 21:
        try:
            print("[7/6] 高手推荐生成...", flush=True)
            import fetch_expert
            fetch_expert.main()
        except Exception as e:
            print(f"  高手推荐生成失败：{e}", flush=True)
    else:
        print(f"[7/6] 高手推荐跳过（当前{_now_hour}点，仅16:00-21:00抓取）", flush=True)

    return matches

def rule_weight(cfg, g):
    """规则权重自动寻优：按该规则组历史命中率动态加权。
    样本≥20 时 w = 命中率/50（50% 为基准），封顶 [0.5, 1.8]；样本不足返回 1.0 不调整。"""
    c = cfg.get(g) or {}
    n = c.get("samples") or 0
    if n < 20:
        return 1.0
    rate = c.get("hitRate") if c.get("hitRate") is not None else 50.0
    w = rate / 50.0
    return round(max(0.5, min(1.8, w)), 2)

def evaluate(m, cfg):
    """竞彩版规则判定（阈值由回测配置动态调整）：返回 (verdict, hits, score)
    verdict: {t, txt, dir(推荐方向), stars, score}"""
    hits = []
    prob, sup = m["hadProb"], m["hadSup"]
    rrate = m.get("returnRate")
    goal = m["hhad"]["goal"]

    def diff(d):
        p, s = prob.get(d), sup.get(d)
        if p is None or s is None:
            return None
        return round(s - p, 1)

    t1 = cfg["组1热度深负"]["threshold"]
    t6 = cfg["组6深度过热"]["threshold"]
    t2 = cfg["组2正路确认"]["threshold"]
    t3 = cfg["组3冷门监控"]["threshold"]
    t4 = cfg["组4异常水位"]["threshold"]
    t7 = cfg["组7资金流入"]["threshold"]
    t8 = cfg["组8资金背离"]["threshold"]
    t9 = cfg["组9凯利价值"]["threshold"]
    t10 = cfg["组10排名压制"]["threshold"]
    t11 = cfg["组11进球预期"]["threshold"]
    t12 = cfg["组12半场掌控"]["threshold"]
    t13 = cfg["组13攻防压制"]["threshold"]

    # 组1 热度深负：某方向支持率显著高于概率 -> 该方向过热危险
    for d, name in (("h", "主胜"), ("d", "平局"), ("a", "客胜")):
        dv = diff(d)
        if dv is not None and dv >= t1:
            hits.append({"g": "组1热度深负", "t": "bad", "dir": d,
                         "label": f"{name}支持率{sup[d]}%高于概率{prob[d]}%达{dv}pt，投注过热，该方向危险",
                         "cond": f"{name} 支持率-概率={dv}pt(≥{t1})"})
    # 组6 深度过热
    for d, name in (("h", "主胜"), ("d", "平局"), ("a", "客胜")):
        dv = diff(d)
        if dv is not None and dv >= t6:
            hits.append({"g": "组6深度过热", "t": "bad", "dir": d,
                         "label": f"{name}支持率远超概率{dv}pt，深度过热，机构放任资金涌入",
                         "cond": f"{name} 支持率-概率={dv}pt(≥{t6})"})
    # 组2 正路确认：热门方向(概率>=50)且支持率与概率接近 -> 正路更易打出
    hot = max(("h", "d", "a"), key=lambda d: prob.get(d) or 0)
    hot_p = prob.get(hot) or 0
    if hot_p >= 50:
        dv = diff(hot)
        if dv is not None and abs(dv) < t2:
            hits.append({"g": "组2正路确认", "t": "good", "dir": hot,
                         "label": f"{('主胜','平局','客胜')[('h','d','a').index(hot)]}概率{hot_p}%为热门且支持率偏差仅{dv}pt，方向一致，正路更易打出",
                         "cond": f"热门方向概率{hot_p}%≥50%，支持率-概率={dv}pt(<{t2})"})
    # 组3 冷门监控：让球>=2 且 热门概率>=55 但支持率显著低于概率 -> 小心爆冷
    try:
        gv = int(goal)
    except (TypeError, ValueError):
        gv = 0
    if abs(gv) >= 2 and hot_p >= 55:
        dv = diff(hot)
        if dv is not None and dv <= -t3:
            hits.append({"g": "组3冷门监控", "t": "cold", "dir": hot,
                         "label": f"深盘让球{gv}但{('主胜','平局','客胜')[('h','d','a').index(hot)]}支持率{sup.get(hot)}%低于概率{hot_p}%达{-dv}pt，小心爆冷",
                         "cond": f"让球|{gv}|≥2、热门概率{hot_p}%≥55%、支持率-概率={dv}pt(≤-{t3})"})
    # 组4 异常水位
    if rrate is not None and rrate < t4:
        hits.append({"g": "组4异常水位", "t": "warn", "dir": "",
                     "label": f"胜平负返还率仅{rrate}%，水位异常偏低，谨慎参与",
                     "cond": f"返还率{rrate}%<{t4}%"})

    # 凯利指数 = 赔率 × 官方概率（以返还率为基准，凯利差>0 表示该方向价值偏高）
    kelly, kelly_diff = {}, {}
    for d in ("h", "d", "a"):
        od = m["had"].get(d)
        p = prob.get(d)
        if od and p is not None:
            kelly[d] = round(od * p / 100.0, 3)
            kelly_diff[d] = round(kelly[d] - (rrate or 0) / 100.0, 3)
        else:
            kelly[d], kelly_diff[d] = None, None

    # 组7 资金流入确认：热门方向赔率下降>=阈值 -> 资金流入，强化热门
    mov = m.get("mov") or {}
    mv_hot = mov.get(hot)
    if hot_p >= 45 and mv_hot is not None and mv_hot <= -t7:
        hits.append({"g": "组7资金流入", "t": "good", "dir": hot,
                     "label": f"热门方向{('主胜','平局','客胜')[('h','d','a').index(hot)]}赔率从开盘至今下降{mv_hot}%，资金持续流入，强化打出预期",
                     "cond": f"热门赔率变动={mv_hot}%(≤-{t7})"})
    # 组8 资金背离：热门方向赔率上升>=阈值 -> 资金流出，警示热门
    if hot_p >= 45 and mv_hot is not None and mv_hot >= t8:
        hits.append({"g": "组8资金背离", "t": "bad", "dir": hot,
                     "label": f"热门方向{('主胜','平局','客胜')[('h','d','a').index(hot)]}赔率不降反升{mv_hot}%，资金背离，警惕陷阱",
                     "cond": f"热门赔率变动={mv_hot}%(≥{t8})"})
    # 组9 凯利价值：某方向凯利差显著为正 -> 概率被低估，价值方向
    for d, name in (("h", "主胜"), ("d", "平局"), ("a", "客胜")):
        kd = kelly_diff.get(d)
        if kd is not None and kd * 100 >= t9:
            hits.append({"g": "组9凯利价值", "t": "good", "dir": d,
                         "label": f"{name}凯利指数{kelly[d]}高于返还率{rrate}%，概率或被低估，具备价值",
                         "cond": f"{name} 凯利-返还率={round(kd*100,1)}pt(≥{t9})"})

    # 组10 排名压制：主队排名明显优于客队且主胜热门 -> 实力确认；排名优但市场不认 -> 虚高预警
    hrn, arn = m.get("hrn"), m.get("arn")
    if hrn and arn:
        rank_gap = arn - hrn  # 正值=主队排名更优
        hot_name = ("主胜", "平局", "客胜")[("h", "d", "a").index(hot)]
        if rank_gap >= t10:
            if hot == "h" and hot_p >= 45:
                hits.append({"g": "组10排名压制", "t": "good", "dir": "h",
                             "label": f"主队排名[{m.get('hr')}]优于客队[{m.get('ar')}]达{rank_gap}位且主胜概率{hot_p}%，实力+市场双确认",
                             "cond": f"排名差{rank_gap}位(≥{t10})，主胜概率{hot_p}%≥45%"})
            elif hot == "h" and hot_p < 45:
                hits.append({"g": "组10排名压制", "t": "cold", "dir": "h",
                             "label": f"主队排名[{m.get('hr')}]优于客队[{m.get('ar')}]达{rank_gap}位但主胜概率仅{hot_p}%，排名优势未被市场定价，虚高预警",
                             "cond": f"排名差{rank_gap}位(≥{t10})但主胜概率{hot_p}%<45%"})
        elif rank_gap <= -t10 and hot == "a" and hot_p >= 45:
            hits.append({"g": "组10排名压制", "t": "good", "dir": "a",
                         "label": f"客队排名[{m.get('ar')}]优于主队[{m.get('hr')}]达{-rank_gap}位且客胜概率{hot_p}%，实力+市场双确认",
                         "cond": f"排名差{-rank_gap}位(≥{t10})，客胜概率{hot_p}%≥45%"})

    # 组11 进球预期：总进球期望极高/极低影响让球盘判断（辅助评分，不单独下结论）
    ge = m.get("goalsExp")
    try:
        gv = int(goal)
    except (TypeError, ValueError):
        gv = 0
    if ge is not None and abs(gv) >= 1:
        if ge >= t11:
            hits.append({"g": "组11进球预期", "t": "warn", "dir": hot,
                         "label": f"总进球期望{ge}球偏高，让球{gv}的强队难保不丢球，主胜打出难度加大，谨慎看待",
                         "cond": f"期望进球{ge}≥{t11}、让球{gv}≥1"})
        elif ge <= 2.2 and gv >= 1:
            hits.append({"g": "组11进球预期", "t": "good", "dir": "h",
                         "label": f"总进球期望仅{ge}球偏低，让球{gv}的主队零封概率高，主胜更稳",
                         "cond": f"期望进球{ge}≤2.2、让球{gv}≥1"})

    # 组12 半场掌控：半全场"主/主"或"客/客"隐含概率高 -> 强队全场压制，正路强化
    if m.get("hhProb") is not None and m.get("hhProb") >= t12 and hot == "h":
        hits.append({"g": "组12半场掌控", "t": "good", "dir": "h",
                     "label": f"半全场主/主隐含概率{m['hhProb']}%≥{t12}%，机构预期主队全场压制，强化主胜",
                     "cond": f"主/主隐含概率{m['hhProb']}%≥{t12}%"})
    if m.get("aaProb") is not None and m.get("aaProb") >= t12 and hot == "a":
        hits.append({"g": "组12半场掌控", "t": "good", "dir": "a",
                     "label": f"半全场客/客隐含概率{m['aaProb']}%≥{t12}%，机构预期客队全场压制，强化客胜",
                     "cond": f"客/客隐含概率{m['aaProb']}%≥{t12}%"})

    # 组13 攻防压制：双方近期场均进/失球（北单+竞彩历史池自算）
    atk = m.get("attack") or {}
    ah, aa2 = atk.get("home"), atk.get("away")
    if ah and aa2 and ah["n"] >= 3 and aa2["n"] >= 3:
        h_adv = round(ah["gf"] - aa2["ga"], 2)   # 主队进攻 vs 客队防守净值
        a_adv = round(aa2["gf"] - ah["ga"], 2)   # 客队进攻 vs 主队防守净值
        if h_adv >= t13 and hot == "h":
            hits.append({"g": "组13攻防压制", "t": "good", "dir": "h",
                         "label": f"主队近{ah['n']}场场均进球{ah['gf']} vs 客队近{aa2['n']}场场均失球{aa2['ga']}，攻防净值+{h_adv}，实力压制确认主胜",
                         "cond": f"主攻{ah['gf']}-客防{aa2['ga']}={h_adv}(≥{t13})"})
        if a_adv >= t13 and hot == "a":
            hits.append({"g": "组13攻防压制", "t": "good", "dir": "a",
                         "label": f"客队近{aa2['n']}场场均进球{aa2['gf']} vs 主队近{ah['n']}场场均失球{ah['ga']}，攻防净值+{a_adv}，实力压制确认客胜",
                         "cond": f"客攻{aa2['gf']}-主防{ah['ga']}={a_adv}(≥{t13})"})
        sum_gf = round(ah["gf"] + aa2["gf"], 2)
        ge2 = m.get("goalsExp")
        if ge2 is not None and abs(sum_gf - ge2) >= 1.0:
            hits.append({"g": "组13攻防压制", "t": "warn", "dir": hot,
                         "label": f"双方近期场均进球合计{sum_gf} vs 官方盘口期望{ge2}球，攻防模型与官方分歧，大小球方向谨慎",
                         "cond": f"攻防和{sum_gf} vs 期望{ge2}偏差{round(abs(sum_gf-ge2),2)}(≥1.0)"})

    # 结论优先级: bad > cold > good > warn
    order = {"bad": 0, "cold": 1, "good": 2, "warn": 3}
    hits.sort(key=lambda x: order.get(x["t"], 9))
    verdict = None
    if hits:
        v = hits[0]
        verdict = {"t": v["t"], "txt": v["label"]}

    # 综合信心评分：方向独立打分 -> 推荐方向 + 星级（含规则权重寻优）
    scores = {"h": 0.0, "d": 0.0, "a": 0.0}
    reasons = []
    for h in hits:
        d = h.get("dir")
        if not d:
            continue
        w = rule_weight(cfg, h["g"])
        if h["t"] == "good":
            add = round(1.5 * w, 2)
            scores[d] += add
            reasons.append(f"{h['g']}·{('主胜','平局','客胜')[('h','d','a').index(d)]}+{add}{'×'+str(w) if w != 1.0 else ''}")
        elif h["t"] in ("bad", "cold"):
            sub = round(1.0 * w, 2)
            scores[d] -= sub
            reasons.append(f"{h['g']}·{('主胜','平局','客胜')[('h','d','a').index(d)]}-{sub}{'×'+str(w) if w != 1.0 else ''}")
        elif h["t"] == "warn":
            sub = round(0.5 * w, 2)
            scores[d] -= sub
            reasons.append(f"{h['g']}·{('主胜','平局','客胜')[('h','d','a').index(d)]}-{sub}{'×'+str(w) if w != 1.0 else ''}")
    for d in ("h", "d", "a"):
        pct = mov.get(d)
        if pct is not None and pct <= -t7:
            scores[d] += 1.0
            reasons.append(f"资金流入{('主胜','平局','客胜')[('h','d','a').index(d)]}赔率{pct}%+1.0")
        elif pct is not None and pct >= t8:
            scores[d] -= 1.0
            reasons.append(f"资金流出{('主胜','平局','客胜')[('h','d','a').index(d)]}赔率+{pct}%-1.0")
        kd = kelly_diff.get(d)
        if kd is not None and kd * 100 >= t9:
            scores[d] += 0.5
            reasons.append(f"凯利价值{('主胜','平局','客胜')[('h','d','a').index(d)]}+0.5")
        elif kd is not None and kd * 100 <= -t9:
            scores[d] -= 0.5

    # ========== BSD增强数据维度（伤停/AI预测/教练/天气/裁判/首发）==========
    bsd = m.get("bsd") or {}
    if bsd:
        # 1. 伤停影响：主队伤停多→主胜减分，客队伤停多→客胜减分
        h_inj = bsd.get("injury_count_home", 0) or 0
        a_inj = bsd.get("injury_count_away", 0) or 0
        inj_diff = h_inj - a_inj
        if inj_diff >= 3:  # 主队伤停明显更多
            scores["a"] += 1.5
            scores["h"] -= 0.5
            reasons.append(f"伤停·主队{h_inj}人vs客队{a_inj}人，主队阵容不整→客胜+1.5")
        elif inj_diff <= -3:  # 客队伤停明显更多
            scores["h"] += 1.5
            scores["a"] -= 0.5
            reasons.append(f"伤停·客队{a_inj}人vs主队{h_inj}人，客队阵容不整→主胜+1.5")

        # 2. AI预测方向：从ai_preview中提取预测比分
        ai_preview = bsd.get("ai_preview", "") or ""
        if ai_preview:
            import re
            pred_match = re.search(r'(\d+)\s*[-:]\s*(\d+)', ai_preview)
            if pred_match:
                try:
                    pred_h = int(pred_match.group(1))
                    pred_a = int(pred_match.group(2))
                    if pred_h > pred_a:
                        ai_dir = "h"
                    elif pred_h < pred_a:
                        ai_dir = "a"
                    else:
                        ai_dir = "d"
                    scores[ai_dir] += 2.0
                    reasons.append(f"AI预测·{pred_h}-{pred_a}→{('主胜','平局','客胜')[('h','d','a').index(ai_dir)]}+2.0")
                except (ValueError, IndexError):
                    pass

        # 3. 教练风格：进攻型教练→所在球队胜加分
        home_coach = bsd.get("home_coach") or {}
        away_coach = bsd.get("away_coach") or {}
        h_styles = home_coach.get("styles", []) if isinstance(home_coach, dict) else []
        a_styles = away_coach.get("styles", []) if isinstance(away_coach, dict) else []
        h_attack = any(s in str(h_styles).lower() for s in ['attack', 'pressing', 'offensive'])
        a_attack = any(s in str(a_styles).lower() for s in ['attack', 'pressing', 'offensive'])
        if h_attack and not a_attack:
            scores["h"] += 0.8
            reasons.append(f"教练·主队进攻型({home_coach.get('formation','')})→主胜+0.8")
        elif a_attack and not h_attack:
            scores["a"] += 0.8
            reasons.append(f"教练·客队进攻型({away_coach.get('formation','')})→客胜+0.8")

        # 4. 天气影响：恶劣天气→平局加分
        weather = bsd.get("weather") or {}
        if isinstance(weather, dict):
            temp = weather.get("temperature_c", 20) or 20
            wind = weather.get("wind_speed", 0) or 0
            if temp <= 5 or temp >= 35 or wind >= 30:
                scores["d"] += 0.5
                reasons.append(f"天气·温度{temp}°C/风速{wind}km/h，恶劣天气→平局+0.5")

        # 5. 裁判风格：出牌多的裁判→比赛激烈，分胜负加分
        referee = bsd.get("referee") or {}
        if isinstance(referee, dict):
            yc = referee.get("yellow_cards", 0) or 0
            games = referee.get("career_games", 1) or 1
            yc_per_game = yc / max(games, 1)
            if yc_per_game >= 4:
                scores["d"] -= 0.3
                reasons.append(f"裁判·{referee.get('name','')}场均{yc_per_game:.1f}黄，执法严格→平局-0.3")

        # 6. 预期首发完整性
        lineups = bsd.get("expected_lineups") or {}
        if isinstance(lineups, dict):
            h_lu = lineups.get("home", []) or []
            a_lu = lineups.get("away", []) or []
            h_complete = len(h_lu) >= 10
            a_complete = len(a_lu) >= 10
            if h_complete and not a_complete:
                scores["h"] += 0.5
                reasons.append(f"首发·主队完整({len(h_lu)}人)vs客队不整({len(a_lu)}人)→主胜+0.5")
            elif a_complete and not h_complete:
                scores["a"] += 0.5
                reasons.append(f"首发·客队完整({len(a_lu)}人)vs主队不整({len(h_lu)}人)→客胜+0.5")

    # ========== BSD增强数据维度结束 ==========

    best = max(scores, key=scores.get)
    sc = round(scores[best], 2)
    stars = 5 if sc >= 3.5 else 4 if sc >= 2.5 else 3 if sc >= 1.5 else 2 if sc >= 0.8 else 1 if sc > 0 else 0
    score = {"dir": best, "score": sc, "stars": stars,
             "kelly": {d: kelly[d] for d in "hda"},
             "kellyDiff": {d: (round(kd*100, 1) if kd is not None else None) for d, kd in kelly_diff.items()},
             "mov": mov, "reasons": reasons}
    if verdict:
        verdict["dir"] = best
        verdict["stars"] = stars
        verdict["score"] = sc
        verdict["reasons"] = reasons
    return verdict, hits, score


def get_core_direction(m):
    """获取核心推荐方向（普通胜平负 h/d/a），用于统一让球盘和亚指推荐
    优先级：港澳双方同时降水方向 > 规则引擎综合评分方向 > 胜平负概率最高方向
    """
    # 1. 港澳赔率双方同时降水方向（最权威资金信号）
    hkmo = m.get("hkmo") or {}
    if hkmo.get("both_drop") and hkmo.get("common_drop_dirs"):
        return hkmo["common_drop_dirs"][0], "港澳双方同时降水"
    # 2. 规则引擎综合评分方向
    score = m.get("score") or {}
    if score.get("dir"):
        return score["dir"], "规则引擎综合评分"
    # 3. 胜平负概率最高方向
    prob = m.get("hadProb") or {}
    if prob:
        best = max(("h", "d", "a"), key=lambda d: prob.get(d) or 0)
        return best, "胜平负概率最高"
    return None, "无信号"


def evaluate_hhad(m, cfg, core_dir=None):
    """竞彩让球盘推荐：基于hhad概率/支持率/赔率/初盘变动，返回 {t,txt,dir,stars,score,reasons}
    core_dir: 核心方向（普通胜平负h/d/a），用于确保推荐方向一致性
    """
    hhad = m.get("hhad") or {}
    prob = m.get("hhadProb") or {}
    sup = m.get("hhadSup") or {}
    hhad_open = m.get("hhadOpen") or {}
    goal = hhad.get("goal", "0")
    if not prob or not hhad:
        return None
    # 解析让球数
    try:
        gv = int(goal)
    except (TypeError, ValueError):
        gv = 0
    hits = []
    def diff(d):
        p, s = prob.get(d), sup.get(d)
        if p is None or s is None:
            return None
        return round(s - p, 1)
    t1 = cfg.get("组1热度深负", {}).get("threshold", 15)
    t2 = cfg.get("组2正路确认", {}).get("threshold", 5)
    t7 = cfg.get("组7资金流入", {}).get("threshold", 5)
    t8 = cfg.get("组8资金背离", {}).get("threshold", 5)
    hot = max(("h", "d", "a"), key=lambda d: prob.get(d) or 0)
    hot_p = prob.get(hot) or 0
    dir_names = {"h": "让胜", "d": "让平", "a": "让负"}
    # 组1 热度深负
    for d in ("h", "d", "a"):
        dv = diff(d)
        if dv is not None and dv >= t1:
            hits.append({"t": "bad", "dir": d, "label": f"{dir_names[d]}支持率{sup[d]}%高于概率{prob[d]}%达{dv}pt，投注过热"})
    # 组2 正路确认
    if hot_p >= 50:
        dv = diff(hot)
        if dv is not None and abs(dv) < t2:
            hits.append({"t": "good", "dir": hot, "label": f"{dir_names[hot]}概率{hot_p}%为热门且支持率偏差仅{dv}pt，方向一致"})
    # 资金流入/流出（让球盘初→现）
    mov_hhad = {}
    for d in ("h", "d", "a"):
        od = hhad.get(d)
        op = hhad_open.get(d) if isinstance(hhad_open, dict) else None
        if od and op and op > 0:
            mov_hhad[d] = round((od - op) / op * 100, 1)
    for d in ("h", "d", "a"):
        mv = mov_hhad.get(d)
        if mv is not None and mv <= -t7:
            hits.append({"t": "good", "dir": d, "label": f"{dir_names[d]}赔率从开盘下降{mv}%，资金流入"})
        elif mv is not None and mv >= t8:
            hits.append({"t": "bad", "dir": d, "label": f"{dir_names[d]}赔率上升{mv}%，资金背离"})
    # 评分
    scores = {"h": 0.0, "d": 0.0, "a": 0.0}
    reasons = []
    for h in hits:
        d = h.get("dir")
        if not d:
            continue
        if h["t"] == "good":
            scores[d] += 1.5
            reasons.append(f"让球盘·{dir_names[d]}+1.5")
        elif h["t"] in ("bad", "cold"):
            scores[d] -= 1.0
            reasons.append(f"让球盘·{dir_names[d]}-1.0")

    # ========== BSD增强数据维度（伤停/AI预测/教练/天气/裁判/首发）==========
    bsd = m.get("bsd") or {}
    if bsd:
        # 1. 伤停影响：主队伤停多→主队让球方向减分，客队伤停多→客队让球方向减分
        h_inj = bsd.get("injury_count_home", 0) or 0
        a_inj = bsd.get("injury_count_away", 0) or 0
        inj_diff = h_inj - a_inj  # 正数=主队伤停更多
        if inj_diff >= 3:  # 主队伤停明显更多
            if gv < 0:  # 主队让球：主队伤停多→让胜/让平减分，让负加分
                scores["a"] += 1.5
                scores["h"] -= 0.5
                reasons.append(f"伤停·主队{h_inj}人vs客队{a_inj}人，主队阵容不整→让负+1.5")
            else:  # 主队受让：主队伤停多→让胜减分，让负加分
                scores["a"] += 1.5
                scores["h"] -= 0.5
                reasons.append(f"伤停·主队{h_inj}人vs客队{a_inj}人，主队阵容不整→让负+1.5")
        elif inj_diff <= -3:  # 客队伤停明显更多
            if gv < 0:  # 主队让球：客队伤停多→让胜加分
                scores["h"] += 1.5
                scores["a"] -= 0.5
                reasons.append(f"伤停·客队{a_inj}人vs主队{h_inj}人，客队阵容不整→让胜+1.5")
            else:  # 主队受让：客队伤停多→让胜加分
                scores["h"] += 1.5
                scores["a"] -= 0.5
                reasons.append(f"伤停·客队{a_inj}人vs主队{h_inj}人，客队阵容不整→让胜+1.5")

        # 2. AI预测方向：从ai_preview中提取预测比分，转换为让球方向
        ai_preview = bsd.get("ai_preview", "") or ""
        if ai_preview:
            import re
            # 提取预测比分（如 "Roma 2-0 Torino" 或 "Prediction: Roma 2-0"）
            pred_match = re.search(r'(\d+)\s*[-:]\s*(\d+)', ai_preview)
            if pred_match:
                try:
                    pred_h = int(pred_match.group(1))
                    pred_a = int(pred_match.group(2))
                    adj_pred_h = pred_h + gv
                    if adj_pred_h > pred_a:
                        ai_dir = "h"
                    elif adj_pred_h < pred_a:
                        ai_dir = "a"
                    else:
                        ai_dir = "d"
                    scores[ai_dir] += 2.0
                    reasons.append(f"AI预测·{pred_h}-{pred_a}（让球后{adj_pred_h}-{pred_a}）→{dir_names[ai_dir]}+2.0")
                except (ValueError, IndexError):
                    pass

        # 3. 教练风格：进攻型教练→主胜/大球加分
        home_coach = bsd.get("home_coach") or {}
        away_coach = bsd.get("away_coach") or {}
        h_styles = home_coach.get("styles", []) if isinstance(home_coach, dict) else []
        a_styles = away_coach.get("styles", []) if isinstance(away_coach, dict) else []
        h_attack = any(s in str(h_styles).lower() for s in ['attack', 'pressing', 'offensive', '4-3-3', '4-2-3-1'])
        a_attack = any(s in str(a_styles).lower() for s in ['attack', 'pressing', 'offensive', '4-3-3', '4-2-3-1'])
        if h_attack and not a_attack:
            if gv < 0:  # 主队让球：主队进攻型→让胜加分
                scores["h"] += 0.8
                reasons.append(f"教练·主队进攻型({home_coach.get('formation','')})→让胜+0.8")
        elif a_attack and not h_attack:
            if gv < 0:  # 主队让球：客队进攻型→让负加分
                scores["a"] += 0.8
                reasons.append(f"教练·客队进攻型({away_coach.get('formation','')})→让负+0.8")

        # 4. 天气影响：恶劣天气→小球/平局加分
        weather = bsd.get("weather") or {}
        if isinstance(weather, dict):
            temp = weather.get("temperature_c", 20) or 20
            wind = weather.get("wind_speed", 0) or 0
            if temp <= 5 or temp >= 35 or wind >= 30:
                scores["d"] += 0.5
                reasons.append(f"天气·温度{temp}°C/风速{wind}km/h，恶劣天气→让平+0.5")

        # 5. 裁判风格：出牌多的裁判→比赛激烈，大球/分胜负加分
        referee = bsd.get("referee") or {}
        if isinstance(referee, dict):
            yc = referee.get("yellow_cards", 0) or 0
            games = referee.get("career_games", 1) or 1
            yc_per_game = yc / max(games, 1)
            if yc_per_game >= 4:
                scores["d"] -= 0.3
                reasons.append(f"裁判·{referee.get('name','')}场均{yc_per_game:.1f}黄，执法严格→让平-0.3")

        # 6. 预期首发完整性：首发完整度高的球队加分
        lineups = bsd.get("expected_lineups") or {}
        if isinstance(lineups, dict):
            h_lu = lineups.get("home", []) or []
            a_lu = lineups.get("away", []) or []
            h_complete = len(h_lu) >= 10
            a_complete = len(a_lu) >= 10
            if h_complete and not a_complete:
                if gv < 0:
                    scores["h"] += 0.5
                    reasons.append(f"首发·主队完整({len(h_lu)}人)vs客队不整({len(a_lu)}人)→让胜+0.5")
            elif a_complete and not h_complete:
                if gv < 0:
                    scores["a"] += 0.5
                    reasons.append(f"首发·客队完整({len(a_lu)}人)vs主队不整({len(h_lu)}人)→让负+0.5")

    # ========== BSD增强数据维度结束 ==========
    # 核心方向一致性约束：过滤掉与核心方向矛盾的让球盘方向
    allowed_dirs = {"h", "d", "a"}
    if core_dir:
        if core_dir == "h":  # 核心主胜
            if gv < 0:  # 主队让球：主胜→让球胜或让球平，不能让球负
                allowed_dirs = {"h", "d"}
            else:  # 主队受让：主胜→一定让球胜
                allowed_dirs = {"h"}
        elif core_dir == "a":  # 核心客胜
            if gv < 0:  # 主队让球：客胜→一定让球负
                allowed_dirs = {"a"}
            else:  # 主队受让：客胜→让球负或让球平，不能让球胜
                allowed_dirs = {"a", "d"}
        elif core_dir == "d":  # 核心平局
            if gv < 0:  # 主队让球：平局→让球负
                allowed_dirs = {"a"}
            else:  # 主队受让：平局→让球胜
                allowed_dirs = {"h"}
    # 在允许的方向中选择评分最高的
    filtered_scores = {d: scores[d] for d in allowed_dirs}
    best = max(filtered_scores, key=filtered_scores.get)
    sc = round(filtered_scores[best], 2)
    stars = 5 if sc >= 3.0 else 4 if sc >= 2.0 else 3 if sc >= 1.0 else 2 if sc > 0 else 1
    # 竞彩让球胜平负每场都有结果，必须给出明确推荐方向（让胜/让平/让负）
    # 信心星级根据评分高低调整，评分低=低信心，但仍有推荐方向
    verdict = {"t": "good", "txt": f"推荐{dir_names[best]}（综合评分{sc}）", "dir": best, "stars": stars, "score": sc, "reasons": reasons, "goal": goal}
    return verdict


def evaluate_asian(m, cfg, core_dir=None):
    """亚指让球盘推荐：基于港澳亚盘升降盘+核心方向，返回 {t,txt,dir,stars,asianLine,reasons}
    core_dir: 核心方向（普通胜平负h/d/a），确保推荐方向与港澳降水一致
    亚指方向：h=上盘（让球方），a=下盘（受让方），d=走水
    """
    hkmo = m.get("hkmo") or {}
    asian_line = (hkmo.get("asian") or {}).get("current") or ""
    asian_change = (hkmo.get("asian") or {}).get("change", "none")
    asian_open = (hkmo.get("asian") or {}).get("open", "")
    asian_cur = (hkmo.get("asian") or {}).get("current") or ""
    hhad = m.get("hhad") or {}
    goal = hhad.get("goal", "0")
    had_prob = m.get("hadProb") or {}
    if not asian_line and not asian_cur:
        return None
    hot = max(("h", "d", "a"), key=lambda d: had_prob.get(d) or 0)
    dir_names = {"h": "上盘", "d": "走盘", "a": "下盘"}
    hits = []
    reasons = []
    # 亚盘数值转换（主队让球为正，客队让球为负）
    try:
        from fetch_hkmo import asian_to_num
        line_num = asian_to_num(asian_cur or asian_line)
    except Exception:
        line_num = None
    # 确定主队是上盘还是下盘
    home_is_upper = True  # 默认主队=上盘
    if line_num is not None and line_num < 0:
        home_is_upper = False  # 客队让球，客队=上盘，主队=下盘
    # 升盘=机构看好上盘，降盘=看好下盘
    if asian_change == "up":
        hits.append({"t": "good", "dir": "h", "label": f"亚盘升盘 {asian_open}→{asian_cur}，机构强化上盘"})
        reasons.append(f"亚盘升盘+1.5")
    elif asian_change == "down":
        hits.append({"t": "good", "dir": "a", "label": f"亚盘降盘 {asian_open}→{asian_cur}，机构看衰上盘"})
        reasons.append(f"亚盘降盘+1.5")
    # 核心方向约束：将普通胜平负方向转换为亚指上下盘方向
    core_asian_dir = None
    if core_dir:
        if core_dir == "h":  # 核心主胜
            core_asian_dir = "h" if home_is_upper else "a"  # 主队赢→主队所在盘口
        elif core_dir == "a":  # 核心客胜
            core_asian_dir = "a" if home_is_upper else "h"  # 客队赢→客队所在盘口
        elif core_dir == "d":  # 核心平局
            # 平局时，让球方（上盘）没赢，下盘赢；平手盘走水
            if line_num is not None and abs(line_num) < 0.01:
                core_asian_dir = "d"  # 平手盘平局=走水
            else:
                core_asian_dir = "a"  # 非平手盘平局=下盘赢
        if core_asian_dir:
            hits.append({"t": "good", "dir": core_asian_dir,
                         "label": f"核心方向{('主胜','平局','客胜')[('h','d','a').index(core_dir)]}→{dir_names[core_asian_dir]}"})
            reasons.append(f"核心方向一致+2.0")
    # 竞彩让球与亚盘一致性
    try:
        gv = int(goal)
    except (TypeError, ValueError):
        gv = 0
    if gv != 0 and asian_line:
        hits.append({"t": "warn", "dir": "", "label": f"竞彩让球{goal} vs 亚盘{asian_line}，盘口差异需注意"})
    scores = {"h": 0.0, "d": 0.0, "a": 0.0}
    for h in hits:
        d = h.get("dir")
        if not d:
            continue
        if h["t"] == "good":
            scores[d] += 1.5
        elif h["t"] in ("bad", "cold"):
            scores[d] -= 1.0

    # ========== BSD增强数据维度（伤停/AI预测/教练/天气/裁判/首发）==========
    bsd = m.get("bsd") or {}
    if bsd:
        # 1. 伤停影响：主队伤停多→主队所在盘口减分，客队伤停多→客队所在盘口减分
        h_inj = bsd.get("injury_count_home", 0) or 0
        a_inj = bsd.get("injury_count_away", 0) or 0
        inj_diff = h_inj - a_inj
        if inj_diff >= 3:  # 主队伤停明显更多
            if home_is_upper:  # 主队=上盘→上盘减分，下盘加分
                scores["a"] += 1.5
                scores["h"] -= 0.5
                reasons.append(f"伤停·主队{h_inj}人vs客队{a_inj}人，主队阵容不整→下盘+1.5")
            else:  # 主队=下盘→下盘减分，上盘加分
                scores["h"] += 1.5
                scores["a"] -= 0.5
                reasons.append(f"伤停·主队{h_inj}人vs客队{a_inj}人，主队阵容不整→上盘+1.5")
        elif inj_diff <= -3:  # 客队伤停明显更多
            if home_is_upper:  # 主队=上盘→上盘加分
                scores["h"] += 1.5
                scores["a"] -= 0.5
                reasons.append(f"伤停·客队{a_inj}人vs主队{h_inj}人，客队阵容不整→上盘+1.5")
            else:  # 主队=下盘→下盘加分
                scores["a"] += 1.5
                scores["h"] -= 0.5
                reasons.append(f"伤停·客队{a_inj}人vs主队{h_inj}人，客队阵容不整→下盘+1.5")

        # 2. AI预测方向：从ai_preview中提取预测比分，转换为亚指上下盘
        ai_preview = bsd.get("ai_preview", "") or ""
        if ai_preview:
            import re
            pred_match = re.search(r'(\d+)\s*[-:]\s*(\d+)', ai_preview)
            if pred_match:
                try:
                    pred_h = int(pred_match.group(1))
                    pred_a = int(pred_match.group(2))
                    if line_num is not None and line_num != 0:
                        if line_num > 0:  # 主队让球=上盘
                            goal_diff = pred_h - pred_a
                            upper_win = goal_diff > abs(line_num) + 0.01
                        else:  # 客队让球=上盘
                            goal_diff = pred_a - pred_h
                            upper_win = goal_diff > abs(line_num) + 0.01
                        ai_dir = "h" if upper_win else "a"
                    else:  # 平手盘
                        if pred_h > pred_a:
                            ai_dir = "h" if home_is_upper else "a"
                        elif pred_h < pred_a:
                            ai_dir = "a" if home_is_upper else "h"
                        else:
                            ai_dir = "d"
                    if ai_dir in ("h", "a"):
                        scores[ai_dir] += 2.0
                        reasons.append(f"AI预测·{pred_h}-{pred_a}→{dir_names[ai_dir]}+2.0")
                except (ValueError, IndexError):
                    pass

        # 3. 教练风格：进攻型教练→所在盘口加分
        home_coach = bsd.get("home_coach") or {}
        away_coach = bsd.get("away_coach") or {}
        h_styles = home_coach.get("styles", []) if isinstance(home_coach, dict) else []
        a_styles = away_coach.get("styles", []) if isinstance(away_coach, dict) else []
        h_attack = any(s in str(h_styles).lower() for s in ['attack', 'pressing', 'offensive'])
        a_attack = any(s in str(a_styles).lower() for s in ['attack', 'pressing', 'offensive'])
        if h_attack and not a_attack:
            if home_is_upper:
                scores["h"] += 0.8
                reasons.append(f"教练·主队进攻型→上盘+0.8")
            else:
                scores["a"] += 0.8
                reasons.append(f"教练·主队进攻型→下盘+0.8")
        elif a_attack and not h_attack:
            if home_is_upper:
                scores["a"] += 0.8
                reasons.append(f"教练·客队进攻型→下盘+0.8")
            else:
                scores["h"] += 0.8
                reasons.append(f"教练·客队进攻型→上盘+0.8")

        # 4. 天气影响：恶劣天气→下盘（小球/平局）加分
        weather = bsd.get("weather") or {}
        if isinstance(weather, dict):
            temp = weather.get("temperature_c", 20) or 20
            wind = weather.get("wind_speed", 0) or 0
            if temp <= 5 or temp >= 35 or wind >= 30:
                scores["a"] += 0.5
                reasons.append(f"天气·温度{temp}°C/风速{wind}km/h，恶劣天气→下盘+0.5")

        # 5. 预期首发完整性
        lineups = bsd.get("expected_lineups") or {}
        if isinstance(lineups, dict):
            h_lu = lineups.get("home", []) or []
            a_lu = lineups.get("away", []) or []
            h_complete = len(h_lu) >= 10
            a_complete = len(a_lu) >= 10
            if h_complete and not a_complete:
                if home_is_upper:
                    scores["h"] += 0.5
                    reasons.append(f"首发·主队完整vs客队不整→上盘+0.5")
                else:
                    scores["a"] += 0.5
                    reasons.append(f"首发·主队完整vs客队不整→下盘+0.5")
            elif a_complete and not h_complete:
                if home_is_upper:
                    scores["a"] += 0.5
                    reasons.append(f"首发·客队完整vs主队不整→下盘+0.5")
                else:
                    scores["h"] += 0.5
                    reasons.append(f"首发·客队完整vs主队不整→上盘+0.5")

    # ========== BSD增强数据维度结束 ==========

    # 核心方向作为加分项而非强制选择（让BSD数据也能影响最终方向）
    if core_asian_dir:
        scores[core_asian_dir] += 1.0  # 核心方向加分（从+2.0降为+1.0，避免过度依赖）
    best = max(scores, key=scores.get)
    sc = round(scores[best], 2)
    stars = 4 if sc >= 2.5 else 3 if sc >= 1.5 else 2 if sc > 0 else 1
    # 亚指每场都必须给出推荐方向（上盘/下盘），走水只是比赛结果的一种，不作为推荐方向
    # 如果评分最高的是走水d，则改为上盘/下盘中评分较高的方向
    if best == "d":
        best = "h" if scores["h"] >= scores["a"] else "a"
        sc = round(scores[best], 2)
    verdict = {"t": "good", "txt": f"推荐{dir_names[best]}（{asian_cur or asian_line}，综合评分{sc}）", "dir": best, "stars": stars, "score": sc,
               "asianLine": asian_cur or asian_line, "asianOpen": asian_open, "asianChange": asian_change,
               "reasons": reasons}
    return verdict


def gen_score_predict(m):
    """比分推荐：基于进球预期/胜平负概率/攻防数据/让球，返回 {predicted,alternatives,confidence,reasons}"""
    ge = m.get("goalsExp")
    prob = m.get("hadProb") or {}
    hhad = m.get("hhad") or {}
    attack = m.get("attack") or {}
    if not prob:
        return None
    try:
        gv = int(hhad.get("goal", 0) or 0)
    except (TypeError, ValueError):
        gv = 0
    hot = max(("h", "d", "a"), key=lambda d: prob.get(d) or 0)
    hot_p = prob.get(hot) or 0
    # 预期总进球
    total_goals = ge if ge is not None else 2.5
    # 攻防数据
    ah = attack.get("home") or {}
    aa = attack.get("away") or {}
    h_gf = ah.get("gf", 1.3) if ah else 1.3
    h_ga = ah.get("ga", 1.3) if ah else 1.3
    a_gf = aa.get("gf", 1.3) if aa else 1.3
    a_ga = aa.get("ga", 1.3) if aa else 1.3
    # 主队预期进球 = (主队进攻 + 客队防守) / 2，按总进球缩放
    base_h = (h_gf + a_ga) / 2.0
    base_a = (a_gf + h_ga) / 2.0
    scale = total_goals / max(base_h + base_a, 0.1)
    exp_h = float(f"{base_h * scale:.1f}")
    exp_a = float(f"{base_a * scale:.1f}")
    # 让球调整
    if gv > 0:  # 主队让球，主队更强
        exp_h += 0.3 * gv
    elif gv < 0:
        exp_a += 0.3 * abs(gv)
    exp_h = float(f"{exp_h:.1f}")
    exp_a = float(f"{exp_a:.1f}")
    # 取最可能比分（四舍五入，边界处理）
    def nearest_score(eh, ea):
        sh = max(0, int(round(eh)))
        sa = max(0, int(round(ea)))
        return f"{sh}:{sa}"
    predicted = nearest_score(exp_h, exp_a)
    # 备选比分（±1球范围）
    alts = []
    for dh in (-1, 0, 1):
        for da in (-1, 0, 1):
            if dh == 0 and da == 0:
                continue
            sh = max(0, int(round(exp_h)) + dh)
            sa = max(0, int(round(exp_a)) + da)
            s = f"{sh}:{sa}"
            if s != predicted and s not in alts:
                alts.append(s)
            if len(alts) >= 3:
                break
        if len(alts) >= 3:
            break
    # 信心度
    confidence = min(95, int(hot_p * 0.6 + 40))
    reasons = [
        f"预期总进球{total_goals}球",
        f"主队预期进球{exp_h}·客队预期进球{exp_a}",
        f"热门方向{('主胜','平局','客胜')[('h','d','a').index(hot)]}概率{hot_p}%",
    ]
    if gv != 0:
        reasons.append(f"竞彩让球{gv}球调整")
    return {"predicted": predicted, "alternatives": alts[:3], "confidence": confidence,
            "expHome": exp_h, "expAway": exp_a, "totalGoals": total_goals, "reasons": reasons}


def gen_strong_weak_matches(matches):
    """强弱对战分析：筛选双方实力差距大、得分射手多的比赛。
    判断标准：让球数≥1、概率差距≥25%、进攻火力强、近期状态差距明显。
    数据来源：竞彩官方赔率/概率 + 500球队近况/攻防数据 + 公开球队情报交叉验证。
    返回 [{num, home, away, strongTeam, weakTeam, gapLevel, reasons, scorers, 
            strongInfo, weakInfo, recommendation, dataSource, searchDate}]"""
    import time
    today = time.strftime("%Y-%m-%d")
    search_date = time.strftime("%Y-%m-%d %H:%M")
    result = []

    for m in matches:
        if m.get("date") != today:
            continue
        num = m.get("num", "")
        home = m.get("home", "")
        away = m.get("away", "")
        hhad = m.get("hhad") or {}
        prob = m.get("hadProb") or {}
        attack = m.get("attack") or {}
        form = m.get("form") or {}
        had = m.get("had") or {}

        if not prob or not hhad:
            continue

        try:
            goal = int(hhad.get("goal", 0) or 0)
        except (TypeError, ValueError):
            goal = 0

        # 概率差距
        h_p = prob.get("h", 0) or 0
        d_p = prob.get("d", 0) or 0
        a_p = prob.get("a", 0) or 0
        max_p = max(h_p, a_p)
        min_p = min(h_p, a_p)
        prob_gap = max_p - min_p

        # 进攻数据
        ah = attack.get("home") or {}
        aa = attack.get("away") or {}
        h_gf = ah.get("gf", 0) or 0
        a_gf = aa.get("gf", 0) or 0
        h_ga = ah.get("ga", 0) or 0
        a_ga = aa.get("ga", 0) or 0

        # 近况胜率
        fh = form.get("home") or {}
        fa = form.get("away") or {}
        h_wr = fh.get("wr", 0) or 0
        a_wr = fa.get("wr", 0) or 0
        wr_gap = abs(h_wr - a_wr)

        # 判断强弱队
        if h_p >= a_p:
            strong_team = home
            weak_team = away
            strong_is_home = True
            strong_prob = h_p
            weak_prob = a_p
            strong_gf = h_gf
            weak_gf = a_gf
            strong_wr = h_wr
            weak_wr = a_wr
            strong_ga = h_ga
            weak_ga = a_ga
        else:
            strong_team = away
            weak_team = home
            strong_is_home = False
            strong_prob = a_p
            weak_prob = h_p
            strong_gf = a_gf
            weak_gf = h_gf
            strong_wr = a_wr
            weak_wr = h_wr
            strong_ga = a_ga
            weak_ga = h_ga

        # 强弱判定条件
        gap_score = 0
        reasons = []

        # 让球差距
        if abs(goal) >= 2:
            gap_score += 3
            reasons.append(f"竞彩让球{abs(goal)}球，实力差距悬殊")
        elif abs(goal) >= 1:
            gap_score += 2
            reasons.append(f"竞彩让球{abs(goal)}球，实力差距明显")

        # 概率差距
        if prob_gap >= 40:
            gap_score += 3
            reasons.append(f"胜平负概率差距{prob_gap:.0f}%（{strong_prob:.0f}% vs {weak_prob:.0f}%），强弱分明")
        elif prob_gap >= 25:
            gap_score += 2
            reasons.append(f"胜平负概率差距{prob_gap:.0f}%（{strong_prob:.0f}% vs {weak_prob:.0f}%），优势明显")

        # 进攻火力
        if strong_gf >= 2.0 and weak_ga >= 1.5:
            gap_score += 2
            reasons.append(f"强队场均进球{strong_gf}球，弱队场均失球{weak_ga}球，进攻对位优势大")
        elif strong_gf >= 1.5:
            gap_score += 1
            reasons.append(f"强队场均进球{strong_gf}球，进攻火力充足")

        # 状态差距
        if wr_gap >= 40:
            gap_score += 2
            reasons.append(f"近期胜率差距{wr_gap:.0f}%（{strong_wr:.0f}% vs {weak_wr:.0f}%），状态差距大")
        elif wr_gap >= 20:
            gap_score += 1
            reasons.append(f"近期胜率差距{wr_gap:.0f}%（{strong_wr:.0f}% vs {weak_wr:.0f}%），状态有差距")

        # 主场能力强加分（强队主场胜率高=主场龙，或客场胜率高=客场龙）
        h_home_wr = fh.get("home_wr", 0) or 0
        a_away_wr = fa.get("away_wr", 0) or 0
        if strong_is_home:
            if h_home_wr >= 70:
                gap_score += 2
                reasons.append(f"强队主场胜率{h_home_wr:.0f}%，主场龙属性极强")
            elif h_home_wr >= 55:
                gap_score += 1
                reasons.append(f"强队主场胜率{h_home_wr:.0f}%，主场能力强")
        else:
            if a_away_wr >= 55:
                gap_score += 1
                reasons.append(f"强队客场胜率{a_away_wr:.0f}%，客场作战能力强")

        # 弱队客场/主场差加分（弱队在对应场地表现差，进一步放大差距）
        weak_home_wr = fh.get("home_wr", 0) or 0
        weak_away_wr = fa.get("away_wr", 0) or 0
        if strong_is_home and weak_away_wr <= 25:
            gap_score += 1
            reasons.append(f"弱队客场胜率{weak_away_wr:.0f}%，客场虫属性明显")
        elif not strong_is_home and weak_home_wr <= 30:
            gap_score += 1
            reasons.append(f"弱队主场胜率{weak_home_wr:.0f}%，主场表现差")

        # 硬性过滤：强队近期状态必须好（胜率≥50%），弱队状态差（胜率≤45%）
        # 这样才能保证是"大人打小孩"级别的强弱对决
        if strong_wr < 50:
            continue
        if weak_wr > 45:
            continue

        # 主场能力强过滤：强队主场胜率必须≥50%（主场龙），或客场胜率≥35%（客场不弱）
        # 确保强队在自己的场地有足够统治力（h_home_wr/a_away_wr已在上方加分部分定义）
        if strong_is_home:
            if h_home_wr < 50:
                continue
        else:
            if a_away_wr < 35:
                continue

        # 只有gap_score≥6才纳入强弱对战（只保留"明显"和"悬殊"级别，去掉"较大"）
        if gap_score < 6:
            continue

        # 强弱等级
        if gap_score >= 9:
            gap_level = "悬殊"
        elif gap_score >= 7:
            gap_level = "明显"
        else:
            gap_level = "较大"

        # "大人打小孩"极端标签：让球≥2且概率差距≥35%且强队胜率≥60%
        is_brutal = (abs(goal) >= 2 and prob_gap >= 35 and strong_wr >= 60)

        # 推荐方向
        if strong_is_home:
            rec_dir = "主胜"
            rec_odds = had.get("h", 0)
        else:
            rec_dir = "客胜"
            rec_odds = had.get("a", 0)

        # 让球推荐（与胜平负推荐方向严格一致）
        hhad_v = m.get("hhadVerdict") or {}
        hhad_dir = hhad_v.get("dir", "")
        hhad_names = {"h": "让胜", "d": "让平", "a": "让负"}
        # 根据胜平负推荐方向确定让球盘允许的方向
        if rec_dir == "主胜":
            if goal < 0:  # 主队让球：主胜→让胜或让平
                allowed_hhad = {"h", "d"}
                default_hhad = "h"
            else:  # 主队受让：主胜→一定让胜
                allowed_hhad = {"h"}
                default_hhad = "h"
        elif rec_dir == "客胜":
            if goal < 0:  # 主队让球：客胜→一定让负
                allowed_hhad = {"a"}
                default_hhad = "a"
            else:  # 主队受让：客胜→让负或让平，不能让胜
                allowed_hhad = {"a", "d"}
                default_hhad = "a"
        else:  # 平局
            if goal < 0:  # 主队让球：平局→让负
                allowed_hhad = {"a"}
                default_hhad = "a"
            else:  # 主队受让：平局→让胜
                allowed_hhad = {"h"}
                default_hhad = "h"
        # 优先使用hhadVerdict，但必须在允许方向内
        if hhad_dir and hhad_dir in allowed_hhad:
            hhad_rec = hhad_names[hhad_dir]
            hhad_stars = hhad_v.get("stars", 0)
            hhad_score = hhad_v.get("score", 0)
        elif abs(goal) >= 1:
            # 使用默认方向（与胜平负推荐一致）
            hhad_rec = hhad_names[default_hhad]
            hhad_stars = 0
            hhad_score = 0
        else:
            hhad_rec = "—"
            hhad_stars = 0
            hhad_score = 0

        # 增强数据支撑：伤停、H2H、主客场战绩、关键球员（全部来自真实数据源）
        bsd = m.get("bsd") or {}
        h2h = m.get("h2h") or {}
        form_home = m.get("form", {}).get("home", {}) or {}
        form_away = m.get("form", {}).get("away", {}) or {}

        # 联赛排名差距（BSD无standings字段，暂不显示；如有500排名数据可在此扩展）
        rank_gap = ""

        # 伤停影响（BSD真实数据：injury_count_home/away + 详细伤停名单）
        injury_info = ""
        injury_detail = []
        h_inj = bsd.get("injury_count_home", 0) or 0
        a_inj = bsd.get("injury_count_away", 0) or 0
        h_inj_list = bsd.get("injuries_home", []) or []
        a_inj_list = bsd.get("injuries_away", []) or []
        if isinstance(h_inj, int) and isinstance(a_inj, int) and (h_inj > 0 or a_inj > 0):
            injury_info = f"伤停：主队{h_inj}人/客队{a_inj}人"
            # 提取详细伤停名单（球员名+伤情）
            for inj in h_inj_list[:3]:
                if isinstance(inj, dict):
                    name = inj.get("name", "")
                    reason = inj.get("reason", "")
                    if name:
                        injury_detail.append(f"主队{name}({reason})" if reason else f"主队{name}")
            for inj in a_inj_list[:3]:
                if isinstance(inj, dict):
                    name = inj.get("name", "")
                    reason = inj.get("reason", "")
                    if name:
                        injury_detail.append(f"客队{name}({reason})" if reason else f"客队{name}")
            if h_inj >= 3 or a_inj >= 3:
                if (strong_is_home and a_inj >= 3) or (not strong_is_home and h_inj >= 3):
                    gap_score += 1
                    reasons.append(f"弱队伤停{max(h_inj,a_inj)}人，阵容不整")

        # H2H历史交锋优势（如有真实H2H数据）
        h2h_info = ""
        h2h_home = h2h.get("homeWins") or h2h.get("h", 0)
        h2h_away = h2h.get("awayWins") or h2h.get("a", 0)
        h2h_draw = h2h.get("draws") or h2h.get("d", 0)
        if isinstance(h2h_home, int) and isinstance(h2h_away, int):
            total_h2h = h2h_home + h2h_away + h2h_draw
            if total_h2h >= 3:
                if strong_is_home and h2h_home > h2h_away:
                    h2h_info = f"历史交锋主队{h2h_home}胜{h2h_draw}平{h2h_away}负，占优"
                    gap_score += 1
                    reasons.append(h2h_info)
                elif not strong_is_home and h2h_away > h2h_home:
                    h2h_info = f"历史交锋客队{h2h_away}胜{h2h_draw}平{h2h_home}负，占优"
                    gap_score += 1
                    reasons.append(h2h_info)

        # 主客场战绩（来自500球队近况form数据中的home_wr/away_wr，真实有效）
        # h_home_wr/a_away_wr已在上方加分部分定义
        ha_info = ""
        if strong_is_home:
            # 强队主场 vs 弱队客场
            weak_away_wr = form_away.get("away_wr", 0) or 0
            if h_home_wr >= 50:
                ha_info = f"强队主场胜率{h_home_wr:.0f}% vs 弱队客场胜率{weak_away_wr:.0f}%，主客场差距{abs(h_home_wr-weak_away_wr):.0f}%"
        else:
            # 强队客场 vs 弱队主场
            weak_home_wr = form_home.get("home_wr", 0) or 0
            if a_away_wr >= 40:
                ha_info = f"强队客场胜率{a_away_wr:.0f}% vs 弱队主场胜率{weak_home_wr:.0f}%，主客场差距{abs(a_away_wr-weak_home_wr):.0f}%"

        # 关键球员/射手信息（从BSD ai_preview真实赛前分析中提取，优先进攻核心）
        scorers = []
        top_scorer = ""
        ai_preview = bsd.get("ai_preview", "") or ""
        if ai_preview:
            import re
            # 提取ai_preview中加粗的球员名（**Name**格式）
            bold_names = re.findall(r'\*\*([A-Z][a-zA-Z\s\.\'-]+?)\*\*', ai_preview)
            seen = set()
            # 优先提取与进攻/进球相关段落中的球员（ai_preview中"attack"、"score"附近的球员）
            attack_section = ""
            for keyword in ['attack', 'score', 'goal', 'striker', 'forward', '火力', '进攻']:
                idx = ai_preview.lower().find(keyword)
                if idx > 0:
                    attack_section = ai_preview[max(0,idx-100):idx+200]
                    break
            attack_names = re.findall(r'\*\*([A-Z][a-zA-Z\s\.\'-]+?)\*\*', attack_section) if attack_section else []
            
            # 先从进攻相关段落提取（标注为最强射手）
            for name in attack_names:
                name = name.strip()
                lower_name = name.lower()
                if (len(name) >= 3 and name not in seen 
                    and ' and ' not in lower_name and ' vs ' not in lower_name
                    and not any(w in lower_name for w in ['prediction', 'key', 'storyline', 'expected', 'lineup', 'both', 'home', 'away', 'stade', 'paris', 'lens', 'brest', 'celta', 'malaga', 'heerenveen', 'telstar', 'leipzig', 'hamburg', 'lisbon', 'famalicao', 'lille', 'troyes', 'napoli', 'bologna', 'sassuolo', 'juventus', 'real', 'atletico', 'flamengo', 'corinthians', 'chicago', 'england', 'revolution'])):
                    if not (name.isupper() and len(name) <= 5):
                        seen.add(name)
                        top_scorer = name  # 第一个进攻相关球员标注为最强射手
                        scorers.append(f"★{name}")
                        break
            
            # 再从全文提取其他关键球员
            for name in bold_names:
                name = name.strip()
                lower_name = name.lower()
                if (len(name) >= 3 and name not in seen 
                    and ' and ' not in lower_name and ' vs ' not in lower_name
                    and not any(w in lower_name for w in ['prediction', 'key', 'storyline', 'expected', 'lineup', 'both', 'home', 'away', 'stade', 'paris', 'lens', 'brest', 'celta', 'malaga', 'heerenveen', 'telstar', 'leipzig', 'hamburg', 'lisbon', 'famalicao', 'lille', 'troyes', 'napoli', 'bologna', 'sassuolo', 'juventus', 'real', 'atletico', 'flamengo', 'corinthians', 'chicago', 'england', 'revolution'])):
                    if not (name.isupper() and len(name) <= 5):
                        seen.add(name)
                        scorers.append(name)
                        if len(scorers) >= 4:
                            break
        
        # 也从expected_lineups提取核心球员（前锋位置优先）
        lineups = bsd.get("expected_lineups") or {}
        if len(scorers) < 2 and lineups:
            home_lu = lineups.get("home", []) or []
            away_lu = lineups.get("away", []) or []
            # 强队的前锋优先
            strong_lu = home_lu if strong_is_home else away_lu
            for p in strong_lu:
                if isinstance(p, str) and len(p) >= 3 and p not in seen:
                    seen.add(p)
                    scorers.append(p)
                    if len(scorers) >= 3:
                        break

        # 强队/弱队信息摘要（数据来自竞彩官方概率 + 500球队近况，真实有效）
        # 增加主场能力数据：强队主场胜率/客场胜率
        strong_ha_label = "主场" if strong_is_home else "客场"
        strong_ha_wr = h_home_wr if strong_is_home else a_away_wr
        weak_ha_label = "客场" if strong_is_home else "主场"
        weak_ha_wr = a_away_wr if strong_is_home else h_home_wr
        strong_info = f"胜率{strong_wr:.0f}%·{strong_ha_label}胜率{strong_ha_wr:.0f}%·场均进{strong_gf}球·失{strong_ga}球·概率{strong_prob:.0f}%"
        weak_info = f"胜率{weak_wr:.0f}%·{weak_ha_label}胜率{weak_ha_wr:.0f}%·场均进{weak_gf}球·失{weak_ga}球·概率{weak_prob:.0f}%"

        # 数据真实性验证状态：核心数据（让球/概率/赔率/近况）必须存在才标记已验证
        core_verified = bool(prob and hhad and had and strong_wr > 0)
        # 数据源声明：只声明实际使用的真实数据源
        data_sources = ["竞彩官方赔率/概率/让球", "500球队近况/攻防数据"]
        if bsd:
            data_sources.append("BSD赛前分析/伤停/阵容")
        data_source_str = " + ".join(data_sources)

        result.append({
            "num": num,
            "home": home,
            "away": away,
            "strongTeam": strong_team,
            "weakTeam": weak_team,
            "strongIsHome": strong_is_home,
            "gapLevel": gap_level,
            "gapScore": gap_score,
            "reasons": reasons,
            "scorers": scorers,
            "strongInfo": strong_info,
            "weakInfo": weak_info,
            "recommendation": rec_dir,
            "recommendOdds": rec_odds,
            "hhadRecommendation": hhad_rec,
            "hhadStars": hhad_stars,
            "hhadScore": hhad_score,
            "goal": goal,
            "rankGap": rank_gap,
            "injuryInfo": injury_info,
            "injuryDetail": injury_detail,
            "topScorer": top_scorer,
            "isBrutal": is_brutal,
            "h2hInfo": h2h_info,
            "haInfo": ha_info,
            "homeAdvantage": {
                "strongIsHome": strong_is_home,
                "strongHomeWr": h_home_wr if strong_is_home else a_away_wr,
                "weakAwayWr": a_away_wr if strong_is_home else h_home_wr,
                "label": strong_ha_label,
                "strongHaWr": strong_ha_wr,
                "weakHaWr": weak_ha_wr
            },
            "dataSource": data_source_str,
            "searchDate": search_date,
            "verified": core_verified
        })

    # 按gapScore降序排列
    result.sort(key=lambda x: x["gapScore"], reverse=True)
    print(f"  强弱对战：筛选出{len(result)}场实力差距明显的比赛", flush=True)
    return {"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "searchDate": search_date,
            "count": len(result),
            "matches": result}


def save_strongweak_history(strong_weak, matches):
    """保存强弱对战历史推荐记录，用于赛后追溯命中/错误。
    历史文件：strongweak_history.json，按日期存储。
    每场记录：num/home/away/strongTeam/weakTeam/recommendation/recommendOdds/
              hhadRecommendation/goal/gapLevel/gapScore/result/hit/hhadHit"""
    import os
    hist_path = os.path.join(HERE, "strongweak_history.json")
    today = time.strftime("%Y-%m-%d")
    
    # 读取已有历史
    history = {}
    if os.path.exists(hist_path):
        try:
            with open(hist_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = {}
    
    # 构建今天的记录
    today_records = []
    for m in strong_weak.get("matches", []):
        num = m.get("num", "")
        # 从matches中查找赛果回填
        result = ""
        hit = None
        hhad_hit = None
        for orig in matches:
            if orig.get("num") == num:
                # 赛果
                final = orig.get("final") or orig.get("result") or {}
                if isinstance(final, dict):
                    hs = final.get("homeScore")
                    as_ = final.get("awayScore")
                    if hs is not None and as_ is not None:
                        if hs > as_:
                            result = "主胜"
                        elif hs == as_:
                            result = "平局"
                        else:
                            result = "客胜"
                # 胜平负命中判定
                rec = m.get("recommendation", "")
                if result and rec:
                    hit = (rec == result)
                # 让球命中判定（从history records中查找）
                hhad_rec = m.get("hhadRecommendation", "")
                goal = m.get("goal", 0)
                if result and hhad_rec and goal != 0:
                    # 计算让球后结果
                    try:
                        g = int(goal)
                    except (TypeError, ValueError):
                        g = 0
                    # goal<0=主队让球，goal>0=主队受让
                    if result == "主胜":
                        adj = 3 + g  # 主胜=3分
                    elif result == "平局":
                        adj = 1 + g
                    else:
                        adj = 0 + g
                    if adj > 1:
                        hhad_result = "让胜"
                    elif adj == 1:
                        hhad_result = "让平"
                    else:
                        hhad_result = "让负"
                    hhad_hit = (hhad_rec == hhad_result)
                break
        
        today_records.append({
            "num": num,
            "home": m.get("home", ""),
            "away": m.get("away", ""),
            "strongTeam": m.get("strongTeam", ""),
            "weakTeam": m.get("weakTeam", ""),
            "recommendation": m.get("recommendation", ""),
            "recommendOdds": m.get("recommendOdds", 0),
            "hhadRecommendation": m.get("hhadRecommendation", ""),
            "goal": m.get("goal", 0),
            "gapLevel": m.get("gapLevel", ""),
            "gapScore": m.get("gapScore", 0),
            "topScorer": m.get("topScorer", ""),
            "isBrutal": m.get("isBrutal", False),
            "result": result,
            "hit": hit,
            "hhadHit": hhad_hit
        })
    
    history[today] = {
        "date": today,
        "generatedAt": strong_weak.get("generatedAt", ""),
        "count": len(today_records),
        "matches": today_records
    }
    
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    
    # 历史回填：遍历所有历史日期，从history.json补充赛果和命中判定
    try:
        hist_path2 = os.path.join(HERE, "history.json")
        hist_score_map = {}
        if os.path.exists(hist_path2):
            with open(hist_path2, "r", encoding="utf-8") as f2:
                hist_data = json.load(f2)
            for rec in hist_data.get("records", []):
                d = rec.get("date", "")
                num = rec.get("num", "")
                result = rec.get("result") or {}
                if isinstance(result, dict) and result.get("score") and not result.get("cancel"):
                    try:
                        parts = result["score"].replace("：", ":").split(":")
                        hs, as_ = int(parts[0]), int(parts[1])
                        hist_score_map[f"{d}|{num}"] = {"home": hs, "away": as_}
                    except Exception:
                        pass
        backfilled = 0
        for hist_date, day_data in history.items():
            if hist_date == today:
                continue
            if not isinstance(day_data, dict):
                continue
            for m in day_data.get("matches", []):
                # 已有真实赛果（result非空）才跳过；历史上曾出现hit已预设但result为空的脏数据，需补回填
                if m.get("result"):
                    continue
                num = m.get("num", "")
                key = f"{hist_date}|{num}"
                if key not in hist_score_map:
                    continue
                fs = hist_score_map[key]
                # 胜平负结果 + 具体比分
                score_str = f"{fs['home']}:{fs['away']}"
                if fs["home"] > fs["away"]:
                    m["result"] = f"主胜 {score_str}"
                    m["resultDir"] = "主胜"
                elif fs["home"] == fs["away"]:
                    m["result"] = f"平局 {score_str}"
                    m["resultDir"] = "平局"
                else:
                    m["result"] = f"客胜 {score_str}"
                    m["resultDir"] = "客胜"
                m["actualScore"] = score_str
                rec_text = m.get("recommendation", "")
                if rec_text:
                    m["hit"] = (rec_text == m["resultDir"])
                # 让球命中判定（使用实际比分计算）
                goal = m.get("goal", 0)
                hhad_rec = m.get("hhadRecommendation", "")
                if goal != 0 and hhad_rec and m.get("actualScore"):
                    try:
                        g = int(goal)
                    except (TypeError, ValueError):
                        g = 0
                    # 解析实际比分
                    score_str = m["actualScore"]
                    if ":" in score_str:
                        parts = score_str.split(":")
                        home_goals = int(parts[0])
                        away_goals = int(parts[1])
                        # 计算让球后比分
                        adj_home = home_goals + g
                        if adj_home > away_goals:
                            hhad_result = "让胜"
                        elif adj_home == away_goals:
                            hhad_result = "让平"
                        else:
                            hhad_result = "让负"
                        m["hhadHit"] = (hhad_rec == hhad_result)
                    else:
                        m["hhadHit"] = None
                else:
                    m["hhadHit"] = None
                backfilled += 1
        if backfilled > 0:
            print(f"  强弱对战历史回填：补充{backfilled}场历史赛果", flush=True)
            with open(hist_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  强弱对战历史回填失败: {e}", flush=True)

    # 保存后从文件重读，确保返回值与文件一致
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        pass

    # 统计命中
    hit_count = sum(1 for r in today_records if r["hit"] is True)
    miss_count = sum(1 for r in today_records if r["hit"] is False)
    print(f"  强弱对战历史已保存：{today} {len(today_records)}场（命中{hit_count}/错误{miss_count}）", flush=True)
    return history


# ============ 半全场胜平负推荐 ============
HF_LABELS = {
    "hh": "胜胜", "hd": "胜平", "ha": "胜负",
    "dh": "平胜", "dd": "平平", "da": "平负",
    "ah": "负胜", "ad": "负平", "aa": "负负"
}

def _poisson_prob(lam, k):
    """泊松分布概率"""
    import math
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def _poisson_match_prob(exp_h, exp_a, max_goals=6):
    """基于泊松分布计算胜平负概率"""
    h_prob = 0.0
    d_prob = 0.0
    a_prob = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = _poisson_prob(exp_h, i) * _poisson_prob(exp_a, j)
            if i > j:
                h_prob += p
            elif i == j:
                d_prob += p
            else:
                a_prob += p
    total = h_prob + d_prob + a_prob
    if total > 0:
        h_prob /= total
        d_prob /= total
        a_prob /= total
    return h_prob, d_prob, a_prob

def gen_half_full(matches):
    """半全场胜平负推荐：基于全场概率+半场泊松概率+条件概率矩阵计算9种组合概率。
    
    算法：
    1. 全场结果概率 → hadProb
    2. 半场结果概率 → 预期进球(scoreRec.expHome/expAway × 0.45)泊松分布
    3. 条件概率矩阵 → 半场领先方全场胜率根据强弱程度动态调整
    4. 联合概率 → P(半场X,全场Y) = P(半场X) × P(全场Y|半场X)
    5. 推荐概率最高的1-2种组合，信心度=概率百分比
    """
    import math
    today = time.strftime("%Y-%m-%d")
    today_matches = [m for m in matches if m.get("date") == today]
    now_ts = time.time()
    results = []
    
    for m in today_matches:
        # 只选未开赛或刚开赛的比赛
        kickoff = m.get("kickoff") or ""
        try:
            kt = time.mktime(time.strptime(kickoff, "%Y-%m-%d %H:%M"))
            if now_ts > kt + 45 * 60:  # 开赛超过45分钟（上半场结束）不再推荐
                continue
        except Exception:
            pass
        
        num = m.get("num", "")
        home = m.get("home", "")
        away = m.get("away", "")
        league = m.get("league", "")
        
        # 全场概率（处理只有让球盘没有胜平负的比赛，值为None时用默认值）
        had_prob = m.get("hadProb") or {}
        full_h = (had_prob.get("h") or 33) / 100.0
        full_d = (had_prob.get("d") or 33) / 100.0
        full_a = (had_prob.get("a") or 34) / 100.0
        
        # 半场预期进球（全场预期 × 0.45，上半场进球通常占全场45%）
        score_rec = m.get("scoreRec") or {}
        exp_home_full = score_rec.get("expHome", 1.4)
        exp_away_full = score_rec.get("expAway", 1.2)
        # 如果没有scoreRec，用goalsExp和攻防数据估算
        if exp_home_full <= 0:
            goals_exp = m.get("goalsExp", 2.5)
            attack = m.get("attack") or {}
            h_atk = attack.get("home", {}).get("gf", 1.3)
            a_atk = attack.get("away", {}).get("gf", 1.1)
            total_atk = h_atk + a_atk
            if total_atk > 0:
                exp_home_full = goals_exp * h_atk / total_atk
                exp_away_full = goals_exp * a_atk / total_atk
            else:
                exp_home_full = goals_exp / 2
                exp_away_full = goals_exp / 2
        
        exp_home_half = exp_home_full * 0.45
        exp_away_half = exp_away_full * 0.45
        
        # 半场泊松概率
        half_h_poisson, half_d_poisson, half_a_poisson = _poisson_match_prob(exp_home_half, exp_away_half)
        
        # 贝叶斯调整：半场概率 = 泊松概率×0.6 + 全场概率×0.4
        # 因为半场和全场结果有较强相关性，全场强队半场也更可能领先
        half_h = half_h_poisson * 0.6 + full_h * 0.4
        half_d = half_d_poisson * 0.6 + full_d * 0.4
        half_a = half_a_poisson * 0.6 + full_a * 0.4
        # 归一化
        half_total = half_h + half_d + half_a
        if half_total > 0:
            half_h /= half_total
            half_d /= half_total
            half_a /= half_total
        
        # 强弱程度系数（用于调整条件概率矩阵）
        # 全场主胜概率越高，半场主胜后全场主胜的概率也越高
        strength = full_h - full_a  # 正数=主队强，负数=客队强
        
        # 条件概率矩阵：P(全场Y | 半场X)
        # 基准矩阵（中等强弱比赛）
        cond_base = {
            "h": {"h": 0.72, "d": 0.18, "a": 0.10},  # 半场主胜
            "d": {"h": 0.40, "d": 0.32, "a": 0.28},  # 半场平
            "a": {"h": 0.10, "d": 0.18, "a": 0.72},  # 半场客胜
        }
        
        # 根据强弱程度动态调整条件概率
        cond = {}
        for hf in ["h", "d", "a"]:
            cond[hf] = {}
            for ff in ["h", "d", "a"]:
                base = cond_base[hf][ff]
                # 强队调整：主队强时，半场各种情况下全场主胜概率提升
                if ff == "h":
                    adj = strength * 0.15  # 强弱差距越大，调整越多
                elif ff == "a":
                    adj = -strength * 0.15
                else:
                    adj = 0
                val = max(0.05, min(0.90, base + adj))
                cond[hf][ff] = val
            # 归一化
            total = sum(cond[hf].values())
            if total > 0:
                for ff in cond[hf]:
                    cond[hf][ff] /= total
        
        # 计算9种联合概率
        probs = {}
        for hf in ["h", "d", "a"]:
            half_p = {"h": half_h, "d": half_d, "a": half_a}[hf]
            for ff in ["h", "d", "a"]:
                key = hf + ff
                probs[key] = half_p * cond[hf][ff]
        
        # 归一化
        total_p = sum(probs.values())
        if total_p > 0:
            for k in probs:
                probs[k] /= total_p
        
        # 排序选推荐
        sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
        top1_key, top1_prob = sorted_probs[0]
        top2_key, top2_prob = sorted_probs[1] if len(sorted_probs) > 1 else (None, 0)
        
        # 信心度计算：top1概率 × 数据完整度系数
        data_quality = 1.0
        if not score_rec:
            data_quality *= 0.85
        if not had_prob:
            data_quality *= 0.8
        confidence = round(top1_prob * 100 * data_quality, 1)
        confidence2 = round(top2_prob * 100 * data_quality, 1) if top2_key else 0
        
        # 推荐理由
        reasons = []
        if full_h > 0.55:
            reasons.append(f"全场主胜概率{full_h*100:.0f}%，主队优势明显")
        elif full_a > 0.55:
            reasons.append(f"全场客胜概率{full_a*100:.0f}%，客队优势明显")
        else:
            reasons.append(f"全场胜负接近（主{full_h*100:.0f}%/平{full_d*100:.0f}%/客{full_a*100:.0f}%）")
        
        if half_h > 0.45:
            reasons.append(f"半场主胜概率{half_h*100:.0f}%（预期进{exp_home_half:.1f}球）")
        elif half_a > 0.45:
            reasons.append(f"半场客胜概率{half_a*100:.0f}%（预期进{exp_away_half:.1f}球）")
        else:
            reasons.append(f"半场平局概率{half_d*100:.0f}%，上半场可能胶着")
        
        # 概率分布（用于页面展示）
        prob_dist = {HF_LABELS[k]: round(v * 100, 1) for k, v in sorted_probs}
        
        results.append({
            "num": num,
            "home": home,
            "away": away,
            "league": league,
            "kickoff": kickoff,
            "recommend": HF_LABELS[top1_key],
            "recommendKey": top1_key,
            "confidence": confidence,
            "recommend2": HF_LABELS[top2_key] if top2_key else "",
            "recommend2Key": top2_key or "",
            "confidence2": confidence2,
            "probDist": prob_dist,
            "fullProb": {"h": round(full_h*100,1), "d": round(full_d*100,1), "a": round(full_a*100,1)},
            "halfProb": {"h": round(half_h*100,1), "d": round(half_d*100,1), "a": round(half_a*100,1)},
            "expGoals": {"homeHalf": round(exp_home_half,2), "awayHalf": round(exp_away_half,2),
                          "homeFull": round(exp_home_full,2), "awayFull": round(exp_away_full,2)},
            "reasons": reasons,
            "dataQuality": round(data_quality * 100)
        })
    
    # 按信心度降序
    results.sort(key=lambda x: x["confidence"], reverse=True)
    
    print(f"  半全场推荐：生成{len(results)}场比赛推荐", flush=True)
    return {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "searchDate": today,
        "count": len(results),
        "matches": results
    }


def save_half_full_history(half_full, matches):
    """保存半全场推荐历史记录，用于赛后追溯命中/错误。
    历史文件：half_full_history.json，按日期存储。
    每场记录：num/home/away/recommend/confidence/probDist/actualHalfFull/result/hit"""
    import os
    hist_path = os.path.join(HERE, "half_full_history.json")
    today = time.strftime("%Y-%m-%d")
    
    # 读取已有历史
    history = {}
    if os.path.exists(hist_path):
        try:
            with open(hist_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = {}
    
    # 构建赛果查找表（按num）——先从当天matches，再从history.json历史记录
    score_map = {}
    for m in matches:
        num = m.get("num", "")
        result = m.get("result") or {}
        if isinstance(result, dict) and result.get("score"):
            try:
                parts = result["score"].replace("：", ":").split(":")
                hs = int(parts[0])
                as_ = int(parts[1])
                score_map[num] = {"home": hs, "away": as_}
            except Exception:
                pass
    # 从history.json补充历史赛果（覆盖昨天及更早已完场比赛）
    try:
        hist_path2 = os.path.join(HERE, "history.json")
        if os.path.exists(hist_path2):
            with open(hist_path2, "r", encoding="utf-8") as f2:
                hist_data = json.load(f2)
            for rec in hist_data.get("records", []):
                num = rec.get("num", "")
                if num in score_map:
                    continue  # 当天matches已有，优先用当天的
                result = rec.get("result") or {}
                if isinstance(result, dict) and result.get("score") and not result.get("cancel"):
                    try:
                        parts = result["score"].replace("：", ":").split(":")
                        hs = int(parts[0])
                        as_ = int(parts[1])
                        score_map[num] = {"home": hs, "away": as_}
                    except Exception:
                        pass
    except Exception as e:
        print(f"  半全场历史赛果补充失败: {e}", flush=True)
    
    # 构建今天的记录
    today_records = []
    for m in half_full.get("matches", []):
        num = m.get("num", "")
        actual_hf = ""
        hit = None
        
        # 回填实际半全场结果
        if num in score_map:
            # 注意：这里只有全场比分，没有半场比分
            # 半场比分需要从其他来源获取，暂时只标记全场结果
            # 实际半全场需要半场比分，后续可从500彩票网等获取
            full_score = score_map[num]
            full_dir = "h" if full_score["home"] > full_score["away"] else ("d" if full_score["home"] == full_score["away"] else "a")
            actual_hf = f"?{full_dir}"  # ?表示半场未知
            # 只有全场结果无法判定半全场命中，需要半场比分
            hit = None
        
        today_records.append({
            "num": num,
            "home": m.get("home", ""),
            "away": m.get("away", ""),
            "league": m.get("league", ""),
            "kickoff": m.get("kickoff", ""),
            "recommend": m.get("recommend", ""),
            "recommendKey": m.get("recommendKey", ""),
            "confidence": m.get("confidence", 0),
            "recommend2": m.get("recommend2", ""),
            "confidence2": m.get("confidence2", 0),
            "probDist": m.get("probDist", {}),
            "actualHalfFull": actual_hf,
            "hit": hit,
            "fullScore": score_map.get(num, {})
        })
    
    history[today] = {
        "date": today,
        "generatedAt": half_full.get("generatedAt", ""),
        "count": len(today_records),
        "matches": today_records
    }
    
    # 历史回填：遍历所有历史日期的记录，从history.json补充赛果和半全场命中
    try:
        hist_score_map = {}
        if os.path.exists(hist_path2):
            with open(hist_path2, "r", encoding="utf-8") as f2:
                hist_data = json.load(f2)
            for rec in hist_data.get("records", []):
                d = rec.get("date", "")
                num = rec.get("num", "")
                result = rec.get("result") or {}
                hf_result = rec.get("halfFullResult", "")  # 如"hd"
                if isinstance(result, dict) and result.get("score") and not result.get("cancel"):
                    try:
                        parts = result["score"].replace("：", ":").split(":")
                        hs = int(parts[0])
                        as_ = int(parts[1])
                        hist_score_map[f"{d}|{num}"] = {"home": hs, "away": as_, "halfFull": hf_result}
                    except Exception:
                        pass
        # 半全场9种标签映射
        _hf_labels = {"hh":"胜胜","hd":"胜平","ha":"胜负","dh":"平胜","dd":"平平","da":"平负","ah":"负胜","ad":"负平","aa":"负负"}
        backfilled = 0
        for hist_date, day_data in history.items():
            if hist_date == today:
                continue  # 当天已处理
            if not isinstance(day_data, dict):
                continue
            for m in day_data.get("matches", []):
                if m.get("hit") is not None:
                    continue  # 已判定
                num = m.get("num", "")
                key = f"{hist_date}|{num}"
                if key not in hist_score_map:
                    continue
                fs = hist_score_map[key]
                full_dir = "h" if fs["home"] > fs["away"] else ("d" if fs["home"] == fs["away"] else "a")
                m["fullScore"] = {"home": fs["home"], "away": fs["away"]}
                hf_actual = fs.get("halfFull", "")
                if hf_actual:
                    # 有官方半全场结果，精确判定
                    m["actualHalfFull"] = _hf_labels.get(hf_actual, hf_actual)
                    rec_key = m.get("recommendKey", "")
                    m["hit"] = (rec_key == hf_actual)
                    # 次推也判定
                    rec2_key = m.get("recommend2Key", "")
                    if rec2_key and rec2_key == hf_actual:
                        m["hit"] = True
                else:
                    # 无半场比分，仅标记全场结果
                    m["actualHalfFull"] = f"?{full_dir}"
                    m["hit"] = None
                backfilled += 1
        if backfilled > 0:
            print(f"  半全场历史回填：补充{backfilled}场历史赛果", flush=True)
    except Exception as e:
        print(f"  半全场历史回填失败: {e}", flush=True)
    
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    
    # 保存后从文件重读，确保返回值与文件一致（避免内存dict状态不一致）
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        pass

    hit_count = sum(1 for r in today_records if r["hit"] is True)
    miss_count = sum(1 for r in today_records if r["hit"] is False)
    pending = sum(1 for r in today_records if r["hit"] is None)
    print(f"  半全场历史已保存：{today} {len(today_records)}场（命中{hit_count}/错误{miss_count}/待赛{pending}）", flush=True)
    return history


def gen_high_odds_parlay(matches):
    """高倍混合串关（智能版）：每场比赛从胜平负、让球胜平负、总进球三种玩法中
    自动选择信心度×赔率最优的选项，组成混合高倍串关。
    信心度计算：规则触发+港澳降水+支持率背离+概率价值+数据匹配度。
    只选当天比赛，不跨天。
    返回 {parlay2, parlay3, generatedAt, strategy}"""
    import math
    today = time.strftime("%Y-%m-%d")
    matches = [m for m in matches if m.get("date") == today]
    candidates = []
    now_ts = time.time()

    for m in matches:
        # 只选未开赛的比赛
        kickoff = m.get("kickoff") or ""
        try:
            kt = time.mktime(time.strptime(f"{m.get('date','')} {kickoff}", "%Y-%m-%d %H:%M"))
            if kt < now_ts:
                continue
        except Exception:
            pass

        had = m.get("had") or {}
        hhad = m.get("hhad") or {}
        ttg = m.get("ttg") or {}
        verdict = m.get("verdict") or {}
        hhad_v = m.get("hhadVerdict") or {}
        vtype = verdict.get("t", "n-a")
        stars = verdict.get("stars", 0) or 0
        vdir = verdict.get("dir")
        hkmo = m.get("hkmo") or {}
        both_drop = hkmo.get("both_drop", False)
        prob = m.get("hadProb") or {}
        sup = m.get("hadSup") or {}
        goals_exp = m.get("goalsExp") or 2.5
        attack = m.get("attack") or {}

        match_options = []

        # ===== 玩法1：胜平负 =====
        # 基于历史回测的实际命中率：good正向50-57%，bad正向18-23%，bad反向60-82%
        # 策略：排除bad正向，纳入bad反向，good正向正常
        for d, label in [("h", "主胜"), ("d", "平局"), ("a", "客胜")]:
            odds = had.get(d)
            if not odds or odds < 1.5:
                continue
            # 基础信心度
            conf = 30.0
            reasons = []
            # 规则触发：基于历史回测调整
            if vtype == "good" and vdir == d:
                # good正向：命中率50-57%，高信心
                conf += stars * 10 + 20
                reasons.append(f"正路{stars}★")
            elif vtype == "bad" and vdir != d:
                # bad反向：bad推荐其他方向，当前方向是反向，命中率60-82%
                reverse_label = {"h": "主胜", "d": "平局", "a": "客胜"}.get(vdir, vdir)
                conf += stars * 8 + 25
                reasons.append(f"反指{stars}★(推{reverse_label})")
            elif vtype == "bad" and vdir == d:
                # bad正向：命中率仅18-23%，大幅降分，基本排除
                conf -= 20
                reasons.append(f"危险正向(低命中)")
            elif vtype == "warn" and vdir == d:
                # warn正向：命中率25%，降分
                conf -= 5
                reasons.append(f"谨慎{stars}★")
            # 移除港澳降水加分（仅用于港澳tab，不影响胜平负分析）
            # 支持率背离（支持率<概率=被低估，有价值）
            d_prob = prob.get(d, 0) or 0
            d_sup = sup.get(d, 0) or 0
            divergence = d_prob - d_sup
            if divergence > 10:
                conf += 10
                reasons.append(f"低估{divergence:.0f}%")
            elif divergence > 5:
                conf += 5
            # 赔率价值（概率×赔率>1有价值）
            value = (d_prob / 100.0) * odds if d_prob else 0
            if value > 1.1:
                conf += 8
                reasons.append(f"价值{value:.2f}")
            elif value > 0.9:
                conf += 3
            # 赔率合理性加分
            if 2.5 <= odds <= 6.0:
                conf += 5
            elif 6.0 < odds <= 9.0:
                conf += 0
            else:
                conf -= 5

            conf = min(95, max(20, conf))
            # bad正向信心度低于50的直接排除（命中率太低）
            if vtype == "bad" and vdir == d and conf < 55:
                continue
            match_options.append({
                "play": "胜平负", "playKey": "spf", "dir": d, "label": label,
                "odds": odds, "confidence": round(conf, 1),
                "score": round(conf * odds, 1), "reasons": reasons
            })

        # ===== 玩法2：让球胜平负 =====
        goal = hhad.get("goal", 0)
        hhad_vtype = hhad_v.get("t", "n-a")
        hhad_stars = hhad_v.get("stars", 0) or 0
        hhad_dir = hhad_v.get("dir")
        hhad_prob = m.get("hhadProb") or {}
        hhad_sup = m.get("hhadSup") or {}
        # 让球方向一致性约束：与核心推荐方向（港澳降水>综合评分>概率最高）保持一致
        core_dir, _ = get_core_direction(m)
        try:
            gv = int(goal)
        except (TypeError, ValueError):
            gv = 0
        allowed_hhad = {"h", "d", "a"}
        if core_dir:
            if core_dir == "h":  # 核心主胜
                allowed_hhad = {"h", "d"} if gv < 0 else {"h"}
            elif core_dir == "a":  # 核心客胜
                allowed_hhad = {"a"} if gv < 0 else {"a", "d"}
            elif core_dir == "d":  # 核心平局
                allowed_hhad = {"a"} if gv < 0 else {"h"}
        for d, label in [("h", "让胜"), ("d", "让平"), ("a", "让负")]:
            if d not in allowed_hhad:
                continue  # 跳过与核心方向矛盾的让球方向
            # 低概率过滤：让球数绝对值>=2时，不选穿盘方向（让2球赢3球+概率极低）
            if gv <= -2 and d == "h":
                continue  # 主队让2球+，让胜需要赢3球+，概率太低
            if gv >= 2 and d == "a":
                continue  # 主队受让2球+，让负需要输3球+，概率太低
            odds = hhad.get(d)
            if not odds or odds < 1.5:
                continue
            conf = 28.0
            reasons = []
            if hhad_vtype != "n-a" and hhad_dir == d:
                conf += hhad_stars * 7 + (12 if hhad_vtype in ("bad", "cold") else 6)
                reasons.append(f"让球规则{hhad_vtype}{hhad_stars}★")
            # 移除港澳降水（仅用于港澳tab，不影响让球盘分析）
            d_prob = hhad_prob.get(d, 0) or 0
            d_sup = hhad_sup.get(d, 0) or 0
            divergence = d_prob - d_sup
            if divergence > 10:
                conf += 8
                reasons.append(f"低估{divergence:.0f}%")
            value = (d_prob / 100.0) * odds if d_prob else 0
            if value > 1.1:
                conf += 6
                reasons.append(f"价值{value:.2f}")
            if 2.5 <= odds <= 5.5:
                conf += 5
            elif odds > 8.0:
                conf -= 5

            conf = min(95, max(20, conf))
            match_options.append({
                "play": f"让球{goal}", "playKey": "hhad", "dir": d, "label": label,
                "odds": odds, "confidence": round(conf, 1),
                "score": round(conf * odds, 1), "reasons": reasons
            })

        # ===== 玩法3：总进球 =====
        if ttg and goals_exp:
            # 泊松分布计算每个进球数的概率
            lam = float(goals_exp)
            poisson_probs = []
            for k in range(8):
                p = math.exp(-lam) * (lam ** k) / math.factorial(k)
                poisson_probs.append(p)
            # 攻防稳定性（样本数越多越稳定）
            ah = attack.get("home") or {}
            aa = attack.get("away") or {}
            stability = min(1.0, ((ah.get("n", 0) or 0) + (aa.get("n", 0) or 0)) / 20.0)

            for k in range(8):
                odds = ttg.get(str(k))
                if not odds or odds < 1.5:
                    continue
                # 基于泊松概率的基础信心度（总进球基于数据模型，基础信心度更高）
                base_prob = poisson_probs[k] * 100
                # 只选概率>8%的合理区间（过滤掉5球以上的低概率博冷选项）
                if base_prob < 8.0:
                    continue
                conf = 45 + base_prob * 1.5 * stability
                reasons = [f"预期{goals_exp}球", f"概率{base_prob:.0f}%"]
                # 偏差越小加分
                diff = abs(k - lam)
                if diff < 0.5:
                    conf += 10
                    reasons.append("最可能区间")
                elif diff < 1.0:
                    conf += 5
                # 赔率价值
                value = (base_prob / 100.0) * odds
                if value > 1.0:
                    conf += 6
                    reasons.append(f"价值{value:.2f}")
                elif value > 0.85:
                    conf += 2
                # 赔率合理性（总进球2-5倍最佳）
                if 1.8 <= odds <= 5.0:
                    conf += 5
                elif odds > 7.0:
                    conf -= 8

                conf = min(95, max(20, conf))
                label = f"{k}球" if k < 7 else "7+球"
                match_options.append({
                    "play": "总进球", "playKey": "ttg", "dir": str(k), "label": label,
                    "odds": odds, "confidence": round(conf, 1),
                    "score": round(conf * odds, 1), "reasons": reasons
                })

        # 每场选择Top3最优选项（信心度×赔率排序，但限制单场赔率≤5.0以控制总倍率、务实命中优先）
        match_options.sort(key=lambda x: x["score"], reverse=True)
        ranked = []
        for opt in match_options:
            if opt["odds"] <= 5.0:
                ranked.append(opt)
            if len(ranked) >= 3:
                break
        if len(ranked) < 3:
            # 若合理区间不足3个，从全量中补足
            for opt in match_options:
                if opt not in ranked:
                    ranked.append(opt)
                if len(ranked) >= 3:
                    break
        for opt in ranked[:3]:  # 每场最多3个选项进入候选
            candidates.append({
                "num": m.get("num"), "league": m.get("league"),
                "home": m.get("home"), "away": m.get("away"),
                "kickoff": kickoff, "play": opt["play"], "playKey": opt["playKey"],
                "dir": opt["dir"], "label": opt["label"],
                "odds": opt["odds"], "confidence": opt["confidence"],
                "score": opt["score"], "reasons": opt["reasons"]
            })

    # 按综合评分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    def build_parlay(n, min_odds=2.0, max_odds=10.0, total_cap=None, min_conf=65):
        """从candidates中选n场不同比赛，优先混合玩法，组成串关。
        total_cap: 总赔率上限，控制高倍串关不过度虚高（务实命中优先）。
        min_conf: 最低信心门槛，过滤低信心选项。
        组合搜索：在总赔率上限内选择平均信心最高的组合（命中优先），
        总赔率低于下限时不强求（避免选不满返回None）。"""
        import itertools

        # 候选预过滤：单场赔率范围 + 最低信心门槛
        pool = [c for c in candidates if min_odds <= c["odds"] <= max_odds and c["confidence"] >= min_conf]
        if len(pool) < n:
            # 放宽单场赔率上限，但仍保留合理上限（max_odds*1.3），防止虚高
            relaxed_max = max_odds * 1.3
            pool = [c for c in candidates if c["odds"] >= min_odds and c["odds"] <= relaxed_max and c["confidence"] >= min_conf - 5]

        def combo_ok(comb):
            nums = [c["num"] for c in comb]
            if len(set(nums)) != n:
                return False
            # 玩法分散优先：至少2种玩法（n=2时），n>=3时同玩法最多2个
            plays = [c["playKey"] for c in comb]
            if len(set(plays)) < 2 and n >= 2:
                return False
            total = 1.0
            for c in comb:
                total *= c["odds"]
            if total_cap and total > total_cap:
                return False
            return True

        best = None
        best_conf = -1
        for comb in itertools.combinations(pool, n):
            if not combo_ok(comb):
                continue
            avg_conf = sum(c["confidence"] for c in comb) / n
            # 优先高信心；同信心优先总赔率接近上限（保持高倍价值）
            if avg_conf > best_conf:
                best_conf = avg_conf
                best = comb

        if best is None:
            # 最后放宽：不限制玩法分散，但总赔率上限仍生效
            for comb in itertools.combinations(pool, n):
                nums = [c["num"] for c in comb]
                if len(set(nums)) != n:
                    continue
                total = 1.0
                for c in comb:
                    total *= c["odds"]
                if total_cap and total > total_cap:
                    continue
                avg_conf = sum(c["confidence"] for c in comb) / n
                if avg_conf > best_conf:
                    best_conf = avg_conf
                    best = comb

        if best is None:
            return None
        selected = list(best)
        total_odds = 1.0
        for s in selected:
            total_odds *= s["odds"]
        avg_conf = sum(s["confidence"] for s in selected) / len(selected)
        play_types = list(set(s["play"] for s in selected))
        return {
            "type": f"{n}串1", "legs": selected,
            "totalOdds": round(total_odds, 2),
            "avgConfidence": round(avg_conf, 1),
            "minOdds": min(s["odds"] for s in selected),
            "maxOdds": max(s["odds"] for s in selected),
            "playTypes": play_types,
            "isMixed": len(play_types) > 1
        }

    # 中倍稳胆策略：放弃高倍，专注命中率
    # 2串1：单场赔率1.8-3.5，总赔率上限8倍，信心门槛80（稳胆优先）
    parlay2 = build_parlay(2, min_odds=1.8, max_odds=3.5, total_cap=8.0, min_conf=80)
    # 3串1：单场赔率1.8-3.0，总赔率上限15倍，信心门槛80
    parlay3 = build_parlay(3, min_odds=1.8, max_odds=3.0, total_cap=15.0, min_conf=80)

    result = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "candidateCount": len(candidates),
        "strategy": "中倍稳胆：放弃高倍追求命中率，单场赔率1.8-3.5中倍区间，信心门槛80%+，bad反向+good正向策略，混合过关玩法分散",
        "parlay2": parlay2,
        "parlay3": parlay3
    }
    p2_info = f"{parlay2['totalOdds']}倍(信心{parlay2['avgConfidence']}%,{'混合' if parlay2.get('isMixed') else '单一'})" if parlay2 else "N/A"
    p3_info = f"{parlay3['totalOdds']}倍(信心{parlay3['avgConfidence']}%,{'混合' if parlay3.get('isMixed') else '单一'})" if parlay3 else "N/A"
    print(f"  中倍稳胆串关：候选{len(candidates)}个，2串1{p2_info}，3串1{p3_info}", flush=True)
    return result


def calc_kelly_index(odds, prob):
    """凯利指数计算：Kelly = (b*p - q) / b
    b=净赔率, p=胜率, q=败率
    凯利值>0为正期望值，<0为负期望值"""
    if not odds or odds <= 1:
        return 0
    b = odds - 1
    p = prob / 100
    q = 1 - p
    kelly = (b * p - q) / b
    return kelly * 100  # 百分比表示

def poisson_prob(lam_home, lam_away, max_goals=6):
    """泊松分布计算比分概率矩阵
    返回 {(h,a): prob} 字典"""
    import math
    probs = {}
    for h in range(max_goals+1):
        for a in range(max_goals+1):
            p = (math.exp(-lam_home) * lam_home**h / math.factorial(h)) * \
                (math.exp(-lam_away) * lam_away**a / math.factorial(a))
            probs[(h, a)] = p
    return probs

def poisson_1x2_probs(lam_home, lam_away):
    """泊松分布计算胜平负概率"""
    probs = poisson_prob(lam_home, lam_away)
    home_win = sum(p for (h, a), p in probs.items() if h > a)
    draw = sum(p for (h, a), p in probs.items() if h == a)
    away_win = sum(p for (h, a), p in probs.items() if h < a)
    total = sum(probs.values())
    return {
        'h': home_win/total*100,
        'd': draw/total*100,
        'a': away_win/total*100
    }

def calc_cold_hot_deviation(prob, support):
    """冷热偏差：概率 vs 支持率
    正值=概率高于支持率（被低估，热）
    负值=概率低于支持率（被高估，冷）"""
    return prob - support

def calc_market_move(open_odds, curr_odds):
    """市场异动：初盘 vs 即时盘变动
    负值=赔率下降（庄家看好，降水）
    正值=赔率上升（庄家不看好，升水）"""
    if not open_odds or not curr_odds:
        return 0
    return (curr_odds - open_odds) / open_odds * 100

def calc_dispersion(mov_data):
    """离散度分析：多家博彩公司赔率标准差/均值
    离散度高=分歧大，离散度低=共识强"""
    if not mov_data or len(mov_data) < 3:
        return 0
    # 过滤None值和非数字值
    vals = [v for v in mov_data.values() if v is not None and isinstance(v, (int, float))]
    if len(vals) < 3:
        return 0
    mean = sum(vals) / len(vals)
    if mean == 0:
        return 0
    variance = sum((v - mean)**2 for v in vals) / len(vals)
    std = variance ** 0.5
    return std / mean * 100  # 变异系数

def detect_upset_risk(prob, odds, kelly):
    """冷门预警：综合判断爆冷风险
    返回风险等级: low/medium/high"""
    # 概率<30%但赔率异常低？
    if prob < 30 and odds < 2.0:
        return 'high'  # 异常低赔，可能有内幕
    # 概率>50%但凯利值为负？
    if prob > 50 and kelly < -5:
        return 'medium'  # 正路但价值为负
    # 概率在30-40%之间？
    if 30 <= prob <= 40:
        return 'medium'
    return 'low'

def five_dimension_rating(prob_score, rule_score, odds_score, form_score, market_score):
    """五维雷达评分：五个维度综合评分
    每个维度0-20分，总分100"""
    return min(100, max(0, prob_score + rule_score + odds_score + form_score + market_score))


def calc_same_odds_signal(history_records, play_key, direction, ref_odds=None, goal=None, tol=0.12):
    """历史同指：在相同赔率参考（胜平负）或相同盘口线（让球）下，该方向的历史命中率。
    免费自建，仅用竞彩官方 history.json（354+ 已完赛记录），不依赖任何付费数据。
    返回 (hit_rate 0-100 或 None, n)。样本<5 视为无统计意义，返回 (None, n)。

    - play_key='spf'：用 ref_odds{h,d,a} 全赔率线匹配（三家赔率均落在容差内才算"同指"）
    - play_key='hhad'：用 goal 盘口线 + 同方向 verdict 匹配（历史同盘口同方向命中）
    """
    hits = 0
    n = 0
    for r in history_records:
        res = r.get("result")
        if not isinstance(res, dict) or res.get("home") is None or res.get("cancel"):
            continue
        if play_key == "spf":
            o = r.get("odds") or {}
            # 当前候选赔率含 None（某方向未开售）则无法比"同指"，跳过
            if not (isinstance(ref_odds, dict) and ref_odds.get(direction) is not None):
                continue
            ro = ref_odds.get(direction)
            ho = o.get(direction)
            if not (isinstance(ho, (int, float)) and isinstance(ro, (int, float))):
                continue
            # 同指：按所投方向的那一赔匹配（同一赔率参考 historically 命中率）；
            # 放宽到单一方向容差，避免"三家全同"过于稀疏导致几乎无样本
            if abs(ho - ro) > max(tol, 0.15):
                continue
            actual = res.get("had")
            if actual == direction:
                hits += 1
            n += 1
        elif play_key == "hhad":
            if r.get("goal") != goal:
                continue
            hv = r.get("hhadVerdict") or {}
            if hv.get("dir") != direction:
                continue
            hh = r.get("hhadHit")
            if hh == "hit":
                hits += 1
            elif hh == "miss":
                pass
            else:
                continue  # n-a 不计入样本
            n += 1
    if n < 5:
        return (None, n)
    return (round(hits / n * 100, 1), n)

def _parlay_true_prob(m, d):
    """串关用：返回该方向的「校准后真实概率」(0-1)。

    优先采用智能引擎 smart_engine 的校准概率 p_cal：
      - 若串关方向与规则推荐方向一致 → 直接用 p_cal
      - 若方向相反（利用 bad 反指）→ 把 (1 - p_cal) 按市场相对比例分配给其余两个结果
    无智能引擎结果时回退到市场概率。
    """
    prob = m.get("hadProb") or m.get("prob") or {}
    base = (prob.get(d) or 0) / 100.0
    smart = m.get("smart") or {}
    v = m.get("verdict") or {}
    vdir = v.get("dir")
    p_cal = smart.get("p_cal")
    if not vdir or p_cal is None:
        return base
    if d == vdir:
        return min(max(p_cal, 0.02), 0.96)
    others = [x for x in ("h", "d", "a") if x != vdir]
    tot = sum((prob.get(x) or 0) for x in others)
    rest = max(0.0, 1.0 - min(max(p_cal, 0.02), 0.96))
    if tot <= 0:
        return base
    return min(max(rest * (prob.get(d) or 0) / tot, 0.01), 0.95)


def gen_safe_parlay(matches):
    """智能混合过关稳串（命中率优先策略）：
    核心原则：
    1. 混合过关：胜平负+让球+总进球，每场选最优选项
    2. 命中率优先：基于历史数据校准，单场信心>70%
    3. 赔率合理：单场1.6-3.0，总赔3-8倍，拒绝蚊子肉
    4. 数据驱动：每场至少3项技术指标支撑
    5. 动态场数：比赛质量高时3串1，质量一般时2串1
    返回 {parlay2, parlay3, generatedAt, strategy}
    """
    today = time.strftime("%Y-%m-%d")
    matches = [m for m in matches if m.get("date") == today]
    candidates = []
    now_ts = time.time()
    # 历史同指：载入竞彩官方赛果库（354+ 已完赛），免费自建同指命中率
    try:
        _hist_recs = load_history()
    except Exception:
        _hist_recs = []

    for m in matches:
        kickoff = m.get("kickoff") or ""
        try:
            if len(kickoff) >= 10 and kickoff[4] == '-':
                kt = time.mktime(time.strptime(kickoff, "%Y-%m-%d %H:%M"))
            else:
                kt = time.mktime(time.strptime(f"{m.get('date','')} {kickoff}", "%Y-%m-%d %H:%M"))
            if kt < now_ts:
                continue
        except Exception:
            pass

        had = m.get("had") or {}
        hhad = m.get("hhad") or {}
        ttg = m.get("ttg") or {}
        verdict = m.get("verdict") or {}
        hhad_v = m.get("hhadVerdict") or {}
        vtype = verdict.get("t", "n-a")
        stars = verdict.get("stars", 0) or 0
        vdir = verdict.get("dir")
        prob = m.get("hadProb") or {}
        sup = m.get("hadSup") or {}
        goals_exp = m.get("goalsExp") or 2.5
        attack = m.get("attack") or {}
        form = m.get("form") or {}
        score_rec = m.get("scoreRec") or {}
        had_open = m.get("hadOpen") or {}
        mov = m.get("mov") or {}
        hh_prob = m.get("hhProb") or 0
        aa_prob = m.get("aaProb") or 0

        match_options = []

        # ===== 泊松分布概率 =====
        exp_home = score_rec.get("expHome", goals_exp * 0.55)
        exp_away = score_rec.get("expAway", goals_exp * 0.45)
        poisson_probs = poisson_1x2_probs(exp_home, exp_away)

        # ===== 离散度 =====
        disp_h = abs(calc_dispersion(mov)) if mov else 0

        # ===== 稳胆指数（综合8维数据，0-100）：替代赔率筛选，选数据支撑最强的比赛 =====
        # 1. 实力集中度（20分）：概率越集中，实力差距越大
        max_prob = max(prob.get("h") or 0, prob.get("d") or 0, prob.get("a") or 0)
        strength_score = min(20, max(0, (max_prob - 33) * 0.6))
        # 2. 状态优势（15分）：近期状态差异
        form_h = (form.get("home") or {}).get("wr", (form.get("home") or {}).get("winRate", 50)) or 50
        form_a = (form.get("away") or {}).get("wr", (form.get("away") or {}).get("winRate", 50)) or 50
        form_diff = abs(form_h - form_a)
        form_score = min(15, form_diff * 0.3)
        # 3. 规则信号（25分）：bad反指权重加倍
        rule_score = 0
        if vtype == "bad":
            rule_score = 18 + stars * 2  # bad反指历史命中率74%
        elif vtype == "good" and stars >= 3:
            rule_score = 10 + stars * 2
        elif vtype == "cold":
            rule_score = 8
        rule_score = min(25, rule_score)
        # 4. 盘口一致性（15分）：市场概率与泊松吻合度
        poisson_max = max(poisson_probs.get("h") or 0, poisson_probs.get("d") or 0, poisson_probs.get("a") or 0)
        prob_align = 15 - min(15, abs(max_prob - poisson_max) * 1.5)
        # 5. 离散度（10分）：越低越稳定，无数据默认稳定给10分
        if mov:
            disp_h = abs(calc_dispersion(mov))
            disp_score = max(0, 10 - disp_h * 2)
        else:
            disp_score = 10
        # 6. 冷热偏差（10分）：支持率与概率背离
        sup_max = max(sup.get("h") or 0, sup.get("d") or 0, sup.get("a") or 0)
        cold_hot = abs(max_prob - sup_max)
        cold_score = min(10, cold_hot * 0.5)
        # 7. 比分预测信心（5分）
        score_conf = score_rec.get("confidence", 50) or 50
        score_score = min(5, score_conf / 20)
        # 稳胆指数合计（纯7维数据模型，不含赔率——赔率只展示不筛选）
        stable_index = strength_score + form_score + rule_score + prob_align + disp_score + cold_score + score_score
        stable_index = round(min(100, max(0, stable_index)), 1)

        # ========== 选项1：胜平负（核心玩法，历史命中率最高） ==========
        for d, label in [("h", "主胜"), ("d", "平局"), ("a", "客胜")]:
            odds = had.get(d)
            d_prob = prob.get(d, 0) or 0
            if not odds:
                continue
            # 赔率只过滤极端不合理值，不做硬下限——由稳胆指数（含赔率合理性维度）自然筛选
            if odds < 1.05 or odds > 10.0:
                continue
            # 概率下限：原本 25% 会把绝大多数中高赔（赔率>3.3）直接剔除，
            # 导致候选池只剩 1.3-1.5 的蚊子肉。放宽到 10%，各档位再按自身门槛把关。
            if d_prob < 10:
                continue
            # 稳胆指数门槛：只有数据支撑强的比赛才进入候选（32分门槛，适配数据维度少的联赛）
            if stable_index < 32:
                continue

            reasons = []
            tech_count = 0

            # 1. 市场概率
            reasons.append(f"市场概率{d_prob:.0f}%")

            # 2. 泊松验证
            poisson_p = poisson_probs.get(d, 0)
            poisson_diff = abs(poisson_p - d_prob)
            if poisson_diff < 8:
                tech_count += 1
                reasons.append(f"泊松验证{poisson_p:.0f}%")
            elif poisson_diff < 15:
                reasons.append(f"泊松参考{poisson_p:.0f}%")

            # 3. 凯利指数
            kelly = calc_kelly_index(odds, d_prob)
            if kelly > 0:
                tech_count += 1
                reasons.append(f"凯利正价值{kelly:.1f}")
            elif kelly > -8:
                reasons.append(f"凯利{kelly:.1f}")

            # 4. 规则引擎（bad反指是核心信号，历史命中率74%）
            rule_conf = 0
            if vtype == "bad" and vdir != d:
                tech_count += 2  # bad反指权重加倍
                rule_conf = 75 + stars * 3
                reverse_label = {"h": "主胜", "d": "平局", "a": "客胜"}.get(vdir, vdir)
                reasons.append(f"反指{stars}★(推{reverse_label})")
            elif vtype == "good" and vdir == d and stars >= 3:
                tech_count += 1
                rule_conf = 70 + stars * 2
                reasons.append(f"正路{stars}★")
            elif vtype == "cold" and vdir == d:
                tech_count += 1
                rule_conf = 72
                reasons.append("冷门确认")

            # 5. 盘口异动
            open_odds = had_open.get(d)
            if open_odds:
                move = calc_market_move(open_odds, odds)
                if move < -3:
                    tech_count += 1
                    reasons.append(f"降水{abs(move):.1f}%")
                elif move > 3:
                    reasons.append(f"升水{move:.1f}%")

            # 6. 冷热偏差
            d_sup = sup.get(d, 0) or 0
            cold_hot = calc_cold_hot_deviation(d_prob, d_sup)
            if cold_hot > 8:
                tech_count += 1
                reasons.append(f"被低估{cold_hot:.0f}%")

            # 7. 状态优势
            hf = form.get("home") or {}
            af = form.get("away") or {}
            if d == "h" and hf.get("wr", 0) > af.get("wr", 0) + 15:
                tech_count += 1
                reasons.append(f"主队状态{hf.get('wr',0)}%")
            elif d == "a" and af.get("wr", 0) > hf.get("wr", 0) + 15:
                tech_count += 1
                reasons.append(f"客队状态{af.get('wr',0)}%")

            # 8. 离散度
            if disp_h > 0 and disp_h < 8:
                tech_count += 1
                reasons.append(f"共识强(离散{disp_h:.0f}%)")

            # 冷门预警（只标记不扣tech，tech是数据维度计数；风险通过conf上限体现）
            upset = detect_upset_risk(d_prob, odds, kelly)
            if upset == 'high':
                reasons.append("⚠冷门预警")

            # ===== 综合信心度（纯数据驱动，不含赔率——赔率只展示不筛选） =====
            # 基础：市场概率 * 0.85（市场概率通常高估）
            base_conf = d_prob * 0.85
            # 技术加成：每项+2.5%
            tech_bonus = tech_count * 2.5
            # 规则加成：bad反指历史命中率74%，权重0.6
            rule_bonus = max(0, rule_conf - base_conf) * 0.6 if rule_conf else 0
            # 平局惩罚：平局天然难猜
            draw_penalty = 5 if d == "d" else 0

            conf = base_conf + tech_bonus + rule_bonus - draw_penalty
            conf = min(88, max(55, conf))

            # 筛选：纯数据驱动，赔率不参与，三档门槛
            # 1. bad反指(历史命中率74%)：tech≥1，反指本身是强依据
            # 2. good≥4★且方向一致：tech≥1，高星正路也是强依据
            # 3. 无强信号：tech≥2，需多维度交叉验证
            has_bad_reverse = (vtype == "bad" and vdir != d)
            has_good_strong = (vtype == "good" and stars >= 3 and vdir == d)
            # 入池只要求技术验证达标（conf 已被下限截断到 55，再叠加 conf 门槛等于排除中高赔）；
            # 真正的严格程度由各档位的 minConf / minJoint 控制。
            if has_bad_reverse:
                pass_filter = tech_count >= 1
            elif has_good_strong:
                pass_filter = tech_count >= 1
            else:
                pass_filter = tech_count >= 2
            if pass_filter:
                # ===== 历史同指（免费自建，仅用竞彩官方 history.json）=====
                so_hit, so_n = calc_same_odds_signal(_hist_recs, "spf", d, ref_odds=had)
                if so_hit is not None:
                    if so_hit >= 60 and so_n >= 10:  # 同指高命中：价值确认，+1 技术维度
                        tech_count += 1
                    same_odds_hit, same_odds_n = so_hit, so_n
                else:
                    same_odds_hit, same_odds_n = None, so_n
                # ===== 五维雷达（真实输出，已接线）：市场概率/规则/赔率价值/状态/市场共识 =====
                fd_prob = min(20, strength_score + max(0, prob_align - 5) * 0.9)
                fd_rule = min(20, rule_conf * 0.22)
                fd_odds = min(20, max(0, kelly) * 4)
                fd_form = min(20, abs(form_h - form_a) * 0.5)
                fd_mkt = min(20, disp_score * 1.4 + cold_score * 0.6)
                five_dim = round(five_dimension_rating(fd_prob, fd_rule, fd_odds, fd_form, fd_mkt), 1)
                match_options.append({
                    "play": "胜平负", "playKey": "spf", "dir": d, "label": label,
                    "odds": odds, "confidence": round(conf, 1),
                    "score": round(conf * odds, 1), "reasons": reasons,
                    "techCount": tech_count,
                    "pTrue": round(_parlay_true_prob(m, d) * 100, 2),
                    "sameOddsHit": same_odds_hit, "sameOddsN": same_odds_n,
                    "fiveDim": five_dim
                })

        # ========== 选项2：让球胜平负（辅助玩法，最多1场） ==========
        goal = hhad.get("goal", 0)
        hhad_prob = m.get("hhadProb") or {}
        hhad_vtype = hhad_v.get("t", "n-a")
        hhad_stars = hhad_v.get("stars", 0) or 0
        hhad_dir = hhad_v.get("dir")
        core_dir, _ = get_core_direction(m)
        try:
            gv = int(goal)
        except (TypeError, ValueError):
            gv = 0

        for d, label in [("h", "让胜"), ("d", "让平"), ("a", "让负")]:
            odds = hhad.get(d)
            d_prob = hhad_prob.get(d, 0) or 0
            if not odds:
                continue
            # 赔率只过滤极端不合理值，由稳胆指数自然筛选
            if odds < 1.05 or odds > 10.0:
                continue
            # 概率下限：原本 25% 会把绝大多数中高赔（赔率>3.3）直接剔除，
            # 导致候选池只剩 1.3-1.5 的蚊子肉。放宽到 10%，各档位再按自身门槛把关。
            if d_prob < 10:
                continue
            # 稳胆指数门槛
            if stable_index < 50:
                continue
            # 注：让球盘方向与胜平负核心方向不矛盾，让负=主队赢不了2球+

            reasons = [f"让球概率{d_prob:.0f}%", f"盘口{goal}"]
            tech_count = 0

            # 规则引擎
            if hhad_vtype == "good" and hhad_dir == d and hhad_stars >= 3:
                tech_count += 2
                reasons.append(f"让球规则{hhad_stars}★")
            elif hhad_vtype == "bad" and hhad_dir != d:
                tech_count += 1
                reasons.append("让球反指")

            # 与胜平负bad反指联动：bad反指平局时，主队让球选让负=高信心
            bad_reverse_bonus = 0
            if vtype == "bad" and vdir == "d":  # 胜平负反指平局
                if gv < 0 and d == "a":  # 主队让球，选让负（主队赢不了2球+）
                    tech_count += 3
                    bad_reverse_bonus = 18
                    reasons.append("反指平局→让负(高信心)")
                elif gv > 0 and d == "h":  # 主队受让，选让胜（客队赢不了2球+）
                    tech_count += 3
                    bad_reverse_bonus = 18
                    reasons.append("反指平局→让胜(高信心)")
            elif vtype == "bad" and vdir == "h":  # 胜平负反指主胜
                if gv < 0 and d == "h":  # 主队让球，选让胜（主队赢2球+）
                    tech_count += 2
                    bad_reverse_bonus = 12
                    reasons.append("反指主胜→让胜")
                elif gv > 0 and d == "h":  # 主队受让，选让胜（主队不败）
                    tech_count += 2
                    bad_reverse_bonus = 12
                    reasons.append("反指主胜→让胜(受让)")
            elif vtype == "bad" and vdir == "a":  # 胜平负反指客胜
                if gv > 0 and d == "a":  # 主队受让，选让负（客队赢2球+）
                    tech_count += 2
                    bad_reverse_bonus = 12
                    reasons.append("反指客胜→让负")
                elif gv < 0 and d == "a":  # 主队让球，选让负（客队不败）
                    tech_count += 2
                    bad_reverse_bonus = 12
                    reasons.append("反指客胜→让负(让球)")

            # 与胜平负核心方向一致
            if core_dir and ((core_dir == "h" and d == "h") or (core_dir == "a" and d == "a")):
                tech_count += 1
                reasons.append("与核心方向一致")

            # 让球盘信心度（纯数据驱动，不含赔率）
            base_conf = d_prob * 0.95  # 让球盘概率本身较准
            tech_bonus = tech_count * 3
            conf = base_conf + tech_bonus + bad_reverse_bonus
            conf = min(88, max(55, conf))

            # 让球筛选：三档门槛
            has_bad = bad_reverse_bonus > 0
            has_good_strong = (vtype == "good" and stars >= 3)
            if has_bad:
                pass_filter = tech_count >= 1 and conf >= 55
            elif has_good_strong:
                pass_filter = tech_count >= 1 and conf >= 55
            else:
                pass_filter = tech_count >= 2 and conf >= 58
            if pass_filter:
                # ===== 历史同指（按盘口线 + 同方向匹配，免费自建）=====
                so_hit, so_n = calc_same_odds_signal(_hist_recs, "hhad", d, goal=str(goal))
                if so_hit is not None:
                    if so_hit >= 60 and so_n >= 10:
                        tech_count += 1
                    same_odds_hit, same_odds_n = so_hit, so_n
                else:
                    same_odds_hit, same_odds_n = None, so_n
                # ===== 五维雷达（让球盘：概率/规则/盘口价值/状态/市场）=====
                fd_prob = min(20, d_prob * 0.4)
                fd_rule = min(20, tech_count * 4)
                fd_odds = min(20, max(0, conf - 55) * 0.7)
                fd_form = min(20, form_score * 1.33)
                fd_mkt = min(20, prob_align * 1.3)
                five_dim = round(five_dimension_rating(fd_prob, fd_rule, fd_odds, fd_form, fd_mkt), 1)
                match_options.append({
                    "play": f"让球{goal}", "playKey": "hhad", "dir": d, "label": label,
                    "odds": odds, "confidence": round(conf, 1),
                    "score": round(conf * odds, 1), "reasons": reasons,
                    "techCount": tech_count,
                    "sameOddsHit": same_odds_hit, "sameOddsN": same_odds_n,
                    "fiveDim": five_dim
                })

        # ========== 选项3：总进球（辅助玩法，最多1场） ==========
        if ttg and goals_exp:
            import math
            lam = float(goals_exp)
            ah = attack.get("home") or {}
            aa = attack.get("away") or {}
            stability = min(1.0, ((ah.get("n", 0) or 0) + (aa.get("n", 0) or 0)) / 20.0)
            for k in range(8):
                odds = ttg.get(str(k))
                if not odds or odds < 1.8 or odds > 3.5:
                    continue
                base_prob = math.exp(-lam) * (lam ** k) / math.factorial(k) * 100
                if base_prob < 20:
                    continue
                reasons = [f"预期{goals_exp}球", f"概率{base_prob:.0f}%"]
                tech_count = 0
                if abs(k - lam) < 1.0:
                    tech_count += 2
                    reasons.append("最可能区间")
                if stability > 0.7:
                    tech_count += 1
                    reasons.append("数据充足")
                conf = 55 + base_prob * 0.6 * stability + tech_count * 2
                conf = min(85, max(58, conf))
                if tech_count >= 2 and conf >= 70:
                    label = f"{k}球" if k < 7 else "7+球"
                    # ===== 历史同指（总进球代理：同预期进球区间→实际总进球命中）=====
                    so_hit, so_n = None, 0
                    _gh = 0
                    for r in _hist_recs:
                        res = r.get("result")
                        ge = r.get("goalsExp") or 0
                        if not isinstance(res, dict) or res.get("home") is None or res.get("cancel"):
                            continue
                        if abs(ge - k) < 0.6:
                            so_n += 1
                            if (res.get("home", 0) + res.get("away", 0)) == k:
                                _gh += 1
                    if so_n >= 5:
                        so_hit = round(_gh / so_n * 100, 1)
                        if so_hit >= 55 and so_n >= 10:
                            tech_count += 1
                    # ===== 五维雷达（总进球：概率/规则/赔率价值/状态/市场）=====
                    fd_prob = min(20, base_prob * 0.35)
                    fd_rule = min(20, tech_count * 4)
                    fd_odds = min(20, max(0, conf - 55) * 0.7)
                    fd_form = min(20, stability * 20)
                    fd_mkt = min(20, abs(k - lam) < 1.0 and 20 or 8)
                    five_dim = round(five_dimension_rating(fd_prob, fd_rule, fd_odds, fd_form, fd_mkt), 1)
                    match_options.append({
                        "play": "总进球", "playKey": "ttg", "dir": str(k), "label": label,
                        "odds": odds, "confidence": round(conf, 1),
                        "score": round(conf * odds, 1), "reasons": reasons,
                        "techCount": tech_count,
                        "sameOddsHit": so_hit, "sameOddsN": so_n,
                        "fiveDim": five_dim
                    })

        # 每场保留最多3个最优选项。
        # 原实现「每场只留信心最高的1个」，而排序键与赔率无关，导致中高赔被系统性挤掉，
        # 候选池只剩 1.25-1.45 的蚊子肉。放宽到3个后，各档位才有挑选空间。
        # 组合时按 num 去重，同一场不会被同时选中，安全。
        if match_options:
            match_options.sort(key=lambda x: x["confidence"] + stable_index * 0.3, reverse=True)
            for opt in match_options[:3]:
                candidates.append({
                    "num": m.get("num"), "league": m.get("league"),
                    "home": m.get("home"), "away": m.get("away"),
                    "kickoff": kickoff, "play": opt["play"], "playKey": opt["playKey"],
                    "dir": opt["dir"], "label": opt["label"],
                    "odds": opt["odds"], "confidence": opt["confidence"],
                    "stableIndex": stable_index,
                    "score": opt["score"], "reasons": opt["reasons"],
                    "techCount": opt.get("techCount", 0),
                    "pTrue": opt.get("pTrue"),
                    "sameOddsHit": opt.get("sameOddsHit"),
                    "sameOddsN": opt.get("sameOddsN"),
                    "fiveDim": opt.get("fiveDim")
                })

    candidates.sort(key=lambda x: x["confidence"], reverse=True)

    # ===== 串关档位：拒绝蚊子肉，按风险分三档，每档在自身赔率区间内选「期望值 EV」最优组合 =====
    # (key, 档位, 场数, 单场下限, 总赔下限, 总赔上限, 单腿信心下限, 联合概率下限, EV下限, 说明)
    # 联合概率下限按「返奖率^n / 典型总赔」反推设定，避免与总赔区间自相矛盾
    # EV下限：稳胆档只取≈盈亏平衡以上（不再推明显负期望"稳胆"）；均衡档放宽容差；搏击档纯娱乐不卡
    TIERS = [
        ("parlay2", "稳健", (2,), 1.45, 2.20, 4.50, 66.0, 0.20, -0.08,
         "2串1 · 主打命中，单场不低于1.45（拒绝蚊子肉），总赔2.2-4.5倍；只取高信心组合里 EV≥-8% 的最不亏方案，EV<0 仍标不推荐"),
        ("parlay3", "均衡", (3,), 1.50, 4.50, 10.0, 55.0, 0.08, -0.15,
         "3串1 · 命中率与赔率折中，总赔4.5-10倍；只取 EV≥-15% 的方案，EV<0 仍标不推荐"),
        ("parlayBoost", "搏击", (3, 4), 1.70, 10.0, 40.0, 45.0, 0.03, -1.0,
         "3-4串1 · 总赔10倍以上，命中率明显下降，方差极大，纯娱乐不卡EV"),
    ]
    # 竞彩单场返奖率：对当日全部场次实测 1/(1/h+1/d+1/a) ≈ 88.6%，即单场抽水约 11.4%。
    # （此前按 70% 估算是错的，会把串关抽水夸大一倍）
    PAYBACK = 0.886

    # alpha 置信度折扣：各细分样本只有 7~71 场、95% 置信区间全部跨越 0，
    # 直接采信 alpha 会高估。按 0.6 折收缩，偏向市场共识（保守）。
    ALPHA_SHRINK = 0.6

    def _leg_prob(c):
        """单个腿的真实概率估计（0-1）"""
        o = c.get("odds") or 0
        p_market = (PAYBACK / o) if o > 1 else 0.0   # 市场共识概率（去抽水）
        pt = c.get("pTrue")
        if pt:
            # 胜平负：p_cal = 市场共识 + alpha，对 alpha 部分打折后使用
            p = p_market + (pt / 100.0 - p_market) * ALPHA_SHRINK
            return min(max(p, 0.01), 0.95)
        # 让球/总进球没有校准概率时直接用市场共识。
        # 不能拿 confidence 当概率用——confidence 是信心指标且被截断在 55 分以上，
        # 会把 60% 的事件估算成 85%，导致 EV 严重虚高。
        if p_market > 0:
            return min(max(p_market, 0.01), 0.95)
        return min(max((c.get("confidence") or 0) / 100.0, 0.01), 0.95)

    def build_tier(tier):
        key, name, ns, min_odds, min_total, max_total, min_conf, min_joint, min_ev, desc = tier
        from itertools import combinations
        best = None
        best_score = None
        for n in ns:
            if len(candidates) < n:
                continue
            max_hhad = 2 if n >= 3 else 1
            for combo in combinations(candidates, n):
                nums = [c["num"] for c in combo]
                if len(set(nums)) < n:
                    continue
                if sum(1 for c in combo if c["playKey"] == "hhad") > max_hhad:
                    continue
                if sum(1 for c in combo if c["playKey"] == "ttg") > 1:
                    continue
                # 拒绝蚊子肉：单场赔率必须达到档位下限
                if any(c["odds"] < min_odds for c in combo):
                    continue
                # 档位信心门槛（单腿）
                if any((c.get("confidence") or 0) < min_conf for c in combo):
                    continue
                total_odds = 1.0
                joint_p = 1.0
                for c in combo:
                    total_odds *= c["odds"]
                    joint_p *= _leg_prob(c)
                if total_odds < min_total or total_odds > max_total:
                    continue
                # 联合概率下限：避免选出数学上几乎不可能中的组合
                if joint_p < min_joint:
                    continue
                # 期望值：EV = 联合概率 × 总赔率 − 1
                ev = joint_p * total_odds - 1.0
                # EV 门槛：稳胆/均衡档只取达到 EV下限的组合，杜绝"明显负期望稳胆"
                if ev < min_ev:
                    continue
                tech_sum = sum(c.get("techCount", 0) for c in combo)
                stable_sum = sum(c.get("stableIndex", 0) for c in combo)
                five_dim_sum = sum(c.get("fiveDim") or 0 for c in combo)
                # 同指陷阱惩罚：某腿历史同指命中率<45%且样本充足 → 该组合减分
                so_penalty = 0.0
                for c in combo:
                    so = c.get("sameOddsHit")
                    if so is not None and c.get("sameOddsN", 0) >= 10 and so < 45:
                        so_penalty += 0.0006 * (45 - so)
                # EV 主导（量级 0.1~1）；技术/稳胆/五维只在 EV 接近时做细微区分
                score = ev + 0.0015 * tech_sum + 0.0003 * stable_sum + 0.0004 * five_dim_sum - so_penalty
                if best_score is None or score > best_score:
                    best_score = score
                    best = (list(combo), total_odds, joint_p, ev, n)
        if not best:
            return None
        selected, total_odds, joint_p, ev, n = best
        avg_conf = sum(s["confidence"] for s in selected) / len(selected)
        play_types = list(set(s["play"] for s in selected))
        # 凯利仓位：四分之一凯利，上限 10%
        kelly = ev / (total_odds - 1.0) if total_odds > 1 else 0.0
        stake = min(max(kelly * 0.25 * 100, 0.0), 10.0)
        rake = (1.0 - PAYBACK ** n) * 100
        if ev >= 0.05:
            ev_note = "正期望，模型认为值得下手"
        elif ev >= -0.05:
            ev_note = "边际，接近盈亏平衡"
        elif ev >= -0.20:
            ev_note = "轻度负期望，靠命中率撑"
        else:
            ev_note = "明显负期望，仅作娱乐"
        return {
            "type": f"{n}串1", "tier": name, "tierDesc": desc,
            "legs": selected,
            "totalOdds": round(total_odds, 2),
            "avgConfidence": round(avg_conf, 1),
            "minOdds": min(s["odds"] for s in selected),
            "maxOdds": max(s["odds"] for s in selected),
            "playTypes": play_types,
            "isMixed": len(play_types) > 1,
            "jointProb": round(joint_p * 100, 1),
            "ev": round(ev * 100, 1),
            "suggestStake": round(stake, 1),
            "rake": round(rake, 1),
            "evNote": ev_note,
            # 是否值得作为「推荐」展示：EV<0 的数学上长期必亏，不再以推荐组合形式展示
            "recommended": ev >= 0,
        }

    built = {t[0]: build_tier(t) for t in TIERS}
    parlay2 = built.get("parlay2")
    parlay3 = built.get("parlay3")
    parlay_boost = built.get("parlayBoost")

    result = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "candidateCount": len(candidates),
        "strategy": "EV 驱动分层串关：拒绝蚊子肉（单场赔率下限 1.45），稳健/均衡/搏击三档各在自身赔率区间内取「期望值 EV」最优组合；概率取自智能引擎校准值；并叠加历史同指命中率（免费自建，竞彩官方赛果库）与五维雷达评分做交叉验证，稳胆档强制 EV≥-3% 才推荐",
        "parlay2": parlay2,
        "parlay3": parlay3,
        "parlayBoost": parlay_boost,
        "legNote": "串关抽水按场数放大：单场抽水11.4%，2串1约21.6%、3串1约30.6%、4串1约38.5%——串得越多，抽水越狠"
    }

    def _fmt(p):
        if not p:
            return "N/A"
        return f"{p['totalOdds']}倍(命中{p['jointProb']}% EV{p['ev']:+.1f}% 建议{p['suggestStake']}%)"

    print(f"  分层串关：候选{len(candidates)}个 | 稳健 {_fmt(parlay2)} | 均衡 {_fmt(parlay3)} | 搏击 {_fmt(parlay_boost)}", flush=True)
    return result


def check_parlay_history():
    """回查串关历史战绩：从parlay_history.json读取历史推荐，从history.json获取赛果，计算命中。
    修复：从result.score获取比分（而非score字段），正确计算让球胜平负，结果写回文件。
    返回 {total, hit, miss, pending, hitRate, recentDays, records}"""
    import os
    parlay_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parlay_history.json")
    history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

    if not os.path.exists(parlay_file):
        return {"total": 0, "hit": 0, "miss": 0, "pending": 0, "hitRate": 0, "records": []}

    try:
        parlay_data = json.load(open(parlay_file, encoding="utf-8"))
    except Exception:
        return {"total": 0, "hit": 0, "miss": 0, "pending": 0, "hitRate": 0, "records": []}

    try:
        history_data = json.load(open(history_file, encoding="utf-8"))
        history_records = history_data.get("records", [])
    except Exception:
        history_records = []

    # 建立(date,num)->赛果的映射（必须按日期匹配，避免今天未开赛的比赛匹配到上周同num的赛果）
    score_map = {}
    goal_map = {}
    for r in history_records:
        num = r.get("num")
        date = r.get("date", "")
        result = r.get("result") or {}
        score = result.get("score")
        if num and score and isinstance(score, str) and ":" in score:
            try:
                parts = score.split(":")
                hs, as_ = int(parts[0]), int(parts[1])
                key = f"{date}|{num}"
                score_map[key] = {"home": hs, "away": as_, "score": score}
                # 让球数：goal<0主队让球，>0主队受让
                goal_raw = r.get("goal", 0)
                try:
                    goal_map[key] = int(goal_raw) if goal_raw else 0
                except (ValueError, TypeError):
                    goal_map[key] = 0
            except Exception:
                pass

    records = parlay_data.get("records", [])
    total = hit = miss = pending = 0
    modified = False

    for rec in records:
        legs = rec.get("legs", [])
        if not legs:
            continue
        total += 1
        all_hit = True
        has_pending = False
        rec_date = rec.get("date", "")
        for leg in legs:
            num = leg.get("num")
            play = leg.get("playKey", "spf")
            direction = leg.get("dir")
            key = f"{rec_date}|{num}"
            sm = score_map.get(key)
            if not sm:
                has_pending = True
                # 清除可能残留的赛果信息
                if "actualScore" in leg:
                    del leg["actualScore"]
                if "legHit" in leg:
                    del leg["legHit"]
                if "adjScore" in leg:
                    del leg["adjScore"]
                continue
            hs, as_ = sm["home"], sm["away"]
            leg_hit = False
            adj_info = ""
            # 判断命中
            if play == "spf":
                # 胜平负
                if direction == "h" and hs > as_:
                    leg_hit = True
                elif direction == "d" and hs == as_:
                    leg_hit = True
                elif direction == "a" and hs < as_:
                    leg_hit = True
            elif play == "ttg":
                # 总进球
                total_goals = hs + as_
                try:
                    target = int(direction)
                    if (target == 7 and total_goals >= 7) or total_goals == target:
                        leg_hit = True
                except Exception:
                    pass
            elif play == "hhad":
                # 让球胜平负：goal<0主队让球，>0主队受让
                # adj_home = 主队进球 + goal；>客=让胜(h)，=让平(d)，<让负(a)
                goal = goal_map.get(key, 0)
                adj_home = hs + goal
                adj_info = f" (让{goal:+d}后{adj_home}:{as_})"
                if direction == "h" and adj_home > as_:
                    leg_hit = True
                elif direction == "d" and adj_home == as_:
                    leg_hit = True
                elif direction == "a" and adj_home < as_:
                    leg_hit = True
            if not leg_hit:
                all_hit = False
            # 把赛果和单场命中状态附加到leg，方便页面展示追溯
            leg["actualScore"] = f"{hs}:{as_}"
            leg["legHit"] = leg_hit
            leg["hit"] = "hit" if leg_hit else "miss"
            if adj_info:
                leg["adjScore"] = adj_info
        if has_pending:
            pending += 1
            new_result = "pending"
        elif all_hit:
            hit += 1
            new_result = "hit"
        else:
            miss += 1
            new_result = "miss"
        # 写回结果到parlay_history.json
        if rec.get("result") != new_result:
            rec["result"] = new_result
            rec["checkedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            modified = True
        elif not has_pending:
            # 即使result没变，也确保leg上的赛果和命中信息已写回文件
            # （leg字段在上方循环中已重新计算，需持久化）
            modified = True

    # 写回文件
    if modified:
        try:
            parlay_data["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            json.dump(parlay_data, open(parlay_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  串关历史回查：已写回{hit+miss}条结果（命中{hit}/错误{miss}/待开奖{pending}）", flush=True)
        except Exception as e:
            print(f"  串关历史写回失败: {e}", flush=True)

    hit_rate = round(hit / (hit + miss) * 100, 1) if (hit + miss) > 0 else 0

    # 最近7天（基于写回后的result）
    recent = [r for r in records if r.get("date", "") >= time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))]
    recent_hit = sum(1 for r in recent if r.get("result") == "hit")
    recent_miss = sum(1 for r in recent if r.get("result") == "miss")
    recent_rate = round(recent_hit / (recent_hit + recent_miss) * 100, 1) if (recent_hit + recent_miss) > 0 else 0

    return {
        "total": total, "hit": hit, "miss": miss, "pending": pending,
        "hitRate": hit_rate, "recent7Rate": recent_rate,
        "records": records[-20:]  # 最近20条
    }


def load_locked_parlay():
    """从parlay_history.json加载今天已锁定的串关推荐，避免每批次变动。
    返回 (safe_parlay, high_odds_parlay) 或 (None, None) 表示今天尚未锁定。"""
    import os
    parlay_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parlay_history.json")
    today = time.strftime("%Y-%m-%d")
    if not os.path.exists(parlay_file):
        return None, None
    try:
        data = json.load(open(parlay_file, encoding="utf-8"))
    except Exception:
        return None, None
    records = data.get("records", [])
    today_records = [r for r in records if r.get("date") == today]
    if not today_records:
        return None, None

    # 若串关涉及的比赛尚未开赛，允许重算（让赔率/数据变化反映到串关里）；
    # 一旦有任何一场已开赛，即锁定当天方案，避免临场追涨杀跌。
    try:
        now_ts = time.time()
        kicks = []
        for r in today_records:
            for leg in (r.get("legs") or []):
                k = leg.get("kickoff") or ""
                if len(k) >= 16:
                    kicks.append(time.mktime(time.strptime(k[:16], "%Y-%m-%d %H:%M")))
        if kicks and min(kicks) > now_ts:
            return None, None
    except Exception:
        pass

    def rebuild(records_of_type, ptype):
        rec = next((r for r in records_of_type if r.get("type") == ptype), None)
        if not rec:
            return None
        return {
            "type": ptype.replace("稳胆", "").replace("高倍", ""),
            "legs": rec.get("legs", []),
            "totalOdds": rec.get("totalOdds", 0),
            "avgConfidence": rec.get("avgConfidence", 0),
            "locked": True,
            "savedAt": rec.get("savedAt", "")
        }

    safe_p2 = rebuild(today_records, "稳胆2串1")
    safe_p3 = rebuild(today_records, "稳胆3串1")

    safe_parlay = {
        "generatedAt": today_records[0].get("savedAt", ""),
        "candidateCount": 0,
        "strategy": "中倍稳串（今日已锁定，不再变动）",
        "parlay2": safe_p2, "parlay3": safe_p3, "locked": True
    } if (safe_p2 or safe_p3) else None

    # 高倍串关已按用户要求永久移除，不再返回
    high_parlay = None

    return safe_parlay, high_parlay


def save_parlay_history(safe_parlay, high_parlay):
    """保存当天串关推荐到parlay_history.json，用于后续回查。"""
    import os
    parlay_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parlay_history.json")
    today = time.strftime("%Y-%m-%d")

    try:
        if os.path.exists(parlay_file):
            data = json.load(open(parlay_file, encoding="utf-8"))
        else:
            data = {"updatedAt": "", "records": []}
    except Exception:
        data = {"updatedAt": "", "records": []}

    records = data.get("records", [])
    # 锁定机制：当天已保存的「同一组 legs（按 场次+玩法+方向）」不再覆盖，避免重复。
    # 用 legs 签名去重（不依赖展示用 type 文案），命名随档位变化也不产生重复。
    def _leg_sig(legs):
        return tuple(sorted((l.get("num"), l.get("playKey"), l.get("dir")) for l in (legs or [])))
    today_sigs = {_leg_sig(r.get("legs", [])) for r in records if r.get("date") == today}

    # 保存各档串关（稳健/均衡/搏击）：即便 EV<0 标「不推荐」也照常落盘，
    # 方便赛后对照「不推荐」是否真的没中，反向验证 EV 模型是否成立。
    for key in ("parlay2", "parlay3", "parlayBoost"):
        p = (safe_parlay or {}).get(key)
        if not p or not p.get("legs"):
            continue
        sig = _leg_sig(p["legs"])
        if sig in today_sigs:
            continue
        today_sigs.add(sig)
        ptype = f'{p.get("tier", "")}{p.get("type", "")}'
        records.append({
            "date": today, "type": ptype,
            "totalOdds": p["totalOdds"], "avgConfidence": p.get("avgConfidence", 0),
            "legs": p["legs"], "recommended": bool(p.get("recommended", False)),
            "ev": p.get("ev"), "result": "pending",
            "savedAt": time.strftime("%Y-%m-%d %H:%M:%S")
        })

    data["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["records"] = records
    json.dump(data, open(parlay_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  串关历史已保存：{len(records)}条记录", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # 顶层兜底：任何未预期异常都打印清晰错误并退出 1，
        # 避免抛出原始 traceback；由 run_daily.sh 决定是否判失败。
        import traceback
        traceback.print_exc()
        print(f"\n[致命] fetch_daily.py 执行异常: {e}", flush=True)
        sys.exit(1)
    # 抓取高手推荐（92玩球易红单）：仅16:00-21:00每小时抓取
    current_hour = time.localtime().tm_hour
    if 16 <= current_hour <= 21:
        print("\n[7/7] 抓取高手推荐...", flush=True)
        try:
            from fetch_expert import main as expert_main
            expert_main()
        except Exception as e:
            print(f"  高手推荐抓取失败: {e}", flush=True)
    else:
        print(f"\n[7/7] 跳过高手推荐抓取（当前{current_hour}:00，仅16:00-21:00抓取）", flush=True)
