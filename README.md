# 闲鱼本地商品搜索与价格统计服务

单平台（闲鱼）本地 MVP。真实搜索 → 商品入库 → 规格筛选 → 在售报价统计 → 可追溯 API。
**只监听 `127.0.0.1`**，不分发、不商用。

父规范：[`Xianyu_Agent_Price_Service_Implementation_Guide.md`](./Xianyu_Agent_Price_Service_Implementation_Guide.md)
设计决策与 Gate A 实测证据：[`docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md`](./docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md)
实施结论与阻塞项：[`IMPLEMENTATION_REPORT.md`](./IMPLEMENTATION_REPORT.md)

---

## ⚠️ 先读：价格口径与红线

- 返回的是**采集时刻的公开在售报价**。**不是成交价**，不含国补、优惠券、议价结果。
- 闲鱼挂牌里混着大量**租赁盘**（实测 ¥40–50/天）、**求购盘**、**配件**、**定金占位链接**。
  不做相关性过滤时，「富士 X-T4 最低价」会被算成 ¥40。本服务默认排除这些，并报告排除原因与数量。
- 采集失败**不会**退化成 `200 + []`。空列表只表示经核验的真实无结果（平台自报 `hasItems=false`）。
- 无法从标题文本确认配置的样本进 `review`，**不静默删除，也不靠图片推断未公开的参数**。
- 金额内部一律**人民币分（整数）**，API 以保留两位的字符串返回。全链路无 float。

---

## 环境要求

| 项 | 要求 | 本项目实测 |
|---|---|---|
| 系统 | macOS / Linux（Windows 需自行换算 PowerShell） | macOS 26.6.2, Darwin arm64 |
| Python | ≥ 3.10（上游要求）；建议 3.12 | CPython 3.12.13（由 uv 管理） |
| uv | 需要 | `~/.local/bin/uv` |
| Playwright Chromium | **搜索不需要**，仅扫脸核身时才需要 | 未安装 |

系统自带的 `python3` 可能是 3.9（不满足上游要求），`scripts/setup.sh` 会用 uv 拉 3.12。

---

## 快速开始

```bash
# 1. 初始化：建 venv、clone 上游、装依赖（幂等，可重复运行）
scripts/setup.sh

# 2. 启动服务（默认 127.0.0.1:8765）
scripts/start-local.sh

# 3. 另开一个终端，一条命令拿结果（会真的请求闲鱼，请保持低频）
scripts/query-price.sh "富士 X-T4" --kind body --pages 2

# 离线测试（不打网络）
.venv/bin/python -m pytest -q
```

`scripts/query-price.sh` 是**给 agent 用的推荐入口**：它把 `POST → 轮询 → stats → products`
四步、超时和错误分诊封成一条命令，输出里强制带上口径声明、样本量、排除原因、采集质量限制
和可追溯链接 —— 调用方没法只报一个中位数就走。退出码：`0` 成功 / `2` 服务或参数问题 /
`3` 采集失败 / `4` 轮询超时。

想看原始 HTTP 交互或做更细的调试，用 `scripts/smoke-local.sh "富士 X-T4" 2 any`
（`RUN_ID=<已完成的run> scripts/smoke-local.sh …` 可复用已有 run，不重复打平台）。

交互式 API 文档：启动后访问 <http://127.0.0.1:8765/docs>，或 `GET /openapi.json`。

### 备份

```bash
scripts/backup-sqlite.sh                       # 自动时间戳，落 data/backups/
scripts/backup-sqlite.sh /tmp/before-upgrade.sqlite3
```

用 `VACUUM INTO`（SQLite ≥ 3.27）而非裸拷主库文件 —— WAL 模式下裸拷会得到不完整快照。
备份后立即 `PRAGMA integrity_check` 并核对行数；**目标文件已存在时拒绝覆盖**，不会悄悄毁掉上一份可用备份。

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

### Agent 调用流程

**推荐**：直接用 `scripts/query-price.sh`（见「快速开始」），它已经实现了下面整套流程，
并把必须转述的口径与限制固化进输出。

需要自己编排时的原始流程：

```text
POST /v1/search  →  202 + run_id
   ↓
GET /v1/search-runs/{run_id}   轮询直到 succeeded / partial / failed
   ↓
GET /v1/products?run_id=...    本轮商品与原始链接
GET /v1/stats?run_id=...&item_kind=body
   ↓
输出时务必带上：统计口径 + 样本量 + 排除规则 + 报价区间 + 商品链接 + 采集时间与状态
```

