#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bump_version.py —— 版本号与更新日志维护工具

用途：每次改动（功能新增 / 缺陷修复 / 数据口径调整）之后，执行本脚本，
      自动把这次改动写进主页「通知」面板的更新日志，并把版本号 +1。

这样你打开网页 → 右上角「通知」，就能看到：
  1) 当前版本号与发布日期
  2) 【当前页数据状态】—— 数据更新时间 / 今日场次数 / 智能引擎校准数 /
     历史战绩条数 / 历史库更新时间 / 下次自动更新批次，一眼核对是否最新
  3) 每次改动的 新增功能 / 修复内容 / 数据口径提醒

用法示例：
  # 补丁版（v1.2.0 -> v1.2.1）
  python3 bump_version.py --title "优化串关 EV 排序" \
      --feature "串关改用 EV 排序替代星级" \
      --fix "修复串关重复选同一场比赛的问题"

  # 次版本（v1.2.1 -> v1.3.0）
  python3 bump_version.py --minor --title "新增矛盾信号计数" \
      --feature "新增 Conflict Count 维度" --note "样本仍不足，仅供参考"

  # 指定版本号 + 指定日期
  python3 bump_version.py --version v2.0.0 --date 2026-10-10 --title "..." --feature "..."

  # 只预览不写入
  python3 bump_version.py --dry --title "..." --feature "..."

  # 交互模式（不传任何参数时进入，逐条输入）
  python3 bump_version.py

参数：
  --major / --minor     版本号递增级别（默认 patch）
  --version X.Y.Z       直接指定版本号（优先于 --major/--minor）
  --date YYYY-MM-DD     发布日期（默认今天）
  --title  "..."        本次更新一句话标题（必填）
  --feature "..."       新增功能，可重复传入
  --fix "..."           修复内容，可重复传入
  --note "..."          数据口径提醒，可重复传入
  --dry                 只打印将要写入的内容，不修改文件
  --no-build            写入后不自动重建 index.html
"""

import argparse
import datetime as _dt
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.html")
BUILD_SCRIPT = os.path.join(HERE, "build_panel.py")

VERSION_RE = re.compile(r'^const VERSION = "(v?\d+\.\d+\.\d+)";', re.M)
CHANGELOG_RE = re.compile(r'^const CHANGELOG = \[', re.M)


def read_text(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def write_text(p, s):
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(s)


def cur_version(text):
    m = VERSION_RE.search(text)
    if not m:
        raise SystemExit("✗ 在 template.html 中找不到 `const VERSION = \"...\";`，请检查文件是否被改动")
    return m.group(1)


def bump(ver, level):
    v = ver.lstrip("v")
    a, b, c = [int(x) for x in v.split(".")]
    if level == "major":
        a, b, c = a + 1, 0, 0
    elif level == "minor":
        b, c = b + 1, 0
    else:
        c = c + 1
    return "v%d.%d.%d" % (a, b, c)


def js_escape(s):
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", " ")
             .replace("</", "<\\/"))


def build_entry(version, date, title, features, fixes, notes):
    lines = []
    lines.append("  {")
    lines.append('    version: "%s",' % version)
    lines.append('    date: "%s",' % date)
    lines.append('    title: "%s",' % js_escape(title))
    for key, items in (("features", features), ("fixes", fixes), ("notes", notes)):
        if not items:
            continue
        lines.append("    %s: [" % key)
        for it in items:
            lines.append('      "%s",' % js_escape(it))
        lines.append("    ],")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("  },")
    return "\n".join(lines)


def interactive():
    print("== 交互模式：逐条填写本次更新内容（每行一条，空行结束该类别）==")
    title = input("一句话标题: ").strip()
    while not title:
        title = input("一句话标题（必填）: ").strip()

    def collect(name):
        print("\n%s（每行一条，直接回车结束）:" % name)
        out = []
        while True:
            s = input("  > ").strip()
            if not s:
                break
            out.append(s)
        return out

    return title, collect("新增功能"), collect("修复内容"), collect("数据口径提醒")


def main():
    ap = argparse.ArgumentParser(add_help=True, description="版本号与更新日志维护工具")
    ap.add_argument("--major", action="store_true")
    ap.add_argument("--minor", action="store_true")
    ap.add_argument("--version", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--feature", action="append", default=[])
    ap.add_argument("--fix", action="append", default=[])
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()

    text = read_text(TEMPLATE)
    old = cur_version(text)

    title, features, fixes, notes = args.title, args.feature, args.fix, args.note
    if not title and not (features or fixes or notes) and sys.stdin.isatty():
        title, features, fixes, notes = interactive()
    if not title:
        raise SystemExit("✗ 缺少 --title（一句话标题）。执行 `python3 bump_version.py -h` 查看用法")

    new = args.version.strip()
    if not new:
        level = "major" if args.major else ("minor" if args.minor else "patch")
        new = bump(old, level)
    if not new.startswith("v"):
        new = "v" + new
    date = args.date.strip() or _dt.date.today().strftime("%Y-%m-%d")

    entry = build_entry(new, date, title, features, fixes, notes)

    print("版本：%s  ->  %s   （%s）" % (old, new, date))
    print("标题：%s" % title)
    print("新增 %d 条 / 修复 %d 条 / 口径提醒 %d 条" % (len(features), len(fixes), len(notes)))
    print("-" * 60)
    print(entry)
    print("-" * 60)

    if args.dry:
        print("（--dry 模式，未写入文件）")
        return

    text = VERSION_RE.sub('const VERSION = "%s";' % new, text, count=1)
    m = CHANGELOG_RE.search(text)
    if not m:
        raise SystemExit("✗ 在 template.html 中找不到 `const CHANGELOG = [`，请检查文件是否被改动")
    pos = m.end()
    text = text[:pos] + "\n" + entry + text[pos:]
    write_text(TEMPLATE, text)
    print("✓ 已写入 template.html：VERSION=%s，更新日志新增 1 条" % new)

    # 版本同步：网页版本是唯一真相源，APK 版本号必须跟随，
    # 否则会出现「网页 v1.2.2 / App v1.5」这种用户无法判断新旧的分叉。
    try:
        import version_sync
        print("→ 正在同步 APK 版本号 ...")
        version_sync.apply_to_manifest(new)
        print("✓ APK 版本已同步为 %s（versionCode=%d）" % (new, version_sync.ver_code(new)))
    except Exception as e:
        print("✗ APK 版本同步失败：%s" % e)
        print("  请手动执行：python3 version_sync.py --apply")

    if not args.no_build:
        if os.path.exists(BUILD_SCRIPT):
            print("→ 正在重建 index.html ...")
            r = subprocess.call([sys.executable, BUILD_SCRIPT], cwd=HERE)
            if r == 0:
                print("✓ index.html 已重建（数据注入完成）")
            else:
                print("✗ index.html 重建失败（exit=%d），请手动执行 python3 build_panel.py" % r)
        else:
            print("! 未找到 build_panel.py，跳过重建")

    print("\n" + "=" * 60)
    print("版本号已统一为 %s（网页 / APK 一致）" % new)
    print("=" * 60)
    print("若要重新打包 App：")
    print("  cd /workspace/apk-build && ./build_apk.sh")
    print("（小改动可不重新打包：App 会自动拉取线上最新页面并覆盖内置快照）")


if __name__ == "__main__":
    main()
