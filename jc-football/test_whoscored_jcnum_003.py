#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：WhoScored 竞彩编号匹配「一字之差」失配修复（v1.7.26）。

背景：2026-10-20 用户反馈「比分预测除周二003 外全部有比分推荐」。
根因：team_map 表内 Portsmouth 有两个中文译名（朴次茅斯 L58 / 朴茨茅斯 L1538），
TEAM_EN2ZH 后行覆盖前行 → en_to_zh('Portsmouth')='朴茨茅斯'，与竞彩官方名
「朴次茅斯」一字之差，_match_jc_num 失配 → jcNum=None → 页面无预测。

修复：
  1) team_map.py L1538 朴茨茅斯→朴次茅斯（对齐竞彩官方名）
  2) _ZH_ALIAS 增加 朴茨茅斯→朴次茅斯 兜底
  3) _match_jc_num 增加中文变体集兜底（en_to_zh_all，免疫其余 166 个同类冲突）

本测试用线上 BUILT_WHOSCORED 的真实记录（含 Wolves vs Portsmouth jcNum=None）
跑 attach_jc_numbers，验证修复后全部挂上编号。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WS_HTML = "/tmp/online_now.html"   # 线上页面（含 BUILT_WHOSCORED 真实数据）


def load_online_ws():
    html = open(WS_HTML, encoding="utf-8").read()
    m = re.search(r'const\s+BUILT_WHOSCORED\s*=\s*(\{.*?\});\s*\n', html, re.S)
    assert m, "线上页面未找到 BUILT_WHOSCORED"
    return json.loads(m.group(1))


def main():
    from fetch_whoscored import attach_jc_numbers, _load_jc_matches, _load_jc_archive

    ws = load_online_ws()
    matches = ws.get("matches", [])
    pom = next((x for x in matches if x.get("id") == "1989199"), None)
    assert pom is not None, "线上数据缺少 Wolves vs Portsmouth (id=1989199)"
    print(f"测试样本：{len(matches)} 场 WhoScored 记录")
    print(f"  目标场次：{pom['home']} vs {pom['away']}  date={pom['date']}  pred={pom.get('predH')}:{pom.get('predA')}  jcNum={pom.get('jcNum')}")

    jc = _load_jc_matches()
    archive = _load_jc_archive()
    print(f"  竞彩当前销售日 {len(jc)} 场，持久档案 {len(archive)} 条")

    # 关键断言：跑匹配前先确认「未修复前是失配的」
    from team_map import en_to_zh, zh_norm
    hz, az = zh_norm(en_to_zh(pom["home"])), zh_norm(en_to_zh(pom["away"]))
    jc003 = next((m for m in jc if m.get("num") == "周二003"), None)
    assert jc003 is not None, "matches.json 缺少周二003"
    old_ok = (zh_norm(jc003["home"]) == hz and zh_norm(jc003["away"]) == az)
    print(f"  主译名精确匹配（修复后直接匹配即应通过）: home {hz!r}=={zh_norm(jc003['home'])!r}, away {az!r}=={zh_norm(jc003['away'])!r} → {old_ok}")
    assert old_ok, "主译名仍未对齐，team_map 修复未生效"

    n, total, unmatched = attach_jc_numbers(matches, jc=jc, archive=archive)
    print(f"  匹配结果：命中 {n}/{total}")

    pom2 = next((x for x in matches if x.get("id") == "1989199"), None)
    print(f"  修复后 Portsmouth jcNum = {pom2.get('jcNum')}")
    assert pom2.get("jcNum") == "周二003", f"仍未挂上周二003：{pom2.get('jcNum')}"

    # 无回归：原先能挂上的场次不能丢
    attached = [x for x in matches if x.get("jcNum")]
    must = {"2029128": "周二008", "2029203": "周二001", "1983615": "周一005"}
    for mid, num in must.items():
        r = next((x for x in attached if x.get("id") == mid), None)
        assert r is not None and r.get("jcNum") == num, f"回归失败：{mid} 应挂 {num}，实际 {r.get('jcNum') if r else None}"
    print(f"  回归校验通过：{len(attached)} 场挂上编号，抽查 {len(must)} 场无回归")

    print("\n✅ v1.7.26 测试全部通过：Portsmouth 一字之差失配已修复")


if __name__ == "__main__":
    main()
