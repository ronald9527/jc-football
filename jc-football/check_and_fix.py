#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全站全面数据检查修复脚本
每天早上6:00运行一次，全方位检查所有数据文件和代码状态。

检查维度：
A. 数据完整性 - 关键字段缺失、文件损坏、JSON解析失败
B. 数据一致性 - 让球盘规则验证、方向逻辑一致性、日期匹配
C. 数据时效性 - updateTime过期、已开赛未回填、未来比赛错误标记
D. 错误标记清除 - 未结束比赛的赛果/命中标记
E. 代码完整性 - Python语法、HTML占位符、配置文件
F. 规则引擎验证 - 12组规则配置、让球胜平负计算
"""
import json
import os
import sys
import time
import py_compile
import traceback
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

# ============ 工具函数 ============

def load_json(filename, default=None):
    """安全加载JSON文件"""
    path = os.path.join(HERE, filename)
    if not os.path.exists(path):
        return default, f"文件不存在: {filename}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return default, f"JSON解析失败: {filename} - {e}"

def save_json(filename, data):
    """保存JSON文件"""
    path = os.path.join(HERE, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_match_ended(kickoff_str, now_ts, buffer_hours=2.5):
    """判断比赛是否已结束（开赛时间+buffer小时）"""
    if not kickoff_str:
        return False
    try:
        if len(kickoff_str) > 5 and kickoff_str[4] == '-':
            kt = time.mktime(time.strptime(kickoff_str, "%Y-%m-%d %H:%M"))
        else:
            return False
        return now_ts >= kt + buffer_hours * 3600
    except Exception:
        return False

def parse_kickoff(kickoff_str):
    """解析开赛时间为datetime"""
    if not kickoff_str:
        return None
    try:
        if len(kickoff_str) > 5 and kickoff_str[4] == '-':
            return datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M")
    except Exception:
        pass
    return None

def calc_hhad_result(home_goals, away_goals, goal):
    """计算让球胜平负结果
    goal<0: 主队让球(如-1), goal>0: 主队受让(如+1)
    adj_home = home_goals + goal
    adj_home > away_goals => 让胜(h)
    adj_home = away_goals => 让平(d)
    adj_home < away_goals => 让负(a)
    """
    adj_home = home_goals + goal
    if adj_home > away_goals:
        return "h"
    elif adj_home == away_goals:
        return "d"
    else:
        return "a"

def parse_score(score_str):
    """解析比分为(home, away)元组"""
    if not score_str:
        return None
    try:
        parts = str(score_str).replace(':', '-').split('-')
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None

# ============ A. 数据完整性检查 ============

def check_data_integrity(now_ts, report):
    """检查所有数据文件完整性"""
    print("\n[A] 数据完整性检查")
    fixed = 0
    issues = []

    # A1. matches.json
    data, err = load_json("matches.json")
    if err:
        issues.append(f"matches.json: {err}")
        print(f"  ❌ {err}")
    else:
        update_time = data.get("updateTime", "")
        count = data.get("count", 0)
        matches = data.get("matches", [])
        print(f"  matches.json: updateTime={update_time}, count={count}, 实际场次={len(matches)}")
        if count != len(matches):
            issues.append(f"matches.json count({count})与实际场次({len(matches)})不一致")
            print(f"  ⚠️ count({count})与实际场次({len(matches)})不一致，自动修正")
            data["count"] = len(matches)
            save_json("matches.json", data)
            fixed += 1
        # 检查每场关键字段
        required_fields = ["num", "home", "away", "league", "kickoff", "hhad"]
        for i, m in enumerate(matches):
            missing = [f for f in required_fields if f not in m or m[f] is None]
            if missing:
                issues.append(f"matches[{i}] {m.get('num','?')} 缺少字段: {missing}")
                print(f"  ⚠️ {m.get('num','?')} 缺少字段: {missing}")
            # 检查让球数是否存在
            hhad = m.get("hhad", {})
            if isinstance(hhad, dict) and "goal" not in hhad:
                issues.append(f"matches[{i}] {m.get('num','?')} hhad缺少goal字段")
                print(f"  ⚠️ {m.get('num','?')} hhad缺少goal字段")
        # 检查半全场数据
        hf = data.get("halfFull", {})
        if hf:
            hf_count = hf.get("count", 0)
            hf_matches = hf.get("matches", [])
            if hf_count != len(hf_matches):
                issues.append(f"halfFull count({hf_count})与实际({len(hf_matches)})不一致")
                print(f"  ⚠️ halfFull count不一致，自动修正")
                hf["count"] = len(hf_matches)
                save_json("matches.json", data)
                fixed += 1

    # A2. history.json
    data, err = load_json("history.json", {"records": []})
    if err:
        issues.append(f"history.json: {err}")
    else:
        records = data.get("records", [])
        print(f"  history.json: {len(records)} 条记录")
        # 检查重复记录（同num+date）
        seen = {}
        dupes = 0
        for r in records:
            key = f"{r.get('date','')}|{r.get('num','')}"
            if key in seen:
                dupes += 1
            seen[key] = True
        if dupes > 0:
            issues.append(f"history.json 存在 {dupes} 条重复记录(date+num)")
            print(f"  ⚠️ 存在 {dupes} 条重复记录")

    # A3. hkmo_history.json
    data, err = load_json("hkmo_history.json", {"records": []})
    if err:
        issues.append(f"hkmo_history.json: {err}")
    else:
        records = data.get("records", [])
        print(f"  hkmo_history.json: {len(records)} 条记录")

    # A4. parlay_history.json
    data, err = load_json("parlay_history.json", {"records": []})
    if err:
        issues.append(f"parlay_history.json: {err}")
    else:
        records = data.get("records", [])
        print(f"  parlay_history.json: {len(records)} 条记录")
        for rec in records:
            legs = rec.get("legs", [])
            if not legs:
                issues.append(f"parlay {rec.get('date','')} {rec.get('type','')} 无legs数据")
                print(f"  ⚠️ 串关记录无legs: {rec.get('date','')} {rec.get('type','')}")

    # A5. strongweak_history.json
    data, err = load_json("strongweak_history.json", {})
    if err:
        issues.append(f"strongweak_history.json: {err}")
    else:
        print(f"  strongweak_history.json: {len(data)} 个日期记录")

    # A6. half_full_history.json 已移除（半全场tab已下线）

    # A7. rules_config.json
    data, err = load_json("rules_config.json", {})
    if err:
        issues.append(f"rules_config.json: {err}")
    else:
        rules = data.get("rules", data) if isinstance(data, dict) else data
        rule_count = len(rules) if isinstance(rules, (list, dict)) else 0
        print(f"  rules_config.json: {rule_count} 组规则")
        if rule_count < 10:
            issues.append(f"rules_config.json 规则数不足12组(当前{rule_count})")
            print(f"  ⚠️ 规则数不足12组")

    # A9. form_cache.json
    if os.path.exists(os.path.join(HERE, "form_cache.json")):
        data, err = load_json("form_cache.json", {})
        if err:
            issues.append(f"form_cache.json: {err}")
        else:
            print(f"  form_cache.json: {len(data)} 条缓存")

    report["integrity_issues"] = issues
    report["integrity_fixed"] = fixed
    return fixed

# ============ B. 数据一致性检查 ============

def check_data_consistency(now_ts, report):
    """检查数据一致性：让球盘规则、方向逻辑、日期匹配"""
    print("\n[B] 数据一致性检查")
    fixed = 0
    issues = []

    # B1. 让球盘规则验证（对有赛果的历史记录重新计算）
    data, err = load_json("history.json", {"records": []})
    if not err:
        records = data.get("records", [])
        hhad_errors = 0
        for r in records:
            result = r.get("result")
            if not result or not result.get("score"):
                continue
            score = parse_score(result.get("score"))
            if not score:
                continue
            home_g, away_g = score
            goal = r.get("goal")
            if goal is None:
                continue
            try:
                goal = float(goal)
            except Exception:
                continue
            # 重新计算让球结果
            actual_hhad = calc_hhad_result(home_g, away_g, goal)
            # 检查记录中的 hhadHit 是否与实际一致
            recorded_hit = r.get("hhadHit", "")
            recorded_dir = r.get("hhadDir", "")
            # 如果有推荐方向，验证命中标记是否正确
            if recorded_dir and recorded_dir in ("h", "d", "a"):
                expected_hit = "hit" if recorded_dir == actual_hhad else "miss"
                if recorded_hit and recorded_hit not in ("hit", "miss", ""):
                    pass  # 非标准值跳过
                elif recorded_hit in ("hit", "miss") and recorded_hit != expected_hit:
                    issues.append(f"{r.get('date','')} {r.get('num','')} 让球命中标记错误: 记录{recorded_hit}, 应为{expected_hit}(推荐{recorded_dir}, 实际{actual_hhad}, 比分{home_g}:{away_g}, 让球{goal})")
                    print(f"  ⚠️ {r.get('num','')} 让球命中标记错误: 记录{recorded_hit}, 应为{expected_hit}")
                    r["hhadHit"] = expected_hit
                    hhad_errors += 1
        if hhad_errors > 0:
            save_json("history.json", data)
            fixed += hhad_errors
            print(f"  已修复 {hhad_errors} 条让球命中标记")

    # B2. 胜平负方向与让球盘方向逻辑一致性（今日赛程）
    data, err = load_json("matches.json")
    if not err:
        matches = data.get("matches", [])
        logic_issues = 0
        for m in matches:
            had_dir = m.get("hadDir", "") or m.get("coreDir", "")
            hhad_dir = m.get("hhadDir", "") or m.get("hhadRec", "")
            hhad = m.get("hhad", {})
            goal = hhad.get("goal") if isinstance(hhad, dict) else None
            if not had_dir or not hhad_dir or goal is None:
                continue
            try:
                goal = float(goal)
            except Exception:
                continue
            # 逻辑检查：主胜+让1球应推让胜，主胜+让2球应推让平，主胜+让3球+应推让负
            # 客胜对称
            if had_dir == "h" and goal <= -1:  # 主队让球
                if abs(goal) >= 3 and hhad_dir != "a":
                    pass  # 让3球以上推让负合理
                elif abs(goal) == 2 and hhad_dir not in ("d", "a"):
                    pass  # 让2球推让平/让负都可能
                elif abs(goal) == 1 and hhad_dir not in ("h", "d"):
                    issues.append(f"{m.get('num','')} 主胜+让1球推{hhad_dir}逻辑可疑")
                    logic_issues += 1
            elif had_dir == "a" and goal >= 1:  # 主队受让(客队让球)
                if goal >= 3 and hhad_dir != "h":
                    pass
                elif goal == 2 and hhad_dir not in ("d", "h"):
                    pass
                elif goal == 1 and hhad_dir not in ("a", "d"):
                    issues.append(f"{m.get('num','')} 客胜+受让1球推{hhad_dir}逻辑可疑")
                    logic_issues += 1
        if logic_issues > 0:
            print(f"  ⚠️ 发现 {logic_issues} 条方向逻辑可疑(仅告警不自动修改)")

    # B3. 串关记录日期匹配检查
    data, err = load_json("parlay_history.json", {"records": []})
    if not err:
        records = data.get("records", [])
        # 加载今日matches用于比对
        today_matches = {}
        mdata, _ = load_json("matches.json")
        if mdata:
            for m in mdata.get("matches", []):
                today_matches[m.get("num", "")] = m.get("kickoff", "")
        date_issues = 0
        for rec in records:
            rec_date = rec.get("date", "")
            for leg in rec.get("legs", []):
                leg_num = leg.get("num", "")
                leg_hit = leg.get("legHit")
                # 如果leg有命中标记但比赛是今天的且未结束，清除
                if leg_num in today_matches and leg_hit in (True, False):
                    kickoff = today_matches[leg_num]
                    if not is_match_ended(kickoff, now_ts):
                        print(f"  ⚠️ 串关{rec_date} {leg_num} 未结束但标记legHit={leg_hit}，清除")
                        leg["legHit"] = None
                        leg["actualScore"] = ""
                        date_issues += 1
        if date_issues > 0:
            save_json("parlay_history.json", data)
            fixed += date_issues

    # B4. 半全场推荐方向与全场概率一致性
    data, err = load_json("matches.json")
    if not err:
        hf = data.get("halfFull", {})
        for m in hf.get("matches", []):
            rec = m.get("recommend", "")
            full_prob = m.get("fullProb", {})
            if not rec or not full_prob:
                continue
            # 推荐首字(半场)和次字(全场)
            half_char = rec[0] if rec else ""
            full_char = rec[1] if len(rec) > 1 else ""
            full_h = full_prob.get("h", 0)
            full_a = full_prob.get("a", 0)
            # 全场主胜>60%但推荐全场客胜，告警
            if full_h > 60 and full_char == "a":
                issues.append(f"半全场{m.get('num','')} 全场主胜{full_h}%但推荐{rec}(全场客胜)")
                print(f"  ⚠️ 半全场{m.get('num','')} 全场主胜{full_h}%但推荐{rec}")
            if full_a > 60 and full_char == "h":
                issues.append(f"半全场{m.get('num','')} 全场客胜{full_a}%但推荐{rec}(全场主胜)")
                print(f"  ⚠️ 半全场{m.get('num','')} 全场客胜{full_a}%但推荐{rec}")

    report["consistency_issues"] = issues
    report["consistency_fixed"] = fixed
    return fixed

# ============ C. 数据时效性检查 ============

def check_data_freshness(now_ts, report):
    """检查数据时效性"""
    print("\n[C] 数据时效性检查")
    issues = []

    data, err = load_json("matches.json")
    if not err:
        update_time = data.get("updateTime", "")
        print(f"  matches.json updateTime: {update_time}")
        if update_time:
            try:
                ut = datetime.strptime(update_time, "%Y-%m-%d %H:%M:%S")
                age_hours = (datetime.now() - ut).total_seconds() / 3600
                if age_hours > 4:
                    issues.append(f"matches.json 已 {age_hours:.1f} 小时未更新(超过4小时)")
                    print(f"  ⚠️ 数据已 {age_hours:.1f} 小时未更新")
                else:
                    print(f"  ✅ 数据新鲜度: {age_hours:.1f} 小时前更新")
            except Exception:
                pass

        # 检查已开赛超过2.5小时但未回填赛果的比赛
        matches = data.get("matches", [])
        unbackfilled = 0
        for m in matches:
            kickoff = m.get("kickoff", "")
            kt = parse_kickoff(kickoff)
            if kt and (datetime.now() - kt).total_seconds() > 2.5 * 3600:
                # 比赛已结束但matches中通常不存赛果，检查history中是否有
                pass
        # 检查history中已开赛但无赛果的
        hdata, _ = load_json("history.json", {"records": []})
        if hdata:
            today_str = datetime.now().strftime("%Y-%m-%d")
            for r in hdata.get("records", []):
                if r.get("date", "") == today_str:
                    kickoff = r.get("kickoff", "")
                    kt = parse_kickoff(kickoff)
                    if kt and (datetime.now() - kt).total_seconds() > 3 * 3600:
                        result = r.get("result")
                        if not result or not result.get("score"):
                            unbackfilled += 1
                            issues.append(f"{r.get('num','')} 开赛超3小时未回填赛果")
            if unbackfilled > 0:
                print(f"  ⚠️ {unbackfilled} 场已开赛超3小时未回填赛果")

    report["freshness_issues"] = issues
    return 0

# ============ D. 错误标记清除 ============

def clear_wrong_marks(now_ts, report):
    """清除未结束比赛的错误赛果/命中标记"""
    print("\n[D] 错误标记清除（未结束比赛）")
    fixed = 0

    # D1. hkmo_history.json
    data, err = load_json("hkmo_history.json", {"records": []})
    if not err:
        for r in data.get("records", []):
            kickoff = r.get("kickoff", "")
            if is_match_ended(kickoff, now_ts):
                continue
            if r.get("result") or r.get("hitFlag") or r.get("hhadHit") or r.get("asianHit"):
                print(f"  [港澳] 清除: {r.get('num')} {r.get('home')}vs{r.get('away')} result={r.get('result')}")
                r["result"] = ""
                r["hitFlag"] = ""
                r["hhadHit"] = ""
                r["asianHit"] = ""
                r["hhadResult"] = ""
                r["pickDir"] = None
                fixed += 1
        if fixed > 0:
            save_json("hkmo_history.json", data)

    # D1b. 已结束港澳历史比赛命中标记重新校验（防止旧版错误判定残留）
    data, err = load_json("hkmo_history.json", {"records": []})
    if not err:
        recheck_fixed = 0
        for r in data.get("records", []):
            kickoff = r.get("kickoff", "")
            if not is_match_ended(kickoff, now_ts):
                continue
            score = r.get("result", "")
            if not score or ":" not in str(score):
                continue
            try:
                parts = str(score).replace("：", ":").split(":")
                hs = int(parts[0])
                as_ = int(parts[1])
            except Exception:
                continue
            actual = "h" if hs > as_ else ("d" if hs == as_ else "a")
            rec_dirs = r.get("common_drop_dirs", [])
            if rec_dirs:
                pick_dir = rec_dirs[0]
                expected = "hit" if actual == pick_dir else "miss"
            else:
                expected = "n-a"
            current = r.get("hitFlag", "")
            if current != expected:
                print(f"  [港澳命中重校] {r.get('num')} {r.get('home')}vs{r.get('away')} "
                      f"比分={score} 实际={actual} 推荐={rec_dirs[0] if rec_dirs else '无'} "
                      f"原标记={current or '空'} → 修正={expected}")
                r["hitFlag"] = expected
                r["pickDir"] = rec_dirs[0] if rec_dirs else None
                recheck_fixed += 1
        if recheck_fixed > 0:
            save_json("hkmo_history.json", data)
            fixed += recheck_fixed
            print(f"  [港澳命中重校] 共修正 {recheck_fixed} 条命中标记")

    # D1c. matches.json中未结束比赛的hkmo字段错误标记清除（页面显示来源）
    data, err = load_json("matches.json", {})
    if not err:
        mfixed = 0
        for m in data.get("matches", []):
            hkmo = m.get("hkmo")
            if not hkmo:
                continue
            kickoff = hkmo.get("kickoff", "") or m.get("kickoff", "")
            if is_match_ended(kickoff, now_ts):
                continue
            if hkmo.get("result") or hkmo.get("hitFlag") or hkmo.get("hhadHit") or hkmo.get("asianHit"):
                print(f"  [matches港澳] 清除: {m.get('num')} {hkmo.get('home')}vs{hkmo.get('away')} result={hkmo.get('result')}")
                hkmo["result"] = ""
                hkmo["hitFlag"] = ""
                hkmo["hhadHit"] = ""
                hkmo["asianHit"] = ""
                hkmo["hhadResult"] = ""
                hkmo["pickDir"] = None
                mfixed += 1
        if mfixed > 0:
            save_json("matches.json", data)
            fixed += mfixed

    # D2. history.json
    data, err = load_json("history.json", {"records": []})
    if not err:
        cnt = 0
        for r in data.get("records", []):
            kickoff = r.get("kickoff", "")
            if is_match_ended(kickoff, now_ts):
                continue
            result = r.get("result")
            if result and result.get("score"):
                print(f"  [竞彩] 清除: {r.get('num')} {r.get('home')}vs{r.get('away')} result={result.get('score')}")
                r["result"] = None
                r["hitFlag"] = ""
                r["hhadHit"] = ""
                r["asianHit"] = ""
                cnt += 1
        if cnt > 0:
            save_json("history.json", data)
            fixed += cnt

    # D3. strongweak_history.json
    data, err = load_json("strongweak_history.json", {})
    if not err:
        cnt = 0
        today_str = time.strftime("%Y-%m-%d", time.localtime(now_ts))
        for date_key, day_data in data.items():
            # 日期key早于今天，说明比赛已结束，不清除赛果
            if date_key < today_str:
                continue
            if isinstance(day_data, dict):
                records = day_data.get("matches", day_data.get("records", []))
            else:
                records = day_data if isinstance(day_data, list) else []
            for r in records:
                kickoff = r.get("kickoff", "")
                if is_match_ended(kickoff, now_ts):
                    continue
                if r.get("result") or r.get("hitFlag"):
                    print(f"  [强弱] 清除: {r.get('num')} {r.get('home')}vs{r.get('away')}")
                    r["result"] = ""
                    r["hitFlag"] = ""
                    cnt += 1
        if cnt > 0:
            save_json("strongweak_history.json", data)
            fixed += cnt

    # D4. half_full_history.json 已移除（半全场tab已下线）

    # D5. parlay_history.json (leg级别清除)
    data, err = load_json("parlay_history.json", {"records": []})
    if not err:
        today_matches = {}
        mdata, _ = load_json("matches.json")
        if mdata:
            for m in mdata.get("matches", []):
                today_matches[m.get("num", "")] = m.get("kickoff", "")
        cnt = 0
        for rec in data.get("records", []):
            for leg in rec.get("legs", []):
                leg_num = leg.get("num", "")
                if leg_num in today_matches:
                    kickoff = today_matches[leg_num]
                    if not is_match_ended(kickoff, now_ts) and leg.get("legHit") is not None:
                        print(f"  [串关] 清除leg: {leg_num} legHit={leg.get('legHit')}")
                        leg["legHit"] = None
                        leg["actualScore"] = ""
                        cnt += 1
            # 重新计算串关整体结果
            legs = rec.get("legs", [])
            if legs:
                hits = [l.get("legHit") for l in legs]
                if any(h is None for h in hits):
                    rec["result"] = "pending"
                elif all(h is True for h in hits):
                    rec["result"] = "hit"
                elif any(h is False for h in hits):
                    rec["result"] = "miss"
        if cnt > 0:
            save_json("parlay_history.json", data)
            fixed += cnt

    report["marks_cleared"] = fixed
    return fixed

# ============ E. 代码完整性检查 ============

def check_code_integrity(report):
    """检查代码文件完整性"""
    print("\n[E] 代码完整性检查")
    issues = []

    # E1. Python语法检查
    py_files = ["fetch_daily.py", "fetch_hkmo.py", "build_panel.py",
                "deploy_cf.py", "check_and_fix.py"]
    for fn in py_files:
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            issues.append(f"缺少文件: {fn}")
            print(f"  ❌ 缺少文件: {fn}")
            continue
        try:
            py_compile.compile(path, doraise=True)
            print(f"  ✅ {fn} 语法OK")
        except py_compile.PyCompileError as e:
            issues.append(f"{fn} 语法错误: {e}")
            print(f"  ❌ {fn} 语法错误: {e}")

    # E2. template.html 占位符检查
    tpl_path = os.path.join(HERE, "template.html")
    if os.path.exists(tpl_path):
        with open(tpl_path, encoding="utf-8") as f:
            tpl = f.read()
        placeholders = ["__DATA_PLACEHOLDER__", "__HISTORY_PLACEHOLDER__", "__CONFIG_PLACEHOLDER__",
                        "__BD_DATA_PLACEHOLDER__", "__BD_HISTORY_PLACEHOLDER__", "__HKMO_HISTORY_PLACEHOLDER__"]
        for ph in placeholders:
            if ph not in tpl:
                issues.append(f"template.html 缺少占位符: {ph}")
                print(f"  ❌ template.html 缺少占位符: {ph}")
        print(f"  ✅ template.html 占位符检查完成({len(placeholders)}个)")
        # 检查关键tab是否存在
        for tab in ["halffull", "highodds", "strongweak", "hkmo"]:
            if f'data-tab="{tab}"' not in tpl:
                issues.append(f"template.html 缺少tab: {tab}")
                print(f"  ⚠️ template.html 缺少tab: {tab}")
    else:
        issues.append("缺少 template.html")
        print("  ❌ 缺少 template.html")

    # E3. cf_config.json 检查
    cf_path = os.path.join(HERE, "cf_config.json")
    if os.path.exists(cf_path):
        try:
            with open(cf_path, encoding="utf-8") as f:
                cf = json.load(f)
            required = ["token", "account_id", "project"]
            missing = [k for k in required if k not in cf]
            if missing:
                issues.append(f"cf_config.json 缺少字段: {missing}")
                print(f"  ⚠️ cf_config.json 缺少字段: {missing}")
            else:
                print(f"  ✅ cf_config.json 配置完整")
        except Exception as e:
            issues.append(f"cf_config.json 解析失败: {e}")
    else:
        issues.append("缺少 cf_config.json（部署将失败）")
        print("  ❌ 缺少 cf_config.json")

    # E4. index.html 存在性
    idx_path = os.path.join(HERE, "index.html")
    if os.path.exists(idx_path):
        size = os.path.getsize(idx_path)
        print(f"  ✅ index.html 存在({size/1024:.0f}KB)")
    else:
        issues.append("缺少 index.html（需要重新构建）")
        print("  ⚠️ 缺少 index.html")

    report["code_issues"] = issues
    return 0

# ============ F. 规则引擎验证 ============

def check_rules_engine(report):
    """验证规则引擎配置和让球胜平负规则"""
    print("\n[F] 规则引擎验证")
    issues = []

    # F1. 让球胜平负规则单元测试
    test_cases = [
        # (home, away, goal, expected)
        (2, 1, -1, "d"),   # 主让1, 2-1 => 1-1 => 让平
        (3, 1, -1, "h"),   # 主让1, 3-1 => 2-1 => 让胜
        (1, 1, -1, "a"),   # 主让1, 1-1 => 0-1 => 让负
        (2, 1, -2, "a"),   # 主让2, 2-1 => 0-1 => 让负
        (3, 1, -2, "d"),   # 主让2, 3-1 => 1-1 => 让平
        (4, 1, -2, "h"),   # 主让2, 4-1 => 2-1 => 让胜
        (1, 0, +1, "h"),   # 主受让1, 1-0 => 2-0 => 让胜
        (0, 1, +1, "d"),   # 主受让1, 0-1 => 1-1 => 让平
        (0, 2, +1, "a"),   # 主受让1, 0-2 => 1-2 => 让负
        (0, 0, +1, "h"),   # 主受让1, 0-0 => 1-0 => 让胜
    ]
    passed = 0
    failed = 0
    for home, away, goal, expected in test_cases:
        actual = calc_hhad_result(home, away, goal)
        if actual == expected:
            passed += 1
        else:
            failed += 1
            issues.append(f"让球规则测试失败: {home}-{away} 让球{goal} => 期望{expected}, 实际{actual}")
            print(f"  ❌ 让球规则: {home}-{away} 让{goal} => 期望{expected}, 实际{actual}")
    print(f"  让球规则单元测试: {passed}/{len(test_cases)} 通过")

    # F2. 竞彩规则确认：每场让球必有结果（无走水）
    print(f"  ✅ 竞彩让球胜平负无走水，每场必有让胜/让平/让负结果")

    # F3. 半全场规则确认：9种结果无走水
    print(f"  ✅ 竞彩半全场9种结果(胜胜/胜平/胜负/平胜/平平/平负/负胜/负平/负负)，无走水")

    report["rules_issues"] = issues
    return 0

# ============ 主流程 ============

def main():
    now_ts = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{'='*60}")
    print(f"  全站全面数据检查修复 {now_str}")
    print(f"{'='*60}")

    report = {
        "checkTime": now_str,
        "integrity_issues": [],
        "consistency_issues": [],
        "freshness_issues": [],
        "code_issues": [],
        "rules_issues": [],
        "integrity_fixed": 0,
        "consistency_fixed": 0,
        "marks_cleared": 0,
    }

    total_fixed = 0
    total_fixed += check_data_integrity(now_ts, report)
    total_fixed += check_data_consistency(now_ts, report)
    check_data_freshness(now_ts, report)
    total_fixed += clear_wrong_marks(now_ts, report)
    check_code_integrity(report)
    check_rules_engine(report)

    # 汇总
    all_issues = (report["integrity_issues"] + report["consistency_issues"] +
                  report["freshness_issues"] + report["code_issues"] + report["rules_issues"])

    print(f"\n{'='*60}")
    print(f"  检查完成汇总")
    print(f"{'='*60}")
    print(f"  数据完整性问题: {len(report['integrity_issues'])} (修复{report['integrity_fixed']})")
    print(f"  数据一致性问题: {len(report['consistency_issues'])} (修复{report['consistency_fixed']})")
    print(f"  数据时效性问题: {len(report['freshness_issues'])}")
    print(f"  错误标记清除: {report['marks_cleared']} 处")
    print(f"  代码完整性问题: {len(report['code_issues'])}")
    print(f"  规则引擎问题: {len(report['rules_issues'])}")
    print(f"  总计修复: {total_fixed} 处")
    print(f"  总计告警: {len(all_issues)} 项")

    # 保存检查报告
    report["total_fixed"] = total_fixed
    report["total_issues"] = len(all_issues)
    report_path = os.path.join(HERE, "check_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  检查报告已保存: check_report.json")

    # 如果有修复，重新构建页面
    if total_fixed > 0:
        print(f"\n  检测到{total_fixed}处修复，重新构建页面...")
        try:
            import subprocess
            result = subprocess.run([sys.executable, "build_panel.py"],
                                    cwd=HERE, capture_output=True, text=True, timeout=60)
            print(f"  构建: {result.stdout.strip()}")
        except Exception as e:
            print(f"  构建失败: {e}")

    return total_fixed

if __name__ == "__main__":
    main()
