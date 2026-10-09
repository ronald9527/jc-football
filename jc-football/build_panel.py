#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建看板：读取 matches.json + history.json + rules_config.json -> 注入 template.html -> 生成 index.html"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    with open(os.path.join(HERE, "matches.json"), encoding="utf-8") as f:
        data = json.load(f)
    hist = {"updatedAt": "", "total": 0, "records": []}
    hp = os.path.join(HERE, "history.json")
    if os.path.exists(hp):
        try:
            with open(hp, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            pass
    cfg = {}
    cp = os.path.join(HERE, "rules_config.json")
    if os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    # 北单、半全场 tab 已下线，不再加载相关数据
    bd = {"updateTime": "", "count": 0, "matches": []}
    bdh = {"updateTime": "", "count": 0, "records": []}
    # 从 matches.json 中剥离半全场数据（tab 已下线）
    if isinstance(data, dict):
        data.pop("halfFull", None)
        data.pop("halfFullHistory", None)
    hkmoh = {"records": []}
    hkp = os.path.join(HERE, "hkmo_history.json")
    if os.path.exists(hkp):
        try:
            with open(hkp, encoding="utf-8") as f:
                hkmoh = json.load(f)
        except Exception:
            pass
    # 强弱对战历史
    swh = {}
    swp = os.path.join(HERE, "strongweak_history.json")
    if os.path.exists(swp):
        try:
            with open(swp, encoding="utf-8") as f:
                swh = json.load(f)
        except Exception:
            pass
    expert = {"updatedAt": "", "recommendations": []}
    ep = os.path.join(HERE, "expert_recommendations.json")
    if os.path.exists(ep):
        try:
            with open(ep, encoding="utf-8") as f:
                expert = json.load(f)
        except Exception:
            pass
    exporth = {}
    ehp = os.path.join(HERE, "expert_history.json")
    if os.path.exists(ehp):
        try:
            with open(ehp, encoding="utf-8") as f:
                exporth = json.load(f)
        except Exception:
            pass
    expertstats = {}
    esp = os.path.join(HERE, "expert_stats.json")
    if os.path.exists(esp):
        try:
            with open(esp, encoding="utf-8") as f:
                esd = json.load(f)
                expertstats = esd.get("stats", {})
        except Exception:
            pass
    # BSD 国际权威赔率（独立源，不污染核心 matches.json）
    odds_bsd = {"updatedAt": "", "source": "", "independent": True, "matches": [], "total_movements": 0}
    obp = os.path.join(HERE, "odds_bsd.json")
    if os.path.exists(obp):
        try:
            with open(obp, encoding="utf-8") as f:
                odds_bsd = json.load(f)
        except Exception:
            pass
    # football-data.org 官方赛果核对（独立源，不参与模型）
    fd = {"updatedAt": "", "source": "football-data.org", "enabled": False, "matches": [], "byNum": {}}
    fdp = os.path.join(HERE, "fd_data.json")
    if os.path.exists(fdp):
        try:
            with open(fdp, encoding="utf-8") as f:
                fd = json.load(f)
        except Exception:
            pass
    # BSD 伤停/首发/教练/天气/裁判/AI预测（独立源，不污染核心 matches.json）
    bsd_data = {"byNum": {}}
    bsp = os.path.join(HERE, "bsd_data.json")
    if os.path.exists(bsp):
        try:
            with open(bsp, encoding="utf-8") as f:
                bsd_data = json.load(f)
        except Exception:
            pass
    # 把专家战绩合并到每条推荐（主推荐expertStats + 其他专家hitRate）
    for _r in expert.get("recommendations", []):
        _name = _r.get("expertName", "")
        _st = expertstats.get(_name)
        if _st:
            _r["expertStats"] = _st
        for _o in _r.get("otherExperts", []):
            _oname = _o.get("expertName", "")
            _ost = expertstats.get(_oname)
            if _ost:
                _o["hitRate"] = _ost.get("hitRate", 0)
                _o["judged"] = _ost.get("judged", 0)
                _o["isNew"] = _ost.get("isNew", False)
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        tpl = f.read()

    js_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    js_hist = json.dumps(hist, ensure_ascii=False).replace("</", "<\\/")
    js_cfg = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    js_bd = json.dumps(bd, ensure_ascii=False).replace("</", "<\\/")
    js_bdh = json.dumps(bdh, ensure_ascii=False).replace("</", "<\\/")
    js_hkmoh = json.dumps(hkmoh, ensure_ascii=False).replace("</", "<\\/")
    js_swh = json.dumps(swh, ensure_ascii=False).replace("</", "<\\/")
    js_expert = json.dumps(expert, ensure_ascii=False).replace("</", "<\\/")
    js_exporth = json.dumps(exporth, ensure_ascii=False).replace("</", "<\\/")
    js_expertstats = json.dumps(expertstats, ensure_ascii=False).replace("</", "<\\/")
    js_odds_bsd = json.dumps(odds_bsd, ensure_ascii=False).replace("</", "<\\/")
    js_fd = json.dumps(fd, ensure_ascii=False).replace("</", "<\\/")
    js_bsd = json.dumps(bsd_data.get("byNum", {}), ensure_ascii=False).replace("</", "<\\/")
    out = (tpl.replace("__DATA_PLACEHOLDER__", js_data)
              .replace("__HISTORY_PLACEHOLDER__", js_hist)
              .replace("__CONFIG_PLACEHOLDER__", js_cfg)
              .replace("__BD_DATA_PLACEHOLDER__", js_bd)
              .replace("__BD_HISTORY_PLACEHOLDER__", js_bdh)
              .replace("__HKMO_HISTORY_PLACEHOLDER__", js_hkmoh)
              .replace("__STRONGWEAK_HISTORY_PLACEHOLDER__", js_swh)
              .replace("__EXPERT_PLACEHOLDER__", js_expert)
              .replace("__EXPERT_HISTORY_PLACEHOLDER__", js_exporth)
              .replace("__EXPERT_STATS_PLACEHOLDER__", js_expertstats)
              .replace("__ODDS_BSD_PLACEHOLDER__", js_odds_bsd)
              .replace("__FD_PLACEHOLDER__", js_fd)
              .replace("__BSD_PLACEHOLDER__", js_bsd))

    for ph in ("__DATA_PLACEHOLDER__", "__HISTORY_PLACEHOLDER__", "__CONFIG_PLACEHOLDER__",
               "__BD_DATA_PLACEHOLDER__", "__BD_HISTORY_PLACEHOLDER__", "__HKMO_HISTORY_PLACEHOLDER__",
               "__STRONGWEAK_HISTORY_PLACEHOLDER__",
               "__EXPERT_PLACEHOLDER__", "__EXPERT_HISTORY_PLACEHOLDER__", "__EXPERT_STATS_PLACEHOLDER__",
               "__ODDS_BSD_PLACEHOLDER__", "__FD_PLACEHOLDER__", "__BSD_PLACEHOLDER__"):
        assert ph not in out, f"占位符未替换: {ph}"
    out_path = os.path.join(HERE, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print("已生成:", out_path, f"({len(out)} 字符, 今日{data.get('count',0)}场, 历史{hist.get('total',0)}条, 港澳历史{len(hkmoh.get('records',[]))}条, 高手推荐{len(expert.get('recommendations',[]))}条, 国际权威赔率{len(odds_bsd.get('matches',[]))}场, 官方赛果{len(fd.get('matches',[]))}场)")

if __name__ == "__main__":
    main()
