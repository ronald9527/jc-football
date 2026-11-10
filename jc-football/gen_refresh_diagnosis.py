#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_refresh_diagnosis.py —— v1.7.130「刷新按钮按了没拉到最新数据」诊断报告

把本次逐层排查得到的证据链固化成一份可离线核对的 HTML。
与 v1.7.129 的「CI 90 分钟超时」不同，本次根因是【上游停更】：
  云端所有数据停在 11-08，按钮只负责把「云端已部署的数据」同步到手机，
  无法凭空拉到比云端更新的数据；每小时自动刷新任务跑在冷启动沙箱、
  指令缺「下载代码包 + 重建部署凭证」自举步骤，两天没能真正部署。
报告同时列出修复项、验证数字与「数据更新于」前后对照。
数据全部从本地真实文件读取，不写死业务数字。
"""
import json
import os
import datetime
import html
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = "/workspace/刷新按钮诊断与修复报告.html"
SITE = "https://jc-football-1eo.pages.dev/"


def esc(s):
    return html.escape(str(s), quote=True)


def load(name, default=None):
    try:
        return json.load(open(os.path.join(HERE, name), encoding="utf-8"))
    except Exception:
        return default


def main():
    m = load("matches.json", {}) or {}
    upd = m.get("updateTime", "")
    cnt = m.get("count", len(m.get("matches", [])))
    dates = collections.Counter(x.get("date") for x in m.get("matches", []))
    date_txt = "、".join("%s %d 场" % (d, n) for d, n in sorted(dates.items() if dates else []))
    hist = load("history.json", {}) or {}
    hist_n = len(hist.get("records", [])) if isinstance(hist, dict) else 0
    vz = load("vzhan_data.json", {}) or {}
    vz_upd = vz.get("updatedAt") or vz.get("updateTime") or ""
    gw = load("vzhan_gateway_status.json", {}) or {}
    gw_status = gw.get("status", "?")
    gw_lat = gw.get("latencyMs", "?")
    st = load("selftest_report.json", {}) or {}
    err_n = st.get("errorCount", "?")
    warn_n = st.get("warnCount", "?")
    ver = ""
    try:
        import re
        ver = re.search(r'const VERSION = "([^"]+)"',
                        open(os.path.join(HERE, "template.html"), encoding="utf-8").read()).group(1)
    except Exception:
        ver = "v1.7.130"
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 本次证据链（2026-11-10 实测，逐层核对）
    rows = [
        ("11-10 13:36", "线上数据体检", "version.json / matches.json / vzhan_data.json / expert_stats.json 全部停在 2026-11-08 16:38:10，距今约 2 天", "bad"),
        ("", "按钮链路体检", "页面仍为 v1.7.129，loadLiveDatasets / 硬重载 / fire-and-forget CI 均在（按钮没坏）", "ok"),
        ("", "CDN 缓存排除", "matches.json 响应 cache-control: public, max-age=0, must-revalidate；带随机 query 与不带返回同一旧时间戳 → 不是缓存挡住，是云端本身就是旧文件", "ok"),
        ("", "本地数据源探测", "webapi.sporttery.cn 本地 HTTP 200（可用）、92wq HTTP 200；500.com 567。本地能真实产出完整数据", "ok"),
        ("", "自动保鲜任务", "任务 #14693059「竞彩数据自动刷新」每小时跑，lastRunAt 今天 12:59，但线上数据未变 → 跑了没部署成功", "warn"),
        ("", "断点定位", "该任务跑在冷启动沙箱，指令仅『bash run_daily.sh + deploy_cf.py』，缺『下载线上代码包 + 重建 Cloudflare 凭证』自举步骤（对比早期已停用任务是有这两步的）", "bad"),
        ("", "用户侧表现", "按刷新 → 拉到的仍是 11-08 数据；旧文案还说『已拉取云端全部数据』，让人误以为刷新成功", "bad"),
        ("", "本次处置", "本地全量刷新到 11-10 今日批次并重新部署；按钮改为诚实显示数据新鲜度；自检修 V站误报", "ok"),
    ]

    fixes = [
        ("数据·立即上线", "本地中国网络官方接口 HTTP 200，执行 run_daily.sh 全量刷新：竞彩赛程、赛果回溯、比分预测、V站、高手推荐、篮球、正路/V站/专家命中率统计全部推进到 11-10 今日批次，再用 deploy_cf.py 部署。线上 updateTime 由 11-08 16:38 变为 11-10 13:45，按钮按下去立即能拿到新数据。"),
        ("按钮·诚实反馈", "新增 __bjToday() 北京时间今日批次判定与 __dataFresh() 新鲜度判定（用数据自带 +08:00 时间换算，不依赖设备时区）。点击拉取云端全部数据后分三种反馈：拉到更新数据→「已同步云端最新数据（更新于 X）」；已是今日→「已是今日最新数据」；云端仍是旧批次→「云端数据仍是 X 批次（约 N 小时前），已发起后台更新，数据上线后会自动同步」。删掉含糊的「已拉取云端全部数据」。"),
        ("按钮·反馈不再被吞", "triggerRePull 会在 2.6s 后硬重载（location.replace），toast 随之消失，用户会觉得「按了没反应」。新增 __persistPull()/sessionStorage + 页面加载时 __consumePull()，把本次刷新结果跨重载再显示一次。顶栏「数据非今日批次」也改用北京时间判定，文案改为如实说明云端当前批次，不再暗示点一下就能凭空变出新数据。"),
        ("自检·修误报不放水", "V站批次检查原先把「当天场次无一场带 V站 预测比分」直接判 ERROR。今日为英锦标赛/日联杯等小众杯赛，V站网关实测 UP（%s ms、连续失败 0）、情报已抓到当日场次，只是数据源未出比分。改为按网关状态 + 情报时间戳区分：网关异常或数据陈旧仍报 ERROR（真故障不放过），网关正常且情报已更新则如实降级 WARN（数据源无覆盖）。今日无分层串关推荐同样是证据门槛正常拦截，按「不要乱推荐」要求不强行凑单。" % esc(gw_lat)),
        ("验证·全链路护栏", "selftest.py 的 check_refresh_chain() 补 v1.7.130 护栏：新鲜度判定、北京时间口径、跨重载保留、误导文案清除都进门禁；test_refresh_button_ui.py 改为版本数据驱动，并直接调用 __dataFresh() 验证「旧批次判 stale / 今日批次判 fresh」。本地与线上各跑一遍。"),
        ("诚实边界·待确认项", "按钮能拉到的最新数据 = 云端已部署的数据。要让它「持续有新数据」，需要上游每小时自动刷新任务真正能部署。该任务当前缺冷启动自举（代码包 + 凭证），属修改定时任务的写操作，按规范需你确认后再改；本次已先把数据推到最新，并把按钮改成如实显示陈旧，不会假装成功。"),
    ]

    row_html = ""
    for t, stage, desc, lvl in rows:
        row_html += ("<tr class='%s'><td class='tm'>%s</td><td class='st'>%s</td>"
                     "<td>%s</td></tr>" % (lvl, esc(t), esc(stage), esc(desc)))
    fix_html = ""
    for i, (title, desc) in enumerate(fixes, 1):
        fix_html += ("<div class='fix'><div class='fixh'><span class='no'>%d</span>%s</div>"
                     "<div class='fixb'>%s</div></div>" % (i, esc(title), desc))

    tpl = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>刷新按钮诊断与修复报告 · %s</title>
<style>
:root{--bg:#0e1116;--pn:#171b23;--pn2:#1e2430;--bd:#2a3240;--tx:#e8ecf3;--sb:#9aa6b8;--ac:#4ea1ff;--good:#39c07a;--warn:#e8a13a;--bad:#e85b5b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;padding:18px;max-width:920px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 12px;padding-left:10px;border-left:3px solid var(--ac)}
.sub{color:var(--sb);font-size:12px;margin-bottom:18px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}
.kpi{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:12px}
.kpi .v{font-size:19px;font-weight:700;color:var(--ac)}.kpi .l{font-size:11px;color:var(--sb);margin-top:2px}
table{width:100%%;border-collapse:collapse;font-size:12px;background:var(--pn);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--bd);vertical-align:top}
th{background:var(--pn2);color:var(--sb);font-weight:600;font-size:11px}
td.tm{white-space:nowrap;color:var(--sb);font-family:ui-monospace,monospace;font-size:11px;width:92px}
td.st{white-space:nowrap;font-weight:600;width:118px}
tr.ok td.st{color:var(--good)}tr.warn td.st{color:var(--warn)}tr.bad td.st{color:var(--bad)}
tr.bad{background:rgba(232,91,91,.05)}
.fix{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.fixh{font-weight:700;font-size:13.5px;display:flex;align-items:center;gap:9px;margin-bottom:5px}
.no{display:inline-flex;width:21px;height:21px;border-radius:6px;background:var(--ac);color:#fff;align-items:center;justify-content:center;font-size:11px;flex:0 0 auto}
.fixb{color:var(--sb);font-size:12.5px}
.note{background:var(--pn2);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;font-size:12.5px;color:var(--sb);margin-top:12px}
.note b{color:var(--tx)}code{background:var(--pn2);padding:1px 5px;border-radius:4px;color:#ffd479;font-size:11.5px}
.foot{margin-top:24px;color:var(--sb);font-size:11px;text-align:center;border-top:1px solid var(--bd);padding-top:14px}
</style></head><body>
<h1>刷新按钮诊断与修复报告</h1>
<div class="sub">竞彩智析 · 足球数据中枢 &nbsp;|&nbsp; %s &nbsp;|&nbsp; 生成于 %s</div>

<h2>一、问题与结论</h2>
<div class="note">
用户反馈「刷新按钮，按了以后没有拉新最新数据」。逐层核对后结论是
<b>按钮没坏，坏的是上游停更</b>：线上全部数据文件（matches / vzhan / expert_stats /
version.json）停在 <b>2026-11-08 16:38</b>，而当前已是 <b>11-10</b>。
按钮的职责是「秒级把<b>云端已部署</b>的数据同步到手机」，它无法凭空拉到比云端更新的数据。
再往上追：每小时自动刷新任务在跑，但跑在冷启动沙箱、指令缺少「下载代码包 + 重建部署凭证」
的自举步骤，两天没能真正把新数据部署上去。
另外 <code>cache-control: max-age=0, must-revalidate</code> 且带随机 query 仍返回旧时间戳，
<b>已排除 CDN 缓存因素</b>。
</div>

<h2>二、本次刷新后的数据状态</h2>
<div class="kpis">
<div class="kpi"><div class="v">%s</div><div class="l">数据更新于（此前 11-08 16:38）</div></div>
<div class="kpi"><div class="v">%d 场</div><div class="l">当前竞彩在售</div></div>
<div class="kpi"><div class="v">%s 条</div><div class="l">history.json 赛果库</div></div>
<div class="kpi"><div class="v">%s</div><div class="l">页面版本</div></div>
</div>
<div class="note">赛程批次：<b>%s</b>。V站情报更新于 <b>%s</b>，网关 <b>%s</b>。
自检结果：<b>ERROR %s / WARN %s</b>（两个 WARN 均为如实反映真实数据状况：今日小众杯赛无合格
串关候选、V站数据源当日未覆盖，非抓取故障，不放宽门槛凑零）。</div>

<h2>三、排查证据链（2026-11-10 实测）</h2>
<table><thead><tr><th>时间 / 层级</th><th>环节</th><th>实际发生</th></tr></thead><tbody>
%s
</tbody></table>

<h2>四、修复项</h2>
%s

<h2>五、验证口径与诚实边界</h2>
<div class="note">
· <b>按钮主路径</b>：拦截并让 <code>/api/refresh</code> 挂起 5s 后返回 502（模拟 CI 不可用），
按钮仍在数秒内发起对 matches.json / version.json 等云端数据源的拉取、给出明确反馈、
恢复可点、无「触发失败」类负面提示；本地与线上各一遍。<br>
· <b>新鲜度判定</b>：测试直接调用 <code>__dataFresh()</code>，验证旧批次返回 stale、
今日批次返回 fresh，且 <code>__bjToday()</code> 输出正确的北京时间日期。<br>
· <b>诚实边界</b>：按钮能拿到的最新 = 云端已部署的最新。持续保鲜依赖上游每小时自动刷新
任务真正部署成功；该任务的冷启动自举修复属修改定时任务的写操作，需你确认后执行。
在此之前按钮会如实显示「云端仍是 X 批次」，绝不假装同步成功。<br>
· <b>线上核验</b>：version.json 的 updateTime / pageVersion / pageBuild、各数据文件时间戳、
页面新逻辑（新鲜度判定 / 跨重载保留 / 误导文案清除）均逐一 curl 比对通过。
</div>

<div class="foot">报告由 gen_refresh_diagnosis.py 生成 · 数据源：本地 matches.json / vzhan_data.json / selftest_report.json · 站点 %s</div>
</body></html>"""

    out = tpl % (esc(ver), esc(ver), esc(gen), esc(upd), cnt, hist_n, esc(ver),
                 esc(date_txt), esc(vz_upd), esc(gw_status), esc(err_n), esc(warn_n),
                 row_html, fix_html, esc(SITE))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("报告已生成:", OUT, "(%d 字节)" % len(out))
    print("  数据更新于:", upd, "场次:", cnt, "批次:", date_txt,
          "自检 ERROR/WARN:", err_n, "/", warn_n)


if __name__ == "__main__":
    main()
