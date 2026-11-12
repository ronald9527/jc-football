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

# 强制北京时间：CI 容器为 UTC，会让数据时间戳/日期判定慢 8 小时（v1.7.79 修复）
export TZ=Asia/Shanghai

log() { echo "[$(date '+%F %T')] $*"; }

# v1.7.132：注入权威北京时间，杜绝沙箱休眠/恢复后系统时钟漂移导致
# 「竞彩当天批次判错一天」。实测 2026-11-12 沙箱时钟比 Cloudflare HTTP Date
# 慢 6.5h，DAY 判错 → 主批次永远展示前一天的赛程、已开赛场次被当成未开赛。
# 整个流水线共享同一个 JC_BEIJING_NOW，避免各脚本各自取时间跨零点不一致。
_BJ_INFO="$("$PY" -c 'import beijing_clock as b; print(b.now().strftime("%Y-%m-%d %H:%M:%S") + "|" + b.source())' 2>/dev/null || true)"
_BJ_NOW="${_BJ_INFO%%|*}"
_BJ_SRC="${_BJ_INFO##*|}"
if [ -n "$_BJ_NOW" ]; then
  export JC_BEIJING_NOW="$_BJ_NOW"
  log "权威北京时间 $JC_BEIJING_NOW（来源 $_BJ_SRC），本地时钟 $(date '+%F %T')"
else
  log "!! 权威时间校准不可用，沿用本地时钟（selftest 会提示）"
fi

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
# v1.7.129：给核心抓取加硬墙钟（双保险）。线上 CI 曾因海外官方接口 HTTP 567
# 让「赔率历史 + 赛果回填」逐场/逐条 30s 超时累加，fetch_daily.py 单独一步就
# 烧满 90 分钟被 job timeout 强杀，后续构建+部署永远执行不到。
# fetch_daily.py 内部已有 _OFFICIAL_DOWN 熔断 + JC_FETCH_WALL_SEC 网络墙钟，
# 这里再用 timeout 包一层，防止任何未预见的网络慢点拖垮整条流水线。
# 本地默认 600s（中国网络官方接口通、有缓存，正常 1-3 分钟完成），CI 由
# JC_FETCH_TIMEOUT 收紧；超时即恢复备份快照，绝不整体中断其余数据源。
FETCH_WALL="${JC_FETCH_TIMEOUT:-600}"
timeout "$FETCH_WALL" "$PY" fetch_daily.py
FETCH_RC=$?
set -e
if [ "$FETCH_RC" -ne 0 ]; then
  log "!! 抓取异常（exit=$FETCH_RC），恢复上次正常数据"
  mv -f matches.json.bak_run matches.json 2>/dev/null || true
  # 竞彩数据本次沿用上次正常快照，但**不再整体退出**。
  # 原因：海外 CI（GitHub Actions）访问中国竞彩接口 webapi.sporttery.cn 会被拒
  # （HTTP 567；本地中国网络同接口 HTTP 200）。若在此直接 exit 0，
  # WhoScored 预测比分 / 篮球 / 历史回溯等国际数据源会一并停更。
  # 改为「能更新的都更新」，后续步骤照常执行并部署。
  JC_FETCH_FAILED=1
  log "   [诊断] 竞彩与备用源可达性探测（判断是否海外 IP 封锁）："
  for _u in "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry" \
           "https://trade.500.com/jczq/" "https://liansai.500.com/"; do
    _code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 -A "Mozilla/5.0" "$_u" 2>/dev/null || echo "ERR")
    log "     $_u -> HTTP $_code"
  done
  log "==> 竞彩数据本次未更新（沿用上次成功快照），继续更新其余数据源"
fi
rm -f matches.json.bak_run

