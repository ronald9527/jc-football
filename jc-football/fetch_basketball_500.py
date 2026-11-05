#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩篮球【备用数据源】500.com 抓取器（v1.7.119）。

为什么需要它
------------
篮球官方接口 webapi.sporttery.cn 的海外 IP 长期返回 HTTP 567（GitHub Actions
机房在美国），而足球早已有 fetch_500.py 兜底，篮球却没有——这就是篮球从
2026-11-02 起整整停更 4 天、AI 预测与串关反复拿旧比赛造假推荐的根因。

500.com 竞彩篮球页 trade.500.com/jclq/?date=YYYY-MM-DD 实测海外 HTTP 200，
服务端渲染 GBK，按「销售批次日」返回整批比赛（含已结束场次的比分），包含：
  竞彩编号 / 批次日 / 比赛日期时间 / 联赛 / 主客队 / 让分线 / 预设总分 /
  让分胜负赔率 / 胜分差 12 档赔率 / 赛果比分

字段口径（逐项与官方接口对照验证）
--------------------------------
  - data-pei 两个 <span>  = 让分胜负 hdc 的 [主盘 h, 客盘 a] 赔率
  - data-rangfen          = hdc.goalLine（负=主队让分，与官方符号一致）
  - data-yszf             = hilo.goalLine（预设总分）
  - 展开区「客胜」6 档     = 官方 wnm.l1..l6；「主胜」6 档 = 官方 wnm.w1..w6
  - class="score"         = 「主:客」比分（用官方档案 10-31 火箭98:119雷霆校准）
  - mnl 赔率              = 由胜分差 12 档隐含概率反推（市场共识，非官方 SP）
  - hilo 赔率             = 页面不提供，用竞彩标准 1.70/1.70 并标记近似

产出结构与 fetch_basketball.fetch_calc() 完全一致，可无缝合并；
降级数据带 _src/_oddsApprox 标记，前端可如实提示「备用源参考赔率」。
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

JCLQ_URL = "https://trade.500.com/jclq/?date=%s"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PAYBACK = 0.886          # 竞彩篮球理论返奖率，用于从胜分差概率反推胜负赔率

_ROW_RE = re.compile(r'<tr[^>]*class="[^"]*bet-tb-tr[^"]*"[^>]*>.*?</tr>', re.S)
_MORE_RE = re.compile(r'<tr><th>(客胜|主胜)</th>(.*?)</tr>', re.S)
_ATTR_RE = re.compile(r'data-([a-z]+)="([^"]*)"')
_SCORE_RE = re.compile(r'class="score"[^>]*>\s*(\d+)\s*:\s*(\d+)\s*</a>')
_PEI_RE = re.compile(r'<td class="td td-pei"[^>]*>.*?</td>', re.S)
_SPAN_RE = re.compile(r'<span>\s*([\d.]+)\s*</span>')
_SP_RE = re.compile(r'data-sp="([\d.]+)"')
_RANK_RE = re.compile(r'\[([^\]]+)\]')


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _http_get(url, tries=3, timeout=25):
    """带浏览器指纹的 GET，返回 GBK 解码后的 HTML；失败返回空串。"""
    for i in range(tries):
        try:
            from curl_cffi import requests as cr
            r = cr.get(url, impersonate="chrome124", timeout=timeout,
                       headers={"User-Agent": UA, "Referer": "https://www.500.com/"})
            if r.status_code == 200 and len(r.content) > 20000:
                return r.content.decode("gbk", errors="ignore")
        except Exception:
            pass
        try:
            import requests
            r = requests.get(url, timeout=timeout,
                             headers={"User-Agent": UA, "Referer": "https://www.500.com/"})
            if r.status_code == 200 and len(r.content) > 20000:
                return r.content.decode("gbk", errors="ignore")
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1.5)
    return ""


def _parse_more(segment):
    """某场比赛主行之后、下一主行之前的片段 -> (客胜6档 l1..l6, 主胜6档 w1..w6)。

    不能用 <tr class="bet-more-wrap ... </tr> 整体匹配：展开区里还嵌套了
    两行 <tr><th>客胜/主胜</th>，非贪婪正则会在第一个内层 </tr> 处截断，
    导致只拿到客胜 6 档、主胜 6 档永远缺失（mnl 反推因此恒为 None）。
    """
    guest, host = [], []
    for side, payload in _MORE_RE.findall(segment):
        vals = [float(x) for x in _SP_RE.findall(payload)[:6]]
        if len(vals) == 6:
            if side == "客胜":
                guest = vals
            else:
                host = vals
    return guest, host


def _derive_mnl(guest, host):
    """由胜分差 12 档隐含概率反推胜负 mnl 赔率（市场共识口径）。

    六个主胜分区间 + 六个客胜分区间覆盖全部赛果，Σ(1/赔率) 即各自原始概率，
    归一化后按竞彩返奖率折算成固定赔率。实测与官方 mnl 偏差通常在 0.1 内。
    """
    if len(guest) < 6 or len(host) < 6:
        return None
    ph = sum(1.0 / x for x in host if x > 1)
    pa = sum(1.0 / x for x in guest if x > 1)
    if ph <= 0 or pa <= 0:
        return None
    p_home, p_away = ph / (ph + pa), pa / (ph + pa)
    return {
        "h": round(PAYBACK / p_home, 2),
        "a": round(PAYBACK / p_away, 2),
    }


