#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
version_sync.py —— 全项目版本号单一真相源（Single Source of Truth）

背景（为什么要有这个文件）：
  之前网页版本号（template.html 的 const VERSION）和 APK 版本号
  （AndroidManifest 的 android:versionName）是两套独立维护的数字，
  各自递增、互不相干，结果出现「网页 v1.2.2 / App v1.5」这种分叉——
  用户根本判断不了自己手机里装的到底是不是最新版。

规则（此后全项目必须遵守）：
  1. 网页版本是唯一真相源：template.html 里的 `const VERSION = "vX.Y.Z";`
  2. APK 的 versionName 必须等于网页版本（去掉 v 前缀）
  3. versionCode 由版本号换算：major*10000 + minor*100 + patch
     —— 这样版本号越大 versionCode 必然越大，Android 升级判断永不回退
  4. 换算算法必须与 App 端 MainActivity.verCode() 完全一致，否则 App 会误判
  5. 任何脚本都不得手工写死版本号，一律调用本模块

用法：
  python3 version_sync.py            # 查看当前各处的版本号是否一致
  python3 version_sync.py --apply    # 以网页版本为准，回写 AndroidManifest
  python3 version_sync.py --check    # 不一致时退出码非 0（供 CI / 自检调用）
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.html")
INDEX = os.path.join(HERE, "index.html")
MANIFEST = os.path.join(HERE, "..", "apk-build", "AndroidManifest.xml")

VERSION_RE = re.compile(r'^const VERSION = "(v?\d+\.\d+\.\d+)";', re.M)
MANIFEST_NAME_RE = re.compile(r'android:versionName="([^"]*)"')
MANIFEST_CODE_RE = re.compile(r'android:versionCode="(\d+)"')


def read_text(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def write_text(p, s):
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(s)


def parse_ver(v):
    """'v1.3.0' / '1.3.0' -> (1, 3, 0)"""
    v = (v or "").strip().lstrip("vV")
    parts = v.split(".")
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        raise ValueError("无法解析版本号: %r" % v)
    return tuple(int(x) for x in parts)


def ver_code(v):
    """语义化版本 -> Android versionCode。必须与 App 端 verCode() 算法一致。"""
    a, b, c = parse_ver(v)
    return a * 10000 + b * 100 + c


def page_version():
    """读取网页版本（优先 template.html，回退 index.html）"""
    for p in (TEMPLATE, INDEX):
        if os.path.exists(p):
            m = VERSION_RE.search(read_text(p))
            if m:
                return m.group(1)
    raise SystemExit("✗ 未能在 template.html / index.html 中找到 const VERSION")


def apk_version():
    """读取 APK 版本号 -> (versionName, versionCode)"""
    if not os.path.exists(MANIFEST):
        return None, None
    t = read_text(MANIFEST)
    mn = MANIFEST_NAME_RE.search(t)
    mc = MANIFEST_CODE_RE.search(t)
    return (mn.group(1) if mn else None,
            int(mc.group(1)) if mc else None)


def apply_to_manifest(ver):
    """把网页版本回写到 AndroidManifest。返回是否发生了改动。"""
    if not os.path.exists(MANIFEST):
        print("! 未找到 AndroidManifest.xml，跳过 APK 版本同步")
        return False
    t = read_text(MANIFEST)
    name = ver.lstrip("v")
    code = ver_code(ver)
    old_name, old_code = apk_version()
    if old_name == name and old_code == code:
        return False
    t = MANIFEST_NAME_RE.sub('android:versionName="%s"' % name, t, count=1)
    t = MANIFEST_CODE_RE.sub('android:versionCode="%d"' % code, t, count=1)
    write_text(MANIFEST, t)
    print("✓ AndroidManifest: %s(%s) -> %s(%d)" % (old_name, old_code, name, code))
    return True


def main():
    args = set(sys.argv[1:])
    ver = page_version()
    name, code = apk_version()
    want_code = ver_code(ver)

    print("网页版本（真相源）: %s" % ver)
    print("APK versionName  : %s" % name)
    print("APK versionCode  : %s  （应为 %d）" % (code, want_code))

    consistent = (name == ver.lstrip("v")) and (code == want_code)

    if "--apply" in args:
        changed = apply_to_manifest(ver)
        if not changed:
            print("✓ APK 版本已是最新，无需改动")
        name, code = apk_version()
        consistent = (name == ver.lstrip("v")) and (code == want_code)
        print("同步后: versionName=%s versionCode=%s" % (name, code))

    if consistent:
        print("\n✓ 版本一致")
        return 0

    print("\n✗ 版本不一致")
    if "--check" in args or "--apply" in args:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