### `POST /v1/search` → `202 Accepted`

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"富士 X-T4","max_pages":2,"sort":"newest","item_kind":"body"}'
```

```json
{
  "run_id": "b1ad3465-6af3-4390-afd2-cff01fd09455",
  "status": "pending",
  "status_url": "/v1/search-runs/b1ad3465-6af3-4390-afd2-cff01fd09455"
}
```

| 字段 | 约束 |
|---|---|
| `keyword` | 必填，非空，≤ 64 字符 |
| `max_pages` | 默认 1，上限 `MAX_SEARCH_PAGES`（默认 3） |
| `sort` | `newest`(默认) / `price_asc` / `price_desc` / `default`，对齐上游实测 `SORT_OPTIONS` |
| `min_price_yuan` / `max_price_yuan` | Decimal 字符串，≥ 0，min ≤ max |
| `item_kind` | `body` / `kit` / `any`（默认）。**本地后置过滤**，不是平台过滤 |
| `city` / `province` / `publish_days` | 上游 `SearchFilters` 支持，但**平台是否真过滤未经实测**，传非 null 一律 `422 UNSUPPORTED_FILTER`，不暗称已过滤 |

任务生命周期：单实例串行锁 + 应用内异步任务，**所有状态落库**。进程崩溃重启后，遗留的
`pending`/`running` 会被判定为 `failed` + `RUN_INTERRUPTED`，不会永远挂着。

### `GET /v1/search-runs/{run_id}`

```bash
curl -sS http://127.0.0.1:8765/v1/search-runs/b1ad3465-6af3-4390-afd2-cff01fd09455
```

```json
{
  "run_id": "b1ad3465-6af3-4390-afd2-cff01fd09455",
  "status": "succeeded",
  "platform": "xianyu",
  "keyword": "富士 X-T4",
  "auth_mode": "guest",
  "pages_requested": 2,
  "pages_fetched": 2,
  "raw_count": 60,
  "distinct_count": 60,
  "eligible_count": 14,
  "started_at": "2026-09-21T19:00:43.182826Z",
  "ended_at": "2026-09-21T19:00:47.053104Z",
  "warnings": [],
  "error": null,
  "adapter_version": "xps-xianyu/0.1.0",
  "source_commit": "eb52bd4d1901eee9ba8035e860583cddf50ead4c"
}
```

`raw_count`（原始条目）/ `distinct_count`（去重后商品）/ `eligible_count`（进入统计的合格样本）是**分层**计数。
`source_commit` 让你事后能判断这轮数据是哪个上游版本抓的。

### `GET /v1/products`

```bash
curl -sS "http://127.0.0.1:8765/v1/products?run_id=<RUN>&eligible_only=true&limit=50&offset=0"
```

`limit` 默认 50、上限 100；返回 `total` 以便分页。每项含 `product_id`、`title`、`canonical_url`、
`price_text`（原文）、`price_yuan`（字符串或 `null`）、`observed_at`、`published_at`、`area`、
`seller_display_name`、`image_url`、`flags`、`item_kind`、`excluded`、`exclusion_reasons`、
`needs_review`、`price_parse_status`、`source_run_id`。

**可见字段缺失即 `null`**，不会插入「暂无」「未知」「匿名卖家」之类的占位伪数据
（上游 `safe_get(default="暂无")` 的行为被本项目显式避开）。

### `GET /v1/stats`

```bash
curl -sS "http://127.0.0.1:8765/v1/stats?run_id=<RUN>&item_kind=body"
```

`run_id` **必填** —— 默认绝不跨日期或跨关键词混合历史数据。真实输出（2026-09-22，guest，2 页）：

```json
{
  "item_kind": "body", "currency": "CNY", "run_status": "succeeded", "partial": false,
  "raw_count": 60, "distinct_count": 60,
  "eligible_count": 12, "excluded_count": 33, "needs_review_count": 15, "suspicious_price_count": 0,
  "min_yuan": "4390.00", "p25_yuan": "4772.50", "median_yuan": "5249.50",
  "p75_yuan": "5349.25", "max_yuan": "7500.00",
  "excluded_by_reason": {
    "rental_or_lease": 22, "deposit_or_placeholder": 21, "wanted_to_buy": 6,
    "model_mismatch": 3, "accessory_only": 2, "item_kind_mismatch": 2
  },
  "lowest_items": [
    {"product_id": 58, "price_yuan": "4390.00",
     "title": "富士X-T4微单机身，黑色，富士X-T4机身，黑色款，适合日常扫街、旅游、Vlog…",
     "canonical_url": "https://www.goofish.com/item?id=1086862848607"}
  ],
  "insufficient_sample": false,
  "sample_quality": ["guest_auth"]
}
```

`excluded_by_reason` 各项之和可能大于 `excluded_count`：一个商品可同时命中多个排除原因
（例如租赁盘的样板文案里同时出现「押金」），按原因分别计数，商品本身只算一次。

### `GET /health`、`GET /v1/auth/status`、`POST /v1/auth/reload`

```bash
curl -sS http://127.0.0.1:8765/health
# {"status":"ok","database":"ok","version":"0.1.0","adapter":"XianyuUpstreamAdapter"}

