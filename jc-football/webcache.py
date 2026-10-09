"""统一网页抓取 TTL 缓存层

目的：把「慢变、可复用」的外部抓取（赔率历史、球队近况、港澳赔率、500竞彩页）
      按 key 做带过期时间的本地缓存，把每天约 1500~1900 次外部请求砍掉一个数量级，
      显著降低 sporttery/500.com 的反爬/封 IP 风险。

设计：
- 缓存落盘 web_cache.json，跨「运行」共享（进程级字典 + 文件持久化）。
- 沙箱重置导致文件丢失时，自动降级为「全抓」（与改造前行为一致），不报错。
- 只包裹「慢变」接口；实时核心赔率（CALC/SUPPORT）保持原样不缓存。
- 缓存项惰性清理：写入时顺手删除已过期项，避免文件无限增长。
"""

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "web_cache.json")
TTL_DEFAULT = 3 * 3600  # 秒（默认 3 小时）

_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(CACHE_FILE):
        try:
            _cache = json.load(open(CACHE_FILE, encoding="utf-8"))
            return _cache
        except Exception:
            _cache = {}
            return _cache
    _cache = {}
    return _cache


def get(key):
    """返回未过期缓存数据；缺失或已过期返回 None"""
    d = _load()
    e = d.get(key)
    if not e:
        return None
    ttl = e.get("ttl", TTL_DEFAULT)
    if time.time() - e.get("ts", 0) < ttl:
        return e.get("data")
    return None


def set(key, data, ttl_hours=3):
    """写入缓存（带惰性清理过期项）"""
    d = _load()
    now = time.time()
    # 惰性清理：删除已过期项，控制文件体积
    for k in list(d.keys()):
        ek = d[k]
        if now - ek.get("ts", 0) >= ek.get("ttl", TTL_DEFAULT):
            del d[k]
    d[key] = {"ts": now, "ttl": int(ttl_hours * 3600), "data": data}
    try:
        json.dump(d, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


# 命中统计（仅 WEB_CACHE_DEBUG=1 时打印，用于压测缓存收益）
STATS = {"hit": 0, "miss": 0}


def ttl_fetch(key, fetch_fn, ttl_hours=3):
    """带 TTL 的抓取：命中直接返回缓存；未命中则调用 fetch_fn() 抓并写回。

    fetch_fn 抛异常时不会写缓存（保持「失败即重试」语义），异常向上冒泡由调用方兜底。
    """
    cached = get(key)
    if cached is not None:
        STATS["hit"] += 1
        if os.environ.get("WEB_CACHE_DEBUG"):
            print(f"  [cache HIT] {key}", flush=True)
        return cached
    STATS["miss"] += 1
    if os.environ.get("WEB_CACHE_DEBUG"):
        print(f"  [cache MISS] {key}", flush=True)
    data = fetch_fn()
    set(key, data, ttl_hours)
    return data