# ---------- 每日自检：已前移到全部数据生成 + 页面构建之后（步骤2.6）----------
# 旧位置（步骤1.2）在篮球 / 比分预测 / 正路统计尚未生成时运行，会稳定误报
# 「篮球数据停更」「比分预测停更」「缺少 goodRateStats」3 条 ERROR，
# 并误触发 check_and_fix.py 修复流程。2026-11-07 全量刷新实测：
# 同一批数据最终自检 ERROR 0 / WARN 0，确认纯属顺序问题，非数据故障。
# 现在由步骤2.6 在上线前做真正的门禁校验。

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
# v1.7.45 修复：fd_config.json 被 .gitignore 排除（不入 git，防密钥泄露），任务沙箱从 git
# 初始化时无此文件 → fetch_scores.py 缺 key。v1.7.45 起缺 key 不再「冻结旧数据」，
# 而是改用竞彩 matches.json 的赔率隐含期望进球（hadProb/goalsExp）做 Poisson 兜底预测，
# 数据每日随赛程刷新，『比分预测』Tab 始终新鲜（覆盖为竞彩赛事）。若 CI 配置了 FD_API_KEY
# 环境变量（或 cf_config.fd_api_key），则优先用 football-data.org 全球联赛覆盖。自愈逻辑如下。
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
    _cf_key=$(python3 - <<'PY' 2>/dev/null
import json
try:
    print(json.load(open("cf_config.json")).get("fd_api_key", "") or "")
except Exception:
    print("")
PY
)
    if [ -n "$_cf_key" ]; then
      python3 - <<PY
import json
k = json.load(open("cf_config.json")).get("fd_api_key", "")
json.dump({"api_key": k, "enabled": True},
          open("fd_config.json", "w"), ensure_ascii=False, indent=2)
PY
      log "   ✅ 已从 cf_config.json.fd_api_key 恢复 fd_config.json（密钥自愈，比分预测可正常更新）"
    else
      log "   ℹ️ 无 football-data Key：『比分预测』改用竞彩赔率兜底预测（每日随赛程刷新）；如需全球联赛覆盖，请在 CI 配置 FD_API_KEY"
    fi
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

# ---------- 步骤1.852：比分预测赛果回填（500体育 + 规范化，v1.7.113）----------
# 痛点：fetch_scores.py 从竞彩档案补全历史场次时把 scoreActual("1:2") 同时塞进
# actH/actA、status 留空 → 页面只在 status=FINISHED 且 actH 为整数时认赛果，
# 于是已完赛场次显示「未开赛」、日期分组「已结束 0」；且竞彩官方开奖接口封锁
# 海外 IP（HTTP 567），近几日 result 迟迟不回填。500体育 trade.500.com 海外可达，
# 其竞彩历史页服务端渲染带比分，用它补齐近几日赛果。
# 顺序约束：必须在 1.85 fetch_scores（每轮重写 matches）之后、步骤2 build_panel
# （读 score_predict.json）之前。build_panel 内另有 normalize_sp 内存兜底，
# 保证步骤2.5 陈旧豁免重跑 fetch_scores 后页面依旧正确。
# 隔离设计：纯增量幂等零噪声、抓网失败静默降级、失败只记日志绝不阻断部署。
log "==> 步骤1.852 比分预测赛果回填 (sp_results.py)"
set +e
if have_time 40; then
  "$PY" sp_results.py --quiet
  _sp_rc=$?
  if [ "$_sp_rc" -ne 0 ]; then
    log "!! 赛果回填失败（exit=$_sp_rc），沿用上次数据（不影响部署）"
  fi
else
  log "!! 剩余时间不足，跳过赛果回填（沿用上次数据），优先保证部署"
fi
set -e

log "==> 步骤1.88 WhoScored 人工预测比分 (fetch_whoscored.py)"
set +e
if have_time 120; then
  # v1.7.61：按剩余流水线时间传预算（留 40s 给后续构建部署缓冲），竞彩在售
  # 场次的源站预测比分必须在开球前抓到——预算不足会导致「未推荐」假象。
  _ws_left=$(( BUDGET - $(elapsed) - 40 ))
  [ "$_ws_left" -lt 110 ] && _ws_left=110
  "$PY" fetch_whoscored.py --budget "$_ws_left"
else
  log "!! 剩余时间不足，跳过 WhoScored 抓取（沿用上次数据），优先保证部署"
