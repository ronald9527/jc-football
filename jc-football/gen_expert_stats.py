#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_expert_stats.py —— 专家命中率统一统计引擎（v1.7.128）

为什么需要它
------------
旧链路里 fetch_expert.py 是「先算 expert_stats、后做赛果回填」，
导致本轮回填的赛果要等下一轮才进命中率；而流水线里专家抓取又排在
sp_results.py 赛果回填之前，统计永远是上一轮口径。此外：
  * backfill_history() 有 `if date >= today: continue`，当天已完赛场次
    永远不结算；
  * 只有 11 条主推荐带 expertStats，同场其他专家与 23 篇文章里的推荐
    一条命中率都没有；
  * 页面「专家排行榜」按推荐分排序、默认折叠，不是命中率榜。

本脚本作为「赛后回溯之后」的单一统计入口，做四件事：
  1. 用与抓取模块同源的 backfill_history() 重跑赛果回填（幂等，
     只对 hit 为空且有官方赛果的记录结算），写回 expert_history.json；
  2. 用同源 calculate_expert_stats() 复算每位专家战绩，再补
     Wilson 95% CI、样本层级、排行榜名次；
  3. 把命中率回填到 expert_recommendations.json 的【全部】推荐
     （主推 / otherExperts / 文章流），保证页面每条推荐都有徽章；
  4. 产出 expert_stats.json、expert_leaderboard.json 与可离线核对的
     /workspace/专家命中率排行榜.html。

口径（诚实优先，绝不事后改战绩）
--------------------------------
  * 命中判定完全复用 fetch_expert.judge_hit（胜平负 / 让球 / 双选），
    已结算的 hit 一律保留，只补未结算且能判定的记录；
  * 找不到官方赛果或方向文本无法判定的，保持 pending 并在报告里列出；
  * 命中率 = 命中 / 已判定；样本 <3 场标「样本不足」，不参与榜首竞争，
    避免「1 战 1 中 100%」霸榜；
  * Wilson 95% CI 只作不确定性提示，命中率高不代表可盈利（不含赔率/ROI）。
