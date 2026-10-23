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
# 竞彩中文队名 ↔ 英文队名 权威映射（单一真相源），用于服务端精确匹配竞彩编号
try:
    from team_map import en_to_zh, en_to_zh_all, zh_norm
except Exception:
    def en_to_zh(n):
        return n
    def en_to_zh_all(n):
        return [n] if n else []
    def zh_norm(n):
        return n or ""

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "whoscored_data.json")
PREVIEW_CACHE = os.path.join(HERE, "whoscored_preview_cache.json")   # 预测比分不可变 → 永久缓存
HISTORY_PATH = os.path.join(HERE, "whoscored_history.json")          # 累积历史（按比赛 id 去重）
# v1.7.40 一次性回填表：pending 机制上线前已评估的比赛没有赛前 conf 存档，
# 用上线时整理的快照回填表补 preConf（详见文件内 _note）。随自举代码包下发，
# 任务沙箱每轮加载时自愈，不受部署竞态影响；记录自带 preConf 后不再命中。
BACKFILL_PATH = os.path.join(HERE, "preconf_backfill.json")
# 近期比赛元数据持久化：/previews 只列「未开球」的比赛，一旦开球就从列表消失。
# 每轮把见到的比赛 upsert 到这里；输出时把「今天及往前几天」的比赛从存储补回，
# 保证当天竞彩场次全天都在（不会一开球就从面板消失）。仅存元数据，不存预测（预测在 PREVIEW_CACHE）。
RECENT_PATH = os.path.join(HERE, "whoscored_recent.json")
RECENT_KEEP_DAYS = 7      # 存储保留天数
RECENT_BACKFILL_DAYS = 4  # 回填窗口：今天往前 4 天（覆盖周末/节假日连赛）
# v1.7.40：赛前可信度存档保留天数。可信度只对未开赛场次计算，赛后即从 matches
# 消失；pending 把每轮的赛前 conf 按 mid 暂存，比赛评估时搬进历史记录的
# preConf 字段，让「赛前标过🔥高可信」在赛后回溯里逐场可验证。评估后 preConf
# 已持久化在 records 里，pending 只剩未评估残留，按日期清理防无限增长。
PENDING_KEEP_DAYS = 10

BASE = "https://www.whoscored.com"
# 指纹链：chrome124 / safari184 实测可完整拿到数据（含软挑战真实页）；
# chrome131 会被 Cloudflare 403 硬封，排后面兜底。链的顺序影响硬封锁 URL 的
# 重试成本（每个全封锁 URL ≈ 指纹数×请求耗时 + 退避），已知能用的放最前。
IMPS = ["chrome124", "safari184", "chrome131", "chrome120"]
DELAY = 0.4                    # 每次页面抓取间隔（秒）
MAX_PREVIEW_FETCH = 220        # 单次运行最多抓多少个 preview 页（首_run 全量约 140）
MAX_RESULT_FETCH = 60          # 单次运行最多补多少个历史赛果（按 title）
BJ_OFFSET = 8 * 3600           # 北京时间 = UTC + 8h
# 反爬页面识别（数据感知，绝不误杀软挑战真实页）。WhoScored 的反爬有两种形态：
#   ① 软挑战：真实完整页面（100~600KB）顶部注入 verify-client.js 验证脚本，
#      **数据完整可直接解析**——曾因把它当挑战页拒绝，导致 /livescores、/previews、
#      单场 preview 全部解析失败（历史 records 为空、回溯按钮 0 个）。
#   ② 硬封锁：仅几百字节的质询页 / Cloudflare 403「Attention Required」页，无数据。
CHALLENGE_MARKS = ("Just a moment", "Attention Required", "cf-chl",
                   "challenge-platform", "Enable JavaScript and cookies to continue")
MIN_PAGE_BYTES = 5000          # 硬封锁质询页仅几百~几KB
REAL_PAGE_MARKS = ("hypernova", "predicted-score")   # 真实内容标记
GET_RETRIES = 1                # 已改为快速失败（见 _get docstring），保留常量兼容
GET_BACKOFF = (0,)             # 同上：不再做长退避
# 进程内短期页面缓存：同一轮内同一 URL 只抓一次。
# /livescores 会被 fetch_livescores_matches 和 fetch_results_today 两个解析函数
# 用到，各抓一次既浪费 10s+ 时间又加倍限流压力；软挑战真实页响应慢（3s+/页），
# 重复抓取的代价更高。成功页缓存 PAGE_CACHE_TTL_S；失败/封锁页缓存
# FAIL_CACHE_TTL_S——IP 级封锁持续数小时，45s 内不会翻转，短缓存可避免
# 同轮内重复白抓；又不至于把偶发网络抖动错误缓存太久。
PAGE_CACHE_TTL_S = 120
FAIL_CACHE_TTL_S = 45
_page_cache = {}               # url -> (ts, status, text)
PREVIEW_BUDGET_S = 70          # 预览抓取阶段预算（秒）：必须给赛果回填留时间，
                               # 否则「预测×赛果」的历史累积永远排不上队
