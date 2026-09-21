# 闲鱼本地价格服务 — 设计决策与 Gate A 验证记录

日期：2026-09-22（Asia/Taipei）
状态：设计已获用户批准，Gate A 已通过
上游规范：`Xianyu_Agent_Price_Service_Implementation_Guide.md`（v1.0）是本设计的父规范。本文只记录**指南留空的决策**与**实测证据**，不重复指南内容。

---

## 1. 已验证事实（Phase 0 / Phase 1 实测）

### 1.1 环境

| 项 | 实测值 |
|---|---|
| 系统 | macOS 26.6.2 (Build 25G83), Darwin arm64 |
| 系统 python3 | 3.9.6 —— **不满足上游要求的 3.10+** |
| 实际使用 | `uv venv --python 3.12` → CPython **3.12.13**，位于 `.venv/` |
| 关键依赖 | fastapi 0.141.1 / httpx 0.28.1 / pydantic 2.11.10 / tortoise-orm 1.1.8 / playwright 1.63.0 |
| Playwright Chromium | **未安装，搜索路径不需要**。Playwright 仅在 `xianyu/qr_browser.py:156` 函数内惰性导入，用于扫脸核身 |

### 1.2 上游 `superboyyy/xianyu_spider`

| 项 | 实测值 |
|---|---|
| commit | `eb52bd4d1901eee9ba8035e860583cddf50ead4c` |
| commit 日期 | 2026-09-12T00:31:52+10:00（= 2026-09-11T14:31:52Z） |
| 仓库 pushed_at | 2026-09-18T09:19:54Z |
| archived | false |
| stars | 862 |
| CLI | `python spider.py {serve,login,search}`，`--host` **默认 `0.0.0.0`**（须显式覆盖为 127.0.0.1） |

**实测签名：**

```
scrape_xianyu_http(keyword: str, max_pages: int = 1, filters: SearchFilters | None = None)
mtop.search(keyword: str, page: int = 1, filters=None)   # 返回完整原始 mtop JSON
mtop.init()                                              # 需外网；取 _m_h5_tk
mtop.probe_login() -> dict
SORT_OPTIONS = {'newest': ('create','desc'), 'price_asc': ('price','asc'),
                'price_desc': ('price','desc'), 'default': ('','')}
SearchFilters(sort, min_price, max_price, province, city, publish_days)
```

### 1.3 许可证结论：**未确认**

- 仓库根目录**无 LICENSE / COPYING 文件**（`gh api repos/.../license` → HTTP 404；`ls` 确认）。
- 全仓库唯一许可声明是 `README.md:176`：「本项目采用 [MIT License](LICENSE)，请合理使用并注明出处。数据抓取结果不得用于商业用途。」——**其引用的 LICENSE 文件不存在**，且附带了与 MIT 冲突的商用限制。
- 所有 `.py` 源码头部无任何 copyright / SPDX 声明。

**处置（依指南 §0.10、§2）：**
1. 不 vendor、不复制任何上游源码进本项目可分发部分。
2. 上游保留为独立 checkout，位于 gitignored 的 `upstream/xianyu_spider/`。
3. 集成方式限定为**进程内 import 调用其公开函数**，不修改上游一行代码（避免派生改动）。
4. 仅本机个人使用，API 只监听 127.0.0.1，不对外分发、不商用。
5. 此项记为**未决法律风险**，不宣布合规。用户已在知情下选择此路径。

### 1.4 备选 `Usagi-org/ai-goofish-monitor`

MIT 已确认；但 GitHub API 返回 `archived: false`（**与指南 §2 所述「2026-06-09 已归档」矛盾**），最后推送 2026-05-18。未采用：它是 Playwright + Web UI + AI 的重型监控系统，面向持续监控而非「一次搜索返回商品列表」。保留为许可兜底退路。

### 1.5 真实采集验证（未登录 / guest）

命令：`.venv/bin/python scripts/verify_upstream.py --keyword "富士 X-T4" --pages N --tag T`

| 轮次 | 参数 | 结果 |
|---|---|---|
| A-p1 | 1 页 | `ret=['SUCCESS::调用成功']`，**30 条**，0.34s |
| A-p2 | 2 页 | page1 30 条 + page2 30 条，**`distinct_item_ids=60`，`duplicated_ids=0`** → 真翻页，未重复第一页 |
| B | 重抓 1 页 | 30 条，与轮次 A page1 **重叠 30/30 = 100%**，ID 完全一致 |

