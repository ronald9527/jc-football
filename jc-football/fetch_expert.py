#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高手推荐抓取与生成（独立展示源，不参与模型）
数据源：92玩球(92wq.com) - 易红单平台真实专家推荐（欧核、彭阿多等）、懂球帝荐单
- 抓取最新专家文章，提取竞彩推荐方向、比分、分析
- 历史战绩回溯
- 仅保留当天竞彩官方赛程比赛

【独立源声明 · 继承接管规范】
本模块只产出独立的 expert_recommendations.json / expert_stats.json / expert_history.json，
由前端「高手推荐」tab 独立展示。它只「只读」matches.json 与 history.json 用于赛果回溯，
绝不回写核心 matches.json、绝不写入 history.json（模型训练数据）、绝不改动 smart_model.json。
即：高手荐单与模型/核心数据零耦合，仅作参考展示。
"""
import json, time, os, re, urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))

def load_json(name):
    p = os.path.join(DIR, name)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json(name, data):
    with open(os.path.join(DIR, name), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_92wq_articles():
    """从92玩球获取最新专家文章列表：
    1) 从第1页逐页往后翻，严格只保留当天日期发布的帖子
    2) 遇到非当天日期的帖子连续2页就停止翻页
    3) 记录已处理文章ID到expert_seen.json，下次只处理新帖子
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.92wq.com/",
    }
    today = time.strftime("%Y-%m-%d")

    # 读取已处理文章ID记录和首尾定位
    seen_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expert_seen.json")
    seen_ids = set()
    last_max_id = ""
    last_min_id = ""
    if os.path.exists(seen_path):
        try:
            seen_data = json.load(open(seen_path, encoding="utf-8"))
            seen_ids = set(seen_data.get("ids", []))
            last_max_id = seen_data.get("lastMaxId", "")
            last_min_id = seen_data.get("lastMinId", "")
        except Exception:
            pass

    all_articles = {}
    new_ids = []
    non_today_pages = 0  # 连续非当天日期的页数
    max_pages = 10  # 最多翻10页防止死循环
    consecutive_seen = 0  # 连续遇到已seen的当天文章数
    reached_last_min = False  # 是否遇到上次最旧文章

    for page_num in range(1, max_pages + 1):
        if page_num == 1:
            page_url = "https://www.92wq.com/list-1.htm"
        else:
            page_url = f"https://www.92wq.com/list-1-{page_num}.htm"
        try:
            req = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"  92玩球 {page_url} 抓取失败: {e}")
            break

        page_has_today = False
        page_new_count = 0
        # 按li块解析：每个li包含文章链接和日期
        for li_m in re.finditer(r'<li class="media thread[^"]*"[^>]*data-href="(view-\d+\.htm)"[^>]*>(.*?)</li>', html, re.DOTALL):
            vid = li_m.group(1)
            block = li_m.group(2)
            # 从li块提取标题
            tm = re.search(r'<a href="' + re.escape(vid) + r'"[^>]*>([^<]{6,120})</a>', block)
            if not tm:
                continue
            title = tm.group(1).strip()
            if '复盘' in title or vid in all_articles:
                continue
            expert = ""
            em = re.search(r'【([\u4e00-\u9fa5A-Za-z0-9·]{2,6})】分析', title)
            if em:
                expert = em.group(1)
            elif '今日解读' in title:
                expert = "92玩球编辑部"
            league = ""
            lm = re.search(r'【([\u4e00-\u9fa5A-Za-z]+)】', title)
            if lm and lm.group(1) not in ['今日解读', '冰岛超']:
                league = lm.group(1)
            home = away = ""
            mm = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})VS([\u4e00-\u9fa5A-Za-z0-9·]{2,})', title)
            if mm:
                home = mm.group(1).strip()
                away = mm.group(2).strip()
                for suffix in ['解读', '分析', '预测', '推荐']:
                    if away.endswith(suffix):
                        away = away[:-len(suffix)]
                    if home.endswith(suffix):
                        home = home[:-len(suffix)]
            # 从li块提取日期（格式：2026-09-17 23:02:48）
            pub_date = ""
            dm = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})\s+\d{1,2}:\d{2}', block)
            if dm:
                pub_date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
            else:
                dm = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', block)
                if dm:
                    pub_date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"

            # 严格过滤：只保留当天日期发布的帖子
            if pub_date != today:
                continue
            page_has_today = True

            all_articles[vid] = {
                "id": vid, "title": title, "expert": expert,
                "league": league, "home": home, "away": away,
                "url": f"https://www.92wq.com/{vid}",
                "pubDate": pub_date,
            }
            if vid not in seen_ids:
                new_ids.append(vid)
                page_new_count += 1
                consecutive_seen = 0  # 遇到新文章，重置连续计数
            else:
                consecutive_seen += 1
            # 检查是否遇到上次最旧文章
            if last_min_id and vid == last_min_id:
                reached_last_min = True

        # 如果已经遇到上次最旧文章，且连续5篇都是已seen的，说明后面都是旧的，可以停止
        if reached_last_min and consecutive_seen >= 5:
            print(f"  92玩球：已到上次最旧位置({last_min_id})且连续{consecutive_seen}篇已解析，停止翻页")
            break

        if not page_has_today:
            non_today_pages += 1
            if non_today_pages >= 2:
                print(f"  92玩球：连续{non_today_pages}页无当天({today})帖子，停止翻页")
                break
        else:
            non_today_pages = 0

    # 计算首尾ID（按数字大小排序）
    all_ids = sorted(all_articles.keys(), key=lambda x: int(re.search(r'(\d+)', x).group(1)))
    today_max_id = all_ids[-1] if all_ids else ""
    today_min_id = all_ids[0] if all_ids else ""

    # 保存已处理文章ID记录（保留最近500条）+ 首尾定位
    all_seen = list(seen_ids | set(all_articles.keys()))
    all_seen = all_seen[-500:]
    try:
        json.dump({
            "ids": all_seen,
            "lastUpdate": today,
            "lastMaxId": today_max_id,
            "lastMinId": today_min_id,
        }, open(seen_path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

    # 返回所有当天文章（包括已处理过的），确保推荐内容更新不遗漏
    # 已处理文章也重新解析，因为专家可能更新推荐内容
    result = [all_articles[vid] for vid in all_ids]
    print(f"  92玩球：当天({today})帖子{len(all_articles)}条（新帖子{len(new_ids)}条，首尾:{today_min_id}~{today_max_id}）")
    return result

def parse_article_92wq(url, expert_default="", home_default="", away_default=""):
    """解析92玩球文章，提取推荐"""
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://www.92wq.com/",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return [], ""

    # 提取专家名（支持多种格式）
    expert = expert_default
    # 格式1: 【专家名】分析
    em = re.search(r'【([\u4e00-\u9fa5A-Za-z0-9·]{2,8})】分析', html)
    if em:
        expert = em.group(1)
    else:
        # 格式2: title中的专家名_说球/实证/剖析/分析/解读/推荐
        em2 = re.search(r'<title>[^<]*?_?([\u4e00-\u9fa5A-Za-z0-9·]{2,8})\s*(?:说球|实证|剖析|分析|解读|推荐)', html)
        if em2:
            expert = em2.group(1)
        else:
            # 格式3: 从h1标题提取
            em3 = re.search(r'<h1[^>]*>([^<]{2,40})</h1>', html)
            if em3:
                title_text = em3.group(1).strip()
                # 格式3a: 队名VS队名_专家名
                em4 = re.search(r'[_—-]\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,8})\s*$', title_text)
                if em4:
                    expert = em4.group(1)
                else:
                    # 格式3b: 专家名 周X 编号 联赛 队名VS队名
                    em5 = re.match(r'^([\u4e00-\u9fa5A-Za-z0-9·]{2,8})\s+(?:周六|周日|周一|周二|周三|周四|周五)', title_text)
                    if em5:
                        expert = em5.group(1)
                    # 格式3c: 今日解读 → 92玩球编辑部
                    elif '今日解读' in title_text or '赛前分析' in title_text:
                        expert = "92玩球编辑部"

    # 提取文章发布日期（用于日期核实，确保是当天比赛推荐）
    pub_date = ""
    dm = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', html)
    if dm:
        pub_date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    if not pub_date:
        dm2 = re.search(r'发布时间[：:]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', html)
        if dm2:
            pub_date = f"{dm2.group(1)}-{int(dm2.group(2)):02d}-{int(dm2.group(3)):02d}"

    # 提取所有p标签（带属性也匹配）
    ps = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
    texts = [re.sub(r'<[^>]+>', '', p).strip() for p in ps]
    texts = [t for t in texts if t and len(t) > 2]

    recommendations = []
    track_record = ""

    # 格式1: 欧核风格 - [数字]队名VS队名 + 竞彩：单X + 比分：X比X
    current = None
    for t in texts:
        # 新比赛开始
        m = re.match(r'^\[(\d+)\]([\u4e00-\u9fa5A-Za-z0-9·]{2,})VS([\u4e00-\u9fa5A-Za-z0-9·]{2,})', t)
        if m:
            if current:
                recommendations.append(current)
            current = {
                "home": m.group(2).strip(),
                "away": m.group(3).strip(),
                "recommend": "",
                "score": "",
                "analysis": "",
                "expert": expert,
                "pubDate": pub_date,
            }
        elif current:
            if t.startswith("竞彩"):
                current["recommend"] = t
            elif t.startswith("比分"):
                current["score"] = t
            elif t.startswith("分析"):
                current["analysis"] = t[:120]
        elif '连红' in t and '中' in t and len(t) < 200:
            # 专家战绩介绍
            track_record = t[:100]

    if current:
        recommendations.append(current)

    # 格式2: 彭阿多风格 - 段落式，分析：主让X分，比分：X比X、X比X
    if not recommendations:
        home = home_default
        away = away_default
        rec = score = analysis = ""
        for t in texts:
            if not home:
                mm = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})上轮', t)
                if mm:
                    home = mm.group(1).strip()
            if '分析：' in t:
                analysis = t
                rec = t.replace("分析：", "").strip()
            if t.startswith("比分"):
                score = t
            if '连红' in t and '中' in t and len(t) < 200:
                track_record = t[:100]
        # 从段落中找客队
        if home and not away:
            for t in texts:
                if '；' in t and home in t:
                    parts = t.split('；')
                    for p in parts:
                        if '上轮' in p and home not in p:
                            am = re.match(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})上轮', p.strip())
                            if am:
                                away = am.group(1).strip()
                                break
                if not away and 'VS' in t and home in t:
                    am = re.search(r'VS([\u4e00-\u9fa5A-Za-z0-9·]{2,})', t)
                    if am:
                        away = am.group(1).strip()
                        break
        if home and rec:
            recommendations.append({
                "home": home,
                "away": away or "",
                "recommend": rec,
                "score": score,
                "analysis": analysis[:120],
                "expert": expert,
                "pubDate": pub_date,
            })

    # 格式3: 长篇分析 - 智能提取推荐方向（优先从文章末尾明确推荐段提取）
    if not recommendations:
        # 去掉尾部导航噪音
        clean_texts = []
        for t in texts:
            t = re.sub(r'就爱玩球.*$', '', t)
            t = re.sub(r'本站不提供.*$', '', t)
            t = re.sub(r'&nbsp;', ' ', t)
            t = re.sub(r'\s+', ' ', t).strip()
            if t and len(t) > 2:
                clean_texts.append(t)

        # === 智能提取队名 ===
        # 从标题或正文中提取队名（支持VS/vs/Vs/vS/对阵等多种格式）
        if not home_default or not away_default:
            # 从文章标题提取
            tm = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})\s*(?:VS|vs|Vs|vS|对阵|VS.)\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,})', html)
            if tm:
                if not home_default:
                    home_default = tm.group(1).strip()
                if not away_default:
                    away_default = tm.group(2).strip()
            # 从第一段提取（支持更多格式）
            if not home_default or not away_default:
                for t in clean_texts[:5]:
                    tm2 = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})\s*(?:VS|vs|Vs|vS|对阵|主场|客场|VS.)\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,})', t)
                    if tm2:
                        if not home_default:
                            home_default = tm2.group(1).strip()
                        if not away_default:
                            away_default = tm2.group(2).strip()
                        break
            # 清理队名后缀（去掉"解读""分析""预测""推荐"等）
            for suffix in ['解读', '分析', '预测', '推荐', '赛前', '赛事']:
                if home_default and home_default.endswith(suffix):
                    home_default = home_default[:-len(suffix)]
                if away_default and away_default.endswith(suffix):
                    away_default = away_default[:-len(suffix)]

        # === 多场比赛检测与分割 ===
        # 检测多种场次标题格式
        multi_match_indices = []
        for i, t in enumerate(clean_texts):
            # 格式1: 第一场/第二场/第N场
            if re.match(r'^第[一二三四五六七八九十\d]+场[：:]?$', t) or re.match(r'^第[一二三四五六七八九十\d]+场\s', t):
                multi_match_indices.append(i)
            # 格式2: 编号+联赛+队名vs队名（如"004 欧联 奥莫尼亚vs塞尔塔"）
            elif re.match(r'^\d{3}\s+[\u4e00-\u9fa5]+', t) and re.search(r'(?:vs|VS|Vs|vS|对阵)', t):
                if i not in multi_match_indices:
                    multi_match_indices.append(i)
            # 格式3: 队名vs队名 单独成行（如"米尔顿vs克劳利"）
            elif re.match(r'^[\u4e00-\u9fa5A-Za-z0-9·]{2,}\s*(?:vs|VS|Vs|vS|对阵)\s*[\u4e00-\u9fa5A-Za-z0-9·]{2,}$', t):
                if i not in multi_match_indices:
                    multi_match_indices.append(i)
            # 格式4: 周X编号 联赛 队名vs队名（如"周二002 英锦赛 米尔顿vs克劳利"）
            elif re.match(r'^周[一二三四五六日]\d{3}', t) and re.search(r'(?:vs|VS|Vs|vS|对阵)', t):
                if i not in multi_match_indices:
                    multi_match_indices.append(i)

        def parse_single_match(seg_texts, seg_home, seg_away):
            """解析单场比赛的推荐，返回(rec, score, analysis)或None"""
            rec = ""
            score = ""
            analysis = ""
            found = False

            def extract_scores_local(text):
                if not text:
                    return ""
                # 支持多种比分标记格式
                m_mark = re.search(r'(?:比分参考|参考比分|比分[：:]|内容[：:]|比分预测|比分：|参考：)\s*', text)
                if m_mark:
                    raw_seg = text[m_mark.end():m_mark.end()+100]
                    cut = re.split(r'(?:第二场|第\d+场|另一场|竞彩第|换一场|下一位|半全场|进球数)', raw_seg)
                    seg = cut[0][:60]
                    # 支持斜杠/逗号/顿号分隔的多个比分
                    scores = re.findall(r'(\d+)\s*[比:：\-/]\s*(\d+)', seg)
                else:
                    scores = re.findall(r'(\d+)\s*[比:：\-/]\s*(\d+)', text)
                if not scores:
                    return ""
                seen = set()
                unique = []
                for s in scores:
                    # 过滤异常比分（进球数>10的可能是时间或其他数字）
                    if int(s[0]) > 10 or int(s[1]) > 10:
                        continue
                    mm = re.search(rf'{s[0]}\s*([比:：\-/])\s*{s[1]}', text)
                    sep_used = mm.group(1) if mm else None
                    if sep_used in [":", "："] and len(str(s[1])) >= 2:
                        continue
                    key = f"{s[0]}-{s[1]}"
                    if key not in seen:
                        seen.add(key)
                        unique.append(s)
                    if len(unique) >= 3:
                        break
                if not unique:
                    return ""
                return "比分" + "、".join([f"{s[0]}比{s[1]}" for s in unique])

            # 策略0: 让球玩法（支持多种格式）
            if not found:
                for idx in range(len(seg_texts)-1, max(-1, len(seg_texts)-9), -1):
                    t = seg_texts[idx]
                    # 格式1: 竞彩/推荐/方向/观点：让胜/让平/让负
                    m = re.search(r'(?:竞彩|竞足|单选|推荐|方向|观点|我的观点|最终观点|个人观点)[^：:]*[：:]\s*(让胜|让平|让负)', t)
                    if m:
                        rec = m.group(1)
                        score = extract_scores_local(t)
                        for j in range(idx+1, min(idx+3, len(seg_texts))):
                            if not score: score = extract_scores_local(seg_texts[j])
                        analysis = t[:300]
                        found = True
                        break
                    # 格式2: 让胜/让平/让负 单独成行
                    m2 = re.match(r'^(让胜|让平|让负)[，,。.！!？?]?\s*$', t)
                    if m2:
                        rec = m2.group(1)
                        score = extract_scores_local(t)
                        for j in range(idx+1, min(idx+3, len(seg_texts))):
                            if not score: score = extract_scores_local(seg_texts[j])
                        analysis = t[:300]
                        found = True
                        break

            # 策略1: 明确推荐段（支持多种格式）
            for idx in range(len(seg_texts)-1, max(-1, len(seg_texts)-12), -1):
                if found: break
                t = seg_texts[idx]
                # 格式1: 主胜/客胜/平局 单独成行
                m = re.match(r'^(主胜|客胜|平局|主队胜|客队胜|打平)[，,。.！!？?]?', t)
                if m:
                    rec = m.group(1)
                    score = extract_scores_local(t)
                    for j in range(idx+1, min(idx+4, len(seg_texts))):
                        if not score: score = extract_scores_local(seg_texts[j])
                    analysis = t[:300]
                    found = True
                    break
                # 格式2: 竞彩单3/1/0
                m = re.search(r'竞彩[^：:]*[：:]?\s*单([310])(?![\d.])', t)
                if m:
                    rec = "主胜" if m.group(1) == "3" else ("客胜" if m.group(1) == "0" else "平局")
                    score = extract_scores_local(t)
                    for j in range(idx+1, min(idx+4, len(seg_texts))):
                        if not score: score = extract_scores_local(seg_texts[j])
                    analysis = t[:300]
                    found = True
                    break
                # 格式3: 竞彩：主胜/客胜/平局
                if not found:
                    m = re.search(r'竞彩[^：:]*[：:]\s*(主胜|客胜|平局)', t)
                    if m:
                        rec = m.group(1)
                        score = extract_scores_local(t)
                        for j in range(idx+1, min(idx+4, len(seg_texts))):
                            if not score: score = extract_scores_local(seg_texts[j])
                        analysis = t[:300]
                        found = True
                        break
                # 格式4: 推荐/方向/结论/最终/观点/我的观点：主胜/客胜/平局/负/胜/平
                m = re.search(r'(?:推荐|方向|结论|最终|观点|我的观点|个人观点|最终观点)[^：:，。；]*[：:]?\s*(?:竞彩)?\s*(主胜|客胜|平局|主队不败|客队不败|负|胜|平)', t)
                if m:
                    raw = m.group(1)
                    if raw == "负": rec = "客胜"
                    elif raw == "胜": rec = "主胜"
                    elif raw == "平": rec = "平局"
                    elif "不败" in raw:
                        rec = "主胜/平局" if "主" in raw else "客胜/平局"
                    else: rec = raw
                    score = extract_scores_local(t)
                    for j in range(idx+1, min(idx+4, len(seg_texts))):
                        if not score: score = extract_scores_local(seg_texts[j])
                    analysis = t[:300]
                    found = True
                    break
                # 格式5: 单选：主胜/客胜/平局
                if not found:
                    m = re.search(r'(?:单选|竞彩单选|足彩单选)[^：:]*[：:]\s*(主胜|客胜|平局|让胜|让平|让负)', t)
                    if m:
                        rec = m.group(1)
                        score = extract_scores_local(t)
                        for j in range(idx+1, min(idx+4, len(seg_texts))):
                            if not score: score = extract_scores_local(seg_texts[j])
                        analysis = t[:300]
                        found = True
                        break

            # 策略2: 比分段找方向
            if not found:
                for t in reversed(seg_texts[-10:]):
                    if '比分' not in t and '内容' not in t:
                        continue
                    sc = extract_scores_local(t)
                    if sc:
                        score = sc
                        for seg in [t] + ([seg_texts[seg_texts.index(t)-1]] if seg_texts.index(t) > 0 else []):
                            dm = re.search(r'(主胜|客胜|平局|主队胜|客队胜|打平|主队赢|客队赢|主队不败|客队不败|主队取胜|客队取胜|观点[：:]\s*(?:负|胜|平))', seg)
                            if dm:
                                raw = dm.group(1)
                                if raw.startswith("观点"):
                                    inner = re.search(r'观点[：:]\s*(负|胜|平)', raw)
                                    raw = inner.group(1) if inner else raw
                                if raw == "负": rec = "客胜"
                                elif raw == "胜": rec = "主胜"
                                elif raw == "平": rec = "平局"
                                elif "不败" in raw:
                                    rec = "主胜/平局" if "主" in raw else "客胜/平局"
                                elif "赢" in raw or "取胜" in raw:
                                    rec = "主胜" if "主" in raw else "客胜"
                                else: rec = raw
                                analysis = seg[:300]
                                found = True
                                break
                        if found: break

            # 策略3: 全文智能提取
            if not found:
                full_text = " ".join(seg_texts)
                # 观点：负/胜/平 格式（简洁推荐）
                m = re.search(r'观点[^：:]*[：:]\s*(负|胜|平)(?![\d])', full_text)
                if m:
                    raw = m.group(1)
                    rec = "客胜" if raw == "负" else ("主胜" if raw == "胜" else "平局")
                    start = max(0, m.start()-80)
                    analysis = full_text[start:m.end()+200].strip()[:300]
                    score_ctx = full_text[m.start():m.end()+250]
                    score = extract_scores_local(score_ctx)
                    found = True

                if not found:
                    patterns = [
                        (r'(?:看好|推荐|认为|相信|预测|倾向|选择|建议|搏|博)[^。！？]{0,30}?(主胜|客胜|平局|主队胜|客队胜|打平)', "direct"),
                        (r'(?:看好|推荐|认为|相信|预测|倾向|选择|建议|支持|搏|博)[^。！？]{0,20}?(主队|客队)[^。！？]{0,15}?(赢|胜|不败|取胜|赢球|拿分|抢分)', "team_result"),
                        (r'(主队|客队)[^。！？]{0,10}?不败', "team_unbeaten"),
                        (r'(主队|客队)[^。！？]{0,10}?(赢球|取胜|赢|胜|拿分|抢分)', "team_win"),
                    ]
                    # 队名匹配
                    if seg_home:
                        hk = re.escape(seg_home[:2]) if len(seg_home) >= 2 else re.escape(seg_home)
                        patterns.append((rf'{hk}[^。！？]{{0,8}}?不败', "home_unbeaten"))
                        patterns.append((rf'{hk}[^。！？]{{0,8}}?(赢球|取胜|赢|胜|拿分|抢分)', "home_win"))
                        patterns.append((rf'(?:看好|推荐|支持|搏|博)[^。！？]{{0,10}}?{hk}', "home_support"))
                    if seg_away:
                        ak = re.escape(seg_away[:2]) if len(seg_away) >= 2 else re.escape(seg_away)
                        patterns.append((rf'{ak}[^。！？]{{0,8}}?不败', "away_unbeaten"))
                        patterns.append((rf'{ak}[^。！？]{{0,8}}?(赢球|取胜|赢|胜|拿分|抢分)', "away_win"))
                        patterns.append((rf'(?:看好|推荐|支持|搏|博)[^。！？]{{0,10}}?{ak}', "away_support"))

                    # 收集所有匹配，取位置最靠后的（作者真正推荐通常在末尾）
                    all_matches = []
                    for pat, ptype in patterns:
                        try:
                            for m in re.finditer(pat, full_text):
                                # 否定词排除：匹配位置前后30字符内有否定/困难词则跳过
                                ctx_around = full_text[max(0, m.start()-30):min(len(full_text), m.end()+30)]
                                if re.search(r'(很难|不易|困难|难以|无法|没(?:有|办法)|不(?:太|大|太|甚)|未必|不见得|不必|没有必要)', ctx_around):
                                    continue
                                all_matches.append((m.start(), m, ptype))
                        except Exception:
                            continue

                    if all_matches:
                        all_matches.sort(key=lambda x: x[0], reverse=True)
                        _, m, ptype = all_matches[0]
                        if ptype == "direct":
                            raw = m.group(1)
                            rec = "客胜" if raw in ["客胜","客队胜"] else ("平局" if raw in ["平局","打平"] else "主胜")
                        elif ptype == "team_result":
                            team = m.group(1); result = m.group(2)
                            rec = "主胜/平局" if (team=="主队" and result=="不败") else ("客胜/平局" if (team=="客队" and result=="不败") else ("主胜" if team=="主队" else "客胜"))
                        elif ptype == "team_unbeaten":
                            rec = "主胜/平局" if m.group(1)=="主队" else "客胜/平局"
                        elif ptype == "team_win":
                            rec = "主胜" if m.group(1)=="主队" else "客胜"
                        elif ptype in ["home_unbeaten","home_support"]: rec = "主胜/平局" if "unbeaten" in ptype else "主胜"
                        elif ptype == "home_win": rec = "主胜"
                        elif ptype in ["away_unbeaten","away_support"]: rec = "客胜/平局" if "unbeaten" in ptype else "客胜"
                        elif ptype == "away_win": rec = "客胜"
                        start = max(0, m.start()-80)
                        analysis = full_text[start:m.end()+200].strip()[:300]
                        score_ctx = full_text[m.start():m.end()+250]
                        score = extract_scores_local(score_ctx)
                        found = True

            if found and rec:
                return rec, score, analysis
            return None

        # 执行多场或单场解析
        found = False  # 提前初始化，防止多场分支解析失败后下方读取未赋值变量
        if len(multi_match_indices) >= 2:
            # 多场比赛：按标记分割
            for mi_idx, start_i in enumerate(multi_match_indices):
                end_i = multi_match_indices[mi_idx+1] if mi_idx+1 < len(multi_match_indices) else len(clean_texts)
                seg_texts = clean_texts[start_i:end_i]
                # 从场次标题提取队名
                seg_home = seg_away = ""
                for t in seg_texts[:3]:
                    mm = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})\s*(?:vs|VS|Vs|对阵)\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,})', t)
                    if mm:
                        seg_home = mm.group(1).strip()
                        seg_away = mm.group(2).strip()
                        break
                if not seg_home:
                    seg_home = home_default
                    seg_away = away_default
                result = parse_single_match(seg_texts, seg_home, seg_away)
                if result and seg_home:
                    rec, score, analysis = result
                    direction = rec
                    if '/' not in rec:
                        if '让胜' in rec: direction = "让胜"
                        elif '让平' in rec: direction = "让平"
                        elif '让负' in rec: direction = "让负"
                        elif '主胜' in rec or '主队胜' in rec: direction = "主胜"
                        elif '客胜' in rec or '客队胜' in rec: direction = "客胜"
                        elif '平局' in rec or '打平' in rec: direction = "平局"
                    recommendations.append({
                        "home": seg_home,
                        "away": seg_away or "",
                        "recommend": direction,
                        "score": score,
                        "analysis": analysis[:200],
                        "expert": expert,
                        "pubDate": pub_date,
                    })
            # 多场解析成功后直接返回，跳过后面的单场逻辑
            if recommendations:
                return recommendations, track_record
        else:
            # 单场比赛：原有逻辑（使用内联函数保持兼容）
            rec = ""
            score = ""
            analysis = ""
            found = False

        def extract_scores(text):
            """从文本中提取所有比分预测，返回'比分X比Y、X比Z'格式（支持和/、/，/-分隔）
            排除时间格式（如 3：00 开赛时间），仅保留真实比分"""
            if not text:
                return ""
            # 优先从明确比分标记后提取（比分参考/参考比分/比分：/内容：/比分预测），避免混入历史赛果
            m_mark = re.search(r'(?:比分参考|参考比分|比分[：:]|内容[：:]|比分预测)\s*', text)
            if m_mark:
                raw_seg = text[m_mark.end():m_mark.end()+80]
                # 遇到下一场比赛标记则截断，避免混入他场比分/历史赛果
                cut = re.split(r'(?:第二场|第\d+场|另一场|竞彩第|换一场|下一位)', raw_seg)
                seg = cut[0][:50]
                scores = re.findall(r'(\d+)\s*[比:：\-]\s*(\d+)', seg)
            else:
                scores = re.findall(r'(\d+)\s*[比:：\-]\s*(\d+)', text)
            if not scores:
                return ""
            seen = set()
            unique = []
            for s in scores:
                # 排除日期格式：前数≥4位（如2026-09、2026比09）视为年份/日期
                if len(str(s[0])) >= 4:
                    continue
                # 检查分隔符类型
                mm = re.search(rf'{s[0]}\s*([比:：\-])\s*{s[1]}', text)
                sep_used = mm.group(1) if mm else None
                # 排除时间格式：冒号分隔且后数≥2位（如3:00、23:30开赛时间）
                # 足球比分后数一般为0-9单数字（如2:0、2:1），两位数后数视为时间
                if sep_used in [":", "："] and len(str(s[1])) >= 2:
                    continue
                key = f"{s[0]}-{s[1]}"
                if key not in seen:
                    seen.add(key)
                    unique.append(s)
                if len(unique) >= 3:
                    break
            if not unique:
                return ""
            return "比分" + "、".join([f"{s[0]}比{s[1]}" for s in unique])

        # 策略0: 让球玩法推荐（竞彩让胜/让平/让负，最高优先级，含"竞足单选：让胜"等格式）
        if not found:
            # 只检查末尾3段是否有胜平负推荐（不败/主胜/客胜/平局），避免历史战绩中的"不败"干扰
            tail_text = " ".join(clean_texts[-3:]) if len(clean_texts) >= 3 else " ".join(clean_texts)
            # 先移除"让球主胜/让球客胜/让球平"等让球玩法词汇，避免误判为胜平负推荐
            tail_for_check = re.sub(r'让球[^：:]{0,5}?(主胜|客胜|平|让胜|让平|让负|[310])', '', tail_text)
            has_had_rec = bool(re.search(r'(不败|主胜|客胜|平局|主队胜|客队胜|打平|单[310])', tail_for_check))
            for idx in range(len(clean_texts)-1, max(-1, len(clean_texts)-9), -1):
                t = clean_texts[idx]
                # 格式1: 竞彩/竞足/单选/推荐/方向/观点/我的观点：让胜/让平/让负（明确官方语法，最高优先级）
                m = re.search(r'(?:竞彩|竞足|单选|推荐|方向|观点|我的观点|最终观点|个人观点)[^：:]*[：:]\s*(让胜|让平|让负)', t)
                if m:
                    rec = m.group(1)
                else:
                    # 格式5: 方向/单选/推荐/竞彩：让球3/让球1/让球0（明确让球玩法标记，不受has_had_rec限制）
                    m = re.search(r'(?:方向|单选|推荐|竞彩|竞足)[^：:]*[：:]\s*让球\s*([310])(?![\d.])', t)
                    if m:
                        raw = m.group(1)
                        rec = "让胜" if raw == "3" else ("让负" if raw == "0" else "让平")
                    else:
                        # 格式6: 看好/推荐+队名+让球胜/平/负（如"看好水晶宫让球胜"）
                        m = re.search(r'(?:看好|推荐|单选|关注|支持)\s*[\u4e00-\u9fa5A-Za-z·]{2,12}?\s*让球\s*(胜|平|负)(?!\w)', t)
                        if m:
                            raw = m.group(1)
                            rec = "让胜" if raw == "胜" else ("让平" if raw == "平" else "让负")
                        else:
                            # 格式4: 重心关注让X/关注让X + 双选可加防让Y（史密斯式让球盘推荐）
                            m = re.search(r'(?:重心|重点|主推)?\s*(?:关注|推荐|看好|选择)\s*(让胜|让平|让负)', t)
                            if m:
                                rec = m.group(1)
                                m2 = re.search(r'(?:双选可)?(?:加)?防\s*(让胜|让平|让负)', t)
                                if m2 and m2.group(1) != rec:
                                    rec = f"{rec}/{m2.group(1)}"
                            elif not has_had_rec:
                                # 只有在全文没有胜平负推荐时，才用需要转换的让球格式
                                # 格式2: 让球主胜/让球客胜/让球平
                                m = re.search(r'让球[^：:]{0,5}?(主胜|客胜|平)', t)
                                if m:
                                    raw = m.group(1)
                                    rec = "让胜" if raw == "主胜" else ("让负" if raw == "客胜" else "让平")
                                else:
                                    # 格式3: 让球3/让球1/让球0（3=让胜, 1=让平, 0=让负）
                                    m = re.search(r'让球[^：:]{0,5}?([310])(?![\d.])', t)
                                    if m:
                                        raw = m.group(1)
                                        rec = "让胜" if raw == "3" else ("让负" if raw == "0" else "让平")
                if m:
                    score = extract_scores(t)
                    for j in range(idx+1, min(idx+3, len(clean_texts))):
                        if not score: score = extract_scores(clean_texts[j])
                    analysis = t[:300]
                    if idx+1 < len(clean_texts) and '比分' in clean_texts[idx+1]:
                        analysis += " " + clean_texts[idx+1][:100]
                    found = True
                    break

        # 策略1: 从文章末尾向前找明确推荐段（以主胜/客胜/平局开头，或包含竞彩：单X）
        for idx in range(len(clean_texts)-1, max(-1, len(clean_texts)-9), -1):
            if found:
                break
            t = clean_texts[idx]
            # 明确推荐：段落以主胜/客胜/平局开头
            m = re.match(r'^(主胜|客胜|平局|主队胜|客队胜|打平)[，,。.！!]?', t)
            if m:
                rec = m.group(1)
                score = extract_scores(t)
                for j in range(idx+1, min(idx+3, len(clean_texts))):
                    if not score: score = extract_scores(clean_texts[j])
                analysis = t[:300]
                if idx+1 < len(clean_texts) and '比分' in clean_texts[idx+1]:
                    analysis += " " + clean_texts[idx+1][:100]
                found = True
                break
            # 竞彩：单X 格式（必须有"单"字，排除赔率数字误匹配；数字后不能紧跟数字或小数点）
            m = re.search(r'竞彩[^：:]*[：:]?\s*单([310])(?![\d.])', t)
            if m:
                raw = m.group(1)
                rec = "主胜" if raw == "3" else "客胜"
                score = extract_scores(t)
                for j in range(idx+1, min(idx+3, len(clean_texts))):
                    if not score: score = extract_scores(clean_texts[j])
                analysis = t[:300]
                if idx+1 < len(clean_texts) and '比分' in clean_texts[idx+1]:
                    analysis += " " + clean_texts[idx+1][:100]
                found = True
                break
            # 关注方向：3/1/0 格式（竞彩310简写，3=主胜1=平0=客胜）
            m = re.search(r'(?:关注方向|方向|关注|推荐方向)[^：:]*[：:]\s*单?([310])(?![\d.])', t)
            if m:
                raw = m.group(1)
                rec = "主胜" if raw == "3" else ("平局" if raw == "1" else "客胜")
                score = extract_scores(t)
                for j in range(idx+1, min(idx+3, len(clean_texts))):
                    if not score: score = extract_scores(clean_texts[j])
                analysis = t[:300]
                if idx+1 < len(clean_texts) and '比分' in clean_texts[idx+1]:
                    analysis += " " + clean_texts[idx+1][:100]
                found = True
                break
            # 竞彩：主胜/客胜/平局 文字格式（排除"竞彩开出"等赔率描述）
            if not found:
                m = re.search(r'竞彩[^：:]*[：:]\s*(主胜|客胜|平局)', t)
                if m:
                    rec = m.group(1)
                    score = extract_scores(t)
                    for j in range(idx+1, min(idx+3, len(clean_texts))):
                        if not score: score = extract_scores(clean_texts[j])
                    analysis = t[:300]
                    if idx+1 < len(clean_texts) and '比分' in clean_texts[idx+1]:
                        analysis += " " + clean_texts[idx+1][:100]
                    found = True
                    break
            # 推荐：X / 方向：X / 观点：X 格式（支持"推荐：竞彩客胜"、"个人观点：胜"）
            m = re.search(r'(?:推荐|方向|结论|最终|观点)[^：:，。；]*[：:]?\s*(?:竞彩)?\s*(主胜|客胜|平局|主队不败|客队不败|负|胜|平)', t)
            if m:
                raw = m.group(1)
                if raw == "负": rec = "客胜"
                elif raw == "胜": rec = "主胜"
                elif raw == "平": rec = "平局"
                else: rec = raw
                score = extract_scores(t)
                for j in range(idx+1, min(idx+3, len(clean_texts))):
                    if not score: score = extract_scores(clean_texts[j])
                analysis = t[:300]
                if idx+1 < len(clean_texts) and '比分' in clean_texts[idx+1]:
                    analysis += " " + clean_texts[idx+1][:100]
                found = True
                break

        # 策略2: 找包含比分预测的段落（必须含"比分"关键词，排除历史赛果），从末尾向前
        if not found:
            for t in reversed(clean_texts[-10:]):
                if '比分' not in t:  # 只从明确的比分预测段提取，排除历史赛果
                    continue
                sc = extract_scores(t)
                if sc:
                    score = sc
                    # 从该段或前一段找推荐方向（支持多种表达方式）
                    for seg in [t] + ([clean_texts[clean_texts.index(t)-1]] if clean_texts.index(t) > 0 else []):
                        # 明确方向词（排除"平局拉高"、"胜赔"等描述性用法）
                        dm = re.search(r'(?<!拉高)(?<!压低)(?<!升)(?<!降)(主胜|客胜|平局|主队胜|客队胜|打平|主队赢|客队赢|主队不败|客队不败|主队取胜|客队取胜)', seg)
                        # 额外排除：平局拉高/压低等赔率描述
                        if dm and re.search(r'平(局|赔|盘)(拉高|压低|升|降)', seg):
                            dm = None
                        if dm:
                            raw = dm.group(1)
                            if "不败" in raw:
                                rec = "主胜/平局" if "主" in raw else "客胜/平局"
                            elif "赢" in raw or "取胜" in raw:
                                rec = "主胜" if "主" in raw else "客胜"
                            elif raw in ["客胜", "客队胜"]:
                                rec = "客胜"
                            elif raw in ["平局", "打平"]:
                                rec = "平局"
                            else:
                                rec = "主胜"
                            analysis = seg[:300]
                            found = True
                            break
                        # "看好主队/客队" 格式
                        dm2 = re.search(r'(?:看好|推荐|支持|搏|博)[^。！？]{0,10}?(主队|客队)', seg)
                        if dm2:
                            team = dm2.group(1)
                            rec = "主胜" if team == "主队" else "客胜"
                            analysis = seg[:300]
                            found = True
                            break
                    if found:
                        break

        # 策略3: 全文智能提取推荐方向（支持多种自然语言表达方式）
        if not found:
            full_text = " ".join(clean_texts)

            def judge_team(ctx, home_default, away_default):
                """从上下文判断是主队还是客队方向"""
                # 优先匹配队名
                if home_default:
                    hk = home_default[:2] if len(home_default) >= 2 else home_default
                    if hk in ctx:
                        return "home"
                if away_default:
                    ak = away_default[:2] if len(away_default) >= 2 else away_default
                    if ak in ctx:
                        return "away"
                # 匹配"主队/客队"关键词
                if "主队" in ctx and "客队" not in ctx:
                    return "home"
                if "客队" in ctx and "主队" not in ctx:
                    return "away"
                # 匹配"主场/客场"
                if "主场" in ctx and "客场" not in ctx:
                    return "home"
                if "客场" in ctx and "主场" not in ctx:
                    return "away"
                return None

            # 智能匹配模式（按优先级排序）
            smart_patterns = [
                # 模式1: 明确方向词（主胜/客胜/平局）
                (r'(?:看好|推荐|认为|相信|预测|倾向|选择|建议|搏|博)[^。！？]{0,30}?(主胜|客胜|平局|主队胜|客队胜|打平)', "direct"),
                # 模式2: 主队/客队 + 结果词（赢/胜/不败/取胜/赢球）
                (r'(?:看好|推荐|认为|相信|预测|倾向|选择|建议|支持|搏|博)[^。！？]{0,20}?(主队|客队)[^。！？]{0,15}?(赢|胜|不败|取胜|赢球|拿分|抢分)', "team_result"),
                # 模式3: 主队/客队不败（单独）
                (r'(主队|客队)[^。！？]{0,10}?不败', "team_unbeaten"),
                # 模式4: 主队/客队赢/胜（单独，无前置动词）
                (r'(主队|客队)[^。！？]{0,10}?(赢球|取胜|赢|胜|拿分|抢分)', "team_win"),
                # 模式5: 坐和望赢
                (r'(主队|客队)[^。！？]{0,10}?坐和望赢', "team_draw_win"),
                # 模式6: 方向/结论/最终 + 冒号 + 方向
                (r'(?:最终|结论|方向|本场)[^：:]*[：:]\s*(主胜|客胜|平局|主队不败|客队不败)', "colon_direct"),
            ]

            # 队名匹配模式（动态构建，支持"队名+不败/赢球/方向"等）
            team_patterns = []
            if home_default:
                hk = re.escape(home_default[:2]) if len(home_default) >= 2 else re.escape(home_default)
                # 队名+不败
                team_patterns.append((rf'{hk}[^。！？]{{0,8}}?不败', "home_unbeaten"))
                # 队名+赢/胜/取胜
                team_patterns.append((rf'{hk}[^。！？]{{0,8}}?(赢球|取胜|赢|胜|拿分|抢分)', "home_win"))
                # 看好+队名
                team_patterns.append((rf'(?:看好|推荐|支持|搏|博)[^。！？]{{0,10}}?{hk}', "home_support"))
                # 队名+方向（如"博塔弗戈方向"）
                team_patterns.append((rf'{hk}[^。！？]{{0,5}}?方向', "home_support"))
            if away_default:
                ak = re.escape(away_default[:2]) if len(away_default) >= 2 else re.escape(away_default)
                team_patterns.append((rf'{ak}[^。！？]{{0,8}}?不败', "away_unbeaten"))
                team_patterns.append((rf'{ak}[^。！？]{{0,8}}?(赢球|取胜|赢|胜|拿分|抢分)', "away_win"))
                team_patterns.append((rf'(?:看好|推荐|支持|搏|博)[^。！？]{{0,10}}?{ak}', "away_support"))
                team_patterns.append((rf'{ak}[^。！？]{{0,5}}?方向', "away_support"))

            all_patterns = smart_patterns + [(p, t) for p, t in team_patterns]

            # 匹配类型优先级：有明确推荐动词的 > 不败 > 纯队名+赢/胜
            type_priority = {
                "direct": 0, "team_result": 0, "colon_direct": 0,
                "home_support": 0, "away_support": 0,
                "team_unbeaten": 1, "home_unbeaten": 1, "away_unbeaten": 1, "team_draw_win": 1,
                "team_win": 2, "home_win": 2, "away_win": 2,
            }
            # 收集所有匹配，否定词排除，历史战绩排除
            all_matches = []
            for pat, ptype in all_patterns:
                try:
                    for m in re.finditer(pat, full_text):
                        # 否定词排除：匹配位置前后30字符内有否定/困难词则跳过
                        ctx_around = full_text[max(0, m.start()-30):min(len(full_text), m.end()+30)]
                        if re.search(r'(很难|不易|困难|难以|无法|没(?:有|办法)|不(?:太|大|太|甚)|未必|不见得|不必|没有必要)', ctx_around):
                            continue
                        # 纯队名+赢/胜类型：排除历史战绩描述（上轮/本赛季/主场X胜/客场X胜/近X场等）
                        if ptype in ["team_win", "home_win", "away_win"]:
                            ctx_wider = full_text[max(0, m.start()-40):min(len(full_text), m.end()+20)]
                            if re.search(r'(上轮|本赛季|赛季|联赛|近\d+场|主场\d+胜|客场\d+胜|战绩|排名|积分)', ctx_wider):
                                continue
                        # 队名+不败类型：排除历史战绩描述（历史/交锋/此前/之前/上赛季/保持不败等）
                        if ptype in ["team_unbeaten", "home_unbeaten", "away_unbeaten"]:
                            ctx_wider = full_text[max(0, m.start()-40):min(len(full_text), m.end()+20)]
                            if re.search(r'(历史|交锋|此前|之前|上赛季|赛季|战绩|保持|对阵|交手)', ctx_wider):
                                continue
                        pri = type_priority.get(ptype, 3)
                        all_matches.append((pri, m.start(), m, ptype))
                except Exception:
                    continue

            if all_matches:
                # 先按类型优先级，再按位置靠后排序
                all_matches.sort(key=lambda x: (x[0], -x[1]))
                _, _, m, ptype = all_matches[0]
                ctx = full_text[max(0, m.start()-120):m.end()+80]

                if ptype == "direct":
                    raw = m.group(1)
                    if raw in ["客胜", "客队胜"]:
                        rec = "客胜"
                    elif raw in ["平局", "打平"]:
                        rec = "平局"
                    else:
                        rec = "主胜"
                elif ptype == "team_result":
                    team = m.group(1)
                    result = m.group(2)
                    if result == "不败":
                        rec = "主胜/平局" if team == "主队" else "客胜/平局"
                    else:
                        rec = "主胜" if team == "主队" else "客胜"
                elif ptype == "team_unbeaten":
                    team = m.group(1)
                    rec = "主胜/平局" if team == "主队" else "客胜/平局"
                elif ptype == "team_win":
                    team = m.group(1)
                    rec = "主胜" if team == "主队" else "客胜"
                elif ptype == "team_draw_win":
                    team = m.group(1)
                    rec = "主胜/平局" if team == "主队" else "客胜/平局"
                elif ptype == "colon_direct":
                    raw = m.group(1)
                    if raw == "客胜":
                        rec = "客胜"
                    elif raw == "平局":
                        rec = "平局"
                    elif "不败" in raw:
                        rec = "主胜/平局" if "主" in raw else "客胜/平局"
                    else:
                        rec = "主胜"
                elif ptype == "home_unbeaten":
                    rec = "主胜/平局"
                elif ptype == "home_win":
                    rec = "主胜"
                elif ptype == "home_support":
                    # 检查匹配位置后10字符内是否有"不败"，有则双选
                    after_ctx = full_text[m.end():m.end()+10]
                    rec = "主胜/平局" if "不败" in after_ctx else "主胜"
                elif ptype == "away_unbeaten":
                    rec = "客胜/平局"
                elif ptype == "away_win":
                    rec = "客胜"
                elif ptype == "away_support":
                    after_ctx = full_text[m.end():m.end()+10]
                    rec = "客胜/平局" if "不败" in after_ctx else "客胜"

                # 保存原文上下文
                start = max(0, m.start() - 80)
                end = min(len(full_text), m.end() + 200)
                analysis = full_text[start:end].strip()[:300]
                # 比分从匹配位置后200字符内提取
                score_ctx = full_text[m.start():m.end()+250]
                score = extract_scores(score_ctx) if '比分' in score_ctx or re.search(r'\d+\s*[比:：\-]\s*\d+', score_ctx) else ""
                found = True

            # 策略3b: 如果还没找到，尝试从文章末尾3段中找"主队/客队"+方向
            if not found and len(clean_texts) >= 3:
                tail = " ".join(clean_texts[-3:])
                # 找"主队/客队" + 赢/胜/不败
                m = re.search(r'(主队|客队)[^。！？]{0,15}?(赢球|取胜|赢|胜|不败|拿分|抢分|坐和望赢)', tail)
                if m:
                    team = m.group(1)
                    result = m.group(2)
                    if "不败" in result or "坐和望赢" in result:
                        rec = "主胜/平局" if team == "主队" else "客胜/平局"
                    else:
                        rec = "主胜" if team == "主队" else "客胜"
                    ctx_start = max(0, tail.find(m.group(0)) - 80)
                    analysis = tail[ctx_start:ctx_start+300]
                    score = extract_scores(tail[tail.find(m.group(0)):])
                    found = True

        if found and rec and home_default:
            # 隐藏内容文章（"请您登录后查看"）且无明确推荐动词时不产出推荐
            full_check = " ".join(clean_texts)
            if "隐藏内容" in full_check and not re.search(r'(看好|推荐|单选|倾向|支持|主胜|客胜|平局|单[310]|让胜|让平|让负)', full_check):
                print(f"  [隐藏内容过滤] {expert or '无署名'} 文章预测为登录可见隐藏内容且无明确推荐，跳过")
                return [], track_record
            # 标准化方向（双选含"/"保持不变）
            direction = rec
            if '/' in rec:
                direction = rec  # 双选（如"主胜/平局"）保持不变
            elif '主胜' in rec or '主队胜' in rec or '主队赢' in rec:
                direction = "主胜"
            elif '客胜' in rec or '客队胜' in rec or '客队赢' in rec:
                direction = "客胜"
            elif '平局' in rec or '打平' in rec:
                direction = "平局"

            recommendations.append({
                "home": home_default,
                "away": away_default or "",
                "recommend": direction,
                "score": score,
                "analysis": analysis[:200],
                "expert": expert,
                "pubDate": pub_date,
            })

    return recommendations, track_record

