#!/usr/bin/env bash
# 竞彩足球数据分析台 - 一键日常更新
# 用法:
#   ./run_daily.sh            抓取 + 构建（不部署）
#   ./run_daily.sh deploy     抓取 + 构建 + 部署到 Cloudflare Pages
#   ./run_daily.sh check      仅执行 6 维度检查修复 + 重建
#   ./run_daily.sh selfcheck  数据自检（有问题退出码非 0，便于 cron / CI 告警）
#   ./run_daily.sh note       记录一次改动到主页「通知」更新日志（会调 bump_version.py）
set -euo pipefail

cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
MODE="${1:-build}"

log() { echo "[$(date '+%F %T')] $*"; }

# ---------- 自检：只体检，不改数据 ----------
if [ "$MODE" = "selfcheck" ]; then
  log "==> 数据自检 (selftest.py)"
  set +e
  "$PY" selftest.py
  RC=$?
  set -e
  if [ "$RC" -ne 0 ]; then
    log "!! 自检发现问题（exit=$RC），详见 selftest_report.json"
    exit "$RC"
  fi
  log "==> 自检通过"
  exit 0
fi

# ---------- 记更新日志 ----------
if [ "$MODE" = "note" ]; then
  log "==> 记录本次改动到主页「通知」更新日志 (bump_version.py)"
  shift || true
  "$PY" bump_version.py "$@"
  log "==> 完成（日志已写入 template.html 并自动重建 index.html）"
  exit 0
fi

# ---------- 第 0 步：历史数据对账（必须在抓取之前！）----------
# 沙箱若被重置，本地 history.json 会丢失；若直接抓取并部署，
# 会把 Pages 上完好的历史副本一起覆盖掉，造成不可恢复的损失。
# 因此先比对本地与线上记录数，本地残缺时从线上拉回。
# ---------- 时间预算：平台定时任务有超时上限（当前 600s）----------
# 若抓取步骤把时间耗光，最关键的「构建+部署」会被超时掐断，线上永远停在旧版本。
# 因此给每个非核心步骤设预算门槛：剩余时间不足就跳过（用上一轮缓存数据），
# 确保部署步骤一定有机会执行。
START_TS=$(date +%s)
BUDGET=${DAILY_BUDGET:-480}          # 抓取阶段总预算（秒），留 ~2 分钟给构建+部署
elapsed(){ echo $(( $(date +%s) - START_TS )); }
have_time(){ local need=$1; local left=$(( BUDGET - $(elapsed) )); [ "$left" -ge "$need" ]; }

if [ "$MODE" != "selfcheck" ] && [ "$MODE" != "note" ]; then
  log "==> 步骤0 历史数据对账 (ensure_history.py)"
  set +e
  "$PY" ensure_history.py
  set -e
fi

log "==> 步骤1 抓取数据 (fetch_daily.py)"
# 容错：定时任务沙箱里竞彩/500.com 偶发网络抖动会让 fetch_daily.py 抛异常，
# 若直接非零退出会被平台判为「任务失败」并触发告警/邮件。这里改为：
# 抓取失败时恢复上次正常数据、本次跳过更新（exit 0），依赖每 30 分钟的周期自愈重试。
cp -f matches.json matches.json.bak_run 2>/dev/null || true
set +e
"$PY" fetch_daily.py
FETCH_RC=$?
set -e
if [ "$FETCH_RC" -ne 0 ]; then
  log "!! 抓取异常（exit=$FETCH_RC），恢复上次正常数据，本次跳过更新（下个周期自动重试）"
  mv -f matches.json.bak_run matches.json 2>/dev/null || true
  log "==> 完成（数据未变更）"
  exit 0
fi
rm -f matches.json.bak_run

# ---------- 每日自检（抓取后立即做，问题写进报告）----------
log "==> 步骤1.2 数据自检 (selftest.py)"
set +e
"$PY" selftest.py
SELF_RC=$?
set -e
if [ "$SELF_RC" -ne 0 ]; then
  log "!! 自检发现问题（exit=$SELF_RC），自动执行修复流程"
  "$PY" check_and_fix.py
  "$PY" selftest.py || log "!! 修复后仍有问题，详见 selftest_report.json"
fi

if [ "$MODE" = "check" ]; then
  log "==> 步骤1.5 检查修复 (check_and_fix.py)"
  "$PY" check_and_fix.py
fi

# ---------- 步骤1.8：football-data.org 官方赛果核对（独立源，绝不参与模型）----------
log "==> 步骤1.8 football-data.org 官方赛果 (fetch_football_data.py)"
set +e
if have_time 120; then
  "$PY" fetch_football_data.py
