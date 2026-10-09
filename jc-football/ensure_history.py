#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ensure_history.py —— 历史数据「防抹除」保护

为什么必须有这个脚本：
  history.json 是永久累积的历史战绩库（模型统计、规则回测、alpha 计算全靠它）。
  定时任务跑在国内沙箱里，如果沙箱被重置，本地 history.json 会丢失。
  更危险的是：定时任务会拿着空历史重新构建并部署，
  **把 Cloudflare Pages 上那份完好的历史也一起覆盖掉** —— 双重丢失。

  因此每次跑定时任务之前，必须先做一次「对账」：
    本地记录数 < 线上记录数  ->  判定为本地丢失，从线上拉回（本地留存为 .bak）
    本地记录数 >= 线上       ->  本地更新，正常继续

用法：
  python3 ensure_history.py            # 对账并按需恢复
  python3 ensure_history.py --dry      # 只报告不写文件
"""
import io, json, os, sys, urllib.request, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# 线上站点地址：历史数据同时部署在 Pages 上，作为云端副本
BASE_URL = "https://jc-football-1eo.pages.dev"

# 需要保护的累积型数据文件
HIST_FILES = [
    "history.json",             # 主历史战绩库（最关键）
    "parlay_history.json",      # 串关历史
    "hkmo_history.json",        # 港澳赔率历史
    "strongweak_history.json",  # 强弱队历史
    "half_full_history.json",   # 半全场历史
    "smart_model.json",         # 智能引擎自动调参结果
]

TIMEOUT = 40


def records_count(obj):
    """从已解析对象提取真实记录数：list 取长度；dict 优先取 records 列表；否则取顶层键数。

    历史库文件（history.json 等）结构是 {"updatedAt":..,"total":..,"records":[...]}，
    若直接 len(dict) 只会数到 3 个顶层键，会掩盖真实丢失，必须深入 records。"""
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        if isinstance(obj.get("records"), list):
            return len(obj["records"])
        return len(obj)
    return 0


def count(path):
    """返回文件内的记录数；无法解析返回 None"""
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        return records_count(d)
    except Exception:
        return None


def fetch(url, retry=3):
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jc-history-guard"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception:
            if i == retry - 1:
                return None
    return None


def main():
    dry = "--dry" in sys.argv
    print("=" * 60)
    print("历史数据对账  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    restored, kept, missing_remote = [], [], []

    for name in HIST_FILES:
        local = os.path.join(HERE, name)
        local_n = count(local) if os.path.exists(local) else 0
        remote_bytes = fetch("%s/%s" % (BASE_URL, name))

        if remote_bytes is None:
            missing_remote.append(name)
            print("  %-26s 本地 %s 条 | 线上取不到（首次部署前属正常）"
                  % (name, local_n))
            continue

        try:
            remote_d = json.loads(remote_bytes.decode("utf-8"))
            remote_n = records_count(remote_d)
        except Exception:
            missing_remote.append(name)
            print("  %-26s 线上内容无法解析，跳过" % name)
            continue

        if remote_n > (local_n or 0):
            # 线上更全 -> 本地丢了，恢复
            if dry:
                print("  %-26s 本地 %s 条 < 线上 %s 条 → 需恢复（--dry 未写入）"
                      % (name, local_n, remote_n))
                restored.append(name)
                continue
            if os.path.exists(local):
                bak = local + ".bak_lost_" + datetime.datetime.now().strftime("%m%d%H%M%S")
                os.rename(local, bak)
                print("  %-26s 本地残缺版本已留存为 %s" % (name, os.path.basename(bak)))
            with io.open(local, "wb") as f:
                f.write(remote_bytes)
            print("  %-26s ⚠️ 本地 %s 条 < 线上 %s 条 → 已从线上恢复"
                  % (name, local_n, remote_n))
            restored.append(name)
        else:
            print("  %-26s 本地 %s 条 >= 线上 %s 条 → 正常" % (name, local_n, remote_n))
            kept.append(name)

    print("-" * 60)
    print("恢复 %d 个 / 正常 %d 个 / 线上缺失 %d 个"
          % (len(restored), len(kept), len(missing_remote)))
    if restored:
        print("⚠️ 检测到历史数据丢失并已从线上副本恢复，本次定时任务可安全继续。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
