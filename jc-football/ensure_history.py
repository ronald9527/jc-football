#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ensure_history.py —— 历史数据「防抹除」保护

为什么必须有这个脚本：
  history.json 是永久累积的历史战绩库（模型统计、规则回测、alpha 计算全靠它）。
  定时任务跑在国内沙箱里，如果沙箱被重置，本地 history.json 会丢失。
  更危险的是：定时任务会拿着空历史重新构建并部署，
  **把 Cloudflare Pages 上那份完好的历史也一起覆盖掉** —— 双重丢失。

  因此每次跑定时任务之前，必须先做一次「对账」：
    本地记录数 < 线上记录数  ->  判定为本地丢失，从线上拉回（本地留存为 .bak）
    本地记录数 >= 线上       ->  本地更新，正常继续

用法：
  python3 ensure_history.py            # 对账并按需恢复
  python3 ensure_history.py --dry      # 只报告不写文件
"""
import io, json, os, sys, urllib.request, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# 线上站点地址：历史数据同时部署在 Pages 上，作为云端副本
BASE_URL = "https://jc-football-1eo.pages.dev"

# 需要保护的累积型数据文件
HIST_FILES = [
    "history.json",             # 主历史战绩库（最关键）
    "parlay_history.json",      # 串关历史
    "hkmo_history.json",        # 港澳赔率历史
    "strongweak_history.json",  # 强弱队历史
    "half_full_history.json",   # 半全场历史
    "smart_model.json",         # 智能引擎自动调参结果
    "whoscored_history.json",       # WhoScored 比分预测评估历史（records 为 dict）
    "whoscored_preview_cache.json", # WhoScored 预测比分缓存（平铺 dict）
    "whoscored_recent.json",        # WhoScored 近期比赛元数据（平铺 dict）
    "basketball_history.json",      # 篮球赛后档案（records 为 dict，v1.7.38 纳入保护）
    "basketball.json",              # 篮球在售赛程快照（v1.7.38，时间戳判定防旧快照速冻）
]

# 各文件的真实记录数提取方式（默认 records_count 按 list/records-list 处理，
# 但 whoscored 三件套是 dict 结构，必须专门计数，否则永远比对不出丢失）
def _count_whoscored_history(obj):
    if isinstance(obj, dict):
        recs = obj.get("records")
        return len(recs) if isinstance(recs, (dict, list)) else 0
    return 0


def _count_basketball_history(obj):
    """篮球档案 records 以 matchId 为键（dict 结构），同样必须专门计数，
    否则 records_count 只会数到 2 个顶层键，永远比对不出丢失。"""
    if isinstance(obj, dict):
        recs = obj.get("records")
        return len(recs) if isinstance(recs, (dict, list)) else 0
    return 0


def _count_basketball_schedule(obj):
    """basketball.json 是在售赛程快照（{"updatedAt", "matches": [list]}），
    真实条数是 matches 长度；不专门计数只会数到 2 个顶层键。"""
    if isinstance(obj, dict):
        ms = obj.get("matches")
        return len(ms) if isinstance(ms, list) else 0
    return 0


COUNT_FN = {
    "whoscored_history.json": _count_whoscored_history,
    "basketball_history.json": _count_basketball_history,
    "basketball.json": _count_basketball_schedule,
}

# 篮球两文件（v1.7.38）：条数非单调——在售赛程每天进出、赛果随时间回填，
# 同样条数完全可能是旧快照（2026-10-22 事故：本地 21 条 vs 线上 21 条，
# 时间戳却差 44 小时，按条数对账判「正常」，旧快照就此长期 Serve）。
# 因此这两个文件改用**更新时间戳优先**判定，条数仅作同刻并列时的补充。
BK_TS_FILES = {"basketball.json", "basketball_history.json"}


def load_json(path):
    """返回 (对象或None, 记录数, updatedAt字符串)。文件缺失/损坏 -> (None, None, "")"""
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None, None, ""
    name = os.path.basename(path)
    fn = COUNT_FN.get(name)
    n = fn(d) if fn else records_count(d)
    upd = ""
    if isinstance(d, dict):
        upd = str(d.get("updatedAt") or d.get("updateTime") or "")
    return d, n, upd


def bk_should_restore(local, remote):
    """篮球两文件的恢复判定（纯函数，便于单测）：
    local/remote 为 load_json 的返回值三元组 (对象, 条数, updatedAt)。

    恢复条件（满足其一）：
      1. 本地缺失/损坏，线上可解析          -> 恢复（冷启动/沙箱重置）
      2. 线上更新时间严格晚于本地           -> 恢复（本地是被速冻的旧快照）
      3. 时间戳相同或都缺失，且线上条数更多  -> 恢复（同刻累积更多）
    其余情况保持本地（本地更新或同样新鲜）。
    """
    l_obj, l_n, l_upd = local
    r_obj, r_n, r_upd = remote
    if r_obj is None:
        return False                      # 线上不可用，无从恢复
    if l_obj is None:
        return True                       # 本地缺失/损坏
    if l_upd and r_upd:
        if r_upd > l_upd:
            return True                   # 线上更新更晚——本地是旧快照
        if r_upd < l_upd:
            return False                  # 本地更新更晚（抓取成功但部署被节流跳过）
        return (r_n or 0) > (l_n or 0)    # 同刻比条数
    # 有一侧时间戳缺失：退回条数比较
    return (r_n or 0) > (l_n or 0)

TIMEOUT = 40


def records_count(obj):
    """从已解析对象提取真实记录数：list 取长度；dict 优先取 records 列表；否则取顶层键数。

    历史库文件（history.json 等）结构是 {"updatedAt":..,"total":..,"records":[...]}，
    若直接 len(dict) 只会数到 3 个顶层键，会掩盖真实丢失，必须深入 records。"""
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        if isinstance(obj.get("records"), list):
            return len(obj["records"])
        return len(obj)
    return 0


def count(path):
    """返回文件内的记录数；无法解析返回 None"""
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        name = os.path.basename(path)
        fn = COUNT_FN.get(name)
        return fn(d) if fn else records_count(d)
    except Exception:
        return None


def fetch(url, retry=3):
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jc-history-guard"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception:
            if i == retry - 1:
                return None
    return None


def main():
    dry = "--dry" in sys.argv
    print("=" * 60)
    print("历史数据对账  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    restored, kept, missing_remote = [], [], []

    for name in HIST_FILES:
        local = os.path.join(HERE, name)
        l_obj, l_n, l_upd = load_json(local) if os.path.exists(local) else (None, None, "")
        remote_bytes = fetch("%s/%s" % (BASE_URL, name))

        if remote_bytes is None:
            missing_remote.append(name)
            print("  %-26s 本地 %s 条 | 线上取不到（首次部署前属正常）"
                  % (name, l_n if l_n is not None else "缺失"))
            continue

        try:
            remote_d = json.loads(remote_bytes.decode("utf-8"))
        except Exception:
            missing_remote.append(name)
            print("  %-26s 线上内容无法解析，跳过" % name)
            continue
        fn = COUNT_FN.get(name)
        r_n = fn(remote_d) if fn else records_count(remote_d)
        r_upd = ""
        if isinstance(remote_d, dict):
            r_upd = str(remote_d.get("updatedAt") or remote_d.get("updateTime") or "")

        is_bk = name in BK_TS_FILES
        if is_bk:
            need = bk_should_restore((l_obj, l_n, l_upd), (remote_d, r_n, r_upd))
        else:
            need = r_n > (l_n or 0)

        if need:
            # 线上更新 -> 本地丢了/被速冻，恢复
            if dry:
                print("  %-26s 本地 %s 条(%s) | 线上 %s 条(%s) → 需恢复（--dry 未写入）"
                      % (name, l_n if l_n is not None else "缺失", l_upd or "无时间戳",
                         r_n, r_upd or "无时间戳"))
                restored.append(name)
                continue
            if os.path.exists(local):
                bak = local + ".bak_lost_" + datetime.datetime.now().strftime("%m%d%H%M%S")
                os.rename(local, bak)
                print("  %-26s 本地旧版本已留存为 %s" % (name, os.path.basename(bak)))
            with io.open(local, "wb") as f:
                f.write(remote_bytes)
            print("  %-26s ⚠️ 本地 %s 条(%s) < 线上 %s 条(%s) → 已从线上恢复"
                  % (name, l_n if l_n is not None else "缺失", l_upd or "无时间戳",
                     r_n, r_upd or "无时间戳"))
            restored.append(name)
        else:
            if is_bk:
                print("  %-26s 本地 %s 条(%s) | 线上 %s 条(%s) → 正常（时间戳判定）"
                      % (name, l_n, l_upd or "无时间戳", r_n, r_upd or "无时间戳"))
            else:
                print("  %-26s 本地 %s 条 >= 线上 %s 条 → 正常" % (name, l_n, r_n))
            kept.append(name)

    print("-" * 60)
    print("恢复 %d 个 / 正常 %d 个 / 线上缺失 %d 个"
          % (len(restored), len(kept), len(missing_remote)))
    if restored:
        print("⚠️ 检测到历史数据丢失并已从线上副本恢复，本次定时任务可安全继续。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
