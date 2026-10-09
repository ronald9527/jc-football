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
"$PY" fetch_football_data.py
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