fi
set -e

# ---------- 步骤1.882：比分预测赛后结果全量离线回溯（v1.7.112）----------
# 隔离设计：纯本地 JSON 对账，零外网、零抓取预算占用（实测 <1s）。
# 痛点：海外 CI 抓 WhoScored /livescores 常被软限流，赛果回填静默饿死 →
# whoscored_history.json 的 pending 不断堆积（曾堆积 173 条），赛后回溯区缺记录。
# 本步骤用本地权威赛果源（竞彩 history.json 官方赛果 + 两套预测历史 +
# score_predict.matches + fd_data）自动补漏，并清理「已评估残留」僵尸 pending。
# 顺序约束（改代码前必读）：
#   · 必须在 1.88 fetch_whoscored 之后 —— fetch 先更新 records/pending/源站数据；
#   · 必须在 1.887 accuracy_ab（A/B 对比，读 whoscored_history.records）之前；
#   · 必须在 步骤2 build_panel（读 whoscored_data.json）之前。
# whoscored_data.json 的 history 每轮由 records 重建（eva=records.values()），
# 故回填进 records 的记录不会被覆盖；两个产物 json 均在 daily.yml 回写 FILES 内，
# 且部署指纹含 whoscored_data 的 history/reliable，补到新赛果会自动落库并上线。
# 幂等零噪声：无新增/无清理/派生字段无变化时不写盘，不制造空数据快照提交。
# 失败只记日志，绝不阻断部署（沿用项目一贯的隔离设计）。
log "==> 步骤1.882 比分预测赛后回溯 (backfill_ws_results.py)"
set +e
if have_time 15; then
  "$PY" backfill_ws_results.py --quiet
  _bf_rc=$?
  if [ "$_bf_rc" -ne 0 ]; then
    log "!! 赛后回溯失败（exit=$_bf_rc），沿用上次数据（不影响足球与部署）"
  fi
else
  log "!! 剩余时间不足，跳过赛后回溯（沿用上次数据），优先保证部署"
fi
set -e

# ---------- 步骤1.886：V站·呼死狗 中文情报（独立源，v1.7.75；零翻译、竞彩编号直配）----------
# 隔离设计：V站抓取失败只跳过本源（保留旧 vzhan_data.json），绝不影响 WhoScored/模型/部署；
# 与 WhoScored 共用 have_time 门槛，超预算同样跳过，保证部署步骤一定被执行。
log "==> 步骤1.886 V站·呼死狗情报 (fetch_vzhan.py)"
# v1.7.118：不再卡 have_time 预算门槛。此前 V站常被「剩余时间不足」跳过，
# 而接口实测 0.1s 可达、全流程约 20s，是用户明确要求每天必更的情报源；
# 失败时脚本自身保留旧数据（不覆盖为空），这里再加一层隔离即可。
set +e
"$PY" fetch_vzhan.py || log "!! V站 抓取失败（保留上次数据），继续其余模块"
set -e

# v1.7.133：V站 抓取完成后，把最新 V站 预测重合并进 score_predict.json。
# fetch_scores(1.85) 本轮早于 fetch_vzhan(1.886)，用的是上一轮 V站 数据；
# 不重合并会让 vzhanMerged.vzTotal 与当前 vzhan_data.json 对不上，
# 自检误报「重复匹配」，页面也可能显示已过期的 V站 比分。
log "==> 步骤1.8862 比分预测 V站 一等字段重合并 (fetch_scores.py --merge-vzhan-only)"
"$PY" fetch_scores.py --merge-vzhan-only || log "!! V站 重合并失败（保留本轮合并结果）"

# ---------- 步骤1.8865：V站网关稳定性探活（监控，v1.7.77；不阻断部署）----------
# 隔离设计：网关探活失败只记录 vzhan_gateway_status.json + 可选告警，
# 绝不中断流水线、绝不影响部署（静默停更是最隐蔽的事故，故独立监控）。
log "==> 步骤1.8865 V站网关探活 (check_vzhan_gateway.py)"
set +e
if have_time 30; then
  "$PY" check_vzhan_gateway.py
  _gw_rc=$?
  if [ "$_gw_rc" -ne 0 ]; then
    log "!! V站网关探活失败（详情见上方 [网关] 行 / vzhan_gateway_status.json）；已记录，不阻断部署"
  fi
