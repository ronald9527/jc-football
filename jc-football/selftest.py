#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— 数据自检（每日自动跑，发现问题退出码非 0）

检查 6 大类：
  1. 数据完整性：必填字段、编号唯一、日期合法
  2. 赔率合理性：范围 1.05~25、返奖率落在 85%~92%（异常说明接口数据错乱）
  3. 概率一致性：胜平负三者概率之和接近 100%（±3pt）
  4. 串关合理性：拒绝蚊子肉（单场 >= 1.60）、总赔 >= 2.2、EV/命中率字段存在
  4.5 串关历史完整性：防截断（页面注入量≥源文件）、防消失（昨天组数完整可见）、
      防快照丢失（最新记录带 snapshot）——2026-10-13「昨天推荐翻不到」事故加固
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
    for key in ("parlay2", "parlay3", "parlay4"):
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
        if min_o < 1.60:
            add("WARN", "串关", "%s 存在蚊子肉单场 %.2f（下限 1.60）" % (key, min_o))
        if tot < 2.2:
            add("WARN", "串关", "%s 总赔 %.2f 偏低" % (key, tot))
        for f in ("jointProb", "ev", "rake"):
            if p.get(f) is None:
                add("WARN", "串关", "%s 缺少字段 %s" % (key, f))
        # v1.7.121 证据门禁：负超额腿 = 历史同方向实证命中率低于市场定价，
        # 这类腿不得进入任何一档；被标「推荐」更是乱推荐，直接判 ERROR。
        neg = [l for l in legs if (l.get("edge") or 0) < -1.0]
        if neg:
            add("ERROR", "串关", "%s 含 %d 条负超额腿（历史实证差于市场定价，证据准入失效）" % (key, len(neg)))
        if p.get("recommended"):
            if (p.get("realEv") or 0) < -2:
                add("ERROR", "串关", "%s 标为推荐但真实EV %s%% 未达标" % (key, p.get("realEv")))
            if p.get("negEdgeLegs"):
                add("ERROR", "串关", "%s 标为推荐却有负超额腿" % key)
        print("  %s：%s %.2f倍 命中%s%% EV%s%% 真实EV%s%% 负超额腿%d 推荐=%s" % (
            key, p.get("tier"), tot, p.get("jointProb"), p.get("ev"),
            p.get("realEv"), len(neg), p.get("recommended")))
    # ===== v1.7.122 门禁：先有合格单场、才有组合（前缀嵌套 + 统一资格）=====
    # 用户原则「选串关是把单场最有把握，数据支撑，合理，才进行串关组合」。
    # 防止引擎退回「先定 2/3/4 腿档位、再各自凑组合、档位越高门槛越松」。
    _tiers = [(k, sp.get(k)) for k in ("parlay2", "parlay3", "parlay4")]
    _present = [(k, p) for k, p in _tiers if p]
    for _k, _p in _present:
        _ls = _p.get("legs") or []
        # ① 每条腿都必须是合格单场：实证超额≥0、样本≥20、赔率 1.60~3.20
        _bad = [l for l in _ls if (l.get("edge") or 0) < 0
                or (l.get("empN") or 0) < 20
                or not (1.60 <= (l.get("odds") or 0) <= 3.20)]
        if _bad:
            add("ERROR", "串关", "%s 含 %d 条不合格单场（应：超额≥0、样本≥20、赔率1.60~3.20）" % (_k, len(_bad)))
        # ② 把握度排名必须连续 1..n（前缀）
        _ranks = sorted((l.get("gripRank") or 0) for l in _ls)
        if _ranks != list(range(1, len(_ls) + 1)):
            add("ERROR", "串关", "%s 把握度排名不连续 %s（应为 1..%d 前缀）" % (_k, _ranks, len(_ls)))
    # ③ 后档严格包含前档全部腿（嵌套）
    def _sig(legs):
        return set((l.get("num"), l.get("label")) for l in (legs or []))
    for (_ka, _pa), (_kb, _pb) in zip(_present, _present[1:]):
        if not _sig(_pa.get("legs")).issubset(_sig(_pb.get("legs"))):
            add("ERROR", "串关", "%s 未包含 %s 全部腿（前缀嵌套被破坏，又在独立凑组合）" % (_kb, _ka))
    # ④ 总赔随腿数严格递增
    _tos = [_p.get("totalOdds") or 0 for _k, _p in _present]
    if any(x >= y for x, y in zip(_tos, _tos[1:])):
        add("ERROR", "串关", "三档总赔未严格递增 %s" % _tos)
    # ⑤ v1.7.123 竞彩批次隔离：任一组合的所有腿必须来自同一竞彩销售批次。
    # 用户明确要求「别周五的和周六的串起来」——用腿上 num 前缀（周五/周六/周日）
    # 与 batchDate 双重判定；顶层三档与 batches 内每一档都检查。
    import re as _re
    def _batch_violation(legs):
        dates, labels = set(), set()
        for l in (legs or []):
            d = l.get("batchDate")
            n = str(l.get("num") or "")
            mt = _re.match(r"^(周[一二三四五六日])", n)
            lab = l.get("batchLabel") or (mt.group(1) if mt else "")
            if d:
                dates.add(d)
            elif lab:
                dates.add(lab)
            if lab:
                labels.add(lab)
        # 兼容旧单批数据（无批次字段）：不作要求，只检查有标注的情况
        return len(dates) > 1, sorted(dates), sorted(labels)
    for _k, _p in _present:
        _bad, _ds, _ls = _batch_violation(_p.get("legs"))
        if _bad:
            add("ERROR", "串关", "%s 跨竞彩批次组合 %s（禁止把周五和周六串起来）" % (_k, _ds))
    _batches = sp.get("batches")
    if isinstance(_batches, list):
        for _b in _batches:
            _bk = _b.get("batchKey") or _b.get("batchDate")
            for _k in ("parlay2", "parlay3", "parlay4"):
                _p = _b.get(_k)
                if not _p:
                    continue
                _bad, _ds, _ls = _batch_violation(_p.get("legs"))
                if _bad:
                    add("ERROR", "串关", "批次 %s 的 %s 跨批次 %s" % (_bk, _k, _ds))
                # 每档腿的批次必须等于所属批次
                _bd = set(str(l.get("batchDate") or "") for l in (_p.get("legs") or [])
                          if l.get("batchDate"))
                if _bd and _b.get("batchDate") and not _bd.issubset({_b["batchDate"]}):
                    add("ERROR", "串关", "批次 %s 的 %s 混入其它批次腿 %s" % (_bk, _k, sorted(_bd)))
        print("  批次隔离：%d 个竞彩批次，逐档核对同批次完成" % len(_batches))
    # ⑥ 合格单场不足 N 条时该档必须为空（不得硬凑）
    _qn = sp.get("qualifiedCount")
    if isinstance(_qn, int):
        for _k, _n in (("parlay2", 2), ("parlay3", 3), ("parlay4", 4)):
            if _qn < _n and sp.get(_k):
                add("ERROR", "串关", "合格单场仅 %d 条，%s 却仍出档（硬凑）" % (_qn, _k))
    print("  合格单场 %s 条 | 前缀嵌套 / 总赔递增 / 排名连续 检查完成" % sp.get("qualifiedCount"))
    if found == 0:
        add("WARN", "串关", "今日无串关推荐（候选不满足档位门槛）")