**决定性结论：** 重抓时全部 30 件都是「老商品」，上游此时会报 `new_records=0` / `new_record_ids=[]`，但本轮实际有 30 件真实结果。**证实指南 §3 选项 B 与 §6 `run_items` 的警告成立** —— 绝不能拿 `new_record_ids` 当本轮结果。

其他实测：
- **未登录即可搜索**（`logged_in=False, login_expired=False`）→ 不触发 `BLOCKED_HUMAN_LOGIN`。
- 30/30 价格可精确解析为纯数字。
- 平台自报 `resultInfo.searchResControlFields` = `minPrice:490, maxPrice:2886600, hasItems:true`（单位：分）；本页解析范围 4000–884800 分，为其子集，一致。
- `resultInfo.hasNextPage = true` → 可用作翻页终止的真实信号。
- 本批 30 条 `isAuction` 全 false、`isAliMaMaAD` 全 false、`has_oriPrice` 6 条 true。

### 1.6 实测字段路径（解析器契约）

```
data.resultList[]                                     # 商品数组
data.resultInfo.hasNextPage                           # bool，真实翻页信号
data.resultInfo.searchResControlFields.{hasItems,minPrice,maxPrice}
  .data.item.main.targetUrl                           # "fleamarket://item?id=...&referPageArgs=...&gulSource=search&extra={...}"
  .data.item.main.exContent.itemId                    # ★ 真实平台商品 ID（跨轮 100% 稳定）
  .data.item.main.exContent.title
  .data.item.main.exContent.price[]                   # [{type: sign|integer|decimal, text: "¥"|"5642"|".50"}, ...]
  .data.item.main.exContent.area
  .data.item.main.exContent.userNickName
  .data.item.main.exContent.picUrl                    # "http://img.alicdn.com/..."
  .data.item.main.exContent.isAuction                 # bool
  .data.item.main.exContent.isAliMaMaAD               # bool，广告位
  .data.item.main.exContent.oriPrice                  # 可选，原价（≠ 在售价）
  .data.item.main.exContent.want
  .data.item.main.clickParam.args.publishTime         # epoch 毫秒字符串
  .data.item.main.clickParam.args.tagname             # "包邮" / "包邮/已验货"
  .data.item.main.clickParam.args.{item_id,id}        # 与 exContent.itemId 一致（冗余校验用）
```

**必须避开的上游缺陷（指南已明令禁止，实测确认存在）：**

| 位置 | 问题 | 本项目处置 |
|---|---|---|
| `xianyu/search.py:get_link_unique_key` | `link.split("&", 1)[0]` 再 md5 —— §7 明确禁止。本次恰好因 `id` 是首参数而侥幸稳定，参数顺序一变即碰撞/漏匹配 | 用 `exContent.itemId`；后备用剥跟踪参数后的规范化 URL + SHA-256 |
| `xianyu/search.py:safe_get` | `default="暂无"` —— §8.3 明确禁止的占位伪数据 | 缺失字段一律 `None`，API 返回 `null` |
| `xianyu/search.py:handle_data` | 价格异常时写入字符串哨兵 `"价格异常"`；`万` 走 `float(...)*10000` | 自己解析，Decimal → 分 INTEGER；异常置 `price_fen=NULL` + `price_parse_status` |
| `xianyu/search.py:scrape_xianyu_http` | `asyncio.gather` + `Semaphore(3)` 并发抓页 → 无逐页成败粒度，无法如实报告 `pages_fetched` / `partial`；异常会被 gather 吞掉或整体失败 | 不用它。改为逐页串行 + 可配延迟，自己记每页成败 |
| `exContent.detailParams.soldPrice` | 字段名叫 soldPrice 但实为挂牌价 | **不使用**，避免把挂牌价当成交价（§0.4） |
| `spider.py --host` | 默认 `0.0.0.0` | 不起上游 HTTP 服务；进程内 import 无监听 |

---

## 2. 架构决策

### 2.1 集成路径：选项 A（进程内 import），用户已批准

