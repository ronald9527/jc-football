#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能增强引擎（Smart Engine）—— 让推荐从「看起来准」变成「真的有期望值」

【为什么需要它】
对 348 场已开赛历史的回测发现三个致命问题：
  1. 总体命中率 68.5% 是被「反指信号(bad)」撑起来的；可直接下注的正路信号(good)
     命中率仅 50.0%，与市场赔率隐含概率(50.3%)几乎完全一致 —— 没有任何信息优势。
  2. 信心星级与实际命中率「不单调」：0★ 命中 77.4%，3★ 只有 57.0%（低于基线）。
     星级越高反而越不准，属于误导性指标。
  3. 模型存在明确的「负价值区间」：官方概率 30%~45% 的推荐，实际命中率仅 10%，
     alpha -23.2pt，是纯亏损区。

【本引擎做什么】
  1. 从历史战绩中「学习」各概率区间 / 各规则组的真实 alpha（超越市场共识的部分）；
  2. 用贝叶斯收缩处理小样本，避免 4 场 3 中 = 75% 这类假象；
  3. 输出校准后的预期命中率 + 期望值(EV)，并给出「是否值得下注」的结论；
  4. 自动回写规则权重，形成 预测→验证→修正 的闭环。

【用法】
    python3 smart_engine.py report       # 输出学习与诊断报告
    python3 smart_engine.py tune         # 自动校准规则权重，写回 rules_config.json
    python3 smart_engine.py predict      # 对当日 matches.json 逐场给出 EV 评估

【接入】
    from smart_engine import SmartEngine
    eng = SmartEngine()
    r = eng.predict(match_record)   # -> {"p_cal","ev","stars","advice","reasons"}
