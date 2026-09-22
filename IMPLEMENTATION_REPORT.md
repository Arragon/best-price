# 实施报告

> 按指南 §12「`IMPLEMENTATION_REPORT.md` 必填模板」填写。
> 本报告严格区分**真实验证通过**与**仅离线 fixture 验证**。未经真实验证的项一律标注，不伪造成功记录。

---

## 2026-09-23 优化 Phase 0–6：购买研究闭环

状态：`IMPLEMENTED` + `TESTED_WITH_FIXTURE`；真实平台为 `BLOCKED_EXTERNAL`。

- 基线、数据库备份与能力边界记录在 `docs/optimization/baseline.md`。
- 正常末页使用独立 `exhausted` 状态，不再作为失败页导致 `partial`。
- 规范化请求指纹支持运行中合并与近期成功 run 复用；`force_refresh` 只跳过结果缓存。
- `MAX_PENDING_JOBS` 提供有界队列；满载返回 `QUEUE_FULL` 且不调用适配器。
- `scheduler_state` 持久化最后平台尝试、限流冷却和人工处理停止状态。
- 任务级 `pace` 默认兼容旧 `MIN_SECONDS_BETWEEN_SEARCHES`；只有显式
  `ALLOW_FASTER_PACE=true` 后，fast 才能在平台硬下限以上缩短等待。
- CLI 增加全量分页 JSON、`--output`、`--reuse-run`、pace 与强刷选项；默认 text 保持兼容。
- 新 Skill 位于 `.agents/skills/best-price/`；旧 `price-parser` 暂留兼容指针。
- SQLite schema 增量升级至 v4，原始 observations 与 `/v1/stats` 未筛选语义不变。
- Phase 2：SearchProfile、请求预算、run 关联、动态型号候选、型号层/挂牌层分离。
- Phase 3：规则基线、可选 OpenAI-compatible 模型、文本 hash 缓存和原文证据校验。
- Phase 4：确定性五维评分、风险/证据/反馈、同 SKU 跨 run 去重可比统计。
- Phase 5：可审计 Quote Import、Mock 拒绝、URL/平台校验、SKU 匹配和完整成本价差。
- Phase 6：capabilities、结构化 ranked 输出、Skill/README/OpenAPI 与外部闸门文档。

当前真实访问只得到一次 `AUTH_EXPIRED`（0 页、0 商品、未重试），所以没有把 fixture
结果写成 live 结论；详情见基线文档。

最终离线闸门：`.venv/bin/python -m pytest -q` → `452 passed, 4 deselected`；v2 真实备份
副本连续执行 v4 迁移两次，`integrity_check=ok`，10 个 run、422 个 product、509 个
observation 行数不变。阶段与外部门禁矩阵见 `docs/optimization/status.md`；零售候选的
commit、许可、依赖与运行闸门见 `docs/optimization/retail-adapter-audit.md`。

---

## ⚠️ 2026-09-22 架构改造：移除相关性分类筛选层

**本节是最新事实，优先级高于下文所有历史章节。** 下文中描述规则分类器（`classify.py`）、
标签体系、`item_kind` / `eligible_only` / `excluded_by_reason` / IQR 围栏的段落，
均已按段落加了失效标注，但**原始测量数据一律保留**，不改写、不删除。

### 改了什么

删除整个「相关性分类筛选层」，改为把平台字段**清洗后原样透传**给调用方（agent），
由 agent 自己判断哪条商品可比。

| 项 | 变化 |
|---|---|
| `src/xps/services/classify.py`、`tests/test_classify.py` | **已删除** |
| 版本 | `0.1.0` → **`0.2.0`**（`xps.__version__`）；`ADAPTER_VERSION` `xps-xianyu/0.1.0` → **`xps-xianyu/0.2.0`** |
| `SCHEMA_VERSION` | 1 → **2**（加法迁移，见下） |
| IQR 异常低价围栏 | **一并删除**（那同样是筛选）。最低/最高各 3 件照实列出 |

### 为什么删：词表只对相机成立

旧分类器靠「单机身 / 套机 / 机身盖 / 遮光罩」这类**相机词汇**判断商品配置。
非相机品类根本没有这些词，于是所有条目都被打成 `unknown_variant` 送进 review，统计样本直接归零。
更糟的是 `model_mismatch` 的判定逻辑：「标题里没原样出现关键词的型号 token 就排除」——
这是把**证据缺失当成了证据存在**。

下表每行是一轮真实搜索，数据取自仓库里的真实 SQLite 库 `data/price.sqlite3`
（含此前多轮真实抓取的历史数据）。「旧合格」= 旧筛选器允许进入统计的条数
（老库 `search_runs.eligible_count` 落库值）；「新有价」= 现版本 `compute_stats`
在**同一批已入库 observations** 上重算出的 `priced_count`：

| 关键词 | 总条数 | 旧合格 | 新有价 | 旧筛选器主要排除原因 |
|---|---|---|---|---|
| 富士 X-T4 | 60 | 14 | 60 | `rental_or_lease` 22、`deposit_or_placeholder` 21、`needs_review` 15、`wanted_to_buy` 6、`model_mismatch` 3 |
| 富士 X-T4 | 30 | 4 | 30 | `rental_or_lease` 16、`deposit_or_placeholder` 15、`needs_review` 6、`accessory_only` 3、`model_mismatch` 2 |
| xs10相机 | 59 | **0** | 58 | **`model_mismatch` 59（全部）**、`rental_or_lease` 30、`accessory_only` 11 |
| 富士 XS10 | 90 | 52 | 90 | `accessory_only` 21、`needs_review` 12、`model_mismatch` 2 |
| 27寸 4K 144Hz 显示器 | 60 | 4 | 60 | **`needs_review` 44**、`repair_or_fault` 8、`model_mismatch` 4 |
| 27寸4K显示器 电竞 | 90 | **0** | 90 | **`model_mismatch` 87**、`needs_review` 3 |
| 富士X-T4 | 30 | 15 | 30 | `model_mismatch` 11、`needs_review` 3 |

两个显示器关键词几乎全军覆没（90 → 0、60 → 4），`xs10相机` 59 条**全部**被 `model_mismatch` 干掉。
排除原因分布按老库 `observations.exclusion_reasons_json` 与 `needs_review` 逐条聚合而来
（这些列在老库里物理上仍在，代码已不再读写）。

### 代价（如实记录，不只报喜）

不筛选意味着分布里混进了不可比条目。同一批真实数据上，现版本算出的**未筛选**中位数：

| 关键词 | 未筛选中位数 | 说明 |
|---|---|---|
| 富士 X-T4（30 条那轮） | **¥80.00** | 那一轮里全是 ¥40 / ¥50 / ¥90 的日租盘 |
| xs10相机 | **¥325.00** | 混进了配件 |
| 27寸 4K 144Hz 显示器 | ¥233.50 | — |
| 27寸4K显示器 电竞 | ¥820.00 | — |
| 富士 XS10 | ¥5000.00 | — |
| 富士X-T4 | ¥5399.00 | — |

「富士 X-T4 中位数 ¥80」显然荒谬，但它**如实反映了那一轮平台返回的东西**。
所以新契约是：**服务端只给事实和算术，判断可比性是调用方（agent）的责任**，
而且它现在有足够字段做这个判断（见下）。

### 新透传的字段

搜索响应里本来就带这些，旧代码没抽。覆盖率基于 `data/probe/raw_富士_X-T4_p1a_p1.json` +
`…_p1b_p1.json` 两页共 **60 条真实抓取样本**：