```
本地脚本 / Agent / (后续) Web UI
        │ HTTP 127.0.0.1:8765
        ▼
  FastAPI (src/xps)   ← 本项目全部自有代码
        │
  SearchService：单实例串行锁 + 逐页节流 + run 生命周期落库
        │
  XianyuAdapter (Protocol)
        │  sys.path 注入 upstream/xianyu_spider
        ├─ mtop.init()            复用：token/签名/协议（不重写）
        ├─ mtop.search(kw,page,f) 复用：单页请求
        ├─ mtop.probe_login()     复用：登录态探测
        └─ SearchFilters          复用：筛选参数构造
        │
  自有解析层：raw JSON → RawListing（真实 null、Decimal 价格、itemId 身份）
        │
  normalize / identity / classify / statistics
        │
  SQLite (WAL)：products | observations | search_runs (+ run_items 视图)
```

**不复用** `scrape_xianyu_http` / `handle_data` / `save_to_db` / `XianyuProduct` 模型，理由见 §1.6 表。上游只当「签名与传输层」使用。

**为什么不起上游 HTTP 服务：** `POST /search/` 只返回 `total_results`/`new_records`/`new_record_ids`，拿不到本轮商品（§1.5 已证伪）；且它默认绑 `0.0.0.0`。进程内 import 反而更少暴露面。

### 2.2 身份（identity）

优先级：
1. `exContent.itemId` → `identity_key = "xianyu:item:<itemId>"`，`id_source = "platform_item_id"`
2. 后备：`targetUrl` 转 https、剥跟踪参数（保留白名单参数 `id`）、host 限定 `goofish.com` / `www.goofish.com` / `h5.m.goofish.com`，再 SHA-256 → `identity_key = "xianyu:url:<sha256>"`，`id_source = "url_fingerprint"`
3. 都不成立 → `id_source = "invalid_identity"`，打标 `invalid_identity`，**排除出统计**

`canonical_url` 一律重写为 `https://www.goofish.com/item?id=<itemId>`（剥除全部跟踪参数），原始 `targetUrl` 存进 `raw_payload_json` 备查。

### 2.3 价格

- 原文：按 `price[]` 顺序拼接各段 `text`（`sign`+`integer`+`decimal`）→ 如 `¥5642.50`，原样存 `price_raw`。
- 金额：`Decimal` 解析 → `price_fen` INTEGER。**全程不出现 float。**
- 「万」：`Decimal("0.35") * 10000` → 精确。
- `price_parse_status`：`valid` / `ambiguous` / `missing` / `invalid`。
- `ambiguous` 触发词（不参与合格样本）：定金、押金、面议、私聊、议价、起、区间（`-`/`~`/`到`）、1 元占位、租/日租/月租、不包邮中未确认运费。
- 解析失败 → `price_fen = NULL`，**绝不置 0**。

### 2.4 相关性标签

指南 §7 的 9 个：`exact_model`、`compatible_variant`、`bundle`、`accessory_only`、`repair_or_fault`、`wanted_to_buy`、`deposit_or_placeholder`、`suspicious_price`、`unknown_variant`。

**新增 3 个，均有本次实测依据：**

| 新标签 | 实测依据 |
|---|---|
| `rental_or_lease` | 两轮均出现租赁盘且是本次最大价格污染源：`¥90 #重庆同城#索尼a7m4/a7m3/a7c2/a7r3免押租赁`（id 1086948380894）、`¥50 杭州租索尼/A7M5/A7M4/A7M3...猴哥相机租赁`（id 1083927299431）。租赁日租价混入机身价会把中位数打穿 |
| `auction` | schema 中确有 `exContent.isAuction`。起拍价不是在售报价。本批 30 条全 false，但字段存在即须覆盖 |
| `promoted_ad` | schema 中确有 `exContent.isAliMaMaAD`。广告位非自然结果。本批全 false |

判定原则（依 §7）：**只排除规则能明确判定的**（配件、求购、故障、租赁、拍卖、广告、异常价、占位/定金）；不确定的进 `review` 并计入 `needs_review_count`，不静默删除。禁止用图片内容推定未公开参数。

`item_kind`：`body`（单机身）/ `kit`（套机）/ `any`。套机与单机身**不得**混入同一价格统计。

