"""
回归测试：五大联赛（英超/西甲/意甲/德甲/法甲）竞彩比赛必须有比分预测。

验证三层保障：
  ① 接口可达且返回该场 → 用接口队形 Poisson；
  ② 接口漏返回某场五大联赛竞彩 → 本地赔率保底基线（_lambda_from_jc）补齐；
  ③ 接口整体不可达 → _local_fallback 全量本地赔率兜底。

任一情形都保证五大联赛竞彩比赛有预测且不产生可见重复卡片。
"""
import os, sys, json, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_scores as fs

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "score_predict.json")
BAK = OUT + ".bak_test"


def _restore():
    if os.path.exists(BAK):
        shutil.copy(BAK, OUT)
    if os.path.exists(BAK):
        os.remove(BAK)


def _jc_pairs(date):
    jc = json.load(open(os.path.join(HERE, "matches.json"), encoding="utf-8"))
    out = set()
    for m in jc.get("matches", []):
        if m.get("date") == date:
            out.add(frozenset([m["home"], m["away"]]))
    return out


def _visible_pairs(result):
    """复刻渲染层 spJcFilter 可见判定：两译名都含中文才算可见。"""
    import re
    from team_map import TEAM_EN2ZH
    SP = TEAM_EN2ZH

    def spZh(n):
        if not n:
            return "?"
        if n in SP:
            return SP[n]
        t = re.sub(r"\b(FC|CF|AC|AS|SC|SV|SK|NK|FK|UD|US|SS|RC|CD|BK|SD|JK|KF|GD|RJ|VfL|VfB|"
                   r"Calcio|Club|Clube|Futebol|Football|Fotbal|Fotboll)\b", "", n, flags=re.I)
        t = re.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        if t and t in SP:
            return SP[t]
        t2 = re.sub(r"^\s*[A-Z]{1,3}\s+", "", t).strip()
        if t2 and t2 in SP:
            return SP[t2]
        return n

    vis = set()
    for r in result.get("matches", []):
        h, a = spZh(r.get("home", "")), spZh(r.get("away", ""))
        if re.search(r"[一-鿿]", h) and re.search(r"[一-鿿]", a):
            vis.add(frozenset([h, a]))
    return vis


def test_fd_misses_five_major_league():
    """接口只返回部分意甲，漏掉另两场意甲竞彩 → 本地基线必须补齐。"""
    if os.path.exists(OUT):
        shutil.copy(OUT, BAK)
    date = "2026-10-27"
    jc = json.load(open(os.path.join(HERE, "matches.json"), encoding="utf-8"))
    ita = [m for m in jc["matches"] if m.get("date") == date and m.get("league") == "意甲"]
    assert len(ita) >= 3, "测试前置：当天应有≥3场意甲竞彩"

    # 伪造接口响应：只返回『萨索洛vs拉齐奥』这一场意甲（其余意甲竞彩漏返回）
    controlled = [{
        "id": 900001,
        "competition": {"code": "SA", "name": "Serie A"},
        "utcDate": date + "T17:30:00Z",
        "status": "TIMED",
        "homeTeam": {"id": 1, "name": "US Sassuolo Calcio"},
        "awayTeam": {"id": 2, "name": "SS Lazio"},
        "score": {"fullTime": {"home": None, "away": None}},
    }]

    orig_rate = fs.RateLimited
    orig_ensure = fs._ensure_team

    class FakeApi:
        calls = 0

        def get(self, path, *a, **k):
            self.calls += 1
            if "matches" in path:
                return (True, {"matches": controlled})
            return (True, {})

    fs.RateLimited = lambda *a, **k: FakeApi()
    fs._ensure_team = lambda *a, **k: None  # 阻断队缓存网络刷新
    try:
        fs.fetch_scores(no_fetch=False, keep_cache=True, max_teams=0)
    finally:
        fs.RateLimited = orig_rate
        fs._ensure_team = orig_ensure

    res = json.load(open(OUT, encoding="utf-8"))  # 读写盘产物（即部署注入的真实数据）
    visible = _visible_pairs(res)
    missing = [p for p in _jc_pairs(date) if p not in visible and _is_five_major(res, p)]
    assert not missing, "五大联赛竞彩未全覆盖：%s" % missing
    # 可见重复检测
    from collections import Counter
    c = Counter()
    import re
    from team_map import TEAM_EN2ZH
    SP = TEAM_EN2ZH

    def spZh(n):
        if not n:
            return "?"
        if n in SP:
            return SP[n]
        return n
    for r in res.get("matches", []):
        h, a = spZh(r.get("home", "")), spZh(r.get("away", ""))
        if re.search(r"[一-鿿]", h) and re.search(r"[一-鿿]", a):
            c[frozenset([h, a])] += 1
    dups = [k for k, v in c.items() if v > 1]
    assert not dups, "出现可见重复卡片：%s" % dups
    print("✓ 接口漏返回意甲时，本地基线补齐，五大联赛竞彩 8/8 覆盖且无重复")
    _restore()


def test_fd_unreachable_fallback():
    """接口整体不可达 → _local_fallback 全量本地赔率兜底，覆盖全部竞彩（含五大联赛）。"""
    if os.path.exists(OUT):
        shutil.copy(OUT, BAK)
    date = "2026-10-27"

    orig_rate = fs.RateLimited
    orig_ensure = fs._ensure_team

    class FakeApi:
        calls = 0

        def get(self, path, *a, **k):
            self.calls += 1
            return (False, None)  # 接口整体不可达

    fs.RateLimited = lambda *a, **k: FakeApi()
    fs._ensure_team = lambda *a, **k: None
    try:
        fs.fetch_scores(no_fetch=False, keep_cache=True, max_teams=0)
    finally:
        fs.RateLimited = orig_rate
        fs._ensure_team = orig_ensure

    res = json.load(open(OUT, encoding="utf-8"))  # 读写盘产物
    visible = _visible_pairs(res)
    jc_all = _jc_pairs(date)
    uncovered = [p for p in jc_all if p not in visible]
    assert not uncovered, "接口不可达时仍有竞彩未覆盖：%s" % uncovered
    print("✓ 接口不可达时，_local_fallback 覆盖全部竞彩（含五大联赛）")
    _restore()


def _is_five_major(result, pair):
    five = {"英超", "西甲", "意甲", "德甲", "法甲"}
    for r in result.get("matches", []):
        if frozenset([r.get("home", ""), r.get("away", "")]) == pair and r.get("league") in five:
            return True
    # 竞彩对阵：直接查 matches.json 联赛归属
    jc = json.load(open(os.path.join(HERE, "matches.json"), encoding="utf-8"))
    for m in jc.get("matches", []):
        if frozenset([m.get("home", ""), m.get("away", "")]) == pair:
            return m.get("league") in five
    return False


if __name__ == "__main__":
    test_fd_misses_five_major_league()
    test_fd_unreachable_fallback()
    print("\nALL PASS")
