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

log "==> 步骤1 抓取数据 (fetch_daily.py)"
"$PY" fetch_daily.py

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

log "==> 步骤2 构建页面 (build_panel.py)"
"$PY" build_panel.py

if [ "$MODE" = "deploy" ]; then
  # 不再因缺凭据而 exit：deploy_cf.py 内部会降级为「只生成 dist/ 离线包」并打印引导，
  # 保证无人值守的定时任务不会因此整体失败。
  log "==> 步骤3 部署 (deploy_cf.py)"
  set +e
  "$PY" deploy_cf.py
  DEPLOY_RC=$?
  set -e
  if [ "$DEPLOY_RC" -ne 0 ]; then
    log "!! 部署未成功（exit=$DEPLOY_RC）。离线包已保留在 dist/，可手动上传："
    log "   npx wrangler pages deploy dist --project-name jc-football"
  fi
fi

log "==> 完成"

if [ "$MODE" != "check" ]; then
  echo ""
  echo "提示：本次若有功能/修复改动，记得写进主页「通知」的更新日志，方便核对版本："
  echo "    ./run_daily.sh note --title \"一句话标题\" --feature \"新增了什么\" --fix \"修了什么\""
  echo "（只更新数据、无代码改动时不需要执行）"
fi
