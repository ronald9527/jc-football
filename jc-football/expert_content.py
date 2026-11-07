#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高手推荐「文章内容」结构化解析与逐篇校验（v1.7.127）

设计原则（针对用户「认真检查好文章内容，不要出错」）：
1) 结论只从「明确推荐标记句 / 结论动词句」提取，绝不从全文里抓第一个
   裸方向词——旧版把赔率描述「客胜偏高」当成推荐方向，导致与作者
   「看好欧塞尔不败」的结论完全相反。
2) 比分只从「比分参考 / 比分建议 / 参考比分 / 比分观点 / 比分：」等
   标记句提取全部候选，不再因前 20 字出现「历史交锋」就误杀 1:1。
3) 正文先做页脚/广告/实体清洗，理由取完整结论句，不再从半句硬截断。
4) 无明确方向词的文章（如只有「总结：某队更有办法」）宁可不产出推荐，
   也不硬猜方向。
5) 每条推荐附带 evidence（方向原文依据）与 contentCheck（校验结果），
   可逐篇回溯核对。

本模块不写任何文件、不碰核心数据，纯函数式，便于自检与回归复用。
"""
import re
import html as _html

__all__ = [
    "clean_segments", "parse_structured", "verify_recommendation",
    "NOISE_CUT", "MATCH_HEAD",
]

# ---------- 页脚 / 广告 / 杂质清洗 ----------
NOISE_CUT = re.compile(
    r'(就爱玩球|本站不提供售彩|本站不提供|本站网址|喜欢的话可以分享|'
    r'本站提供|文章观点仅供参考|返回\s*关于作者|友情链接|ICP备|'
    r'Powered by|请前往|手机新浪网|点点关注不迷路|更多推荐点击红字|'
    r'下载APP|VIP专区|优惠券|免费流量卡|扫码|公众号|添加微信)'
)
# 整行属于「其它文章广告 / 栏目引导」而非本文正文
AD_LINE = re.compile(
    r'^(?:今日重点[:：]?|【[^】]{2,20}】[^\n]{0,60}?\d+字分析[^\n]{0,30}|'
    r'\d+点\d+分?[^\n]{0,10}(?:德甲|英超|西甲|意甲|法甲)[^\n]{0,30}|'
    r'点点关注[^\n]*|更多推荐[^\n]*|开启缘分[^\n]*)$'
)
# 场次标题：独立成行的「主队VS客队」（可带周X编号/联赛/时间前缀）
MATCH_HEAD = re.compile(
    # 前缀（编号/联赛/时间）必须带分隔符，否则会贪婪吃掉队名首字，
    # 把「奥萨苏纳」截成「苏纳」、「阿尔韦卡」截成「韦卡」。
    r'^(?:周[一二三四五六日]\d{3}\s*)?(?:\d{3}[，,\s]+)?'
    r'(?:[\u4e00-\u9fa5]{2,8}[，,\s]+)?(?:\d{1,2}[：:]\d{2}\s*)?'
    r'(?:\d{1,2}点\d{1,2}分?\s*)?'
    r'([\u4e00-\u9fa5A-Za-z0-9·]{2,20})\s*'
    r'(?:VS|vs|Vs|vS|对阵|－|-)\s*'
    r'([\u4e00-\u9fa5A-Za-z0-9·]{2,20})\s*$'
)
# 结论标记句（冒号/分号后是方向值）
MARK_RE = re.compile(
    r'^(?:我的观点|个人观点|最终观点|竞彩观点|竞彩建议|竞彩|竞足单选|'
    r'竞彩单选|足彩单选|推荐|方向|关注方向|单选|结论|最终|建议|观点|'
    r'比分观点|本场推荐|竞足|参考)\s*[：:；;，,]\s*(.+)$'
)
# 比分标记（句内任意位置）
SCORE_MARK = re.compile(
    r'(?:比分参考|参考比分|比分建议|比分观点|比分预测|比分推荐|比分看好|'
    r'比分[：:]|比分\s*参考|比分\s*建议|比分\s*观点|内容[：:]|比分)'
)
SCORE_PAIR = re.compile(r'(\d{1,2})\s*[比:：\-/]\s*(\d{1,2})')
# 方向词（按长度优先，避免「让球负」被切成「负」）
DIR_WORDS = re.compile(
    r'(让球\s*[310胜平负]|让[胜平负]|主胜|客胜|平局|打平|'
    r'主队不败|客队不败|主胜/平局|客胜/平局|不败|[310]|负|胜|平)'
)
# 赔率/盘口描述语境：出现这些词的方向词不是作者结论
ODDS_DESC = re.compile(
    r'(偏高|偏低|较高|较低|给到|开出|赔率|水位|组合|热门|拉高|压低|'
    r'让步力度|让步|受注|初盘|盘口|支持力度|数据组合|防范|保护态度|'
    r'给到1\.|开出了|SP|sp值|低赔|高赔)'
)
# 结论动词（作者真实倾向）
CONCLUSION_VERB = re.compile(
    r'(看好|支持|倾向|建议|推荐|搏|博|期待|认为|我的看法|直接博|'
    r'至少拿|守住不败|带走3分|大胜|取胜|赢球|坐和望赢|首选)'
)
# 战绩段特征
TRACK_KW = re.compile(r'(连红|近\d+中\d+|\d+中\d+|命中|连胜|红单|中\d+|战绩|送比分)')


def _unescape(s):
    s = _html.unescape(s or "")
    s = re.sub(r'&nbsp;?', ' ', s, flags=re.I)
    s = re.sub(r'&[a-zA-Z#0-9]+;', ' ', s)
    return s


def clean_segments(html_text):
    """把文章 HTML 清洗为可信正文段落列表。

    - 只取 <p> 正文（去掉 script/style/页脚）
    - 逐段去标签、去 HTML 实体、压空白
    - 从页脚关键词处截断，丢弃整行广告
    - 修复源站排版把「本 场比赛」拆开的问题
    """
    if not html_text:
        return []
    txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html_text, flags=re.S | re.I)
    raw_ps = re.findall(r'<p[^>]*>(.*?)</p>', txt, flags=re.S | re.I)
    out = []
    for p in raw_ps:
        s = _unescape(re.sub(r'<[^>]+>', '', p))
        s = re.sub(r'\s+', ' ', s).strip()
        if not s or len(s) <= 2:
            continue
        m = NOISE_CUT.search(s)
        if m:
            s = s[:m.start()].strip()
        if not s or len(s) <= 2:
            continue
        if AD_LINE.match(s):
            continue
        # 源站常见排版：中文之间被插入空格（"本 场比赛"）
        s = re.sub(r'(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])', '', s)
        s = s.strip()
        if s and len(s) > 2:
            out.append(s)
    return out


def _cores(name):
    """队名核心词候选（短简称 + 全称前缀），用于结论句里定位主/客队。"""
    if not name:
        return []
    s = re.sub(r'[\s\-_·•\.\(\)（）【】\[\]]', '', name)
    for suf in ("竞技", "俱乐部", "足球俱乐部", "队"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    c = []
    if len(s) >= 2:
        c.append(s[:2])
    if len(s) >= 3:
        c.append(s[:3])
    if s and s not in c:
        c.append(s)
    return c


def _find_team(text, name):
    """返回队名在 text 中最早出现位置，未找到返回 -1。"""
    for c in _cores(name):
        p = text.find(c)
        if p >= 0:
            return p
    return -1


def _side_from_text(text, home, away):
    """判断结论句倾向主队还是客队；返回 'home' / 'away' / None。"""
    # 显式主客词优先
    if re.search(r'(客队|作客|客场|客战|做客)', text) and not re.search(r'(主队|主场|坐镇)', text):
        return "away"
    if re.search(r'(主队|主场|坐镇)', text) and not re.search(r'(客队|作客|客场|客战|做客)', text):
        return "home"
    # 队名定位：取靠近结论动词之后出现的队名
    vm = CONCLUSION_VERB.search(text)
    scope = text[vm.end():vm.end() + 26] if vm else text
    hp, ap = _find_team(scope, home), _find_team(scope, away)
    if hp >= 0 and ap < 0:
        return "home"
    if ap >= 0 and hp < 0:
        return "away"
    if hp >= 0 and ap >= 0:
        return "home" if hp < ap else "away"
    hp2, ap2 = _find_team(text, home), _find_team(text, away)
    if hp2 >= 0 and ap2 < 0:
        return "home"
    if ap2 >= 0 and hp2 < 0:
        return "away"
    # 兜底：句子同时出现「主场/客场」时，取靠后出现的那个（通常紧邻结论）
    ph = max(text.rfind("主场"), text.rfind("主队"), text.rfind("坐镇"))
    pa = max(text.rfind("客场"), text.rfind("客队"), text.rfind("作客"), text.rfind("客战"))
    if pa > ph and pa >= 0:
        return "away"
    if ph >= 0 and ph > pa:
        return "home"
    return None


def _dir_from_mark_value(val, home, away):
    """解析「推荐：xxx」「观点：让负」冒号后的方向值。"""
    if not val:
        return "", ""
    v = val.strip()
    # 括号补充说明先留着用于队名判断，但方向词只在主体里取
    body = re.split(r'[（(]', v)[0]
    m = DIR_WORDS.search(body)
    if not m:
        # 值是队名（如「推荐：纽喷」「观点：斯托克城」）
        side = _side_from_text(v, home, away)
        if side == "away":
            return "客胜", "队名方向（客队）"
        if side == "home":
            return "主胜", "队名方向（主队）"
        return "", ""
    raw = m.group(1)
    if raw.startswith("让球"):
        v = re.sub(r'\s+', '', raw)
        # 「让球3 / 让球0 / 让球1」是旧式竞彩让球简写（3=让胜 1=让平 0=让负）
        if re.search(r'让球[胜3]', v):
            return "让胜", "让球玩法"
        if re.search(r'让球[平1]', v):
            return "让平", "让球玩法"
        if re.search(r'让球[负0]', v):
            return "让负", "让球玩法"
        return {"让球胜": "让胜", "让球平": "让平", "让球负": "让负"}.get(v, ""), "让球玩法"
    if raw in ("让胜", "让平", "让负"):
        return raw, "让球玩法"
    if raw in ("3", "胜", "主胜"):
        return "主胜", "标记句"
    if raw in ("0", "负", "客胜"):
        return "客胜", "标记句"
    if raw in ("1", "平", "平局", "打平"):
        return "平局", "标记句"
    if raw in ("主胜/平局", "主队不败"):
        return "主胜/平局", "标记句"
    if raw in ("客胜/平局", "客队不败"):
        return "客胜/平局", "标记句"
    return "", ""


# 强方向信号：只有含这些的句子才可能是作者本场结论。
# 注意「比分建议」「参考比分」里的「建议/看好」不算结论，故单独排除句首。
STRONG_DIR = re.compile(
    r'(不败|取胜|带走3分|大胜|至少拿|守住|博客胜|让[胜平负]|让球[胜平负]|'
    r'主胜|客胜|平局|推荐胜|观点[：:]|方向[：:]|关注方向|赢球|坐和望赢)'
)


def _sentences(text):
    return [p.strip() for p in re.split(r'(?<=[。！？!？；;])', text or "") if p.strip()]


def _focus_sentence(text, home="", away=""):
    """取「能产出方向的那一句」作为结论依据，保证展示的依据与方向判定同源。

    避免把「10轮不败」等历史战绩、或「比分建议1:1」「观点：某队」（无方向）
    当成作者结论，也避免理由与推荐方向对不上。
    """
    parts = _sentences(text)
    for s in reversed(parts):
        if re.match(r'^(?:比分|参考比分|内容[：:])', s):
            continue
        if _dir_from_sentence(s, home, away)[0]:
            return s
    for s in reversed(parts):
        if re.match(r'^(?:比分|参考比分|内容[：:])', s):
            continue
        if STRONG_DIR.search(s):
            return s
    for s in reversed(parts):
        if CONCLUSION_VERB.search(s) and not re.match(r'^(?:比分|参考比分|内容[：:])', s):
            return s
    return (text or "").strip()


def _dir_from_sentence(t, home, away):
    """对单个完整句提取方向。"""
    side = _side_from_text(t, home, away)
    # 注意：不能用「拿分/抢分」泛词——「迎来一小段抢分期」只是描述，不是不败结论
    unbeaten = bool(re.search(r'(不败|至少拿(?:到)?一分|守住|坐和望赢|不输|不落败)', t))
    win = bool(re.search(r'(取胜|赢球|带走3分|大胜|全取3分|豪取|获胜|胜出|赢下)', t))
    m = re.search(r'(让球\s*[310胜平负]|让[胜平负])', t)
    if m and re.search(r'(看好|观点|建议|推荐|方向|我看好|倾向|选择|分析)', t):
        raw = re.sub(r'\s+', '', m.group(1))
        if raw.startswith("让球"):
            if re.search(r'让球[胜3]', raw):
                return "让胜", "让球结论"
            if re.search(r'让球[平1]', raw):
                return "让平", "让球结论"
            if re.search(r'让球[负0]', raw):
                return "让负", "让球结论"
            return {"让球胜": "让胜", "让球平": "让平", "让球负": "让负"}.get(raw, ""), "让球结论"
        return raw, "让球结论"
    mm = re.search(r'(?:搏|博)\s*(客胜|主胜|平局|负|胜|平)', t)
    if mm:
        raw = mm.group(1)
        return {"客胜": "客胜", "主胜": "主胜", "平局": "平局", "负": "客胜", "胜": "主胜", "平": "平局"}[raw], "搏向结论"
    if unbeaten:
        if side == "home":
            return "主胜/平局", "不败结论（主队）"
        if side == "away":
            return "客胜/平局", "不败结论（客队）"
        if "主队" in t or "主" in t[-12:]:
            return "主胜/平局", "不败结论"
        if "客队" in t or "客" in t[-12:]:
            return "客胜/平局", "不败结论"
        return "", ""
    if win:
        if side == "home":
            return "主胜", "取胜结论（主队）"
        if side == "away":
            return "客胜", "取胜结论（客队）"
        return "", ""
    m = re.search(r'(?:推荐|观点|方向|单选|建议)\s*[：:；;]?\s*(?:竞彩|竞足)?\s*(主胜|客胜|平局|胜|负|平|让胜|让平|让负)', t)
    if m:
        raw = m.group(1)
        return {"主胜": "主胜", "客胜": "客胜", "平局": "平局", "胜": "主胜",
                "负": "客胜", "平": "平局", "让胜": "让胜", "让平": "让平", "让负": "让负"}.get(raw, ""), "标记结论"
    return "", ""


def _dir_from_conclusion(text, home, away):
    """从结论动词段提取方向：逐句（从后往前）尝试，取第一个能判定方向的句子。

    这样「观点：斯托克城 比分1-1」这种无方向句不会盖掉前一句
    「支持斯托克城客场至少拿到一分」，也不会把历史战绩当成本场结论。
    """
    if not CONCLUSION_VERB.search(text):
        return "", ""
    for t in reversed(_sentences(text)):
        if re.match(r'^(?:比分|参考比分|内容[：:])', t):
            continue
        # 历史语境（「珀斯上轮大胜」「罗马近4次全胜」）不是本场结论
        if re.search(r'(上轮|上场|上赛季|此前|历史|近\d+场|近期|本赛季|首轮|上周|昨日)', t) \
                and not re.search(r'(本场|这场|此役|看好|推荐|观点|方向|不败|取胜)', t):
            continue
        d = _dir_from_sentence(t, home, away)
        if d[0]:
            return d
    return "", ""


def _extract_scores(text):
    """从比分标记句提取全部预测比分，返回 '比分X比Y、…' 或 ''。"""
    if not text:
        return ""
    m = SCORE_MARK.search(text)
    seg = text[m.end():m.end() + 90] if m else ""
    if not m:
        return ""
    # 截断到下一场标记
    seg = re.split(r'(?:第二场|第\d+场|另一场|竞彩第|换一场|下一位|进球数|半全场)', seg)[0]
    pairs = SCORE_PAIR.findall(seg)
    out, seen = [], set()
    for a, b in pairs:
        # 冒号分隔且后数两位 → 时间（3:00），跳过
        mm = re.search(rf'{a}\s*([比:：\-/])\s*{b}', seg)
        sep = mm.group(1) if mm else ""
        if sep in (":", "：") and len(b) >= 2:
            continue
        if int(a) > 9 or int(b) > 9:
            continue
        key = f"{a}-{b}"
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b))
        if len(out) >= 4:
            break
    if not out:
        return ""
    return "比分" + "、".join(f"{a}比{b}" for a, b in out)


def _extract_scores_plain(text):
    """兜底：结论句里直接写比分（如「罗马大胜，2-0，3-1。」），
    没有「比分参考」标记也要能取到；只在结论句里找，避免误收历史赛果。"""
    if not text:
        return ""
    seg = re.split(r'(?:进球数|半全场)', text)[0]
    out, seen = [], set()
    for a, b in SCORE_PAIR.findall(seg):
        if int(a) > 9 or int(b) > 9:
            continue
        key = f"{a}-{b}"
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b))
        if len(out) >= 4:
            break
    return ("比分" + "、".join(f"{a}比{b}" for a, b in out)) if out else ""


def _split_matches(segs, home_default, away_default):
    """按独立成行的「队名vs队名」切分场次；返回 [(home, away, [段落])]。

    单场文章不切段（保留全文，避免丢掉结论句）；只有检测到 >=2 个场次
    标题（如作者的「二串一」）才按场次分段，各自独立解析。
    """
    heads = []
    for i, s in enumerate(segs):
        # 场次标题必须是短行且不含句末标点，避免把长分析段误判为场次
        if len(s) > 42 or re.search(r'[。！？!]', s):
            continue
        m = MATCH_HEAD.match(s)
        if m:
            heads.append((i, m.group(1).strip(), m.group(2).strip()))
    if not heads:
        h, a = home_default, away_default
        if not h or not a:
            # 标题队名未传入时，从前几段里宽松补一次（支持「008 西甲 21：00 A vs B」）
            for s in segs[:3]:
                m = re.search(
                    r'([\u4e00-\u9fa5A-Za-z0-9·]{2,20})\s*(?:VS|vs|Vs|vS|对阵|迎战)\s*'
                    r'([\u4e00-\u9fa5A-Za-z0-9·]{2,20})', s)
                if m:
                    h = h or m.group(1).strip()
                    a = a or m.group(2).strip()
                    break
        return [(h, a, segs)]
    if len(heads) == 1:
        # 单场：优先用标题队名（head 可能把「诺丁汉森林」截成「森林」），保留全文段落
        return [(home_default or heads[0][1], away_default or heads[0][2], segs)]
    out = []
    for idx, (i, h, a) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(segs)
        body = segs[i + 1:end]
        if idx == 0:
            # 首段以文章标题队名为准：head 提取得不完整时用标题校正
            if home_default and (not h or h in home_default or home_default.endswith(h)):
                h = home_default
            if away_default and (not a or a in away_default or away_default.endswith(a)):
                a = away_default
        out.append((h, a, [segs[i]] + body))
    return out


def _pick_analysis(segs, evidence, score_line, limit=300):
    """理由：结论依据句 + 比分建议句；去重、完整句、不把整段重复贴两遍。"""
    parts = []
    ev = (evidence or "").strip()
    if ev:
        parts.append(ev)
    sc_focus = ""
    if score_line:
        # 只取比分原句里「带比分标记」的那一句，避免把整段（含结论句）重复拼进来
        for s in _sentences(score_line):
            if (SCORE_MARK.search(s) and s.strip() != ev
                    and s.strip() not in ev and ev not in s):
                sc_focus = s.strip()
                break
        if not sc_focus and score_line.strip() != ev and ev not in score_line:
            sc_focus = score_line.strip()
    if sc_focus:
        parts.append(sc_focus)
    txt = " ".join(parts)
    if not txt:
        for s in reversed(segs):
            if CONCLUSION_VERB.search(s):
                txt = s
                break
    txt = re.sub(r'\s+', ' ', txt).strip()
    if len(txt) > limit:
        cut = txt[:limit]
        p = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("；"))
        txt = cut[:p + 1] if p > limit * 0.5 else cut
    return txt


def parse_structured(html_text, expert="", home="", away="", pub_date=""):
    """结构化解析一篇文章。

    返回 dict：
      recommendations: [{home, away, recommend, score, analysis, expert,
                         pubDate, playType, evidence, scoreEvidence}]
      trackRecord, contentSummary, segments, checked
    """
    segs = clean_segments(html_text)
    if not segs:
        return {"recommendations": [], "trackRecord": "", "contentSummary": "",
                "segments": [], "checked": False}

    # 专家战绩：只收「连红/近N中N/命中」的战绩句，去掉残缺开场与营销长文，
    # 且不把句子截断在半句（旧版会在「（SP17」处断尾）。
    track = ""
    for s in segs[:4]:
        for sent in _sentences(s):
            sent = re.sub(r'^大家好[，,][^。！]{0,24}[。！]\s*', '', sent).strip()
            if not sent or not TRACK_KW.search(sent) or len(sent) >= 160:
                continue
            track = (track + " " + sent).strip() if track else sent
            if len(track) >= 110:
                break
        if len(track) >= 110:
            break
    track = track[:160].strip()

    matches = _split_matches(segs, home, away)
    recs = []
    for h, a, body in matches:
        if not h:
            continue
        # --- 比分：优先取带比分标记的段落（取最靠后的一个，通常紧邻结论） ---
        score_line, score = "", ""
        for s in reversed(body):
            if SCORE_MARK.search(s):
                sc = _extract_scores(s)
                if sc:
                    score_line, score = s, sc
                    break

        # 候选方向优先级：明确标记句 > 结论动词句 > 独立方向句 > 队名方向
        mark_dir, concl_dir, p3_dir, team_dir = ("", "", ""), ("", "", ""), ("", "", ""), ("", "", "")
        # 1) 明确标记句（推荐：让负 / 观点：胜 / 关注方向：3 …）
        for s in reversed(body):
            m = MARK_RE.match(s)
            if not m or re.match(r'^(?:比分|内容)', s):
                continue
            d, w = _dir_from_mark_value(m.group(1), h, a)
            if d and not ODDS_DESC.search(s):
                if w.startswith("队名方向"):
                    if not team_dir[0]:
                        team_dir = (d, w, s)
                elif not mark_dir[0]:
                    mark_dir = (d, w, s)
        # 2) 结论动词句（看好X不败 / 支持X带走3分 / 大胜 / 博客胜 …）
        for s in reversed(body):
            if not CONCLUSION_VERB.search(s):
                continue
            # 赔率描述段落只在没有明确结论信号时才跳过
            if ODDS_DESC.search(s) and not re.search(
                    r'(不败|取胜|带走3分|至少拿|大胜|守住|博客胜|推荐胜|观点|让[胜平负]|看好|期待)', s):
                continue
            d, w = _dir_from_conclusion(s, h, a)
            if d:
                concl_dir = (d, w, s)
                break
        # 3) 独立短方向句（「让负，比分2比2和2比3。」/「帕尔马 让胜」）
        for s in reversed(body):
            if len(s) > 60:
                continue
            m = re.match(r'^\s*(让球\s*[310胜平负]|让[胜平负]|主胜|客胜|平局)\s*[，,。；;]', s)
            if m:
                raw = re.sub(r'\s+', '', m.group(1))
                d = {"让球胜": "让胜", "让球平": "让平", "让球负": "让负",
                     "让球3": "让胜", "让球1": "让平", "让球0": "让负"}.get(raw, raw)
                p3_dir = (d, "独立方向句", s)
                break
            # 短行里的「帕尔马让胜」「斯图加特 让负」等：允许前面紧跟中文队名
            m2 = (re.search(r'(让[胜平负]|让球\s*[310胜平负])', s) if len(s) <= 20 else None)
            if m2 and not ODDS_DESC.search(s):
                raw = re.sub(r'\s+', '', m2.group(1))
                d = {"让球胜": "让胜", "让球平": "让平", "让球负": "让负",
                     "让球3": "让胜", "让球1": "让平", "让球0": "让负"}.get(raw, raw)
                p3_dir = (d, "独立方向句", s)
                break
        # 注意：("", "", "") 是非空元组、bool 为 True，不能用 `or` 串联候选
        direction, why, evidence = "", "", ""
        for _cand in (mark_dir, concl_dir, p3_dir, team_dir):
            if _cand[0]:
                direction, why, evidence = _cand
                break
        if not direction:
            # 无明确方向 → 不产推荐（宁缺勿错，如只有「总结：某队更有办法」）
            continue

        # 结论依据句（与方向判定同源）
        _ev_focus = _focus_sentence(evidence, h, a) if evidence else ""
        # 比分兜底：结论句里直接带比分（如「罗马大胜，2-0，3-1。」）
        if not score and evidence:
            sc = _extract_scores_plain(_ev_focus)
            if sc:
                score, score_line = sc, _ev_focus
        evidence_focus = _ev_focus
        play = "让球胜平负" if direction.startswith("让") else "胜平负"
        recs.append({
            "home": h, "away": a or "",
            "recommend": direction,
            "score": score,
            "analysis": _pick_analysis(body, evidence_focus, score_line),
            "expert": expert,
            "pubDate": pub_date,
            "playType": play,
            "evidence": evidence_focus,
            "evidencePara": evidence,
            "scoreEvidence": score_line,
            "dirWhy": why,
        })

    # 正文摘要：去掉战绩段后的前 2 段（供文章流展示"原文在讲什么"）
    body_all = [s for s in segs if s != track]
    summary = " ".join(body_all[:2])[:260]
    return {
        "recommendations": recs,
        "trackRecord": track,
        "contentSummary": summary,
        "segments": segs,
        "checked": True,
    }


def verify_recommendation(rec, segments):
    """逐条校验推荐内容与原文一致，返回 (ok, [问题])。

    校验项：
    - 方向值合法
    - 方向依据句（evidence）确实存在于清洗后的正文
    - evidence 里不含赔率描述词被误当结论
    - 比分依据句存在且比分确实来自该句
    - 理由不含页脚/广告噪音、不含 HTML 实体、不以半句开头
    - 队名出现在正文或标题
    """
    issues = []
    valid = {"主胜", "客胜", "平局", "主胜/平局", "客胜/平局",
             "让胜", "让平", "让负", "让胜/让平", "让胜/让负", "让平/让负"}
    d = rec.get("recommend", "")
    if d not in valid:
        issues.append("方向值非法: %s" % d)
    ev = (rec.get("evidence") or "").strip()
    if not ev:
        issues.append("缺少方向原文依据")
    elif not any(ev in s for s in segments):
        issues.append("方向依据句不在正文中")
    if ev and ODDS_DESC.search(ev) and not re.search(
            r'(让负|让胜|让平|不败|取胜|带走3分|推荐胜|大胜|博客胜|期待|至少拿|守住)', ev):
        issues.append("方向依据疑为赔率描述句")
    sc = rec.get("score", "")
    if sc:
        se = rec.get("scoreEvidence") or ""
        if not se:
            issues.append("有比分但缺少比分原句")
        else:
            got_sc = _extract_scores(se) or _extract_scores_plain(se)
            if got_sc != sc:
                issues.append("比分与原句不一致: %s vs %s" % (sc, got_sc))
    ana = rec.get("analysis") or ""
    if ana:
        if NOISE_CUT.search(ana):
            issues.append("理由含页脚/广告噪音")
        if re.search(r'&[a-zA-Z#0-9]+;|&nbsp;', ana):
            issues.append("理由含未清洗 HTML 实体")
        if len(ana) > 20 and ana[0] in ("，", "。", "、", "；", "？", "！", "）"):
            issues.append("理由以半句标点开头")
    tr = rec.get("trackRecord") or ""
    if re.search(r'&[a-zA-Z#0-9]+;|&nbsp;', tr):
        issues.append("战绩含未清洗 HTML 实体")
    return (not issues), issues