def parse_jclq(html, batch_date=None):
    """解析 500 竞彩篮球页 -> 与 fetch_calc() 同结构的 match 列表。

    每个 match 额外带：
      score        [主, 客]（仅已结束场次有）
      batchDate    销售批次日（与官方 businessDate 同义）
      _src         "500.com"
      _oddsApprox  赔率为参考值而非官方 SP 的玩法列表
    """
    out = []
    row_matches = list(_ROW_RE.finditer(html or ""))
    for idx, rm in enumerate(row_matches):
        row = rm.group(0)
        seg_end = row_matches[idx + 1].start() if idx + 1 < len(row_matches) else len(html)
        segment = html[rm.end():seg_end]
        attr = dict(_ATTR_RE.findall(row))
        home = (attr.get("homesxname") or "").strip()
        away = (attr.get("awaysxname") or "").strip()
        if not home or not away:
            continue
        num = (attr.get("matchnum") or "").strip()
        mid = (attr.get("id") or "").strip()
        mdate = (attr.get("matchdate") or "").strip()
        mtime = (attr.get("matchtime") or "").strip()[:5]
        batch = (attr.get("processdate") or "").strip() or batch_date or mdate
        kickoff = (mdate + " " + mtime).strip()

        # 排名：team-l 是客队、team-r 是主队（竞彩篮球页面客队在前）
        plain = re.sub(r"<[^>]+>", " ", row)
        ranks = _RANK_RE.findall(plain)
        ar = ranks[0] if ranks else ""
        hr = ranks[-1] if len(ranks) > 1 else ""

        # 让分胜负赔率（隐藏 td-pei 两值 = 主盘/客盘）
        pei = []
        pei_block = _PEI_RE.search(row)
        if pei_block:
            pei = [float(x) for x in _SPAN_RE.findall(pei_block.group(0))[:2]
                   if _f(x) is not None]
        rf = (attr.get("rangfen") or "").strip()
        # 官方 goalLine 正数带 "+"，统一口径便于前端展示与 _line_num 解析
        if rf and not rf.startswith(("-", "+")):
            rf = "+" + rf
        yszf = (attr.get("yszf") or "").strip()
        hdc = {"goalLine": rf}
        if len(pei) == 2:
            hdc["h"], hdc["a"] = pei[0], pei[1]

        # 胜分差 12 档
        guest, host = _parse_more(segment)
        wnm = {}
        for i, v in enumerate(guest, 1):
            wnm["l%d" % i] = v
        for i, v in enumerate(host, 1):
            wnm["w%d" % i] = v

        mnl = _derive_mnl(guest, host)
        hilo = {"goalLine": yszf, "h": 1.70, "l": 1.70} if yszf else None

        rec = {
            "id": mid,
            "num": num,
            "week": num[:2] if num else "",
            "date": batch,
            "kickoff": kickoff,
            "time": mtime,
            "league": (attr.get("simpleleague") or "").strip(),
            "home": home,
            "away": away,
            "hr": hr,
            "ar": ar,
            "mnl": mnl,
            "hdc": hdc if hdc.get("h") else None,
            "hilo": hilo,
            "wnm": wnm or None,
            "hasOdds": bool(mnl or hdc.get("h") or wnm),
            "batchDate": batch,
            "_src": "500.com",
            "_oddsApprox": ["mnl", "hilo"],
        }
        sc = _SCORE_RE.search(row)
        if sc:
            rec["score"] = [int(sc.group(1)), int(sc.group(2))]
            rec["evaluated"] = True
        out.append(rec)
    return out


def fetch_day(date, tries=3):
    """抓某一销售批次日；返回 match 列表，网络失败返回 None。"""
    html = _http_get(JCLQ_URL % date, tries=tries)
    if not html:
        return None
    return parse_jclq(html, date)


def fetch_recent(days=6, today=None, tries=3):
    """抓最近 N 个批次日（含今天），返回 (matches, failed_dates)。

    按批次日去重：同一场比赛可能连续出现在多天页面里，后抓的（更接近今天）
    覆盖先抓的，保证拿到最新赔率/赛果。
    """
    today = today or datetime.now()
    by_key = {}
    failed = []
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        ms = fetch_day(d, tries=tries)
        if ms is None:
            failed.append(d)
            continue
        for m in ms:
            k = (m.get("date", ""), m.get("num", ""), m.get("home", ""))
            by_key[k] = m
        time.sleep(0.25)
    return list(by_key.values()), failed


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        ms = fetch_day(args[0])
        if ms is None:
            print("[FAIL] 500.com 抓取失败")
            raise SystemExit(1)
        print("[OK] %s 共 %d 场" % (args[0], len(ms)))
        for m in ms:
            print("  %s %s %s %s vs %s  让分%s [%s/%s]  总分%s  赛果%s" % (
                m["num"], m["date"], m["time"], m["home"], m["away"],
                (m.get("hdc") or {}).get("goalLine", "-"),
                (m.get("hdc") or {}).get("h", "-"),
                (m.get("hdc") or {}).get("a", "-"),
                (m.get("hilo") or {}).get("goalLine", "-"),
                m.get("score") or "待赛"))
    else:
        ms, bad = fetch_recent(6)
        print("[OK] 近 6 批次共 %d 场（失败日期 %s）" % (len(ms), bad or "无"))
        print(json.dumps(ms[:1], ensure_ascii=False, indent=1))
