# Best Price：给用户和 Agent 使用的本地购买研究服务

Best Price 是一个运行在你自己电脑上的 API 服务，帮助你搜索闲鱼商品、保存原始挂牌、
比较同款二手报价，并让 Agent 进一步完成型号研究、商品评估和新旧价格对照。

当前版本：`0.4.0`。默认只监听 `127.0.0.1:8765`，数据保存在本机 SQLite，适合个人购物研究；
不面向公网部署，不用于高频采集或商业分发。

## 这份手册能帮你做什么

- [安装、配置与启动服务](#安装配置与启动服务)
- [部署为长期运行的本地服务](#部署与日常运维)
- [使用命令行或 HTTP API](#用户怎么使用)
- [让 Agent 和 Skill 使用服务](#让-agent-使用-best-price)
- [了解当前功能与限制](#功能全景)
- [排查登录和 API 问题](#登录可选)

如果只想尽快用起来，完成下面三步即可：

```bash
scripts/setup.sh
scripts/start-local.sh
# 另开一个终端
scripts/query-price.sh "富士 X-T4" --pages 2
```

> `query-price.sh` 会真实访问闲鱼。请保持低频；遇到登录、验证码或限流提示时停止自动操作，
> 按错误信息完成必要的人工处理。

---

## 功能全景

| 能力 | 现在能做什么 | 使用入口 |
|---|---|---|
| 闲鱼搜索 | 按关键词、价格范围、排序和有限页数搜索；记录每次 run 的状态和来源 | `POST /v1/search`、`query-price.sh` |
| 原始商品数据 | 保存完整描述、挂牌价、卖家公开信息、地区、主图、包邮、拍卖/广告等信号 | `GET /v1/products` |
| 原始价格统计 | 计算当前 run 的最低价、分位数、中位数、最高价与样本质量 | `GET /v1/stats` |
| 任务调度 | 合并重复查询、复用短期缓存、限制队列、跨重启保留冷却/人工阻断状态 | 搜索 API 自动处理 |
| 购买研究 | 支持指定型号、品类探索、具体挂牌核查三种模式；保存预算、约束、候选池和停止原因 | `/v1/researches*` |
| 文本分析 | 默认用保守规则提取配件、租赁、定金、故障等原文证据；可选接入兼容模型 | `POST /v1/text-analyses` |
| 商品评估 | 保存五维评分、证据覆盖度、风险、人工反馈和评分版本 | `/v1/evaluations*` |
| 可比二手价 | 只对 Agent 已确认的同 SKU、同配置商品去重统计，不污染原始统计 | `GET /v1/researches/{id}/comparable-stats` |
| 新旧价格对比 | 导入有 URL、时间、SKU 和条件的新品报价；成本完整时计算节省额和比例 | `/v1/new-prices*`、quote match |
| 结果分组 | 分开返回型号层和挂牌层的 Primary、Extra、Review、Excluded | `GET /v1/researches/{id}/ranked` |
| Agent 自助发现 | 返回实际端点、配置状态、错误恢复方式和当前降级能力 | `/help`、`/openapi.json`、`/v1/capabilities` |

Best Price 的特色是：**原始事实、可比性判断和购买建议分层保存**。它不会为了给出一个“漂亮答案”
而改写原始挂牌，也不会把 AI 推断冒充平台事实。每个研究结果都可以追溯到 run、商品链接、
采集时间、证据、规则版本和新品报价来源。

### 当前明确限制

- 自动采集平台目前只有闲鱼；京东、淘宝、天猫、拼多多自动适配器尚未通过真实账号和许可证闸门。
- 新品价格可以通过 Quote Import 导入，但 Mock/Dry-run 报价会被拒绝。
- 云端文本模型默认关闭；关闭时规则分析、原始搜索和价格统计仍可正常使用。
- 闲鱼搜索结果本身可能包含租赁、求购、配件、定金链接、拍卖起拍价和广告位。
- 搜索响应目前只有主图，不把未取得的多图或商品规格编造出来。
- 服务默认仅供本机访问；不要直接把端口暴露到公网。

---

## 安装、配置与启动服务

### 1. 环境要求

| 项 | 要求 |
|---|---|
| 系统 | macOS 或 Linux；Windows 需要自行换算 PowerShell 命令 |
| Git | 用于拉取 Best Price 和独立的闲鱼上游采集器 |
| uv | 用于创建 Python 3.12 虚拟环境和安装依赖 |
| Python | 建议 3.12；`setup.sh` 会交给 uv 管理 |
| Chromium | 普通搜索不需要；只有人工扫脸核身时才需要 |

首次安装：

```bash
git clone https://github.com/Arragon/best-price.git bestprice
cd bestprice
scripts/setup.sh
```

`setup.sh` 会创建 `.venv`、安装项目依赖、把闲鱼采集器放入 gitignored 的 `upstream/`
独立目录，并记录上游 commit。脚本是幂等的，可以重复运行。

### 2. 创建配置

不改配置也能以安全默认值启动。需要定制时：

```bash
cp .env.example .env
```

常用配置：

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `APP_HOST` | `127.0.0.1` | API 监听地址 |
| `APP_PORT` | `8765` | API 端口 |
| `DATABASE_PATH` | `./data/price.sqlite3` | SQLite 数据库位置 |
| `MAX_SEARCH_PAGES` | `3` | 单次搜索允许的最大页数 |
| `MIN_SECONDS_BETWEEN_SEARCHES` | `30` | 兼容旧部署的全局搜索间隔下限 |
| `SECONDS_BETWEEN_PAGES` | `3` | 同一次搜索的分页间隔 |
| `MAX_PENDING_JOBS` | `12` | 本地等待队列上限 |
| `SEARCH_CACHE_TTL_SECONDS` | `600` | 成功 run 的本地复用时间 |
| `MIN_SAMPLE_THRESHOLD` | `8` | 低于该样本量时标记证据不足 |
| `LOG_FILE` | `./data/logs/bestprice.log` | 有界轮转日志位置 |
| `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | `5242880` / `3` | 单日志大小和保留份数 |
| `AI_ENABLED` | `false` | 是否启用兼容 OpenAI API 的文本分析模型 |
| `AI_BASE_URL` / `AI_MODEL` | 见 `.env.example` | 模型服务地址和模型名 |
| `AI_API_KEY` | 空 | 只允许放在本机 `.env`，不要写入 Git 或聊天 |

完整配置及注释见 [`.env.example`](./.env.example)。`pace=fast` 只是任务偏好；默认情况下仍受
`MIN_SECONDS_BETWEEN_SEARCHES` 保护。只有明确启用 `ALLOW_FASTER_PACE=true` 后才采用更快档位，
并且永远不能越过 `PLATFORM_FLOOR_SECONDS`、冷却状态或人工验证状态。

启用可选文本模型时，只在本机 `.env` 中填写：

```dotenv
AI_ENABLED=true
AI_BASE_URL=https://你的兼容服务/v1
AI_MODEL=你的模型名
AI_API_KEY=你的本机密钥
```

模型必须提供 OpenAI-compatible Chat Completions 接口。配置错误或模型不可用时，文本分析会明确
降级到规则结果，不会让原始搜索失败；模型输出的每个标签仍必须引用挂牌原文。

### 3. 启动并检查

```bash
scripts/start-local.sh
```

服务以前台单进程运行。看到监听地址后，另开终端检查：

```bash
curl -sS http://127.0.0.1:8765/health
curl -sS http://127.0.0.1:8765/v1/capabilities
curl -sS http://127.0.0.1:8765/help
```

常用页面：

- 交互式 API 文档：<http://127.0.0.1:8765/docs>
- OpenAPI：<http://127.0.0.1:8765/openapi.json>
- Agent 使用说明：<http://127.0.0.1:8765/help?format=text>

按 `Ctrl+C` 可以安全停止。不要同时启动多个实例写同一个 SQLite 数据库；服务的正确部署模型是
**一个进程、一个 worker、一个数据库**。

---

## 部署与日常运维

### 推荐部署方式

本项目推荐部署在拥有数据和登录状态的那台 Mac/Linux 主机上：

```text
用户或 Agent
    ↓ 本机 HTTP / SSH 或 Tailscale 隧道
Best Price（单进程，127.0.0.1:8765）
    ↓
SQLite + 独立上游采集器
```

- 日常启动命令始终是 `scripts/start-local.sh`。需要长期运行时，把这个脚本交给 launchd、systemd
  或你已有的进程管理器；工作目录必须是仓库根目录，实例数必须保持为 1。
- 默认监听 loopback。跨设备访问优先使用 SSH 端口转发或 Tailscale，不建议直接监听公网。
- 如果确实修改为非 loopback 地址，必须显式配置 `ALLOW_REMOTE_ACCESS=true`；这只解除启动保护，
  **不等于服务已经具备公网鉴权、TLS 或防火墙**。
- 运行日志写入 `data/logs/bestprice.log` 并自动轮转。数据库、日志、登录状态和 `.env` 均不应提交 Git。

升级或修改 schema 前先备份：

```bash
scripts/backup-sqlite.sh
git pull --ff-only
scripts/setup.sh
# 再由你的进程管理器重启，或重新运行 scripts/start-local.sh
```

备份使用 SQLite `VACUUM INTO`，随后立即检查完整性和核心表行数；不会覆盖已有备份文件。

离线验收不会访问闲鱼：

```bash
.venv/bin/python -m pytest -q
```

真实 smoke 会访问平台，只在你明确需要时运行：

```bash
scripts/smoke-local.sh "富士 X-T4" 1
RUN_ID=<已有run_id> scripts/smoke-local.sh "富士 X-T4" 1  # 复用结果，不重新请求
```

---

## 用户怎么使用

### 最简单：一条命令查询

```bash
scripts/query-price.sh "富士 X-T4" --pages 2
scripts/query-price.sh "RTX 4090" --pages 1 --min-price 2000
scripts/query-price.sh "27寸 4K 144Hz 显示器" --pace economy
```

保存完整 JSON，适合交给 Agent 或后续分析：

```bash
scripts/query-price.sh "富士 X-T4" \
  --pages 2 --format json --output /tmp/x-t4.json
```

恢复一次已提交或超时的任务，不重新访问平台：

```bash
scripts/query-price.sh --reuse-run <run_id> --format json
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--pages N` | 闲鱼采集页数，不超过 `MAX_SEARCH_PAGES` |
| `--sort` | `newest`、`price_asc`、`price_desc` 或 `default` |
| `--min-price` / `--max-price` | 价格范围，单位为元 |
| `--pace` | `economy`、`balanced` 或 `fast`，不绕过安全下限 |
| `--force-refresh` | 跳过结果缓存，但仍受节流和人工阻断限制 |
| `--reuse-run` | 读取已有 run，不重新搜索 |
| `--format json` | 自动遍历本地分页，输出所有商品和完整描述 |
| `--output FILE` | 把报告或 JSON 保存到文件 |
| `--timeout` | 控制客户端等待时长；超时后仍可用 run_id 恢复 |

退出码：`0` 成功、`2` 服务或参数问题、`3` 采集失败、`4` 等待超时。

### 直接使用 HTTP API

单次查询的基本流程：

```text
POST /v1/search
  → GET /v1/search-runs/{run_id} 轮询
  → GET /v1/products?run_id=...
  → GET /v1/stats?run_id=...
```

购物研究流程会在此基础上增加：

- `POST /v1/researches`：创建指定型号、品类探索或具体挂牌核查任务。
- `POST /v1/researches/{id}/runs`：关联搜索 run 并消耗声明的研究预算。
- `POST /v1/researches/{id}/candidates`：记录用户指定、Agent 提议或市场发现的型号。
- `POST /v1/text-analyses`：提取带原文证据的商品类型、价格和风险信号。
- `POST /v1/evaluations`：保存可重放的挂牌评估。
- `GET /v1/researches/{id}/comparable-stats`：计算同 SKU 可比二手挂牌分布。
- `POST /v1/new-prices/quotes`：导入可追溯的新品报价。
- `POST /v1/evaluations/{id}/quote-match`：确认 SKU 关系并计算新旧总成本价差。
- `GET /v1/researches/{id}/ranked`：读取型号层和挂牌层分组结果。

下面的 [API](#api) 章节包含请求、响应、分页、价格口径和错误码的完整说明。

---

## 让 Agent 使用 Best Price

Agent 可以直接调用命令行/API，也可以使用项目自带 Skill。推荐使用 Skill，因为它会约束 Agent：
先确认用户需求和请求预算，再查询；区分原始统计与可比统计；保留证据、链接和不确定性；遇到登录、
验证码或限流立即停止。

### 方式一：在本仓库中使用项目 Skill

主 Skill 位于 [`.agents/skills/best-price/SKILL.md`](./.agents/skills/best-price/SKILL.md)。
支持项目级 Skill 的 Agent 在打开本仓库后会自动发现它。可以直接这样说：

```text
使用 best-price Skill，帮我调查 3000 元左右适合旅行航拍的二手无人机。
预算不是硬上限，可以给少量 Extra；最多搜索 6 次，慢慢找。
```

或者显式触发：

```text
$best-price 帮我检查这条闲鱼 X-T4 挂牌是否值得买，并对照同配置新品。
```

旧客户端如果仍发现 `price-parser`，它只会引导到新的 `best-price` Skill。

### 方式二：让其他 Agent 读取 Skill

如果 Agent 客户端不支持 `.agents/skills/` 自动发现，把下面这句话作为任务的一部分：

```text
先完整读取 .agents/skills/best-price/SKILL.md，只按当前 /help、/openapi.json 和
/v1/capabilities 中实际存在的能力执行；不要根据 README 猜测端点。
```

需要跨项目复用时，再按具体客户端支持的用户级 Skill 目录安装整个
`.agents/skills/best-price/` 文件夹，不能只复制 `SKILL.md`，因为它还会按需读取
`references/` 和 `assets/`。安装后应让客户端列出可用 Skill，确认 `best-price` 已被发现。

### Agent 开始任务前应做什么

```bash
curl -sS http://127.0.0.1:8765/health
curl -sS http://127.0.0.1:8765/v1/capabilities
curl -sS "http://127.0.0.1:8765/help?format=text"
```

这三步分别确认服务在线、外部能力是否已配置，以及当前端点、错误处理和报告纪律。
OpenAPI 是最终接口事实来源；README 和 Skill 负责说明正确工作方法。

### 怎样让 Agent 更高效

给 Agent 的任务里尽量明确以下信息：

1. **任务模式**：指定型号、品类探索，还是检查一条具体挂牌。
2. **预算含义**：目标预算还是硬上限；例如“约 3000”与“最多 3000”不同。
3. **硬条件与偏好**：必须满足什么、哪些可以妥协、是否接受替代型号和 Extra。
4. **研究预算**：允许多少次闲鱼请求、每次最多几页、使用 `economy/balanced/fast` 哪个节奏。
5. **新品偏好**：例如“二手只便宜 10% 以内时优先考虑新品”。
6. **输出要求**：要求 Primary/Extra/Review 分组、链接、采集时间、样本量、风险和待人工核实项。

一个高质量提示词示例：

```text
使用 best-price Skill 调研 3000 元左右的二手无人机，主要用于旅行航拍。
硬上限 3500，必须功能正常并能完整起飞拍摄；便携和续航是偏好，允许推荐老旗舰。
最多 8 次闲鱼请求，每个关键词最多 2 页，pace=balanced。先做宽泛发现，再聚焦值得研究的型号。
同配置二手只比可核验新品便宜 10% 以内时提醒我考虑新品。
最终把型号判断和具体挂牌分开，输出 Primary、Extra、Review、来源链接、样本时间、停止原因和未验证信息。
```

提高效率的关键不是让 Agent 高频请求，而是：优先复用缓存和已有 run；先用宽泛词发现候选，
再对少量高价值型号做聚焦搜索；拿到数据后尽量在本地完成文本分析、去重和评估；超时后使用
`--reuse-run` 恢复，不重复提交同一查询。

### Agent 与 API 的职责边界

- API 负责采集、持久化、节流、来源、算术、证据校验和可重放的派生记录。
- Agent 负责理解用户需求、规划关键词、识别同款/同配置、调查型号知识和形成购买建议。
- 文本模型只负责结构化提取明确写在挂牌里的事实，不负责决定商品真假或替用户做最终选择。
- 用户负责登录、验证码、支付、联系卖家和最终交易决定；这些操作不会被自动化。

---

## 价格口径与安全红线

- 返回的是**采集时刻的公开在售报价**，不是成交价，也不自动包含国补或议价结果。
- `/v1/stats` 是**未筛选统计**；`sample_quality` 恒含 `unfiltered`。租赁、配件、求购、
  定金、拍卖和广告只要价格可解析，就会出现在原始算术中。
- 判断商品是否可比，需要读取完整描述、SKU、套装、状态和卖家公开信息；使用研究级
  `comparable-stats` 时必须先由 Agent 提交证据化评估。
- 采集失败不会伪装成 `200 + []`。字段缺失保持 `null`，不会补成“暂无”或让 AI 猜测。
- 金额内部一律以人民币分的整数保存；API 同时提供整数分和两位小数字符串。
- 不绕过登录、验证码、访问限制或平台风控，不轮换账号/IP继续采集。
- 不要把 Cookie、密码、短信验证码或 API Key 发给 Agent，也不要写入 URL、日志或 Git。

### 备份

```bash
scripts/backup-sqlite.sh
scripts/backup-sqlite.sh /tmp/before-upgrade.sqlite3
```

数据默认位于 `data/`，该目录已被 Git 忽略。自动清理目前未启用；被研究、评估或报价引用的
记录需要保留。商品图片只保存远程 URL，不会默认下载到本机。

### 进一步文档

- [架构与边界](./docs/architecture.md)
- [长期维护知识](./docs/know-how.md)
- [优化完成状态](./docs/optimization/status.md)
- [外部能力闸门](./docs/optimization/external-gates.md)
- [实施报告](./IMPLEMENTATION_REPORT.md)
- [原始实施指南](./Xianyu_Agent_Price_Service_Implementation_Guide.md)

---

## 登录（可选）

**实测未登录（guest）即可搜索**，登录不是必需项。只有需要登录态数据时才做，且**必须由你本人在本机终端完成**：

```bash
scripts/login.sh              # 终端画二维码，用闲鱼 App 扫；需要时自动开浏览器扫脸
scripts/login.sh --cookie     # 粘贴浏览器 Cookie（在本机终端里输入）
scripts/login.sh --browser    # 直接打开官方登录页
```

### 凭证记忆：一次登录，长期有效

登录成功后上游会把 `{cookies, device_id, user, user_id}` 写入
`upstream/xianyu_spider/data/session.json`（由 `mtop.persist_login()` 落盘）。
本服务每次 `init()` 都会 `load_session()` 把它读回内存，所以**扫一次码，之后重启服务都自动带登录态**。

服务运行期间在另一个终端登录了？不用重启：

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/auth/reload
```

它会重跑 `init()` 重新读取 `session.json`。（上游的 `login_snapshot()` 读的是内存 cookie jar，
不会自己感知磁盘变化，所以需要这个显式动作。）

### 不主动过期

本服务**不会**主动去平台校验登录态，也**不会**主动作废你的凭证。原因是上游
`probe_login()` 的行为：它在 `fetch_login_user()` 抛**任何**异常时都会走
`invalidate_expired_login()`，而后者 `client.cookies.clear()` 并 `clear_session()` ——
**直接删除 `session.json`**。也就是一次网络抖动就能毁掉你扫码换来的登录态，逼人重新扫脸。

取而代之：`/v1/auth/status` 只读纯内存快照，并返回 `verified: false` 如实表明未向平台校验。
凭证真的失效时，会在**搜索时**由平台返回的 `ret` 码被动反映为 `AUTH_EXPIRED`
（`requires_human_action: true`），那时再 `scripts/login.sh` 重新登录即可。
这是有证据的判定，不会因为一次抖动就误删凭证。

### 安全约束

- 不要把 Cookie、密码、短信验证码发给任何人，也不要粘贴进聊天。本服务不读取、不打印、不上传任何凭据。
- `session.json` 由 `scripts/login.sh` 自动 `chmod 600`，且被 `.gitignore` 双重排除（`upstream/` 整体 + `session.json`）。
- `/v1/auth/status` 只回传状态枚举，**绝不回传 Cookie 或 user_id 原值**。
- 首次扫脸核身需要 Chromium：`.venv/bin/python -m playwright install chromium`

---

## API

### 先读 `/help`：agent 自助发现

不需要事先读文档。`GET /help` 返回机器可读的完整使用说明：

```bash
curl -sS http://127.0.0.1:8765/help                 # JSON
curl -sS "http://127.0.0.1:8765/help?format=text"   # 纯文本，便于直接塞进模型上下文
curl -sS http://127.0.0.1:8765/                     # 根路径指路，不会 404
```

它比 OpenAPI 多给出三类**OpenAPI 表达不了**的信息：

- **价格口径纪律** —— 这是在售报价不是成交价、`null` 就是缺失而非占位、分位数算法、
  `must_report`（转述给用户时必须包含的 8 项）与 `must_not`（8 条禁止事项）。
  两者都已围绕「样本**未筛选**」重写：`must_report` 里明确要求转述 `unfiltered`、
  `unpriced_count` / `unpriced_by_status`、`auction_count` / `ad_count`；
  `must_not` 里明确禁止把 `stats` 的分布说成已清洗过的结果
- **透传字段清单**（`passthrough` 段）—— 分「平台原话」/「本服务解析结果」/「拿不到」三类，
  逐字段说明语义。「拿不到」里写明了多图为什么拿不到（见下文实测结论），省得调用方试错撞墙
- **错误码 → 行动指引** —— 当前 22 个错误码各自带 `http_status` / `retryable` /
  `requires_human_action` / `agent_action`，告诉调用方**下一步该做什么**，而不只是发生了什么

`/help` 不会与实际 API 漂移：端点清单从 `app.openapi()` 派生，请求字段清单从
`SearchSubmitRequest` 派生，错误码行动指引与 `scripts/query-price.sh` 共用
`errors.AGENT_ACTIONS` 同一份数据。另有测试直接遍历真实路由对象反向核对，
所以任何一方改动而另一方没跟上，测试就会红。

`city` / `province` / `publish_days` 在 `/help` 里被明确列为 `unsupported_filters`
并附原因，agent 不必靠试错去撞 422。`search_request.no_relevance_filters` 另有一句明示：
**本服务不接受任何相关性筛选参数**（历史上的 `item_kind` 已移除）。

### Agent 调用流程

**推荐**：直接用 `scripts/query-price.sh`（见「快速开始」），它已经实现了下面整套流程，
并把必须转述的口径与限制固化进输出。

需要自己编排时的原始流程：

```text
POST /v1/search  →  202 + run_id
   ↓
GET /v1/search-runs/{run_id}   轮询直到 succeeded / partial / failed / blocked_login
   ↓
GET /v1/products?run_id=...    本轮商品的完整平台字段与原始链接（主要产出）
GET /v1/stats?run_id=...       未筛选的中位数 / 分位数 / 样本量
   ↓
自己判断哪几条可比（读 description / seller.credit / signals.*）
   ↓
输出时务必带上：统计口径 + 样本量 + 「未筛选」这一事实 + 你自己剔了什么按什么剔
              + 报价区间 + 商品链接 + 采集时间与状态
```

### `POST /v1/search` → `202 Accepted`

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"RTX 4090","max_pages":1,"sort":"newest","pace":"balanced","cache_policy":"prefer_fresh"}'
```

```json
{
  "run_id": "026babbc-6e7e-4db0-bb83-691805172f64",
  "status": "pending",
  "status_url": "/v1/search-runs/026babbc-6e7e-4db0-bb83-691805172f64",
  "reused": false,
  "cache_hit": false
}
```

| 字段 | 约束 |
|---|---|
| `keyword` | 必填，非空，≤ 64 字符 |
| `max_pages` | 默认 1，上限 `MAX_SEARCH_PAGES`（默认 3） |
| `sort` | `newest`(默认) / `price_asc` / `price_desc` / `default`，对齐上游实测 `SORT_OPTIONS` |
| `min_price_yuan` / `max_price_yuan` | Decimal 字符串，≥ 0，min ≤ max |
| `pace` | `economy` / `balanced`(默认) / `fast`；偏好，不能越过服务端硬下限或风控状态 |
| `cache_policy` | `prefer_fresh`(默认) / `force_refresh`；强刷也不绕节流 |
| `idempotency_key` | 可选客户端追踪键；结果去重仍以规范化请求指纹为准 |
| `city` / `province` / `publish_days` | 上游 `SearchFilters` 支持，但**平台是否真过滤未经实测**，传非 null 一律 `422 UNSUPPORTED_FILTER`，不暗称已过滤 |

**没有 `item_kind`**（旧版的单机身/套机筛选已移除）。请求体是 `extra="forbid"`，
传它会得到 `422 INVALID_QUERY`，不会被静默忽略。

相同运行中请求返回同一 `run_id`；近期成功请求默认复用原 run 并返回 `cache_hit=true`。
队列达到 `MAX_PENDING_JOBS` 时返回 `429 QUEUE_FULL`，不会向平台发请求。任务生命周期：
单实例串行锁 + 应用内异步任务，**所有状态落库**。平台正常末页返回 `exhausted=true`，不再
误报 `partial`。进程崩溃重启后，遗留的
`pending`/`running` 会被判定为 `failed` + `RUN_INTERRUPTED`，不会永远挂着。

### `GET /v1/search-runs/{run_id}`

```bash
curl -sS http://127.0.0.1:8765/v1/search-runs/026babbc-6e7e-4db0-bb83-691805172f64
```

真实响应（run `026babbc…`，2026-09-22，`RTX 4090`，1 页，`adapter_version` 已是 `0.2.0`）：

```json
{
  "run_id": "026babbc-6e7e-4db0-bb83-691805172f64",
  "status": "succeeded",
  "platform": "xianyu",
  "keyword": "RTX 4090",
  "auth_mode": "logged_in",
  "pages_requested": 1,
  "pages_fetched": 1,
  "raw_count": 30,
  "distinct_count": 30,
  "priced_count": 30,
  "started_at": "2026-09-21T21:30:22.501814Z",
  "ended_at": "2026-09-21T21:30:22.888185Z",
  "warnings": [],
  "error": null,
  "adapter_version": "xps-xianyu/0.2.0",
  "source_commit": "eb52bd4d1901eee9ba8035e860583cddf50ead4c"
}
```

`raw_count`（原始条目）/ `distinct_count`（去重后商品）/ `priced_count`（价格能解析成数字的条目）
是**分层**计数。`priced_count` 由 `eligible_count` 改名而来 —— 语义是「有几条能参与算术」，
**不是**「有几条通过了筛选」，本服务不筛选。
`source_commit` 让你事后能判断这轮数据是哪个上游版本抓的。

> 迁移注意：`search_runs.eligible_count` 是用 `ALTER TABLE … RENAME COLUMN` 改名的，
> 所以**改造前**跑的老 run，这一列里躺着的仍是旧筛选口径的数值（例如 run `b1ad3465…` 显示
> `priced_count: 14`，而 `/v1/stats` 对同一 run 重算出的是 `60`）。`/v1/stats` 永远按当前代码
> 从 `observations` 现算，不受影响；要老 run 的正确计数就以 `/v1/stats` 为准。

### `GET /v1/products`

**这是本服务的主要产出。**

```bash
curl -sS "http://127.0.0.1:8765/v1/products?run_id=<RUN>&priced_only=true&limit=50&offset=0"
```

`limit` 默认 50、上限 100；返回 `total` 以便分页。
`priced_only`（旧名 `eligible_only`，传旧名会 **422**）的语义是**「价格能解析成数字」**，
不是相关性筛选；被它挡掉的条目用 `priced_only=false` 照样能取到价格原文。

每个 item 的结构（真实响应，run `026babbc…`，长文本已截断并标注）：

```json
{
  "product_id": 354,
  "source_run_id": "026babbc-6e7e-4db0-bb83-691805172f64",
  "observed_at": "2026-09-21T21:30:22.881190Z",
  "canonical_url": "https://www.goofish.com/item?id=1085864453438",
  "title": "华硕DUAL RTX4060-08G-V2显卡，99.9新 塑封膜都没揭！！！8G显存双风扇，国行正…（截断）",
  "description": "华硕DUAL RTX4060-08G-V2显卡，99.9新 塑封膜都没揭！！！\n8G显存双风扇，国行正品在保到2028年3月。\n成色几乎全新，功能正常，无拆无修，插上即用！\n厦门翔安区附近自提，外地可邮寄\n喜欢直接拍，细节私聊！（截断，原文含换行分段）",
  "price": {
    "raw": "¥2289", "yuan": "2289.00", "fen": 228900,
    "parse_status": "valid", "original_text": null, "coupon_text": null
  },
  "seller": {
    "display_name": "天马星朦胧的狼牙鱼", "credit": "卖家信用极好",
    "review_count": 611, "positive_rate": "75%",
    "identity": null, "avatar_url": "http://img.alicdn.com/bao/uploaded/i1/O1CN01Cykm7k1nzKoDGTr86_!!4611686018427383608-0-mtopupload.jpg"
  },
  "area": "福建",
  "media": {
    "image_url": "http://img.alicdn.com/bao/uploaded/i4/2691215160/O1CN01NKDsgdChbNE1klhQ_!!4611686018427383608-0-xy_item.jpg",
    "has_video": false
  },
  "published_at": "2026-09-21T07:31:21Z",
  "signals": {
    "published_text": "13小时前发布", "want_count": 4, "free_shipping": true,
    "labels": [], "is_auction": false, "is_ad": false
  }
}
```

注意这条真实样本：搜「RTX 4090」，平台返回的第一低价却是一张 **RTX 4060**（¥2289）。
本服务**不会**替你把它剔掉 —— `description` 里写得清清楚楚，判断是你的事。

**可见字段缺失即 `null`**，不会插入「暂无」「未知」「匿名卖家」之类的占位伪数据
（上游 `safe_get(default="暂无")` 的行为被本项目显式避开）。
改造前入库的老 observation，新增字段（`description`、`seller.credit` 等）为 `null` ——
这是诚实的：那些字段当时没采集。

### `GET /v1/stats`

```bash
curl -sS "http://127.0.0.1:8765/v1/stats?run_id=<RUN>"
```

`run_id` **必填** —— 默认绝不跨日期或跨关键词混合历史数据。
**没有 `item_kind` 参数**，传它会 422（见「防呆」）。

真实响应（run `026babbc…`，`RTX 4090`，1 页，`lowest_items`/`highest_items` 标题已截断）：

```json
{
  "run_id": "026babbc-6e7e-4db0-bb83-691805172f64",
  "keyword": "RTX 4090", "run_status": "succeeded", "currency": "CNY",
  "partial": false, "auth_mode": "logged_in",
  "raw_count": 30, "distinct_count": 30,
  "priced_count": 30, "unpriced_count": 0, "unpriced_by_status": {},
  "auction_count": 0, "ad_count": 0,
  "min_yuan": "2289.00", "p25_yuan": "8249.75", "median_yuan": "23250.00",
  "p75_yuan": "24500.00", "max_yuan": "30500.00",
  "min_fen": 228900, "p25_fen": 824975, "median_fen": 2325000,
  "p75_fen": 2450000, "max_fen": 3050000,
  "lowest_items": [
    {"product_id": 354, "price_yuan": "2289.00",
     "title": "华硕DUAL RTX4060-08G-V2显卡，99.9新 塑封膜都没揭！！！8…（截断）",
     "canonical_url": "https://www.goofish.com/item?id=1085864453438"}
  ],
  "highest_items": [
    {"product_id": 336, "price_yuan": "30500.00",
     "title": "全新 RTX4090 涡轮 48GB 算力卡 显卡 135mm高，ai服务器专用…（截断）",
     "canonical_url": "https://www.goofish.com/item?id=1085970977824"}
  ],
  "insufficient_sample": false,
  "sample_quality": ["unfiltered", "single_page_only"],
  "started_at": "2026-09-21T21:30:22.501814Z",
  "ended_at": "2026-09-21T21:30:22.888185Z"
}
```

`lowest_items` 与 `highest_items` 各列 **3 件**（照实列出，不做异常值剔除），
让调用方自己看见分布两端是什么东西。上例最低价那 3 件里有 2 件是 RTX 4060 —— 这就是「未筛选」的字面意思。

金额同时给 `_yuan`（保留两位的字符串）与 `_fen`（整数）两种形态，取用哪个都不会引入浮点误差。

### `GET /health`、`GET /v1/auth/status`、`POST /v1/auth/reload`

```bash
curl -sS http://127.0.0.1:8765/health
# {"status":"ok","database":"ok","version":"0.4.0","adapter":"XianyuUpstreamAdapter"}

curl -sS http://127.0.0.1:8765/v1/auth/status
# {"state":"guest","requires_human_action":false,"verified":false,
#  "hint":"未登录（guest 可搜索）；如需登录态请在本机运行 scripts/login.sh 后调用 POST /v1/auth/reload"}

curl -sS -X POST http://127.0.0.1:8765/v1/auth/reload   # 登录后免重启
```

- `/health` 只看进程与本地 SQLite；**登录态丢失不算进程不健康**。
- `verified: false` 表示未向平台主动校验 —— 这是刻意的，见上文「不主动过期」。
- `/v1/auth/reload` 只接受 POST（GET 返回 405），避免被预取式请求意外触发；它不修改也不删除任何凭证文件。

### 防呆：已移除的筛选参数会**显式 422**

`/v1/products` 与 `/v1/stats` 不接受以下参数，并且**不会像 FastAPI 默认那样静默忽略**：

| 参数 | 为什么拒 |
|---|---|
| `item_kind` | 本服务不再做单机身/套机之类的相关性筛选 |
| `eligible_only` | 已改名 `priced_only`，且它只表示「价格能解析成数字」，不是相关性筛选 |
| `exclude_rental` | 本服务不排除任何条目，租赁盘照常返回 |
| `flags` | 分类标签体系已移除 |

理由是：拿着旧接口记忆传 `item_kind=body` 的调用方会**误以为结果已经筛过**，那是谎报口径。
静默忽略比报错危险得多。返回体形如：

```json
{"code": "INVALID_QUERY",
 "message": "以下参数已移除，不会被静默忽略：item_kind（本服务不再做单机身/套机之类的相关性筛选）。请改用 /v1/products 读原始字段自行判断可比性。",
 "run_id": null, "retryable": false, "requires_human_action": false}
```

实现在 `src/xps/api/deps.py` 的 `reject_removed_filters`，由 `tests/test_api_contract.py::test_relevance_filters_are_not_accepted_at_all` 守护。

---

## 统计方法（口径固定，可复现）

**样本准入**：仅当前 `run_id` + 去重后 + `price_parse_status = 'valid'` 的商品。
**就这三条，没有相关性条件。** 租赁盘、拍卖起拍价、配件、求购盘、广告位只要价格能解析成数字，
一律留在样本里。价格解析不出来的条目**不删除**，原文照样从 `/v1/products` 返回，
并按 `ambiguous` / `missing` / `invalid` 归入 `unpriced_by_status` 上报。

**分位数**：inclusive 线性插值，与 `statistics.quantiles(n=4, method='inclusive')` 等价，
但**全程用 `Decimal` 实现**，避免 float 进入金额路径：

```
位置 = (样本数 - 1) × i / 4          i ∈ {1, 2, 3} → P25, P50, P75
落在两点之间时按小数部分线性插值
结果按 ROUND_HALF_UP 取整到分
中位数：奇数取中间值；偶数取中间两值均值（可能落在 .5 分，同样 ROUND_HALF_UP）
样本数为 1 时只给 min/median/max，P25/P75 为 null
```

为什么不用 float：`float(1.15) * 10000 == 11499.999999999998`，`int(float("1154.99") * 100) == 115498`，都会少一分。

**没有异常值剔除。** 旧版的 IQR 下界围栏（`Q1 − 1.5 × IQR` 以下标 `suspicious_price` 并移出样本）
已随分类层一起删除 —— 那同样是筛选，而且它剔掉的恰好是最需要被看见的东西（¥40 的日租盘）。
现在最低/最高各 3 件照实列在 `lowest_items` / `highest_items` 里，让调用方自己看见两端。
`tests/test_stats.py::test_a_cheap_outlier_stays_in_the_arithmetic` 专门守着这条，
防止 IQR 剔除悄悄回来。

**`insufficient_sample`**：`priced_count` < `MIN_SAMPLE_THRESHOLD`（默认 8）时为 `true`。
统计仍展示已知样本，但不产出「市场公允价」。8 是 MVP 告警阈值，不是统计学保证。

**`sample_quality`** 会列出本轮的明确限制，取值全部来自 `src/xps/services/statistics.py`：

| 取值 | 何时出现 |
|---|---|
| `unfiltered` | **恒定出现**，第一项。任何转述都不得省略 |
| `insufficient_sample` | `priced_count` < 8 |
| `includes_auction_start_prices` | 本轮存在 `is_auction` 条目 |
| `includes_promoted_ads` | 本轮存在 `is_ad` 条目 |
| `some_prices_unparsed` | 有条目价格解析不出（见 `unpriced_by_status`） |
| `single_page_only` | `pages_requested` ≤ 1 |
| `partial_pages:已抓/请求` | `pages_fetched` < `pages_requested` |
| `guest_auth` / `unknown_auth` | `auth_mode` 为 `guest` / `unknown` |
| `status_xxx` | run 状态不是 `succeeded`（如 `status_partial`） |

---

## 服务端不筛选：为什么，以及你该怎么自己判断

旧版有一个 `src/xps/services/classify.py`，用中文词表规则给每条商品打标签
（`accessory_only` 配件、`rental_or_lease` 租赁、`model_mismatch` 型号不符、`unknown_variant` 待核验等），
命中标签就从统计样本里排除。**这一层已于 2026-09-22 整体删除**，
`src/xps/services/classify.py` 与 `tests/test_classify.py` 都不存在了。

### 为什么删：词表只对相机成立

那套规则靠「单机身 / 套机 / 机身盖 / 遮光罩」这类**相机词汇**判断商品配置。
非相机品类根本没有这些词，于是所有条目都被打成 `unknown_variant` 送进 review，统计样本直接归零。
更糟的是 `model_mismatch` 的逻辑：「标题里没原样出现关键词的型号 token 就排除」——
这是把**证据缺失当成了证据存在**。搜「xs10相机」时，标题写作「XS10」「X-S10」「xs-10」的
全部被判为型号不符。

下表是从仓库里真实的 SQLite 库（`data/price.sqlite3`，含此前多轮真实抓取的历史数据）查出的
旧筛选器实际战果。「旧合格」= 旧筛选器允许进入统计的条数；「新有价」= 现版本代码在**同一批已入库数据**上
算出的 `priced_count`（本文档撰写时用当前 `compute_stats` 对库中每一轮重算得到）：

| 关键词 | 总条数 | 旧合格 | 新有价 | 旧筛选器主要排除原因 |
|---|---|---|---|---|
| 富士 X-T4 | 60 | 14 | 60 | `rental_or_lease` 22、`deposit_or_placeholder` 21、`needs_review` 15、`wanted_to_buy` 6、`model_mismatch` 3 |
| 富士 X-T4 | 30 | 4 | 30 | `rental_or_lease` 16、`deposit_or_placeholder` 15、`needs_review` 6、`accessory_only` 3、`model_mismatch` 2 |
| xs10相机 | 59 | **0** | 58 | **`model_mismatch` 59（全部）**、`rental_or_lease` 30、`accessory_only` 11 |
| 富士 XS10 | 90 | 52 | 90 | `accessory_only` 21、`needs_review` 12、`model_mismatch` 2 |
| 27寸 4K 144Hz 显示器 | 60 | 4 | 60 | **`needs_review` 44**、`repair_or_fault` 8、`model_mismatch` 4 |
| 27寸4K显示器 电竞 | 90 | **0** | 90 | **`model_mismatch` 87**、`needs_review` 3 |
| 富士X-T4 | 30 | 15 | 30 | `model_mismatch` 11、`needs_review` 3 |

两个显示器关键词几乎全军覆没（90 条 → 0 条、60 条 → 4 条），`xs10相机` 59 条**全部**被
`model_mismatch` 干掉。这就是删掉它的实证依据。
（表中「旧合格」取自老库 `search_runs.eligible_count` 的历史落库值，「排除原因」按
`observations.exclusion_reasons_json` 与 `needs_review` 逐条聚合而来；这些列在老库里物理上仍在，
只是代码不再读写。「新有价」与下文的中位数均为现版本代码在同一批 `observations` 上重算的结果。）

### 代价：不筛选意味着分布里混进了不可比条目

**这不是免费的。** 同一批真实数据上，现版本算出的**未筛选**中位数：

| 关键词 | 未筛选中位数 | 为什么偏离 |
|---|---|---|
| 富士 X-T4（30 条那轮） | **¥80.00** | 那一轮里全是 ¥40 / ¥50 / ¥90 的日租盘 |
| xs10相机 | **¥325.00** | 混进了大量配件 |
| 27寸 4K 144Hz 显示器 | ¥233.50 | — |
| 27寸4K显示器 电竞 | ¥820.00 | — |
| 富士 XS10 | ¥5000.00 | — |
| 富士X-T4 | ¥5399.00 | — |

「富士 X-T4 中位数 ¥80」显然是荒谬的 —— 但它**如实反映了那一轮平台返回的东西**。
所以新契约是：**服务端只给事实和算术，判断可比性是调用方（agent）的责任。**
把 ¥80 直接报给用户是错的；假装服务端已经筛过、于是把中位数当行情报出去，同样是错的。
正确做法是读 `/v1/products`，说明你剔了哪几条、按什么文本证据剔的。

### 你有足够字段做这个判断

以下字段的覆盖率基于 `data/probe/raw_富士_X-T4_p1a_p1.json` + `…_p1b_p1.json`
两页共 **60 条真实抓取样本**（本文档撰写时用当前适配器重新解析这两个文件核对过）：

| 字段 | 平台来源 | 实测覆盖 | 用途 |
|---|---|---|---|
| `description` | `detailParams.title` | 60/60 有值，**42/60 含换行**（`exContent.title` 含换行 **0/60**），长度 46–1511 字 | 与 `title` 同文但保留分段。**判断是不是租赁 / 配件 / 求购主要靠它** |
| `title` | `exContent.title` | 60/60 | 搜索页展示的单行标题 |
| `seller.credit` | `fishTags.r4[].data.content` | 56/60：「卖家信用极好」42、「卖家信用优秀」14 | 卖家可信度 |
| `seller.review_count` / `seller.positive_rate` | `userFishShopLabel.tagList[].data.content` | 60/60 | 形如「318条评价」「好评率39%」→ 解析为 `318` / `"39%"` |
| `area` | `exContent.area` | 60/60 | 卖家所在地，**只到省市**（平台不给更细） |
| `signals.published_text` | `fishTags.r2` | 60/60 | 形如「8小时前发布」 |
| `signals.want_count` | `fishTags.r3` | 6/60 | 形如「8人想要」 |
| `price.coupon_text` | `fishTags.r3` | 10/60 | 形如「券已抵50元」。**关系价格口径：展示价可能已扣券** |
| `signals.free_shipping` | `fishTags.r1` 的 `freeShippingIcon` | 32/60 | 是否包邮 |
| `signals.labels` | `fishTags.r1` 其余标签 | 「严选」10/60、「验货宝」4/60 | 徽标原文，不推断含义 |
| `seller.identity` | `exContent.userIdentityShow` | 10/60 | 「闲鱼严选卖家」 |
| `price.original_text` | `exContent.oriPrice` | 12/60 | 划线原价 |
| `seller.avatar_url` | `exContent.userAvatarUrl` | 60/60 | 卖家头像 |
| `media.has_video` | `exContent.showVideoIcon` | 4/60 为真 | 是否带视频 |
| `media.image_url` | `exContent.picUrl` | 60/60 | **主图，只有 1 张**（见下） |
| `signals.is_auction` / `signals.is_ad` | `exContent.isAuction` / `isAliMaMaAD` | 该批 0/60、0/60 | 平台事实标记：起拍价 / 广告位 |

另核实过：`detailParams.soldPrice` 与价格控件三段拼接（`sign` / `integer` / `decimal`）
在 **60/60** 条上一致（`soldPrice` 是不带 `¥` 的数值形态）。因此价格仍沿用已测过的三段解析，
**没有**引入 `soldPrice` 这个冗余来源。

### 保留下来的（不是「全删了」）

删掉的只是**相关性判断**。以下这些一件没动：

- **`parse_price`**：全程 Decimal 的价格解析，以及 `valid` / `ambiguous` / `missing` / `invalid`
  四态。**这不是筛选** —— 解析不出数字时原文照样保留（`price.raw`）、条目照样返回，
  只是不参与算术，并按状态计入 `unpriced_by_status`。
- **`resolve_identity`**：身份键与去重（平台商品 ID → 可信 URL 稳定参数 → 剥跟踪参数后的 URL SHA-256）。
- **既无平台 ID 又无可信 URL 的条目仍不入库**（`storable=False`）：它们的 `identity_key`
  会全部碰撞成 `sha256("")` 同一行、静默吞掉数据。这是**数据完整性约束**，不是相关性筛选。
- 单实例串行锁、逐页节流、任务状态全落库、进程重启判定 `RUN_INTERRUPTED`、只监听 loopback、
  **失败不退化成 `200 + 空列表`**。
- 上游许可红线：`upstream/xianyu_spider` 无 LICENSE 文件，**永不 vendor** 进仓库，
  只以独立 checkout + 进程内 import 调用。

### 拿不到的东西（别试错撞墙）

- **商品多张图片**：搜索响应每件商品**只有 1 张主图**。从闲鱼 PC 站前端 bundle
  `https://g.alicdn.com/idle-pc/xy-site/0.0.175/js/p_item-index.js` 里核实出真实详情接口是
  `mtop.taobao.idle.pc.detail`，版本 `1.0`，入参 `{itemId}`。
  但 2026-09-22 用 guest 身份实调，返回 `RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试!`
  —— 这是阿里的风控/滑块挑战码。按项目红线**立即停手，没有重试、没有绕过**。
  结论：**未登录拿不到多图；登录后能否拿到未实测。**
- **商品详情页 HTML**：`https://www.goofish.com/item?id=…` 与 `https://h5.m.goofish.com/item?id=…`
  都是客户端渲染空壳（分别 10574 / 3983 字节），HTML 里没有内嵌商品数据。
- **成交价 / 历史价格趋势**：平台搜索接口不返回，本服务也不做跨轮历史留存统计。
- **精确到区县的地址**：平台只给到省市（`area`）。

### 转述纪律

引用本服务的数字给用户时，**必须**包含（与 `GET /help` 的 `must_report` 同源）：

1. 价格口径：采集时刻的公开在售报价，不是成交价
2. 样本量 `priced_count`，以及 `insufficient_sample` 是否为真
3. **样本未筛选**：`sample_quality` 恒含 `unfiltered`，租赁盘 / 拍卖起拍价 / 配件 / 广告位都在分布里
4. 无价条目：`unpriced_count` 与 `unpriced_by_status`
5. 平台标记：`auction_count` 与 `ad_count`
6. 可追溯链接：`lowest_items` 或商品列表里的 `canonical_url`
7. 采集时间与状态：`started_at` / `ended_at` / `status` / `auth_mode` / `partial`
8. `sample_quality` 数组原样转述

**禁止**：把挂牌价说成成交价或「市场行情 / 公允价」；把 `stats` 的分布说成已清洗过的结果；
把 `status=failed` 读成「平台没有商品」；跨 `run_id` 混合历史数据凑样本量；
`insufficient_sample=true` 时给确定性结论；用图片或大模型推断未公开的商品参数补齐规格；
`requires_human_action=true` 时自动重试。

你自己做筛选时，依据**只能**是上表这些字段里的文本证据（`description` 写明租赁 / 配件 / 求购、
`signals.is_auction` 为真等），并且要在回答里说明剔除了几条、为什么。不得凭空推断。

---

## 商品身份

优先级（指南 §7）：

1. `exContent.itemId` → `identity_key = "xianyu:item:<id>"`。实测跨两轮搜索 30/30 完全一致。
2. 可信 URL 里的 `id` 参数（同样归为 `platform_item_id`）；再后备：剥除跟踪参数
   （`referPageArgs`/`gulSource`/`extra`/`spm`…）后的规范化 URL 取 SHA-256
   → `identity_key = "xianyu:url:<sha256>"`，`id_source = "url_fingerprint"`
3. 都不成立 → `id_source = "invalid_identity"`

域名白名单：`goofish.com` / `www.goofish.com` / `h5.m.goofish.com`；实测真实形态
`fleamarket://item?id=...` 会被转换为 `https://www.goofish.com/item?id=...`。
短链、非 http(s)、不可信主机一律拒绝。

第 3 类的处置**不是相关性筛选，而是数据完整性约束**（`normalize_listing` 的 `storable` +
`Repository.store_listings` 的两个计数）：

- 既无平台 ID 又无可信 URL（闲鱼适配器此时造不出 `canonical_url`）→ `storable=False`，**不入库**，
  只累加计数并写成 run 警告 `skipped_unidentifiable:N`。原因是它们的 `identity_key`
  会全部碰撞成 `sha256("")` 同一行，静默吞掉数据。
- 若条目仍带着 URL 而身份判定为不可信，则**照常入库**（它是平台真实返回的），
  `canonical_url` 为 `NULL`，另计 `untrusted_identity:N` 并写成 run 警告 ——
  否则调用方会以为每条都有可点开的追溯链接。

**刻意不采用**上游 `get_link_unique_key()` 的 `link.split("&", 1)[0]` 再 md5：本次恰好因为 `id`
是第一个参数而侥幸稳定，参数顺序一变就会碰撞或漏匹配（指南 §7 明令禁止）。

---

## 数据模型

```
search_runs   每次真实搜索任务（状态、分层计数、错误码、警告、采集来源）
products      商品实体，identity_key UNIQUE；first_seen_at 永不变，last_seen_at 前移
observations  本次实际观察快照，UNIQUE(run_id, product_id)
run_items     视图 = SELECT run_id, product_id FROM observations
```

`observations` 的 `UNIQUE(run_id, product_id)` 兼任 `run_items` 关系（指南 §6 明确允许），
避免双表重复储存没有用途的信息。同轮重复分页**以首次观察为准**，`page_number` 记录首见页码。

这解决了一个关键 bug：上游 `POST /search/` 只返回 `new_record_ids`，而**实测重抓时
30/30 全是老商品**，上游会报 `new_records=0` —— 拿它当本轮结果会得到空列表。

约束：`PRAGMA foreign_keys=ON`、WAL、单进程串行写、迁移幂等（`IF NOT EXISTS` + `user_version`）。

隐私：只存公开的卖家昵称、信用标签、评价数、好评率、身份标识与头像 URL。
**不存** Cookie、token、手机号、精确住址、私信。

与指南 §6 表格的两处有意偏离（都为遵守优先级更高的 §0.4「严禁占位伪数据」）：
`products.title_latest` 与 `products.canonical_url` 改为可空 —— 平台未给标题时存 `NULL`，
不填「暂无」；身份不可信的条目 `canonical_url` 为 `NULL`，但保留记录以便追溯。

### `SCHEMA_VERSION = 2`（2026-09-22 增量迁移）

`observations` **新增 15 列**，用来落地上文的透传字段：
`description`、`original_price_text`、`coupon_text`、`seller_credit`、`seller_review_count`、
`seller_positive_rate`、`seller_identity`、`seller_avatar_url`、`has_video`、`published_text`、
`want_count`、`free_shipping`、`labels_json`、`is_auction`、`is_ad`。

**不再写入**的列：`flags_json`、`item_kind`、`excluded`、`exclusion_reasons_json`、`needs_review`
（都是旧分类器的输出）。这些列在**老库里物理上仍然存在** —— 迁移是**加法**的，
SQLite 删列要重写整表，为几列死数据冒这个险不值当；代码不再读写它们。
新建库的 `schema.sql` 里已经没有这些列。

`search_runs.eligible_count` 通过 `ALTER TABLE … RENAME COLUMN` 改名为 `priced_count`
（语义没变：能参与算术的条目数，只是不再用「合格」这种带筛选意味的词）。

迁移逻辑在 `src/xps/storage/db.py` 的 `_migrate_additive`，**幂等**。已在真实库
（`data/price.sqlite3`）的副本上验证：

- `PRAGMA user_version` 1 → 2，`PRAGMA integrity_check` 返回 `ok`
- **332 个 products / 419 个 observations 全部保留**，行数迁移前后一致
- 重复执行无副作用（`ALTER TABLE ADD COLUMN` 前先查 `PRAGMA table_info`）
- 老 run 仍能正常读出；老行的新字段为 `NULL` —— 这是诚实的，那些字段当时没采集

### `SCHEMA_VERSION = 3`（2026-09-23 增量迁移）

- `search_runs.request_fingerprint`：运行中合并与近期成功 run 缓存复用。
- `search_runs.exhausted`：平台正常末页的明确状态，不与失败页混淆。
- `scheduler_state`：跨重启保留最后平台请求、限流冷却和人工处理停止状态。

迁移仍为加法、可重复执行；本次没有改写既有 product/observation 行。

### `SCHEMA_VERSION = 4`（购买研究派生层）

新增 researches/search_profiles/research_runs/model candidates、文本分析、挂牌评估与证据、
风险、反馈、零售报价和 SKU 匹配表。所有表通过外键引用原始 run/product/observation；
AI、评分和用户偏好不会写进 `products` 或 `observations`。

改造前**先备份**：`scripts/backup-sqlite.sh`（`VACUUM INTO` + 立即 `integrity_check` + 核对行数，
目标文件已存在时拒绝覆盖）。

---

## 故障处理

统一错误体：`{code, message, run_id, retryable, requires_human_action}`。

| code | HTTP | retryable | 需人工 | 你该做什么 |
|---|---|---|---|---|
| `INVALID_QUERY` | 422/404 | ✗ | ✗ | 检查参数；404 表示 run_id 不存在；传了已移除的筛选参数（`item_kind` / `eligible_only` / `exclude_rental` / `flags`）也是这个码，见「防呆」 |
| `UNSUPPORTED_FILTER` | 422 | ✗ | ✗ | 该筛选未经验证，去掉它 |
| `AUTH_REQUIRED` | 401 | ✗ | ✓ | 运行 `scripts/login.sh` |
| `AUTH_EXPIRED` | 401 | ✗ | ✓ | 运行 `scripts/login.sh` 重新登录，再 `POST /v1/auth/reload`（免重启）；guest 搜索仍可继续 |
| `CHALLENGE_REQUIRED` | 409 | ✗ | ✓ | **停止自动操作**，到闲鱼 App 完成验证 |
| `RATE_LIMITED` | 429 | ✓ | ✓ | 停止重试，调大 `MIN_SECONDS_BETWEEN_SEARCHES` |
| `UPSTREAM_CHANGED` | 502 | ✗ | ✓ | 平台/上游结构漂移，重跑 `scripts/verify_upstream.py` |
| `UPSTREAM_TIMEOUT` | 504 | ✓ | ✗ | 有限次重试，勿并发放大 |
| `UPSTREAM_UNAVAILABLE` | 503 | ✓ | ✗ | 检查网络、上游 checkout、依赖是否装齐 |
| `DB_ERROR` | 500 | ✓ | ✗ | 看日志；必要时从 `data/backups/` 恢复 |
| `NO_VALID_RESULTS` | 200 | ✗ | ✗ | 本轮没有任何价格能解析成数字的条目。看 `unpriced_by_status`：`missing` = 平台没给价格控件，`ambiguous` = 给的是「面议 / 租金 / 区间」。**这不是被筛选掉的**，本服务不做相关性筛选；考虑换关键词，或用 `/v1/products`（不带 `priced_only`）读原文自行判断 |
| `RUN_INTERRUPTED` | 200 | ✗ | ✗ | 进程曾崩溃，重新发起搜索 |

出现 `CHALLENGE_REQUIRED` / `RATE_LIMITED` 时，适配器会**立即停止翻页**，不继续撞。
不要靠调小节流参数或更换账号/IP 绕过。

`partial` 状态表示部分页成功：`/v1/stats` 会同时返回 `partial=true` 与
`sample_quality` 里的 `partial_pages:已抓/请求`。

### 连本机却拿到 502？（macOS 系统代理）

`scripts/query-price.sh` 已内置 `trust_env=False`，不会踩这个坑。但如果你自己写 Python 客户端：

httpx 默认 `trust_env=True`，会调 `urllib.request.getproxies()`。在 macOS 上该函数读**系统代理**
且**不应用** ExceptionsList（bypass 列表），所以即使系统设置里已把 `127.0.0.1` 排除，
httpx 仍会把本机请求塞给代理（实测 `127.0.0.1:7890`），拿回 **502**，看起来像服务挂了。
`curl` 会自己应用 bypass，所以 curl 通、httpx 不通。

```python
httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False)   # 只连本机，忽略代理
```

---

## 安全与部署

- API 默认只监听 `127.0.0.1`。`APP_HOST=0.0.0.0` 而未设 `ALLOW_REMOTE_ACCESS=true` 会**启动失败** ——
  这是刻意的，不接受「只改成 0.0.0.0 就宣布安全」。
- 跨设备访问请优先 Tailscale / SSH 隧道，而不是暴露端口。
- 不启动上游的 HTTP 服务（它默认绑 `0.0.0.0`）。本项目以进程内 import 调用其函数，少一个对外监听面。
- `.gitignore` 已排除 `.env`、`data/`、`*.sqlite3`、`session.json`、`upstream/`。

---

## 测试

分层：

| 层 | 命令 | 数据 |
|---|---|---|
| 单元 + API 契约 | `.venv/bin/python -m pytest -q` | 合成 fixture，离线，默认运行 |
| 真实集成 | `.venv/bin/python -m pytest -m live -q` | 真实闲鱼，**默认排除**，手动低频 |
| Gate A 探针 | `.venv/bin/python scripts/verify_upstream.py --keyword "富士 X-T4" --pages 2` | 真实闲鱼 |

`pyproject.toml` 里 `addopts = '-m "not live"'`，CI 绝不会触发真实请求。
所有 fixture 文件头部都标注了 `SYNTHETIC`；结构对齐实测响应，取值虚构。

**实际运行记录**（禁止在无证据时声称「测试通过」，指南 §11）：

```text
日期    2026-09-23（完整优化 Phase 0–6）
环境    macOS arm64 / CPython 3.12.13 / pytest 9.1.1

命令    .venv/bin/python -m pytest -q
结果    452 passed, 4 deselected（离线，未访问网络）
        4 个 deselected 是显式标记的真实网络测试，默认不跑

日期    2026-09-22（移除相关性筛选层之后）
环境    macOS 26.6.2 (Darwin arm64) / CPython 3.12.13 / pytest 9.1.1 / fastapi 0.141.1

命令    .venv/bin/python -m pytest -q
结果    323 passed, 4 deselected in 5.16s   （离线，未访问网络）
        4 个 deselected 是标了 live 的真实网络测试，默认不跑
```

改造前是 `359 passed, 4 deselected`（在 HEAD 的独立 worktree 上实测）。差额构成：
`tests/test_classify.py` 的 **59 条**随分类层一起删除，其余测试文件为适配新契约净增 **23 条**
（359 − 59 + 23 = 323）。

守护新契约的几条关键用例：

| 用例 | 守什么 |
|---|---|
| `test_stats.py::test_a_cheap_outlier_stays_in_the_arithmetic` | ¥50 的条目必须留在算术里，防止 IQR 剔除悄悄回来 |
| `test_stats.py::test_result_is_always_marked_unfiltered` | `sample_quality` 恒含 `unfiltered` |
| `test_api_contract.py::test_relevance_filters_are_not_accepted_at_all` | `item_kind` 等已移除参数必须 422，不得静默忽略 |

`-m live` 的真实网络测试**未在本次文档更新时重跑**；`IMPLEMENTATION_REPORT.md` 里那条
`4 passed, 359 deselected in 21.67s` 是改造前的记录，保留以供追溯。

---

## 许可与第三方边界

上游 [`superboyyy/xianyu_spider`](https://github.com/superboyyy/xianyu_spider)
**没有 LICENSE 文件**（`gh api repos/.../license` → 404），但 `README.md:176` 写着
「本项目采用 [MIT License](LICENSE)」并附加「数据抓取结果不得用于商业用途」。
所有 `.py` 源码头部均无版权/SPDX 声明。→ **许可未确认。**

因此：

- 上游保留为独立 checkout，位于 gitignored 的 `upstream/xianyu_spider/`
- **不复制、不 vendor** 其任何源码进本项目
- 集成方式为进程内 import 调用其公开函数，**不修改上游一行代码**
- 仅限本机个人使用，不分发、不商用
- 此项记为**未决法律风险**，本项目不宣布合规

commit 由 `scripts/setup.sh` 钉在 `data/upstream-commit.txt`；与已验证 commit 不一致时会告警。

---

## 平台合规

低频、仅用于本人购物研究。遇到访问拒绝、风控、验证码、登录失效即**停止自动操作并交还用户**。
网页与平台接口不受本项目控制，**不承诺「稳定永久爬取」**。

---

## 目录

```text
src/xps/
├── main.py               FastAPI 装配、生命周期、统一错误处理
├── settings.py           环境变量与配置校验（含 loopback 强制）
├── errors.py             错误码全集与 HTTP/retryable/需人工映射、AGENT_ACTIONS
├── cli.py                一键查询客户端；render_report/render_failure 固化转述纪律
├── adapters/
│   ├── base.py           RawListing / CrawlResult / PageOutcome / XianyuAdapter 契约
│   └── xianyu.py         进程内包装上游 mtop；逐页串行 + 节流 + ret 码映射；透传字段抽取；登录态不主动过期
├── api/
│   ├── schemas.py        外部 API 数据类型（ProductItem 的 price/seller/media/signals 嵌套结构）
│   ├── deps.py           共享依赖（含「失败 run 不得退化成 200+[]」与 reject_removed_filters）
│   ├── help.py           GET /help：agent 自助发现，从 openapi() 与请求模型派生，不会漂移；含 passthrough 段
│   ├── search.py  products.py  stats.py  system.py
├── services/
│   ├── search_service.py 任务生命周期、串行锁、节流、标准化入库
│   ├── normalize.py      价格解析（Decimal→分）、四态 parse_status、fen_to_yuan
│   ├── identity.py       身份键与域名白名单
│   └── statistics.py     inclusive 分位数、未筛选样本构成、sample_quality
└── storage/
    ├── db.py             连接/WAL/幂等加法迁移（SCHEMA_VERSION=4）/VACUUM INTO 备份
    ├── schema.sql
    └── repository.py
scripts/   setup.sh start-local.sh login.sh query-price.sh smoke-local.sh
           backup-sqlite.sh verify_upstream.py
tests/     conftest.py helpers.py fake_adapter.py fixtures/（均标注 SYNTHETIC）
           离线单测与 API 契约 + test_help.py + test_smoke_live.py(-m live)
data/      gitignored：price.sqlite3、backups/、probe/、upstream-commit.txt
upstream/  gitignored：上游独立 checkout，许可未确认
```

`src/xps/services/classify.py` 与 `tests/test_classify.py` **已删除**（相关性分类筛选层）。