curl -sS http://127.0.0.1:8765/v1/auth/status
# {"state":"guest","requires_human_action":false,"verified":false,
#  "hint":"未登录（guest 可搜索）；如需登录态请在本机运行 scripts/login.sh 后调用 POST /v1/auth/reload"}

curl -sS -X POST http://127.0.0.1:8765/v1/auth/reload   # 登录后免重启
```

- `/health` 只看进程与本地 SQLite；**登录态丢失不算进程不健康**。
- `verified: false` 表示未向平台主动校验 —— 这是刻意的，见上文「不主动过期」。
- `/v1/auth/reload` 只接受 POST（GET 返回 405），避免被预取式请求意外触发；它不修改也不删除任何凭证文件。

---

## 统计方法（口径固定，可复现）

**样本准入**：仅当前 `run_id` + 去重后 + 匹配请求的 `item_kind` + 未被排除 + 非 `needs_review`
+ `price_parse_status = 'valid'` 的商品。

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

**异常低价围栏**：合格样本 ≥ `MIN_SAMPLE_THRESHOLD`（默认 8）时启用 IQR 下界
`Q1 − 1.5 × IQR`，低于者标 `suspicious_price` 并移出样本。这是标签漏判时的第二道防线
（例如未写「租」字的日租盘）。样本不足时**不**自动删数据 —— 小样本 IQR 不稳定。

**`insufficient_sample`**：合格样本 < 8 时为 `true`。统计仍展示已知样本，但不产出「市场公允价」。
8 是 MVP 告警阈值，不是统计学保证。

**`sample_quality`** 会列出本轮的明确限制：`insufficient_sample`、`mixed_item_kinds`、
`single_page_only`、`partial_pages:已抓/请求`、`guest_auth`、`unknown_auth`、`status_*`。

---

## 相关性规则

标签来自指南 §7 的 9 个，另加 4 个**有实测依据**的：

| 标签 | 含义 | 处置 |
|---|---|---|
| `exact_model` | 标题命中目标型号 token | 保留 |
| `compatible_variant` | 同型号不同颜色/版本（银色、国行…） | 保留 |
| `bundle` | 套机/套装 | `item_kind=kit`，不与单机身混算 |
| `accessory_only` | 只卖配件 | **排除** |
| `repair_or_fault` | 故障机 | **排除** |
| `wanted_to_buy` | 求购/回收（非卖盘） | **排除** |
| `deposit_or_placeholder` | 定金/订金/押金/占位/补差价/专拍 | **排除** |
| `suspicious_price` | IQR 下界外的异常低价 | **排除** |
| `unknown_variant` | 配置无法从文本确认 | 进 `review`，不排除 |
| `rental_or_lease` ⭐ | 租赁/出租/日租/免押 | **排除** |
| `auction` ⭐ | `exContent.isAuction`，起拍价非在售报价 | **排除** |
| `promoted_ad` ⭐ | `exContent.isAliMaMaAD`，广告位非自然结果 | **排除** |
| `model_mismatch` ⭐ | 型号 token 明确不匹配（如搜 X-T4 出现 X-T3） | **排除** |

⭐ = 本项目在指南 9 个之外新增，依据见 `IMPLEMENTATION_REPORT.md`。

三条从**真实误判**中总结出的规则，是本模块最关键的部分：

1. **故障词必须做否定与枚举语境判断。**「无拆修」是卖家声明*没有*拆修；
   「避免磕碰、受潮、进水」是租赁须知；「支持(人为损坏、进水进液…)」是质保条款。
   裸子串匹配曾在 60 条真实数据里误删 4 台正常整机。
2. **配件词只在标题开头 40 字的自述范围内匹配。** 真整机常在后段列附带清单
   （「配件:品牌电池2块 充电器」），真配件（「两块沣标…相机电池」）一定在开头点明。
3. **「机身」是弱信号。**「机身外观轻微使用痕迹」是外观描述，不能与「套机」判成冲突。
   强信号为 单机身/单机/裸机/主机/本体。同理，「成色如图」说的是外观，不单独触发 review。

型号 token 从关键词推导：含数字的 token 视为型号（`富士 X-T4` → `XT4`；`9950X3D`、`2TB NVMe SSD` 同理）。
匹配时尾部加 `(?![0-9])`，避免 `XT4` 命中 `X-T40`。

---

## 商品身份

优先级（指南 §7）：

1. `exContent.itemId` → `identity_key = "xianyu:item:<id>"`。实测跨两轮搜索 30/30 完全一致。
2. 后备：剥除跟踪参数（`referPageArgs`/`gulSource`/`extra`/`spm`…）后的规范化 URL 取 SHA-256
   → `identity_key = "xianyu:url:<sha256>"`
3. 都不成立 → `id_source = "invalid_identity"`，打标排除，不进可信统计

域名白名单：`goofish.com` / `www.goofish.com` / `h5.m.goofish.com`；实测真实形态
`fleamarket://item?id=...` 会被转换为 `https://www.goofish.com/item?id=...`。
短链、非 http(s)、不可信主机一律拒绝。

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