TOTAL_BUDGET_S = 110           # 整体预算（秒）：调度器 have_time 120 门槛内收工
EVAL_RESERVE_S = 8             # 赛果回填预留（秒）：留给循环退出后的历史累积+写文件
                               # （实测 <2s）。v1.7.28：15→8——收尾很快，预留过多
                               # 会白扔回填窗口（每轮少回填 3+ 场）
HTTP_TIMEOUT_S = 12            # 单次请求超时（秒）。v1.7.28：30→12——WhoScored 正常
                               # 响应实测 1.6-2s；超过 12s 的请求基本都是 IP 软限流下的
                               # 挣扎，快速失败把时间让给同批次其他场次更重要
                               # （旧值 30s×2 次尝试最坏烧 60s+，一轮只回填 1-2 场）

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


# ---------------- 抓取（curl_cffi 指纹伪装 + 挑战页识别） ----------------
def _is_challenge(txt):
    """WhoScored 反爬页面识别（数据感知，绝不误杀软挑战真实页）。

    两种形态：
      ① 软挑战：真实完整页面（100~600KB）顶部注入 verify-client.js 验证脚本，
         数据完整、可直接解析 —— 必须放行。历史上因把它当挑战页拒绝，
         /livescores、/previews、单场 preview 全部解析失败。
      ② 硬封锁：几百字节质询页 / Cloudflare 403「Attention Required」页，无数据。
    """
    if not txt:
        return True
    if any(k in txt for k in CHALLENGE_MARKS):   # 硬封锁标记
        return True
    if len(txt) < MIN_PAGE_BYTES:                 # 几百字节质询页
        return True
    if any(k in txt for k in REAL_PAGE_MARKS):    # 真实内容标记 → 放行
        return False
    return True                                   # 中等大小但无数据标记 → 重试


