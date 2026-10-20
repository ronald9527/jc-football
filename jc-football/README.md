# 竞彩足球数据分析台 - 源码包

> ⚠️ **用户偏好（最高优先级）**：凡需用户操作的步骤，一律给**直达链接**（URL 直达目标动作），不给选项列表、不让用户做选择——用户只操作最后一步。详见《竞彩足球数据分析台-接管文档.md》开头「用户偏好」章节。

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
| `fetch_scores.py` | 比分预测（数据驱动）：泊松/ELO 模型输出每场预测比分与胜平负概率 |
| `fetch_whoscored.py` | 比分预测（WhoScored 人工预测）：抓 whoscored.com/previews 官方人工预测比分，带永久缓存 + 赛果回填 + 历史累积（按比赛 id 去重）；竞彩编号双源匹配（matches.json 当前销售日 + history.json 持久档案） |
| `team_map.py` | 英中队名权威映射（单一真相源），供 WhoScored 预测匹配竞彩编号（en_to_zh 三级查链 + norm_team 归一化 + _ZH_ALIAS 中文异写归一） |
| `ensure_history.py` | 历史库防丢守卫：本地历史缺失/过少时从 Pages 云端副本拉回 |
| `build_panel.py` | 读取各 JSON → 注入 `template.html` 占位符 → 生成 `index.html` |
| `deploy_cf.py` | 用 Wrangler CLI 部署 `dist/` 到 Cloudflare Pages（缺凭据时降级为生成离线包，退出码仍为 0） |
| `check_and_fix.py` | 6 维度全站检查与自动修复 |
| `selftest.py` | 6 大类数据自检（含串关蚊子肉检测、版本一致性），有问题退出码非 0 |
| `version_sync.py` | **版本号单一真相源**：以网页版本为准同步 APK versionName/versionCode |
| `setup_cf.py` | Cloudflare 凭据一键配置（写入 600 权限文件 + 联网校验令牌） |
| `bump_version.py` | 版本号 +1、写更新日志、**自动同步 APK 版本**、重建 index.html |

### 可选增强模块（需 API Key，未配置时静默降级）
| 文件 | 作用 | Key 来源（均为**免费层**） | 启用状态 |
|---|---|---|---|
| `fetch_bsd.py` | 伤停/首发/教练/天气/裁判/AI预测 | https://dashboard.api-football.com/register（免费 100 次/天） | ⛔ **暂不启用**：免费额度严重不足（详见下） |
| `fetch_odds_bsd.py` | Pinnacle/Bet365 等实时赔率与水位变动（**独立源**：只写 `odds_bsd.json`） | https://the-odds-api.com（免费 500 次/月） | ⛔ **暂不启用**：免费额度不足（详见下） |
| `fetch_football_data.py` | football-data.org 官方赛果/赛程核对（**独立源**：只写 `fd_data.json`） | https://www.football-data.org/client/register（免费层） | ✅ 已启用（你提供的 key） |
| `fetch_basketball.py` | 竞彩篮球：官方 basketball 接口赛程+四玩法赔率（胜负/让分/大小分/胜分差）+ 赛后赛果回填（**独立源**：只写 `basketball.json` / `basketball_history.json`，与足球零耦合） | 竞彩官方 webapi（同足球网关，免费） | ✅ 已启用（v1.7.29 新增板块） |

> ⚠️ **免费额度实测结论（按自动任务 30 次/天、约 900 次/月）**：
> - **API-Football（100/天）**：每次运行约 60–120 次调用（1 次索引 + 每场 3 次），**首跑即超额 18–36 倍**，完全不够。
> - **The Odds API（500/月）**：每次运行 5–10 次（按当日联赛数），月均需 4500–9000 次，**超额 9–18 倍**，不够。
> - 两者免费层均**不足以支撑本项目的刷新频率**，按"免费优先铁律"**直接过滤、暂不启用**。若日后需要，必须加节流（如赔率每天只抓几次）另议。

配置方式：`cp bsd_config.json.example bsd_config.json` 填 Key，或设环境变量 `BSD_API_KEY` / `ODDS_API_KEY` / `FD_API_KEY`。

> 🔒 **免费优先铁律（用户硬性要求）**：本项目**只使用免费层数据源**，任何需要付费/订阅的源或付费升级方案，**一律不过问用户、直接过滤掉**。若某免费层额度用尽，对应模块**静默降级**（不报错、不提示付费、不阻塞主流程）。核心竞彩数据与模型训练永远零成本。