else
  log "!! 剩余时间不足，跳过官方赛果抓取（沿用上次数据），优先保证部署"
fi
set -e

# ---------- 步骤1.85：独立「比分预测」Tab（football-data.org + 自建 Poisson，零耦合核心模型）----------
# v1.7.44 修复：fd_config.json 被 .gitignore 排除（不入 git，防密钥泄露），任务沙箱从 git
# 初始化时无此文件 → fetch_scores.py 缺 key → 走「保留旧数据」分支，把 score_predict.json 的
# updatedAt 冻结回上一轮，导致「比分预测」Tab 静默停更（v1.7.39 同款坑）。自愈：缺 key 但
# 环境变量 FD_API_KEY 已配置则写入 fd_config.json；都没有则打印明确告警便于排查。
_cfg_ok=$(python3 - <<'PY' 2>/dev/null
import json, os
try:
    d = json.load(open("fd_config.json"))
    print("" if d.get("api_key", "").strip() else "MISSING")
except Exception:
    print("MISSING")
PY
)
if [ -z "$_cfg_ok" ]; then
  log "   比分预测密钥就绪 (fd_config.json)"
else
  if [ -n "$FD_API_KEY" ]; then
    python3 - <<PY
import json, os
json.dump({"api_key": os.environ["FD_API_KEY"], "enabled": True},
          open("fd_config.json", "w"), ensure_ascii=False, indent=2)
PY
    log "   ✅ 已从环境变量 FD_API_KEY 恢复 fd_config.json（密钥自愈，比分预测可正常更新）"
  else
    log "   ⚠️ 警告：fd_config.json 缺密钥且无 FD_API_KEY 环境变量，『比分预测』将暂停更新（沿用旧数据）"
  fi
fi
log "==> 步骤1.85 比分预测 (fetch_scores.py)"
# 球队详情每天只需刷新一次（全量刷新曾耗时 16 分钟，是流水线的头号超时元凶）
TEAM_FLAG=.last_team_refresh
if [ "$(cat $TEAM_FLAG 2>/dev/null)" = "$(date +%F)" ]; then
  log "   球队缓存今日已刷新，本轮跳过球队抓取（--keep-cache）"
  SCORES_ARG="--keep-cache"
else
  SCORES_ARG=""
fi
set +e
if have_time 150; then
  "$PY" fetch_scores.py $SCORES_ARG --max-teams 25
  [ -z "$SCORES_ARG" ] && date +%F > $TEAM_FLAG
else
  log "!! 剩余时间不足，跳过比分预测抓取（沿用上次数据），优先保证部署"
fi
set -e

log "==> 步骤1.88 WhoScored 人工预测比分 (fetch_whoscored.py)"
set +e
if have_time 120; then
  "$PY" fetch_whoscored.py
else
  log "!! 剩余时间不足，跳过 WhoScored 抓取（沿用上次数据），优先保证部署"
fi
set -e

# ---------- 步骤1.89：竞彩篮球（独立源，与足球数据/模型零耦合，v1.7.29）----------
log "==> 步骤1.89 竞彩篮球 (fetch_basketball.py)"
# 隔离设计：篮球抓取失败只跳过篮球（保留旧数据），绝不影响足球与部署；
# 与足球共用 have_time 门槛，超预算同样跳过，保证部署步骤一定被执行。
set +e
if have_time 90; then
  "$PY" fetch_basketball.py
else
  log "!! 剩余时间不足，跳过篮球抓取（沿用上次数据），优先保证部署"
fi
set -e

# ---------- 步骤1.90：篮球预测/串关/统计（v1.7.44，独立源零耦合）----------
# 隔离设计同 1.89：篮球 AI 失败只跳过篮球预测，绝不影响足球与部署。
# fetch_basketball_ai.py 产出 basketball_ai.json（四玩法预测）+ basketball_stats.json
# （联赛历史统计）；fetch_basketball_parlay.py 产出 basketball_parlay_history.json（串关）。
# 三者均依赖 1.89 的 basketball.json / basketball_history.json，故放在其后。
set +e
if have_time 90; then
  log "==> 步骤1.90a 篮球预测与统计 (fetch_basketball_ai.py)"
  "$PY" fetch_basketball_ai.py
  if [ $? -ne 0 ]; then
    log "!! 篮球预测/统计生成失败，沿用上次（不影响足球与部署）"
  fi
  log "==> 步骤1.90b 篮球串关 (fetch_basketball_parlay.py)"
  "$PY" fetch_basketball_parlay.py
  if [ $? -ne 0 ]; then
    log "!! 篮球串关生成失败，沿用上次（不影响足球与部署）"
  fi
