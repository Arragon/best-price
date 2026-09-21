# 闲鱼本地商品搜索与价格统计服务

单平台（闲鱼）本地 MVP。真实搜索 → 商品去重入库 → 平台字段清洗后原样透传 → **未筛选**的在售报价算术 → 可追溯 API。
**只监听 `127.0.0.1`**，不分发、不商用。版本 `0.2.0`（`GET /health` 可查）。

父规范：[`Xianyu_Agent_Price_Service_Implementation_Guide.md`](./Xianyu_Agent_Price_Service_Implementation_Guide.md)
设计决策与 Gate A 实测证据：[`docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md`](./docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md)
实施结论与阻塞项：[`IMPLEMENTATION_REPORT.md`](./IMPLEMENTATION_REPORT.md)

---

## ⚠️ 先读：价格口径与红线

- 返回的是**采集时刻的公开在售报价**。**不是成交价**，不含国补、议价结果。
  若平台打了券标签（`price.coupon_text`，如「券已抵50元」），**展示价可能已扣券** —— 原文一并透出。
- **本服务不做任何相关性筛选。** 闲鱼挂牌里混着大量**租赁盘**（实测 ¥40–50/天）、**求购盘**、
  **配件**、**定金占位链接**、**拍卖起拍价**、**广告位**，它们**全部照常入库、照常进入算术**。
  `/v1/stats` 的 `sample_quality` 因此**恒含 `unfiltered`**，转述时不得省略。
- 判断哪条商品可比，是**调用方（agent）的责任**。为此 `/v1/products` 透出了足够字段：
  完整挂牌描述（保留换行分段）、卖家信用、好评率与评价数、卖家地址、想要人数、券抵扣、
  包邮/严选/验货宝徽标、拍卖与广告标记。见「服务端不筛选」一节。
- 采集失败**不会**退化成 `200 + []`。空列表只表示经核验的真实无结果（平台自报 `hasItems=false`）。
- **不靠图片推断未公开的参数**，也不用大模型补齐规格。字段缺失就是 `null`，不填「暂无」占位。
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
scripts/query-price.sh "富士 X-T4" --pages 2
scripts/query-price.sh "RTX 4090" --pages 2      # 任意品类，同一套用法

# 离线测试（不打网络）
.venv/bin/python -m pytest -q
```

`scripts/query-price.sh` 是**给 agent 用的推荐入口**：它把 `POST → 轮询 → stats → products`
四步、超时和错误分诊封成一条命令，输出里强制带上口径声明、**「样本未筛选」声明**、样本量、
无价条目分组（`unpriced_by_status`）、拍卖/广告计数、采集质量限制和可追溯链接 ——
调用方没法只报一个中位数就走。每条最低价样本旁边还会打上卖家信用、好评率与券抵扣。
退出码：`0` 成功 / `2` 服务或参数问题 / `3` 采集失败 / `4` 轮询超时。

参数：`--pages N`(≤ `MAX_SEARCH_PAGES`，默认 3)、`--sort newest|price_asc|price_desc|default`、
`--min-price` / `--max-price`、`--top N`、`--timeout`、`--poll-interval`、`--base-url`。
**没有 `--kind`**：相关性筛选已整体移除。

想看原始 HTTP 交互或做更细的调试，用 `scripts/smoke-local.sh "富士 X-T4" 2`
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
- **错误码 → 行动指引** —— 12 个错误码各自带 `http_status` / `retryable` /
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
  -d '{"keyword":"RTX 4090","max_pages":1,"sort":"newest"}'
```

```json
{
  "run_id": "026babbc-6e7e-4db0-bb83-691805172f64",
  "status": "pending",
  "status_url": "/v1/search-runs/026babbc-6e7e-4db0-bb83-691805172f64"
}
```

| 字段 | 约束 |
|---|---|
| `keyword` | 必填，非空，≤ 64 字符 |
| `max_pages` | 默认 1，上限 `MAX_SEARCH_PAGES`（默认 3） |
| `sort` | `newest`(默认) / `price_asc` / `price_desc` / `default`，对齐上游实测 `SORT_OPTIONS` |
| `min_price_yuan` / `max_price_yuan` | Decimal 字符串，≥ 0，min ≤ max |
| `city` / `province` / `publish_days` | 上游 `SearchFilters` 支持，但**平台是否真过滤未经实测**，传非 null 一律 `422 UNSUPPORTED_FILTER`，不暗称已过滤 |

**没有 `item_kind`**（旧版的单机身/套机筛选已移除）。请求体是 `extra="forbid"`，
传它会得到 `422 INVALID_QUERY`，不会被静默忽略。

任务生命周期：单实例串行锁 + 应用内异步任务，**所有状态落库**。进程崩溃重启后，遗留的
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
# {"status":"ok","database":"ok","version":"0.2.0","adapter":"XianyuUpstreamAdapter"}

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

### `SCHEMA_VERSION = 2`（2026-09-22 迁移）

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
    ├── db.py             连接/WAL/幂等加法迁移（SCHEMA_VERSION=2）/VACUUM INTO 备份
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