| 字段 | 平台来源 | 实测覆盖 |
|---|---|---|
| `description` | `detailParams.title` | 60/60 有值；**42/60 含换行**（`exContent.title` 含换行 **0/60**）；长度 46–1511 字。判断租赁/配件/求购主要靠它 |
| `seller.credit` | `fishTags.r4[].data.content` | 56/60：「卖家信用极好」42、「卖家信用优秀」14 |
| `seller.review_count` / `positive_rate` | `userFishShopLabel.tagList[].data.content` | 60/60；形如「318条评价」「好评率39%」 |
| `area` | `exContent.area` | 60/60；**只到省市**，平台不给更细 |
| `signals.published_text` | `fishTags.r2` | 60/60；形如「8小时前发布」 |
| `signals.want_count` | `fishTags.r3` | 6/60；形如「8人想要」 |
| `price.coupon_text` | `fishTags.r3` | 10/60；形如「券已抵50元」。**关系价格口径：展示价可能已扣券** |
| `signals.free_shipping` | `fishTags.r1` 的 `freeShippingIcon` | 32/60 |
| `signals.labels` | `fishTags.r1` 其余标签 | 「严选」10/60、「验货宝」4/60 |
| `seller.identity` | `exContent.userIdentityShow` | 10/60（「闲鱼严选卖家」） |
| `price.original_text` | `exContent.oriPrice` | 12/60（划线原价） |
| `seller.avatar_url` | `exContent.userAvatarUrl` | 60/60 |
| `media.has_video` | `exContent.showVideoIcon` | 4/60 为真 |
| `media.image_url` | `exContent.picUrl` | 60/60（**只有 1 张主图**） |
| `signals.is_auction` / `is_ad` | `exContent.isAuction` / `isAliMaMaAD` | 该批 0/60、0/60 |

另核实：`detailParams.soldPrice` 与价格控件三段拼接（`sign`/`integer`/`decimal`）在 **60/60** 条上
一致（`soldPrice` 是不带 `¥` 的数值形态）。故价格仍沿用已测过的三段解析，
**未**引入 `soldPrice` 这个冗余来源。

### 多图探测：**做不到**（已实测到风控边界即停手）

搜索响应每件商品**只有 1 张主图**。从闲鱼 PC 站前端 bundle
`https://g.alicdn.com/idle-pc/xy-site/0.0.175/js/p_item-index.js` 里核实出真实详情接口是
`mtop.taobao.idle.pc.detail`，版本 `1.0`，入参 `{itemId}`。
但 2026-09-22 用 guest 身份实调，返回 `RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试!`
—— 阿里的风控/滑块挑战码。按项目红线**立即停手，没有重试、没有绕过**。

- 结论：**未登录拿不到多图**；**登录后能否拿到未实测**（已补入 `NOT_VERIFIED_LIVE` 清单）。
- 顺带记录：`https://www.goofish.com/item?id=…` 与 `https://h5.m.goofish.com/item?id=…`
  都是客户端渲染空壳（分别 **10574 / 3983 字节**），HTML 里没有内嵌商品数据。

### API 契约变化（**破坏性**）

| 端点 | 变化 |
|---|---|
| `POST /v1/search` | 请求体**移除** `item_kind`。因 `extra="forbid"`，传它会得到 `422 INVALID_QUERY` |
| `GET /v1/stats` | **移除** `item_kind` 查询参数。删除 `item_kind`、`eligible_count`、`excluded_count`、`excluded_by_reason`、`needs_review_count`、`suspicious_price_count`；新增 `priced_count`、`unpriced_count`、`unpriced_by_status`（按 `ambiguous`/`missing`/`invalid` 分组）、`auction_count`、`ad_count`、`highest_items`。`sample_quality` **恒含 `unfiltered`**。保留 `min/p25/median/p75/max`（`_yuan` 字符串与 `_fen` 整数两种形态）、`lowest_items`、`insufficient_sample`、`raw_count`、`distinct_count`、`partial`。**分位数算法未变**（inclusive 线性插值、全程 Decimal、ROUND_HALF_UP 取整到分） |
| `GET /v1/products` | `eligible_only` **改名** `priced_only`，语义是「价格能解析成数字」，不是相关性筛选。item 结构改为嵌套：`price{raw,yuan,fen,parse_status,original_text,coupon_text}`、`seller{display_name,credit,review_count,positive_rate,identity,avatar_url}`、`media{image_url,has_video}`、`signals{published_text,want_count,free_shipping,labels,is_auction,is_ad}`，外加顶层 `product_id`、`source_run_id`、`observed_at`、`canonical_url`、`title`、`description`、`area`、`published_at`。删除 `flags`、`item_kind`、`excluded`、`exclusion_reasons`、`needs_review`、`price_text`、`price_yuan`、`seller_display_name`、`image_url`（后四者移入嵌套对象） |
| `GET /v1/search-runs/{run_id}` | `eligible_count` **改名** `priced_count` |
| `GET /help` | 新增 `passthrough` 段（分「平台原话」/「本服务解析结果」/「拿不到」三类）；`must_report`（8 项）与 `must_not`（8 条）已围绕「未筛选」重写 |
| CLI（`python -m xps.cli` / `scripts/query-price.sh`） | 移除 `--kind`。报告新增未筛选声明、无价条目分组、拍卖/广告计数，并把卖家信用 + 好评率 + 券抵扣打在每条最低价样本旁边 |
| `errors.py` | `NO_VALID_RESULTS` 的 agent 行动指引已改写（不再提 `excluded_by_reason` / `item_kind`），改为指向 `unpriced_by_status` |

**新增防呆**：`/v1/products` 与 `/v1/stats` 会**显式 422 拒绝**已移除的筛选参数
（`item_kind`、`eligible_only`、`exclude_rental`、`flags`），而不是像 FastAPI 默认那样静默忽略 ——
因为拿着旧接口记忆传 `item_kind=body` 的调用方会误以为结果已经筛过，那是谎报口径。
实现在 `src/xps/api/deps.py` 的 `reject_removed_filters`。

### 存储层变化

- `observations` **新增 15 列**：`description`、`original_price_text`、`coupon_text`、
  `seller_credit`、`seller_review_count`、`seller_positive_rate`、`seller_identity`、
  `seller_avatar_url`、`has_video`、`published_text`、`want_count`、`free_shipping`、
  `labels_json`、`is_auction`、`is_ad`。
- **不再写入**：`flags_json`、`item_kind`、`excluded`、`exclusion_reasons_json`、`needs_review`。
  这些列在**老库里物理上仍然存在** —— 迁移是加法的，SQLite 删列要重写整表，
  为几列死数据冒这个险不值当；代码不再读写它们。新建库的 `schema.sql` 里已无这些列。
- `search_runs.eligible_count` 通过 `ALTER TABLE … RENAME COLUMN` 改名为 `priced_count`。
- `store_listings` 新增 `untrusted_identity` 计数（身份不可信、造不出 `canonical_url` 的条目），
  由 `search_service` 写成 run 的 `untrusted_identity:N` 警告。这类条目**仍入库**。
  既无平台 ID 又无可信 URL 的条目仍 `storable=False` 不入库，计 `skipped_unidentifiable:N`
  —— 那是数据完整性约束（否则 `identity_key` 全碰撞成 `sha256("")` 同一行），不是相关性筛选。

**迁移验证**（在真实库 `data/price.sqlite3` 的副本上执行 `_migrate_additive`）：

- `PRAGMA user_version` 1 → 2；`PRAGMA integrity_check` → **`ok`**
- **332 个 products / 419 个 observations 全部保留**，行数迁移前后一致
- 重复执行**无副作用**（加列前先查 `PRAGMA table_info`）
- 老 run 仍能正常读出；老行的新字段为 `NULL` —— 这是诚实的，那些字段当时没采集

> ⚠️ 一处**已知不一致**（如实记录）：`RENAME COLUMN` 保留旧值，所以改造**前**跑的老 run，
> `priced_count` 列里躺着的仍是旧筛选口径的数值。实测 run `b1ad3465…`：
> `GET /v1/search-runs/{run_id}` 返回 `priced_count: 14`，而 `GET /v1/stats?run_id=…`
> 对同一 run 现算返回 `priced_count: 60`。`/v1/stats` 永远从 `observations` 重算，不受影响；
> 老 run 的正确计数以 `/v1/stats` 为准。未做数据回填（回填等于用今天的口径改写历史记录）。