> 📉 **Cloudflare Pages 构建配额（重要）**：免费层仅 **500 次构建/月**。自动任务原 30 次/天 ≈ 900 次/月会超限（约第 17 天起新部署被拒、站点停更）。已在 `run_daily.sh` 加**部署节流**：① 核心数据无变化则跳过；② 两次部署至少间隔 90 分钟 → 上限 ≈16 次/天（≈480/月，留余量）。抓取/分析仍 30 次/天照常免费跑，只有"部署上站"被节流。

> 🕷️ **抓取防反爬节流（重要）**：每天 30 次运行对 sporttery.cn / 500.com 的慢变抓取（赔率历史、球队近况、港澳赔率、竞彩页）原来约 **1470 次/天**，易触发限频/封 IP 导致数据退化。已加统一 `webcache.py` TTL 缓存层：赔率历史/港澳赔率 3 小时、球队近况/竞彩页 6 小时刷新一次，压到约 **250–300 次/天（省 ~80%）**。冷/热两次压测验证：冷缓存 49 MISS、热缓存 49 HIT / 0 MISS。沙箱重置丢失缓存时自动降级为全抓（与改造前一致），不报错、不影响核心数据。

> 🛡️ **竞彩 WAF 概率性拦截与重试（2026-10-15 修复，改抓取代码前必读）**：`webapi.sporttery.cn` 启用了腾讯云 WAF，对单次请求**概率性**返回 403 拦截页、567 竞彩反爬页、或 `200` 但空数据（`success:true` 却无 `matchInfoList`）——10 连采仅 3 次通过，**重试即可通过**（8 次内几乎必过，被拦响应本身 <1s，重试成本低）。此前 `fetch()` 无重试，一次被拦就抛异常 → `run_daily.sh` 按容错设计恢复旧数据、跳过更新（exit 0）→ **定时任务每天"成功"但数据永远停在旧批次**（页面提示「数据已过期」）。现 `fetch()`/`fetch_html()` 统一带退避重试（403/567/网络异常/空响应都重试，`must_contain` 参数识别空响应）；`fetch_odds_history` 失败时改抛异常，避免空结果被 TTL 缓存毒化 3 小时。**教训：对竞彩接口，「一次失败即跳过」的容错设计会把概率性拦截放大成永久停更——关键接口必须重试。**

> 🔐 **自举包凭据泄露事故（2026-10-15 修复，改打包规则前必读）**：`deploy_cf.py` 每次部署会把整套代码打包成 `code-bundle.tar.gz` 上传到**公开** Pages 供定时任务沙箱自举下载。原排除规则只挡 `.last_` 前缀和持久数据，**漏了 `cf_config.json`**——定时任务 prompt 第 2 步会在沙箱重写该文件（真实 Cloudflare token），随后部署打包 → **token 随公开包泄露约 17 分钟**（12:04–12:21）。修复：打包新增 `_is_secret()` 排除所有 `*_config.json`（`.example` 除外），已重新部署覆盖线上包并验证无敏感文件。**✅ 轮换已完成（2026-10-19 14:15 用户 Dashboard Roll；2026-10-20 复查补漏）**：旧 token 已作废，新 token 同步三处——① 本地 `cf_config.json`；② 定时任务「竞彩数据自动更新」（id 11572893）与「验证自动部署-一次性」（id 11826523）prompt 内的兜底凭证 heredoc；③ 新 token 实测部署成功（DEPLOY_OK）。⚠️ 2026-10-20 复查发现 11572893 的 heredoc **实际仍写着旧 token**（此前「已同步」的操作未真正落库——活动任务会用它覆盖代码包新凭证，下次定时部署必失败），已重新更新并用 get 接口验证持久化。新 token 请勿再写入任何会进公开包的文件。**教训：token/配置同步后必须重新读取验证落库，不能凭操作记录认为已完成。****教训：凡上传到公开域的自举/交付包，排除规则必须按「默认拒绝、显式白名单」思维设计——新增任何配置文件时先问「它能公开吗」。**