else
  log "!! 剩余时间不足，跳过篮球预测/串关/统计（沿用上次数据），优先保证部署"
fi
set -e

log "==> 步骤2 构建页面 (build_panel.py)"
# 容错：构建脚本偶发异常不应让定时任务被判失败（否则触发告警/邮件）。
# 抓取步骤已恢复数据，构建失败可沿用上次 index.html，下个周期重试。
set +e
"$PY" build_panel.py
BUILD_RC=$?
set -e
if [ "$BUILD_RC" -ne 0 ]; then
  log "!! 构建异常（exit=$BUILD_RC），沿用上次 index.html（下个周期自动重试）"
fi

if [ "$MODE" = "deploy" ]; then
  # ---------- 部署节流：Cloudflare Pages 免费层仅 500 次构建/月 ----------
  # 自动任务 30 次/天 ≈ 900 次/月会超限（约第 17 天起新部署被拒，站点停在旧版本）。
  # 策略：① 核心数据无变化则跳过部署；② 两次部署至少间隔 90 分钟（上限 16 次/天≈480/月，留余量）。
  # 这样既保证赛果/赔率变化能在 90 分钟内上站，又把构建额度稳稳压在免费线内。
  NOW_TS=$(date +%s)
  LAST_TS=$(cat .last_deploy.ts 2>/dev/null || echo 0)
  GAP=$(( NOW_TS - LAST_TS ))
  NEW_SHA=$(python3.11 - <<'PY' || echo ""
import json, hashlib
def norm(p, *keep):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return b""
    if keep:
        d = {k: d.get(k) for k in keep if k in d}
    return json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")
def filehash(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).digest()
    except Exception:
        return b""
parts = norm("matches.json", "matches", "highOddsParlay", "safeParlay", "parlayHistory", "strongWeak")
parts += norm("history.json", "records")
parts += norm("fd_data.json", "matches", "byNum")
parts += norm("score_predict.json", "matches", "reliable", "accuracy")
parts += norm("whoscored_data.json", "matches", "dateList", "history", "reliable")
# 关键：代码/模板变更也要触发部署，否则「只改前端逻辑」会被误判为数据无变化而跳过上线
parts += filehash("template.html")
parts += filehash("fetch_daily.py")
parts += filehash("build_panel.py")
print(hashlib.sha256(parts).hexdigest())
PY
)
  OLD_SHA=$(cat .last_deploy.sha 2>/dev/null || echo "")
  DATA_CHANGED=$([ "$NEW_SHA" != "$OLD_SHA" ] && echo 1 || echo 0)
  SHOULD_DEPLOY=0
  if [ "$DATA_CHANGED" = 1 ] && [ "$GAP" -ge 1800 ]; then SHOULD_DEPLOY=1; fi
  if [ "$GAP" -ge 5400 ]; then SHOULD_DEPLOY=1; fi
  if [ "$SHOULD_DEPLOY" = 1 ]; then
    log "==> 步骤3 部署 (deploy_cf.py)$( [ "$DATA_CHANGED" = 1 ] && echo ' [数据有变化]' || echo ' [定时刷新]' )"
    set +e
    "$PY" deploy_cf.py
    DEPLOY_RC=$?
    set -e
    if [ "$DEPLOY_RC" -ne 0 ]; then
      log "!! 部署未成功（exit=$DEPLOY_RC）。离线包已保留在 dist/，可手动上传："
      log "   npx wrangler pages deploy dist --project-name jc-football"
    else
      echo "$NOW_TS" > .last_deploy.ts
      echo "$NEW_SHA" > .last_deploy.sha
      log "==> 已部署并记录快照（距上次 ${GAP}s）"
    fi
  else
    log "==> 步骤3 跳过部署：数据无变化且距上次部署 ${GAP}s < 下限（已节省 Cloudflare 构建额度）"
  fi
fi

log "==> 完成"

if [ "$MODE" != "check" ]; then
  echo ""
  echo "提示：本次若有功能/修复改动，记得写进主页「通知」的更新日志，方便核对版本："
  echo "    ./run_daily.sh note --title \"一句话标题\" --feature \"新增了什么\" --fix \"修了什么\""
  echo "（只更新数据、无代码改动时不需要执行）"
fi
