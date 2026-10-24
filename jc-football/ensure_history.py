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

  两档判定（v1.7.38 起，v1.7.39 泛化）：
    累积型文件（history 等）  -> 条数优先：线上更多即恢复；条数相同但线上
      时间戳严格更晚也恢复（混合规则，防「同条数旧快照」覆盖线上新数据）
    滚动型文件（TS_FILES：篮球赛程/档案、比分预测展示数据、各实时快照）-> 按更新时间戳：
      条数非单调（在售赛程每天进出、赛果回填、展示窗滚动），
      同样条数可能是旧快照；线上 updatedAt 严格更晚即恢复，同刻才比条数。

  专用规则（v1.7.41）：
    parlay_history.json -> 签名并集对账（parlay_union）：条数+时间戳对
      「双端同条数、内容互补」的分叉无能为力（2026-10-24 事故：本地独有
      10-23 组被线上 10-24 组的同条数新快照吞掉，用户看到「昨天的串关
      历史又跑了」）。按 (date, legs签名) 取并集，任一方独有的记录都
      保留并写回本地，随部署推回 Pages。

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
    "score_predict.json",           # 比分预测展示数据（v1.7.39，时间戳判定：
                                     # 任务沙箱丢 Key 后天天抹空，靠 Pages 副本续命）
    "score_predict_cache.json",     # 比分预测球队缓存（v1.7.39，条数判定：
                                     # 沙箱重置后从 Pages 拉回 165 支球队，免每天 25 支重建）
    # 实时快照（v1.7.39，时间戳判定）：这些文件由任务沙箱每轮抓取后部署到 Pages，
    # 是「任务侧最后一份成功数据」。手动部署若用本地旧快照，会把线上更新的数据
    # 覆盖回旧值（2026-10-22 实测：本地 matches 14:03 vs 线上 17:33）。
    # 纳入对账后，手动部署前自动把线上更新的快照拉回本地，杜绝回归。
    "matches.json",                 # 竞彩核心赛程+当日推荐（updateTime）
    "hkmo_cache.json",              # 港澳赔率缓存（fetched_at）
    "odds_bsd.json",                # The Odds API 赔率异动（updatedAt）
    "fd_data.json",                 # football-data.org 官方赛果核对（updatedAt）
    # WhoScored 人工预测快照与专家推荐快照（v1.7.39）：二者只经 index.html 的
    # BUILT_WHOSCORED / BUILT_EXPERT 注入页面，此前无静态云端副本——手动部署
    # 若用本地旧快照构建，会把任务侧更新的数据覆盖回旧值（2026-10-22 实测：
    # 本地 10-20 vs 线上 10-22）。纳入对账后，部署前自动拉回线上更新快照。
    "whoscored_data.json",          # WhoScored 人工预测比分（updatedAt）
    "expert_recommendations.json",  # 92玩球专家推荐（updatedAt）
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


def _count_score_predict(obj):
    """score_predict.json 展示数据：真实条数是 matches（展示窗）长度；
    顶层还有 history/dateIndex 等大字段，不专门计数只会数到约 9 个顶层键。"""
    if isinstance(obj, dict):
        ms = obj.get("matches")
        return len(ms) if isinstance(ms, list) else 0
    return 0


def _count_team_cache(obj):
    """球队缓存：只数「有赛果记录」的球队，空壳键不计。"""
    if isinstance(obj, dict):
        return sum(1 for v in obj.values()
                   if isinstance(v, dict) and v.get("matches"))
    return 0


def _count_matches_list(obj):
    """快照类文件（matches.json / hkmo_cache.json）：真实条数是 matches 列表长度。"""
    if isinstance(obj, dict):
        ms = obj.get("matches")
        return len(ms) if isinstance(ms, list) else 0
    return 0


def _count_whoscored_data(obj):
    """WhoScored 人工预测快照：真实条数是 matches（583 场人工预测）长度。"""
    if isinstance(obj, dict):
        ms = obj.get("matches")
        return len(ms) if isinstance(ms, list) else 0
    return 0