隐私：只存公开卖家昵称。**不存** Cookie、token、手机号、地址、私信。

与指南 §6 表格的两处有意偏离（都为遵守优先级更高的 §0.4「严禁占位伪数据」）：
`products.title_latest` 与 `products.canonical_url` 改为可空 —— 平台未给标题时存 `NULL`，
不填「暂无」；身份不可信的条目 `canonical_url` 为 `NULL`，但保留记录以便追溯。

---

## 故障处理

统一错误体：`{code, message, run_id, retryable, requires_human_action}`。

| code | HTTP | retryable | 需人工 | 你该做什么 |
|---|---|---|---|---|
| `INVALID_QUERY` | 422/404 | ✗ | ✗ | 检查参数；404 表示 run_id 不存在 |
| `UNSUPPORTED_FILTER` | 422 | ✗ | ✗ | 该筛选未经验证，去掉它 |
| `AUTH_REQUIRED` | 401 | ✗ | ✓ | 运行 `scripts/login.sh` |
| `AUTH_EXPIRED` | 401 | ✗ | ✓ | 运行 `scripts/login.sh` 重新登录，再 `POST /v1/auth/reload`（免重启）；guest 搜索仍可继续 |
| `CHALLENGE_REQUIRED` | 409 | ✗ | ✓ | **停止自动操作**，到闲鱼 App 完成验证 |
| `RATE_LIMITED` | 429 | ✓ | ✓ | 停止重试，调大 `MIN_SECONDS_BETWEEN_SEARCHES` |
| `UPSTREAM_CHANGED` | 502 | ✗ | ✓ | 平台/上游结构漂移，重跑 `scripts/verify_upstream.py` |
| `UPSTREAM_TIMEOUT` | 504 | ✓ | ✗ | 有限次重试，勿并发放大 |
| `UPSTREAM_UNAVAILABLE` | 503 | ✓ | ✗ | 检查网络、上游 checkout、依赖是否装齐 |
| `DB_ERROR` | 500 | ✓ | ✗ | 看日志；必要时从 `data/backups/` 恢复 |
| `NO_VALID_RESULTS` | 200 | ✗ | ✗ | 本轮无合格样本，看 `excluded_by_reason` |
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
日期    2026-09-22
环境    macOS 26.6.2 (Darwin arm64) / CPython 3.12.13 / pytest 9.1.1 / fastapi 0.141.1

命令    .venv/bin/python -m pytest -q
结果    326 passed, 4 deselected          （离线，未访问网络；耗时 2–8s 随机器负载浮动）

命令    .venv/bin/python -m pytest -m live -q
结果    4 passed, 326 deselected in 21.81s （真实闲鱼，guest，6 次搜索页请求）
```

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
├── errors.py             错误码全集与 HTTP/retryable/需人工映射
├── cli.py                一键查询客户端；render_report/render_failure 固化转述纪律
├── adapters/
│   ├── base.py           RawListing / CrawlResult / PageOutcome / XianyuAdapter 契约
│   └── xianyu.py         进程内包装上游 mtop；逐页串行 + 节流 + ret 码映射；登录态不主动过期
├── api/
│   ├── schemas.py        外部 API 数据类型
│   ├── deps.py           共享依赖（含「失败 run 不得退化成 200+[]」）
│   ├── search.py  products.py  stats.py  system.py
├── services/
│   ├── search_service.py 任务生命周期、串行锁、节流、标准化入库
│   ├── normalize.py      价格解析（Decimal→分）、fen_to_yuan
│   ├── identity.py       身份键与域名白名单
│   ├── classify.py       相关性规则
│   └── statistics.py     inclusive 分位数、IQR 围栏、样本质量
└── storage/
    ├── db.py             连接/WAL/幂等迁移/VACUUM INTO 备份
    ├── schema.sql
    └── repository.py
scripts/   setup.sh start-local.sh login.sh query-price.sh smoke-local.sh
           backup-sqlite.sh verify_upstream.py
tests/     离线单测与 API 契约 + fake_adapter.py + fixtures/ + test_smoke_live.py(-m live)
data/      gitignored：price.sqlite3、backups/、probe/、upstream-commit.txt
upstream/  gitignored：上游独立 checkout，许可未确认
```