> 🔁 **自举包指纹排除导致版本无限膨胀（2026-10-15 修复，改打包规则前必读）**：自举包原先把 `.last_template.sha`（模板指纹）也当 `.last_` 前缀一律排除，而定时沙箱可能残留上一轮的旧指纹文件 → 新包的 template.html 与残留旧指纹**必然不匹配** → `template_autobump` 每轮都误判「模板变了但 VERSION 未手升」→ **版本号每 30 分钟自动 +1 无限膨胀**（实测 13:00 轮 v1.7.14→v1.7.15，页面内容其实毫无变化，只是 VERSION 字符串）。修复：打包规则精细化——`.last_deploy.ts`/`.last_deploy.sha`（部署节流状态）仍然排除（跨环境共享会让沙箱误判「刚部署过」而跳过更新），但 **`.last_template.sha` 必须随包传播**，保证模板与指纹同源、比对可信。修复后实测：连续两轮部署版本稳定不跳。**教训：指纹类文件要与被指纹化的对象同包传播；「按前缀一刀切排除」会把一致性保障文件当成垃圾扫掉——排除规则要逐文件论证「它在新环境里应该是旧值还是不存在」。**

> 🌙 **凌晨误报「数据已过期」（2026-10-18 修复，v1.7.16）**：定时批次为每日 09:00–21:00 每 30 分钟，凌晨 0–9 点本就不跑。但页面新鲜度判定只按「数据日期 == 今天」，导致凌晨打开时昨晚 23:01 的晚间批次被误报为红色「数据已过期（非今日批次），说明云端抓取任务未执行」——数据其实是最新的，纯属误报。修复：`renderUpdateBar` 与 `renderDataFreshness` 增加夜间宽容规则——凌晨 0–9 点打开且数据为昨晚 20:00 后批次时，降级为 warn「夜间无新赛事批次（昨晚 HH:MM 仍为最新），09:00 首轮自动更新」。**教训： staleness 判定要考虑业务批次的运行窗口，不能只做日期比对。**

> 📦 **定时部署丢失 APK（2026-10-18 修复，v1.7.17）**：`deploy_cf.py` 从 `/workspace/竞彩足球数据分析台-v{版本}.apk` 找安装包上线，但定时任务沙箱解包 `code-bundle.tar.gz` 后只有 `jc-football/` 代码目录，**`/workspace` 顶层没有 APK** → 每轮定时部署都「未找到 APK，跳过」→ Pages 全量替换后线上 `.apk` 全部退化成 HTML fallback。且 `_headers` 给 `/jc-football*.apk` 强制了 `Content-Type: application/vnd.android.package-archive`，fallback 的 HTML 也被伪装成 APK  content-type，形成**「假 200」**：下载得到 3.9MB HTML，手机装不了。修复：APK 显式加入自举包（arc 为顶层文件名，解包后正好落在 `deploy_cf.py` 寻找位置），每轮定时部署都带着 APK 上线。**教训：①「本地有但目标环境没有」的部署输入，必须随自举包传播；② 对不存在的资源强制 content-type 会把 404 fallback 伪装成正常响应，排查时不能只信状态码和 content-type，要校验文件签名（APK/ZIP 应为 `PK\x03\x04`）。**

> **数据源隔离原则（接管规范）**：`fetch_odds_bsd.py`（The Odds API）、`fetch_football_data.py`（football-data.org）、
> `fetch_bsd.py`（API-Football）、`fetch_expert.py`（92玩球/懂球帝高手荐单）均为**独立展示/核对源**。它们只产出各自的 JSON
> （`odds_bsd.json` / `fd_data.json` / `bsd_data.json` / `expert_recommendations.json`），**绝不写回核心 `matches.json`、
> 绝不写入 `history.json`（模型训练数据）、绝不改动 `smart_model.json`**。模型只读取竞彩官方 `history.json`，
> 与上述独立源零耦合。`selftest.py` 第 7 类专门校验此隔离（已确认 `matches.json` 不含 `odds_bsd`/`bsd` 字段）。

> 🧩 **改了 template.html 忘了重跑 build_panel.py（2026-10-19 修复，v1.7.20）**：改完 `template.html` 后直接跑 `deploy_cf.py`——部署的 `index.html` 是上一次 `build_panel.py` 的产物，`template_autobump` 只对 `template.html` 算指纹并升 `version.json` 的 pageVersion，**页面内容还是旧的**：线上 version.json 显示 v1.7.20 但新文案根本没上线（名实不符，还误判了一次「APK 版本回退」）。修复：重跑 `python3 build_panel.py` 生成 index.html 后再部署，线上 grep 验证文案存在 + playwright 渲染验证。**教训：部署链路的真相源是 `index.html`（由 build_panel 从 template.html 生成），自动升版机制只保护版本号不保护内容——改模板后必须「build_panel → deploy」两步走，部署后必须校验页面内容而非只看 version.json。**

