#!/usr/bin/env bash
# 竞彩足球数据分析台 — 本地自动刷新循环（v1.7.93）
#
# 背景：旧版「重新抓取」按钮触发 GitHub Actions，但①本地新代码未上 GitHub，
# CI 部署的是旧版本；②GitHub Actions 在海外访问不了中国竞彩接口(HTTP 567)，
# 所谓"全站重新抓取"连核心数据都抓不回。因此改为：
#   数据流水线在本地(中国网络)运行 → 上传到 Cloudflare Pages → 网页端
#   「刷新数据」按钮运行时拉取 /<name>.json 即时刷新。
# 本脚本就是那个"本地持续运行、定期刷新并上传"的进程。
#
# 配额保护：Cloudflare Pages 免费层 500 次构建/月。按 90 分钟一轮部署，
# 约 16 次/天 ≈ 480/月，稳守免费线内；同时保证数据 ≤90 分钟上站。
set -u
cd "$(dirname "$0")"
export TZ=Asia/Shanghai
LOG=/tmp/refresh_loop.log
INTERVAL=5400   # 90 分钟

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

while true; do
  log "==> 步骤1 抓取+构建数据 (run_daily.sh)"
  DAILY_BUDGET=600 bash run_daily.sh >> "$LOG" 2>&1 || log "!! run_daily.sh 非零退出（详见日志，沿用已有数据）"
  log "==> 步骤2 部署到 Cloudflare Pages (deploy_cf.py)"
  python3 deploy_cf.py >> "$LOG" 2>&1 || log "!! deploy_cf.py 失败"
  log "==> 本轮完成，休眠 ${INTERVAL}s 后下一轮"
  sleep "$INTERVAL"
done