"""
import json, os, sys, math
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "history.json")
MATCHES = os.path.join(HERE, "matches.json")
RULES = os.path.join(HERE, "rules_config.json")
MODEL = os.path.join(HERE, "smart_model.json")

# 贝叶斯收缩先验强度：样本越少，越向 0 收缩
SHRINK_K = 15.0
# 多组叠加衰减系数：触发组数越多，边际贡献越低
STACK_DECAY = 0.3
# 概率分箱边界
BINS = [(0.0, 0.30), (0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.01)]
BIN_LABEL = ["<30%", "30-45%", "45-55%", "55-70%", ">70%"]


def _rate(h, m):
    return (h / (h + m)) if (h + m) else None


def _shrink(raw, n, k=SHRINK_K):
    """贝叶斯收缩：样本不足时向 0 靠拢"""
    if n <= 0:
        return 0.0
    return raw * (n / (n + k))


class SmartEngine:
    def __init__(self, history_path=HISTORY):
        self.history_path = history_path
        self.records = []
        self.bin_alpha = {}      # 概率区间 -> alpha
        self.group_alpha = {}    # 规则组 -> alpha（仅正路信号）
        self.league_alpha = {}   # 联赛 -> alpha
        self.base_hit = 0.0
        self.load()
        self.learn()

    # ---------- 数据加载 ----------
    def load(self):
        if not os.path.exists(self.history_path):
            return
        try:
            with open(self.history_path, encoding="utf-8") as f:
                d = json.load(f)
            self.records = d.get("records", []) if isinstance(d, dict) else (d or [])
        except Exception:
            self.records = []

    def _good_samples(self):
        """可直接下注的样本：verdict.t == good 且已有赛果判定"""
        out = []
        for r in self.records:
            v = r.get("verdict") or {}
            if v.get("t") != "good":
                continue
            if r.get("hitFlag") not in ("hit", "miss"):
                continue
            d = v.get("dir")
            odds = (r.get("odds") or {}).get(d)
            prob = (r.get("prob") or {}).get(d)
            if not odds or not prob:
                continue
            out.append({
                "rec": r, "dir": d, "odds": float(odds),
                "p_off": float(prob) / 100.0,
                "hit": r.get("hitFlag") == "hit",
                "stars": int(v.get("stars") or 0),
                "league": r.get("league") or "-",
                "groups": [x.get("g") for x in (r.get("hits") or [])
                           if isinstance(x, dict) and x.get("g")],
            })
        return out

    # ---------- 学习 ----------
    def learn(self):
        samples = self._good_samples()
        if not samples:
            return
        self.n_samples = len(samples)
        hits = sum(1 for s in samples if s["hit"])
        self.base_hit = hits / len(samples)

        # 1) 概率区间 alpha
        for (lo, hi), label in zip(BINS, BIN_LABEL):
            sub = [s for s in samples if lo <= s["p_off"] < hi]
            if len(sub) < 5:
                self.bin_alpha[label] = {"n": len(sub), "alpha": 0.0, "raw": None}
                continue
            mp = sum(s["p_off"] for s in sub) / len(sub)
            act = sum(1 for s in sub if s["hit"]) / len(sub)
            self.bin_alpha[label] = {
                "n": len(sub), "raw": act - mp,
                "alpha": _shrink(act - mp, len(sub)),
                "mean_p": mp, "actual": act,
            }

        # 2) 规则组 alpha（相对该场官方概率的超出部分）
        g = defaultdict(list)
        for s in samples:
            for k in s["groups"]:
                g[k].append(s)
        for k, sub in g.items():
            mp = sum(x["p_off"] for x in sub) / len(sub)
            act = sum(1 for x in sub if x["hit"]) / len(sub)
            self.group_alpha[k] = {
                "n": len(sub), "raw": act - mp,
                "alpha": _shrink(act - mp, len(sub)),
                "actual": act, "mean_p": mp,
            }

        # 3) 联赛 alpha
        lg = defaultdict(list)
        for s in samples:
            lg[s["league"]].append(s)
        for k, sub in lg.items():
            if len(sub) < 6:
                continue
            mp = sum(x["p_off"] for x in sub) / len(sub)
            act = sum(1 for x in sub if x["hit"]) / len(sub)
            self.league_alpha[k] = {
                "n": len(sub), "raw": act - mp,
                "alpha": _shrink(act - mp, len(sub)),
            }

    # ---------- 预测 ----------
    @staticmethod
    def _odds_prob(rec, d):
        """兼容两种数据格式：历史库用 odds/prob，当日数据用 had/hadProb"""
        odds = (rec.get("odds") or {}).get(d)
        prob = (rec.get("prob") or {}).get(d)
        if odds is None:
            odds = (rec.get("had") or {}).get(d)
        if prob is None:
            prob = (rec.get("hadProb") or {}).get(d)
        try:
            odds = float(odds) if odds not in (None, "") else None
        except (TypeError, ValueError):
            odds = None
        try:
            prob = float(prob) if prob not in (None, "") else None
        except (TypeError, ValueError):
            prob = None
        return odds, prob

    def predict(self, rec):
        """对单场比赛给出校准概率 / EV / 建议

        rec 需含: 赔率(had/odds)、概率(hadProb/prob)、verdict.dir、hits[]
        """
        v = rec.get("verdict") or {}
        d = v.get("dir")
        if not d:
            return None
        odds, prob = self._odds_prob(rec, d)
        if not odds or not prob:
            return None
        p_off = float(prob) / 100.0 if prob > 1 else float(prob)
        odds = float(odds)

        # 命中判定类型：反指(bad/cold)不参与下注评估
        t = v.get("t")

        # 概率区间 alpha
        label = BIN_LABEL[-1]
        for (lo, hi), lb in zip(BINS, BIN_LABEL):
            if lo <= p_off < hi:
                label = lb
                break
        a_bin = self.bin_alpha.get(label, {}).get("alpha", 0.0)

        # 规则组叠加（带衰减）
        groups = [x.get("g") for x in (rec.get("hits") or [])
                  if isinstance(x, dict) and x.get("g")]
        ga = [self.group_alpha.get(k, {}).get("alpha", 0.0) for k in groups]
        a_grp = sum(ga) / (1 + STACK_DECAY * max(len(ga) - 1, 0)) if ga else 0.0

        # 联赛修正
        a_lg = self.league_alpha.get(rec.get("league") or "-", {}).get("alpha", 0.0)

        alpha = a_bin + a_grp + a_lg * 0.5
        p_cal = min(max(p_off + alpha, 0.02), 0.96)

        ev = p_cal * odds - 1.0

        # 星级：改由「校准概率 + EV」驱动，替代原手工星级
        stars = 0
        if ev > 0.15 and p_cal >= 0.55:
            stars = 5
        elif ev > 0.08 and p_cal >= 0.50:
            stars = 4
        elif ev > 0.02:
            stars = 3
        elif ev > -0.05:
            stars = 2
        elif ev > -0.12:
            stars = 1

        if t in ("bad", "cold"):
            advice = "反指信号：用于排除该方向，不可直接下注"
        elif t == "warn" or not t:
            advice = "警示信号：不参与下注评估"
        elif ev >= 0.05:
            advice = "值得考虑（正期望）"
        elif ev >= -0.05:
            advice = "边际，接近盈亏平衡"
        else:
            advice = "不建议（负期望）"

        reasons = [
            f"官方概率{p_off * 100:.0f}%",
            f"区间{label} alpha{a_bin * 100:+.1f}pt",
        ]
        if ga:
            reasons.append(f"规则信号{len(ga)}组 alpha{a_grp * 100:+.1f}pt")
        if abs(a_lg) > 0.02:
            reasons.append(f"联赛修正{a_lg * 100:+.1f}pt")

        return {
            "dir": d, "odds": odds, "p_off": round(p_off, 4),
            "alpha": round(alpha, 4), "p_cal": round(p_cal, 4),
            "ev": round(ev, 4), "stars": stars,
            "advice": advice, "reasons": reasons,
            "type": t,
        }

    # ---------- 报告 ----------
    def report(self):
        print("=" * 74)
        print("  智能引擎诊断报告")
        print("=" * 74)
        print(f"历史记录: {len(self.records)} 条")
        print(f"可用于下注评估的正路样本: {getattr(self, 'n_samples', 0)} 场")
        print(f"正路信号总体命中率: {self.base_hit * 100:.1f}%")

        print("\n【1】概率区间 alpha（决定「哪些概率段值得押」）")
        print(f"{'区间':<10}{'样本':>6}{'官方概率':>10}{'实际命中':>10}{'alpha':>10}{'收缩后':>10}   verdict")
        for lb in BIN_LABEL:
            b = self.bin_alpha.get(lb, {})
            if not b.get("n"):
                print(f"{lb:<10}{0:>6}{'—':>10}{'—':>10}{'—':>10}{'—':>10}   样本不足")
                continue
            raw = b.get("raw")
            verdict = "✅ 有效" if (raw or 0) > 0.05 else ("❌ 负价值" if (raw or 0) < -0.05 else "— 中性")
            print(f"{lb:<10}{b['n']:>6}{b.get('mean_p', 0) * 100:>9.1f}%{b.get('actual', 0) * 100:>9.1f}%"
                  f"{(raw or 0) * 100:>+9.1f}pt{b.get('alpha', 0) * 100:>+9.1f}pt   {verdict}")

        print("\n【2】规则组 alpha（仅统计可直接下注的正路信号）")
        print(f"{'规则组':<16}{'样本':>6}{'实际命中':>10}{'alpha':>10}{'收缩后':>10}   处理建议")
        for k, v in sorted(self.group_alpha.items(), key=lambda x: -x[1]["n"]):
            a = v.get("alpha", 0)
            raw = v.get("raw", 0) or 0
            if v["n"] < 10:
                sug = "⚠ 样本不足，降权"
            elif raw < -0.02:
                sug = "❌ 反向，剔除或反向用"
            elif a < 0.02:
                sug = "➖ 无信息量，大幅降权（噪音源）"
            elif v["n"] >= 30 and a >= 0.05:
                sug = "✅ 核心信号，加权"
            elif a >= 0.03:
                sug = "🔶 有效，保留观察"
            else:
                sug = "— 中性"
            print(f"{k:<16}{v['n']:>6}{v.get('actual', 0) * 100:>9.1f}%{v.get('raw', 0) * 100:>+9.1f}pt"
                  f"{a * 100:>+9.1f}pt   {sug}")

        print("\n【3】联赛修正（样本>=6）")
        print(f"{'联赛':<12}{'样本':>6}{'alpha':>10}{'收缩后':>10}")
        for k, v in sorted(self.league_alpha.items(), key=lambda x: -x[1]["n"])[:12]:
            print(f"{k:<12}{v['n']:>6}{v.get('raw', 0) * 100:>+9.1f}pt{v.get('alpha', 0) * 100:>+9.1f}pt")

        print("\n【4】核心结论")
        dead = [lb for lb in BIN_LABEL
                if (self.bin_alpha.get(lb, {}).get("raw") or 0) < -0.05]
        good_b = [lb for lb in BIN_LABEL
                  if (self.bin_alpha.get(lb, {}).get("raw") or 0) > 0.05]
        if dead:
            print(f"  ❌ 负价值概率区间（应停止推荐）: {', '.join(dead)}")
        if good_b:
            print(f"  ✅ 有效概率区间（应重点推荐）: {', '.join(good_b)}")
        bad_g = [k for k, v in self.group_alpha.items()
                 if v["n"] >= 10 and v.get("raw", 0) < 0]
        if bad_g:
            print(f"  ❌ 拖累型规则组: {', '.join(bad_g)}")
        print("  注：竞彩返奖率约 65%~73%，平均抽水近 30%，"
              "仅当 alpha 足够大且赔率合适时才可能形成正 EV。")

    # ---------- 自动调参 ----------
    def tune(self):
        """把学到的 alpha 写回 rules_config.json 的权重与样本统计"""
        if not os.path.exists(RULES):
            print("缺少 rules_config.json")
            return
        cfg = json.load(open(RULES, encoding="utf-8"))
        changed = []
        for k in cfg:
            g = self.group_alpha.get(k)
            if not g:
                continue
            old_base = cfg[k].get("base")
            # base 阈值按 alpha 调整：alpha 为正 -> 适度上调权重；为负 -> 下调
            a = g.get("alpha", 0.0)
            factor = 1.0 + max(min(a * 2.0, 0.5), -0.5)   # ±50% 封顶
            if isinstance(old_base, (int, float)):
                new_base = round(old_base * factor, 2)
                if abs(new_base - old_base) > 0.01:
                    cfg[k]["base"] = new_base
                    cfg[k]["threshold"] = round(new_base * 0.95, 2)
                    changed.append(f"{k}: base {old_base} -> {new_base} (alpha{a * 100:+.1f}pt)")
            cfg[k]["samples"] = g["n"]
            cfg[k]["hitRate"] = round(g.get("actual", 0) * 100, 1) if g.get("actual") is not None else None
            cfg[k]["alpha"] = round(a * 100, 2)
        with open(RULES, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        print("规则权重已校准:")
        for c in changed:
            print("  ", c)
        if not changed:
            print("  （无可调整项）")

        model = {
            "updatedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "samples": getattr(self, "n_samples", 0),
            "baseHit": round(self.base_hit, 4),
            "binAlpha": self.bin_alpha,
            "groupAlpha": self.group_alpha,
            "leagueAlpha": self.league_alpha,
        }
        with open(MODEL, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=1)
        print(f"模型已保存: {MODEL}")

    # ---------- 当日预测 ----------
    def predict_today(self):
        if not os.path.exists(MATCHES):
            print("缺少 matches.json")
            return
        data = json.load(open(MATCHES, encoding="utf-8"))
        ms = data.get("matches", [])
        print(f"当日 {len(ms)} 场 EV 评估：\n")
        print(f"{'编号':<9}{'联赛':<7}{'对阵':<22}{'方向':<5}{'赔率':>6}{'官方':>7}{'校准':>7}{'EV':>9}  建议")
        rows = []
        for m in ms:
            r = self.predict(m)
            if not r:
                continue
            name = f"{m.get('home', '')}vs{m.get('away', '')}"[:20]
            dn = {"h": "主胜", "d": "平局", "a": "客胜"}.get(r["dir"], r["dir"])
            print(f"{m.get('num', ''):<9}{m.get('league', '')[:6]:<7}{name:<22}{dn:<5}"
                  f"{r['odds']:>6.2f}{r['p_off'] * 100:>6.0f}%{r['p_cal'] * 100:>6.0f}%"
                  f"{r['ev'] * 100:>+8.1f}%  {r['advice']}")
            rows.append({"num": m.get("num"), "match": name, **r})
        bettable = [x for x in rows if x.get("type") == "good"]
        print(f"\n其中可直接下注的正路信号: {len(bettable)} 场 "
              f"（反指/警示 {len(rows) - len(bettable)} 场，仅用于排除）")
        bettable.sort(key=lambda x: -x["ev"])
        if bettable:
            print("\n正路推荐按 EV 排序:")
            for x in bettable:
                print(f"  {x['num']:<9}{x['match']:<22} 官方{x['p_off'] * 100:>3.0f}% → "
                      f"校准{x['p_cal'] * 100:>3.0f}%  赔率{x['odds']:.2f}  "
                      f"EV{x['ev'] * 100:>+6.1f}%  {x['advice']}")
        else:
            print("  今日无正路推荐信号")
        pos = [x for x in bettable if x["ev"] > 0]
        print(f"\n正 EV 场次: {len(pos)} / {len(bettable)}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    eng = SmartEngine()
    if not eng.records:
        print("没有历史数据，请先运行 fetch_daily.py 或 extract_history.py 恢复历史")
        return
    if cmd == "report":
        eng.report()
    elif cmd == "tune":
        eng.tune()
    elif cmd == "predict":
        eng.predict_today()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