### 保留下来的（不是「全删了」）

- **`parse_price`**：全程 Decimal 的价格解析（`float(1.15)*10000 == 11499.999999999998` 那个坑）
  与 `valid`/`ambiguous`/`missing`/`invalid` 四态。**这不是筛选**：解析不出数字时原文照样保留、
  条目照样返回，只是不参与算术，并按状态计入 `unpriced_by_status`。
- **`resolve_identity`**：身份键与去重（平台商品 ID → 可信 URL 稳定参数 → 剥跟踪参数后的 URL SHA-256）。
- 单实例串行锁、逐页节流、任务状态全落库、进程重启判定 `RUN_INTERRUPTED`、只监听 loopback、
  失败不退化成 `200 + 空列表`。
- 上游许可红线不变：`upstream/xianyu_spider` 无 LICENSE 文件，**永不 vendor**，
  只以独立 checkout + 进程内 import 调用。

### 测试现状

```
$ .venv/bin/python -m pytest -q
323 passed, 4 deselected in 5.16s
```

（4 个 deselected 是标了 `live` 的真实网络测试，默认不跑。改造前为 `359 passed, 4 deselected`，
在 HEAD 的独立 worktree 上实测；差额构成为 `tests/test_classify.py` 的 **59 条**随分类层删除，
其余测试文件为适配新契约净增 **23 条**：359 − 59 + 23 = 323。）

新增的契约守护用例：

| 用例 | 守什么 |
|---|---|
| `tests/test_stats.py::test_a_cheap_outlier_stays_in_the_arithmetic` | ¥50 的条目必须留在算术里，防止 IQR 剔除悄悄回来 |
| `tests/test_stats.py::test_result_is_always_marked_unfiltered` | `sample_quality` 恒含 `unfiltered` |
| `tests/test_api_contract.py::test_relevance_filters_are_not_accepted_at_all` | 已移除的筛选参数必须 422，不得静默忽略 |

改造后另有一轮真实采集落库可供追溯：run `026babbc-6e7e-4db0-bb83-691805172f64`
（关键词 `RTX 4090`，1 页，`adapter_version=xps-xianyu/0.2.0`，`raw_count=30`、`priced_count=30`、
`status=succeeded`，库中记录 `auth_mode=logged_in`）。未筛选中位数 ¥23250.00，
而 `lowest_items` 前 3 件里有 2 件其实是 **RTX 4060**（¥2289 / ¥2399）——
这正是「服务端不筛选、判断归调用方」的字面示例。
**登录后与 guest 的结果集差异仍未做系统对比**，见 `NOT_VERIFIED_LIVE`。

---

## 基本信息

- **日期/系统/设备**：2026-09-22（Asia/Taipei）/ macOS 26.6.2 (Build 25G83), Darwin arm64 / 本机 MacBook（用户 `aragon_magic`）
- **Python**：CPython 3.12.13（`uv venv --python 3.12`）。系统自带 `python3` 为 **3.9.6，不满足上游要求的 3.10+**，故未使用。
- **上游项目与 commit**：`superboyyy/xianyu_spider` @ `eb52bd4d1901eee9ba8035e860583cddf50ead4c`
  - commit 日期 `2026-09-12T00:31:52+10:00`（= `2026-09-11T14:31:52Z`）
  - 仓库 `pushed_at` `2026-09-18T09:19:54Z`，`archived: false`，862 stars
  - commit 已钉在 `data/upstream-commit.txt`（gitignored），并落库到每轮 `search_runs.source_commit`
- **关键依赖版本**：fastapi 0.141.1 / uvicorn 0.53.0 / httpx 0.28.1 / pydantic 2.11.10 / tortoise-orm 1.1.8 / playwright 1.63.0 / pytest 9.1.1 / pytest-asyncio 1.4.0

## 许可证：**未确认**（附依据）

| 检查项 | 结果 |
|---|---|
| 仓库根目录 LICENSE / COPYING | **不存在**（`ls -la` 确认；`gh api repos/superboyyy/xianyu_spider/license` → HTTP 404） |
| GitHub 许可证识别 | `license.spdx_id = null` |
| 全仓库许可声明扫描 | 唯一一处为 `README.md:176`：「本项目采用 [MIT License](LICENSE)，请合理使用并注明出处。**数据抓取结果不得用于商业用途**。」—— 其引用的 LICENSE 文件不存在，且附加了与 MIT 冲突的商用限制 |
| `.py` 源码头部 copyright / SPDX | 全部无 |

**结论：许可无法确认。** 依指南 §0.10 / §2 处置：

1. **不复制、不 vendor** 上游任何源码进本项目可分发部分（`git grep` 已确认无上游代码进入本仓库）
2. 上游保留为独立 checkout，位于 gitignored 的 `upstream/xianyu_spider/`
3. 集成方式为**进程内 import 调用其公开函数**，**未修改上游一行代码**（`git -C upstream/xianyu_spider status --short` 为空）
4. 仅限本机个人使用，只监听 `127.0.0.1`，不分发、不商用
5. 此项记为**未决法律风险，本项目不宣布合规**。用户已在知情下选择此路径（选项 A）。

**备选项目核实**：`Usagi-org/ai-goofish-monitor` MIT 已确认；但 GitHub API 返回 `archived: false`，
**与指南 §2 所述「原仓库在 2026-06-09 已归档」矛盾**（最后推送 `2026-05-18`）。未采用：它是
Playwright + Web UI + AI 分析的重型监控系统，面向持续监控而非「一次搜索返回商品列表」，
改造成本显著更高。保留为许可兜底退路。

## 当前使用的采集路径

**指南 §3 选项 A：进程内 import。** 复用上游的签名/协议层，解析与业务判断全部自有：

| 复用（不重写） | 自有实现（刻意不复用上游） |
|---|---|
| `xianyu.mtop.init()` —— 取 `_m_h5_tk` 匿名 token | 逐页串行采集 + 节流（上游 `scrape_xianyu_http` 用 `Semaphore(3)` 并发，且拿不到逐页成败） |
| `xianyu.mtop.search(keyword, page, filters)` —— 单页 mtop 请求与签名，返回**完整原始 JSON** | 原始 JSON → `RawListing` 解析（上游 `handle_data` 会注入「暂无」「价格异常」占位伪数据，且用 float 换算「万」） |
| `xianyu.mtop.probe_login()` —— 登录态探测 | 身份键（上游 `get_link_unique_key` 用 `link.split("&",1)[0]`，指南 §7 明令禁止） |
| `xianyu.search_query.SearchFilters` —— 筛选参数构造 | 价格解析、相关性分类、统计、SQLite 持久化、HTTP API |

> ⚠️ 上表「相关性分类」一项已于 **2026-09-22 移除**（`src/xps/services/classify.py` 已删除），
> 现由「平台字段清洗后原样透传 + 调用方自行判断」取代。其余四项不变。见文首改造章节。

**刻意不走**的三条路：
- 选项 B（上游 HTTP `POST /search/`）：**实测证伪**。该端点只返回 `total_results` / `new_records` / `new_record_ids`，拿不到本轮商品。
- 不启动上游 HTTP 服务：其 `spider.py --host` **默认 `0.0.0.0`**，会多一个对外监听面。
- 不复用上游 `XianyuProduct` 表 / `save_to_db`：其 `link_hash` 身份不稳定，且无 run 关联。

## 实际登录：**guest**（未登录）

- 实测**未登录即可搜索**，返回 `logged_in: false`。故**不触发 `BLOCKED_HUMAN_LOGIN`**。
- 本项目全部真实采集均在 guest 态完成。
- 未索取、未接收、未存储、未打印任何 Cookie / token / user_id。
- `scripts/login.sh` 已提供，供用户本人在需要登录态数据时自行扫码；脚本会自动 `chmod 600 session.json`。
- 当前**无 `session.json`**（用户未登录）。

### 凭证记忆与「不主动过期」（按用户要求实现）

代码路径已逐行核实：