else
  log "!! 剩余时间不足，跳过 V站网关探活（沿用上次状态），优先保证部署"
fi
set -e

# ---------- 步骤1.8868：高手推荐·92玩球易红单（独立源，v1.7.118）----------
# 此前只挂在 fetch_daily.py 最末尾、且带「北京时间 08-23 点」时间门：
# 主脚本一旦中途崩溃（如 500 备用源 KeyError），此处永远不执行，
# expert_recommendations.json 停更多日。现改为独立无条件步骤 + 失败隔离。
log "==> 步骤1.8868 高手推荐·92玩球易红单 (fetch_expert.py)"
set +e
"$PY" fetch_expert.py || log "!! 高手推荐抓取失败（保留上次数据），继续其余模块"
# v1.7.118：凌晨（09:00 前）专家还没发今日文章，严格「只取当天」会稳定产出 0 篇，
# 页面每天凌晨都像模块坏了。此时自动回退抓「昨天」这一最近批次并标注 batchDate。
_EXP_N=$("$PY" -c "import json;print(len(json.load(open('expert_recommendations.json',encoding='utf-8')).get('articles',[])))" 2>/dev/null || echo 0)
if [ "$_EXP_N" = "0" ]; then
  _YDAY=$(TZ=Asia/Shanghai date -d "yesterday" +%F)
  log "   今日暂无专家发文，回退抓最近一批（$_YDAY）"
  JC_EXPERT_DATE="$_YDAY" "$PY" fetch_expert.py || log "!! 回退批次抓取失败（保留上次数据），继续其余模块"
fi
# v1.7.127：用与当天抓取同源的结构化解析器重跑历史文章原文，
# 修正理由截断/页脚噪音/比分漏提取；只补文本与依据，不改已结算的命中方向。
log "==> 步骤1.8869 高手推荐·历史文章修复 (rebuild_expert_history.py)"
set +e
"$PY" rebuild_expert_history.py || log "!! 历史文章修复失败（保留上次归档），继续其余模块"
set -e

# ---------- 步骤1.887：模型 vs WhoScored 命中率 A/B 对比（独立分析，零耦合核心数据）----------
# 把每日 matches.json(同场双预测) + 两套历史(带赛果) 按规范键合并到 ab_compare.json，
# 算各源独立命中率 + 同场头对头(配对符号检验)。纯分析产物，不入部署指纹，不挤占部署预算。
log "==> 步骤1.887 模型/WhoScored 命中率 A/B 对比 (accuracy_ab.py)"
if have_time 30; then
  "$PY" accuracy_ab.py
else
  log "!! 剩余时间不足，跳过 A/B 对比更新（沿用上次统计）"
fi
set -e

# ---------- 步骤1.89：竞彩篮球（独立源，与足球数据/模型零耦合，v1.7.29）----------
log "==> 步骤1.89 竞彩篮球 (fetch_basketball.py 双源 + 赛果回溯)"
# 隔离设计：篮球抓取失败只跳过篮球（保留旧数据），绝不影响足球与部署。
# v1.7.119：去掉 have_time 预算门槛，改为无条件执行。此前篮球常被预算跳过，
# 叠加官方接口海外 567 且无备用源，直接造成 11-02~11-05 连续 4 天停更。
# fetch_basketball.py 自带 500.com 备用源与近 6 批次赛果回溯，单轮约 5-8s，
# 不会挤压部署时间；失败仍 exit 0 保留旧数据。
set +e
"$PY" fetch_basketball.py --backfill-days 6
set -e