### 2.5 数据模型

按指南 §6。三张实表 + 一个视图：

- `products`：`identity_key UNIQUE`、`platform`、`platform_item_id`、`id_source`、`canonical_url`、`title_latest`、`first_seen_at`、`last_seen_at`、`latest_observation_id`
- `observations`：`UNIQUE(run_id, product_id)`、`page_number`、`observed_at`、`title_raw`、`price_raw`、`price_fen`、`currency='CNY'`、`price_parse_status`、`area`、`seller_display_name`、`image_url`、`published_at`、`raw_payload_json`（脱敏限长）、`flags_json`
- `search_runs`：`id` TEXT UUID、`keyword`、`filters_json`、`status`、`started_at`、`ended_at`、`auth_mode`、`pages_requested`、`pages_fetched`、`raw_count`、`stored_count`、`eligible_count`、`error_code`、`error_message`、`warnings_json`、`adapter_version`、`source_commit`
- `run_items`：**视图**，`SELECT run_id, product_id FROM observations`

**为什么用视图而非实表：** §6 明确允许「让 `observations` 的 `(run_id, product_id)` 唯一键兼任关系」，并要求「不要双表重复储存没有用途的信息」。同轮重复分页由 `INSERT OR IGNORE` 处理，语义为**首次观察为准**，`page_number` 记录首见页码。

约束：`PRAGMA foreign_keys=ON`；WAL；单进程串行写；迁移幂等；备份用 `VACUUM INTO`（不在 WAL 下裸拷主库文件）。

隐私：只存公开卖家昵称。**不存** Cookie、token、手机号、地址、私信。上游 `data/session.json` 留在 `upstream/` 内，由用户自行 `chmod 600`。

### 2.6 API

严格实现指南 §8：`POST /v1/search`(202) / `GET /v1/search-runs/{id}` / `GET /v1/products` / `GET /v1/stats` / `GET /health` / `GET /v1/auth/status`，错误码用 §8.6 全集，统一 `{code,message,run_id,retryable,requires_human_action}`。

任务生命周期：`asyncio.Lock` 单实例串行 + 内存队列；**所有状态落库**；应用启动时把遗留 `pending`/`running` 改判 `failed` + `error_code='RUN_INTERRUPTED'`（§8.1 要求，进程崩溃不得永远 running）。

`sort` 枚举对齐实测 `SORT_OPTIONS`：`newest|price_asc|price_desc|default`。`max_pages` 默认 1、上限 3。金额走 `Decimal` 字符串。`city`/`province`/`publish_days` 上游 `SearchFilters` 确实支持，故可透传；**未经实测验证的筛选一律返回 `422 UNSUPPORTED_FILTER`**，不暗称已由平台过滤。

### 2.7 上游 `ret` 码 → 本项目错误码映射

| 上游信号 | 映射 | HTTP | retryable | requires_human_action |
|---|---|---|---|---|
| `mtop.init()` 抛 `初始化闲鱼 token 失败` | `UPSTREAM_UNAVAILABLE` | 503 | true | false |
| `ret` 含 `SUCCESS` | 正常 | 200 | — | — |
| `ret` 含 `FAIL_SYS_TOKEN_EMPTY` / `FAIL_SYS_TOKEN_EXOIRED` / `_EXPIRED` | `AUTH_EXPIRED` | 401 | false | true |
| `ret` 含 `RGV587_ERROR` / `FAIL_SYS_USER_VALIDATE` / `SM` / 滑块 | `CHALLENGE_REQUIRED` | 409 | false | true |
| `ret` 含 `FAIL_SYS_ILLEGAL_ACCESS` / `FLOW_LIMIT` / `LIMIT` | `RATE_LIMITED` | 429 | true | true |
| `ret` 含 `SESSION_EXPIRED` / `NEED_LOGIN` | `AUTH_REQUIRED` | 401 | false | true |
| `httpx.TimeoutException` | `UPSTREAM_TIMEOUT` | 504 | true | false |
| `resultList` 空 + `hasItems=false` | 经核验的真实无结果 → `200 + []` | 200 | — | — |
| `resultList` 空 + `hasItems=true`，或字段路径缺失 | `UPSTREAM_CHANGED`（保存脱敏结构样本） | 502 | false | true |
| 启动时发现遗留 running | `RUN_INTERRUPTED` | 200（查询时） | false | false |