def normalize_recommend(rec_text):
    """将推荐文本转为标准方向"""
    if not rec_text:
        return "", ""
    # 竞彩：单0 -> 客胜
    if '单0' in rec_text or '单3' in rec_text or '单1' in rec_text:
        if '单0' in rec_text:
            return "客胜", "a"
        elif '单3' in rec_text:
            return "主胜", "h"
        elif '单1' in rec_text:
            return "平局", "d"
    # 让球玩法双选（重心让X防让Y）：必须排在单让球判断之前
    if '让平/让负' in rec_text or '让负/让平' in rec_text:
        return "让平/让负", "hd,ha"
    if '让胜/让平' in rec_text or '让平/让胜' in rec_text:
        return "让胜/让平", "hh,hd"
    if '让胜/让负' in rec_text or '让负/让胜' in rec_text:
        return "让胜/让负", "hh,ha"
    # 让球玩法
    if '让胜' in rec_text:
        return "让胜", "hh"
    if '让平' in rec_text:
        return "让平", "hd"
    if '让负' in rec_text:
        return "让负", "ha"
    # 主让X分 -> 主胜
    if '主让' in rec_text:
        return "主胜", "h"
    # 客让X分 -> 客胜
    if '客让' in rec_text:
        return "客胜", "a"
    # 双选
    if '主胜/平局' in rec_text or '平局/主胜' in rec_text:
        return "主胜/平局", "h,d"
    if '客胜/平局' in rec_text or '平局/客胜' in rec_text:
        return "客胜/平局", "a,d"
    # 单选方向
    if '主胜' in rec_text or '主队胜' in rec_text:
        return "主胜", "h"
    if '客胜' in rec_text or '客队胜' in rec_text:
        return "客胜", "a"
    if '平局' in rec_text or '打平' in rec_text:
        return "平局", "d"
    return rec_text[:20], ""