# ---------- 步骤1.90：篮球预测/串关/统计（v1.7.44，独立源零耦合）----------
# 隔离设计同 1.89：篮球 AI 失败只跳过篮球预测，绝不影响足球与部署。
# 三者均依赖 1.89 的 basketball.json / basketball_history.json，故放在其后。
# v1.7.119：同样去掉预算门槛，保证赛果回填后立刻结算串关与回溯命中率。
set +e
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
set -e

log "==> 步骤1.9 队标映射 (fetch_crests.py --no-fetch)"
# 离线从已抓队标文件 + team_map 反推中文键（曼城/阿森纳等竞彩短名），
# 保证 team_map 增补后队标映射同步；FD crest 本体随首次联网抓取结果入库、长期稳定。
set +e
"$PY" fetch_crests.py --no-fetch 2>/dev/null || true
set -e

# ---------- 步骤1.95：正路推荐正确率统计（v1.7.124）----------
# 用户诉求：今日赛程里标「正路」的推荐，历史正确率到底是多少，要可核对的统计。
# 纯本地 JSON 计算（history.json 官方开奖回溯 + matches.json 竞彩赛程），零外网、<1s。
# 顺序约束：必须在步骤2 build_panel.py 之前——产物写进 matches.json.goodRateStats，
# 由 build_panel 一并注入 index.html；失败只记日志，绝不阻断部署。
log "==> 步骤1.95 正路推荐正确率统计 (gen_good_rate.py)"
set +e
"$PY" gen_good_rate.py
set -e

# ---------- 步骤1.951：V站 赛果归档回溯（v1.7.126）----------
# vzhan_history.json 历史上一直 0 场：fetch_vzhan 只能从 V站 report/info 读
# 完赛比分、且要求开球过 3h，而抓取的是当天在售赛程、快照时比赛还没开踢。
# backfill 用 history.json 官方开奖按 (date, jcNum) 对齐补归档，幂等不覆盖；
# --no-git 为轻量模式（只扫本地文件），全量 git 历史回溯已在本地一次性完成，
# 产物固化上云由 ensure_history 冷启动拉回，这里只做每轮增量兜底。
log "==> 步骤1.951 V站 赛果归档回溯 (backfill_vzhan_history.py)"
set +e
"$PY" backfill_vzhan_history.py --no-git
set -e

# ---------- 步骤1.952：V站 🔥高信心预测比分/方向命中率统计（v1.7.126）----------
# 纯本地 JSON 计算，零外网、<1s。顺序约束：必须在 fetch_vzhan(1.886) 归档与
# 1.951 回溯之后、步骤2 build_panel.py 之前——产物写进 matches.json.vzHighStats，
# 由 build_panel 一并注入 index.html；失败只记日志，绝不阻断部署。
log "==> 步骤1.952 V站高信心命中率统计 (gen_vz_high_stats.py)"
set +e
"$PY" gen_vz_high_stats.py
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

# ---------- 步骤2.5：关键数据「陈旧豁免重抓」——每日新鲜的最后保险 ----------
# 成因：早期步骤 1.85/1.89 的 have_time 预算门槛可能在总预算吃紧时跳过篮球/比分预测，
# 导致这两类数据偶尔陈旧（v1.7.44~v1.7.45 反复出现 17h/98h 停更）。
# v1.7.119 起篮球两步已改为无条件执行，这里保留「>18h 陈旧」兜底，
# 应对极端场景（进程被杀、网络全断后恢复）。部署是受保护的必跑步骤。
_py_age() { "$PY" -c "import time,os,sys; p=sys.argv[1]; print(int(time.time()-os.path.getmtime(p)) if os.path.exists(p) else 999999)" "$1"; }
_refreshed=0
if [ -f basketball.json ]; then
  _age=$(_py_age basketball.json)
  if [ "$_age" -gt 64800 ]; then
    log "   ⚠️ 竞彩篮球已陈旧（$((_age/3600))h），触发预算豁免强制重抓"
    if "$PY" fetch_basketball.py --backfill-days 6 2>/dev/null; then
      log "   ✅ 竞彩篮球已刷新"
      "$PY" fetch_basketball_ai.py 2>/dev/null && "$PY" fetch_basketball_parlay.py 2>/dev/null
      _refreshed=1
    else
      log "   !! 竞彩篮球重抓失败（沿用旧数据）"
    fi
  fi
