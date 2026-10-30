#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_vzhan_gateway.py — V站(mapi.shemen365.com) API 网关稳定性监控
====================================================================

为什么需要它：
    V站·呼死狗 情报来自逆向的免鉴权网关 mapi.shemen365.com。
    该网关稳定性未知（公开站 vzhan310.com 是阿里云 WAF 永久墙，H5 网关
    也可能随时加鉴权/限流）。一旦网关挂掉，每日 fetch_vzhan 静默失败，
    页面 V站 分区会无声无息地停更——这是最隐蔽的事故。本脚本做主动探活
    + 状态历史 + 可选告警，把「静默停更」变成「可见告警」。

探针逻辑：
    GET /choose/match/football —— 仅读取前若干字节判定：
      · HTTP 200 且响应体含 `"code":0` 且 `"data":`（列表非空即好）
      · 否则（连接超时/非200/业务码非0/空数据）判为 down
    只读前 8KB，避免每次探活下载整个竞彩列表（省带宽、降打扰）。

状态历史（vzhan_gateway_status.json）：
    updatedAt / status(up|down) / latencyMs / lastUp / lastDown /
    consecFails / totalChecks / totalFails / note

告警：
    · 默认仅日志输出（适合 run_daily 内嵌，不阻断部署）
    · 若设置环境变量 VZHAN_ALERT_WEBHOOK（钉钉/企业微信/Slack 通用），
      则在「首次转 down」与「恢复 up」两个节点推送一条消息；
      连续失败期间不重复刷屏。
    · 退出码：up=0，down=1（便于 cron/CI 识别并升级告警）

用法：
    python3.11 check_vzhan_gateway.py            # 探活+写状态+日志
    python3.11 check_vzhan_gateway.py --json     # 额外打印状态 JSON
    python3.11 check_vzhan_gateway.py --probe-only  # 只探活不写文件/不告警
"""
import os
import sys
import json
import time
import urllib.request
import urllib.parse
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(HERE, "vzhan_gateway_status.json")
BASE = "https://mapi.shemen365.com"
PROBE = "/choose/match/football"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
TIMEOUT = 20
READ_BYTES = 8192


def load_status():
    try:
        return json.load(open(STATUS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_status(s):
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("  [warn] 写网关状态失败:", str(e)[:80])


def probe():
    """返回 (ok:bool, latency_ms:int, note:str)。"""
    url = BASE + PROBE
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            code = r.getcode()
            head = r.read(READ_BYTES).decode("utf-8", "ignore")
        dt = int((time.time() - t0) * 1000)
        if code != 200:
            return False, dt, "HTTP %s" % code
        if '"code":0' not in head and '"code": 0' not in head:
            return False, dt, "业务码非0(鉴权/限流?)"
        if '"data":' not in head and '"data": [' not in head:
            return False, dt, "响应无 data 列表"
        return True, dt, "OK"
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        return False, dt, str(e)[:80]


def _alert(msg):
    """可选告警：仅当 VZHAN_ALERT_WEBHOOK 配置时推送。"""
    url = os.environ.get("VZHAN_ALERT_WEBHOOK", "").strip()
    if not url:
        return
    try:
        body = {"text": msg}
        if "qyapi.weixin.qq.com" in url:
            body = {"msgtype": "text", "text": {"content": msg}}
        elif "dingtalk.com" in url:
            body = {"msgtype": "text", "text": {"content": msg}}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        print("  [alert] 已推送告警 -> %s" % url.split("?")[0])
    except Exception as e:
        print("  [warn] 告警推送失败:", str(e)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="额外打印状态 JSON")
    ap.add_argument("--probe-only", action="store_true", help="只探活，不写文件/不告警")
    args = ap.parse_args()

    ok, dt, note = probe()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print("[网关] mapi.shemen365.com -> %s | 延迟 %dms | %s" %
          ("UP" if ok else "DOWN", dt, note))

    if args.probe_only:
        return 0 if ok else 1

    st = load_status()
    total = int(st.get("totalChecks", 0)) + 1
    total_fails = int(st.get("totalFails", 0)) + (0 if ok else 1)
    consec = 0 if ok else int(st.get("consecFails", 0)) + 1

    new = {
        "updatedAt": now,
        "status": "up" if ok else "down",
        "latencyMs": dt,
        "lastUp": now if ok else st.get("lastUp", ""),
        "lastDown": now if not ok else st.get("lastDown", ""),
        "consecFails": consec,
        "totalChecks": total,
        "totalFails": total_fails,
        "note": note,
    }
    save_status(new)

    # 告警：首次转 down / 恢复 up 两个节点各推一次，避免刷屏
    prev = st.get("status")
    if not ok and prev != "down":
        _alert("⚠️ V站网关探活失败：%s（延迟%dms）。fetch_vzhan 可能静默停更，请关注。" % (note, dt))
    elif ok and prev == "down":
        _alert("✅ V站网关已恢复（延迟%dms）。fetch_vzhan 明日运行将重新拉取。" % dt)

    if args.json:
        print(json.dumps(new, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
