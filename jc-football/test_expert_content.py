#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高手推荐文章内容逐篇核对回归（v1.7.127）

离线用源站原文缓存，逐篇断言：方向、比分、队名、理由清洗、战绩清洗。
期望值由人工逐篇核对 92wq 原文得出，见 EXPECTED。
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expert_content import parse_structured, verify_recommendation

CACHE = os.environ.get(
    "EXPERT_CACHE",
    "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d41-fe30e7520000/art_raw_127",
)

# (文章id后缀, 专家, 期望[(方向, 比分, 主队, 客队)...])  —— 方向为空表示「不应产出推荐」
EXPECTED = [
    ("view-329496.htm", "张晓彬", [("主胜", "比分2比0、3比1", "罗马", "萨索洛")]),
    ("view-329452.htm", "冒险王", [("主胜/平局", "比分1比1、2比1", "欧塞尔", "巴黎FC")]),
    ("view-329480.htm", "张磊", [("主胜", "比分2比0", "阿尔维卡", "卡萨皮亚")]),
    ("view-329463.htm", "史密斯", [("客胜", "比分1比2、1比3", "奥萨苏纳", "毕尔巴鄂"),
                                    ("主胜", "比分2比0、1比0", "阿尔韦卡", "卡萨皮亚")]),
    ("view-329482.htm", "杨震", [("客胜", "", "奥萨苏纳", "毕尔巴鄂")]),
    ("view-329484.htm", "彭雷", [("让负", "比分2比2、2比3", "斯图加特", "不来梅")]),
    ("view-329490.htm", "阿莲", [("主胜", "比分2比0、2比1", "斯图加特", "不来梅")]),
    ("view-329497.htm", "宋昭毅", [("客胜", "比分0比2、1比2", "诺丁汉", "曼城")]),
    ("view-329506.htm", "范德彪", [("让负", "比分1比1、1比2", "斯图加特", "不来梅")]),
    ("view-329507.htm", "中.誉", [("客胜", "", "斯图加特", "不来梅")]),
    ("view-329481.htm", "罗辑", [("让胜", "比分0比0、1比1", "帕尔马", "博洛尼亚")]),
    ("view-329483.htm", "布海东", [("客胜", "比分0比2、1比2", "帕尔马", "博洛尼亚")]),
    ("view-329461.htm", "工体博哥", [("客胜/平局", "", "西班牙人", "拉科")]),
    ("view-329462.htm", "航哥", [("让负", "比分2比2、1比2", "西班牙人", "拉科")]),
    ("view-329489.htm", "刘建宏", []),  # 全文无明确方向词，宁缺勿错
    ("view-329476.htm", "晓哥看球", [("主胜", "比分2比1、2比0", "马拉加", "桑坦德")]),
    ("view-329485.htm", "绿茵七号位", [("让负", "比分1比3、2比3", "马拉加", "桑坦德")]),
    ("view-329493.htm", "秦游夏", [("客胜/平局", "比分1比1、1比2", "马拉加", "桑坦德")]),
    ("view-329465.htm", "敬真", [("让负", "比分1比1、1比2", "朴次茅斯", "斯托克城")]),
    ("view-329478.htm", "徐弋", [("客胜/平局", "比分1比1、2比2、1比2", "朴次茅斯", "斯托克城")]),
    ("view-329464.htm", "张羽鸿", [("客胜", "比分2比2、1比2", "珀斯", "纽喷气机")]),
    ("view-329475.htm", "蒋娴", [("让负", "比分1比1、1比2", "金泉尚武", "安养FC")]),
    ("view-329479.htm", "晓哥看球", [("让负", "比分1比1、1比2", "金泉尚武", "安养FC")]),
]


def main():
    total = passed = failed = 0
    for fid, expert, exp in EXPECTED:
        fp = os.path.join(CACHE, fid + ".html")
        if not os.path.exists(fp):
            print("SKIP %s 缺缓存" % fid)
            continue
        html_text = open(fp, encoding="utf-8", errors="ignore").read()
        # 与真实抓取一致：把文章标题里的主客队名传给解析器
        h0 = exp[0][2] if exp else ""
        a0 = exp[0][3] if exp else ""
        r = parse_structured(html_text, expert=expert, home=h0, away=a0,
                             pub_date="2026-11-07")
        recs = r["recommendations"]
        total += 1
        ok = True
        msgs = []
        if len(recs) != len(exp):
            ok = False
            msgs.append("推荐条数 %d != 期望 %d" % (len(recs), len(exp)))
        for i, e in enumerate(exp):
            if i >= len(recs):
                break
            d, sc, h, a = e
            got = recs[i]
            if got["recommend"] != d:
                ok = False
                msgs.append("[%d] 方向 %s != %s (依据:%s)" % (i, got["recommend"], d, got.get("evidence", "")[:60]))
            if got["score"] != sc:
                ok = False
                msgs.append("[%d] 比分 %s != %s (原句:%s)" % (i, got["score"], sc, got.get("scoreEvidence", "")[:60]))
            # 队名必须与原文对阵一致（防止场次标题正则把「奥萨苏纳」截成「苏纳」）
            if h and got.get("home", "") != h:
                ok = False
                msgs.append("[%d] 主队 %s != %s" % (i, got.get("home"), h))
            if a and got.get("away", "") != a:
                ok = False
                msgs.append("[%d] 客队 %s != %s" % (i, got.get("away"), a))
        # 内容校验
        for i, rec in enumerate(recs):
            rec = dict(rec)
            rec["trackRecord"] = r["trackRecord"]
            vok, issues = verify_recommendation(rec, r["segments"])
            if not vok:
                ok = False
                msgs.append("[%d] 校验: %s" % (i, "; ".join(issues)))
        if "&nbsp;" in r["trackRecord"] or "&nbsp" in r["trackRecord"]:
            ok = False
            msgs.append("战绩含 &nbsp;")
        if ok:
            passed += 1
            print("PASS %-18s %-8s %s" % (fid, expert, [(x["recommend"], x["score"]) for x in recs]))
        else:
            failed += 1
            print("FAIL %-18s %-8s %s" % (fid, expert, " | ".join(msgs)))
            for x in recs:
                print("      got:", x["recommend"], x["score"], "| ev:", x.get("evidence", "")[:100],
                      "| scEv:", x.get("scoreEvidence", "")[:80])
    print("\n=== 合计 %d 篇: PASS %d / FAIL %d ===" % (total, passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