- `persist_login()`（`mtop.py:234`）把 `{cookies, device_id, user, user_id}` 写入
  `SESSION_PATH` = `upstream/xianyu_spider/data/session.json`（由 `config.py` 的 `ROOT_DIR` 推导）
- `mtop.init()`（`mtop.py:132`）调 `load_session()`，若有 cookies 即 `apply_cookies()` 并恢复
  `_device_id` / `_user_info` → **扫一次码，之后每次进程启动自动带登录态**

**发现的上游危险行为**：`probe_login()`（`mtop.py:407`）在 `fetch_login_user()` 抛**任何**异常时
都会走 `invalidate_expired_login()`（`mtop.py:382`），后者执行 `client.cookies.clear()` 并
`clear_session()` —— **直接删除 `session.json`**。也就是一次网络抖动就能永久毁掉用户扫码换来的登录态。

**处置**：`auth_status()` 改为只调 `login_snapshot()`（已核实为纯内存函数：无 `await`、无 `client` 调用），
**绝不调用 `probe_login()`**。语义变为「一次登录后，凭证在本进程生命周期内持续有效，不主动过期」。
真实失效改由搜索时平台返回的 `ret` 码被动反映（`FAIL_SYS_SESSION_EXPIRED` → `AUTH_EXPIRED`）。
`/v1/auth/status` 增加 `verified: false` 字段，如实表明未向平台校验，不让调用方误以为已确认有效。

配套新增 `POST /v1/auth/reload`：重跑 `init()` 重新读取 `session.json`，用户在另一终端登录后
**无需重启服务**。（上游 `login_snapshot()` 读内存 cookie jar，不会自己感知磁盘变化。）
该端点只接受 POST，GET 返回 405。

顺带修正一处错误码语义：`FAIL_SYS_SESSION_EXPIRED` 原被映射为 `AUTH_REQUIRED`（需登录），
实际含义是「曾登录、凭证已失效」，改为 `AUTH_EXPIRED` 并纳入 `HALT_CODES`
（凭证已死时继续翻页无意义）。两者给用户的行动指引不同。

测试覆盖（`tests/test_adapter_lifecycle.py`）：`probe_login` 调用次数恒为 0；
构造一个「探测即销毁凭证」的桩，断言 `session_file_exists` 仍为 True；
`reload()` 能在不重启的情况下拾取新登录态且确实重跑了 `init()`。

## 一页真实搜索：**pass**

| 项 | 值 |
|---|---|
| 关键词 | `富士 X-T4` |
| 排序 / 页数 | `newest` / 1 |
| 返回 | `ret=['SUCCESS::调用成功']`，**30 条**，0.34s |
| 登录态 | guest |
| 有效价格比例 | **30 / 30**（全部可精确解析） |
| 异常 | 无验证码、无风控、无拒绝 |
| 平台自报区间 | `resultInfo.searchResControlFields` = `minPrice:490, maxPrice:2886600, hasItems:true`（单位：分）；本页解析范围 4000–884800 分，为其子集，一致 |

抽样链接（真实可打开，**请人工在浏览器交叉核对**，见「阻塞项」）：

- <https://www.goofish.com/item?id=1083967235157> —— ¥5499「几乎全新富士XT4银色国行…单机+原厂配件+原厂电池」
- <https://www.goofish.com/item?id=1085916193246> —— ¥5642.50「富士 X-T4_银色 经检测…」
- <https://www.goofish.com/item?id=1086948380894> —— ¥90「#重庆同城#索尼a7m4/a7m3/a7c2/a7r3免押租赁」（**租赁盘**，被 `rental_or_lease` 正确排除）

> ⚠️ 上条括注里的 `rental_or_lease` 标签已于 **2026-09-22 随分类层移除**：这类租赁盘现在
> **照常入库、照常进入未筛选算术**，改由调用方读 `description` 自行判断。
> 链接与价格本身是真实采集记录，保留不改。

## 二页及重抓：**pass**

| 轮次 | 参数 | 结果 |
|---|---|---|
| A | 2 页 `newest` | page1 30 条 + page2 30 条，`distinct_item_ids=60`，**`duplicated_ids=0`** → 真翻页，未重复抓第一页 |
| B | 重抓 1 页 | 30 条，与轮次 A page1 **重叠 30/30 = 100%**，商品 ID 完全一致 |

**决定性结论**：重抓时全部 30 件都是「老商品」，上游此时会报 `new_records=0` / `new_record_ids=[]`，
但本轮实际有 30 件真实结果。**证实指南 §3 选项 B 与 §6 `run_items` 的警告成立** ——
`new_record_ids` 绝不等于本轮结果。本服务用 `observations UNIQUE(run_id, product_id)` +
`run_items` 视图解决，已用 `test_run_items_view_covers_every_product_seen_this_round` 锁定。

商品 ID 跨轮稳定：`exContent.itemId` 在轮次 A / B 完全一致，且与关键词无关。

## Gate A 判定：**通过**

- [x] checkout 的 commit 和许可证结论已记录（`eb52bd4…`；许可**未确认**，依据见上）
- [x] 已确认上游真实搜索调用方式及返回形态（`mtop.search` 返回完整原始 mtop JSON；`POST /search/` 只返回摘要）
- [x] 至少一轮可与浏览器交叉核对的真实商品列表（30 条，链接为 `www.goofish.com/item?id=…` 规范形态）—— **浏览器人工交叉核对待用户完成**
- [x] 能返回**本轮所有**商品，不是只返回「新增记录」（重抓 30/30 重叠已证）
- [x] 相同商品跨两次搜索 ID 稳定，不因换关键词而变（`exContent.itemId`）
- [x] 登录失效 / 验证码 / 空结果 / 平台拒绝能够区分（`ret` 码映射 + `probe_login().login_expired` + `resultInfo.hasItems`）—— **验证码分支未主动触发验证，见 NOT_VERIFIED_LIVE**

## 商品标准化 / 数据库：**pass**

命令与结果（2026-09-22，macOS 26.6.2 arm64，CPython 3.12.13，pytest 9.1.1）：

```
$ .venv/bin/python -m pytest -q
359 passed, 4 deselected          # 离线，未访问网络
```

覆盖（对应指南 §Phase2 验收与 §11 矩阵）：

- 相同商品被两轮搜索发现 → `products` 只有 1 个实体、`observations` 2 条（`test_same_product_in_two_runs_is_one_entity_two_observations`）
- 同轮重复分页去重，首次观察为准并记 `page_number`（`test_duplicate_within_one_run_is_deduped_first_wins`）
- 相同标题+价格的不同商品不合并（`test_different_items_with_identical_title_and_price_stay_separate`）
- 相同商品不同跟踪参数 → 同一 `identity_key`；**参数顺序变化也一致**（`test_param_order_does_not_change_key`，专杀 `split("&",1)[0]`）
- 价格原文始终可还原；非法价格 `price_fen=NULL` 而非 0（`test_price_raw_is_preserved_verbatim`、`test_ambiguous_price_stores_null_not_zero`）
- 缺失字段存 `NULL`，不存「暂无」占位（`test_missing_title_is_stored_as_null_not_placeholder`）
- 金额列为 `INTEGER` 而非 `REAL`（`test_money_column_is_integer_not_real`）
- `PRAGMA foreign_keys=ON`、WAL、迁移幂等且不丢数据
- 备份 → 恢复至临时库 → `integrity_check=ok` 且行数匹配（`test_backup_restores_to_a_working_database`）

**真实数据端到端**（run `b1ad3465-6af3-4390-afd2-cff01fd09455`，2026-09-22，guest，2 页）：
`raw_count=60`，`distinct_count=60`，`status=succeeded`，`source_commit=eb52bd4…` 已落库。

## HTTP API：**pass**

> ⚠️ 下表 `/v1/products` 与 `/v1/stats` 两行的参数与响应内容已于 **2026-09-22 改变**
> （`eligible_only` → `priced_only`；`item_kind` 与全部排除原因字段移除，改为未筛选口径 +
> 透传字段）。以下为历史实施记录，当前契约见文首「API 契约变化」与 `README.md`。

