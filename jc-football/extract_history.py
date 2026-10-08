#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从线上完整版页面中提取被注入的数据块，恢复成本地 JSON 资产。

用途：源码包自带的历史数据缺失（仅16条），而线上页面里内嵌了 356 条历史战绩、
85 条港澳记录等。本脚本把这些资产还原为独立 JSON，供分析、回测与后续构建使用。

用法: python3 extract_history.py [--base apk-build/online_base.html]
"""
import json, os, sys, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
# 基线默认在同级 apk-build/ 下，也兼容放在 ../apk-build/
_CANDS = [
    os.path.join(HERE, "apk-build", "online_base.html"),
    os.path.join(HERE, "..", "apk-build", "online_base.html"),
]
DEFAULT_BASE = next((p for p in _CANDS if os.path.exists(p)), _CANDS[-1])

# 页面变量名 -> 输出文件名
VAR_MAP = {
    "HISTORY": "history.json",
    "BUILT_HKMOH": "hkmo_history.json",
    "BUILT_SWH": "strongweak_history.json",
    "EXPERT": "expert_recommendations.json",
    "BUILT_DATA": "matches_online.json",   # 线上当日数据（仅供比对，不覆盖 matches.json）
}


def json_span(text, start):
    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    raise ValueError("JSON 未闭合")


def main():
    base = DEFAULT_BASE
    for i, a in enumerate(sys.argv):
        if a == "--base" and i + 1 < len(sys.argv):
            base = sys.argv[i + 1]
    if not os.path.exists(base):
        sys.exit(f"找不到基线文件: {base}")

    html = open(base, encoding="utf-8").read()
    print(f"基线: {base} ({len(html) / 1024 / 1024:.2f} MB)")

    for var, out_name in VAR_MAP.items():
        idx = html.find(f"{var} = ")
        if idx < 0:
            print(f"  [跳过] 未找到 {var}")
            continue
        brace = html.find("{", idx)
        raw = json_span(html, brace)
        try:
            data = json.loads(raw.replace("<\\/", "</"))
        except Exception as e:
            print(f"  [失败] {var} 解析错误: {e}")
            continue

        out_path = os.path.join(HERE, out_name)
        # 历史类文件做合并去重，避免覆盖本地新增记录
        if out_name in ("history.json", "hkmo_history.json") and os.path.exists(out_path):
            try:
                old = json.load(open(out_path, encoding="utf-8"))
                if isinstance(old, dict) and isinstance(data, dict) and "records" in data:
                    seen = {r.get("id") for r in old.get("records", []) if r.get("id")}
                    added = [r for r in data.get("records", []) if r.get("id") not in seen]
                    data["records"] = old.get("records", []) + added
                    print(f"  [合并] {out_name}: 本地 {len(old.get('records', []))} + 新增 {len(added)}")
            except Exception as e:
                print(f"  [警告] 合并失败，直接覆盖: {e}")

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

        if isinstance(data, dict):
            n = len(data.get("records", [])) if "records" in data else len(data.get("matches", []))
            print(f"  [写出] {out_name}: {n} 条")

    print("提取完成")


if __name__ == "__main__":
    main()