### 配置与模板
- `template.html` — 页面模板（CSS/JS 全内联，无外部 CDN；已内置 PWA manifest）
- `rules_config.json` — 12 组规则引擎配置（含回测样本数与命中率）
- `cf_config.json.example` — 部署凭据样例
- `bsd_config.json.example` — 增强模块凭据样例
- `fd_config.json.example` — football-data.org 凭据样例（实际 `fd_config.json` 权限 600，已本地化）

### 数据文件（自动生成）
`matches.json`（当日比赛，**核心数据集，不含任何独立源字段**）、`history.json`（历史战绩，永久累计，**模型训练数据**）、`parlay_history.json`（串关）、`hkmo_history.json` / `hkmo_cache.json`（港澳赔率）、`strongweak_history.json`、`web_cache.json`（本地 TTL 抓取缓存，**不上传**、不上库）、`check_report.json`；
独立源文件：`odds_bsd.json`（The Odds API 国际赔率）、`fd_data.json`（football-data.org 官方赛果）、`expert_recommendations.json` / `expert_stats.json` / `expert_history.json`（高手荐单）、`whoscored_data.json`（WhoScored 人工预测，页面「比分预测」Tab 的 WhoScored 区块数据源）；
WhoScored 累积三件套（**部署到 Pages 防沙箱重置丢失**）：`whoscored_history.json`（预测×赛果配对的回测记录，按比赛 id 去重）、`whoscored_preview_cache.json`（预测比分永久缓存——预测必须在开球前抓，已开球的 preview 页只返回硬封锁）、`whoscored_recent.json`（近期比赛元数据，补回已滚出 /previews 列表的当天场次）

## 数据自检

每次抓取后自动跑，也可手动：

```bash
./run_daily.sh selfcheck     # 有问题退出码非 0，便于 cron / CI 告警
python3 selftest.py          # 同上，报告写入 selftest_report.json
```

检查 7 大类：数据完整性 / 赔率合理性（实测返奖率应≈88.6%）/ 概率一致性 / 串关蚊子肉检测 / 历史库 / 页面产物（含**版本一致性**）/ **数据源隔离**（独立源不得写回核心 `matches.json` 或 `history.json`）。

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

不再只给低赔蚊子肉。稳健 / 均衡 / 精锐三档各在自身赔率区间内取最优组合，
并给出联合命中率、EV、抽水比例、建议仓位（四分之一凯利）。

**单场单意见（v1.7.24，改代码前必读）**：每场比赛先用统一评分（历史同指命中率
优先，叠加 EV/信心/技术信号）选出**唯一**「系统意见」选项，三档只在该意见池内做
组合——同一场比赛全天一个说法，档位差异只体现在「选不选它、拼几场」。
⚠️ 旧实现每场保留最多 3 个选项、各档自行挑选，导致同一场在稳健档选「客胜」、
均衡档选「让球+1让胜」的自相矛盾展示（用户实测反馈），已废除。
代价：意见池收窄后，若当日唯一意见凑不满某档的赔率区间/软腿约束，该档留空
（前端如实展示），不靠同场改玩法硬凑。

**赔率单调（v1.7.25，改代码前必读）**：三档按 **稳健→均衡→精锐** 顺序构建，
每档总赔必须**高于上一档实际总赔**（动态地板 = max(静态下限, 上一档总赔+0.01)）。
⚠️ 旧实现三档独立构建，精锐档 hit 模式天然偏爱便宜腿（赔率低=市场共识概率高=
同指命中率高），4 条便宜腿乘积极可能低于 3 条中价腿——2026-10-20 实测出现
「精锐4串1 6.03 倍 < 均衡3串1 6.27 倍」的倒挂（用户投诉「4 场比 3 场赔得少」），
档位语义崩坏，已修复。顺序构建后若某档无达标组合（候选不足/赔率单调门槛未过），
该档**留空不硬凑**，`poolNotes` 如实告知用户，候选池（每场的系统意见+赔率+
信心+同指命中率）在页面完整透明展示。

