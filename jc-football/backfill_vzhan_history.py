#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_vzhan_history.py —— V站 高信心预测赛果全量回溯（v1.7.126）

为什么需要它
============
`fetch_vzhan.py` 的赛果回填一直依赖 V站 `report/info` 返回的 match_info
真实比分，并要求「开球已过 3 小时」。但 V站 抓取的是「当天在售的竞彩赛程」，
快照抓下来的时刻比赛大多还没开踢；等比赛踢完，这一批场次早已滚出销售窗口，
再也不会被抓到 —— 于是 vzhan_history.json 从接入至今一直是 0 场归档，
「🔥高信心到底准不准」既无数据也无法验证。

本脚本用项目内已有的三份权威离线数据做一次性全量回溯（零外网、幂等）：

  1) V站 赛前预测：git 历史里所有 vzhan_data.json 快照 + index.html 内嵌的
     BUILT_VZHAN，按 (date, jcNum) 去重，取最早出现的赛前预测（不做事后替换）。
  2) 赛前模型方向：git 历史里所有 matches.json / index.html 内嵌 BUILT_DATA 的
     modelPred，按 (date, num) 建索引。高信心口径与前端 vzConfidence() 完全
     一致 —— V站 预测方向 == 模型预测方向。找不到模型快照的场次标为
     `confAvailable=false`，单列「无法判定」，绝不硬凑进任一组。
  3) 官方赛果：history.json 的 result（竞彩官方开奖回溯），按 (date, num)
     严格对齐，队名做相似度校验防错配；取消/无比分一律排除。

产物 vzhan_history.json 与 fetch_vzhan.py 的 update_history() 格式完全兼容，
前端 renderVzBackfill() 可直接展示；之后每天由 fetch_vzhan 增量归档，本脚本
不必重复跑（重跑也幂等，已有 key 默认不覆盖）。

用法：
    python3 backfill_vzhan_history.py              # 幂等合并，打印摘要
    python3 backfill_vzhan_history.py --force      # 用回溯结果覆盖同名 key
    python3 backfill_vzhan_history.py --no-git     # 只扫本地文件，不读 git 历史
