#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 APK 内置快照：
  1. 以【本地最新构建版】index.html 为底（= 最新 template.html + 最新当日数据 + 368 条历史）
     —— 早期版本曾以线上抓取的 online_base.html 为底，导致 App 永远落后于网页版，
        这是「App 跟网页版不一样」的根因，现已改为本地优先。
  2. 仅当本地历史条数少于线上基线时，才从线上基线回补历史数据（HISTORY/HKMOH/SWH/EXPERT）
  3. 抹掉豆包相关文案与「安装到桌面」引导（App 内已无意义）
输出: apk-build/assets/index.html
"""
import json, os, re, sys

# 基线优先级：本地最新构建版（首选，保证 App 与网页版完全一致）> 线上完整版（仅用于回补历史）
_HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_HTML = "/workspace/jc-football/index.html"
ONLINE_HTML = os.path.join(_HERE, "online_base.html")
LOCAL_MATCHES = "/workspace/jc-football/matches.json"
OUT = os.path.join(_HERE, "assets", "index.html")

# 需要从线上基线回补的历史变量（仅在本地更薄时）
HIST_VARS = ["BUILT_HISTORY", "BUILT_HKMOH", "BUILT_SWH", "BUILT_EXPERT",
             "BUILT_EXPORTH", "BUILT_EXPERTSTATS"]


def json_span(text, start):
    """从 start 处的 '{' 开始，返回 JSON 对象的结束下标（考虑字符串转义）"""
    assert text[start] == "{", text[start:start + 20]
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
                    return i + 1
        i += 1
    raise ValueError("JSON 未闭合")


def replace_var(html, varname, new_json_obj):
    """替换 `const VAR = {...};` 形式的注入变量"""
    key = f"{varname} = "
    idx = html.find(key)
    if idx < 0:
        print(f"  [跳过] 未找到 {varname}")
        return html, False
    brace = html.find("{", idx)
    end = json_span(html, brace)
    payload = json.dumps(new_json_obj, ensure_ascii=False).replace("</", "<\\/")
    new = html[:brace] + payload + html[end:]
    print(f"  [替换] {varname}: 原 {end - brace} 字符 -> 新 {len(payload)} 字符")
    return new, True


def div_span(html, start):
    """从 start 处标签开始，返回匹配闭合标签后的下标"""
    depth = 0
    i = start
    while i < len(html):
        m = re.compile(r"<(/?)div\b", re.I).search(html, i)
        if not m:
            break
        depth += -1 if m.group(1) else 1
        i = m.end()
        if depth == 0:
            # 跳到该 </div> 的 '>'
            gt = html.find(">", i)
            return gt + 1
    return start


def remove_js_binding(html, sel):
    """删除形如 $("#sel").addEventListener(...); 的完整语句（支持多行箭头函数）"""
    marker = '$("#%s").addEventListener' % sel
    out = html
    removed = 0
    while True:
        idx = out.find(marker)
        if idx < 0:
            break
        ls = out.rfind("\n", 0, idx) + 1
        i = out.find("(", idx + len(marker))
        if i < 0:
            break
        depth = 0
        j = i
        in_str = False
        quote = ""
        esc = False
        end = None
        while j < len(out):
            c = out[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == quote:
                    in_str = False
            else:
                if c in "\"'`":
                    in_str = True
                    quote = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        k = j + 1
                        if k < len(out) and out[k] == ";":
                            end = k + 1
                        else:
                            end = j + 1
                        break
            j += 1
        if end is None:
            end = j
        while end < len(out) and out[end] == "\n":
            end += 1
        out = out[:ls] + out[end:]
        removed += 1
    return out, removed


def missing_ids(html):
    """JS 中 $("#id") 引用但 DOM 不存在的 id 集合"""
    ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', html))
    return {i for i in ids if ('id="%s"' % i) not in html}


def read_var(html, varname):
    """读取 `const VAR = {...};` 的 JSON 对象，失败返回 None"""
    key = f"{varname} = "
    idx = html.find(key)
    if idx < 0:
        return None
    brace = html.find("{", idx)
    if brace < 0:
        return None
    try:
        end = json_span(html, brace)
        return json.loads(html[brace:end])
    except Exception:
        return None


def backfill_history(html, online_html):
    """本地历史更薄时，从线上基线回补历史变量"""
    filled = []
    for var in HIST_VARS:
        lo = read_var(html, var)
        on = read_var(online_html, var)
        if on is None:
            continue
        n_lo = len(lo.get("records", [])) if isinstance(lo, dict) else 0
        n_on = len(on.get("records", [])) if isinstance(on, dict) else 0
        if n_on > n_lo:
            html, ok = replace_var(html, var, on)
            if ok:
                filled.append(f"{var}({n_lo}->{n_on})")
    return html, filled


def main():
    if not os.path.exists(LOCAL_HTML):
        sys.exit(f"缺少本地构建版 {LOCAL_HTML}，请先执行 python3 build_panel.py")
    BASE_HTML = LOCAL_HTML
    html = open(BASE_HTML, encoding="utf-8").read()
    print(f"基线（本地最新构建版）: {len(html) / 1024 / 1024:.2f} MB")

    # ---------- 0. 历史数据回补（仅当本地比线上基线更薄时）----------
    if os.path.exists(ONLINE_HTML):
        on_html = open(ONLINE_HTML, encoding="utf-8").read()
        lo_h = read_var(html, "BUILT_HISTORY") or {}
        on_h = read_var(on_html, "BUILT_HISTORY") or {}
        n_lo = len(lo_h.get("records", []))
        n_on = len(on_h.get("records", []))
        print(f"历史条数：本地 {n_lo} 条 / 线上基线 {n_on} 条")
        if n_on > n_lo:
            html, filled = backfill_history(html, on_html)
            print(f"  [回补] {', '.join(filled) or '无'}")
        else:
            print("  [回补] 不需要，本地历史已完整")
    else:
        print("  [回补] 跳过（无线上基线）")

    # ---------- 1. 替换当日数据为本地最新 ----------
    with open(LOCAL_MATCHES, encoding="utf-8") as f:
        local = json.load(f)
    print(f"本地当日数据: {local.get('count')} 场, updateTime={local.get('updateTime')}")
    html, ok = replace_var(html, "BUILT_DATA", local)
    if ok:
        html = html.replace('"updateTime": "2026-10-08 12:43:30"',
                            f'"updateTime": "{local.get("updateTime")}"', 1)

    # ---------- 2. 抹掉豆包文案与安装引导 ----------
    removed = []

    # 2a. 删除「安装到桌面」引导弹层（含豆包提示）
    m = re.search(r'<!--\s*安装 App 引导弹层[^>]*-->', html)
    if m:
        end = m.end()
        # 注释后紧跟的 <div class="modal-mask" id="installMask">
        dm = re.compile(r'<div[^>]*id="installMask"[^>]*>').search(html, end)
        if dm and dm.start() - end < 200:
            stop = div_span(html, dm.start())
            html = html[:m.start()] + html[stop:]
            removed.append("安装引导弹层(installMask)")

    # 2b. 兜底：若弹层未连带注释删除，再尝试直接按 id 删
    if "installMask" in html:
        dm = re.compile(r'<div[^>]*id="installMask"[^>]*>').search(html)
        if dm:
            stop = div_span(html, dm.start())
            html = html[:dm.start()] + html[stop:]
            removed.append("installMask(兜底)")

    # 2c. 豆包正文文案改中性
    before = html
    html = html.replace("到点请在豆包对话回复「更新」取最新链接",
                        "到点将自动拉取最新数据")
    html = html.replace("豆包 App 内打开时无法直接安装", "当前环境无法直接安装")
    html = html.replace("豆包/微信等 App 内无法直接装桌面", "部分内置浏览器无法直接装桌面")
    if html != before:
        removed.append("豆包文案")

    # 2d. 移除指向安装弹层的按钮（如有）
    for pat in [r'<button[^>]*id="[^"]*install[^"]*"[^>]*>.*?</button>']:
        found = re.findall(pat, html, re.I | re.S)
        if found:
            for f in found:
                html = html.replace(f, "")
            removed.append(f"安装按钮x{len(found)}")

    # 2e. 删除指向已移除 DOM 的 JS 事件绑定（避免 null.addEventListener 崩溃）
    for sel in ["installBtn", "closeInstall", "installMask", "copyUrlBtn"]:
        html, n = remove_js_binding(html, sel)
        if n:
            removed.append(f"JS绑定#{sel}x{n}")

    # 2f. 全局兜底：为仍可能被引用的已删元素注入隐藏占位（双保险）
    anchor = "const $ = s => document.querySelector(s);"
    guard = (anchor +
             '\n/* APK兜底：为已移除的安装引导组件注入隐藏占位，防止空引用导致初始化中断 */'
             '["installBtn","installMask","closeInstall","pageUrl","copyUrlBtn"]'
             '.forEach(function(id){if(!document.getElementById(id)){var d=document.createElement("div");'
             'd.id=id;d.style.display="none";(document.body||document.documentElement).appendChild(d);}});')
    if anchor in html:
        html = html.replace(anchor, guard, 1)
        removed.append("兜底占位注入")

    # ---------- 3. 校验 ----------
    left = html.count("豆包")
    print(f"清理项: {removed or '无'}")
    print(f"残留「豆包」字样: {left}")

    # 悬空引用校验：快照的缺失引用集不应超出基线的缺失集（基线缺失有函数级保护）
    # INTENTIONAL = 本次刻意移除、且已由「兜底占位注入」在运行时补齐的元素
    base_html = open(BASE_HTML, encoding="utf-8").read()
    base_missing = missing_ids(base_html)
    snap_missing = missing_ids(html)
    INTENTIONAL = {"installBtn", "installMask", "closeInstall", "pageUrl", "copyUrlBtn"}
    bad = snap_missing - base_missing - INTENTIONAL
    if bad:
        print(f"❌ 校验失败：快照引入了新的悬空引用 {sorted(bad)}，将导致 JS 崩溃")
        sys.exit(1)
    print(f"✅ 悬空引用校验通过（基线固有缺失 {len(base_missing)} 项，均受函数级保护；新增 0 项）")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已输出: {OUT} ({len(html) / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