> 竞彩实测单场抽水 11.4%，串关按场数放大：2串1 21.6%、3串1 30.6%、4串1 38.5%。
> 因此多数时候三档 EV 均为负、建议仓位 0%——这是市场的真实数学期望，不是模型看不看好。
> 展示策略（少而精）：有正期望档时只展正期望档；三档全负时只出一张「今日无正期望串关机会」
> 总结卡，负期望详情折叠可展开——不推一堆负期望组合让人亏钱。
> 兜底纪律（v1.7.24）：EV 未达标的兜底组合与达标组合**分开计分**，达标组合永远优先返回，
> 杜绝「高同指但深度负 EV」的组合挤掉达标组合。
> 均衡档单腿信心下限 55→60（v1.7.25）：拒绝为填赔率带塞入信心仅 55、同指 47% 的蹭线弱腿；
> 静态总赔下限 4.50→4.00，让位给动态地板（消除与精锐档的赔率带重叠）。
> **引擎版本门（v1.7.25）**：`save_parlay_history` 落档时记录 `engineVer`（template.html 的
> VERSION）；`load_locked_parlay` 发现当日有**旧引擎生成且尚未开球**的 pending 记录时，自动删除
> 并解锁，让新引擎当日重生——解决「缺陷版本推荐被当日锁定保护、修复无法当天生效」的死锁
> （2026-10-20 实测：定时任务用自己的旧历史把 v1.7.25 部署覆盖回 v1.7.23 倒挂推荐）。
> 已判定（hit/miss）的记录是历史战绩凭证，绝不删除；已开球的 pending 记录同样保留（进入投注
> 窗口后不可篡改）。定时任务同步的是主工作区 git HEAD——**修复必须 commit 才对定时任务生效**。

## 比分预测（Tab）

两个独立预测源，共用「比分预测」Tab；**均只显示竞彩每天开出的比赛**（v1.7.23 起，非竞彩场次隐藏）：

1. **数据驱动**（`fetch_scores.py`）：泊松/ELO 模型，输出每场预测比分与胜平负概率；
2. **WhoScored 人工预测**（`fetch_whoscored.py`）：抓取 whoscored.com/previews 的官方人工
   预测比分，含历史日期回溯（按比赛 id 去重累积「预测×赛果」回测记录）。

**竞彩主干化口径（v1.7.23，改代码前必读）**：

- 列表主干 = `matches.json` 当天竞彩开售赛程的全部场次；每场按「**竞彩编号 + 日期±2天**」关联
  WhoScored 预测（`wsJcCandidate`）。⚠️ **竞彩编号每周循环**（每周一都有「周一002」），
  只按编号直查会把上周同号比赛误配到本周——日期±2 天过滤后编号在窗口内不可能重复，匹配才唯一；
- 服务端 `attach_jc_numbers` 双数据源匹配：`matches.json`（当前销售日）+ `history.json`
  （持久档案 549 条，比赛滚出销售窗口后仍能补挂编号）；历史累积记录带 `jcNum`，
  赛后回溯区只显示对应竞彩的场次；
- 无预测的场次标「未推荐」并三分类原因：同联赛有推荐（疑似漏匹配，⚠️ 待人工核对）/
  该联赛 N 场 0 推荐 / 数据源未覆盖；
- 自建 Poisson 模型区同样按竞彩队名组合过滤（`spJcPairs`/`spJcFilter`/`spJcNumOf`），
  主客顺序不敏感，翻译失败保守隐藏。

**竞彩编号匹配加固（v1.7.26，改代码前必读）**：匹配链是
`en_to_zh(英文队名) → zh_norm(中文名) → 与竞彩队名精确/归一化比对`，历史上断在
**「一字之差」**：team_map 表内同一英文别名在不同分区登记了不同中文译名，且
`TEAM_EN2ZH[_en] = _zh` **后行覆盖前行**——Portsmouth 在英冠区是「朴次茅斯」（与竞彩
官方名一致），在英系汇总区被「朴茨茅斯」覆盖，导致周二003 狼队vs朴次茅斯
「有预测（1:1）但挂不上编号、页面显示未推荐」（用户实测投诉）。全表扫描共
**167 个同类潜在冲突**。三层修复：

