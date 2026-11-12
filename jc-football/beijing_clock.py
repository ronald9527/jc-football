#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一权威北京时间时钟（v1.7.132 起全流水线唯一「今天」口径）。

事故背景
--------
2026-11-13 排查「分层串关 / 比分预测一直不更新」时发现：沙箱 / CI 容器的
系统时钟可能与真实北京时间漂移数小时（实测沙箱 UTC 12:49，而 Cloudflare
两个独立域名返回的 HTTP Date 均为 UTC 19:20，差 6.5h）。
原先 fetch_daily.py 用 ``time.strftime("%Y-%m-%d")`` + ``time.time()`` 判定
「竞彩当天批次」与「是否已开赛」，于是：
  * 真实北京已是 11-13（周五批次），系统仍算 11-12（周四批次）；
  * 周四场次（凌晨 01:00 开球、其实已开赛）被当成未开赛塞进候选池；
  * 比分预测展示窗、部署时间戳整体偏移。
DAY 判定错一天 → 每天默认展示错批次 → 用户看到「一直没有推荐 / 不更新」。

口径优先级（全部失败才用本地时钟，绝不静默假装成功）
----------------------------------------------------
1. 环境变量 ``JC_BEIJING_NOW``（run_daily.sh 用权威 HTTP Date 校准后注入，
   格式 ``YYYY-MM-DD HH:MM:SS``）；整个流水线共享同一时刻，避免各脚本各自取
   时间导致跨零点不一致。
2. 权威 HTTP ``Date`` 响应头（Cloudflare Pages / cloudflare.com），UTC +8h。
3. 本地系统时间（run_daily.sh 已 export TZ=Asia/Shanghai），离线兜底。

模块级缓存：一轮流水线只做一次网络校准；单次超时 3s，失败静默降级并在
``source()`` 里如实标注，供 selftest 判定是否用了漂移时钟。
"""
import os
import time
import datetime
import email.utils

_CACHE = {}
_ENV = "JC_BEIJING_NOW"
_CALIB_URLS = (
    "https://jc-football-1eo.pages.dev/version.json",
    "https://www.cloudflare.com",
)


def _from_http_date():
    """返回 (北京时间 naive datetime, 说明) 或 (None, 原因)。"""
    try:
        import urllib.request
        for url in _CALIB_URLS:
            try:
                req = urllib.request.Request(url, method="HEAD",
                                            headers={"User-Agent": "jc-beijing-clock/1.0"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    raw = r.headers.get("Date")
                if not raw:
                    continue
                dt = email.utils.parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                bj = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                return bj.replace(tzinfo=None), "HTTP Date(%s)" % url.split("/")[2]
            except Exception:
                continue
    except Exception:
        pass
    return None, "no-http-date"


def _resolve():
    global _CACHE
    if _CACHE:
        return _CACHE["now"], _CACHE["source"]
    env = (os.environ.get(_ENV) or "").strip()
    if env:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(env, fmt)
                _CACHE = {"now": dt, "source": "env(%s)" % _ENV}
                return _CACHE["now"], _CACHE["source"]
            except ValueError:
                continue
    bj, src = _from_http_date()
    if bj is not None:
        _CACHE = {"now": bj, "source": src}
        return bj, src
    # 离线兜底：本地时钟（run_daily.sh 已设 TZ=Asia/Shanghai）
    _CACHE = {"now": datetime.datetime.now(), "source": "local-clock"}
    return _CACHE["now"], _CACHE["source"]


def now():
    """权威北京时间的 naive datetime。"""
    dt, _ = _resolve()
    return dt


def today():
    """权威北京日期字符串 YYYY-MM-DD（竞彩当天批次的唯一口径）。"""
    return now().strftime("%Y-%m-%d")


def timestamp():
    """权威北京时间的 Unix 时间戳（用于「是否已开赛」比较）。"""
    return time.mktime(now().timetuple())


def source():
    """本次时间来源，供 selftest / 日志核对。"""
    _, src = _resolve()
    return src


def drift_seconds():
    """权威时间与本地系统时钟的差值（秒），selftest 用它判定漂移。"""
    return now().timestamp() - datetime.datetime.now().timestamp()


if __name__ == "__main__":
    print(now().strftime("%Y-%m-%d %H:%M:%S"), "|", source(),
          "| local=", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "| drift=%.0fs" % drift_seconds())