def fetch_dongqiudi_plans():
    """从懂球帝获取高手推荐方案（作为补充数据源）"""
    url = "https://www.dongqiudi.com/forecast"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"  懂球帝抓取失败: {e}")
        return []

    m = re.search(r'window\.__NUXT__\s*=\s*(.+?);\s*</script>', html, re.DOTALL)
    if not m:
        return []
    raw = m.group(1)

    plans = []
    plans_start = raw.find('plans:[{matchInfo:')
    if plans_start < 0:
        return []
    plan_text = raw[plans_start + 6:]
    plan_positions = [m.start() for m in re.finditer(r'\{matchInfo:\[', plan_text)]

    for i, pos in enumerate(plan_positions):
        end_pos = plan_positions[i+1] if i+1 < len(plan_positions) else len(plan_text)
        block = plan_text[pos:end_pos].rstrip(',')

        labels_m = re.search(r'high_labels_string:"([^"]*)"', block)
        labels = labels_m.group(1) if labels_m else ""
        summary_m = re.search(r'summary:"([^"]*)"', block)
        summary = summary_m.group(1) if summary_m else ""

        # 提取对阵
        home = away = ""
        vm = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})\s*(?:VS|vs)\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,})', summary)
        if vm:
            home, away = vm.group(1).strip(), vm.group(2).strip()
        else:
            vm2 = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})\s*(?:迎来|对阵|大战)\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,})', summary)
            if vm2:
                home, away = vm2.group(1).strip(), vm2.group(2).strip()
            else:
                vm3 = re.search(r'([\u4e00-\u9fa5A-Za-z0-9·]{2,})\s*客战\s*([\u4e00-\u9fa5A-Za-z0-9·]{2,})', summary)
                if vm3:
                    home, away = vm3.group(2).strip(), vm3.group(1).strip()

        # 专家名
        expert_name = ""
        en_m = re.match(r'^([\u4e00-\u9fa5A-Za-z0-9]{2,6}(?:精研|说球|竞彩|品球|看球|侃球|解球))', summary)
        if en_m:
            expert_name = en_m.group(1)

        red_streak = 0
        rm = re.search(r'(\d+)连红', labels)
        if rm:
            red_streak = int(rm.group(1))
        recent = ""
        nm = re.search(r'近(\d+)中(\d+)', labels)
        if nm:
            recent = f"近{nm.group(1)}中{nm.group(2)}"

        if home and away:
            plans.append({
                "expert": expert_name or "懂球帝高手",
                "home": home,
                "away": away,
                "recommend": "高手关注（方向详见懂球帝）",
                "score": "",
                "analysis": summary[:100],
                "redStreak": red_streak,
                "recent": recent,
                "labels": labels,
            })

    return plans