接口清单（全部实现并有契约测试）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/search` | `202` + `run_id` + `status_url`；单实例串行；状态全落库 |
| GET | `/v1/search-runs/{run_id}` | 状态、分层计数、警告、`error`、`adapter_version`、`source_commit` |
| GET | `/v1/products` | `run_id` 必填；`eligible_only` / `limit`(默认50,上限100) / `offset`；返回 `total` |
| GET | `/v1/stats` | `run_id` 必填 + `item_kind`；分位数、排除原因分组、`lowest_items`、`sample_quality` |
| GET | `/health` | 进程 + SQLite；登录态丢失**不算**不健康 |
| GET | `/v1/auth/status` | 本地登录态快照 + `verified` 标志；绝不回传 Cookie / user_id |
| POST | `/v1/auth/reload` | 重跑 `init()` 重读 `session.json`；登录后免重启。GET → 405 |
| GET | `/help` | **agent 自助使用说明**；`?format=text` 出纯文本。见下节 |
| GET | `/` | 根路径指路，避免 agent 探测时拿到 404 |
| GET | `/openapi.json`、`/docs` | OpenAPI 文档（实测 `/docs` → HTTP 200，openapi 3.1.0，9 条路径） |

验收（§Phase3）：

- 测试客户端走通 `POST → poll → products → stats` 同一 run（`test_api_contract.py` 全量）
- 进程重启后旧 run 可查，且遗留 `running` 被判定为 `failed` + `RUN_INTERRUPTED`
  （`test_interrupted_runs_are_recovered_on_startup`、`test_recover_interrupted_runs_marks_stale_running_as_failed`）
- **采集失败不退化成 `200 + []`**：失败 run 的 `/v1/products` 直接按上游错误码报错
  （`CHALLENGE_REQUIRED` → 409）；`200 + []` 只在平台自报 `hasItems=false` 时出现，并带
  `page_N_verified_empty` 警告（`test_adapter_failure_is_not_disguised_as_empty_results`、
  `test_verified_empty_result_is_the_only_legitimate_empty_list`）
- 串行锁：两次并发提交，适配器观测到的最大并发数为 1（`test_searches_are_serialised`）
- `city` / `province` / `publish_days` 一律 `422 UNSUPPORTED_FILTER`（平台是否真过滤未经实测）

**真实端到端验证**：`scripts/smoke-local.sh "富士 X-T4" 2 any` 于 2026-09-22 跑通完整链路
（health → auth/status → POST → poll → products → stats），输出见 README「统计方法」一节的真实响应。

> ⚠️ 该行已失效两处：`smoke-local.sh` 现在只接受「关键词 + 页数」两个参数（第三个 `any`
> 是已移除的 `item_kind`）；引用的 README 响应也已换成未筛选口径的新结构。
> 链路本身（health → auth/status → POST → poll → products → stats）不变。

## Agent 客户端：**pass**

> ⚠️ 本节描述的 `render_report()` 输出内容已于 **2026-09-22 改写**：`excluded_by_reason` 分组与
> `needs_review_count` 已随分类层移除，替换为「样本未筛选」声明、`unpriced_count` /
> `unpriced_by_status` 分组、`auction_count` / `ad_count`，并在每条最低价样本旁打印
> 卖家信用 + 好评率 + 券抵扣。「合格样本为 0」的分支改为「有价样本 0 件」。
> 以下为历史实施记录，`--kind` 参数亦已移除。

新增 `src/xps/cli.py` + `scripts/query-price.sh`：一条命令完成 `POST → poll → stats → products`。

```
$ .venv/bin/python -m pytest -q tests/test_cli.py
29 passed
```

（改造后重跑：**32 passed**，2026-09-22。上面的 `29 passed` 是改造前的历史记录。）

设计重点是**把转述纪律固化成代码**，而不是靠提示词约束 agent：

- `render_report()` 无条件输出口径声明（「采集时刻的公开在售报价，不是成交价，不含国补/优惠券/议价」）、
  样本量、`excluded_by_reason` 分组、`needs_review_count`、`sample_quality` 全量、可追溯链接、
  `run_id` / `source_commit` / 采集时间
- `insufficient_sample` 为真时打印醒目告警，且**测试断言输出里不得出现「公允价」**（§7：不产生过度确定的结论）
- 合格样本为 0 时不打印中位数，改为指向 `excluded_by_reason`，并明确「这是筛选结果，不代表平台查不到商品」
- `render_failure()` 按错误码给出具体下一步（`CHALLENGE_REQUIRED` → 到闲鱼 App 完成验证；
  `AUTH_EXPIRED` → `scripts/login.sh` 后 `POST /v1/auth/reload`），并**测试断言输出里不得出现
  「没有商品」「无结果」**（§0.6：失败不得伪装成空结果）
- 退出码：`0` 成功 / `2` 服务或参数问题 / `3` 采集失败 / `4` 轮询超时；失败与超时路径均有测试

### 顺带修掉一个会让 CLI 在 macOS 上直接不可用的坑

现象：`curl http://127.0.0.1:8765/health` 正常，但 CLI 用 httpx 请求同一地址拿到 **HTTP 502**。

根因（已实测确认，非推测）：

```
$ python -c "import urllib.request; print(urllib.request.getproxies())"
{'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890', 'socks': 'http://127.0.0.1:7890'}

$ scutil --proxy
  ExceptionsList : [127.0.0.1, 192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, localhost, *.local, …]
  HTTPProxy : 127.0.0.1   HTTPPort : 7890
```

macOS 系统代理的 `ExceptionsList` **明确包含 `127.0.0.1`**，但 `urllib.request.getproxies()`
返回代理时**不应用这个 bypass 列表**；httpx 默认 `trust_env=True` 用的正是它，于是把本机请求
也塞给了 `127.0.0.1:7890`，代理回 502。curl 自己会应用 bypass，所以 curl 通、httpx 不通 ——
极易被误判成「服务挂了」。

处置：`build_client()` 固定 `trust_env=False`（本 CLI 只连本机，没有任何走代理的理由）。
测试同时断言「httpx 默认客户端**确实**挂载了代理」作为前提护栏，避免该测试在 httpx 行为变化后变成假阳性。

**影响面提示**：任何开着系统代理的 macOS 上，未设 `trust_env=False` 的 Python 客户端都会踩到。
README「故障处理」已加入该条排查说明。

## Agent 自助发现（`GET /help`）：**pass**

```
$ .venv/bin/python -m pytest -q tests/test_help.py
33 passed
```

（改造后重跑：**36 passed**，2026-09-22。上面的 `33 passed` 是改造前的历史记录。）

动机：OpenAPI 只描述「有什么参数」，不传达**价格口径纪律**与「遇到某个错误码该怎么办」。
`GET /help` 补上这两类信息，让 agent 不必先读 README 就能正确使用本服务。
`?format=text` 输出纯文本，便于直接塞进模型上下文。

内容（实测：9 个端点、12 个错误码、6 条 must_report、7 条 must_not）：
价格口径（是什么/不是什么/单位/分位数算法/null 语义）、一条命令入口、四步调用流程、
端点清单（含 POST 请求体的必填字段与全部字段）、上限、任务状态语义、失败语义、
错误码 → `agent_action`、转述必含项、禁止项、登录说明。

> ⚠️ 上述计数已于 **2026-09-22 变化**：`must_report` 6 → **8** 条、`must_not` 7 → **8** 条，
> 均已围绕「样本**未筛选**」重写；另新增 `passthrough` 段（分「平台原话」/「本服务解析结果」/
> 「拿不到」三类）与 `search_request.no_relevance_filters` 明示。端点数（9）与错误码数（12）不变。
> 防漂移机制本身未变，下述测试仍在（`tests/test_help.py`）。

**防漂移设计**（这是本端点的主要工程价值，否则 help 会变成一份会撒谎的文档）：

