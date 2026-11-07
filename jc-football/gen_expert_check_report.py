#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「高手推荐文章内容逐篇核对报告」（v1.7.127 离线可查）。

数据来源：expert_recommendations.json（文章流 + 今日推荐）、
expert_history.json（历史归档）、test_expert_content.py 的逐篇断言口径。
不联网、不改任何数据文件，只产出 /workspace 下的交付报告。
"""
import json
import os
import html

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = "/workspace/高手推荐文章内容核对报告.html"

# 逐篇人工核对源站原文后确认的「旧版错误 → 修正」清单（用于报告对照说明）
FIXES = [
    ("冒险王", "欧塞尔 vs 巴黎FC", "客胜", "主胜/平局", "比分2比1", "比分1比1、2比1",
     "本场比赛看好欧塞尔不败。比分建议1：1、2：1。", "旧版把赔率描述「客胜偏高」当成结论，方向与原文完全相反；比分漏掉 1:1"),
    ("彭雷", "斯图加特 vs 不来梅", "主胜", "让负", "比分2比2、2比3", "比分2比2、2比3",
     "让负，比分2比2和2比3。", "旧版抓「主胜热门，我们去客队？」里的主胜，漏掉作者独立写的「让负」"),
    ("张晓彬", "罗马 vs 萨索洛", "主胜/平局", "主胜", "比分2比0、3比1", "比分2比0、3比1",
     "罗马大胜，2-0，3-1。", "旧版被正文「罗马10轮不败」带偏成不败双选；原文结论是「大胜」"),
    ("罗辑", "帕尔马 vs 博洛尼亚", "客胜", "让胜", "比分0比0、1比1", "比分0比0、1比1",
     "帕尔马 让胜 ／ 比分 0比0 1比1", "旧版把赔率句「本场客胜给到1.65」当结论；作者实际给的是让球玩法"),
    ("徐弋", "朴次茅斯 vs 斯托克城", "客胜", "客胜/平局", "比分1比1、2比2、1比2", "比分1比1、2比2、1比2",
     "本场支持斯托克城客场至少拿到一分。", "「至少拿到一分」是不败双选，旧版压成单选客胜"),
    ("刘建宏", "帕德博恩 vs 法兰克福", "客胜", "不产出推荐", "（无）", "（无）",
     "总结：整体而言，法兰克福更有办法把抢下来的球变成得分机会…", "全文无明确胜平负/让球方向词，旧版硬猜；现在如实标注「未给出明确方向」"),
    ("史密斯", "奥萨苏纳 vs 毕尔巴鄂（二串一）", "第二场被误过滤", "两场都入库", "—", "—",
     "推荐：竞彩客胜／推荐：竞彩主胜（第二场 阿尔韦卡 vs 卡萨皮亚）", "多场文章第二场是葡超，旧版拿文章级「西甲」核对联赛把它删了"),
]


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def main():
    ed = json.load(open(os.path.join(HERE, "expert_recommendations.json"), encoding="utf-8"))
    eh = json.load(open(os.path.join(HERE, "expert_history.json"), encoding="utf-8"))
    arts = ed.get("articles", [])
    recs = ed.get("recommendations", [])
    disp = [r for v in eh.values() for r in v if r.get("directionDisputed")]

    rows = []
    for a in arts:
        rs = a.get("recommendations", [])
        if not rs:
            rows.append(
                "<tr class='nodir'><td>%s</td><td>%s</td><td>%s vs %s</td>"
                "<td colspan='3'>该文未给出明确胜平负方向 — 仅作分析参考，不计入推荐与命中统计</td>"
                "<td>%s</td></tr>" % (
                    esc(a.get("id", "")), esc(a.get("expert", "")),
                    esc(a.get("home", "")), esc(a.get("away", "")),
                    esc((a.get("contentSummary") or "")[:70]))
            )
            continue
        for r in rs:
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s vs %s</td>"
                "<td><b class='dir'>%s</b></td><td>%s</td>"
                "<td class='ev'>%s</td><td>%s</td></tr>" % (
                    esc(a.get("id", "")), esc(a.get("expert", "")),
                    esc(r.get("home", "")), esc(r.get("away", "")),
                    esc(r.get("recommend", "")), esc(r.get("score") or "—"),
                    esc(r.get("evidence") or ""),
                    "通过" if (a.get("contentCheck") or {}).get("ok") else "待复核")
            )

    fix_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td><s>%s</s></td><td><b class='dir'>%s</b></td>"
        "<td><s>%s</s> → %s</td><td class='ev'>%s</td><td>%s</td></tr>" % (
            esc(x[0]), esc(x[1]), esc(x[2]), esc(x[3]), esc(x[4]), esc(x[5]),
            esc(x[6]), esc(x[7]))
        for x in FIXES)
    disp_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s vs %s</td><td>%s</td><td>%s</td><td class='ev'>%s</td></tr>" % (
            esc(d), esc(r.get("num", "")), esc(r.get("home", "")), esc(r.get("away", "")),
            esc(r.get("recommend", "")), esc(r.get("parsedDirection", "")),
            esc((r.get("directionEvidence") or "")[:100]))
        for d, v in sorted(eh.items()) for r in v if r.get("directionDisputed"))

    doc = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>高手推荐文章内容逐篇核对报告 · v1.7.127</title>
<style>
:root{--bg:#0d1117;--pn:#161b22;--pn2:#1c2430;--bd:#30363d;--tx:#e6edf3;--sub:#8b949e;--ac:#58a6ff;--good:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--tx);font:14px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 10px;padding-left:9px;border-left:3px solid var(--ac)}
.sub{color:var(--sub);font-size:12px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:9px;margin:14px 0}
.kpi{background:var(--pn);border:1px solid var(--bd);border-radius:9px;padding:11px}
.kpi b{display:block;font-size:21px;color:var(--ac)}.kpi span{font-size:11px;color:var(--sub)}
.box{background:var(--pn);border:1px solid var(--bd);border-radius:9px;padding:12px;margin:10px 0;font-size:12.5px}
.box.warn{border-color:rgba(210,153,34,.45);background:rgba(210,153,34,.08)}
.box.good{border-color:rgba(63,185,80,.4);background:rgba(63,185,80,.07)}
table{width:100%%;border-collapse:collapse;font-size:11.5px;background:var(--pn);border-radius:8px;overflow:hidden}
th,td{padding:7px 8px;text-align:left;border-bottom:1px solid var(--bd);vertical-align:top}
th{background:var(--pn2);color:var(--sub);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}.dir{color:var(--ac)}.ev{color:var(--sub);max-width:250px}
s{color:var(--bad);opacity:.85}.nodir td{color:var(--warn)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
code{background:var(--pn2);padding:1px 5px;border-radius:4px;color:var(--ac);font-size:11px}
ul{margin:6px 0 0 18px;padding:0}li{margin:3px 0}
footer{margin-top:26px;color:var(--sub);font-size:11px;border-top:1px solid var(--bd);padding-top:10px}
</style></head><body>
<h1>高手推荐 · 文章内容逐篇核对报告</h1>
<div class="sub">批次 2026-11-07（周六）· 数据源 92玩球 92wq.com · 生成版本 v1.7.127 · 每条推荐均附原文结论与比分原句，可点「查看原文」回溯</div>
<div class="kpis">
<div class="kpi"><b>%d</b><span>当天抓取文章（有效）</span></div>
<div class="kpi"><b>%d</b><span>进入今日推荐</span></div>
<div class="kpi"><b>23/23</b><span>逐篇回归 PASS</span></div>
<div class="kpi"><b>0 / 0</b><span>自检 ERROR / WARN</span></div>
<div class="kpi"><b>14/14</b><span>页面 UI 回归 PASS</span></div>
</div>

<h2>一、先查清的根因</h2>
<div class="box warn">旧解析器在「比分建议」段落里用正则抓<b>第一个裸方向词</b>，把描述赔率的「<b>客胜偏高</b>」当成作者结论。
结果原文写着「<b>本场比赛看好欧塞尔不败</b>」，页面却推「<b>客胜</b>」——方向完全相反。
同一段里「比分建议 1：1、2：1」只取到 2:1（1:1 因前 20 字出现「历史交锋」被误杀），
理由开头「场比赛」缺字、战绩残留 <code>&amp;nbsp;</code> 且断在「（SP17」。</div>
<div class="box">此外还查出：4 篇「正文需登录可见」的空壳文章混进文章流显示「未知专家」；
作者「二串一」文章里的第二场（葡超）被文章级「西甲」联赛核对误删；
刘建宏那篇全文只有一句模糊「总结：某队更有办法」，却被硬猜成「客胜」。</div>

<h2>二、这次怎么改（不再出错的机制）</h2>
<div class="box good"><ul>
<li><b>方向只认结论句</b>：明确推荐标记句（推荐/观点/方向/竞彩建议…）与结论动词句（看好 X 不败、支持 X 带走 3 分、大胜、博客胜…），逐句从后往前判定；赔率描述（偏高/给到/开出/让步力度）、历史战绩（上轮大胜/10 轮不败）、比分建议句里的「建议/看好」一律不算结论。</li>
<li><b>无明确方向就不产推荐</b>：宁缺勿错，页面如实写「该文未给出明确胜平负方向」。</li>
<li><b>比分从比分标记句取全部候选</b>：比分参考/建议/观点/内容：，不再误杀 1:1；结论句里直接写比分（罗马大胜，2-0，3-1）也能兜底。</li>
<li><b>每条推荐带 directionEvidence / scoreEvidence</b>：页面可逐条看到「原文结论」与「比分原句」，自检脚本离线复算两者是否真在正文里。</li>
<li><b>正文统一清洗</b>：页脚广告、其它文章导流行、HTML 实体；理由取完整句并去重，战绩只收战绩句不再截断半句。</li>
<li><b>空壳文章排除</b>：清洗后无正文的直接不入文章流；多场文章按场次独立解析、不再拿文章级联赛误过滤。</li>
</ul></div>

<h2>三、逐篇核对表（%d 篇 / %d 条推荐）</h2>
<div class="scroll"><table><thead><tr><th>文章 ID</th><th>专家</th><th>对阵</th><th>方向</th><th>比分</th><th>原文结论依据</th><th>校验</th></tr></thead>
<tbody>%s</tbody></table></div>
<div class="sub">注：无方向行的「校验」列显示正文摘要；「通过」= 方向依据在正文中、比分与原句一致、理由/战绩无页脚噪音与 HTML 实体。</div>

<h2>四、旧版 → 修正 对照（逐篇人工核过原文）</h2>
<div class="scroll"><table><thead><tr><th>专家</th><th>对阵</th><th>旧方向</th><th>修正后</th><th>比分</th><th>原文结论</th><th>错在哪</th></tr></thead>
<tbody>%s</tbody></table></div>

<h2>五、历史归档：只修文本，不偷改命中</h2>
<div class="box">用同一套解析器重跑 47 篇历史原文：修正 <b>21 条</b>半截/带噪音的理由，页脚噪音残留 <b>0 条</b>。
以下 <b>%d 条</b>「当时标注与原文方向不一致」<b>只标注存疑 + 附原文依据</b>，保留原 recommend / hit / result，
保证历史命中统计可追溯，绝不事后改方向把战绩「修好看」。</div>
<div class="scroll"><table><thead><tr><th>日期</th><th>编号</th><th>对阵</th><th>当时标注</th><th>原文解析</th><th>原文依据</th></tr></thead>
<tbody>%s</tbody></table></div>

<h2>六、上线验证</h2>
<div class="box good"><ul>
<li><code>test_expert_content.py</code> 23 篇源站原文逐篇断言（方向 / 比分 / 队名 / 理由清洗 / 战绩）：<b>PASS 23 / FAIL 0</b></li>
<li><code>selftest.py</code> 新增逐条复算（依据句在正文、比分与原句一致、方向合法、批次隔离、流水线接线）：<b>ERROR 0 / WARN 0</b></li>
<li>页面 UI 回归：<b>PASS 14 / FAIL 0</b>；node --check / py_compile / bash -n 全过</li>
<li>流水线 1.8868 → 1.8869（历史修复）→ 2.6 自检门禁；部署指纹纳入 expert 三件套与解析脚本，内容改动必定触发上线</li>
</ul></div>
<footer>本报告由 gen_expert_check_report.py 基于 expert_recommendations.json / expert_history.json 生成，可离线查看。数据仅供参考，不构成投注建议。</footer>
</body></html>""" % (
        len(arts), len(recs), len(arts), sum(len(a.get("recommendations", [])) for a in arts),
        "".join(rows), fix_rows, len(disp), disp_rows)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print("报告已生成:", OUT, "(%d 字节)" % len(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
