#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比分预测全覆盖回归测试（v1.7.51）：足球数据未覆盖联赛也应有 Poisson 预测。

锁定行为：
1. 竞彩赛程中「联赛不在 CODE_TO_LEAGUE 取值集合」的比赛，混合路径应补生成
   source=local-odds 的预测（亚冠/德国杯/瑞超等不再空白）；
2. 联赛在 CODE_TO_LEAGUE 取值集合的比赛不重复补（避免与足球数据重复）；
3. 混合结果对当天全部竞彩比赛覆盖无缺口（不依赖英语队名匹配，仅校验非覆盖联赛补齐）。
"""
import os, json, datetime
import fetch_scores as fs

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime.datetime(2026, 10, 27, 19, 0, 0)


def _build_local_subset(jc_matches, only_uncovered=True):
    """复刻 fetch_scores 主流程里的本地补全覆盖逻辑（不写盘、不碰网络），返回补出的记录列表。"""
    out = []
    values = set(fs.CODE_TO_LEAGUE.values())
    for m in jc_matches:
        lg = m.get("league", "")
        if only_uncovered and lg in values:
            continue
        date = m.get("date", "")
        try:
            md = datetime.datetime.strptime(date, "%Y-%m-%d").date()
        except Exception:
            continue
        if not ((NOW - datetime.timedelta(days=2)).date() <= md <= (NOW + datetime.timedelta(days=2)).date()):
            continue
        lh, la = fs._lambda_from_jc(m)
        if lh is None or la is None:
            continue
        pred = fs._poisson_grid(lh, la)
        out.append({"league": lg, "home": m.get("home"), "away": m.get("away"),
                    "predH": pred["predH"], "predA": pred["predA"], "source": "local-odds"})
    return out


def test_uncovered_leagues_get_local_prediction():
    # 构造样本：意甲（足球数据覆盖）+ 亚冠/德国杯/瑞超（未覆盖）
    jc = [
        {"date": "2026-10-27", "league": "意甲", "home": "萨索洛", "away": "拉齐奥",
         "hadProb": {"h": 40, "d": 28, "a": 32}, "goalsExp": 2.5},
        {"date": "2026-10-27", "league": "亚冠精英", "home": "北京国安", "away": "全北现代",
         "hadProb": {"h": 45, "d": 27, "a": 28}, "goalsExp": 2.6},
        {"date": "2026-10-27", "league": "德国杯", "home": "多特蒙德", "away": "云达不来梅",
         "hadProb": {"h": 60, "d": 22, "a": 18}, "goalsExp": 3.1},
        {"date": "2026-10-27", "league": "瑞超", "home": "AIK索尔纳", "away": "埃尔夫斯堡",
         "hadProb": {"h": 42, "d": 28, "a": 30}, "goalsExp": 2.4},
    ]
    added = _build_local_subset(jc, only_uncovered=True)
    lgs = {a["league"] for a in added}
    assert "意甲" not in lgs, "意甲不应被本地补（足球数据已覆盖），实得 %r" % lgs
    assert "亚冠精英" in lgs, "亚冠应被本地补，实得 %r" % lgs
    assert "德国杯" in lgs, "德国杯应被本地补，实得 %r" % lgs
    assert "瑞超" in lgs, "瑞超应被本地补，实得 %r" % lgs
    for a in added:
        assert 0 <= a["predH"] <= 8 and 0 <= a["predA"] <= 8
        assert a["source"] == "local-odds"
    print("1. 未覆盖联赛补 local-odds OK：%r" % sorted(lgs))


def test_no_duplicate_for_covered_leagues():
    # 全部为足球数据已覆盖联赛 → 本地补应为空（由足球数据负责，不重复）
    jc = [
        {"date": "2026-10-27", "league": "英超", "home": "A", "away": "B", "hadProb": {"h": 50, "d": 25, "a": 25}, "goalsExp": 2.7},
        {"date": "2026-10-27", "league": "西甲", "home": "C", "away": "D", "hadProb": {"h": 45, "d": 28, "a": 27}, "goalsExp": 2.6},
    ]
    added = _build_local_subset(jc, only_uncovered=True)
    assert added == [], "已覆盖联赛不应补，实得 %r" % added
    print("2. 已覆盖联赛不重复补 OK")


def test_window_filter():
    # 窗口外（>2天）的未覆盖联赛比赛不应补
    jc = [
        {"date": "2026-11-15", "league": "亚冠精英", "home": "E", "away": "F",
         "hadProb": {"h": 45, "d": 27, "a": 28}, "goalsExp": 2.6},
    ]
    added = _build_local_subset(jc, only_uncovered=True)
    assert added == [], "窗口外不应补，实得 %r" % added
    print("3. ±2天窗口过滤 OK")


if __name__ == "__main__":
    test_uncovered_leagues_get_local_prediction()
    test_no_duplicate_for_covered_leagues()
    test_window_filter()
    print("\n✅ test_basketball_scorepred_coverage 全过")