fi
if [ -f score_predict.json ]; then
  _age=$(_py_age score_predict.json)
  if [ "$_age" -gt 64800 ]; then
    log "   ⚠️ 比分预测已陈旧（$((_age/3600))h），触发预算豁免强制重抓"
    if "$PY" fetch_scores.py --keep-cache --max-teams 25 2>/dev/null; then
      log "   ✅ 比分预测已刷新"
      # v1.7.113：重抓会重写 matches（丢历史竞彩日、status 复位），
      # 复用已有 500体育缓存重新规范化，避免磁盘与页面数据回退（不再抓网）。
      "$PY" sp_results.py --no-net --quiet 2>/dev/null || true
      _refreshed=1
    else
      log "   !! 比分预测重抓失败（沿用旧数据）"
    fi
  fi
fi
if [ "$_refreshed" = "1" ]; then
  log "==> 步骤2.5b 陈旧重抓后重建页面"
  # v1.7.124：fetch_scores.py 重抓会重写 matches.json（丢掉 goodRateStats），
  # 必须先重算统计再构建页面，否则页面统计区会退回旧字段或空白。
  # v1.7.126：V站 高信心统计同理由（同样挂在 matches.json 上），一并重算。
  "$PY" gen_good_rate.py 2>/dev/null || true
  "$PY" gen_vz_high_stats.py 2>/dev/null || true
  "$PY" build_panel.py 2>/dev/null || true
fi

# ---------- 步骤2.5c 专家命中率·赛后回溯重算（v1.7.128）----------
# 位置约束：必须在 sp_results.py / fetch_scores.py 赛果回填、2.5b 重算之后，
# 2.6 自检之前。命中率必须基于本轮最新官方赛果，否则页面数字停留在上一轮口径。
log "==> 步骤2.5c 专家命中率·赛后回溯重算 (gen_expert_stats.py)"
"$PY" gen_expert_stats.py || log "!! 专家命中率统计失败（保留上次统计），继续其余模块"
"$PY" build_panel.py 2>/dev/null || true

# ---------- 步骤2.6 最终数据自检（上线门禁）----------
# 位置约束：必须在 gen_good_rate.py / build_panel.py / 陈旧豁免重抓全部完成之后。
# 此时篮球、比分预测、正路统计、页面产物均已就绪，自检结果才反映真实状态。
log "==> 步骤2.6 最终数据自检 (selftest.py)"
set +e
"$PY" selftest.py
SELF_RC=$?
set -e
if [ "$SELF_RC" -ne 0 ]; then
  log "!! 最终自检发现问题（exit=$SELF_RC），自动执行修复流程"
  "$PY" check_and_fix.py
  "$PY" selftest.py || log "!! 修复后仍有问题，详见 selftest_report.json"
fi

