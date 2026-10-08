# 竞彩足球数据分析台 - 源码包

纯静态 HTML 数据看板：Python 抓取竞彩官方 + 500彩票网 + 92玩球数据 → 注入模板生成 index.html → 部署到 Cloudflare Pages。

线上地址：https://jc-football-1eo.pages.dev/

## 快速开始

```bash
# 1. 本地跑通（零第三方依赖，纯 Python 标准库）
python3 fetch_daily.py      # 抓取数据
python3 build_panel.py      # 构建 index.html
# 浏览器打开 index.html 预览

# 2. 一键脚本
./run_daily.sh              # 抓取 + 构建
./run_daily.sh check        # 抓取 + 检查修复 + 构建
./run_daily.sh deploy       # 抓取 + 构建 + 部署

# 3. 部署上线（首次需配一次凭据，之后永久生效）
python3 setup_cf.py          # 交互模式，粘贴 API Token 与 Account ID，自动写入并校验
python3 setup_cf.py --show   # 查看当前配置状态
python3 deploy_cf.py         # 输出 DEPLOY_OK 成功
```

> **缺凭据不会让流水线变红**：`deploy_cf.py` 在没有凭据时照常生成 `dist/` 离线部署包、
> 打印配置引导，并以**退出码 0** 结束，保证定时任务不受影响。详见接管文档 5.3。

环境要求：Python 3.8+（推荐 3.10+，零第三方依赖）；仅部署时需 `npm install -g wrangler`。

## 文件说明

### 核心脚本
| 文件 | 作用 |
|---|---|
| `fetch_daily.py` | 主抓取：竞彩赛程/赔率/支持率、12组规则引擎、球队近况、H2H、港澳赔率、稳胆串关、赛果回填 |
| `fetch_expert.py` | 高手推荐抓取（92玩球，仅 16:00–21:00 运行） |
| `fetch_hkmo.py` | 港澳赔率抓取（500彩票网：香港马会 + 澳门彩票） |
| `build_panel.py` | 读取各 JSON → 注入 `template.html` 占位符 → 生成 `index.html` |
| `deploy_cf.py` | 用 Wrangler CLI 部署 `dist/` 到 Cloudflare Pages（缺凭据时降级为生成离线包，退出码仍为 0） |
| `check_and_fix.py` | 6 维度全站检查与自动修复 |
| `selftest.py` | 6 大类数据自检（含串关蚊子肉检测、版本一致性），有问题退出码非 0 |
| `version_sync.py` | **版本号单一真相源**：以网页版本为准同步 APK versionName/versionCode |
| `setup_cf.py` | Cloudflare 凭据一键配置（写入 600 权限文件 + 联网校验令牌） |
| `bump_version.py` | 版本号 +1、写更新日志、**自动同步 APK 版本**、重建 index.html |

### 可选增强模块（需 API Key，未配置时静默降级）
| 文件 | 作用 | Key 来源 |
|---|---|---|
| `fetch_bsd.py` | 伤停/首发/教练/天气/裁判/AI预测 | https://dashboard.api-football.com/register |
| `fetch_odds_bsd.py` | Pinnacle/Bet365 等实时赔率与水位变动 | https://the-odds-api.com |

配置方式：`cp bsd_config.json.example bsd_config.json` 填 Key，或设环境变量 `BSD_API_KEY` / `ODDS_API_KEY`。

### 配置与模板
- `template.html` — 页面模板（CSS/JS 全内联，无外部 CDN；已内置 PWA manifest）
- `rules_config.json` — 12 组规则引擎配置（含回测样本数与命中率）
- `cf_config.json.example` — 部署凭据样例
- `bsd_config.json.example` — 增强模块凭据样例

### 数据文件（自动生成）
`matches.json`（当日比赛）、`history.json`（历史战绩，永久累计）、`parlay_history.json`（串关）、`hkmo_history.json` / `hkmo_cache.json`（港澳赔率）、`strongweak_history.json`、`form_cache.json`、`check_report.json`

## 数据自检

每次抓取后自动跑，也可手动：

```bash
./run_daily.sh selfcheck     # 有问题退出码非 0，便于 cron / CI 告警
python3 selftest.py          # 同上，报告写入 selftest_report.json
```

检查 6 大类：数据完整性 / 赔率合理性（实测返奖率应≈88.6%）/ 概率一致性 / 串关蚊子肉检测 / 历史库 / 页面产物（含**版本一致性**）。

## 版本号统一（不许再分叉）

网页版本是**唯一真相源**，APK 版本号必须自动跟随。曾出现过「网页 v1.2.2 / App v1.5」的分叉，
导致无法判断手机里装的是不是最新版。

| 项 | 规则 |
|---|---|
| 真相源 | `template.html` 的 `const VERSION = "vX.Y.Z"` |
| APK versionName | 网页版本去掉 `v` |
| APK versionCode | `major*10000 + minor*100 + patch`（v1.6.0 → 10600），与 App 端 `verCode()` 算法必须一致 |

```bash
python3 version_sync.py            # 查看是否一致
python3 version_sync.py --apply    # 以网页为准回写 AndroidManifest
python3 version_sync.py --check    # 不一致退出码非 0（CI 调用）
```

三道防线：`bump_version.py` 自动同步 → `build_apk.sh` 打包前强制同步 → `selftest.py` 分叉报 ERROR。

## 串关（三档分层）

不再只给低赔蚊子肉。稳健 / 均衡 / 搏击三档各在自身赔率区间内取「期望值 EV」最优组合，
并给出联合命中率、EV、抽水比例、建议仓位（四分之一凯利）。

> 竞彩实测单场抽水 11.4%，串关按场数放大：2串1 21.6%、3串1 30.6%、4串1 38.5%。
> 因此多数时候三档 EV 均为负、建议仓位 0%——这是市场的真实数学期望，不是模型看不看好。

## App（PWA）

模板已内置 PWA manifest 与 `apple-mobile-web-app-capable`。手机浏览器打开线上地址 → 「添加到主屏幕」，即以独立窗口运行，与网页共用同一份部署，无需上架。

## 定时更新

推荐 crontab（详见接管文档第七节）：

```bash
0 12-21 * * * cd /opt/jc-football && python3 fetch_daily.py && python3 build_panel.py && python3 deploy_cf.py
0 6 * * *     cd /opt/jc-football && python3 check_and_fix.py && python3 build_panel.py && python3 deploy_cf.py
```

## 更新日志（每次改动必做）

用户要求改动要在主页「通知」处写明白，已脚本化：

```bash
./run_daily.sh note --title "一句话标题" --feature "新增了什么" --fix "修了什么" [--note "数据口径提醒"]
```

自动完成：版本号 +1 → 写入 `CHANGELOG` → 重建 `index.html`。
打开网页右上角「通知」即可看到：**当前页数据状态**（数据更新时间 / 今日场次 / 历史条数 + 新鲜度判定）+ 逐版更新日志；有未读更新时「通知」按钮亮红点。

> 版本号只代表功能改动；**数据是否最新一律以「数据更新时间」为准**。
> **App 自动同步（原 v1.4，现统一编号 v1.6.0）**：网页与数据改动会自动同步到 App，无需重装、无需任何操作；
> 只有 Android 原生代码（`MainActivity.java`）改动才需要重新打 APK：
> `cd /workspace/apk-build && ./build_apk.sh`

## 注意

- 换机器部署时务必保留 `history.json`，否则历史战绩清零
- 高手推荐仅在 16:00–21:00 抓取，其他时段为空属正常
- 页面登录密码硬编码在前端 JS（admin / 512049996），仅防随手点开，非真实鉴权
