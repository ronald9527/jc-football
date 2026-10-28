#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞彩足球【备用数据源】500.com 抓取器
=====================================

为什么需要它
------------
主数据源是中国竞彩官方接口 webapi.sporttery.cn，它**封锁海外 IP**：
  - 本地（中国网络）访问 -> HTTP 200
  - GitHub Actions（美国）访问 -> HTTP 567 拒绝
这导致无人值守流水线跑在海外时抓不到竞彩赛程与赔率。

500.com 是竞彩官方授权销售平台，其竞彩足球页面 trade.500.com/jczq/
经实测**海外可正常访问（HTTP 200）**，且数据为服务端渲染，包含：
  竞彩编号 / 联赛 / 比赛日期时间 / 主客队中文名 / 排名 / 让球 / 胜平负赔率 / 让球胜平负赔率

因此把它作为官方接口的回退源：官方不通时用它，产出与官方同结构的
match 字典，流水线其余环节无需改动即可消费。

产出字段与 fetch_daily.py 主流程保持一致（num/date/home/away/had/hhad...），
缺失项（总进球 ttg、半全场 hhProb 等）留空，下游已按可缺省处理。
"""

import re
import time

JCZQ_URL = "https://trade.500.com/jczq/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 单场块： <tr class="bet-tb-tr ..." data-xxx="..."> ... </tr>
_ROW_RE = re.compile(r'<tr[^>]*class="[^"]*bet-tb-tr[^"]*"[^>]*>.*?</tr>', re.S)
_ATTR_RE = re.compile(r'data-([a-z]+)="([^"]*)"')
_NUM_RE = re.compile(r'(周[一二三四五六日]\d{3})')
_ODD_RE = re.compile(r'>\s*(\d+\.\d{2})\s*<')


def _f(x):
    """赔率转 float。必须与 fetch_daily.to_float/fmt_rate 保持一致：
    下游会直接做 1.0/h 之类的数学运算，字符串会抛 TypeError。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _http_get(url, tries=3, timeout=25):
    """带指纹的 GET；返回解码后的 HTML 文本，失败返回空串。"""
    for i in range(tries):
        try:
            from curl_cffi import requests as cr
            r = cr.get(url, impersonate="chrome124", timeout=timeout,
                       headers={"User-Agent": UA, "Referer": "https://www.500.com/"})
            if r.status_code == 200 and len(r.content) > 20000:
                # 500.com 页面为 GBK 编码
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
            time.sleep(2)
    return ""


def parse_jczq(html):
    """解析 500.com 竞彩足球页面 -> 与官方接口同结构的 match 列表。"""
    out = []
    for b in _ROW_RE.findall(html or ""):
        attr = dict(_ATTR_RE.findall(b))
        home = (attr.get("homesxname") or "").strip()
        away = (attr.get("awaysxname") or "").strip()
        if not home or not away:
            continue

        plain = re.sub(r"<[^>]+>", "", b)
        mnum = _NUM_RE.search(plain)
        odds = _ODD_RE.findall(b)

        date = (attr.get("matchdate") or "").strip()
        t = (attr.get("matchtime") or "").strip()
        num = mnum.group(1) if mnum else ""

        # 赔率序列固定为：胜平负(3) + 让球胜平负(3)
        had = {"h": _f(odds[0]), "d": _f(odds[1]), "a": _f(odds[2])} if len(odds) >= 3 else {}
        hhad = {"h": _f(odds[3]), "d": _f(odds[4]), "a": _f(odds[5])} if len(odds) >= 6 else {}
        if not had.get("h") and not hhad.get("h"):
            continue

        # 让球：500 的 data-rangqiu 与竞彩 goalLine 符号约定一致
        # （负数=主队让球，正数=主队受让），正数补 "+" 便于展示
        rq = (attr.get("rangqiu") or "").strip()
        goal = ""
        if rq:
            try:
                rqf = float(rq)
                goal = ("+%g" % rqf) if rqf > 0 else ("%g" % rqf)
            except Exception:
                goal = rq
        if hhad:
            hhad["goal"] = goal

        # 排名：行内形如 [10]布鲁马波 ... 赫根[4]
        ranks = re.findall(r"\[(\d+)\]", plain)
        hr = ranks[0] if len(ranks) > 0 else ""
        ar = ranks[1] if len(ranks) > 1 else ""

        out.append({
            "id": attr.get("fixtureid") or attr.get("infomatchid") or "",
            "num": num,
            "week": num[:2] if num else "",
            "date": date,
            "kickoff": (date + " " + t).strip(),
            "time": t,
            "league": (attr.get("simpleleague") or "").strip(),
            "home": home,
            "away": away,
            "hr": hr,
            "ar": ar,
            "hrn": int(hr) if hr.isdigit() else None,
            "arn": int(ar) if ar.isdigit() else None,
            "status": "",
            "had": had,
            "hhad": hhad,
            # 500.com 页面不提供总进球/半全场玩法，留空由下游缺省处理
            "ttg": {},
            "goalsExp": None,
            "hhProb": None,
            "aaProb": None,
            "_src": "500.com",
        })
    return out


def fetch_jczq_500(tries=3):
    """抓取并解析；成功返回 match 列表（可能为空列表），失败返回 None。"""
    html = _http_get(JCZQ_URL, tries=tries)
    if not html:
        return None
    return parse_jczq(html)


if __name__ == "__main__":
    import json
    ms = fetch_jczq_500()
    if ms is None:
        print("[FAIL] 500.com 抓取失败（网络不可达或页面结构变化）")
        raise SystemExit(1)
    print(f"[OK] 500.com 抓到 {len(ms)} 场")
    for m in ms[:8]:
        print("  %s %s %s %s vs %s  胜平负 %s/%s/%s  让球%s %s/%s/%s" % (
            m["num"], m["date"], m["time"], m["home"], m["away"],
            m["had"].get("h", "-"), m["had"].get("d", "-"), m["had"].get("a", "-"),
            m["hhad"].get("goal", ""), m["hhad"].get("h", "-"),
            m["hhad"].get("d", "-"), m["hhad"].get("a", "-")))
    print(json.dumps(ms[:1], ensure_ascii=False, indent=1))