if [ "$MODE" = "deploy" ]; then
  # ---------- v1.7.129：残缺数据部署守卫（CI 专用，防覆盖生产）----------
  # 海外 CI 拿不到官方接口（HTTP 567），只能回退 500.com 抓到约 30 场
  # 无隐含概率/无让球盘的残缺赛程；若不拦截就直接部署，会把生产上
  # 59 场完整数据 + 赛果回溯 + 各类统计一起覆盖掉 —— 这正是过去每轮 CI
  # 都必须人工 cancel 的根本原因。
  # 精准判据：fetch_daily.py 在官方接口熔断时会写 .ci_official_down 标记，
  # 每轮开始时清除。只有「CI 环境 + 本轮确实熔断」才判残缺并跳过部署；
  # 本地中国网络官方接口 200，不写标记，正常部署，绝无误伤。
  # 注：不能用「场次数对比」判残缺 —— 当天在售本就比历史累积少，会误伤正常更新。
  if [ -n "${GITHUB_ACTIONS:-}" ] && [ -f .ci_official_down ]; then
    log "!! 部署守卫：本轮官方接口熔断（海外 CI 仅 500.com 残缺数据），跳过部署以保护生产"
    log "   数据统一由本地全量刷新 + deploy_cf.py 上线；本轮其余步骤照常完成"
    log "==> 完成（部署守卫拦截，未改动线上）"
    exit 0
  fi
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
parts = norm("matches.json", "matches", "highOddsParlay", "safeParlay", "parlayHistory", "strongWeak", "goodRateStats", "vzHighStats")
parts += norm("history.json", "records")
parts += norm("fd_data.json", "matches", "byNum")
parts += norm("score_predict.json", "matches", "reliable", "accuracy", "vzhanMerged", "dateIndex", "dateList")
parts += norm("whoscored_data.json", "matches", "dateList", "history", "reliable")
# 篮球数据（v1.7.46 补入：此前漏算，导致篮球更新了也不触发上线 / 部署指纹失准）
parts += filehash("basketball.json")
parts += filehash("basketball_history.json")
parts += filehash("basketball_ai.json")
parts += filehash("basketball_stats.json")
parts += filehash("basketball_parlay_history.json")
parts += filehash("basketball_ai_accuracy.json")
# v1.7.126：V站 赛果归档 / 在售预测变化也要触发上线，否则高信心统计刷新了
# 页面却不更新；归档是跨轮累积的持久档案，单独计入指纹。
parts += filehash("vzhan_history.json")
parts += filehash("vzhan_data.json")
# v1.7.127：高手推荐文章内容/历史归档变化也要触发上线（此前完全没进指纹，
# 专家文章修好了页面却可能因「数据无变化」被判定跳过部署）。
parts += filehash("expert_recommendations.json")
parts += filehash("expert_history.json")
parts += filehash("expert_stats.json")
parts += filehash("expert_content.py")
parts += filehash("fetch_expert.py")
parts += filehash("rebuild_expert_history.py")
# v1.7.128：专家命中率榜与统计引擎变化也要触发上线
parts += filehash("expert_leaderboard.json")
parts += filehash("gen_expert_stats.py")
# v1.7.133：比分预测 / V站 / 权威时间 / 自检 / 部署门槛的代码与数据变化
# 也必须触发上线。此前指纹漏算这些文件，出现过「数据明明刷新了却被判定
# 数据无变化 → 跳过部署」，正是用户反复反馈的「刷新后还是旧数据」。
parts += filehash("fetch_scores.py")
parts += filehash("fetch_vzhan.py")
parts += filehash("fetch_expert.py")
parts += filehash("selftest.py")
parts += filehash("beijing_clock.py")
parts += filehash("run_daily.sh")
parts += filehash("score_predict.json")
parts += filehash("deploy_cf.py")
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
  # v1.7.133：数据/代码一变就立即部署，不再等 30 分钟冷却。
  # 旧逻辑「DATA_CHANGED=1 且 GAP>=1800」曾让整轮刷新后线上仍是旧数据
  # （用户多次反馈「按了刷新还是旧数据」）；Cloudflare 构建额度足够，
  # 正确性优先。无变化时仍保留 90 分钟定时部署做保底。
  if [ "$DATA_CHANGED" = 1 ]; then SHOULD_DEPLOY=1; fi
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
      # 部署后线上校验：专抓「部署成功但线上仍是旧数据/功能校验未过」类隐患
      # （2026-10-28 足球、篮球两次中招，根因都是只看版本号发现不了）。
      # 校验失败自动重部署一次；仍失败则告警（不阻断，避免部署死循环）。
      set +e
      "$PY" verify_deploy.py
      VRC=$?
      set -e
      if [ "$VRC" -ne 0 ]; then
        log "!! 线上校验未通过，疑似部署未生效，自动重部署一次…"
        "$PY" deploy_cf.py
        set +e; "$PY" verify_deploy.py; VRC2=$?; set -e
        if [ "$VRC2" -ne 0 ]; then log "!! 二次校验仍失败，请人工核查线上状态"; fi
      fi
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