| 内容 | 派生自 | 漂移即失败 |
|---|---|---|
| 端点清单 | `app.openapi()` 运行时生成 | `test_help_lists_exactly_the_real_routes` 拿真实路由定义反向核对 |
| 搜索请求字段 | `SearchSubmitRequest.model_fields` | `test_help_documents_every_search_request_field` |
| 错误码集合 | `errors.ALL_CODES` | `test_help_covers_every_error_code` |
| 错误码行动指引 | `errors.AGENT_ACTIONS`（**与 CLI 共用同一份**） | `test_cli_failure_rendering_uses_the_shared_action_text` 逐码断言 |
| http/retryable/需人工 | `errors.http_status/is_retryable/requires_human` | `test_every_error_code_entry_is_complete_and_consistent` |

为消除重复，原先写在 `cli.py` 里的 `_NEXT_STEPS` 已上提为 `errors.AGENT_ACTIONS`，
由 CLI 的失败渲染与 `/help` 共同消费 —— 两处不可能再各说一套。

**实现途中发现并绕过的一个版本差异**：FastAPI 0.141.1 把 `include_router` 的结果包成
`_IncludedRouter`，**不再展平进 `app.routes`**，故 `isinstance(route, APIRoute)` 在顶层匹配不到
任何路由（交叉核对会变成空集 == 空集的假阳性）。改为经 `original_router` 下钻；同时该版本在
`add_api_route` 时已把 `router.prefix` 拼进 `route.path`，再乘一次前缀会得到 `/v1/v1/products`。
两处都写了注释，并加了 `test_route_set_helper_actually_finds_routes` 作为护栏，
防止 helper 退化成空集后让相等断言静默通过。

`format` 只接受 `json` / `text`，其他值返回 422 `INVALID_QUERY`（实测 `?format=yaml` → 422）。

## 统计：**pass**

> ⚠️ **本节描述的规则分类器与 IQR 异常值围栏已于 2026-09-22 移除**，以下为历史实施记录，
> 保留原始测量数据以供追溯。当前口径为**零筛选的纯算术**：`tests/test_classify.py` 已删除，
> IQR 下界围栏已删除，`excluded_by_reason` / `eligible` / `review` / `item_kind` 三口径对照
> 均已不存在于 API 中。分位数算法（inclusive 线性插值、全程 Decimal、ROUND_HALF_UP）
> 与 `insufficient_sample` 阈值（8）**未变**。见文首改造章节。

```
$ .venv/bin/python -m pytest -q tests/test_stats.py tests/test_classify.py
89 passed
```

（改造后 `tests/test_classify.py` 已删除，`tests/test_stats.py` 单独重跑：**23 passed**，2026-09-22。）

- 分位数固定为 **inclusive 线性插值**（等价 `statistics.quantiles(n=4, method='inclusive')`），
  但**全程 Decimal 实现**，期望值由手工推导并硬编码在测试里，不用实现自身算期望值。
  已在 README 写明算法。
- `float` 陷阱有专测：`float(1.15)*10000 == 11499.999999999998`、`int(float("1154.99")*100) == 115498`
- 有效样本 < 8 → `insufficient_sample=true`；仍展示已知样本，不产出「市场公允价」
- IQR 下界围栏 `Q1 − 1.5×IQR` 作为标签漏判的第二道防线；样本 < 8 时**不**自动删数据
- `excluded_by_reason` 分组计数；多原因商品按原因分别计数、商品本身只算一次

**真实数据统计结果**（run `b1ad3465…`，60 条原始，guest，2 页；下表数字直接取自
`GET /v1/stats` 的真实响应，非离线推算）：

| 口径 | eligible | excluded | review | min | P25 | 中位数 | P75 | max | insufficient |
|---|---|---|---|---|---|---|---|---|---|
| `any` | 14 | 31 | 15 | ¥4390 | ¥4824.75 | **¥5299.00** | ¥5687.50 | ¥7500 | false |
| `body` | 12 | 33 | 15 | ¥4390 | ¥4772.50 | **¥5249.50** | ¥5349.25 | ¥7500 | false |
| `kit` | 2 | 43 | 15 | ¥6550 | ¥6737.25 | **¥6924.50** | ¥7111.75 | ¥7299 | **true** |

**套机中位数（¥6924.50）显著高于单机身（¥5249.50）**，符合 X-T4 真实市场结构 ——
这是分类规则有效性的独立旁证。`kit` 样本仅 2 件，正确标记 `insufficient_sample`；
`any` 口径正确标记 `mixed_item_kinds`。

`body` 口径排除原因（取自真实响应）：`rental_or_lease 22`、`deposit_or_placeholder 21`、
`wanted_to_buy 6`、`model_mismatch 3`、`accessory_only 2`、`item_kind_mismatch 2`
（总和 56 > `excluded_count` 33，因一个商品可同时命中多个原因）。

> ⚠️ 上表与上段已整体失效：`item_kind` 三口径（`any`/`body`/`kit`）、`eligible`/`excluded`/`review`
> 分层、`excluded_by_reason` 分组、`sample_quality` 里的 `mixed_item_kinds` 均已从代码与 API 中删除。
> 同一个 run（`b1ad3465…`）用现版本代码重算的结果是：`priced_count=60`、未筛选中位数 **¥4745.00**、
> `min ¥40.00` / `max ¥8848.00`、`sample_quality=["unfiltered","guest_auth"]` ——
> ¥40 那几条正是本表当年要排除的日租盘。数字保留，结论作废。

### ⚠️ 真实数据暴露并修复的三类误判（本项目最重要的诚实发现）

> ⚠️ **本节描述的规则分类器已于 2026-09-22 移除**，以下为历史实施记录，
> 保留原始测量数据以供追溯。`tests/test_classify.py` 已随之删除（含下文提到的
> `test_conflicting_body_and_kit_prices_still_go_to_review`）。
> 这三类误判本身就是「词表规则不可靠」的证据链的一部分：修完相机品类的三个坑之后，
> 换到显示器品类仍然全军覆没（见文首新旧对比表），最终结论是**这一层不该由服务端做**。

第一版规则用**裸子串匹配**，在真实 60 条数据上误删了正常整机。离线用库中真实标题复核后定位并修复，
每一条都以真实标题原文钉成回归测试（`tests/test_classify.py`）：

| # | 症状 | 根因 | 修复 | 效果 |
|---|---|---|---|---|
| 1 | `repair_or_fault` 命中 **21** 条，其中 **4 条是仅因此一项就被排除的正常整机** | 「**无拆修**」是卖家声明*没有*拆修；「避免磕碰、受潮、**进水**等」是租赁须知；「支持(人为损坏、**进水**进液…)」是质保条款 | 逐个出现位置检查：被否定词（无/没/未/非/免，4 字窗口）覆盖、或紧跟顿号/逗号枚举分隔符的，一律跳过 | 21 → **0**（本批确实无故障机），4 台整机救回 |
| 2 | `accessory_only` 命中 **8** 条 = 1 件真配件 + **4 台 ¥5100–6450 的正常整机** + 3 条回收服务 | 真整机常在标题**后段**列附带清单（「配件:品牌电池2块 充电器」「全套包装配件都在送国产皮套」），而真配件一定在**开头**点明 | 配件词只在标题前 40 字的自述范围内匹配 | 8 → **1**（只剩那块 ¥100 的真电池）；3 条回收服务仍被 `wanted_to_buy` 正确排除，只是不再多背一个错标签 |
| 3 | 上述救回的整机全部落到 `review`，合格样本仍被抽空 | (a)「**机身**外观轻微使用痕迹」里的「机身」是外观描述，不是单机身信号，却与「套机」判成冲突；(b)「成色**如图**」是外观措辞，在二手挂牌里几乎无处不在，却被当成「规格推给图片」 | (a) 区分强信号（单机身/单机/裸机/主机/本体）与弱信号（机身），套机胜过弱信号；(b) 图片措辞不再单独触发 review | 同一批 60 条标题离线重算：eligible(any) **9 → 14** |

