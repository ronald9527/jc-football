#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— 数据自检（每日自动跑，发现问题退出码非 0）

检查 6 大类：
  1. 数据完整性：必填字段、编号唯一、日期合法
  2. 赔率合理性：范围 1.05~25、返奖率落在 85%~92%（异常说明接口数据错乱）
  3. 概率一致性：胜平负三者概率之和接近 100%（±3pt）
  4. 串关合理性：拒绝蚊子肉（单场 >= 1.45）、总赔 >= 2.2、EV/命中率字段存在
  5. 历史库：条数、日期连续、命中判定字段完整
  6. 页面产物：占位符是否已全部替换、无未替换标记
  7. 数据源隔离：独立源（The Odds API / football-data.org / 高手荐单）不得写回核心
     matches.json 或 history.json（模型训练数据），各自落独立文件

输出：selftest_report.json；控制台打印 [OK]/[WARN]/[ERROR]
退出码：0=通过  1=有 ERROR  2=仅有 WARN（可用 --strict 让 WARN 也失败）
"""
import json
import os
import re
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "selftest_report.json")

issues = []          # {"level":"ERROR|WARN","cat":"...","msg":"..."}


def add(level, cat, msg):
    issues.append({"level": level, "cat": cat, "msg": msg})
    print("  [%s] %s：%s" % (level, cat, msg))


def load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        add("ERROR", "文件", "%s 解析失败：%s" % (name, e))
        return default


def check_matches(data):
    print("· 检查 1/6 数据完整性")
    ms = (data or {}).get("matches", [])
    if not ms:
        add("ERROR", "数据完整性", "matches.json 无比赛数据")
        return
    today = datetime.date.today().strftime("%Y-%m-%d")
    upd = data.get("updateTime") or ""
    if not upd:
        add("ERROR", "数据完整性", "缺少 updateTime")
    elif upd[:10] != today:
        add("WARN", "时效性", "数据更新时间 %s 不是今天（%s），抓取任务可能停摆" % (upd, today))

    nums = [m.get("num") for m in ms]
    dup = {n for n in nums if nums.count(n) > 1}
    if dup:
        add("ERROR", "数据完整性", "编号重复：%s" % sorted(dup))

    for m in ms:
        n = m.get("num")
        if not m.get("home") or not m.get("away"):
            add("ERROR", "数据完整性", "%s 缺少队名" % n)
        if not m.get("kickoff"):
            add("WARN", "数据完整性", "%s 缺少开赛时间" % n)

    print("  共 %d 场" % len(ms))


def check_odds(data):
    print("· 检查 2/6 赔率合理性")
    ms = (data or {}).get("matches", [])
    bad_range, rates = [], []
    for m in ms:
        had = m.get("had") or {}
        hs = [had.get(k) for k in ("h", "d", "a")]
        if not all(isinstance(x, (int, float)) and x > 0 for x in hs):
            continue
        for k, v in had.items():
            if k in ("h", "d", "a") and not (1.05 <= v <= 25):
                bad_range.append("%s %s=%.2f" % (m.get("num"), k, v))
        s = 1 / hs[0] + 1 / hs[1] + 1 / hs[2]
        rates.append(1 / s)
    if bad_range:
        add("ERROR", "赔率合理性", "赔率超出合理区间：%s" % bad_range[:5])
    if rates:
        avg = sum(rates) / len(rates)
        print("  平均返奖率 %.1f%%（样本 %d 场）" % (avg * 100, len(rates)))
        if not (0.84 <= avg <= 0.93):
            add("WARN", "赔率合理性", "平均返奖率 %.1f%% 偏离常态 88.6%%，接口数据可能异常" % (avg * 100))


def check_prob(data):
    print("· 检查 3/6 概率一致性")
    ms = (data or {}).get("matches", [])
    off = []
    for m in ms:
        p = m.get("hadProb") or {}
        hs = [p.get("h"), p.get("d"), p.get("a")]
        if not all(isinstance(x, (int, float)) for x in hs):
            continue
        tot = sum(hs)
        if abs(tot - 100) > 3:
            off.append("%s=%.1f" % (m.get("num"), tot))
    if off:
        add("WARN", "概率一致性", "概率之和偏离 100%% 超过 3pt：%s" % off[:5])


def check_parlay(data):
    print("· 检查 4/6 串关合理性（蚊子肉检测）")
    sp = (data or {}).get("safeParlay") or {}
    found = 0
    for key in ("parlay2", "parlay3", "parlayBoost"):
        p = sp.get(key)
        if not p:
            continue
        found += 1
        legs = p.get("legs") or []
        if not legs:
            add("ERROR", "串关", "%s 无腿" % key)
            continue
        min_o = min(l.get("odds") or 0 for l in legs)
        tot = p.get("totalOdds") or 0
        if min_o < 1.45:
            add("WARN", "串关", "%s 存在蚊子肉单场 %.2f（下限 1.45）" % (key, min_o))
        if tot < 2.2:
            add("WARN", "串关", "%s 总赔 %.2f 偏低" % (key, tot))
        for f in ("jointProb", "ev", "rake"):
            if p.get(f) is None:
                add("WARN", "串关", "%s 缺少字段 %s" % (key, f))
        print("  %s：%s %.2f倍 命中%s%% EV%s%%" % (
            key, p.get("tier"), tot, p.get("jointProb"), p.get("ev")))
    if found == 0:
        add("WARN", "串关", "今日无串关推荐（候选不满足档位门槛）")


def check_history(hist):
    print("· 检查 5/6 历史库")
    recs = (hist or {}).get("records", [])
    if not recs:
        add("ERROR", "历史库", "history.json 无记录")
        return
    dates = sorted({r.get("date") for r in recs if r.get("date")})
    no_result = sum(1 for r in recs if not (r.get("result") or {}).get("score"))
    print("  共 %d 条，覆盖 %d 个日期（%s ~ %s），无赛果 %d 条" % (
        len(recs), len(dates), dates[0], dates[-1], no_result))
    if len(recs) < 50:
        add("WARN", "历史库", "历史仅 %d 条，模型统计不可靠" % len(recs))


def check_page():
    print("· 检查 6/6 页面产物")
    p = os.path.join(HERE, "index.html")
    if not os.path.exists(p):
        add("ERROR", "页面", "index.html 不存在")
        return
    with open(p, encoding="utf-8") as f:
        html = f.read()
    left = re.findall(r"__[A-Z_]+_PLACEHOLDER__", html)
    if left:
        add("ERROR", "页面", "存在未替换占位符：%s" % set(left))
    m = re.search(r'const VERSION = "([^"]+)"', html)
    print("  页面版本 %s，大小 %.2f MB" % (m.group(1) if m else "?", len(html.encode()) / 1048576))
    if "分层串关" not in html:
        add("WARN", "页面", "未发现分层串关渲染，可能未重建")

    # 版本一致性：网页版本是唯一真相源，APK 必须跟随。
    # 一旦分叉（如曾经的「网页 v1.2.2 / App v1.5」），用户无法判断手机里是否是最新版。
    try:
        import version_sync
        ver = version_sync.page_version()
        name, code = version_sync.apk_version()
        want = version_sync.ver_code(ver)
        if name != ver.lstrip("v") or code != want:
            add("ERROR", "版本", "APK 版本 %s(%s) 与网页 %s 不一致（应为 %s/%d）"
                % (name, code, ver, ver.lstrip("v"), want))
        else:
            print("  APK 版本 %s(%d) 与网页一致" % (name, code))
    except Exception as e:
        add("WARN", "版本", "版本一致性检查失败：%s" % e)


def check_isolation():
    print("· 检查 7/7 数据源隔离（独立源不得污染核心数据/模型）")
    data = load("matches.json", {})
    ms = (data or {}).get("matches", [])
    leaked = [m.get("num") for m in ms if isinstance(m, dict) and "odds_bsd" in m]
    if leaked:
        add("ERROR", "隔离", "核心 matches.json 仍含 odds_bsd 字段（%d 场），未与独立源解耦" % len(leaked))
    else:
        print("  核心 matches.json 不含 odds_bsd（已解耦）")

    # 独立源文件结构校验（存在即校验合法性；缺失不报错，属正常降级）
    obsd = load("odds_bsd.json")
    if obsd is not None:
        if not isinstance(obsd.get("matches"), list) or not obsd.get("independent"):
            add("WARN", "隔离", "odds_bsd.json 结构异常")
        else:
            print("  独立源 odds_bsd.json：%d 场（The Odds API）" % len(obsd.get("matches", [])))
    fd = load("fd_data.json")
    if fd is not None:
        if not isinstance(fd.get("matches"), list) or fd.get("source") != "football-data.org":
            add("WARN", "隔离", "fd_data.json 结构异常")
        else:
            print("  独立源 fd_data.json：%d 场（football-data.org）" % len(fd.get("matches", [])))
    # 高手荐单（独立展示源）：只校验存在时不污染核心数据
    exp = load("expert_recommendations.json")
    if exp is not None and not isinstance(exp.get("recommendations"), list):
        add("WARN", "隔离", "expert_recommendations.json 结构异常")


def main():
    strict = "--strict" in sys.argv
    print("=" * 60)
    print("竞彩足球数据分析台 · 数据自检  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    data = load("matches.json", {})
    hist = load("history.json", {})
    check_matches(data)
    check_odds(data)
    check_prob(data)
    check_parlay(data)
    check_history(hist)
    check_page()
    check_isolation()

    errs = [i for i in issues if i["level"] == "ERROR"]
    warns = [i for i in issues if i["level"] == "WARN"]
    report = {
        "checkedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "errorCount": len(errs),
        "warnCount": len(warns),
        "issues": issues,
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("-" * 60)
    print("结果：ERROR %d 项 / WARN %d 项 → selftest_report.json" % (len(errs), len(warns)))
    if errs:
        return 1
    if warns and strict:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