1. **译名对齐**：冲突行改为竞彩官方名（朴次茅斯）；补录缺失条目（Sabah FK→萨巴赫，
   注意与马来西亚球队「沙巴」是两队、只登记精确别名；Bodoe/Glimt 拼写变体；
   Slovan Bratislava→布拉迪斯拉发）；`_ZH_ALIAS` 增加 朴茨茅斯→朴次茅斯、
   博多格林特→博德闪耀 两条同队异写归一；
2. **变体集兜底（根治）**：`team_map.en_to_zh_all()` 返回该英文别名登记过的**全部**
   中文译名，`_match_jc_num` 在主译名精确匹配失败时用变体集再匹配一次——
   表内任何「同英文→不同中文」冲突都不可能再让整场预测失配；
3. **回归测试**：`test_whoscored_jcnum_003.py` 用线上真实数据验证 003 挂载 +
   抽查无回归。修复后不仅今天 10/10 全覆盖，还顺带治愈 6 场历史静默失配记录
   （如 10-19 周日018 诺丁汉森林vs阿森纳）。

**赛果回填防饿死（v1.7.28，改代码前必读）**：赛果回填段从
「按 matches 原始顺序遍历、不看有没有预测」改为「候选 = 有预测 + 未回填 + 已开球，
按 UTC 开球时间升序回填（等最久先服务）」。饿死根因：单轮 60 场 title 抓取配额被
**无预测的场次白白吃掉**——10-17 一天 660 场里约 590 场无预测，回填了也不会写
history（历史累积段同样要求 predH + evaluated），排队尾部的 10-18/19 永远轮不上
（用户实测：比分预测历史缺 10-18/10-19 两天）。配套：HTTP 超时 30s→12s
（WhoScored 正常响应实测 1.6-2s，超时请求多半是 IP 软限流下的挣扎，快速失败把
时间让给同批次其他场次），回填预留 15s→8s（收尾实测 <2s）。修复后单轮回填
10+ 场，候选按日期 FIFO 排队，历史数据逐轮自动补齐、任何日期都不会再饿死。

WhoScored 反爬对策（实测结论，改代码前必读）：

- **软挑战 ≠ 封锁**：真实完整页（100–600KB）顶部注入 verify-client.js，**数据完整可直接解析**，
  曾因误判为挑战页拒绝，导致全链路解析失败（历史 records 为空、回溯按钮 0 个）；
- **硬封锁**：几百字节质询页 / Cloudflare 403「Attention Required」页，无数据；
- **IP 级限流持续数小时**：秒级退避等不来解封 → 改为快速失败（2 次尝试），下轮调度自然重试；
- **预测必须在开球前抓**：已开球/完赛的 preview 页只返回硬封锁 → 抓取阶段按「今天/明天优先」
  排序 + 跳过已开球超 10 分钟的场次，预测进永久缓存，赛果走 livescores / live 页 title 回填。

## 竞彩篮球（Tab）

v1.7.29 新增板块，v1.7.30 重构为**独立专区 + 二级 tab 架构**（回应用户「新增的是第二页竞彩篮球板块，后续我需要的东西可以在这一页新增 tab」），与足球**完全隔离**（回应「新增副页会不会影响足球的数据」：不会，见下方隔离设计）。

- **专区结构**：一级 tab「竞彩篮球」→ 进入篮球专区页（大标题 + 隔离说明 + 数据更新时间 + **专区专属二级 tab 栏**）。当前二级 tab：`赛程赔率`（未开赛/待回填比赛 + 四玩法赔率）/ `赛后回看`（已完赛，按日期倒序分组，比分 + 让分/大小分判定）。**后续篮球新功能（AI 预测、串关、历史统计等）都往二级 tab 里追加**（template.html 的 `renderBasketball` 分派处有扩展注释），一级 tab 栏保持稳定不膨胀
- **数据源**：竞彩官方 webapi 的 basketball 路径（与足球同网关、免费、无需 Key）：
  - 赛程+赔率：`basketball/getMatchCalculatorV1.qry`（在售比赛）
  - 赛果回填：`basketball/getFixedBonusV1.qry?matchId=`（开奖后 `matchResult.poolStatus="Payout"`，`sectionsNo999` 形如 `"93:116"` = 主队:客队比分）