**刻意保留为 review 的难例**：实测有一条「**单机身5499，套机6299**包邮」——标题里两个配置两个价，
规则无法确定 ¥5499 的挂牌价对应哪个配置。按指南 §7「不确定样本放入 review」，**诚实地进 review，不硬猜**。
已用 `test_conflicting_body_and_kit_prices_still_go_to_review` 锁定该行为。

### 指南 9 个标签之外新增的 4 个（均有实测依据）

> ⚠️ **标签体系已于 2026-09-22 整体移除**，以下为历史实施记录。
> 其中 `auction` / `promoted_ad` 两条的**事实依据仍然有效并已保留**：
> `exContent.isAuction` / `isAliMaMaAD` 现以 `signals.is_auction` / `signals.is_ad` 原样透出，
> 并在 `/v1/stats` 里汇总为 `auction_count` / `ad_count` 与
> `includes_auction_start_prices` / `includes_promoted_ads` 质量标记 ——
> 但它们是**事实标记，不再是排除依据**。`rental_or_lease` / `model_mismatch`
> 这两条纯词表规则已彻底删除（后者正是把「证据缺失当成证据存在」的元凶）。

| 新标签 | 实测依据 |
|---|---|
| `rental_or_lease` | 两轮真实搜索都大量出现租赁盘（¥40/¥50/¥90），是本批**最大**价格污染源；60 条中命中 22 条。不拦则「富士 X-T4 最低价」= ¥40 |
| `auction` | 实测 schema 确有 `exContent.isAuction`；起拍价不是在售报价。本批 30+30 条全 false，但字段存在即须覆盖 |
| `promoted_ad` | 实测 schema 确有 `exContent.isAliMaMaAD`；广告位非自然结果。本批全 false |
| `model_mismatch` | 搜 X-T4 出现 X-T3 / 索尼 a7m4 等；有明确文本证据时该排除，不该塞进 review 稀释样本。本批命中 3 条 |

## 安全：**pass**

| 检查 | 结果 |
|---|---|
| API 监听地址 | `lsof -nP -iTCP:8765 -sTCP:LISTEN` → `127.0.0.1:8765 (LISTEN)`，**仅 loopback** |
| 非 loopback 绑定防护 | `APP_HOST=0.0.0.0` 且未设 `ALLOW_REMOTE_ACCESS=true` → **启动失败**并报明确原因（`test_settings_refuse_non_loopback_bind_without_explicit_opt_in`） |
| 上游监听面 | **未启动**上游 HTTP 服务（其 `--host` 默认 `0.0.0.0`），改为进程内 import，少一个对外监听面 |
| `.env` | 不存在；`.env.example` 内无任何真实凭据 |
| `session.json` | 不存在（用户未登录）；已在 `.gitignore` 中；`scripts/login.sh` 会 `chmod 600` |
| 数据库文件 | `data/` 整体 gitignored（含 `*.sqlite3` / `-wal` / `-shm` / `backups/` / `probe/`） |
| `upstream/` | gitignored；`git -C upstream/xianyu_spider status --short` 为空（**未修改上游一行代码**） |
| 已跟踪文件敏感串扫描 | `git grep -IE "_m_h5_tk\|sgcookie\|unb=[A-Za-z0-9]"` 仅命中 spec 文档里对 cookie **名称**的说明，无任何值 |
| 原始载荷限长与脱敏 | `RAW_PAYLOAD_MAX_BYTES=4096`；`raw_payload_json` 只放公开商品字段，有专测断言不含 cookie/token/unb/sgcookie |
| 错误响应 | 不回传 Cookie / user_id；`/v1/auth/status` 有专测扫描响应体确认无凭据字样 |

`git status --short --ignored` 确认被忽略项：`.venv/`、`data/`、`upstream/`、各级 `__pycache__/`、`.pytest_cache/`。

---

## 真实已验证范围

以下均在 2026-09-22 于本机对**真实闲鱼**验证通过（guest 态）：

1. 未登录可搜索；`mtop.init()` 取匿名 token 成功
2. 一页真实搜索：30 条，`ret=SUCCESS`，30/30 价格可精确解析，无风控
3. 二页真实翻页：`distinct=60`、`duplicated=0`
4. 同关键词重抓：与首轮 **30/30 = 100%** 重叠，商品 ID 稳定
5. 完整 HTTP 链路：`health → auth/status → POST /v1/search → 轮询 → /v1/products → /v1/stats`
   （`scripts/smoke-local.sh`，run `b1ad3465-6af3-4390-afd2-cff01fd09455`，`status=succeeded`，60 条入库）
6. 真实数据上的分类与统计：eligible 14(any)/12(body)/2(kit)，套机中位数高于单机身
7. 真实数据暴露并修复三类误判（见上表），修复后离线复核 + 重新真实采集双验证
8. `pytest -m live`：**4 passed, 359 deselected in 21.67s**（真实闲鱼，6 次搜索页请求）。
   改动登录态实现后、以及新增 `/help` 后各**重跑过一次**，不是沿用旧结果
9. 备份脚本对真实数据库执行：`integrity_check=ok`，行数 `search_runs=1, products=60, observations=60`
10. `scripts/query-price.sh "富士 X-T4" --kind body --pages 1` 真实跑通，退出码 0：
    run `864e02c6-5891-4d98-9eb2-f8604c7534f7`，30 条原始 → 合格 4 件、排除 20、待核验 6，
    正确触发 `insufficient_sample` + `single_page_only` + `guest_auth` 三项质量限制，
    并输出 4 条可点开的商品链接
11. 本轮 `accessory_only` 命中 3 条，人工逐条核对**全部为真配件**（¥65 绿联 NP-W235 副厂电池、
    ¥70 沣标 NP-W235 副厂电池、¥460 铭匠 AF 35mm F1.8 镜头）；其中镜头那条标题含
    「无拆修」「无霉」却**未**被误判为故障机 —— 否定语境守卫在新的真实数据上再次生效
12. `POST /v1/auth/reload` 与 `GET /v1/auth/status` 对运行中的真实服务调用成功，
    返回 `verified: false`；`GET /v1/auth/reload` 正确返回 405

真实请求总量：**27 次搜索页请求**（另有每个进程/事件循环初始化时的 2 次 token 请求），
分布在 10 次独立运行中，页间隔 ≥ 3 秒、轮次间隔 ≥ 12 秒。**全程未触发验证码、风控或拒绝。**

明细：`verify_upstream.py` 1 页 + 2 页 + 1 页重抓 = 4；`smoke-local.sh` 两轮各 2 页 = 4；
`pytest -m live` 三次各 6 = 18；`query-price.sh` 1 页 = 1。

## 仅离线 fixture 验证范围（`NOT_VERIFIED_LIVE`）