def normalize_team_name(name):
    """队名标准化：去除编号/联赛前缀/常见后缀，返回核心词用于模糊匹配"""
    if not name:
        return ""
    s = name.strip()
    # 去除编号前缀：周五008、周六001、008、001等
    s = re.sub(r'^[周今明后]\w?\d{3}\s*', '', s)
    s = re.sub(r'^\d{3}\s*', '', s)
    # 去除联赛前缀+第X轮
    s = re.sub(r'^(英超|西甲|意甲|德甲|法甲|荷甲|挪超|瑞超|比甲|苏超|日职|韩K|美职|英冠|英甲|西乙|德乙|法乙|葡超|俄超|土超|希超|奥甲|瑞士超|波兰甲|瑞典超|挪威超|芬兰超|爱尔兰超|冰岛超|白俄超|沙特联|卡塔尔联|阿联酋超|埃及超|南非超|墨西超|美冠联|巴甲|阿甲|欧联|欧罗巴|欧冠|欧协|欧国联|世预赛|友谊赛|联赛杯|足总杯|国王杯|德国杯|意大利杯|法国杯|荷兰杯|比利时杯)[\s\S]*?第\d+轮[\s]*', '', s)
    s = re.sub(r'第\d+轮', '', s)
    # 常见后缀移除
    for suffix in ["俱乐部", "足球俱乐部", "体育", "竞技", "FC", "CF", "AFC", "SC", "队"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    # 去除空格和特殊字符
    s = re.sub(r'[\s\-_·•\.\,\(\)（）【】\[\]]', '', s)
    return s.strip()

# 队名简称映射（文章简称 -> 竞彩官方全称关键词）
TEAM_ALIAS = {
    "格风暴": "格拉茨风暴", "格拉茨": "格拉茨风暴",
    "拉普大学": "拉普拉塔大学生", "拉普拉塔": "拉普拉塔大学生", "拉普": "拉普拉塔大学生",
    "维拉": "阿斯顿维拉", "阿斯顿维拉": "阿斯顿维拉",
    "塞尔塔": "维戈塞尔塔", "维戈塞尔塔": "维戈塞尔塔",
    "马竞": "马德里竞技", "马德里竞技": "马德里竞技",
    "国米": "国际米兰", "国际米兰": "国际米兰",
    "米兰": "AC米兰", "AC米兰": "AC米兰",
    "枪手": "阿森纳", "阿森纳": "阿森纳",
    "红军": "利物浦", "利物浦": "利物浦",
    "蓝军": "切尔西", "切尔西": "切尔西",
    "红魔": "曼联", "曼联": "曼彻斯特联", "曼彻斯特联": "曼彻斯特联",
    "曼城": "曼彻斯特城", "曼彻斯特城": "曼彻斯特城",
    "热刺": "托特纳姆热刺", "托特纳姆热刺": "托特纳姆热刺",
    "拜仁": "拜仁慕尼黑", "拜仁慕尼黑": "拜仁慕尼黑",
    "多特": "多特蒙德", "多特蒙德": "多特蒙德",
    "巴萨": "巴塞罗那", "巴塞罗那": "巴塞罗那",
    "皇马": "皇家马德里", "皇家马德里": "皇家马德里",
    "巴黎": "巴黎圣日耳曼", "巴黎圣日耳曼": "巴黎圣日耳曼",
    "尤文": "尤文图斯", "尤文图斯": "尤文图斯",
    "西汉姆": "西汉姆联", "西汉姆联": "西汉姆联",
    "富勒姆": "富勒姆",
    "贝蒂斯": "皇家贝蒂斯", "皇家贝蒂斯": "皇家贝蒂斯",
    "赫塔费": "赫塔费", "赫塔菲": "赫塔费",
    "毕尔巴鄂": "毕尔巴鄂竞技", "毕尔巴鄂竞技": "毕尔巴鄂竞技",
    "莱万特": "莱万特",
    "基多": "基多体育大学", "基多体育大学": "基多体育大学",
    "帕尔梅拉斯": "帕尔梅拉斯",
    "博塔弗戈": "博塔弗戈",
    "格雷米奥": "格雷米奥",
    "科林蒂安": "科林蒂安",
    "大学生": "拉普拉塔大学生",
    "安德莱赫特": "安德莱赫特",
    "里昂": "里昂",
    "桑德兰": "桑德兰",
    "阿尔克马尔": "阿尔克马尔",
    "本菲卡": "本菲卡",
    "奥萨苏纳": "奥萨苏纳",
    "塞维利亚": "塞维利亚",
    "拉科": "拉科鲁尼亚", "拉科鲁尼亚": "拉科鲁尼亚",
    "奥莫尼亚": "奥莫尼亚",
    "考文垂": "考文垂",
    "雷恩": "雷恩",
    "布城": "布里斯托尔城", "布里斯托尔城": "布里斯托尔城",
    "萨普斯堡": "萨尔普斯堡", "萨尔普斯堡": "萨尔普斯堡",
    "奥斯KFUM": "奥斯陆KFUM", "奥斯陆KFUM": "奥斯陆KFUM",
    "西班牙人": "西班牙人", "埃尔切": "埃尔切",
    "格罗宁根": "格罗宁根", "兹沃勒": "兹沃勒",
    "蒙扎": "蒙扎", "萨索洛": "萨索洛",
    "登博思": "登博思", "海尔蒙特": "海尔蒙特",
    "摩纳哥": "摩纳哥", "朗斯": "朗斯",
    "布伦特福德": "布伦特福德",
    "纽约城": "纽约城", "纽约红牛": "纽约红牛",
    "沃尔夫斯堡": "沃尔夫斯堡", "达姆施塔特": "达姆施塔特",
    "沃夫斯堡": "沃尔夫斯堡", "达姆施塔": "达姆施塔特",
    "布伦特": "布伦特福德",
    "兰斯": "兰斯", "蒙彼利埃": "蒙彼利埃",
    "赫尔辛基火花": "赫尔辛基火花", "赫尔辛基": "赫尔辛基",
    "沙特阿拉伯亚足": "沙特阿拉伯亚足", "卡塔尔亚足": "卡塔尔亚足",
}

def team_match(name1, name2):
    """智能队名匹配：别名映射+标准化+模糊包含匹配"""
    if not name1 or not name2:
        return False
    # 1. 别名映射
    n1 = TEAM_ALIAS.get(name1, name1)
    n2 = TEAM_ALIAS.get(name2, name2)
    if n1 == n2:
        return True
    # 2. 原始包含匹配（处理简称）
    if len(name1) >= 2 and len(name2) >= 2:
        if name1 in name2 or name2 in name1:
            return True
    # 3. 标准化后匹配
    s1 = normalize_team_name(n1)
    s2 = normalize_team_name(n2)
    if not s1 or not s2:
        return False
    if s1 == s2:
        return True
    # 标准化后包含匹配（至少2个字符相同）
    if len(s1) >= 2 and len(s2) >= 2:
        if s1 in s2 or s2 in s1:
            return True
    # 4. 核心词匹配：取前2-3个字匹配
    if len(s1) >= 2 and len(s2) >= 2:
        core1 = s1[:2]
        core2 = s2[:2]
        if core1 == core2:
            return True
    return False

def build_recommendations(articles_data, matches, expert_stats=None):
    """构建高手推荐列表（仅92玩球易红单真实专家，带历史战绩核查）"""
    today = time.strftime("%Y-%m-%d")
    today_matches = [m for m in matches if m.get("date") == today]
    recs = []
    filtered_low = 0

    # 92玩球数据
    for article in articles_data:
        for r in article.get("recommendations", []):
            home = r.get("home", "")
            away = r.get("away", "")
            if not home:
                continue

            matched = None
            for m in today_matches:
                mh = m.get("home", "")
                ma = m.get("away", "")
                if team_match(home, mh) and team_match(away, ma):
                    matched = m
                    break
                if team_match(home, ma) and team_match(away, mh):
                    matched = m
                    break

            if not matched:
                continue

            # === 玩法转换：只开让球盘的比赛，胜平负推荐转换为让球玩法 ===
            had_odds = matched.get("had") or {}
            hhad_odds = matched.get("hhad") or {}
            had_opened = any(v is not None for v in had_odds.values()) if had_odds else False
            hhad_opened = any(v is not None for v in hhad_odds.values()) if hhad_odds else False
            rec_text = r.get("recommend", "")
            if hhad_opened and not had_opened and rec_text:
                # 只开让球盘，转换胜平负推荐为让球玩法
                convert_map = {
                    "主胜": "让胜", "客胜": "让负", "平局": "让平",
                    "主胜/平局": "让胜/让平", "客胜/平局": "让负/让平",
                }
                for old, new in convert_map.items():
                    if rec_text == old:
                        rec_text = new
                        r["recommend"] = new
                        r["playType"] = "让球胜平负"
                        break
                else:
                    r["playType"] = "让球胜平负"
            else:
                r["playType"] = "胜平负" if had_opened else "让球胜平负"

            # === 交叉验证1: 日期核实 ===
            # 文章发布日期必须是当天或前一天（排除过期文章）
            article_pub = r.get("pubDate", "") or article.get("pubDate", "")
            if article_pub:
                try:
                    from datetime import datetime, timedelta
                    pub_dt = datetime.strptime(article_pub, "%Y-%m-%d")
                    today_dt = datetime.strptime(today, "%Y-%m-%d")
                    yesterday_dt = today_dt - timedelta(days=1)
                    if pub_dt < yesterday_dt:
                        print(f"  [日期过滤] {article.get('expert','?')} 文章日期{article_pub}早于昨天，跳过")
                        continue
                except Exception:
                    pass

            # === 交叉验证2: 联赛名称核实 ===
            article_league = article.get("league", "")
            matched_league = matched.get("league", "")
            if article_league and matched_league:
                # 联赛关键词匹配（去除"精英联赛""联赛杯"等后缀后比较核心词）
                def league_core(l):
                    for suffix in ["精英联赛", "联赛杯", "联赛", "杯", "超", "甲", "乙", "冠"]:
                        l = l.replace(suffix, "")
                    return l.strip()
                al_core = league_core(article_league)
                ml_core = league_core(matched_league)
                if al_core and ml_core and al_core != ml_core and al_core not in ml_core and ml_core not in al_core:
                    # 联赛不匹配，跳过（避免队名相同但不同联赛的错误匹配）
                    print(f"  [联赛过滤] {article.get('expert','?')} 文章联赛[{article_league}]≠竞彩联赛[{matched_league}]，跳过")
                    continue

            # === 交叉验证3: 推荐方向合理性校验 ===
            rec_text = r.get("recommend", "")
            if rec_text:
                # 确保推荐方向是合法值（含竞彩让球玩法让胜/让平/让负）
                valid_dirs = ["主胜", "客胜", "平局", "主胜/平局", "客胜/平局", "主胜/客胜", "让胜", "让平", "让负"]
                is_valid = any(d in rec_text for d in valid_dirs) or any(k in rec_text for k in ["单0", "单3", "单1", "让球"])
                if not is_valid and len(rec_text) < 20:
                    # 推荐方向不明确，跳过
                    print(f"  [方向过滤] {article.get('expert','?')} 推荐方向不明确[{rec_text}]，跳过")
                    continue

            expert_name = r.get("expert", article.get("expert", "92玩球专家"))
            # 过滤无署名专家（情报局/无作者栏目文章，非真实高手推荐）
            if not expert_name or expert_name == "92玩球专家":
                print(f"  [专家过滤] {article.get('title','?')[:30]} 无署名专家，跳过")
                continue
            # 专家历史战绩核查
            estats = None
            if expert_stats and expert_name in expert_stats:
                estats = expert_stats[expert_name]
                # 连负>=3场 → 标记谨慎参考（不直接过滤，让用户看到真实战绩）
                if estats.get("streak", 0) <= -3:
                    print(f"  [警告] {expert_name} 已连负{abs(estats.get('streak',0))}场，谨慎参考")

            rec_dir, rec_key = normalize_recommend(r.get("recommend", ""))
            rec_item = {
                "num": matched.get("num", ""),
                "league": matched.get("league", ""),
                "home": matched.get("home", home),
                "away": matched.get("away", away),
                "kickoff": matched.get("kickoff", ""),
                "date": today,
                "recommend": rec_dir or r.get("recommend", ""),
                "recommendKey": rec_key,
                "odds": "-",
                "confidence": 78,
                "expertName": expert_name,
                "source": "92玩球",
                "sourceUrl": article.get("url", ""),
                "trackRecord": article.get("trackRecord", ""),
                "reason": r.get("analysis", "") or r.get("score", ""),
                "scorePred": r.get("score", ""),
                "hit": None,
                "actualScore": None,
                "isExpert": True,
            }
            # 附加专家历史战绩
            if estats:
                rec_item["expertStats"] = {
                    "total": estats["total"],
                    "judged": estats["judged"],
                    "hits": estats["hits"],
                    "miss": estats["miss"],
                    "hitRate": estats["hitRate"],
                    "recent10Rate": estats["recent10Rate"],
                    "streak": estats["streak"],
                    "isNew": estats["judged"] < 3,
                }
            else:
                rec_item["expertStats"] = {"isNew": True, "judged": 0, "hitRate": 0, "note": "新专家，数据积累中"}
            recs.append(rec_item)

    if filtered_low > 0:
        print(f"  专家战绩核查：过滤{filtered_low}条低命中率推荐")

    # 去重（按比赛+专家）
    seen = set()
    unique = []
    for r in recs:
        key = f"{r['home']}vs{r['away']}|{r['expertName']}"
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # 按比赛分组，同场多名专家按胜率排序合并
    match_groups = {}
    for r in unique:
        match_key = f"{r['home']}vs{r['away']}"
        if match_key not in match_groups:
            match_groups[match_key] = []
        match_groups[match_key].append(r)

    merged = []
    for match_key, experts in match_groups.items():
        # 按专家胜率排序：已判定场次多+命中率高的在前，新专家在后
        def sort_key(r):
            estats = r.get("expertStats", {})
            hit_rate = estats.get("hitRate", 0)
            judged = estats.get("judged", 0)
            is_new = estats.get("isNew", True)
            # 排序：非新专家优先，然后命中率降序，然后已判定场次降序
            return (0 if not is_new else 1, -hit_rate, -judged)
        experts.sort(key=sort_key)

        # 主推荐 = 胜率最高的专家
        main = experts[0]
        # 其他专家推荐
        others = []
        for r in experts[1:]:
            others.append({
                "expertName": r.get("expertName", ""),
                "recommend": r.get("recommend", ""),
                "scorePred": r.get("scorePred", ""),
                "reason": r.get("reason", ""),
                "sourceUrl": r.get("sourceUrl", ""),
                "hitRate": r.get("expertStats", {}).get("hitRate", 0),
                "judged": r.get("expertStats", {}).get("judged", 0),
                "isNew": r.get("expertStats", {}).get("isNew", True),
            })
        main["otherExperts"] = others
        main["expertCount"] = len(experts)
        merged.append(main)

    # 按比赛编号排序
    merged.sort(key=lambda r: r.get("num", ""))

    return merged[:15]

def backfill_history(history, matches):
    """回填历史战绩：从matches.json(当天)和history.json(历史)获取赛果"""
    today = time.strftime("%Y-%m-%d")
    score_map = {}
    # 当天比赛赛果
    for m in matches:
        if m.get("date") and m.get("homeScore") is not None:
            key = f"{m['date']}|{m.get('home','')}vs{m.get('away','')}"
            score_map[key] = (m["homeScore"], m["awayScore"], m.get("hadResult"), m.get("hhadResult"))
    # 历史比赛赛果（从history.json）
    try:
        hist_data = load_json("history.json")
        for rec in hist_data.get("records", []):
            d = rec.get("date", "")
            result = rec.get("result") or {}
            if isinstance(result, dict) and result.get("score") and not result.get("cancel"):
                try:
                    parts = result["score"].replace("：", ":").split(":")
                    hs = int(parts[0])
                    aws = int(parts[1])
                    had = "h" if hs > aws else ("d" if hs == aws else "a")
                    key = f"{d}|{rec.get('home','')}vs{rec.get('away','')}"
                    score_map[key] = (hs, aws, had, rec.get("hhadResult"))
                except Exception:
                    pass
    except Exception:
        pass

    def judge_hit(rec_text, had, hhad_result):
        """根据推荐文本判定命中（支持胜平负/让球/双选）"""
        if not rec_text:
            return None
        # 让球双选（重心让X防让Y）：命中其一即中，必须排在单让球判断之前
        if "让平/让负" in rec_text:
            return hhad_result in ("d", "a") if hhad_result else None
        if "让胜/让平" in rec_text:
            return hhad_result in ("h", "d") if hhad_result else None
        if "让胜/让负" in rec_text:
            return hhad_result in ("h", "a") if hhad_result else None
        # 让球玩法（用让球盘结果判定）
        if "让胜" in rec_text:
            return hhad_result == "h" if hhad_result else None
        if "让平" in rec_text:
            return hhad_result == "d" if hhad_result else None
        if "让负" in rec_text:
            return hhad_result == "a" if hhad_result else None
        # 双选
        if "主胜/平局" in rec_text or "平局/主胜" in rec_text:
            return had in ("h", "d")
        if "客胜/平局" in rec_text or "平局/客胜" in rec_text:
            return had in ("a", "d")
        # 单选
        if "主胜" in rec_text or "主队胜" in rec_text:
            return had == "h"
        if "客胜" in rec_text or "客队胜" in rec_text:
            return had == "a"
        if "平局" in rec_text or "打平" in rec_text:
            return had == "d"
        return None

    updated = 0
    for date, day_recs in history.items():
        if date >= today:
            continue
        for r in day_recs:
            key = f"{date}|{r.get('home','')}vs{r.get('away','')}"
            if key not in score_map:
                continue
            hs, aws, had, hhad_result = score_map[key]
            r["actualScore"] = f"{hs}:{aws}"
            # 回填主推荐命中
            if r.get("hit") is None:
                hit = judge_hit(r.get("recommend", ""), had, hhad_result)
                if hit is not None:
                    r["hit"] = hit
                    updated += 1
            # 回填同场其他专家推荐命中（otherExperts）
            for o in r.get("otherExperts", []):
                if o.get("hit") is not None:
                    continue
                o_hit = judge_hit(o.get("recommend", ""), had, hhad_result)
                if o_hit is not None:
                    o["hit"] = o_hit
                    o["actualScore"] = f"{hs}:{aws}"
                    updated += 1
    return updated


def calculate_expert_stats(history):
    """计算每个专家的历史战绩：总场次、命中、命中率、最近10场命中率、连红
    包含主推荐和同场其他专家推荐（otherExperts）"""
    expert_recs = {}
    for date, day_recs in history.items():
        for r in day_recs:
            # 主推荐
            expert = r.get("expertName", "")
            if expert and expert != "AI智能分析":
                if expert not in expert_recs:
                    expert_recs[expert] = []
                expert_recs[expert].append({
                    "date": date,
                    "home": r.get("home", ""),
                    "away": r.get("away", ""),
                    "recommend": r.get("recommend", ""),
                    "hit": r.get("hit"),
                    "actualScore": r.get("actualScore"),
                })
            # 同场其他专家推荐（otherExperts）
            for o in r.get("otherExperts", []):
                o_expert = o.get("expertName", "")
                if not o_expert or o_expert == "AI智能分析":
                    continue
                if o_expert not in expert_recs:
                    expert_recs[o_expert] = []
                expert_recs[o_expert].append({
                    "date": date,
                    "home": r.get("home", ""),
                    "away": r.get("away", ""),
                    "recommend": o.get("recommend", ""),
                    "hit": o.get("hit"),
                    "actualScore": o.get("actualScore"),
                })

    stats = {}
    for expert, recs in expert_recs.items():
        # 按日期排序（最新在前）
        recs_sorted = sorted(recs, key=lambda x: x["date"], reverse=True)
        total = len(recs_sorted)
        judged = [r for r in recs_sorted if r["hit"] is not None]
        hits = sum(1 for r in judged if r["hit"] is True)
        miss = sum(1 for r in judged if r["hit"] is False)
        pending = total - len(judged)
        hit_rate = round(hits / len(judged) * 100, 1) if judged else 0
        # 最近10场命中率
        recent10 = judged[:10]
        recent10_hits = sum(1 for r in recent10 if r["hit"] is True)
        recent10_rate = round(recent10_hits / len(recent10) * 100, 1) if recent10 else 0
        # 当前连红（从最新已判定的开始数连续命中）
        streak = 0
        for r in judged:
            if r["hit"] is True:
                streak += 1
            else:
                break
        # 推荐分计算：命中+10，错误-3，连胜加成，稳定奖励
        score = hits * 10 - miss * 3
        streak_bonus = min(streak * 2, 10) if streak > 0 else 0
        score += streak_bonus
        if len(judged) >= 5 and hit_rate >= 60:
            score += 20  # 稳定专家奖励
        stats[expert] = {
            "expert": expert,
            "total": total,
            "judged": len(judged),
            "hits": hits,
            "miss": miss,
            "pending": pending,
            "hitRate": hit_rate,
            "recent10Rate": recent10_rate,
            "streak": streak,
            "score": score,
            "streakBonus": streak_bonus,
            "isNew": len(judged) < 3,
            "recent": recs_sorted[:10],  # 最近10场明细
        }
    return stats

def main():
    print("[高手推荐] 开始生成...", flush=True)
    matches_data = load_json("matches.json")
    matches = matches_data.get("matches", []) if isinstance(matches_data, dict) else matches_data

    # 0. 读取旧的推荐数据（保留已解析的文章）
    old_data = load_json("expert_recommendations.json") or {}
    old_articles = old_data.get("articles", [])
    old_recs = old_data.get("recommendations", [])
    print(f"  旧数据：{len(old_articles)}篇文章，{len(old_recs)}条推荐")

    # 1. 从92玩球获取新文章列表（只返回未解析的新文章）
    new_articles = fetch_92wq_articles()
    print(f"  92玩球新文章：{len(new_articles)} 篇")

    # 2. 抓取新文章提取推荐
    new_articles_data = []
    for a in new_articles:
        recs, track = parse_article_92wq(a["url"], a.get("expert", ""), a.get("home", ""), a.get("away", ""))
        if recs:
            a["recommendations"] = recs
            a["trackRecord"] = track
            new_articles_data.append(a)
            print(f"  [{a['expert']}] {a['home']}vs{a['away']} -> {len(recs)}场推荐")
        time.sleep(0.3)

    # 合并新旧文章（新文章在前，旧文章在后）
    articles_data = new_articles_data + old_articles

    # 3. 专家历史战绩核查（从历史推荐记录计算真实命中率）
    history_for_stats = load_json("expert_history.json")
    expert_stats = calculate_expert_stats(history_for_stats)
    if expert_stats:
        print(f"  专家战绩核查：{len(expert_stats)}位专家有历史记录")
        for name, s in sorted(expert_stats.items(), key=lambda x: -x[1]["hitRate"]):
            print(f"    {name}: {s['judged']}场已判定 命中率{s['hitRate']}% 近10场{s['recent10Rate']}% 连红{s['streak']}")

    # 4. 构建推荐列表（只保留当天竞彩比赛，仅92玩球易红单真实高手，过滤低命中率）
    recs = build_recommendations(articles_data, matches, expert_stats)
    print(f"  生成高手推荐：{len(recs)} 条（仅当天竞彩比赛，已核查专家战绩）")

    # 5. 保存
    today = time.strftime("%Y-%m-%d")
    out = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sourceNote": "数据来源：92玩球(92wq.com)易红单平台真实专家推荐，已核查专家历史战绩，仅保留当天竞彩官方赛程比赛",
        "articles": articles_data,
        "recommendations": recs,
        "expertStats": expert_stats,
    }
    save_json("expert_recommendations.json", out)
    save_json("expert_stats.json", {"updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "stats": expert_stats})

    # 6. 历史回溯（直接覆盖今天的记录，确保最新推荐被保存）
    history = load_json("expert_history.json")
    history[today] = recs
    backfill = backfill_history(history, matches)
    save_json("expert_history.json", history)

    total = sum(len(v) for v in history.values())
    hits = sum(1 for v in history.values() for r in v if r.get("hit") is True)
    print(f"  历史回填：{backfill} 场，累计 {total} 条，命中 {hits} 条")
    print("[高手推荐] 完成", flush=True)

if __name__ == "__main__":
    main()