- **玩法**（与足球不同，四玩法）：`mnl` 胜负 / `hdc` 让分胜负（goalLine 为代数值，主队实际分+盘口后比较）/ `hilo` 大小分 / `wnm` 胜分差（l1-l6 = 1-5/6-10/11-15/16-20/21-25/26+ 分）
- **编号空间独立**：篮球周二301 起，与足球 001 起并行，互不冲突；页面底部 Tab 切换
- **隔离设计（三层）**：
  1. **文件层**：篮球只写 `basketball.json` / `basketball_history.json`，足球的 `matches.json` / `history.json` / `rules_config.json` 等零触碰；
  2. **抓取层**：`fetch_basketball.py` 独立运行，失败只保留旧数据跳过篮球（exit 0），`run_daily.sh` 步骤1.89 与其他独立源同款 `have_time` 预算门槛，绝不拖垮足球抓取与部署；
  3. **校验层**：`test_basketball_fetch.py` 回归测试实证跑篮球 main() 前后 5 个足球核心文件 sha256 全程不变；`selftest.py` 第 7 项新增校验（核心 `matches.json` 不得混入篮球字段 `mnl/hilo/wnm`）
- **赛果回填节奏**：开赛 2.5 小时后定时任务自动查官方开奖接口，回填比分并判定让分（主队+盘口 vs 客队）与大小分（总分 vs 盘口）结果；单轮上限 30 场保护时间预算
- **首版不含 AI 预测**：足球 Poisson 模型不适用于篮球，篮球预测模型作为二期（大小分期望/让分覆盖），需历史数据积累后再上

## App（PWA）

模板已内置 PWA manifest 与 `apple-mobile-web-app-capable`。手机浏览器打开线上地址 → 「添加到主屏幕」，即以独立窗口运行，与网页共用同一份部署，无需上架。

## 定时更新

✅ **已启用平台自动化定时任务**（任务 ID `11572893`）：每天 **09:00–23:59 每 30 分钟**
自动执行 `./run_daily.sh deploy`（抓取 → 自检 → 失败自动修复 → 构建 → 部署）。

```bash
# 管理命令
cd /root/.codebuddy/skills/automation-task-manager
./scripts/scheduler-api.sh get --id 11572893          # 查看
./scripts/scheduler-api.sh update --id 11572893 --status 0   # 暂停
```

> **为什么不用 GitHub Actions**：实测 GitHub Runner 在境外，竞彩官方接口
> `webapi.sporttery.cn` 返回 `HTTP 567` 拒绝，物理上拿不到国内数据（详见接管文档 7.3）。
> GitHub 仓库仅作代码备份与 `history.json` 云端留存。

**备用方案**（国内服务器 / NAS crontab，见接管文档 7.2）：

```bash
*/30 9-21 * * * cd /opt/jc-football && ./run_daily.sh deploy >> /var/log/jc.log 2>&1
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

### App 整页同步原理与 308 修复（v1.7.11）

同步链路：App 启动/前台/每 3 分钟拉 `version.json` → `pageVersion` 变大（verNewer）或
`version.json` 的 `updateTime` 比本地 `matches.json` 新（dataNewer）→ 下载线上整页覆盖缓存。

> **历史 bug（v1.7.11 修复）**：Cloudflare Pages 对 `/index.html` 返回 **308 永久重定向**到 `/`，
> 而 Java `HttpURLConnection` 默认只跟随 301/302、**不跟随 307/308** → 整页同步自 App 安装起
> 就永远失败（`version.json`/`matches.json` 无后缀名不跳转，所以只有数据同步是好的、
> 页面结构同步是坏的，用户侧症状：「网页版有的，App 版没有」）。
> 修复：`httpGet`/`httpGetRange` 改为手动跟随 301/302/303/307/308（最多 4 跳，相对地址自动解析），
> 页面 URL 同时改用根路径 `/`（少一跳）。**旧 APK 用户需重装一次修复版，之后永久自动同步。**

### 部署侧版本自动提升防护

`deploy_cf.py` 以 `template.html` 的 SHA256 为指纹（`.last_template.sha` 跟踪，已被代码包
排除规则挡在自举包外）：模板变了但 `const VERSION` 忘了手升时，自动升 patch 位并写回
`template.html`（唯一真相源）+ 本次部署的 index.html；`version.json` 同时输出 `pageBuild`
指纹字段，便于核对线上到底是哪个构建。

## 注意

- 换机器部署时务必保留 `history.json`，否则历史战绩清零
- 高手推荐仅在 16:00–21:00 抓取，其他时段为空属正常
- 页面登录密码硬编码在前端 JS（admin / 512049996），仅防随手点开，非真实鉴权