def _count_expert_recommendations(obj):
    """专家推荐快照：真实条数是 articles（当日入库文章）长度；
    recommendations 通常只有几条，expertStats 是 22 个专家的统计 dict。"""
    if isinstance(obj, dict):
        arts = obj.get("articles")
        return len(arts) if isinstance(arts, list) else 0
    return 0


COUNT_FN = {
    "whoscored_history.json": _count_whoscored_history,
    "basketball_history.json": _count_basketball_history,
    "basketball.json": _count_basketball_schedule,
    "score_predict.json": _count_score_predict,
    "score_predict_cache.json": _count_team_cache,
    "matches.json": _count_matches_list,
    "hkmo_cache.json": _count_matches_list,
    "whoscored_data.json": _count_whoscored_data,
    "expert_recommendations.json": _count_expert_recommendations,
}

# 时间戳优先判定的文件（v1.7.38 起）：条数非单调或语义为「快照」——在售赛程每天进出、
# 赛果随时间回填、比分预测展示窗滚动、核心赛程每轮刷新，同样条数可能是旧快照
# （2026-10-22 篮球事故：本地 21 条 vs 线上 21 条，时间戳却差 44 小时，按条数对账
# 判「正常」，旧快照就此长期 Serve）。这些文件改用**更新时间戳优先**判定，
# 条数仅作同刻并列时的补充。
TS_FILES = {"basketball.json", "basketball_history.json", "score_predict.json",
            "matches.json", "hkmo_cache.json", "odds_bsd.json", "fd_data.json",
            "whoscored_data.json", "expert_recommendations.json"}


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
        # 各模块时间戳字段名不统一：updatedAt / updateTime / fetched_at 都算
        upd = str(d.get("updatedAt") or d.get("updateTime") or d.get("fetched_at") or "")
    return d, n, upd


