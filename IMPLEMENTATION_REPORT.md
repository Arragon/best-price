# 实施报告

> 按指南 §12「`IMPLEMENTATION_REPORT.md` 必填模板」填写。
> 本报告严格区分**真实验证通过**与**仅离线 fixture 验证**。未经真实验证的项一律标注，不伪造成功记录。

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
287 passed, 4 deselected in 6.41s          # 离线，未访问网络
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

接口清单（全部实现并有契约测试）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/search` | `202` + `run_id` + `status_url`；单实例串行；状态全落库 |
| GET | `/v1/search-runs/{run_id}` | 状态、分层计数、警告、`error`、`adapter_version`、`source_commit` |
| GET | `/v1/products` | `run_id` 必填；`eligible_only` / `limit`(默认50,上限100) / `offset`；返回 `total` |
| GET | `/v1/stats` | `run_id` 必填 + `item_kind`；分位数、排除原因分组、`lowest_items`、`sample_quality` |
| GET | `/health` | 进程 + SQLite；登录态丢失**不算**不健康 |
| GET | `/v1/auth/status` | 状态枚举；绝不回传 Cookie / user_id |
| GET | `/openapi.json`、`/docs` | OpenAPI 文档 |

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

## 统计：**pass**

```
$ .venv/bin/python -m pytest -q tests/test_stats.py tests/test_classify.py
89 passed
```

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

### ⚠️ 真实数据暴露并修复的三类误判（本项目最重要的诚实发现）

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
8. `pytest -m live`：**4 passed, 287 deselected in 23.23s**（真实闲鱼，约 6 次搜索页请求）
9. 备份脚本对真实数据库执行：`integrity_check=ok`，行数 `search_runs=1, products=60, observations=60`

真实请求总量：**14 次搜索页请求**（另有每个进程/事件循环初始化时的 2 次 token 请求），
分布在 6 次独立运行中，页间隔 ≥ 3 秒、轮次间隔 ≥ 12 秒。**全程未触发验证码、风控或拒绝。**

明细：`verify_upstream.py` 1 页 + 2 页 + 1 页重抓 = 4；`smoke-local.sh` 两轮各 2 页 = 4；
`pytest -m live` = 6。

## 仅离线 fixture 验证范围（`NOT_VERIFIED_LIVE`）

| 项 | 状态 | 说明 |
|---|---|---|
| 验证码 / 滑块 / 风控分支 | **NOT_VERIFIED_LIVE** | 错误码映射与「立即停止翻页」逻辑已实现并有单测（用桩），但**未主动触发真实风控去验证** —— 指南 §5.3 明确「不能主动制造大量风控请求」 |
| 登录态（`logged_in`）下的搜索差异 | **NOT_VERIFIED_LIVE** | 本轮全部为 guest。登录后结果集是否更大、可见字段是否更多、`login_expired` 降级路径在真实环境的表现，均未验证 |
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
| 2 | 登录态数据未验证 | 需人工（可选） | 如需验证登录后的结果差异：本机终端运行 `scripts/login.sh` 自行扫码，完成后告知，我再跑一轮对比。**不要把 Cookie 发给我** |
| 3 | 上游许可未确认 | 需法律判断 | 若要分发或商用，须先向作者取得明确授权，或改用 MIT 的 `ai-goofish-monitor`（需评估改造成本）。当前仅限本机个人使用 |
| 4 | 筛选/排序能力未验证 | 可选 | 如需开放 `city`/`publish_days`/其他 sort，需先各跑一次低频真实搜索并与浏览器结果交叉核对，确认后我再放开对应的 `UNSUPPORTED_FILTER` |

**无阻塞的剩余工作**：P0 已全部交付。P1（关键词保存、定时刷新、降价标记、简易 Web UI）
按指南 §10 Phase 6 需用户确认后再做，本次未实现。

## 改动文件与运行方法

### 运行方法

```bash
scripts/setup.sh                                  # 一次性初始化（幂等）
scripts/start-local.sh                            # 启动服务 127.0.0.1:8765
scripts/smoke-local.sh "富士 X-T4" 2 any           # 真实低频烟测
RUN_ID=<已完成的run> scripts/smoke-local.sh ...    # 复用已有 run，不重复打平台
scripts/backup-sqlite.sh                          # 在线备份 + 校验
scripts/login.sh                                  # 可选：人工登录

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
scripts/setup.sh  start-local.sh  login.sh  smoke-local.sh  backup-sqlite.sh  verify_upstream.py
src/xps/__init__.py  main.py  settings.py  errors.py
src/xps/adapters/base.py  xianyu.py
src/xps/api/schemas.py  deps.py  search.py  products.py  stats.py  system.py
src/xps/services/normalize.py  identity.py  classify.py  statistics.py  search_service.py
src/xps/storage/db.py  schema.sql  repository.py
tests/test_price_normalize.py  test_identity.py  test_classify.py  test_stats.py
tests/test_normalize.py  test_repository.py  test_api_contract.py  test_adapter_lifecycle.py
tests/test_smoke_live.py                         -m live，默认排除
tests/fake_adapter.py  tests/fixtures/mtop_entry.py     均标注 SYNTHETIC
```

### 上游改动

**无。** `git -C upstream/xianyu_spider status --short` 输出为空，未修改上游任何一行代码。

### Git 提交

```
422ff88 fix: 用真实数据修正分类误删，并让上游 init 按事件循环幂等
29ea316 feat: 本地 API、任务生命周期与统计接口（Phase 3/4）
a731f5d feat: 标准化、身份、分类、统计与 SQLite 存储层（Phase 2/4）
f4e217f docs: 记录 Gate A 实测证据与设计决策，钉死上游 commit
```

---

## 未来适配风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 上游许可始终未确认 | 无法合法分发/商用 | 保持不 vendor、仅本机使用；需要时改用 MIT 备选或取得授权 |
| 上游 `mtop.search` 无版本承诺，平台接口随时可能变 | 采集中断 | 每轮落库 `adapter_version` + `source_commit`；`-m live` 里有字段路径哨兵测试；漂移时报 `UPSTREAM_CHANGED` |
| 上游仓库活跃（最近推送 2026-09-18），函数签名可能变 | import 失败 | `scripts/setup.sh` 会比对 commit 与已验证值并在不一致时告警，要求重跑 `verify_upstream.py` |
| 分类规则基于关键词启发式，换品类可能失准 | 误删/漏删 | 规则全部可解释、可追溯（`flags` + `exclusion_reasons` 逐条落库）；不确定样本进 review 而非删除；换关键词后应人工抽样复核 |
| 平台风控策略变化 | 采集中断 | 出现 `CHALLENGE_REQUIRED`/`RATE_LIMITED` 立即停止，不重试硬撞；不换号换 IP |
| 挂牌价 ≠ 成交价 | 结论被误用 | API 与 README 反复标注口径；`sample_quality` 强制暴露样本限制 |
