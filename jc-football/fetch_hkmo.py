#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""香港马会 + 澳门彩票 赔率抓取
数据源：500彩票网欧赔API（公开JSON接口）
  - 香港马会 cid=3
  - 澳门彩票 cid=4
API格式：https://odds.500.com/fenxi1/json/ouzhi.php?fid={北单fid}&cid={公司id}
返回：[[主胜,平局,客胜,返还率,时间,主胜变,平局变,客胜变], ...] 最新在前，初盘在最后
产出：hkmo_cache.json（按UTC日期缓存，含每场比赛的港澳初盘/当前赔率/降水标记）
"""
import os, re, json, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"
HK_CID = 3  # 香港马会
MO_CID = 4  # 澳门彩票

# 亚盘盘口数值映射（主队让球为正，客队让球为负）
# 注意：必须包含完整写法和缩写，避免模糊匹配错误
ASIAN_MAP = {
    "受两球半/三球": -2.75, "受两球半": -2.5, "受两球/两球半": -2.25, "受两球": -2,
    "受球半/两球": -1.75, "受球半": -1.5, "受一球/球半": -1.25, "受一球": -1,
    "受半球/一球": -0.75, "受半球": -0.5, "受平手/半球": -0.25, "受平半": -0.25,
    "平手": 0,
    "平手/半球": 0.25, "平半": 0.25, "半球": 0.5, "半球/一球": 0.75, "一球": 1,
    "一球/球半": 1.25, "球半": 1.5, "球半/两球": 1.75, "两球": 2,
    "两球/两球半": 2.25, "两球半": 2.5, "两球半/三球": 2.75, "三球": 3,
}

def asian_to_num(asian_str):
    """亚盘文字转数值"""
    if not asian_str:
        return None
    asian_str = str(asian_str).strip()
    if asian_str in ASIAN_MAP:
        return ASIAN_MAP[asian_str]
    # 尝试模糊匹配
    for k, v in ASIAN_MAP.items():
        if k in asian_str or asian_str in k:
            return v
    return None

def num_to_asian(num):
    """数值转亚盘文字"""
    if num is None:
        return "—"
    for k, v in ASIAN_MAP.items():
        if abs(v - num) < 0.01:
            return k
    return f"{num:+.2f}"

def fetch(url):
    req = urllib.request.Request(url, headers={
        "Referer": "https://odds.500.com/",
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def name_similar(a, b):
    """模糊匹配队名：精确、包含、常见简称表或去掉后缀后匹配"""
    if not a or not b:
        return False
    a = str(a).strip()
    b = str(b).strip()
    if a == b:
        return True
    if a in b or b in a:
        return True
    # 常见球队简称表（全称: [简称...]）
    ALIASES = {
        "米德尔斯堡": ["米堡"], "马德里竞技": ["马竞"], "基多体育大学": ["基多体大"],
        "帕尔梅拉斯": ["帕梅拉斯"], "托特纳姆热刺": ["热刺"], "阿斯顿维拉": ["维拉"],
        "伊普斯维奇": ["伊普斯"], "普拉滕斯": ["普拉滕斯竞技"], "弗鲁米嫩塞": ["弗鲁米嫩"],
        "拉科鲁尼亚": ["拉科"], "毕尔巴鄂竞技": ["毕尔巴鄂"], "阿布扎比艾因": ["艾因"],
        "京都不死鸟": ["京都"], "卡塔尔亚足": ["卡塔尔亚"], "拉普拉塔大学生": ["拉普大学"],
        "阿森纳": ["阿仙奴"], "利物浦": ["利记"],
    }
    # 精确匹配：一方是简称、一方是全称
    for full, shorts in ALIASES.items():
        if (a == full and b in shorts) or (b == full and a in shorts):
            return True
    # 去掉常见后缀再比较
    suffixes = ["FC", "体育", "竞技", "大学", "俱乐部", "队", "足", "亚足", "亚"]
    def norm(s):
        s2 = s
        for suf in suffixes:
            s2 = s2.replace(suf, "")
        return s2
    na, nb = norm(a), norm(b)
    if na and nb and (na == nb or na in nb or nb in na):
        return True
    return False

def fetch_jc_fids(date_str=None):
    """从500彩票网竞彩足球页面解析竞彩比赛的fid映射
    date_str: 指定日期(YYYY-MM-DD)，None为当日
    返回: {队名vs队名: fid}，队名为500页面显示名（可能为简称）

    带含日期的 6 小时 TTL 缓存：当天 fid 映射不变，30 次/天运行只真实抓 ~4 次。
    """
    from webcache import get, set as _wcset
    cache_key = "jc_fids:" + (date_str or time.strftime("%Y-%m-%d"))
    _c = get(cache_key)
    if _c is not None:
        return _c
    if date_str:
        url = f"https://trade.500.com/jczq/?date={date_str}"
    else:
        url = "https://trade.500.com/jczq/"
    req = urllib.request.Request(url, headers={
        "Referer": "https://trade.500.com/",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("gbk", errors="ignore")
    result = {}
    # 当日在售比赛：bet-tb-tr
    trs = re.findall(r'<tr class="bet-tb-tr"[^>]*>.*?</tr>', html, re.S)
    for tr in trs:
        fid_m = re.search(r'fid="(\d+)"', tr)
        if not fid_m:
            continue
        names = re.findall(r'<a[^>]*>([^<]{2,20})</a>', tr)
        if len(names) < 2:
            continue
        league = names[0]
        home = names[1]
        away = names[2] if len(names) > 2 else ""
        if home in ("析", "亚", "欧", "荐"):
            continue
        result[f"{home}vs{away}"] = {"fid": fid_m.group(1), "league": league, "home": home, "away": away}
    # 历史已完场比赛：bet-tb-tr bet-tb-end（data-fixtureid）
    if not result:
        trs2 = re.findall(r'<tr class="bet-tb-tr bet-tb-end"[^>]*>.*?</tr>', html, re.S)
        for tr in trs2:
            fid_m = re.search(r'data-fixtureid="(\d+)"', tr)
            if not fid_m:
                continue
            home_m = re.search(r'data-homesxname="([^"]*)"', tr)
            away_m = re.search(r'data-awaysxname="([^"]*)"', tr)
            num_m = re.search(r'data-matchnum="([^"]*)"', tr)
            league_m = re.search(r'data-simpleleague="([^"]*)"', tr)
            home = home_m.group(1) if home_m else ""
            away = away_m.group(1) if away_m else ""
            if home and away:
                result[f"{home}vs{away}"] = {
                    "fid": fid_m.group(1),
                    "league": league_m.group(1) if league_m else "",
                    "home": home, "away": away,
                    "num": num_m.group(1) if num_m else "",
                }
    _wcset(cache_key, result, ttl_hours=6)
    return result

def parse_odds_history(data):
    """解析赔率历史，返回 {current, open, is_drop_hk, is_drop_mo, drop_dir}"""
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    # 当前赔率（第一条）
    cur = data[0]
    current = {"h": cur[0], "d": cur[1], "a": cur[2], "returnRate": cur[3], "time": cur[4]}
    # 初盘赔率（最后一条）
    op = data[-1]
    open_odds = {"h": op[0], "d": op[1], "a": op[2], "time": op[4]}
    # 降水判断：当前赔率 < 初盘赔率 = 降水（赔率下降=资金流入=看好）
    drop_dirs = []
    for d in ("h", "d", "a"):
        if current[d] is not None and open_odds[d] is not None:
            if current[d] < open_odds[d] - 0.01:
                drop_dirs.append(d)
    return {
        "current": current,
        "open": open_odds,
        "drop_dirs": drop_dirs,
        "history_count": len(data),
    }

def fetch_hkmo_for_match(fid):
    """抓取单场比赛的香港马会和澳门彩票赔率"""
    from webcache import get, set as _wcset
    cache_key = "hkmo:" + str(fid)
    _c = get(cache_key)
    if _c is not None:
        return _c
    result = {"fid": fid, "hk": None, "mo": None, "both_drop": False, "common_drop_dirs": []}
    try:
        # 香港马会
        hk_data = fetch(f"https://odds.500.com/fenxi1/json/ouzhi.php?fid={fid}&cid={HK_CID}")
        result["hk"] = parse_odds_history(hk_data)
        time.sleep(0.3)
        # 澳门彩票
        mo_data = fetch(f"https://odds.500.com/fenxi1/json/ouzhi.php?fid={fid}&cid={MO_CID}")
        result["mo"] = parse_odds_history(mo_data)
        # 双方同时降水判断
        if result["hk"] and result["mo"]:
            hk_drops = set(result["hk"]["drop_dirs"])
            mo_drops = set(result["mo"]["drop_dirs"])
            common = hk_drops & mo_drops
            result["common_drop_dirs"] = list(common)
            result["both_drop"] = len(common) > 0
    except Exception as e:
        print(f"  港澳赔率抓取失败 fid={fid}: {e}", flush=True)
    if result.get("hk") or result.get("mo"):
        _wcset(cache_key, result, ttl_hours=3)
    return result

def fetch_and_match(bd_matches, date_str=None):
    """批量抓取港澳赔率，匹配北单比赛
    bd_matches: 北单比赛列表（含fid/home/away/asian）
    返回: {date, fetched_at, matches: [...], both_drop_count, asian_up_count, asian_down_count}
    """
    if date_str is None:
        date_str = time.strftime("%Y-%m-%d", time.localtime())  # 用本地时间，避免UTC时差导致日期错误
    cache_path = os.path.join(HERE, "hkmo_cache.json")
    asian_cache_path = os.path.join(HERE, "asian_cache.json")

    # 亚盘初盘缓存：每天首次抓取时记录为初盘
    asian_open = {}
    if os.path.exists(asian_cache_path):
        try:
            ac = json.load(open(asian_cache_path))
            if ac.get("date") == date_str:
                asian_open = ac.get("open", {})
                print(f"  亚盘初盘缓存已加载（{date_str}，{len(asian_open)}场）", flush=True)
        except Exception:
            pass

    # 只抓取有北单fid的比赛
    target_matches = [m for m in bd_matches if m.get("fid")]
    print(f"[港澳赔率] 开始抓取 {len(target_matches)} 场比赛的香港马会+澳门彩票赔率+亚盘监控...", flush=True)
    results = []
    both_drop_count = 0
    asian_up_count = 0
    asian_down_count = 0
    new_asian_open = {}

    for i, m in enumerate(target_matches):
        fid = m["fid"]
        home = m.get("home", "")
        away = m.get("away", "")
        print(f"  [{i+1}/{len(target_matches)}] {home}vs{away} (fid={fid})", flush=True)
        r = fetch_hkmo_for_match(fid)
        r["home"] = home
        r["away"] = away
        r["league"] = m.get("league", "")
        r["num"] = m.get("num", "")
        r["kickoff"] = m.get("kickoff", "")

        # 亚盘升/降盘监控
        current_asian = m.get("asian", "")
        current_num = asian_to_num(current_asian)
        key = f"{home}vs{away}"
        # 记录初盘（首次出现或缓存中没有）
        if key not in asian_open and current_num is not None:
            asian_open[key] = {"text": current_asian, "num": current_num}
            new_asian_open[key] = {"text": current_asian, "num": current_num}
        open_asian = asian_open.get(key, {})
        open_num = open_asian.get("num")
        open_text = open_asian.get("text", "—")

        asian_change = "none"  # up/down/none
        asian_diff = 0
        if open_num is not None and current_num is not None:
            asian_diff = current_num - open_num
            if asian_diff > 0.01:
                asian_change = "up"  # 升盘（主队让球更多）
                asian_up_count += 1
            elif asian_diff < -0.01:
                asian_change = "down"  # 降盘（主队让球更少）
                asian_down_count += 1

        r["asian"] = {
            "open": open_text,
            "open_num": open_num,
            "current": current_asian or "—",
            "current_num": current_num,
            "change": asian_change,
            "diff": asian_diff,
        }

        results.append(r)
        if r["both_drop"]:
            both_drop_count += 1
        time.sleep(0.2)

    # 保存亚盘初盘缓存（合并新旧）
    asian_open.update(new_asian_open)
    with open(asian_cache_path, "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "open": asian_open}, f, ensure_ascii=False, indent=1)

    output = {
        "date": date_str,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "matches": results,
        "both_drop_count": both_drop_count,
        "asian_up_count": asian_up_count,
        "asian_down_count": asian_down_count,
        "total": len(results),
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"[港澳赔率] 完成：{len(results)}场，双方同时降水{both_drop_count}场", flush=True)

    # 保存历史记录（永久保存，按日期去重）
    history_path = os.path.join(HERE, "hkmo_history.json")
    try:
        if os.path.exists(history_path):
            with open(history_path, encoding="utf-8") as f:
                history = json.load(f)
        else:
            history = {"records": []}
        # 按比赛编号(num)去重，同一场比赛在不同日期抓取只保留最新一条
        existing = {}
        for r in history.get("records", []):
            key = r.get("num", "") or f"{r.get('kickoff','')}_{r.get('home','')}vs{r.get('away','')}"
            existing[key] = r
        # 计算date_str对应的星期中文
        weekday_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
        try:
            target_weekday = weekday_map[time.strptime(date_str, "%Y-%m-%d").tm_wday]
        except Exception:
            target_weekday = ""
        for r in results:
            num = r.get("num", "")
            # 过滤：只保留竞彩当天星期的比赛（num以当天星期开头），无num的北单比赛跳过
            if not num or not num.startswith(target_weekday):
                print(f"  [港澳赔率] 跳过非当天比赛: {num} {r.get('home','')}vs{r.get('away','')} (期望{target_weekday})", flush=True)
                continue
            key = num or f"{r.get('kickoff','')}_{r.get('home','')}vs{r.get('away','')}"
            # 判断比赛是否已结束（开赛+2.5h），未结束则不保留旧赛果/命中标记
            r_kickoff = r.get("kickoff", "")
            match_ended = False
            try:
                if len(r_kickoff) >= 10 and r_kickoff[4] == '-':
                    kt = time.mktime(time.strptime(r_kickoff, "%Y-%m-%d %H:%M"))
                    match_ended = time.time() >= kt + 2.5 * 3600
            except Exception:
                match_ended = False
            # 保留已有的赛果和命中判定（仅已结束比赛）
            old = existing.get(key, {})
            r["date"] = date_str
            r["fetched_at"] = output["fetched_at"]
            if match_ended:
                r["result"] = old.get("result", "")  # 赛果
                r["hitFlag"] = old.get("hitFlag", "")  # 命中判定
                # 保留已回填的让球盘/亚指命中判定
                for kf in ("hhadHit", "asianHit", "hhadResult", "hhadVerdict", "asianVerdict"):
                    if old.get(kf) and not r.get(kf):
                        r[kf] = old[kf]
            else:
                # 未结束比赛：强制清空赛果/命中标记，防止旧错误残留
                r["result"] = ""
                r["hitFlag"] = ""
                r["hhadHit"] = ""
                r["asianHit"] = ""
                r["hhadResult"] = ""
                r["pickDir"] = None
            existing[key] = r
        history["records"] = list(existing.values())
        history["records"].sort(key=lambda x: (x.get("date", ""), x.get("home", "")))
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
        print(f"[港澳赔率] 历史记录已保存：{len(history['records'])}条（永久保存）", flush=True)
    except Exception as e:
        print(f"  港澳历史记录保存失败: {e}", flush=True)

    return output

def backfill_date(date_str):
    """从500彩票网历史数据回填指定日期的港澳赔率记录
    1. 从500竞彩历史页面获取fid
    2. 抓取香港马会+澳门彩票赔率
    3. 从history.json匹配比赛，获取hhadVerdict/asianVerdict/goal等
    4. 写入hkmo_history.json（赛果和命中由fetch_daily.py的backfill_hkmo_history后续回填）
    """
    print(f"[港澳历史回填] 开始回填 {date_str} ...", flush=True)
    # 1. 获取500历史页面fid
    try:
        fid_map = fetch_jc_fids(date_str)
    except Exception as e:
        print(f"  获取500历史页面失败: {e}", flush=True)
        return 0
    print(f"  500历史页面获取到 {len(fid_map)} 场比赛", flush=True)
    # 2. 加载history.json匹配比赛
    history_path = os.path.join(HERE, "history.json")
    hist_matches = {}
    if os.path.exists(history_path):
        try:
            with open(history_path, encoding="utf-8") as f:
                hd = json.load(f)
            for r in hd.get("records", []):
                if r.get("date", "") == date_str:
                    hist_matches[f"{r.get('home','')}vs{r.get('away','')}"] = r
        except Exception as e:
            print(f"  加载history.json失败: {e}", flush=True)
    print(f"  history.json中 {date_str} 有 {len(hist_matches)} 场", flush=True)
    # 3. 匹配并抓取港澳赔率
    results = []
    matched = 0
    for key, finfo in fid_map.items():
        fid = finfo["fid"]
        f_home = finfo["home"]
        f_away = finfo["away"]
        # 在history.json中找匹配的比赛（模糊匹配队名）
        matched_rec = None
        for hkey, hrec in hist_matches.items():
            h_home = hrec.get("home", "")
            h_away = hrec.get("away", "")
            if name_similar(f_home, h_home) and name_similar(f_away, h_away):
                matched_rec = hrec
                break
        if not matched_rec:
            print(f"  未匹配: {f_home}vs{f_away}", flush=True)
            continue
        matched += 1
        print(f"  [{matched}] {matched_rec.get('num','')} {f_home}vs{f_away} fid={fid}", flush=True)
        # 抓取港澳赔率
        r = fetch_hkmo_for_match(fid)
        r["home"] = matched_rec.get("home", f_home)
        r["away"] = matched_rec.get("away", f_away)
        r["league"] = matched_rec.get("league", finfo.get("league", ""))
        r["num"] = matched_rec.get("num", finfo.get("num", ""))
        r["kickoff"] = matched_rec.get("kickoff", "")
        r["goal"] = matched_rec.get("goal", "")
        r["odds"] = matched_rec.get("odds", {})
        # 保留让球盘/亚指推荐
        if matched_rec.get("hhadVerdict"):
            r["hhadVerdict"] = matched_rec["hhadVerdict"]
        if matched_rec.get("asianVerdict"):
            r["asianVerdict"] = matched_rec["asianVerdict"]
        # 亚盘信息（从北单或其他来源，历史回填可能没有，留空）
        r["asian"] = {"open": "—", "open_num": None, "current": "—", "current_num": None, "change": "none", "diff": 0}
        results.append(r)
        time.sleep(0.3)
    print(f"  匹配并抓取完成: {matched}/{len(fid_map)} 场", flush=True)
    # 4. 写入hkmo_history.json
    hkmo_hist_path = os.path.join(HERE, "hkmo_history.json")
    try:
        if os.path.exists(hkmo_hist_path):
            with open(hkmo_hist_path, encoding="utf-8") as f:
                hkmo_hist = json.load(f)
        else:
            hkmo_hist = {"records": []}
        existing = {}
        for rec in hkmo_hist.get("records", []):
            key = rec.get("num", "") or f"{rec.get('kickoff','')}_{rec.get('home','')}vs{rec.get('away','')}"
            existing[key] = rec
        for r in results:
            key = r.get("num", "") or f"{r.get('kickoff','')}_{r.get('home','')}vs{r.get('away','')}"
            old = existing.get(key, {})
            r["date"] = date_str
            r["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            # 保留已有的赛果和命中
            for kf in ("result", "hitFlag", "hhadHit", "asianHit", "hhadResult"):
                if old.get(kf) and not r.get(kf):
                    r[kf] = old[kf]
            existing[key] = r
        hkmo_hist["records"] = list(existing.values())
        hkmo_hist["records"].sort(key=lambda x: (x.get("date", ""), x.get("home", "")))
        with open(hkmo_hist_path, "w", encoding="utf-8") as f:
            json.dump(hkmo_hist, f, ensure_ascii=False, indent=1)
        print(f"  hkmo_history.json已保存: {len(hkmo_hist['records'])}条", flush=True)
    except Exception as e:
        print(f"  写入hkmo_history.json失败: {e}", flush=True)
    return matched

if __name__ == "__main__":
    import sys
    # 支持命令行参数：python3 fetch_hkmo.py backfill 2026-09-14
    if len(sys.argv) >= 3 and sys.argv[1] == "backfill":
        backfill_date(sys.argv[2])
    else:
        bd_path = os.path.join(HERE, "bd_matches.json")
        if os.path.exists(bd_path):
            bd = json.load(open(bd_path))
            result = fetch_and_match(bd.get("matches", []))
            print(f"\n双方同时降水场次：{result['both_drop_count']}")
            for m in result["matches"]:
                if m["both_drop"]:
                    dirs = "/".join(["主胜" if d=="h" else "平局" if d=="d" else "客胜" for d in m["common_drop_dirs"]])
                    print(f"  ⚠️ {m['home']}vs{m['away']} 双方同时降水: {dirs}")
        else:
            print("请先运行 fetch_bd.py 生成北单赛程")