def ts_should_restore(local, remote):
    """时间戳优先文件的恢复判定（纯函数，便于单测）：
    local/remote 为 load_json 的返回值三元组 (对象, 条数, updatedAt)。

    恢复条件（满足其一）：
      1. 本地缺失/损坏，线上可解析          -> 恢复（冷启动/沙箱重置）
      2. 线上更新时间严格晚于本地           -> 恢复（本地是被速冻的旧快照/被抹空的老数据）
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


def count_should_restore(local, remote):
    """累积型文件（history.json 等）的恢复判定（纯函数，便于单测）：
    local/remote 为 load_json 的返回值三元组 (对象, 条数, updatedAt)。

    恢复条件（满足其一）：
      1. 本地缺失/损坏，线上可解析          -> 恢复
      2. 线上条数更多                       -> 恢复（经典丢失场景）
      3. 条数相同，但线上时间戳严格更晚      -> 恢复（v1.7.39 混合规则：
         2026-10-22 实测 history.json 本地 597 条 = 线上 597 条，纯条数对账
         判「正常」，但线上时间戳新 3.5 小时（任务侧刚回填过赛果）——
         手动部署这份本地旧快照就会把线上新数据覆盖回旧值）
    其余情况保持本地。
    """
    l_obj, l_n, l_upd = local
    r_obj, r_n, r_upd = remote
    if r_obj is None:
        return False                      # 线上不可用，无从恢复
    if l_obj is None:
        return True                       # 本地缺失/损坏
    if (r_n or 0) > (l_n or 0):
        return True                       # 线上条数更多
    if l_upd and r_upd and r_upd > l_upd:
        return True                       # 同条数但线上更新（赛果回填等）
    return False

def _parlay_leg_sig(legs):
    """串关记录的腿签名（与 fetch_daily.save_parlay_history._leg_sig 同口径）。"""
    return tuple(sorted((l.get("num"), l.get("playKey"), l.get("dir"))
                        for l in (legs or [])))


def _parlay_rec_sig(rec):
    """串关记录签名：(date, legs签名)。同一天同组腿=同一条已发布推荐。"""
    return (rec.get("date", ""), _parlay_leg_sig(rec.get("legs")))


def parlay_union(local_d, remote_d):
    """parlay_history.json 签名并集对账（v1.7.41，纯函数）。

    背景（2026-10-24 事故）：本地 10-23 09:07 生成的 3 组串关推荐，未能在
    线上条数追平前完成合并部署；随后对账两边均为 69 条，按「条数相同、
    线上时间戳更晚」整体覆盖，本地独有的 10-23 组被当旧数据吞掉——用户看到
    「昨天的串关推荐历史记录又跑哪里去了」。条数+时间戳判定对「双端同条数、
    内容互补」的分叉无能为力，必须按记录签名取并集。

    规则：
      1. 签名只在一方出现  -> 无条件保留（任一端独有记录都不丢）
      2. 同签名两边都有    -> 取 savedAt 更晚的版本（赛后结算更新的胜出）
    返回 (merged_records 按 date+savedAt 排序, 本地缺失需从线上补入的条数)。
    local_d 为 None（本地缺失/损坏）时返回线上全量；remote_d 为 None 返回 (None, 0)。
    """
    if remote_d is None:
        return None, 0
    r_recs = (remote_d.get("records") or []) if isinstance(remote_d, dict) else []
    if not isinstance(local_d, dict):
        return list(r_recs), len(r_recs)          # 本地缺失/损坏：整体恢复线上
    l_recs = local_d.get("records") or []
    by_sig = {}
    for r in r_recs:
        by_sig[_parlay_rec_sig(r)] = r
    for r in l_recs:
        s = _parlay_rec_sig(r)
        if s not in by_sig:
            by_sig[s] = r
        elif (r.get("savedAt") or "") > (by_sig[s].get("savedAt") or ""):
            by_sig[s] = r                          # 同签名取 savedAt 更晚（已结算）
    merged = sorted(by_sig.values(),
                    key=lambda r: (r.get("date") or "", r.get("savedAt") or ""))
    l_sigs = {_parlay_rec_sig(r) for r in l_recs}
    n_added = sum(1 for s in by_sig if s not in l_sigs)
    return merged, n_added


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
            r_upd = str(remote_d.get("updatedAt") or remote_d.get("updateTime")
                        or remote_d.get("fetched_at") or "")

        # parlay_history.json 专用：签名并集对账（v1.7.41）。
        # 条数+时间戳的整体覆盖吞过一端独有记录（2026-10-24 事故：本地独有
        # 10-23 组 vs 线上独有 10-24 组，两边 69=69 条时本地组被当旧数据丢弃），
        # 改为按 (date, legs签名) 取并集：任一方独有的记录都写回本地，
        # 随下次部署推回 Pages——服务器保存全部历史，杜绝「又跑了」。
        if name == "parlay_history.json":
            merged, n_added = parlay_union(l_obj, remote_d)
            if merged is None:
                print("  %-26s 线上不可解析，保持本地" % name)
                kept.append(name)
                continue
            if len(merged) > (l_n or 0):
                if dry:
                    print("  %-26s 本地 %s 条 | 线上 %s 条 → 并集 %d 条，"
                          "需从线上补 %d 条（--dry 未写入）"
                          % (name, l_n if l_n is not None else "缺失", r_n,
                             len(merged), n_added))
                    restored.append(name)
                    continue
                if os.path.exists(local):
                    bak = local + ".bak_lost_" + datetime.datetime.now().strftime("%m%d%H%M%S")
                    os.rename(local, bak)
                    print("  %-26s 本地旧版本已留存为 %s" % (name, os.path.basename(bak)))
                base = l_obj if isinstance(l_obj, dict) else remote_d
                out = dict(base)
                out["records"] = merged
                upd = max(str(l_upd or ""), str(r_upd or ""))
                if upd:
                    out["updatedAt"] = upd
                with io.open(local, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=1)
                print("  %-26s ⚠️ 本地 %s 条 < 并集 %d 条（线上补 %d 条）→ 已按签名并集恢复"
                      % (name, l_n if l_n is not None else "缺失",
                         len(merged), n_added))
                restored.append(name)
            else:
                print("  %-26s 本地 %s 条 >= 并集 %d 条 → 正常（签名并集判定）"
                      % (name, l_n if l_n is not None else "缺失", len(merged)))
                kept.append(name)
            continue

        is_bk = name in TS_FILES
        if is_bk:
            need = ts_should_restore((l_obj, l_n, l_upd), (remote_d, r_n, r_upd))
        else:
            need = count_should_restore((l_obj, l_n, l_upd), (remote_d, r_n, r_upd))

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