"""
import argparse
import collections
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BJ = timezone(timedelta(hours=8))
OUT = os.path.join(HERE, "vzhan_history.json")
HISTORY = os.path.join(HERE, "history.json")

# 归一化队名：优先复用项目单一真相源 team_map，失败则退化为去空白
try:
    sys.path.insert(0, HERE)
    from team_map import zh_norm as _zh_norm
except Exception:
    def _zh_norm(s):
        return "".join(str(s or "").split())


def norm(s):
    return _zh_norm(s)


def outcome(h, a):
    if h is None or a is None:
        return None
    return "H" if h > a else ("A" if a > h else "D")


def sim(a, b):
    """队名字符集合 Jaccard：V站简称 vs 竞彩全称（巴黎圣曼 / 巴黎圣日尔曼）。"""
    a, b = set(norm(a)), set(norm(b))
    return len(a & b) / len(a | b) if (a | b) else 0.0


def git_root():
    try:
        return subprocess.check_output(
            ["git", "-C", HERE, "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def git_blobs(repo, relpath):
    """返回 [(commit_time, blob_text)]，按提交时间正序（老→新）。"""
    try:
        lines = subprocess.check_output(
            ["git", "-C", repo, "log", "--all", "--reverse",
             "--format=%H %ct", "--", relpath],
            text=True, stderr=subprocess.DEVNULL).strip().splitlines()
    except Exception:
        return []
    out = []
    for ln in lines:
        p = ln.split()
        if len(p) < 2:
            continue
        sha, ct = p[0], int(p[1])
        try:
            txt = subprocess.check_output(
                ["git", "-C", repo, "show", "%s:%s" % (sha, relpath)],
                text=True, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        out.append((ct, txt))
    return out


def parse_vz_blob(txt):
    """从 vzhan_data.json 文本或 index.html 的 BUILT_VZHAN 行解析出 (updatedAt, matches)。"""
    if txt.lstrip().startswith("{"):
        try:
            d = json.loads(txt)
            return d.get("updatedAt") or "", d.get("matches") or []
        except Exception:
            return "", []
    for ln in txt.splitlines():
        if ln.startswith("let BUILT_VZHAN = "):
            try:
                d = json.loads(ln.split("=", 1)[1].strip().rstrip(";"))
                return d.get("updatedAt") or "", d.get("matches") or []
            except Exception:
                return "", []
    return "", []


def parse_model_blob(txt):
    """从 matches.json 文本或 index.html 的 BUILT_DATA 行解析出 (date,num)->(h,a)。"""
    d = None
    if txt.lstrip().startswith("{"):
        try:
            d = json.loads(txt)
        except Exception:
            d = None
    if d is None:
        for ln in txt.splitlines():
            if ln.startswith("let BUILT_DATA = "):
                try:
                    d = json.loads(ln.split("=", 1)[1].strip().rstrip(";"))
                except Exception:
                    d = None
                break
    out = []
    if isinstance(d, dict):
        for m in d.get("matches") or []:
            mp = m.get("modelPred") or {}
            if isinstance(mp, dict) and mp.get("h") is not None and mp.get("a") is not None:
                out.append(((m.get("date"), m.get("num")), (int(mp["h"]), int(mp["a"]))))
    return out


def collect(use_git=True):
    """返回 (pred_index, model_index, stats)。

    pred_index: (date, jcNum) -> vz match（最早赛前快照优先）
    model_index: (date, num) -> (h, a)（最早快照优先）
    """
    preds, models = {}, {}
    counts = collections.Counter()
    repo = git_root() if use_git else ""

    # ---- 1. V站 预测快照 ----
    vz_sources = []
    local_vz = [os.path.join(HERE, "vzhan_data.json"),
                os.path.join(HERE, "vzhan_data.json.bak_lost_1102183924"),
                os.path.join(HERE, "dist", "vzhan_data.json"),
                os.path.join(HERE, "index.html")]
    for p in local_vz:
        if os.path.exists(p):
            try:
                vz_sources.append((os.path.getmtime(p), open(p, encoding="utf-8").read(), "local"))
            except Exception:
                pass
    if repo:
        rel = os.path.relpath(os.path.join(HERE, "vzhan_data.json"), repo)
        for ct, txt in git_blobs(repo, rel):
            vz_sources.append((ct, txt, "git"))
        rel = os.path.relpath(os.path.join(HERE, "index.html"), repo)
        for ct, txt in git_blobs(repo, rel):
            vz_sources.append((ct, txt, "git"))
    vz_sources.sort(key=lambda x: x[0])
    for _, txt, kind in vz_sources:
        upd, ms = parse_vz_blob(txt)
        if not ms:
            continue
        counts["vz_blobs_" + kind] += 1
        for m in ms:
            if not (m.get("hasPred") and m.get("predH") is not None and m.get("predA") is not None):
                continue
            k = (m.get("date"), m.get("jcNum"))
            if k not in preds:
                m = dict(m)
                m["_snapshotAt"] = upd
                preds[k] = m

    # ---- 2. 模型预测快照 ----
    md_sources = []
    for p in [os.path.join(HERE, "matches.json"), os.path.join(HERE, "index.html")]:
        if os.path.exists(p):
            try:
                md_sources.append((os.path.getmtime(p), open(p, encoding="utf-8").read(), "local"))
            except Exception:
                pass
    if repo:
        rel = os.path.relpath(os.path.join(HERE, "matches.json"), repo)
        for ct, txt in git_blobs(repo, rel):
            md_sources.append((ct, txt, "git"))
    md_sources.sort(key=lambda x: x[0])
    for _, txt, kind in md_sources:
        pairs = parse_model_blob(txt)
        if pairs:
            counts["model_blobs_" + kind] += 1
        for k, v in pairs:
            models.setdefault(k, v)

    return preds, models, counts


def load_official():
    """history.json 官方开奖结果索引 (date, num) -> record。"""
    try:
        h = json.load(open(HISTORY, encoding="utf-8"))
    except Exception as e:
        print("! 读取 history.json 失败：%s" % e, flush=True)
        return {}
    idx = {}
    for r in h.get("records", []):
        res = r.get("result") or {}
        if res.get("cancel") or res.get("home") is None or res.get("away") is None:
            continue
        idx.setdefault((r.get("date"), r.get("num")), r)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="覆盖同名 key")
    ap.add_argument("--no-git", action="store_true", help="只扫本地文件")
    args = ap.parse_args()

    preds, models, counts = collect(use_git=not args.no_git)
    official = load_official()
    print("· V站 赛前预测 %d 场（快照 %s）" % (len(preds), dict(counts)), flush=True)
    print("· 模型预测索引 %d 条 | 官方赛果索引 %d 条" % (len(models), len(official)), flush=True)

    existing = {"matches": {}}
    if os.path.exists(OUT):
        try:
            existing = json.load(open(OUT, encoding="utf-8")) or existing
        except Exception:
            pass
    if not isinstance(existing.get("matches"), dict):
        existing["matches"] = {}
    before = len(existing["matches"])

    added = updated = skipped_noresult = skipped_namesim = 0
    for (dt, jn), m in sorted(preds.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        r = official.get((dt, jn))
        if not r:
            skipped_noresult += 1
            continue
        res = r["result"]
        fh, fa = int(res["home"]), int(res["away"])
        # 队名一致性：主键已是 (date,num)，此处只拦截明显错配
        s1 = sim(m.get("home"), r.get("home"))
        s2 = sim(m.get("away"), r.get("away"))
        if min(s1, s2) < 0.25:
            skipped_namesim += 1
            continue
        mp = models.get((dt, jn))
        vo = outcome(m["predH"], m["predA"])
        mo = outcome(mp[0], mp[1]) if mp else None
        if mp and mo:
            hc = bool(vo == mo)
            why = "V站·模型 方向共识" if hc else "V站·模型 方向分歧"
            conf_ok = True
        else:
            hc, why, conf_ok = False, "赛前无模型预测快照，无法判定", False
        # 快照若已自带赛前固化标记（改造后的 fetch_vzhan 会写），优先沿用
        if isinstance(m.get("highConf"), bool) and m.get("confAvailable") is not False:
            hc = bool(m["highConf"])
            why = m.get("confWhy") or why
            conf_ok = True
        key = "%s|%s|%s" % (jn or dt, m.get("home"), m.get("away"))
        rec = {
            "jcNum": jn, "date": dt, "league": m.get("league") or (r.get("league") or "-"),
            "home": m.get("home"), "away": m.get("away"),
            "predH": int(m["predH"]), "predA": int(m["predA"]),
            "finalH": fh, "finalA": fa,
            "exactHit": bool(int(m["predH"]) == fh and int(m["predA"]) == fa),
            "outcomeHit": bool(vo == outcome(fh, fa)),
            "highConf": bool(hc), "confWhy": why, "confAvailable": bool(conf_ok),
            "modelPred": list(mp) if mp else None,
            "nameSim": round(min(s1, s2), 3),
            "snapshotAt": m.get("_snapshotAt") or "",
            "source": "backfill_vzhan_history.py / history.json 官方开奖回溯",
        }
        if key in existing["matches"] and not args.force:
            continue
        if key in existing["matches"]:
            updated += 1
        else:
            added += 1
        existing["matches"][key] = rec

    existing["updatedAt"] = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    existing["source"] = "V站 赛前预测 + 竞彩官方开奖回溯（backfill_vzhan_history.py）"
    existing["meta"] = {
        "version": "v1.7.126",
        "highConfRule": "V站预测方向 == 模型(modelPred)预测方向；与前端 vzConfidence() 同口径",
        "exactHitRule": "predH:predA == finalH:finalA",
        "outcomeHitRule": "预测胜平负方向 == 实际胜平负方向",
        "officialSource": "history.json 官方开奖 result（按 date + num 严格对齐）",
        "predSnapshots": len(preds), "modelIndex": len(models),
        "skippedNoResult": skipped_noresult, "skippedNameMismatch": skipped_namesim,
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)

    recs = list(existing["matches"].values())
    hot = [x for x in recs if x.get("highConf")]
    ok = [x for x in recs if x.get("confAvailable")]
    print("· 归档 %d → %d 场（新增 %d / 覆盖 %d；无赛果跳过 %d / 队名错配跳过 %d）"
          % (before, len(recs), added, updated, skipped_noresult, skipped_namesim), flush=True)
    print("· 可判定高信心口径 %d 场，其中 🔥高信心 %d 场" % (len(ok), len(hot)), flush=True)
    if hot:
        print("    高信心比分精确 %d / 方向命中 %d"
              % (sum(1 for x in hot if x["exactHit"]), sum(1 for x in hot if x["outcomeHit"])), flush=True)
    print("  全量比分精确 %d / 方向命中 %d"
          % (sum(1 for x in recs if x["exactHit"]), sum(1 for x in recs if x["outcomeHit"])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