def check_parlay_history(data):
    """串关历史「防截断 / 防消失」专项检查（2026-10-13 事故加固）。

    事故回顾：check_parlay_history() 曾只返回最近 20 条注入页面，
    昨天 14 组只露出 6 组；今天组数继续涨，直接把昨天的推荐全部挤出页面，
    用户翻查时「昨天的推荐不见了」，被误认为数据作假。
    这类 bug 静默且直接伤害信任，必须每轮跑批自动盯防：
      防线1 页面注入量不得少于源文件记录数（任何截断都是事故）
      防线2 最近一个已过去日期（昨天）的组数必须完整可见
      防线3 最新一条记录必须带 snapshot 快照（保存逻辑被改回旧版会丢失回放字段）
    """
    print("· 检查 4.5/6 串关历史完整性（防截断/防消失）")
    src = load("parlay_history.json")
    if not isinstance(src, dict) or not src.get("records"):
        return  # 全新部署无历史文件，不报错
    src_recs = src["records"]
    ph = (data or {}).get("parlayHistory") or {}
    inj = ph.get("records") or []
    if not inj:
        add("ERROR", "串关历史", "matches.json 未注入串关历史（页面历史区将空白）")
        return

    # 防线1：页面注入量不得少于源文件（源文件是唯一真相，少显示=截断复发）
    if len(inj) < len(src_recs):
        add("ERROR", "串关历史",
            "页面只注入 %d/%d 组，存在截断——历史推荐会残缺，昨天的组可能翻不到"
            % (len(inj), len(src_recs)))

    # 防线2：最近一个已过去日期（昨天）的组数必须完整可见
    src_cnt, inj_cnt = {}, {}
    for r in src_recs:
        d = r.get("date")
        if d:
            src_cnt[d] = src_cnt.get(d, 0) + 1
    for r in inj:
        d = r.get("date")
        if d:
            inj_cnt[d] = inj_cnt.get(d, 0) + 1
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    past = sorted(d for d in src_cnt if d < today)
    if past:
        y = past[-1]
        if inj_cnt.get(y, 0) < src_cnt[y]:
            add("ERROR", "串关历史",
                "%s 的推荐页面只显示 %d/%d 组——昨日的组被挤出，用户无法翻查对账"
                % (y, inj_cnt.get(y, 0), src_cnt[y]))
        else:
            print("  %s 推荐 %d 组完整可见（防消失 ✓）" % (y, src_cnt[y]))

    # 防线3：最新一条记录必须带 snapshot（保存逻辑若被改回旧版，回放丢字段）
    newest = max(src_recs, key=lambda r: r.get("savedAt") or "")
    if newest.get("date") == today and "snapshot" not in newest:
        add("WARN", "串关历史",
            "今日最新记录（%s）无 snapshot 快照，锁定回放将丢失卡片字段，"
            "检查 save_parlay_history 是否被改动" % newest.get("savedAt"))
    print("  串关历史：源文件 %d 组 / 页面注入 %d 组" % (len(src_recs), len(inj)))


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

    # 竞彩篮球（独立源，v1.7.29）：与足球编号空间不同（篮球 301 起 / 足球 001 起），
    # 校验核心数据零混入 + 篮球文件结构合法
    bk = load("basketball.json")
    if bk is not None:
        bms = bk.get("matches") if isinstance(bk, dict) else None
        if not isinstance(bms, list):
            add("ERROR", "隔离", "basketball.json 结构异常（matches 应为列表）")
        else:
            leak = [m.get("num") for m in ms
                    if isinstance(m, dict) and ("mnl" in m or "hilo" in m or "wnm" in m)]
            if leak:
                add("ERROR", "隔离", "核心 matches.json 混入篮球字段（%d 场）：%s" % (len(leak), leak[:5]))
            print("  独立源 basketball.json：%d 场（篮球编号 %s~%s）"
                  % (len(bms), bms[0].get("num") if bms else "-",
                     bms[-1].get("num") if bms else "-"))
            check_basketball_freshness(bk)


