#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BSD 多博彩公司实时赔率模块（Pinnacle / Bet365 / 1xBet 等）

【接管补充说明】
原源码包缺失本模块，导致 fetch_daily.py 第 5.10 步报 "No module named 'fetch_odds_bsd'"。
本实现为「可插拔适配器」：
  - 未配置 API Key 时静默降级（返回空结果），页面"国际权威赔率"区块自动隐藏；
  - 配置后自动启用，并把上一轮赔率留作 prev_odds，用于降水/升水箭头与 movements 统计。

数据源：The Odds API（https://the-odds-api.com，免费层 500 次/月）
  配置方式（二选一）：
    1) 环境变量：export ODDS_API_KEY="你的Key"
    2) 本目录 bsd_config.json：{"odds_api_key": "你的Key", "enabled": true}

对外接口：
    fetch_and_match(matches) -> dict
        {
          "matches": [{"num":"周五001", "bookmakers":{...}, "movements":[...]},
          "total_movements": int
        }

数据契约（供 template.html 渲染）：
    bookmakers = {
      "Pinnacle": {"HOME": {"odds":1.85,"prev_odds":1.90},
                   "DRAW": {...}, "AWAY": {...}},
      "Bet365": {...}
    }
    movements = [{"bookmaker":"Pinnacle","outcome":"HOME","from":1.90,"to":1.85,
                  "direction":"drop"}]
"""
import os
import re
import json
import time
import urllib.request
import urllib.error
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "odds_bsd_cache.json")
CONFIG_PATH = os.path.join(HERE, "bsd_config.json")

API_BASE = "https://api.theoddsapi.com/v4"

# 竞彩联赛名 -> The Odds API sport key
SPORT_MAP = {
    "英超": "soccer_epl",
    "英冠": "soccer_england_efl_champ",
    "西甲": "soccer_spain_la_liga",
    "西乙": "soccer_spain_segunda_division",
    "德甲": "soccer_germany_bundesliga",
    "德乙": "soccer_germany_bundesliga_2",
    "意甲": "soccer_italy_serie_a",
    "意乙": "soccer_italy_serie_b",
    "法甲": "soccer_france_ligue_one",
    "法乙": "soccer_france_ligue_two",
    "荷甲": "soccer_netherlands_eredivisie",
    "荷乙": "soccer_netherlands_eerste_divisie",
    "葡超": "soccer_portugal_primeira_liga",
    "苏超": "soccer_scotland_premiership",
    "比甲": "soccer_belgium_first_div",
    "挪超": "soccer_norway_eliteserien",
    "瑞典超": "soccer_sweden_allsvenskan",
    "丹超": "soccer_denmark_superliga",
    "俄超": "soccer_russia_premier_league",
    "乌超": "soccer_ukraine_premier_league",
    "土超": "soccer_turkey_super_league",
    "巴甲": "soccer_brazil_campeonato",
    "阿甲": "soccer_argentina_primera_division",
    "日职": "soccer_japan_j_league",
    "日乙": "soccer_japan_j2_league",
    "韩职": "soccer_korea_kleague",
    "澳超": "soccer_australia_aleague",
    "美职": "soccer_usa_mls",
    "中超": "soccer_china_super_league",
}

# 常见球队中文名 -> 英文关键字（用于跨源匹配，未收录时由模糊匹配兜底）
TEAM_ALIAS = {
    "曼联": "Manchester United", "曼彻斯特联": "Manchester United",
    "利物浦": "Liverpool", "阿森纳": "Arsenal", "切尔西": "Chelsea",
    "曼城": "Manchester City", "曼彻斯特城": "Manchester City",
    "热刺": "Tottenham", "托特纳姆热刺": "Tottenham",
    "纽卡斯尔联": "Newcastle", "西汉姆联": "West Ham",
    "多特蒙德": "Dortmund", "拜仁慕尼黑": "Bayern Munich", "拜仁": "Bayern Munich",
    "云达不来梅": "Werder Bremen", "勒沃库森": "Leverkusen",
    "皇家马德里": "Real Madrid", "皇马": "Real Madrid",
    "巴塞罗那": "Barcelona", "巴萨": "Barcelona",
    "马德里竞技": "Atletico Madrid", "马竞": "Atletico Madrid",
    "塞维利亚": "Sevilla", "瓦伦西亚": "Valencia",
    "尤文图斯": "Juventus", "国际米兰": "Inter Milan", "国米": "Inter Milan",
    "AC米兰": "AC Milan", "米兰": "AC Milan", "那不勒斯": "Napoli",
    "罗马": "Roma", "拉齐奥": "Lazio", "亚特兰大": "Atalanta",
    "巴黎圣日耳曼": "Paris Saint Germain", "巴黎圣日尔曼": "Paris Saint Germain",
    "朗斯": "Lens", "里昂": "Lyon", "马赛": "Marseille", "摩纳哥": "Monaco",
    "里尔": "Lille", "雷恩": "Rennes", "尼斯": "Nice",
    "埃因霍温": "PSV", "PSV埃因霍温": "PSV", "阿贾克斯": "Ajax",
    "费耶诺德": "Feyenoord", "阿尔克马尔": "AZ Alkmaar", "海伦芬": "Heerenveen",
    "赫拉克勒斯": "Heracles", "瓦尔韦克": "Waalwijk",
    "本菲卡": "Benfica", "波尔图": "Porto", "里斯本竞技": "Sporting CP",
    "布拉加": "Braga",
    "弗拉门戈": "Flamengo", "帕尔梅拉斯": "Palmeiras",
    "仁川联": "Incheon", "浦项制铁": "Pohang",
    "柏太阳神": "Kashiwa", "神户胜利船": "Vissel Kobe",
    "海登海姆": "Heidenheim", "凯泽斯劳滕": "Kaiserslautern",
    "哥德堡": "Gothenburg", "IFK哥德堡": "Gothenburg",
    "布兰": "Brann", "维京": "Viking",
    "马拉加": "Malaga", "西班牙人": "Espanyol",
    "女王公园巡游者": "Queens Park Rangers",
}

# 优先展示的博彩公司
PREFERRED_BOOKS = ["Pinnacle", "Bet365", "1xBet", "William Hill", "Betway", "Unibet", "bet365"]


def _load_config():
    cfg = {"odds_api_key": "", "enabled": True}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    if not cfg.get("odds_api_key"):
        cfg["odds_api_key"] = os.environ.get("ODDS_API_KEY", "")
    return cfg


def _norm(s):
    if not s:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    for suf in ("fc", "cf", "sc", "ac", "united", "utd", "city", "足球队"):
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[: -len(suf)]
    return s


def _similar(a, b):
    """中文名与英文名的跨语言匹配：先查别名表，再退化字符集重合"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    alias = TEAM_ALIAS.get(str(a).strip())
    if alias and _norm(alias) == nb:
        return True
    if alias and (_norm(alias) in nb or nb in _norm(alias)):
        return True
    if na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    return len(sa & sb) / max(len(sa | sb), 1) >= 0.55


def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(c):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _api_get(path, api_key, timeout=20):
    if not api_key:
        return False, "no_api_key"
    req = urllib.request.Request(API_BASE + path, headers={"User-Agent": "jc-football/1.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        return True, json.loads(raw)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:120]


def _pick_bookmakers(books):
    """挑选 Pinnacle 等主流公司，构造 {公司: {OUTCOME: {odds, prev_odds}}}"""
    out = {}
    for b in books or []:
        title = b.get("title") or b.get("key") or ""
        markets = b.get("markets") or []
        h2h = next((m for m in markets if m.get("key") == "h2h"), None)
        if not h2h:
            continue
        outcomes = {}
        for o in h2h.get("outcomes", []):
            name = (o.get("name") or "").strip()
            side = None
            # 依据 h2h 顺序：第一项为主队
            if not outcomes:
                side, first = "HOME", name
            elif name == first:
                side = "HOME"
            else:
                side = "AWAY"
            outcomes[side] = {"odds": o.get("price")}
        if len(outcomes) < 2:
            continue
        out[title] = outcomes
    # 只保留主流公司，限制列数避免表格过宽
    picked = {}
    for want in PREFERRED_BOOKS:
        for k in out:
            if k.lower() == want.lower() and k not in picked:
                picked[k] = out[k]
                break
    for k, v in out.items():
        if len(picked) >= 4:
            break
        if k not in picked:
            picked[k] = v
    return picked


def fetch_and_match(matches):
    """主入口：返回 {"matches":[...], "total_movements":N}；无 Key 或失败时返回空结构"""
    cfg = _load_config()
    empty = {"matches": [], "total_movements": 0}
    if not cfg.get("enabled"):
        return empty
    api_key = cfg.get("odds_api_key", "")
    if not api_key:
        return empty

    prev_cache = _load_cache()
    result_matches = []
    total_movements = 0
    new_cache = {}

    sports = {}
    for m in matches:
        sk = SPORT_MAP.get(m.get("league", ""))
        if sk:
            sports.setdefault(sk, []).append(m)

    for sk, ms in sports.items():
        ok, data = _api_get(
            f"/sports/{sk}/odds/?apiKey={api_key}&regions=eu,uk&markets=h2h&oddsFormat=decimal",
            api_key,
        )
        if not ok or not isinstance(data, list):
            continue
        for ev in data:
            eh = (ev.get("home_team") or "")
            ea = (ev.get("away_team") or "")
            target = None
            for m in ms:
                if _similar(m.get("home", ""), eh) and _similar(m.get("away", ""), ea):
                    target = m
                    break
            if not target:
                continue
            books = _pick_bookmakers(ev.get("bookmakers"))
            if not books:
                continue
            # 与上一轮比对，生成 movements + prev_odds
            movements = []
            old = (prev_cache.get(target.get("num", "")) or {}).get("books", {})
            for bk, outs in books.items():
                for side, val in outs.items():
                    pv = ((old.get(bk) or {}).get(side) or {}).get("odds")
                    if pv is not None and isinstance(val.get("odds"), (int, float)):
                        val["prev_odds"] = pv
                        if val["odds"] < pv - 0.01:
                            movements.append({"bookmaker": bk, "outcome": side,
                                              "from": pv, "to": val["odds"], "direction": "drop"})
                        elif val["odds"] > pv + 0.01:
                            movements.append({"bookmaker": bk, "outcome": side,
                                              "from": pv, "to": val["odds"], "direction": "rise"})
                    elif pv is not None:
                        val["prev_odds"] = pv
            total_movements += len(movements)
            rec = {"num": target.get("num", ""), "bookmakers": books, "movements": movements}
            result_matches.append(rec)
            new_cache[target.get("num", "")] = {
                "books": books,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        time.sleep(0.3)

    _save_cache(new_cache)
    return {"matches": result_matches, "total_movements": total_movements}


if __name__ == "__main__":
    cfg = _load_config()
    if not cfg.get("odds_api_key"):
        print("未配置 API Key：请设置环境变量 ODDS_API_KEY 或创建 bsd_config.json")
        print("本模块将静默降级，主流程不受影响。")
    else:
        print("API Key 已配置，模块已启用。")