"""
import os
import sys
import time
import html
import json
from string import Template

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

# 与抓取模块同源：赛果回填、命中率计算、读写
from fetch_expert import (  # noqa: E402
    calculate_expert_stats, backfill_history, load_json, save_json,
)

OUT_REPORT = "/workspace/专家命中率排行榜.html"
SLIM_KEYS = ("total", "judged", "hits", "miss", "pending", "hitRate",
             "recent10Rate", "streak", "score", "isNew", "sampleTier",
             "ciLow", "ciHigh", "tierLabel")


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def wilson(hits, n, z=1.96):
    """Wilson score 95% 置信区间（百分数，保留 1 位）。n=0 返回 (0,0)。"""
    if n <= 0:
        return 0.0, 0.0
    p = hits / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return round(max(0.0, center - half) * 100, 1), round(min(1.0, center + half) * 100, 1)


def sample_tier(judged):
    if judged >= 5:
        return "足量", "样本足量"
    if judged >= 3:
        return "偏少", "样本偏少"
    if judged >= 1:
        return "不足", "样本不足"
    return "无战绩", "暂无往绩"


def slim(s):
    """塞进每条推荐的精简战绩对象（不把 recent 明细复制 N 份）。"""
    if not s:
        tier, label = sample_tier(0)
        return {"judged": 0, "hits": 0, "miss": 0, "pending": 0,
                "hitRate": 0, "recent10Rate": 0, "streak": 0, "isNew": True,
                "sampleTier": tier, "tierLabel": label, "ciLow": 0,
                "ciHigh": 0, "total": 0, "score": 0}
    return {k: s.get(k) for k in SLIM_KEYS if k in s}


def rank_key(s):
    """排行榜：命中率为主序，样本门槛只用于「沉底」。

    已判定 ≥3 场的专家进入主榜，直接按命中率降序（再按 Wilson 下限、
    已判定场次）；1-2 场的样本不足者整体沉到底部，避免「1 战 1 中 100%」
    霸榜，也避免「0% 但场次多」反被顶到第一。
    """
    judged = int(s.get("judged", 0))
    group = 0 if judged >= 3 else (1 if judged >= 1 else 2)
    return (group, -float(s.get("hitRate", 0)), -float(s.get("ciLow", 0)),
            -judged, str(s.get("expert", "")))


def main():
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    md = load_json("matches.json") or {}
    matches = md.get("matches", []) if isinstance(md, dict) else (md or [])
    history = load_json("expert_history.json") or {}
    ed = load_json("expert_recommendations.json") or {}

    # ---------- 1. 赛后赛果回填（幂等，只补未结算且有官方赛果的） ----------
    before_pending = sum(1 for v in history.values() for r in v
                         if r.get("hit") is None)
    before_pending += sum(1 for v in history.values() for r in v
                          for o in r.get("otherExperts", []) if o.get("hit") is None)
    newly = backfill_history(history, matches)
    save_json("expert_history.json", history)
    after_pending = sum(1 for v in history.values() for r in v
                        if r.get("hit") is None)
    after_pending += sum(1 for v in history.values() for r in v
                         for o in r.get("otherExperts", []) if o.get("hit") is None)

    # ---------- 2. 同源复算战绩 + 增补统计字段 ----------
    stats = calculate_expert_stats(history) or {}
    for name, s in stats.items():
        lo, hi = wilson(s.get("hits", 0), s.get("judged", 0))
        tier, label = sample_tier(s.get("judged", 0))
        s["ciLow"], s["ciHigh"] = lo, hi
        s["sampleTier"], s["tierLabel"] = tier, label
        s.setdefault("expert", name)

    leaderboard = []
    for s in sorted(stats.values(), key=rank_key):
        row = {k: s.get(k) for k in (
            "expert", "total", "judged", "hits", "miss", "pending", "hitRate",
            "recent10Rate", "streak", "score", "isNew", "sampleTier",
            "tierLabel", "ciLow", "ciHigh")}
        row["recent"] = (s.get("recent") or [])[:5]
        leaderboard.append(row)
    for i, row in enumerate(leaderboard, 1):
        row["rank"] = i

    total_recs = sum(len(v) for v in history.values())
    total_recs += sum(len(r.get("otherExperts", [])) for v in history.values() for r in v)
    judged_recs = sum(1 for s in stats.values() for _ in range(s.get("judged", 0)))
    hits_all = sum(s.get("hits", 0) for s in stats.values())
    pending_all = sum(s.get("pending", 0) for s in stats.values())
    meta = {
        "generatedAt": ts,
        "source": "expert_history.json + matches.json + history.json 官方赛果",
        "historyRecords": total_recs,
        "judgedRecords": judged_recs,
        "hits": hits_all,
        "pendingRecords": pending_all,
        "newlySettled": newly,
        "pendingBefore": before_pending,
        "pendingAfter": after_pending,
        "experts": len(stats),
        "scope": "已结算场次才计入命中率；未开奖/无官方赛果/方向无法判定一律保持待结算",
        "rankRule": "已判定≥3场进主榜按命中率降序（再按Wilson下限、已判定场次）；1-2场样本不足者整体沉底，避免1战1中100%霸榜",
        "ciNote": "Wilson 95% 置信区间只反映样本不确定性；命中率高不等于可盈利，本统计不含赔率与 ROI",
    }

    save_json("expert_stats.json", {
        "updatedAt": ts, "generatedAt": ts, "source": meta["source"],
        "scope": meta["scope"], "stats": stats,
        "leaderboard": leaderboard, "meta": meta,
    })
    save_json("expert_leaderboard.json", {
        "updatedAt": ts, "generatedAt": ts, "meta": meta,
        "leaderboard": leaderboard,
    })

    # ---------- 3. 回填【全部】推荐的命中率 ----------
    attached = {"main": 0, "others": 0, "articles": 0}
    for r in ed.get("recommendations", []):
        r["expertStats"] = slim(stats.get(r.get("expertName", "")))
        attached["main"] += 1
        for o in r.get("otherExperts", []):
            st = slim(stats.get(o.get("expertName", "")))
            o["expertStats"] = st
            o["hitRate"] = st.get("hitRate", 0)
            o["judged"] = st.get("judged", 0)
            o["isNew"] = st.get("isNew", True)
            attached["others"] += 1
    for a in ed.get("articles", []):
        st = slim(stats.get(a.get("expert", "")))
        for r in a.get("recommendations", []):
            r["expertStats"] = st
            r.setdefault("expertName", a.get("expert", ""))
            attached["articles"] += 1
    ed["expertStats"] = stats
    ed["leaderboard"] = leaderboard
    ed["leaderboardMeta"] = meta
    ed["leaderboardUpdatedAt"] = ts
    save_json("expert_recommendations.json", ed)

    # ---------- 4. 离线可核对报告 ----------
    write_report(stats, leaderboard, meta, attached, history)

    print("[专家命中率] 赛后回填 %d 条（待结算 %d → %d）" %
          (newly, before_pending, after_pending))
    print("[专家命中率] %d 位专家 / 已判定 %d 条 / 命中 %d 条 / 待结算 %d 条" %
          (len(stats), judged_recs, hits_all, pending_all))
    print("[专家命中率] 命中率已附到：主推 %d / 同场其他 %d / 文章流 %d 条" %
          (attached["main"], attached["others"], attached["articles"]))
    print("[专家命中率] 排行榜 Top5: %s" % ", ".join(
        "%s %.1f%%(%d场)" % (x["expert"], x["hitRate"], x["judged"])
        for x in leaderboard[:5]))
    print("[专家命中率] 报告: %s" % OUT_REPORT)
    return 0


def write_report(stats, leaderboard, meta, attached, history):
    rows = []
    for x in leaderboard:
        rate = x.get("hitRate", 0)
        cls = "hi" if rate >= 60 else ("mid" if rate >= 50 else "lo")
        tier = x.get("sampleTier", "无战绩")
        ci = ("%.1f%% ~ %.1f%%" % (x.get("ciLow", 0), x.get("ciHigh", 0))
              if x.get("judged", 0) else "—")
        rows.append(
            "<tr class='%s'><td><b>%s</b></td><td>%s</td>"
            "<td><b class='rate'>%.1f%%</b></td><td>%s</td>"
            "<td>%d / %d / %d</td><td>%.1f%%</td><td>%d</td>"
            "<td>%s</td><td>%d</td></tr>" % (
                cls, x.get("rank"), esc(x.get("expert", "")), rate, ci,
                x.get("hits", 0), x.get("miss", 0), x.get("pending", 0),
                x.get("recent10Rate", 0), x.get("streak", 0),
                esc(tier), x.get("judged", 0)))
    pending_rows = []
    for d, recs in sorted(history.items()):
        for r in recs:
            if r.get("hit") is None:
                pending_rows.append(
                    "<tr><td>%s</td><td>%s</td><td>%s vs %s</td><td>%s</td><td>主推荐</td></tr>" % (
                        esc(d), esc(r.get("expertName", "")), esc(r.get("home", "")),
                        esc(r.get("away", "")), esc(r.get("recommend", ""))))
            for o in r.get("otherExperts", []):
                if o.get("hit") is None:
                    pending_rows.append(
                        "<tr><td>%s</td><td>%s</td><td>%s vs %s</td><td>%s</td><td>同场其他</td></tr>" % (
                            esc(d), esc(o.get("expertName", "")), esc(r.get("home", "")),
                            esc(r.get("away", "")), esc(o.get("recommend", ""))))
    doc = Template("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>专家命中率排行榜 · 赛后回溯统计</title>
<style>
:root{--bg:#0d1117;--pn:#161b22;--pn2:#1c2430;--bd:#30363d;--tx:#e6edf3;--sub:#8b949e;--ac:#58a6ff;--good:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--tx);font:14px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 10px;padding-left:9px;border-left:3px solid var(--ac)}
.sub{color:var(--sub);font-size:12px;margin-bottom:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(115px,1fr));gap:9px;margin:14px 0}
.kpi{background:var(--pn);border:1px solid var(--bd);border-radius:9px;padding:11px}
.kpi b{display:block;font-size:20px;color:var(--ac)}.kpi span{font-size:11px;color:var(--sub)}
.box{background:var(--pn);border:1px solid var(--bd);border-radius:9px;padding:12px;margin:10px 0;font-size:12.5px}
.box.good{border-color:rgba(63,185,80,.4);background:rgba(63,185,80,.07)}
.box.warn{border-color:rgba(210,153,34,.45);background:rgba(210,153,34,.08)}
table{width:100%;border-collapse:collapse;font-size:11.5px;background:var(--pn);border-radius:8px;overflow:hidden}
th,td{padding:7px 8px;text-align:left;border-bottom:1px solid var(--bd);vertical-align:top}
th{background:var(--pn2);color:var(--sub);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
.rate{color:var(--ac)}tr.hi .rate{color:var(--good)}tr.lo .rate{color:var(--bad)}
tr.不足 td,tr.无战绩 td{color:var(--sub)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
code{background:var(--pn2);padding:1px 5px;border-radius:4px;color:var(--ac);font-size:11px}
ul{margin:6px 0 0 18px;padding:0}li{margin:3px 0}
footer{margin-top:24px;color:var(--sub);font-size:11px;border-top:1px solid var(--bd);padding-top:10px}
</style></head><body>
<h1>专家命中率排行榜 · 赛后回溯统计</h1>
<div class="sub">生成于 $ts · 数据源：92玩球专家文章 + 竞彩官方赛果（history.json）· 本统计与页面「高手推荐」同源，赛果回填后自动重算</div>
<div class="kpis">
<div class="kpi"><b>$experts</b><span>有记录专家</span></div>
<div class="kpi"><b>$judged</b><span>已结算场次</span></div>
<div class="kpi"><b>$hits</b><span>命中场次</span></div>
<div class="kpi"><b>$pending</b><span>待结算场次</span></div>
<div class="kpi"><b>$newly</b><span>本轮新回填</span></div>
<div class="kpi"><b>$attached</b><span>已附命中率的推荐</span></div>
</div>

<h2>一、命中率排行榜（按命中率排序，样本分层防虚高）</h2>
<div class="scroll"><table><thead><tr><th>#</th><th>专家</th><th>命中率</th><th>Wilson 95% CI</th>
<th>中/错/待</th><th>近10场</th><th>连红</th><th>样本层级</th><th>已判定</th></tr></thead>
<tbody>$rows</tbody></table></div>
<div class="sub">排序：已判定 ≥3 场进入主榜，直接按命中率降序（再按 Wilson 下限与已判定场次）；1-2 场样本不足者整体沉底，避免「1 战 1 中 100%」霸榜。样本层级（足量/偏少/不足/无战绩）仅作提示，不改变主榜的命中率次序。</div>

<h2>二、统计口径（诚实优先）</h2>
<div class="box warn"><ul>
<li><b>命中率 = 命中 / 已判定</b>；只有能找到官方赛果、且方向文本能被判定（胜平负 / 让球 / 双选）的记录才结算。</li>
<li><b>未开奖、无官方赛果、方向无法判定</b>一律保持待结算，绝不猜、绝不把未结算算成不中。</li>
<li><b>已结算的命中一律保留</b>：赛后回溯只补 hit 为空的记录，不事后改方向、不改历史战绩。</li>
<li><b>Wilson 95% CI</b> 只反映样本不确定性，样本越少区间越宽；命中率高 <b>不等于</b> 可盈利，本统计不含赔率、返奖率与 ROI。</li>
</ul></div>
<div class="box good"><b>本次全部推荐已附命中率：</b>主推荐 $main 条 · 同场其他专家 $others 条 · 文章流推荐 $articles 条。
页面每条专家推荐（今日推荐 / 同场其他 / 专家文章 / 首页比赛卡）都显示「命中率X%(N场)」或如实标「暂无往绩」。</div>

<h2>三、仍待结算的记录（$pcount 条）</h2>
<div class="scroll"><table><thead><tr><th>日期</th><th>专家</th><th>对阵</th><th>当时标注</th><th>位置</th></tr></thead>
<tbody>$pending_rows</tbody></table></div>
<div class="sub">这些场次暂无可对齐的官方赛果或方向文本无法判定，保持待结算；赛果补齐后下一次统计自动纳入。</div>

<h2>四、赛后回溯链路</h2>
<div class="box"><ul>
<li><code>run_daily.sh</code> 在赛果回填（sp_results.py / fetch_scores.py）之后无条件执行 <code>gen_expert_stats.py</code>，再重建页面。</li>
<li>修正了 <code>fetch_expert.backfill_history()</code> 里「跳过当天记录」的 bug：当天已完赛场次现在也能结算。</li>
<li><code>selftest.py</code> 离线复算命中率与排行榜，确保页面数字与 expert_history.json 完全一致。</li>
<li>本轮：待结算 $pb → $pa，新回填 $newly 条。</li>
</ul></div>
<footer>本报告由 gen_expert_stats.py 基于 expert_history.json / matches.json / history.json 生成，可离线核对。数据仅供参考，不构成投注建议。</footer>
</body></html>""").substitute(
        ts=esc(meta["generatedAt"]), experts=meta["experts"],
        judged=meta["judgedRecords"], hits=meta["hits"],
        pending=meta["pendingRecords"], newly=meta["newlySettled"],
        attached=attached["main"] + attached["others"] + attached["articles"],
        rows="".join(rows), main=attached["main"], others=attached["others"],
        articles=attached["articles"], pcount=len(pending_rows),
        pending_rows="".join(pending_rows) or
        "<tr><td colspan='5'>无待结算记录</td></tr>",
        pb=meta["pendingBefore"], pa=meta["pendingAfter"])
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(doc)
    print("[专家命中率] 离线报告: %s (%d 字节)" % (OUT_REPORT, len(doc)))


if __name__ == "__main__":
    raise SystemExit(main())
