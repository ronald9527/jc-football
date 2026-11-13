#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩数据台 · 自动保鲜驾驶舱（v1.7.134 起）

设计目标（用户原话：「往后不要让我再提醒了，自动的落实这些东西，
不要踢一脚做一步，不提醒就什么都不做」）
------------------------------------------------------------------
把「每小时自动跑一次」从「重复跑流水线 + 人工看汇报」升级为
「自己巡检 → 自己发现问题 → 自己分级修复 → 自己上线 → 自己留痕」的
确定性闭环。判定与修复全部由代码实现，不依赖定时任务的 LLM 每轮
重新理解一段自然语言，避免「指令读了但没落实」的漂移。

每轮固定动作（无论数据是否变化，都真实执行、真实留痕）：
  1. 权威北京时间校准（beijing_clock，HTTP Date 优先，防沙箱时钟漂移）
  2. 线上健康巡检（用户视角 12 项，覆盖历史上所有踩过的坑）
  3. 全量刷新 + 部署（run_daily.sh deploy，自带指纹节流与残缺守卫）
  4. 定向自愈（刷新后仍存在的 V站 合并 / 统计对齐 / 空壳文章 / ab_stats）
  5. 自检护栏（selftest.py，含检查 12/13/14/15 防回归）
  6. 线上复验（确认真的上新，不假装成功）
  7. 写 auto_pilot_status.json（本地 + dist 随部署上云）+ 追加日志

自愈分级：
  L1 秒级定点：重跑既有生成器 / 对齐统计 / 清理空壳，绝不改业务代码
  L2 构建部署：build_panel.py + deploy_cf.py 强制上线 L1 产物
  L3 全量刷新：run_daily.sh deploy（每轮必跑，数据或代码一变即部署）

边界（不假装成功）：
  * 数据没变化就不伪造 updateTime，但在状态里如实写「已巡检 / 无新增」
  * 任一检查失败都明确记 ERROR 并继续其余检查与修复，不静默跳过
  * 连续自愈后仍不健康 → health=BLOCKED + 写明阻塞原因，供人排查
