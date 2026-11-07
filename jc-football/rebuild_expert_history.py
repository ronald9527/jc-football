#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史「高手推荐」归档文章内容修复（v1.7.127）

用与当天抓取同源的结构化解析器重跑历史文章原文，修正：
- 理由被硬截断在半句 / 混入网站页脚噪音 / 残留 HTML 实体
- 比分建议只提取到一个（漏掉 1:1 等）
- 补上方向原文依据、比分原句，便于逐条回溯核对

【不改动】recommend / recommendKey / hit / result / num / actualScore：
历史已结算，方向一旦变更会让命中统计失去可追溯性。若新解析的方向与
当时标注不一致，只记录 parsedDirection + directionDisputed 供页面提示，
不偷改历史战绩。
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expert_content import parse_structured, verify_recommendation, ODDS_DESC

DIR = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(DIR, "expert_history.json")
# 原文缓存放在系统临时目录，不污染项目目录 / 不进部署包 / 不进 git
CACHE = "/tmp/jc_expert_article_cache"
NOISE = re.compile(r'(1、本站网址|就爱玩球|&nbsp;|喜欢的话可以分享|本站提供|文章观点仅供参考)')
# 只有「明确结论句」才够格用来质疑历史方向；战绩/营销段不算证据
TRUSTED_EV = re.compile(
    r'(推荐[：:]|观点[：:]|方向[：:]|建议[：:]|竞彩|让球|让[胜平负]|'
    r'看好|不败|取胜|关注方向|竞足|分析[：:])')
UNTRUSTED_EV = re.compile(r'(昨天|爆红|连红|近\d+中|分享|战绩|感谢|大家好|延续了)')


def clean_reason(s):
    """纯文本兜底清洗：切掉页脚、去掉 HTML 实体（用于无法重解析的旧记录）。"""
    s = re.sub(r'(1、本站网址[\s\S]*|就爱玩球[\s\S]*|喜欢的话可以分享[\s\S]*|'
               r'本站提供[\s\S]*|文章观点仅供参考[\s\S]*)$', '', s or '')
    s = re.sub(r'&nbsp;?', ' ', s, flags=re.I)
    return re.sub(r'\s+', ' ', s).strip()


def load(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def fetch(url):
    """带本地缓存抓取原文，避免每轮流水线重复请求源站。"""
    os.makedirs(CACHE, exist_ok=True)
    key = re.sub(r'[^0-9A-Za-z]+', '_', url)[-60:] + ".html"
    fp = os.path.join(CACHE, key)
    if os.path.exists(fp) and os.path.getsize(fp) > 2000:
        return open(fp, encoding="utf-8", errors="ignore").read()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://www.92wq.com/",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "ignore")
    if len(html) > 2000:
        open(fp, "w", encoding="utf-8").write(html)
    time.sleep(0.2)
    return html


def main():
    hist = load(HIST, {})
    if not hist:
        print("[历史修复] expert_history.json 为空，跳过")
        return 0
    urls = sorted({r.get("sourceUrl") for recs in hist.values()
                   for r in recs if r.get("sourceUrl")})
    print("[历史修复] %d 条记录 / %d 个原文链接" %
          (sum(len(v) for v in hist.values()), len(urls)), flush=True)

    parsed_by_url = {}
    for u in urls:
        try:
            html = fetch(u)
            # 队名用归档里的竞彩官方名作默认，方向判断更稳
            one = next((r for recs in hist.values() for r in recs
                        if r.get("sourceUrl") == u), {})
            pr = parse_structured(html, expert=one.get("expertName", ""),
                                  home=one.get("home", ""), away=one.get("away", ""),
                                  pub_date=one.get("date", ""))
            parsed_by_url[u] = pr
        except Exception as e:
            print("  [跳过] %s %s" % (u[-20:], str(e)[:50]), flush=True)

    fixed = disputed = skipped = 0
    for dt, recs in hist.items():
        for r in recs:
            # 重跑时先清掉上一轮的存疑标注，避免残留
            r.pop("parsedDirection", None)
            r.pop("directionDisputed", None)
            # 无论能否重解析，都先做一次纯文本兜底清洗，确保理由 0 噪音
            _r = clean_reason(r.get("reason") or "")
            if _r != (r.get("reason") or ""):
                r["reason"] = _r
                fixed += 1
            u = r.get("sourceUrl")
            pr = parsed_by_url.get(u)
            if not pr or not pr.get("checked") or not pr.get("recommendations"):
                skipped += 1
                continue
            # 选与归档对阵最接近的一条推荐
            cand = pr["recommendations"][0]
            for c in pr["recommendations"]:
                ch = (c.get("home") or "")
                rh = (r.get("home") or "")
                if ch and rh and (ch in rh or rh in ch or ch[:2] == rh[:2]):
                    cand = c
                    break
            chk = dict(cand)
            chk["trackRecord"] = pr.get("trackRecord", "")
            ok, issues = verify_recommendation(chk, pr["segments"])
            if not ok:
                skipped += 1
                continue
            old_reason = r.get("reason") or ""
            new_reason = cand.get("analysis") or ""
            # 只在「新理由更干净」时替换：旧理由有噪音/截断，或新理由含完整结论句
            if new_reason and (
                    NOISE.search(old_reason)
                    or (len(old_reason) > 60 and not re.search(r'[。！？]$', old_reason.strip()))
                    or len(new_reason) < len(old_reason)):
                r["reason"] = new_reason
                fixed += 1
            if cand.get("score") and cand["score"] != (r.get("scorePred") or ""):
                # 新比分候选更完整（更多个比分）才更新
                n_old = (r.get("scorePred") or "").count("比")
                n_new = cand["score"].count("比")
                if n_new > n_old or not r.get("scorePred"):
                    r["scorePred"] = cand["score"]
            r["directionEvidence"] = cand.get("evidence", "")
            r["scoreEvidence"] = cand.get("scoreEvidence", "")
            r["contentRebuilt"] = True
            new_dir = cand.get("recommend", "")
            ev = cand.get("evidence", "") or ""
            # 依据必须是可信结论句；战绩/营销段（「昨天…爆红」）不能用来质疑历史
            if (new_dir and new_dir != (r.get("recommend") or "")
                    and TRUSTED_EV.search(ev) and not UNTRUSTED_EV.search(ev[:50])
                    and not ODDS_DESC.search(ev)):
                r["parsedDirection"] = new_dir
                r["directionDisputed"] = True
                disputed += 1

    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    print("[历史修复] 完成：理由修正 %d 条，方向存疑标注 %d 条，无法重解析 %d 条" %
          (fixed, disputed, skipped), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