def _get(url, referer=BASE + "/"):
    """chrome 指纹链请求（2 次快速尝试，不做长退避）。

    为什么不做长退避：WhoScored 的封锁多为 IP 级限流（实测持续数小时），
    秒级退避等不来解封，只会把时间预算烧光。快速失败 → 把时间让给同批次
    其他 URL → 下轮调度（30 分钟）自然重试。软挑战真实页（带数据）直接放行。
    同轮内重复 URL 走进程内缓存（PAGE_CACHE_TTL_S），只抓一次。
    返回 (status, text)。"""
    cached = _page_cache.get(url)
    if cached:
        ttl = PAGE_CACHE_TTL_S if cached[1] == 200 else FAIL_CACHE_TTL_S
        if time.time() - cached[0] < ttl:
            return cached[1], cached[2]
    try:
        from curl_cffi import requests as cr
    except Exception:
        return 0, ""
    last = (0, "")
    for imp in IMPS[:2]:   # chrome124 / safari184：实测可完整拿到数据
        try:
            r = cr.get(url, impersonate=imp, timeout=HTTP_TIMEOUT_S,
                       headers={"Referer": referer,
                                "Accept-Language": "en-US,en;q=0.9",
                                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"})
            txt = r.text or ""
            if r.status_code == 200 and not _is_challenge(txt):
                _page_cache[url] = (time.time(), 200, txt)
                return 200, txt
            last = (r.status_code, txt[:120])
        except Exception as e:
            last = (0, repr(e)[:120])
        time.sleep(0.3)
    _page_cache[url] = (time.time(), last[0], last[1])   # 失败/封锁也短缓存
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


def fetch_livescores_matches():
    """从 /livescores 抓「今天」的比赛清单（完赛/在赛/今晚未开球全含）。
    /previews 只列未开球的比赛，开球后即从列表消失；这里补齐当天场次，
    保证当天竞彩比赛全天都在面板里。返回 [{id, home, away, leagueEn, utcDate,
    homeScore, awayScore, finished, hasPreview}]。"""
    st, html = _get(BASE + "/livescores", BASE + "/")
    if st != 200 or not html:
        return []
    m = re.search(r'<script[^>]*data-hypernova-key="livescores"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    raw = re.sub(r"^<!--|-->$", "", m.group(1).strip()).strip()
    try:
        d = json.loads(raw)
    except Exception:
        return []
    out = []
    for t in d.get("tournaments") or []:
        lg = t.get("tournamentName") or t.get("stageName") or ""
        for ms in t.get("matches") or []:
            try:
                mid = str(ms.get("id"))
                hn = ms.get("homeTeamName"); an = ms.get("awayTeamName")
                if not (hn and an):
                    continue
                out.append({
                    "id": mid, "home": hn, "away": an, "leagueEn": lg,
                    "utcDate": ms.get("startTimeUtc") or ms.get("startTime"),
                    "homeScore": ms.get("homeScore"), "awayScore": ms.get("awayScore"),
                    "finished": ms.get("status") == 6,
                    "hasPreview": bool(ms.get("hasPreview")),
                })
            except Exception:
                continue
    return out


def _bj_from_utc(utc_val):
    """把 /livescores 的 startTimeUtc（ISO 或 epoch 秒/毫秒）转 (北京日期, 时分)。"""
    if utc_val is None:
        return "", ""
    try:
        if isinstance(utc_val, (int, float)):
            sec = float(utc_val)
            if sec > 1e12:      # 毫秒
                sec /= 1000.0
            u = datetime.utcfromtimestamp(sec)
        else:
            s = str(utc_val).replace("Z", "").split(".")[0]
            u = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        b = u + timedelta(seconds=BJ_OFFSET)
        return b.strftime("%Y-%m-%d"), b.strftime("%H:%M")
    except Exception:
        return "", ""


def fetch_result_by_title(mid):
    """已完赛但不在当日列表的比赛 → 抓 /matches/{id}/live 用 <title> 解析赛果。"""
    st, html = _get(f"{BASE}/matches/{mid}/live", BASE + "/livescores")
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    if not t:
        return None
    m = re.search(r"^(.*?)\s+(\d+)\s*-\s*(\d+)\s+(.*?)\s+-\s+.+(Live|Highlights)", t.group(1).strip())
    if not m:
        return None
    return {"homeName": m.group(1).strip(), "homeScore": int(m.group(2)),
            "awayScore": int(m.group(3)), "awayName": m.group(4).strip()}


# ---------------- 竞彩编号匹配（服务端，跨越英文↔中文队名） ----------------
def _load_jc_matches():
    p = os.path.join(HERE, "matches.json")
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8")) or {}
        return d.get("matches", []) or []
    except Exception:
        return []


def _load_jc_archive():
    """竞彩持久档案（history.json）：含 num 编号 + 中文队名 + 销售日，跨日累积。

    matches.json 只保留【当前销售日】的比赛，比赛滚出销售窗口后就从文件里消失，
    赛后回溯记录（history）若只靠 matches.json 匹配，编号必然丢失。
    这份档案是唯一能在比赛结束多日后仍然补挂竞彩编号的数据源。
    """
    p = os.path.join(HERE, "history.json")
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8")) or {}
        return [r for r in (d.get("records") or [])
                if r.get("num") and r.get("date") and r.get("home")]
    except Exception:
        return []


def _jc_by_date(jc, archive):
    """竞彩比赛按销售日索引：当前销售日（matches.json）优先，持久档案（history.json）兜底。
    返回 {date: [(home, away, num), ...]}"""
    by_date = {}
    for m in jc:
        dt = m.get("date")
        if not dt:
            continue
        by_date.setdefault(dt, []).append(
            (m.get("home", ""), m.get("away", ""), m.get("num", "")))
    for a in archive:
        dt = a.get("date")
        if not dt:
            continue
        # 档案条目去重：同日期同队名同编号不重复插入
        ent = (a.get("home", ""), a.get("away", ""), a.get("num", ""))
        if ent not in by_date.setdefault(dt, []):
            by_date[dt].append(ent)
    return by_date


def _match_jc_num(home_en, away_en, wdate, by_date):
    """英文队名 → 竞彩编号：翻译成中文后与竞彩比赛按「队名精确/归一化 + 日期±2天」匹配。

    v1.7.26：新增中文变体集兜底。team_map 表内同一英文别名在不同分区可能登记了
    不同中文译名（TEAM_EN2ZH 后行覆盖前行，如 Portsmouth→朴次茅斯/朴茨茅斯），
    主译名与竞彩官方名一字之差即整场失配（周二003 曾因此丢预测）。主译名精确
    匹配失败时，再用 en_to_zh_all 的全部中文变体（zh_norm 后）匹配一次。
    """
    home_zh = en_to_zh(home_en or "")
    away_zh = en_to_zh(away_en or "")
    hz = zh_norm(home_zh)
    az = zh_norm(away_zh)
    # 变体集兜底（v1.7.26）：主译名之外的登记译名也参与匹配
    try:
        hv = {zh_norm(z) for z in en_to_zh_all(home_en or "")}
        av = {zh_norm(z) for z in en_to_zh_all(away_en or "")}
    except Exception:
        hv, av = set(), set()
    cands = {wdate}
    if wdate:
        try:
            dd = datetime.strptime(wdate, "%Y-%m-%d")
            # ±2 天容差：竞彩销售日期与 WhoScored 实际开球日期常因时差（尤其美洲赛事）漂移 1~2 天
            for off in (-2, -1, 1, 2):
                cands.add((dd + timedelta(days=off)).strftime("%Y-%m-%d"))
        except Exception:
            pass
    for cd in cands:
        for (jh, ja, jnum) in by_date.get(cd, []):
            # 先精确（中英文），再归一化兜底，最后中文变体集兜底（v1.7.26）
            if (jh == home_zh and ja == away_zh) or (jh == (home_en or "") and ja == (away_en or "")) \
                    or (zh_norm(jh) == hz and zh_norm(ja) == az) \
                    or (bool(hv) and zh_norm(jh) in hv and bool(av) and zh_norm(ja) in av):
                return jnum
    return None


def attach_jc_numbers(matches, jc=None, archive=None):
    """为每条 WhoScored 比赛匹配竞彩编号（服务端，避免脆弱的客户端队名匹配）。

    双数据源：matches.json（当前销售日，编号最新最全）+ history.json（持久档案，
    比赛滚出销售窗口后仍能补挂编号，赛后回溯依赖它）。
    返回 (命中数, 竞彩总场数, 未匹配WhoScored场次列表)。
    仅当「翻译后的中文名 == 某条竞彩比赛的 home/away（且日期容差内）」才算命中，
    因此别名表可「宁多勿漏」，不会造成误匹配。
    """
    if jc is None:
        jc = _load_jc_matches()
    if archive is None:
        archive = _load_jc_archive()
    by_date = _jc_by_date(jc, archive)
    n = 0
    unmatched = []
    for wm in matches:
        found = _match_jc_num(wm.get("home", ""), wm.get("away", ""),
                              wm.get("date", ""), by_date)
        if found:
            wm["jcNum"] = found
            n += 1
        else:
            unmatched.append("%s %s vs %s" % (wm.get("date", ""), wm.get("home", ""), wm.get("away", "")))
    return n, len(jc), unmatched


# ---------------- 主流程 ----------------
def _outcome(h, a):
    return "H" if h > a else ("D" if h == a else "A")


def _merge_preconf(old_rec, pend, has_act):
    """赛前可信度快照并入赛后历史记录（v1.7.40，纯函数，便于单测）。

    old_rec：records 里的旧记录（重建前，可能带往轮写入的 preConf）
    pend：pending[mid] 赛前存档（上一轮 conf 循环写入），无则 None
    has_act：重建后的新记录是否带实际比分（actH 非空）

    规则：
      1. pend 有 conf 且新记录有赛果 -> 采用赛前快照。pending 每轮刷新，
         评估前最后一轮写入的就是最接近开球的赛前看法，优先于任何旧值。
      2. pend 缺失/无 conf -> 保留旧记录的 preConf（防御：pending 被清理或
         丢失时，已写入记录的赛前标记不因重建而消失）。
      3. 两者皆无 -> None（不挂 preConf 键）。
    """
    old_rec = old_rec if isinstance(old_rec, dict) else {}
    if has_act and isinstance(pend, dict) and pend.get("conf"):
        return dict(pend["conf"])   # 副本：记录与存档各自独立，防串改
    return old_rec.get("preConf") or None


def _should_score_conf(x, records):
    """该场是否计算赛前可信度（v1.7.40，纯函数，便于单测）。

    False 的条件（满足其一）：
      1. 已评估（本轮刚回填赛果）——赛后不再给「赛前建议」；
      2. 无预测比分——没有评分对象；
      3. records 里已有赛果（actH 非空）——往轮已评估、本轮仍在列表里的
         场次。records 从不持久化 evaluated 键，仅看 x["evaluated"] 会漏判
         （线上曾出现 83 场已完赛比赛仍带可信度建议的事后诸葛）。
    """
    if x.get("evaluated") or x.get("predH") is None:
        return False
    if (records.get(x.get("id")) or {}).get("actH") is not None:
        return False
    return True


def _prune_pending(pending, today_bj, keep_days=PENDING_KEEP_DAYS):
    """清理过期赛前可信度存档（v1.7.40，纯函数，便于单测）。

    today_bj：北京时间当天（YYYY-MM-DD）。date 早于 today-keep_days 的
    存档出库——比赛取消/赛果永远抓不到才会残留这么久；正常比赛评估后
    preConf 已入 records，pending 条目最多再躺几天就被清掉。
    缺 date 的条目按「永不过期」保守保留（脏数据不误删）。
    """
    cutoff = (datetime.strptime(today_bj, "%Y-%m-%d")
              - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    return {k: v for k, v in (pending or {}).items()
            if (v.get("date") or "9999-99-99") >= cutoff}


def _apply_preconf_backfill(records):
    """把上线前已评估的比赛补上 preConf（v1.7.40，一次性回填，幂等）。

    pending 机制只对上线后的新比赛生效；上线前已评估的历史记录没有赛前
    conf 存档。回填表 preconf_backfill.json 按 mid 给出当轮赛前快照
    （快照时间均早于开球，无事后污染），在这里加载时补挂——只补缺失、
    不覆盖，且预测比分必须与记录一致才挂（防错配）。

    返回补挂条数；有补挂时由调用方落盘。
    """
    table = _load(BACKFILL_PATH, {}) or {}
    entries = table.get("records") if isinstance(table, dict) else None
    if not isinstance(entries, dict) or not entries:
        return 0
    n = 0
    for mid, e in entries.items():
        if not isinstance(e, dict):
            continue
        r = records.get(mid)
        if not r or r.get("preConf") or r.get("actH") is None:
            continue                      # 无记录/已挂/未评估（不该有赛果）
        conf = e.get("conf")
        if not conf or not conf.get("sample"):
            continue
        # 防错配：回填快照的预测比分必须与记录一致，否则说明表与数据漂移，不挂
        if e.get("predH") != r.get("predH") or e.get("predA") != r.get("predA"):
            print("  [赛前可信度] 回填跳过 %s：预测比分不一致（表 %s:%s vs 记录 %s:%s）"
                  % (mid, e.get("predH"), e.get("predA"), r.get("predH"), r.get("predA")))
            continue
        r["preConf"] = dict(conf)
        n += 1
    return n


def fetch_whoscored(max_preview=MAX_PREVIEW_FETCH, max_result=MAX_RESULT_FETCH,
                    budget_s=TOTAL_BUDGET_S):
    """主流程。budget_s：整体时间预算（秒）——调度器整条流水线共 480s，
    本环节必须自律：预览抓取限 PREVIEW_BUDGET_S，剩余时间留给赛果回填
    （「预测×赛果」才是历史累积的原料）；超预算就跳过剩余可选抓取
    （缓存只补缺失，下轮再来），但**必须保证走到写输出那一步**。"""
    t_start = time.time()
    deadline = t_start + budget_s
    preview_deadline = t_start + PREVIEW_BUDGET_S
    now = datetime.utcnow()

    def _kickoff_past(x, grace_sec=600):
        """开球已超过 grace（默认10分钟）：进行中或已结束，其 preview 页只返回
        硬封锁质询页，抓了也白费——预测必须在开球前拿到。"""
        try:
            u = datetime.strptime((x.get("utcDate") or "")[:19], "%Y-%m-%dT%H:%M:%S")
            return (now - u).total_seconds() > grace_sec
        except Exception:
            return False

    pc = _load(PREVIEW_CACHE, {})          # {mid: {predH, predA, ts}}
    hist = _load(HISTORY_PATH, {"records": {}})  # {records: {mid: {...}}}
    records = hist.get("records") or {}
    # v1.7.40：赛前可信度存档。conf 只对未开赛场次计算，比赛评估后即从
    # matches 消失——赛后回溯想看「这场赛前是不是标过🔥高可信」就没了依据。
    # pending 按 mid 暂存每轮的赛前 conf，评估时搬进记录的 preConf 字段。
    pending = hist.get("pending") or {}    # {mid: {date, league, home, away, jcNum, predH, predA, conf}}
    # 一次性回填：上线前已评估的历史记录没有 pending 存档，用回填表补 preConf
    n_bk = _apply_preconf_backfill(records)
    if n_bk:
        hist["records"] = records
        _save(HISTORY_PATH, hist)
        print("  [赛前可信度] 回填历史记录 preConf %d 条（上线前已评估比赛，一次性）" % n_bk)

    items, err = fetch_previews()
    matches = []
    jc_n = 0
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

    # ---- /livescores 今日场次并入：/previews 只列未开球比赛，开球后即撤下；
    #      这里把"今天"的完赛/在赛/今晚未开球场次补进来，保证当天竞彩比赛全天在列 ----
    ls_added = 0
    fetched_ls = 0
    try:
        for lm in fetch_livescores_matches():
            if any(x["id"] == lm["id"] for x in matches):
                continue
            lg = lm.get("leagueEn") or ""
            d, tm = _bj_from_utc(lm.get("utcDate"))
            m = {
                "id": lm["id"], "utcDate": lm.get("utcDate"),
                "league": LEAGUE_ZH.get(lg, lg), "leagueEn": lg,
                "home": lm["home"], "away": lm["away"],
                "url": "%s/matches/%s/preview" % (BASE, lm["id"]),
                "date": d, "bjTime": tm,
                # 已完赛标记：完赛场次的 preview 页只返回硬封锁质询页，
                # 无需抓取（预测必须在开球前抓到）；也无需再抓赛果（livescores 已带比分）
                "_finished": lm.get("homeScore") is not None,
            }
            # 有预测标记、未开球、未缓存 → 抓该场 /preview 页拿预测比分（永久缓存）。
            # 已完赛/已开球的不抓：其 preview 页只返回硬封锁质询页（无数据），
            # 每场白费十几秒重试——这是本环节曾经的头号时间黑洞。
            if (lm.get("hasPreview") and lm.get("homeScore") is None
                    and not _kickoff_past(lm)
                    and fetched_ls < max_preview and time.time() < preview_deadline):
                st, html = _get(m["url"], BASE + "/livescores")
                fetched_ls += 1
                time.sleep(DELAY)
                ph, pa = parse_prediction(html) if st == 200 else (None, None)
                if ph is not None:
                    pc[m["id"]] = {"predH": ph, "predA": pa, "ts": int(now.timestamp())}
            matches.append(m)
            ls_added += 1
    except Exception as e:
        print("  [livescores] 今日场次并入异常：%s" % e)
    if ls_added:
        print("  [livescores] 并入今日场次 %d 场（其中抓取 preview %d 页，补 /previews 已撤下的当天比赛）"
              % (ls_added, fetched_ls))

    # ---- 抓预测比分（覆盖 previews + livescores 全部场次；带永久缓存，只补缺失）----
    # 优先级：今天/明天的比赛最先抓。它们即将开球，/previews 一撤下就再也抓不到
    # 预测比分（已开球 preview 页只返回硬封锁质询页），而未来日期的比赛会一直
    # 挂在列表里，下一轮再抓也不迟。历史上「只有未来日期有预测、过去日期 0 预测」
    # 的根因就是没按这个优先级抓 + 把带数据的软挑战页误判为封锁。
    need = [x for x in matches
            if (x["id"] not in pc or pc[x["id"]].get("predH") is None)
            and not x.get("_finished") and not _kickoff_past(x)]
    _today_bj = (datetime.utcnow() + timedelta(seconds=BJ_OFFSET)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    def _prio(x):
        d = x.get("date") or ""
        try:
            delta = (datetime.strptime(d, "%Y-%m-%d") - _today_bj).days
        except Exception:
            delta = 99
        return (delta if delta >= 0 else -1, x.get("bjTime") or "")
    need.sort(key=_prio)
    fetched = challenged = 0
    for x in need:
        if fetched >= max_preview:
            break
        # 时间预算：调度器整条流水线共 480s，本环节必须自律。
        # 预览阶段只分到 PREVIEW_BUDGET_S，超了就停下轮再来（缓存只补缺失，不会漏），
        # 把剩余时间留给赛果回填——没有赛果，预测再多也进不了历史。
        if fetched and time.time() > preview_deadline:
            print("  [预览抓取] 已达阶段预算 %ds，剩余 %d 场下轮补抓"
                  % (PREVIEW_BUDGET_S, len(need) - fetched))
            break
        st, html = _get(x["url"], BASE + "/previews")
        fetched += 1
        if st != 200:
            challenged += 1
        time.sleep(DELAY)
        ph, pa = parse_prediction(html) if st == 200 else (None, None)
        # 解析失败不写缓存，下轮自动重试
        if ph is not None:
            pc[x["id"]] = {"predH": ph, "predA": pa, "ts": int(now.timestamp())}
        else:
            pc.pop(x["id"], None)
    if fetched or ls_added:
        _save(PREVIEW_CACHE, pc)
    if challenged:
        print("  [预览抓取] %d/%d 场被反爬挑战或抓取失败（已退避重试），下轮自动补抓"
              % (challenged, fetched))
    print("  [预览抓取] 完成 %d 场，耗时 %.0fs" % (fetched, time.time() - t_start))

    for x in matches:
        c = pc.get(x["id"]) or {}
        x["predH"], x["predA"] = c.get("predH"), c.get("predA")

    # ---- 近期比赛回填：/previews 傍晚会把「今天」已开球的比赛撤下，
    #      用持久化 recent 存储把它们补回输出，保证当天竞彩场次全天显示预测 ----
    recent = _load(RECENT_PATH, {})
    ids_now = {x["id"] for x in matches}
    for x in matches:
        recent[x["id"]] = {
            "id": x["id"], "utcDate": x.get("utcDate"), "date": x.get("date"),
            "bjTime": x.get("bjTime"), "league": x.get("league"), "leagueEn": x.get("leagueEn"),
            "home": x["home"], "away": x["away"], "url": x.get("url"),
        }
    bj_now = datetime.utcnow() + timedelta(seconds=BJ_OFFSET)
    today_bj = bj_now.strftime("%Y-%m-%d")
    lo_bj = (bj_now - timedelta(days=RECENT_BACKFILL_DAYS)).strftime("%Y-%m-%d")
    keep_from = (bj_now - timedelta(days=RECENT_KEEP_DAYS)).strftime("%Y-%m-%d")
    backfilled = 0
    for mid, meta in list(recent.items()):
        d = meta.get("date") or ""
        if mid in ids_now or not d or not (lo_bj <= d <= today_bj):
            continue
        m = dict(meta)
        c = pc.get(mid) or {}
        m["predH"], m["predA"] = c.get("predH"), c.get("predA")
        matches.append(m)
        backfilled += 1
    # 清理过期元数据，避免无限增长
    recent = {k: v for k, v in recent.items()
              if (v.get("date") or "") >= keep_from}
    _save(RECENT_PATH, recent)
    if backfilled:
        print("  [近期回填] 从 recent 存储补回 %d 场已滚出 /previews 的近期比赛（含今天）" % backfilled)

    # ---- 竞彩编号匹配（服务端，跨越英文↔中文队名） ----
    jc_n, jc_total, jc_unmatched = attach_jc_numbers(matches)
    if jc_unmatched:
        print("  [竞彩编号] 未匹配 %d 场（多为 WhoScored 有但竞彩未开售的场次）：%s"
              % (len(jc_unmatched), "; ".join(jc_unmatched[:12])))

    # ---- 赛果回填 ----
    # v1.7.28：回填候选「只留有预测且未回填」的场次，并按开球时间升序（等最久先服务）。
    # 旧实现按 matches 原始顺序（/previews 未来场 → livescores 今日 → recent 回填）遍历，
    # 且不检查有无预测——无预测的场次也占用单轮 60 场的 title 抓取配额（抓到了也不会
    # 写 history，纯浪费），周末数百场无预测比赛把配额吃光后，排尾部的旧日期「饿死」
    # 永远回填不上（用户实测：比分预测历史缺 10-18/10-19 两天，而 10-16/17 同样为 0）。
    results = fetch_results_today()
    eval_fetch = 0
    eval_cands = []          # [(utc_datetime, match)] 需 title 抓取的候选（有预测+未回填+已开球）
    for x in matches:
        mid = x["id"]
        if x.get("predH") is None:
            continue                       # 无预测：回填了也不进 history，不浪费配额
        rec = records.get(mid) or {}
        if rec.get("evaluated") or rec.get("actH") is not None:
            continue                       # 已回填（含一次性档案回填的有赛果无标记记录）
        r = results.get(mid)
        if r is not None:                  # livescores 当日列表直接带比分，不占配额
            x["actH"], x["actA"], x["evaluated"] = r["homeScore"], r["awayScore"], True
            x["exactHit"] = (x["predH"] == r["homeScore"] and x["predA"] == r["awayScore"])
            x["outcomeHit"] = _outcome(x["predH"], x["predA"]) == _outcome(r["homeScore"], r["awayScore"])
            continue
        # 判断是否大概率已开赛（UTC 开球 + 2.2h）
        try:
            u = datetime.strptime((x.get("utcDate") or "")[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
        if (now - u).total_seconds() > 2.2 * 3600:
            eval_cands.append((u, x))
    eval_cands.sort(key=lambda t: t[0])    # 开球最早的先回填，杜绝旧日期饿死
    if eval_cands:
        print("  [赛果回填] 候选 %d 场（有预测+未回填+已开球，按开球时间升序）" % len(eval_cands))
    for u, x in eval_cands:
        if eval_fetch >= max_result:
            print("  [赛果回填] 已达单轮上限 %d 场，剩余 %d 场下轮继续"
                  % (max_result, len(eval_cands) - eval_fetch))
            break
        if eval_fetch and time.time() > deadline - EVAL_RESERVE_S:
            print("  [赛果回填] 已达时间预算，剩余 %d 场下轮继续"
                  % (len(eval_cands) - eval_fetch))
            break
        r = fetch_result_by_title(x["id"])
        eval_fetch += 1
        time.sleep(DELAY)
        if r:
            x["actH"], x["actA"], x["evaluated"] = r["homeScore"], r["awayScore"], True
            x["exactHit"] = (x["predH"] == r["homeScore"] and x["predA"] == r["awayScore"])
            x["outcomeHit"] = _outcome(x["predH"], x["predA"]) == _outcome(r["homeScore"], r["awayScore"])
    for x in matches:                      # 未回填成功的保持 records 里的旧状态
        if x.get("evaluated") is None:
            x["evaluated"] = bool(records.get(x["id"], {}).get("evaluated"))
    print("  [赛果回填] 补抓 %d 场，累计已评估 %d 场，总耗时 %.0fs"
          % (eval_fetch, sum(1 for x in matches if x.get("evaluated")), time.time() - t_start))

    # ---- 历史累积（按 mid 去重，含本轮新评估） ----
    # 竞彩编号双保险：matches 上的 jcNum（本轮 attach 挂的，含持久档案兜底）
    # 优先；老记录（往轮累积、当时 matches.json 已滚动导致没挂上）在输出前
    # 再用持久档案统一补挂（见下方 backfill 段）。
    for x in matches:
        if x.get("evaluated") and x.get("predH") is not None:
            old = records.get(x["id"]) or {}
            rec = {
                "id": x["id"], "date": x.get("date"), "league": x["league"],
                "home": x["home"], "away": x["away"],
                "predH": x["predH"], "predA": x["predA"],
                "actH": x["actH"], "actA": x["actA"],
                "exactHit": x["exactHit"], "outcomeHit": x["outcomeHit"],
                "jcNum": x.get("jcNum"),
            }
            # v1.7.40：并入赛前可信度快照（pending 优先；缺失时保留旧记录的
            # preConf）——赛后回溯据此展示「该场赛前标过什么可信度」。
            pre = _merge_preconf(old, pending.get(x["id"]), x.get("actH") is not None)
            if pre:
                rec["preConf"] = pre
            records[x["id"]] = rec
    hist["records"] = records
    hist["updatedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    _save(HISTORY_PATH, hist)

    # ---- 存量历史记录补挂竞彩编号（持久档案匹配，只补缺失不覆盖） ----
    # 场景：比赛在多轮前就已完赛评估，但那轮的 matches.json 已滚动到新销售日，
    # 编号没挂上；此后每轮都用持久档案（history.json）尝试补挂一次。
    jc_archive = _load_jc_archive()
    if jc_archive:
        arc_by_date = _jc_by_date([], jc_archive)
        backfilled = 0
        for r in records.values():
            if r.get("jcNum") or not r.get("date"):
                continue
            num = _match_jc_num(r.get("home", ""), r.get("away", ""), r.get("date", ""), arc_by_date)
            if num:
                r["jcNum"] = num
                backfilled += 1
        if backfilled:
            hist["records"] = records
            _save(HISTORY_PATH, hist)
            print("  [竞彩编号] 存量历史记录补挂 %d 条（持久档案匹配）" % backfilled)

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
        if not _should_score_conf(x, records):
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
        # v1.7.40：赛前可信度入 pending 存档。比赛评估时该存档会搬进历史
        # 记录的 preConf，赛后回溯据此标注「这场赛前标过什么可信度」。
        # 样本 0（可信度待积累）不入库——那种场次赛前也没有任何标记。
        if x["conf"].get("sample"):
            pending[x["id"]] = {
                "date": x.get("date"), "league": x.get("league"),
                "home": x["home"], "away": x["away"],
                "jcNum": x.get("jcNum"),
                "predH": x["predH"], "predA": x["predA"],
                "conf": dict(x["conf"]),
            }

    # ---- pending 清理 + 落盘（v1.7.40）----
    # pending 只是中转站：评估时 conf 已搬进 records 的 preConf，这里按日期
    # 清理超过 PENDING_KEEP_DAYS 天未评估的残留（比赛取消/赛果永远抓不到等），
    # 防止无限增长；records 里的 preConf 已持久化，清理不影响赛后回溯展示。
    today_bj = (now + timedelta(seconds=BJ_OFFSET)).strftime("%Y-%m-%d")
    n_pend = len(pending)
    pending = _prune_pending(pending, today_bj)
    hist["pending"] = pending
    if len(pending) != n_pend:
        print("  [赛前可信度] 清理过期 pending %d 条（保留 %d 条）"
              % (n_pend - len(pending), len(pending)))
    hist["updatedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    _save(HISTORY_PATH, hist)

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
          f"对应竞彩编号 {jc_n} 场，"
          f"历史回测 {n} 场（精确命中 {overall.get('exactHit', '-')}%、方向 {overall.get('outcomeHit', '-')}%），"
          f"新抓 preview {fetched} 页 / 补赛果 {eval_fetch} 场 → {OUT_PATH}")
    return out


if __name__ == "__main__":
    fetch_whoscored()