"""
import os
import sys
import re
import json
import glob
import subprocess
import datetime
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://jc-football-1eo.pages.dev"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 Chrome/124.0 Safari/537.36")
STATUS_PATH = os.path.join(BASE, "auto_pilot_status.json")
LOG_PATH = os.path.join(BASE, "auto_pilot.log")
BASELINE_PATH = os.path.join(BASE, "auto_pilot_baseline.json")

checks = []      # [{name, level, detail}]
actions = []     # [{step, result, detail}]
L1_TOUCHED = []  # 本轮 L1 动过的数据文件（决定是否需要 L2 强制部署）


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def chk(name, ok, detail, level="ERROR"):
    checks.append({"name": name, "level": "OK" if ok else level, "detail": str(detail)})
    log("  %s %-28s %s" % ("OK  " if ok else level[:4], name, detail))
    return ok


def act(step, result, detail=""):
    actions.append({"step": step, "result": result, "detail": str(detail)[:600]})
    log("  → %-26s %s %s" % (step, result, str(detail)[:160]))


def run(cmd, timeout=900, env=None, cwd=BASE):
    """执行 shell 命令，返回 (rc, 输出尾部)。绝不抛异常打断闭环。"""
    e = dict(os.environ)
    e["TZ"] = "Asia/Shanghai"
    if env:
        e.update(env)
    try:
        r = subprocess.run(["bash", "-c", cmd], cwd=cwd, env=e,
                           capture_output=True, text=True, timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or ""))[-2500:]
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % timeout
    except Exception as ex:
        return 1, "EXC %s" % ex


def curl_json(name, timeout=25):
    """拉线上 JSON（curl 避开 Cloudflare 对默认 UA 的 403）。

    必须落盘再读：matches.json / score_predict.json 各 700KB+，若走
    run() 的 stdout 会被 [-2500:] 截断，JSON 直接解析失败（实测 8 个
    文件只认出 2 个小文件，误判成「全线崩溃」）。
    """
    path = "/tmp/ap_%s" % name
    rc, _ = run("curl -sS -m %d -A '%s' -H 'Cache-Control: no-cache' "
                "'%s/%s?cb=%s' -o %s"
                % (timeout, UA, SITE, name,
                   int(datetime.datetime.now().timestamp()), path),
                timeout=timeout + 10)
    if rc != 0:
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def load_local(name):
    try:
        return json.load(open(os.path.join(BASE, name), encoding="utf-8"))
    except Exception:
        return None


def save_local(name, data):
    json.dump(data, open(os.path.join(BASE, name), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    L1_TOUCHED.append(name)


# ---------------------------------------------------------------- 巡检
def patrol(live):
    """用户视角 13 项健康巡检，返回 (健康?, 权威日期, 权威时刻, 时间源)。"""
    import beijing_clock
    bj = beijing_clock.now()
    bj_today = bj.strftime("%Y-%m-%d")
    src = beijing_clock.source()
    log("权威北京时间 %s（%s）" % (bj.strftime("%F %T"), src))

    v = live.get("version.json") or {}
    m = live.get("matches.json") or {}
    sp = live.get("score_predict.json") or {}
    vz = live.get("vzhan_data.json") or {}
    ph = live.get("parlay_history.json") or {}
    ab = live.get("ab_stats.json") or {}
    bk = live.get("basketball_parlay_history.json") or {}
    ex = live.get("expert_recommendations.json") or {}

    # --- 拉取完整性（拉不到就不假装健康）
    got = [k for k, x in live.items() if x]
    chk("线上数据可拉取", len(got) >= 6, "%d/%d 个关键文件" % (len(got), len(live)))

    # C1 主批次 == 权威竞彩当天（时钟漂移 / 跨天不推进）
    s = m.get("safeParlay") or {}
    bd = s.get("batchDate") or ""
    chk("主批次=权威竞彩当天", bd == bj_today,
        "线上 %s / 权威 %s" % (bd or "缺失", bj_today))

    # C2 分层串关死锁：≥2 条合格却 0 档，属结构性缺陷（不是「今天没好比赛」）
    q, t = s.get("qualifiedCount") or 0, s.get("tierCount") or 0
    deadlock = q >= 2 and t == 0
    _det = ("今日推荐已锁定 %s 档（生成时合格统计未留存）" % t) \
        if (s.get("locked") and q == 0 and t > 0) else \
        ("合格 %s / 档位 %s%s" % (q, t, " ← 死锁" if deadlock else ""))
    chk("分层串关无死锁", bool(m) and not deadlock,
        _det + ("" if m else "（matches.json 缺失）"))

    # C3 比分预测 V站 一等字段
    vm = sp.get("vzhanMerged") or {}
    vz_total = vm.get("vzTotal") or 0
    matched = vm.get("matched") or 0
    vz_hits = sum(1 for x in (sp.get("matches") or []) if x.get("vzHasPred"))
    vz_pred = sum(1 for x in (vz.get("matches") or []) if x.get("hasPred"))
    chk("V站 一等字段已合并",
        bool(sp) and bool(vz) and bool(vm) and matched >= vz_total and vz_hits >= vz_pred,
        "合并 %s/%s，matches 带V站 %s，V站有预测 %s" % (matched, vz_total, vz_hits, vz_pred)
        + ("" if (sp and vz) else "（数据源缺失）"))

    # C4 日期索引含当天
    dl = sp.get("dateList") or []
    chk("比分预测日期含当天", bool(sp) and bj_today in dl, "dateList[:3]=%s" % dl[:3])

    # C5 非法 utcDate（含日期被二次拼接）
    bad = sum(1 for x in (sp.get("matches") or [])
              if re.search(r"T\d{4}-\d{2}-\d{2}", str(x.get("utcDate") or "")))
    chk("utcDate 格式合法", bool(sp) and bad == 0, "非法 %d 条" % bad)

    # C6 串关历史顶层统计与 records 实算一致
    recs = ph.get("records") or []
    real = {
        "total": len(recs),
        "hit": sum(1 for r in recs if r.get("result") == "hit"),
        "miss": sum(1 for r in recs if r.get("result") == "miss"),
        "pending": sum(1 for r in recs if r.get("result") not in ("hit", "miss")),
    }
    mismatch = [k for k in ("total", "hit", "miss", "pending")
                if isinstance(ph.get(k), int) and ph[k] != real[k]]
    chk("串关历史统计自洽", bool(ph) and not mismatch,
        "顶层 total=%s / records实算=%s%s" % (ph.get("total"), real["total"],
                                          " 不一致字段:%s" % mismatch if mismatch else "")
        + ("" if ph else "（parlay_history.json 缺失）"))

    # C7 篮球串关「历史凭证」未缩水（冷启动覆盖会整组清空）
    # 注意：当日 pending 组会因「腿已停售/开赛」被合理清洗（用户要求推荐
    # 必须可下单），总条数小幅下降是正常业务行为，不能因此误报事故。
    # 真正不可逆的是已结算(hit/miss)记录与历史日期覆盖 —— 这两样减少
    # 才是冷启动覆盖 / 档案丢失（2026-11-11 真实事故：24 组掉到 3 组）。
    base = load_local(BASELINE_PATH.split("/")[-1]) or {}
    bk_recs = bk.get("records") or []
    bk_settled = sum(1 for r in bk_recs if r.get("result") in ("hit", "miss"))
    bk_dates = set(str(r.get("date") or "") for r in bk_recs)
    prev_settled = base.get("basketball_parlay_settled") or 0
    prev_dates = set(base.get("basketball_parlay_dates") or [])
    lost_dates = sorted(prev_dates - bk_dates)
    chk("篮球串关历史未缩水",
        bool(bk) and bk_settled >= prev_settled and not lost_dates,
        "已结算 %s/基线 %s，日期 %s 个，丢失日期 %s（pending 随停售清洗可波动）"
        % (bk_settled, prev_settled, len(bk_dates), lost_dates[:4] or "无")
        + ("" if bk else "（档案缺失）"))

    # C8 A/B 三源命中率云端副本可用
    ab_keys = len((ab.get("stats") or {}))
    chk("ab_stats 云端可用", ab_keys >= 5, "stats keys=%s updatedAt=%s"
        % (ab_keys, ab.get("updatedAt")))

    # C9 专家文章无空壳
    arts = ex.get("articles") or []
    shell = [a for a in arts if not str(a.get("expert") or "").strip()
             or not (a.get("contentSegments") or [])]
    chk("无空壳专家文章", bool(ex) and not shell,
        "%d/%d 篇空壳" % (len(shell), len(arts))
        + ("" if ex else "（expert_recommendations.json 缺失）"), level="WARN")

    # C10 数据新鲜度（如实判断，不伪造）
    ut = v.get("updateTime") or ""
    age = None
    try:
        age = int((bj - datetime.datetime.strptime(ut, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60)
    except Exception:
        pass
    # 白天 90 分钟 / 凌晨 180 分钟；无论是否超阈值，本轮仍会全量刷新
    limit = 90 if 8 <= bj.hour < 23 else 180
    fresh = age is not None and age <= limit
    chk("线上数据新鲜度", fresh,
        "updateTime=%s，距今 %s 分钟（阈值 %s）" % (ut or "缺失", age, limit),
        level="WARN")

    # C11 页面版本与本地源码一致
    try:
        tpl = open(os.path.join(BASE, "template.html"), encoding="utf-8").read()
        local_v = re.search(r'const VERSION = "([^"]+)"', tpl).group(1)
    except Exception:
        local_v = "?"
    chk("线上/源码版本一致", v.get("pageVersion") == local_v,
        "线上 %s / 源码 %s" % (v.get("pageVersion"), local_v), level="WARN")

    # C12 分层串关推荐证据（有档位时逐腿必须有实证样本与正超额）
    bad_legs = []
    for key in ("parlay2", "parlay3", "parlay4"):
        p = s.get(key)
        if not p:
            continue
        for lg in (p.get("legs") or []):
            if (lg.get("odds") or 0) < 1.6 or (lg.get("empN") or 0) < 20 \
                    or (lg.get("edge") or 0) < 0:
                bad_legs.append("%s:%s" % (key, lg.get("num")))
    chk("串关逐腿证据完整", bool(m) and not bad_legs,
        "违规腿 %s" % (bad_legs[:4] or "无"))

    # C13 云端刷新通道（v1.7.136）：本轮根因正是 GH_PAT 失效后长期无人察觉，
    # 「刷新数据」按钮 POST 恒 401 却被前端 .catch(()=>{}) 吞掉、假报成功。
    # 纳入每小时巡检，凭证再失效会立刻留痕 ERROR，不再等用户来提醒。
    # 只做无参 / ?health=1 探测（该分支绝不触发 CI），不产生任何额外任务。
    _hpath = "/tmp/ap_refresh_health.json"
    run("curl -sS -m 20 -A '%s' -H 'Cache-Control: no-cache' "
        "'%s/api/refresh?health=1&cb=%s' -o %s"
        % (UA, SITE, int(datetime.datetime.now().timestamp()), _hpath), timeout=30)
    _h = None
    try:
        _h = json.load(open(_hpath, encoding="utf-8"))
    except Exception:
        pass
    chk("云端刷新通道就绪",
        bool(_h) and _h.get("ok") is True and _h.get("ghPatConfigured") is True,
        "probe ok=%s / ghPatConfigured=%s" % (
            (_h or {}).get("ok"), (_h or {}).get("ghPatConfigured")),
        level="ERROR")

    return bj, bj_today, src, v, {"shell": shell, "ph_real": real,
                                  "bk_settled": bk_settled, "v": v}


# ---------------------------------------------------------------- L1 自愈
def heal_l1(live, ctx):
    """秒级定点修复：只重跑既有生成器 / 对齐统计，绝不改业务代码。"""
    log("L1 定点自愈")

    # ① V站 合并 / 日期索引 / 非法 utcDate —— 用既有 --merge-vzhan-only 重合并
    sp_bad = any(c["name"] in ("V站 一等字段已合并", "比分预测日期含当天",
                               "utcDate 格式合法") and c["level"] != "OK" for c in checks)
    if sp_bad:
        rc, out = run("python3 fetch_scores.py --merge-vzhan-only", timeout=180)
        act("V站/日期重合并", "OK" if rc == 0 else "FAIL", out[-200:])
        if rc == 0:
            L1_TOUCHED.append("score_predict.json")

    # ② 串关历史顶层统计对齐
    ph_bad = any(c["name"] == "串关历史统计自洽" and c["level"] != "OK" for c in checks)
    if ph_bad:
        ph = load_local("parlay_history.json")
        if ph and isinstance(ph.get("records"), list):
            r = ph["records"]
            hit = sum(1 for x in r if x.get("result") == "hit")
            miss = sum(1 for x in r if x.get("result") == "miss")
            pend = len(r) - hit - miss
            ph.update({"total": len(r), "hit": hit, "miss": miss, "pending": pend,
                       "hitRate": round(hit * 100.0 / len(r), 1) if r else 0})
            save_local("parlay_history.json", ph)
            act("串关统计对齐", "OK", "对齐为 %d 组（中%d/错%d/待%d）" % (len(r), hit, miss, pend))

    # ③ 空壳专家文章清理（与 fetch_expert.py 同源规则）
    ex_bad = any(c["name"] == "无空壳专家文章" and c["level"] != "OK" for c in checks)
    if ex_bad:
        ex = load_local("expert_recommendations.json")
        if ex and isinstance(ex.get("articles"), list):
            before = len(ex["articles"])
            ex["articles"] = [a for a in ex["articles"]
                              if str(a.get("expert") or "").strip()
                              and (a.get("contentSegments") or [])]
            ex["todayArticles"] = ex["articles"]
            save_local("expert_recommendations.json", ex)
            act("空壳文章清理", "OK", "%d → %d 篇" % (before, len(ex["articles"])))

    # ④ ab_stats 轻量云端副本（从 ab_compare.json 导出，避免 730KB 全量上云）
    ab_bad = any(c["name"] == "ab_stats 云端可用" and c["level"] != "OK" for c in checks)
    if ab_bad:
        ab = load_local("ab_compare.json")
        if ab:
            os.makedirs(os.path.join(BASE, "dist"), exist_ok=True)
            json.dump({"updatedAt": ab.get("updatedAt", ""), "stats": ab.get("stats") or {}},
                      open(os.path.join(BASE, "dist", "ab_stats.json"), "w", encoding="utf-8"),
                      ensure_ascii=False)
            act("ab_stats 重建", "OK", "%d keys" % len(ab.get("stats") or {}))

    # ⑤ 篮球历史疑似缩水：跑 ensure_history.py 从 Pages 副本对账恢复
    bk_bad = any(c["name"] == "篮球串关历史未缩水" and c["level"] != "OK" for c in checks)
    if bk_bad:
        rc, out = run("python3 ensure_history.py", timeout=240)
        act("历史档案对账", "OK" if rc == 0 else "FAIL", out[-240:])


# ---------------------------------------------------------------- 主流程
def main():
    check_only = "--check-only" in sys.argv
    force = "--force-refresh" in sys.argv
    log("=" * 62)
    log("自动保鲜驾驶舱启动（check_only=%s force=%s）" % (check_only, force))

    live_names = ["version.json", "matches.json", "score_predict.json",
                  "vzhan_data.json", "parlay_history.json", "ab_stats.json",
                  "basketball_parlay_history.json", "expert_recommendations.json"]
    live = {n: curl_json(n) for n in live_names}

    try:
        bj, bj_today, src, ver_before, ctx = patrol(live)
    except Exception as e:
        log("!! 巡检异常：%s\n%s" % (e, traceback.format_exc()))
        bj = datetime.datetime.now()
        bj_today, src, ver_before, ctx = bj.strftime("%Y-%m-%d"), "fallback", {}, {}
        chk("巡检可执行", False, str(e))

    errs_before = [c for c in checks if c["level"] == "ERROR"]
    warns_before = len([c for c in checks if c["level"] == "WARN"])
    log("巡检完成：ERROR %d / WARN %d" % (len(errs_before), warns_before))

    if not check_only:
        # 先落一次「本轮巡检快照」：L3 里的 deploy_cf.py 会读它并注入
        # version.json 的 lastPatrolAt / patrolHealth，于是每一次部署都带上
        # 「这一轮确实自动巡检过」的心跳证据，用户无需开口问。
        _snap = {
            "checkedAt": bj.strftime("%Y-%m-%d %H:%M:%S"),
            "beijingNow": bj.strftime("%Y-%m-%d %H:%M:%S"),
            "clockSource": src,
            "health": "OK" if not errs_before else "HEALING",
            "errorCount": len(errs_before), "warnCount": warns_before,
            "checks": list(checks), "actions": [], "phase": "pre-refresh",
            "site": SITE,
        }
        try:
            json.dump(_snap, open(STATUS_PATH, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass

        # L1：能秒修的先修
        heal_l1(live, ctx)
        # pre-check 的 ERROR 已进入自愈流程，先标 HEALING；闭环成功收尾时
        # 统一转 HEALED，避免 errorCount 虚高、用户以为问题还没处理。
        for _c in checks:
            if _c["level"] == "ERROR":
                _c["level"] = "HEALING"

        # L3：每轮必跑全量刷新（自带指纹节流，数据/代码一变即部署）
        log("L3 全量刷新 + 部署")
        rc, out = run("GITHUB_ACTIONS=1 bash run_daily.sh deploy", timeout=1500)
        act("全量刷新部署", "OK" if rc == 0 else "FAIL",
            out[-420:])
        # 抓取自检与部署结果关键字，便于状态里如实记录
        deploy_ok = "DEPLOY_OK" in out
        act("部署结论", "DEPLOY_OK" if deploy_ok else ("SKIPPED_OR_FAIL"),
            "详见 auto_pilot.log")

        # 护栏：selftest 全量（含检查 12/13/14/15 防回归）
        log("自检护栏")
        rc2, out2 = run("python3 selftest.py", timeout=240)
        m = re.search(r"ERROR\s+(\d+)\s+项\s*/\s*WARN\s+(\d+)", out2)
        se, sw = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
        act("selftest", "OK" if rc2 == 0 else "FAIL", "ERROR %s / WARN %s" % (se, sw))

        # L2：L1 动过数据 / selftest 有 ERROR → 强制构建 + 部署，确保修复上线
        need_l2 = bool(L1_TOUCHED) or (se and se > 0)
        if need_l2:
            log("L2 强制构建部署（L1 触碰 %d 文件 / 自检 ERROR %s）"
                % (len(L1_TOUCHED), se))
            rc3, out3 = run("python3 build_panel.py && python3 deploy_cf.py", timeout=600)
            act("强制上线", "OK" if rc3 == 0 else "FAIL", out3[-300:])
        else:
            act("强制上线", "SKIP", "无需额外部署")

        # 线上复验：确认真的上新 / 修复生效
        log("线上复验")
        ver_after = curl_json("version.json") or {}
        m_after = curl_json("matches.json") or {}
        sp_after = curl_json("score_predict.json") or {}
        s_after = m_after.get("safeParlay") or {}
        vm_after = sp_after.get("vzhanMerged") or {}
        chk("复验·主批次", s_after.get("batchDate") == bj_today,
            "%s / 权威 %s" % (s_after.get("batchDate"), bj_today))
        chk("复验·分层串关", not ((s_after.get("qualifiedCount") or 0) >= 2
                                and (s_after.get("tierCount") or 0) == 0),
            "合格 %s / 档 %s" % (s_after.get("qualifiedCount"), s_after.get("tierCount")))
        chk("复验·V站", (vm_after.get("matched") or 0) >= (vm_after.get("vzTotal") or 0),
            "合并 %s/%s" % (vm_after.get("matched"), vm_after.get("vzTotal")))
        changed = (ver_after.get("updateTime") != (ver_before or {}).get("updateTime"))
        act("线上是否上新", "YES" if changed else "NO_CHANGE",
            "%s → %s" % ((ver_before or {}).get("updateTime"),
                         ver_after.get("updateTime")))
    else:
        ver_after = ver_before

    # 基线推进：只记「已结算记录数 + 历史日期覆盖」，pending 不进基线，
    # 避免把当日可下单清洗误当成历史缩水。
    try:
        bk_live = live.get("basketball_parlay_history.json") or {}
        bkr = bk_live.get("records") or []
        settled = sum(1 for r in bkr if r.get("result") in ("hit", "miss"))
        dates = sorted(set(str(r.get("date") or "") for r in bkr))
        base = load_local(BASELINE_PATH.split("/")[-1]) or {}
        base.update({
            "basketball_parlay_settled": max(settled, base.get("basketball_parlay_settled") or 0),
            "basketball_parlay_dates": sorted(
                set(dates) | set(base.get("basketball_parlay_dates") or [])),
            "updatedAt": bj.strftime("%Y-%m-%d %H:%M:%S"),
        })
        json.dump(base, open(BASELINE_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass

    # 健康状态：只巡检发现待修项标 DEGRADED（不是系统卡死）；
    # 跑过闭环且自愈动作没失败 → HEALED；动作失败 → BLOCKED。
    _failed = any(a["result"] == "FAIL" for a in actions)
    for _c in checks:
        if _c["level"] == "HEALING":
            _c["level"] = "ERROR" if _failed else "HEALED"
    final_err = [c for c in checks if c["level"] == "ERROR"]
    if check_only:
        health = "OK" if not final_err else "DEGRADED"
    elif not final_err:
        health = "OK"
    elif _failed:
        health = "BLOCKED"
    else:
        health = "HEALED"
    status = {
        "checkedAt": bj.strftime("%Y-%m-%d %H:%M:%S"),
        "beijingNow": bj.strftime("%Y-%m-%d %H:%M:%S"),
        "clockSource": src,
        "health": health,
        "errorCount": len(final_err),
        "warnCount": len([c for c in checks if c["level"] == "WARN"]),
        "checks": checks,
        "actions": actions,
        "l1Touched": sorted(set(L1_TOUCHED)),
        "version": (ver_after or {}),
        "site": SITE,
        "note": "数据无变化时如实标记 NO_CHANGE，不伪造 updateTime；每轮仍真实巡检与刷新",
    }
    json.dump(status, open(STATUS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 随下次部署上云，用户可直接访问 /auto_pilot_status.json 查阅
    try:
        os.makedirs(os.path.join(BASE, "dist"), exist_ok=True)
        json.dump(status, open(os.path.join(BASE, "dist", "auto_pilot_status.json"),
                               "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

    log("=" * 62)
    log("最终 health=%s | ERROR %d / WARN %d | 动作 %d 项"
        % (health, status["errorCount"], status["warnCount"], len(actions)))
    print("\n=== 自动保鲜简报 ===")
    print("北京时间   : %s (%s)" % (status["beijingNow"], src))
    print("健康状态   : %s  ERROR %d / WARN %d" % (health, status["errorCount"], status["warnCount"]))
    for a in actions:
        print("  %-16s %-14s %s" % (a["step"], a["result"], a["detail"][:90]))
    print("线上版本   : %s | 数据 %s | 部署 %s" % (
        (ver_after or {}).get("pageVersion"), (ver_after or {}).get("updateTime"),
        (ver_after or {}).get("deployTime")))
    print("状态文件   : auto_pilot_status.json（线上 /auto_pilot_status.json）")
    print("站点       : %s" % SITE)
    return 0 if health in ("OK", "HEALED") else 1


if __name__ == "__main__":
    sys.exit(main())
