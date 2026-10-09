#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
football-data.org 独立数据源（专业、免费层 TIER_ONE）

【设计原则：永远独立，绝不污染核心数据/模型】
- 本模块只读取「竞彩官方」matches.json 做跨源匹配（只读），
  只产出独立的 fd_data.json（赛程 + 官方赛果核对）。
- 不写入 matches.json（核心数据集）、不写入 history.json（模型训练数据）、
  不写入 smart_model.json。它只是官方赛果的「独立核对源」，不参与任何推荐/模型计算。

数据源：https://www.football-data.org  （免费层覆盖 PL/PD/SA/BL1/FL1/DED/PPL/BSA 等）
鉴权：请求头 X-Auth-Token（注意：不是 query 参数，用错会返回空列表）
配置（二选一）：
  1) 环境变量：export FD_API_KEY="你的Key"
  2) 本目录 fd_config.json：{"api_key":"你的Key","enabled":true}

对外接口：
    fetch_fd(matches=None) -> dict  并写入 fd_data.json
        {
          "updatedAt": "...", "source":"football-data.org", "enabled": true,
          "dateFrom":"...", "dateTo":"...",
          "matches": [ {competition, league, home, away, utcDate, status,
                        score:{home,away}, winner, num?}, ... ],
          "byNum":  { "周五001": {...}, ... }   # 成功匹配到竞彩场次号时的映射
        }
"""
import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "fd_config.json")
OUT_PATH = os.path.join(HERE, "fd_data.json")
MATCHES_PATH = os.path.join(HERE, "matches.json")
API_BASE = "https://api.football-data.org/v4"

# 竞彩联赛中文名 -> football-data.org competition code（免费层可访问）
LEAGUE_TO_CODE = {
    "英超": "PL", "英冠": "ELC", "西甲": "PD", "西乙": "SDG",
    "德甲": "BL1", "德乙": "BL2", "意甲": "SA", "意乙": "SB",
    "法甲": "FL1", "法乙": "FL2", "荷甲": "DED", "荷乙": "EDR",
    "葡超": "PPL", "苏超": "SP1", "比甲": "B1", "挪超": "NL1",
    "瑞典超": "ML1", "丹超": "SL", "俄超": "PL1", "土超": "SL1",
    "巴甲": "BSA", "阿甲": "PA1", "日职": "JL1", "日乙": "JL2",
    "韩职": "K1", "澳超": "AL1", "美职": "MLS", "中超": "CSL",
}
CODE_TO_LEAGUE = {v: k for k, v in LEAGUE_TO_CODE.items()}

# 复用 fetch_odds_bsd 的跨语言球队匹配（保证两路赔率/赛果源口径一致）
try:
    from fetch_odds_bsd import _similar, TEAM_ALIAS  # noqa
except Exception:  # pragma: no cover
    _similar = None
    TEAM_ALIAS = {}


def _load_config():
    cfg = {"api_key": "", "enabled": True}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("FD_API_KEY", "")
    return cfg


def _api_get(path, api_key, timeout=20):
    if not api_key:
        return False, "no_api_key"
    req = urllib.request.Request(
        API_BASE + path,
        headers={"X-Auth-Token": api_key, "User-Agent": "jc-football/1.0"},
    )
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        return True, json.loads(raw)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # pragma: no cover
        return False, str(e)[:120]


def _load_matches():
    if os.path.exists(MATCHES_PATH):
        try:
            with open(MATCHES_PATH, encoding="utf-8") as f:
                d = json.load(f)
            return (d.get("matches") if isinstance(d, dict) else d) or []
        except Exception:
            return []
    return []


def _empty(note="", enabled=True):
    return {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "football-data.org",
        "enabled": enabled,
        "note": note,
        "matches": [],
        "byNum": {},
    }


def fetch_fd(matches=None):
    """主入口：抓取窗口内赛事 + 官方赛果，写入独立 fd_data.json 并返回字典。

    matches: 竞彩官方场次列表（用于把 FD 赛事匹配回竞彩场次号 num）。
             为 None 时自动读取本地 matches.json（只读，不改写）。
    """
    cfg = _load_config()
    if not cfg.get("enabled", True):
        return _write(_empty("已禁用（fd_config.json enabled=false）", enabled=False))

    key = cfg.get("api_key", "")
    if not key:
        return _write(_empty("未配置 API Key：请设置 FD_API_KEY 或 fd_config.json", enabled=True))

    if matches is None:
        matches = _load_matches()

    now = datetime.utcnow()
    frm = (now - timedelta(days=1)).strftime("%Y-%m-%d")   # 含昨天，便于刚结束比赛回填
    to = (now + timedelta(days=6)).strftime("%Y-%m-%d")    # 含未来一周赛程
    ok, data = _api_get(f"/matches?dateFrom={frm}&dateTo={to}", key)
    if not ok or not isinstance(data, dict):
        return _write(_empty(f"接口异常：{data}"))

    raw = data.get("matches") or []
    out, by_num = [], {}
    for m in raw:
        comp = (m.get("competition") or {}).get("code") or ""
        ht = (m.get("homeTeam") or {}).get("name") or ""
        at = (m.get("awayTeam") or {}).get("name") or ""
        sc = m.get("score") or {}
        ft = sc.get("fullTime") or {}
        rec = {
            "competition": comp,
            "league": CODE_TO_LEAGUE.get(comp, comp),
            "home": ht,
            "away": at,
            "utcDate": m.get("utcDate", ""),
            "status": m.get("status", ""),
            "score": {"home": ft.get("home"), "away": ft.get("away")},
            "winner": sc.get("winner"),
        }
        out.append(rec)
        # 最佳努力匹配回竞彩场次号（仅当联赛可映射且队名高度相似）
        if matches and _similar:
            for cm in matches:
                lg = cm.get("league", "")
                if CODE_TO_LEAGUE.get(comp) != lg:
                    continue
                if _similar(cm.get("home", ""), ht) and _similar(cm.get("away", ""), at):
                    rec["num"] = cm.get("num")
                    by_num[cm.get("num")] = rec
                    break

    result = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "football-data.org",
        "enabled": True,
        "dateFrom": frm,
        "dateTo": to,
        "total": len(out),
        "matched": len(by_num),
        "matches": out,
        "byNum": by_num,
    }
    return _write(result)


def _write(result):
    try:
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return result


if __name__ == "__main__":
    cfg = _load_config()
    if not cfg.get("api_key"):
        print("未配置 API Key：请设置环境变量 FD_API_KEY 或创建 fd_config.json")
        print("本模块为独立核对源，不配置时静默降级，主流程不受影响。")
    else:
        r = fetch_fd()
        print(f"football-data.org：抓取 {r.get('total', 0)} 场，"
              f"匹配竞彩场次 {r.get('matched', 0)} 场，已写入 {OUT_PATH}")