def check_basketball_freshness(bk):
    """篮球数据新鲜度：抓取静默失败的可见性兜底（v1.7.38）。

    背景（2026-10-22 事故）：fetch_basketball.py 按隔离设计「失败只跳过篮球、
    exit 0 保足球主流程」，失败信息只进 stderr；而自举代码包里携带的篮球数据
    快照会让部署继续 Serve 旧数据——结果篮球数据停了 44 小时无人知晓。
    本检查让「停更」在每轮自检里显形，随任务汇报与 selftest_report.json 曝光。

    阈值按任务节奏（9-23 点每 30 分钟一轮、夜间停跑）设定：
      10-22 点（白天）：>3h WARN / >8h ERROR
      9 点 / 23 点（交接）：>12h WARN / >16h ERROR（23 点停跑到次日 9 点约 10h 属正常）
      0-8 点（夜间停跑）：>12h WARN / >24h ERROR
    """
    upd = bk.get("updatedAt") if isinstance(bk, dict) else None
    if not upd:
        add("WARN", "隔离", "basketball.json 无 updatedAt，无法判断新鲜度")
        return
    try:
        t = datetime.datetime.strptime(str(upd)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        add("WARN", "隔离", "basketball.json updatedAt 无法解析: %r" % upd)
        return
    now = datetime.datetime.now()
    age_h = (now - t).total_seconds() / 3600.0
    if age_h < 0:                      # 沙箱时钟偏差，向前漂移视为新鲜
        print("  篮球数据新鲜度：updatedAt 晚于本机时钟（时钟偏差，跳过）")
        return
    hour = now.hour
    if 10 <= hour <= 22:
        warn_h, err_h = 3, 8
    elif hour in (9, 23):
        warn_h, err_h = 12, 16
    else:
        warn_h, err_h = 12, 24
    tag = "最后更新 %s（%.1f 小时前）" % (upd, age_h)
    if age_h > err_h:
        add("ERROR", "隔离", "篮球数据已停更 %s——fetch_basketball 连续失败或被预算"
            "门槛跳过，页面在 Serve 旧快照，需立即排查" % tag)
    elif age_h > warn_h:
        add("WARN", "隔离", "篮球数据超过 %d 小时未刷新（%s）" % (warn_h, tag))
    else:
        print("  篮球数据新鲜度：%.1f 小时（正常，更新于 %s）" % (age_h, upd))


def check_basketball_ai():
    """篮球预测/串关/统计（v1.7.44）新鲜度与隔离兜底。

    让「篮球 AI 停更 / 串关未生成 / 统计缺失」在每轮自检显形，并守住零耦合红线：
    篮球三份文件不得混入足球字段、预测 id 必须全部来自 basketball.json。
    """
    print("· 检查 8 篮球预测/串关/统计（v1.7.44）新鲜度与隔离")
    bk = load("basketball.json", {})
    bk_ms = (bk or {}).get("matches", []) if isinstance(bk, dict) else []
    # v1.7.119：在售口径改为「尚未开赛且无赛果」。此前把已开赛但赛果未出、
    # 以及停更期残留的旧比赛都算进分母，门禁生效后必然误报「AI 覆盖不足」。
    _bk_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    bk_unsold_ids = {m.get("id") for m in bk_ms
                     if isinstance(m, dict) and not m.get("score")
                     and (m.get("kickoff") or "")[:16] > _bk_now}

    # 1) 预测篮球_ai.json
    ai = load("basketball_ai.json")
    if ai is None:
        add("WARN", "隔离", "basketball_ai.json 缺失——篮球预测未生成（降级：专区 AI 预测为空）")
    else:
        preds = ai.get("predictions") if isinstance(ai, dict) else None
        if not isinstance(preds, list):
            add("ERROR", "隔离", "basketball_ai.json.predictions 结构异常（应为列表）")
        else:
            if not preds:
                add("WARN", "隔离", "篮球 AI 预测为空——大概率 fetch_basketball_ai 失败，专区预测空白")
            else:
                # 覆盖度：当前在售场应有预测（允许个别比赛四玩法全缺赔率而落榜）
                covered = sum(1 for p in preds if p.get("id") in bk_unsold_ids)
                if bk_unsold_ids and covered < len(bk_unsold_ids):
                    add("WARN", "隔离", "篮球 AI 覆盖不足：%d/%d 在售场有预测"
                        % (covered, len(bk_unsold_ids)))
                else:
                    print("  篮球 AI 预测：%d 场（覆盖在售 %d 场）" % (len(preds), len(bk_unsold_ids)))
                # 四玩法健全：每场含 mnl/hdc/hilo/wnm 且赔率>1
                bad = 0
                for p in preds:
                    pls = p.get("plays") or {}
                    m = next((x for x in bk_ms if x.get("id") == p.get("id")), {})
                    for pk in ("mnl", "hdc", "hilo", "wnm"):
                        if not isinstance(m.get(pk), dict):
                            continue
                        if pk not in pls or not (pls[pk].get("odds", 0) > 1):
                            bad += 1
                if bad:
                    add("WARN", "隔离", "篮球 AI 有 %d 处「有赔率却缺预测」" % bad)
                # 隔离：不得混入足球独立源字段
                if any("odds_bsd" in p for p in preds):
                    add("ERROR", "隔离", "basketball_ai.json 混入 odds_bsd 字段（隔离破防）")
                # 样本薄提示应随预测下发（诚实）
                if ai.get("note"):
                    print("  样本薄提示已下发：%s" % ai["note"][:30] + "…")

    # 2) 串关 basketball_parlay_history.json
    pf = load("basketball_parlay_history.json")
    if pf is None:
        add("WARN", "隔离", "basketball_parlay_history.json 缺失——篮球串关未生成（降级：专区串关为空）")
    else:
        recs = pf.get("records") if isinstance(pf, dict) else None
        if not isinstance(recs, list):
            add("ERROR", "隔离", "basketball_parlay_history.json.records 结构异常")
        else:
            if not recs:
                add("WARN", "隔离", "篮球串关历史为空——fetch_basketball_parlay 未落盘（降级：专区串关空白）")
            else:
                # 最新一组应为近期（今天或昨天），证明串关每日生成
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                yest = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                # 修复：取所有记录中日期最大的一组（recs[0] 未必是日期最新，
                # 文件中今日组常排在后面，直接取 [0] 会误把旧日期当最新 → 虚假「停更」告警）
                latest = max((r.get("date") for r in recs if r.get("date")), default=None)
                if latest not in (today, yest):
                    add("WARN", "隔离", "篮球串关最新一组 %s 非今/昨（%s）——串关可能停更"
                        % (latest, today))
                else:
                    print("  篮球串关历史：%d 组（最新 %s，命中 %d/错 %d/待 %d）"
                          % (len(recs), latest, pf.get("hit", 0), pf.get("miss", 0), pf.get("pending", 0)))
                # 诚实标记：realEv<0 必 recommended=False
                dishonest = [r for r in recs
                             if isinstance(r.get("realEv"), (int, float)) and r["realEv"] < 0
                             and r.get("recommended") is not False]
                if dishonest:
                    add("ERROR", "隔离", "篮球串关有 %d 组 realEv<0 却标记推荐（诚实破防）" % len(dishonest))
                # 结构对齐足球：含 legs/engineVer
                if any("engineVer" not in r for r in recs):
                    add("WARN", "隔离", "篮球串关记录缺 engineVer（版本溯源缺失）")

    # 3) 统计 basketball_stats.json
    st = load("basketball_stats.json")
    if st is None:
        add("WARN", "隔离", "basketball_stats.json 缺失——篮球历史统计未生成（降级：专区统计空白）")
    else:
        if not isinstance(st, dict) or not isinstance(st.get("roi"), dict):
            add("ERROR", "隔离", "basketball_stats.json 结构异常（缺 roi）")
        else:
            sc = (st.get("sample") or {}).get("scored", 0)
            if sc <= 0:
                add("WARN", "隔离", "篮球统计样本为 0——历史档案无赛果回填")
            else:
                print("  篮球历史统计：样本 %d 条有赛果，ROI 含 %d 玩法"
                      % (sc, len(st.get("roi", {}))))
            if not isinstance(st.get("leagues"), dict):
                add("WARN", "隔离", "basketball_stats.json 缺 leagues 统计")


def check_basketball_backtrack():
    """篮球 AI 预测 / 串关 历史回溯（v1.7.46 新增）完整性。

    让「篮球回溯库缺失 / 串关结算卡死（永远全 pending）」显形：
    - basketball_ai_accuracy.json 必须存在且 history 非空（回溯库已累积）
    - basketball_parlay_history.json 若存在「本应结算却仍 pending」的记录
      （腿 id 命中了有赛果的档案）则判 ERROR——v1.7.46 前 save_parlay_history
      漏写 leg.id/kickoff，结算恒失败 → 全 pending，回溯形同虚设。
    """
    print("· 检查 8.5 篮球 AI/串关历史回溯（v1.7.46）")
    # 1) AI 预测回溯库
    acc = load("basketball_ai_accuracy.json")
    if acc is None:
        add("WARN", "隔离", "basketball_ai_accuracy.json 缺失——篮球 AI 预测回溯未生成"
            "（降级：专区无历史回溯）")
    else:
        h = acc.get("history") if isinstance(acc, dict) else None
        if not isinstance(h, list) or not h:
            add("WARN", "隔离", "篮球 AI 预测回溯库为空——尚未累积任何回溯记录")
        else:
            settled = [r for r in h if r.get("topHit") in (True, False)]
            print("  篮球 AI 预测回溯库：%d 条（已结算 %d 条）" % (len(h), len(settled)))

    # 2) 串关结算不应卡死
    pf = load("basketball_parlay_history.json")
    bkh = load("basketball_history.json", {})
    bkrec = (bkh or {}).get("records", {}) if isinstance(bkh, dict) else {}
    if isinstance(pf, dict) and isinstance(pf.get("records"), list):
        recs = pf["records"]
        stuck = 0
        for r in recs:
            if r.get("result") != "pending":
                continue
            for leg in (r.get("legs") or []):
                rid = leg.get("id")
                if rid and isinstance(bkrec.get(rid, {}).get("score"), list):
                    stuck += 1
                    break
        if stuck:
            add("ERROR", "隔离", "篮球串关有 %d 组本应已结算却仍 pending"
                "（结算逻辑故障，回溯卡死）" % stuck)
        else:
            print("  篮球串关结算：无卡死（hit %d / miss %d / pending %d）"
                  % (pf.get("hit", 0), pf.get("miss", 0), pf.get("pending", 0)))


def check_scorepred_freshness(sp):
    """比分预测数据新鲜度：配置丢失/停更的可见性兜底（v1.7.39）。

    背景（2026-10-21~22 事故）：任务沙箱丢失 fd_config.json（密钥文件被自举包
    排除、沙箱重置后不回），fetch_scores.py 无密钥路径天天把 score_predict.json
    抹成空结构再部署——「比分预测」Tab 空了 5 天无人知晓（用户侧症状：
    「又几天没更新」）。本检查让停更/缺配置在每轮自检显形。

    与篮球检查的差异：比分预测的正常节奏是任务每 30 分钟一轮都刷新
    （--keep-cache 也照样拉展示窗并写 updatedAt），阈值同篮球；另外
    note 里出现「未配置 Key」通常直接 ERROR——那是确定性停更。但 v1.7.45 起缺 Key
    会改走「本地竞彩赔率兜底」（note 含『兜底/本地竞彩』），数据每日随赛程刷新，
    属正常模式，不再判停更，仅走 updatedAt 新鲜度检查（兜底也失效则照样报错）。
    """
    if not isinstance(sp, dict):
        add("WARN", "隔离", "score_predict.json 缺失或结构异常，无法判断新鲜度")
        return
    note = str(sp.get("note") or "")
    # v1.7.45：缺 football-data Key 时 fetch_scores 改走「本地竞彩赔率兜底」，
    # 数据每日随赛程刷新（note 含『兜底/本地竞彩』），此时不应判停更——改走 updatedAt
    # 新鲜度检查即可（兜底若也失效，updatedAt 会变旧，新鲜度检查照样报错）。
    # 仅当「未配置 Key」且『非兜底』（兜底彻底失败、无数据）才确定停更。
    is_fallback = ("兜底" in note) or ("本地竞彩" in note) or \
                  str(sp.get("source") or "").startswith("本地竞彩")
    if "未配置" in note and not is_fallback:
        add("ERROR", "隔离", "比分预测未配置 football-data.org Key（fd_config.json 或环境变量 "
            "FD_API_KEY）且本地竞彩兜底也不可用——模块已停更，页面 Serve 的是旧数据；"
            "请在任务环境变量加 FD_API_KEY 恢复")
        return
    upd = sp.get("updatedAt")
    if not upd:
        add("WARN", "隔离", "score_predict.json 无 updatedAt，无法判断新鲜度")
        return
    try:
        t = datetime.datetime.strptime(str(upd)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        add("WARN", "隔离", "score_predict.json updatedAt 无法解析: %r" % upd)
        return
    now = datetime.datetime.now()
    age_h = (now - t).total_seconds() / 3600.0
    if age_h < 0:                      # 沙箱时钟偏差，向前漂移视为新鲜
        print("  比分预测新鲜度：updatedAt 晚于本机时钟（时钟偏差，跳过）")
        return
    hour = now.hour
    if 10 <= hour <= 22:
        warn_h, err_h = 3, 8
    elif hour in (9, 23):
        warn_h, err_h = 12, 16
    else:
        warn_h, err_h = 12, 24
    n_match = len(sp.get("matches") or [])
    tag = "最后更新 %s（%.1f 小时前，展示窗 %d 场）" % (upd, age_h, n_match)
    if age_h > err_h:
        add("ERROR", "隔离", "比分预测已停更 %s——fetch_scores 连续失败/缺 Key/被预算"
            "门槛跳过，页面在 Serve 旧数据，需立即排查" % tag)
    elif age_h > warn_h:
        add("WARN", "隔离", "比分预测超过 %d 小时未刷新（%s）" % (warn_h, tag))
    elif n_match == 0:
        add("WARN", "隔离", "比分预测展示窗 0 场（%s）——确认联赛覆盖/接口是否正常" % tag)
    else:
        if is_fallback:
            print("  比分预测新鲜度：%.1f 小时（正常·本地竞彩兜底模式，更新于 %s，%d 场）"
                  % (age_h, upd, n_match))
        else:
            print("  比分预测新鲜度：%.1f 小时（正常，更新于 %s，%d 场）" % (age_h, upd, n_match))


def check_pipeline_safeguards():
    """流水线自愈护栏静态校验（v1.7.46）。

    防止本次修复的两处根因被未来改动误删：
    (1) run_daily.sh 必须有「篮球/比分预测陈旧豁免强制重抓」（步骤2.5）——
        否则单轮被预算门槛跳过就会再次停更；
    (2) 部署决策 SHA 必须含 5 个篮球文件——否则篮球更新了也不触发上线。
    """
    print("· 检查 9 流水线自愈护栏（v1.7.46）")
    try:
        src = open("run_daily.sh", encoding="utf-8").read()
    except Exception:
        add("WARN", "隔离", "run_daily.sh 不可读，跳过自愈护栏校验")
        return
    # (1) 陈旧恢复护栏存在（_py_age 陈旧判定 + 64800s=18h 阈值 + 触发 fetch_basketball）
    if "fetch_basketball.py" in src and "_py_age" in src and "64800" in src:
        print("  篮球/比分预测陈旧豁免重抓护栏：存在")
    else:
        add("ERROR", "隔离", "run_daily.sh 缺少「陈旧豁免强制重抓」护栏（v1.7.46 根治项），"
            "篮球/比分预测将再次面临停更风险")
    # (2) 部署指纹含 5 个篮球文件（+ v1.7.46 篮球 AI 回溯库）
    missing = [f for f in ("basketball.json", "basketball_history.json",
                            "basketball_ai.json", "basketball_stats.json",
                            "basketball_parlay_history.json", "basketball_ai_accuracy.json")
               if ('filehash("%s")' % f) not in src]
    if not missing:
        print("  部署指纹含 5 个篮球文件：是")
    else:
        add("ERROR", "隔离", "run_daily.sh 部署指纹漏算篮球文件：%s（篮球更新不触发上线）"
            % ", ".join(missing))


def check_good_rate(data, hist):
    """检查 10 正路推荐正确率统计（v1.7.124）。

    重点：口径与 history.json 按 judge_hit() 同规则复算比对，防止统计脚本
    悄悄换口径、把未结算/反指场次混进来把正确率做高；再校验竞彩批次隔离、
    独立文件一致性、前端与流水线接线。
    """
    print("· 检查 10 正路推荐正确率统计（v1.7.124）")
    s = (data or {}).get("goodRateStats")
    if not isinstance(s, dict) or not s.get("overall") or not s.get("today"):
        add("ERROR", "正确率统计",
            "matches.json 缺少 goodRateStats（gen_good_rate.py 未跑，或构建前被 fetch_scores 重写覆盖）")
        return
    o, t = s["overall"], s["today"]

    # (1) 与 history.json 官方开奖回溯复算：样本、命中数、正确率必须完全一致
    recs = (hist or {}).get("records", [])
    core = [r for r in recs if r.get("hitFlag") in ("hit", "miss")
            and (r.get("verdict") or {}).get("t") == "good"]
    hits = sum(1 for r in core if r["hitFlag"] == "hit")
    if o.get("n") != len(core) or o.get("hit") != hits:
        add("ERROR", "正确率统计",
            "样本口径与 history.json 不一致：统计 n=%s/hit=%s，复算 n=%s/hit=%s"
            % (o.get("n"), o.get("hit"), len(core), hits))
    elif len(core):
        rate = round(100.0 * hits / len(core), 2)
        if abs(float(o.get("rate") or 0) - rate) > 0.01:
            add("ERROR", "正确率统计",
                "正确率与复算不一致：显示 %s%%，复算 %.2f%%" % (o.get("rate"), rate))
        else:
            print("  口径复算一致：%d 战 %d 中 = %.2f%%" % (len(core), hits, rate))
    if o.get("n") != (o.get("hit") or 0) + (o.get("miss") or 0):
        add("ERROR", "正确率统计", "n ≠ hit + miss，统计结构不自洽")
    w = o.get("wilson95") or []
    if len(w) != 2 or not (0 <= w[0] <= float(o.get("rate") or 0) <= w[1] <= 100):
        add("ERROR", "正确率统计", "Wilson 95% 置信区间异常：%s" % w)
    if (o.get("pricedN") or 0) > (o.get("n") or 0):
        add("ERROR", "正确率统计", "有赔率样本数大于总样本数")

    # (2) 诚实口径必填：不能只报一个漂亮命中率
    for k in ("marketDeVig", "breakEven", "roi", "vsDeVig", "vsBreakEven",
              "testVsDeVig", "testVsBreakEven"):
        if o.get(k) is None:
            add("ERROR", "正确率统计", "缺少关键口径字段 %s（去水隐含/盈亏平衡线/ROI 必须齐全）" % k)
    if "不构成盈利依据" not in (s.get("conclusion") or ""):
        add("ERROR", "正确率统计", "结论缺少「不构成盈利依据」的诚实声明")
    if not (s.get("notes") or []):
        add("ERROR", "正确率统计", "缺少可核对的统计口径说明")

    # (3) 今日竞彩批次隔离：绝不混入周六/周日
    picks = t.get("picks") or []
    if t.get("goodCount") != len(picks):
        add("ERROR", "正确率统计", "今日正路场次数与清单条数不一致")
    prefix = t.get("batchPrefix") or ""
    cross = [p.get("num") for p in picks
             if prefix and not str(p.get("num") or "").startswith(prefix)]
    if cross:
        add("ERROR", "正确率统计", "今日清单混入非本竞彩批次的场次：%s" % ", ".join(map(str, cross)))
    if (t.get("pendingCount") or 0) + (t.get("settledCount") or 0) != len(picks):
        add("ERROR", "正确率统计", "今日已开奖 + 未开奖 ≠ 正路清单条数")
    unsettled_bad = [p.get("num") for p in picks if not p.get("settled") and p.get("hit") is not None]
    if unsettled_bad:
        add("ERROR", "正确率统计", "未开奖场次被错误计入命中：%s" % ", ".join(map(str, unsettled_bad)))

    # (4) 独立统计文件与页面内嵌字段必须同源
    ind = load("good_rate_stats.json")
    if not isinstance(ind, dict):
        add("ERROR", "正确率统计", "缺少独立统计文件 good_rate_stats.json")
    else:
        def sig(x):
            d = json.loads(json.dumps(x))
            d.pop("generatedAt", None)
            return json.dumps(d, ensure_ascii=False, sort_keys=True)
        if sig(ind) != sig(s):
            add("ERROR", "正确率统计", "good_rate_stats.json 与 matches.json.goodRateStats 不同源")

    # (5) 前端模块与每日流水线接线
    try:
        tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    except Exception:
        tpl = ""
    if "renderGoodRate" not in tpl or 'id="goodRateView"' not in tpl:
        add("ERROR", "正确率统计", "template.html 缺少正路统计渲染模块")
    try:
        sh = open(os.path.join(HERE, "run_daily.sh"), encoding="utf-8").read()
    except Exception:
        sh = ""
    if "gen_good_rate.py" not in sh:
        add("ERROR", "正确率统计", "run_daily.sh 未接入 gen_good_rate.py，刷新后统计不会更新")
    if '"goodRateStats"' not in sh:
        add("ERROR", "正确率统计", "部署指纹未纳入 goodRateStats，统计变化不会触发上线")
    print("  今日批次 %s（%s）：%d 场 / 正路 %d 场 / 已开奖 %d 场"
          % (t.get("batchDate"), t.get("batchLabel"), t.get("totalMatches"),
             len(picks), t.get("settledCount")))


def check_vzhan_batch(data):
    """检查 11 V站比分按竞彩批次呈现（v1.7.125）。

    用户「周六的 V站 比分呢」暴露的展示层问题：数据齐全但「今日竞彩」被
    UTC 最新日期误标成明天、V站 59 场周六周日混排且默认折叠。这里校验：
      · vzhan_data.json 每场都有明确的竞彩批次（date + 编号前缀一致）
      · 竞彩当天批次（safeParlay.batchDate）在 V站 数据里存在且有带比分场次
      · template.html 已接入 jcTodayBatch() 权威口径与 V站 批次切换，且不再折叠
    """
    print("· 检查 11 V站比分竞彩批次隔离（v1.7.125）")
    vz = load("vzhan_data.json")
    if not isinstance(vz, dict) or not isinstance(vz.get("matches"), list):
        add("ERROR", "V站批次", "vzhan_data.json 缺失或 matches 非列表")
        return
    ms = vz["matches"]
    if not ms:
        add("WARN", "V站批次", "V站情报 0 场（抓取失败会保留上次数据，请留意）")
        return
    # (1) 每场必须能归入唯一竞彩批次，且编号前缀与 date 的星期一致
    wk = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    import datetime as _dt
    bad_pref = []
    groups = {}
    for m in ms:
        d = m.get("date") or "未排期"
        groups.setdefault(d, []).append(m)
        pre = str(m.get("jcNum") or "")[:2]
        if d != "未排期" and pre:
            try:
                w = wk[_dt.date.fromisoformat(d).weekday()]
                if w != pre:
                    bad_pref.append("%s(%s≠%s)" % (m.get("jcNum"), pre, w))
            except Exception:
                pass
    if bad_pref:
        add("ERROR", "V站批次", "编号前缀与赛程日期星期不一致：%s" % ", ".join(bad_pref[:6]))

    # (2) 当天批次在 V站 数据里必须有带比分场次（用户要看的「周六 V站比分」）
    bd = ((data or {}).get("safeParlay") or {}).get("batchDate")
    if bd:
        sub = groups.get(bd, [])
        n_pred = sum(1 for m in sub if m.get("hasPred"))
        if not sub:
            add("ERROR", "V站批次", "竞彩当天批次 %s 在 V站 数据中无任何场次" % bd)
        elif n_pred == 0:
            add("ERROR", "V站批次", "竞彩当天批次 %s 共 %d 场，但无一场带 V站 预测比分" % (bd, len(sub)))
        else:
            print("  竞彩当天批次 %s：%d 场 / 带 V站比分 %d 场 / 在售批次 %s"
                  % (bd, len(sub), n_pred, ", ".join(sorted(groups))))
    # (3) 前端三处日期逻辑接线
    tp = os.path.join(HERE, "template.html")
    if os.path.exists(tp):
        with open(tp, encoding="utf-8") as f:
            t = f.read()
        if "function jcTodayBatch" not in t:
            add("ERROR", "V站批次", "template.html 缺少 jcTodayBatch() 竞彩当天批次权威口径")
        if "vzbatch-chip" not in t:
            add("ERROR", "V站批次", "template.html 缺少 V站 批次切换条")
        if "const _jcToday = jcTodayBatch();" not in t:
            add("ERROR", "V站批次", "比分预测 Tab 的 todayDate 仍未接入竞彩当天批次")
        # V站情报区不应再默认折叠
        import re as _re
        seg = _re.search(r"V站·呼死狗情报.{0,80}", t)
        if seg and 'class="sp-sec collapsed"' in t[t.rfind('<div class="sp-sec', 0, seg.start()):seg.start() + 20]:
            add("WARN", "V站批次", "V站情报区疑似仍默认折叠")


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
    check_parlay_history(data)
    check_history(hist)
    check_page()
    check_isolation()
    check_basketball_ai()
    check_basketball_backtrack()
    check_scorepred_freshness(load("score_predict.json"))
    check_pipeline_safeguards()
    check_good_rate(data, hist)
    check_vzhan_batch(data)

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