| 项 | 状态 | 说明 |
|---|---|---|
| 验证码 / 滑块 / 风控分支 | **NOT_VERIFIED_LIVE** | 错误码映射与「立即停止翻页」逻辑已实现并有单测（用桩），但**未主动触发真实风控去验证** —— 指南 §5.3 明确「不能主动制造大量风控请求」 |
| 登录态（`logged_in`）下的搜索差异 | **NOT_VERIFIED_LIVE** | 本轮全部为 guest。登录后结果集是否更大、可见字段是否更多，均未验证 |
| `login_expired` 降级路径 / `AUTH_EXPIRED` 真实触发 | **NOT_VERIFIED_LIVE** | `ret` 码 → 错误码的映射由桩测试覆盖，但**没有真实失效凭证**可用于触发。且新设计刻意不主动探测，因此该路径只会在真实搜索被平台拒绝时才走到 |
| `POST /v1/auth/reload` 在**已登录**状态下的效果 | **NOT_VERIFIED_LIVE** | 已在 guest 态对真实服务调用成功（返回 `verified:false`，`init_calls` 递增由桩测试验证）。但「登录后 reload 能否正确切到 `logged_in`」需要真实 `session.json`，当前没有 |
| `login_snapshot()` 与真实 `session.json` 的字段契合度 | **NOT_VERIFIED_LIVE** | 桩忠实模拟了 `init()` 从磁盘加载语义，但未用真实凭证验证过 |
| `city` / `province` / `publish_days` 筛选是否真被平台执行 | **NOT_VERIFIED_LIVE** | 上游 `SearchFilters` 确实构造了对应 mtop 参数，但**未实测平台是否真过滤**。故 API 一律返回 `422 UNSUPPORTED_FILTER`，不暗称已过滤 |
| `sort=price_asc` / `price_desc` / `default` 的真实排序效果 | **NOT_VERIFIED_LIVE** | 仅验证 `newest`。枚举值对齐上游实测 `SORT_OPTIONS`，但其余三种的排序结果未抽样核对 |
| 扫脸核身 / Playwright 路径 | **NOT_VERIFIED_LIVE** | 未安装 Chromium（搜索路径不需要）。已确认 Playwright 仅在 `qr_browser.py:156` 函数内惰性导入 |
| `UPSTREAM_CHANGED` 结构漂移检测 | 仅 fixture | 字段路径哨兵测试存在于 `-m live`（`test_raw_mtop_response_shape_is_unchanged`，本次通过），但漂移后的降级行为只由桩测试覆盖 |
| Windows / SLES 无外网服务器 | 未验证 | 本机为 macOS arm64。指南 §9 指出用户的 SLES 无外网服务器不是首个验证环境 |
| 跨设备访问（Tailscale / 反代 / 认证） | 未实现 | P1，本次范围外 |

## 阻塞项及下一次最小操作

| # | 阻塞项 | 类型 | 用户需做的最小操作 |
|---|---|---|---|
| 1 | **浏览器人工交叉核对**（Gate A 第 3 项的唯一未闭合部分） | 需人工 | 在浏览器打开 <https://www.goofish.com/item?id=1083967235157>（¥5499 单机）与 <https://www.goofish.com/item?id=1086862848607>（¥4390 单机身），确认标题与价格和本服务返回一致。这是 Agent 无法替代的步骤 |
| 2 | 登录态数据未验证 | 需人工（可选） | 本机终端运行 `scripts/login.sh` 自行扫码 → `curl -X POST localhost:8765/v1/auth/reload`（免重启）→ 确认 `/v1/auth/status` 变为 `logged_in`，然后告知我跑一轮 guest vs logged_in 对比。**不要把 Cookie 发给我** |
| 3 | 上游许可未确认 | 需法律判断 | 若要分发或商用，须先向作者取得明确授权，或改用 MIT 的 `ai-goofish-monitor`（需评估改造成本）。当前仅限本机个人使用 |
| 4 | 筛选/排序能力未验证 | 可选 | 如需开放 `city`/`publish_days`/其他 sort，需先各跑一次低频真实搜索并与浏览器结果交叉核对，确认后我再放开对应的 `UNSUPPORTED_FILTER` |

**无阻塞的剩余工作**：P0 已全部交付。P1（关键词保存、定时刷新、降价标记、简易 Web UI）
按指南 §10 Phase 6 需用户确认后再做，本次未实现。

## 改动文件与运行方法

### 运行方法

```bash
scripts/setup.sh                                  # 一次性初始化（幂等）
scripts/start-local.sh                            # 启动服务 127.0.0.1:8765
scripts/query-price.sh "富士 X-T4" --kind body --pages 2   # ★ agent 推荐入口，一条命令出结果
scripts/smoke-local.sh "富士 X-T4" 2 any           # 原始 HTTP 烟测（调试用）
RUN_ID=<已完成的run> scripts/smoke-local.sh ...    # 复用已有 run，不重复打平台
scripts/backup-sqlite.sh                          # 在线备份 + 校验
scripts/login.sh                                  # 可选：人工登录
curl -sS -X POST localhost:8765/v1/auth/reload    # 登录后免重启拾取凭证

.venv/bin/python -m pytest -q                     # 离线测试（默认，不打网络）
.venv/bin/python -m pytest -m live -q             # 真实集成（手动、低频）
.venv/bin/python scripts/verify_upstream.py --keyword "富士 X-T4" --pages 2   # Gate A 探针
```

### 本项目新增文件（全部自有代码，无上游源码）

```
README.md                                        用户文档：启动/登录/API/故障处理/统计算法/许可风险
IMPLEMENTATION_REPORT.md                         本报告
.env.example                                     无敏感值
.gitignore
pyproject.toml                                   自有依赖 + pytest 配置（live 默认排除）
docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md    设计决策与 Gate A 证据
scripts/setup.sh  start-local.sh  login.sh  query-price.sh  smoke-local.sh
scripts/backup-sqlite.sh  verify_upstream.py
src/xps/__init__.py  main.py  settings.py  errors.py  cli.py
src/xps/adapters/base.py  xianyu.py
src/xps/api/schemas.py  deps.py  help.py  search.py  products.py  stats.py  system.py
src/xps/services/normalize.py  identity.py  classify.py  statistics.py  search_service.py
src/xps/storage/db.py  schema.sql  repository.py
tests/conftest.py  helpers.py                    共享 fixture 与路由核对辅助
tests/test_price_normalize.py  test_identity.py  test_classify.py  test_stats.py
tests/test_normalize.py  test_repository.py  test_api_contract.py
tests/test_adapter_lifecycle.py  test_cli.py  test_help.py
tests/test_smoke_live.py                         -m live，默认排除
tests/fake_adapter.py  tests/fixtures/mtop_entry.py     均标注 SYNTHETIC
```

### 上游改动

**无。** `git -C upstream/xianyu_spider status --short` 输出为空，未修改上游任何一行代码。

### Git 提交

```
41dd031 docs: 补齐登录态持久化语义、agent 客户端与代理排查
32a581c feat: 登录态改为进程内长期有效，并新增 agent 一键查询客户端
03f339a docs: README、实施报告与运维脚本
422ff88 fix: 用真实数据修正分类误删，并让上游 init 按事件循环幂等
29ea316 feat: 本地 API、任务生命周期与统计接口（Phase 3/4）
a731f5d feat: 标准化、身份、分类、统计与 SQLite 存储层（Phase 2/4）
f4e217f docs: 记录 Gate A 实测证据与设计决策，钉死上游 commit
```

（`/help` 端点与本轮文档更新在其后提交，故未列出自身哈希。）

---

## 未来适配风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 上游许可始终未确认 | 无法合法分发/商用 | 保持不 vendor、仅本机使用；需要时改用 MIT 备选或取得授权 |
| 上游 `mtop.search` 无版本承诺，平台接口随时可能变 | 采集中断 | 每轮落库 `adapter_version` + `source_commit`；`-m live` 里有字段路径哨兵测试；漂移时报 `UPSTREAM_CHANGED` |
| 上游仓库活跃（最近推送 2026-09-18），函数签名可能变 | import 失败 | `scripts/setup.sh` 会比对 commit 与已验证值并在不一致时告警，要求重跑 `verify_upstream.py` |
| ~~分类规则基于关键词启发式，换品类可能失准~~ **已发生** | 非相机品类样本被大面积误删 | 2026-09-22 用历史真实库核算，确认「27寸4K显示器 电竞」90 条→0 条、「xs10相机」59 条→0 条（全部 `model_mismatch`）。已通过**整体移除该分类层**解决：改为原样透传平台字段，判断交给调用方（见文首改造章节） |
| 服务端不再筛选，未筛选分布可能被人当行情 | 结论被误用 | `sample_quality` 恒含 `unfiltered`；`/help` 的 `must_report` 强制要求转述「样本未筛选」；已移除的筛选参数一律 422，不静默忽略 |
| 平台风控策略变化 | 采集中断 | 出现 `CHALLENGE_REQUIRED`/`RATE_LIMITED` 立即停止，不重试硬撞；不换号换 IP |
| 挂牌价 ≠ 成交价 | 结论被误用 | API 与 README 反复标注口径；`sample_quality` 强制暴露样本限制 |