**`200 + []` 只允许表示经核验的真实无结果**（§0.6、§8.6）。

### 2.8 统计口径

按 §7。仅用当前 `run_id`、去重、匹配 `item_kind`、`price_parse_status='valid'` 的样本。

分位数**固定**为 `statistics.quantiles(data, n=4, method='inclusive')` → P25/中位数/P75，单测锁定，README 写明。有效样本 < 8 → `insufficient_sample=true`（MVP 告警阈值，非统计学保证）。

输出：`raw_count`、`distinct_count`、`eligible_count`、`excluded_count` + 按原因分组、`needs_review_count`、`min/median/p25/p75/max`、`lowest_items`（带可追溯链接）、`sample_quality`、`partial`。

---

## 3. 测试策略

| 层 | 运行方式 | 数据 |
|---|---|---|
| 单元测试 | `pytest`，默认运行，离线 | 合成 fixtures，文件头显式标注「SYNTHETIC — 非真实报价」 |
| API 契约测试 | `pytest`，默认运行，离线 | `FakeAdapter`（明确标注 fixture），不打网络 |
| 真实集成测试 | `pytest -m live` 或 `--live`，**手动低频** | 真实闲鱼；默认 CI 绝不触发 |

覆盖指南 §11 全测试矩阵。`tests/fixtures/` 下真实抓取的原始响应若保留，必须脱敏并标注抓取日期与「仅结构参考，非当前市场价」。

**禁止**在 README 写「测试通过」除非附命令、真实输出、环境、日期（§11）。

---

## 4. 已知局限（必须如实报告，不得掩饰）

| 项 | 状态 |
|---|---|
| 验证码 / 风控分支 | 映射逻辑已实现，但**未主动触发验证** → `NOT_VERIFIED_LIVE`。指南 §5.3 要求「不能主动制造大量风控请求」 |
| 登录态下的搜索差异 | 本轮全部为 guest。登录后结果集/可见字段是否不同 → `NOT_VERIFIED_LIVE` |
| 浏览器人工交叉核对 | 需用户点开链接确认。已提供 id `1083967235157`(¥5499) 与 `1085916193246`(¥5642.50) → 待用户确认 |
| `city`/`province`/`publish_days` 筛选真实生效 | 上游 `SearchFilters` 支持且构造了 mtop 参数，但**未实测平台是否真过滤** → 开放前需验证，否则返回 `UNSUPPORTED_FILTER` |
| 上游许可 | **未确认**（§1.3）。未决法律风险 |
| 上游接口漂移 | `mtop.search` 无版本承诺。已用 `adapter_version` + `source_commit` 落库，`UPSTREAM_CHANGED` 时保存脱敏结构样本 |
| 价格口径 | 采集时**公开在售报价**。不是成交价，不含国补/优惠券/议价 |

---

## 5. 目录布局

```
bestprice/
├── README.md
├── IMPLEMENTATION_REPORT.md
├── Xianyu_Agent_Price_Service_Implementation_Guide.md   # 父规范
├── pyproject.toml
├── .env.example  .gitignore
├── docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md   # 本文
├── src/xps/
│   ├── main.py  settings.py  errors.py
│   ├── api/{schemas,search,products,stats,auth}.py
│   ├── adapters/{base,xianyu,fake}.py
│   ├── services/{search_service,normalize,identity,classify,statistics}.py
│   └── storage/{db,schema.sql,repository,migrate}.py
├── tests/
│   ├── fixtures/            # 标注 SYNTHETIC
│   ├── test_price_normalize.py test_identity.py test_classify.py
│   ├── test_stats.py test_repository.py test_api_contract.py
│   ├── test_run_lifecycle.py test_backup.py
│   └── test_smoke_live.py   # -m live，绝不默认运行
├── scripts/
│   ├── verify_upstream.py   # Gate A 探针（已用于产出 §1.5 证据）
│   ├── setup.sh start-local.sh smoke-local.sh backup-sqlite.sh login.sh
├── data/                    # gitignored：price.sqlite3, backups/, probe/
└── upstream/xianyu_spider/  # gitignored，commit eb52bd4，许可未确认
```
