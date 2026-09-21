# 闲鱼本地商品搜索与价格统计服务：Agent 实施说明书

> 版本：v1.0（闲鱼单平台 MVP）  
> 编写日期：2026-09-22（Asia/Taipei）  
> 交付对象：Codex / OpenCode / Cursor / Claude Code 等 Coding Agent  
> 交付目标：在用户自己的设备上实现**真实闲鱼搜索 → 商品入库 → 规格筛选 → 在售报价统计 → 可追溯 API**。本文件是开发执行规范，不代表程序已经部署或真实抓取已经通过。

## 0. 给执行 Agent 的指令：先读这一节

你是本项目的实施 Agent。请在本地执行和验证，而不是仅仅输出新的架构建议。遵守以下约束：

1. **只实现闲鱼**。淘宝、京东、转转、爱回收、拼多多均不在本次范围。可预留 `platform` 字段和 Adapter 接口，但不得为未来平台建设复杂插件框架。
2. **优先复用现有开源采集器**，不要从零编写闲鱼请求签名、WebSocket、验证码处理或客户端逆向实现；不要引入 Crawl4AI、Scrapling、Crawlab、Redis、Celery、消息队列或多 Agent 框架来完成这个 MVP。
3. 先验证 `superboyyy/xianyu_spider` 的**当前 HEAD、许可、实际接口与真实采集**。它的 README、历史源码和线上 HEAD 可能不一致，禁止直接依赖某篇博客或旧版代码片段做假设。
4. **严禁用 Mock 数据冒充真实报价**；严禁把当前挂牌价格表述为成交价；严禁把已存历史记录冒充本轮新鲜搜索结果。
5. 登录、扫码、扫脸及可能出现的验证均由用户本人完成。Agent 只能提示操作；不得请求用户在聊天里发送 Cookie、密码、短信验证码，也不得实施验证码绕过或大规模账号/代理轮换。
6. 采集器失败时保存失败状态与原因，不能返回 HTTP 200 + 空列表来伪装“闲鱼目前没有商品”。
7. 任何需要用户实际账号、登录态或市场数据才能完成的验收，应报告 `BLOCKED_HUMAN_LOGIN` 或 `NOT_VERIFIED_LIVE`，继续完成不依赖登录的工作；不要编造成功记录。
8. 优先本机原生 Python 部署，默认只监听 `127.0.0.1`。有实际跨设备使用需求时再加安全接入；**不向公网暴露 Cookie、数据库或采集 API**。
9. 每个阶段结束必须运行相应测试，并记录结果、阻塞项、改动文件与下一步。在真实采集失败的情况下，不要无止境追加功能。
10. 不复制或纳入授权不明的第三方源代码到将分发的项目。许可证未确认时先保留独立仓库评估，优先使用确有 MIT 授权且许可适配的备选项目；需要法律判断的情况记录为风险，不擅自宣布合规。

**成功条件：** 用户在本地给出“富士 X-T4 / 9950X3D / 2TB NVMe SSD”之一的搜索词后，服务返回本轮真实搜索的商品与原始链接；能够排除配件/故障品等明显错配、展示报价中位数和样本量，并让 Agent 通过 HTTP API 获取相同的数据和采集质量说明。

---

## 1. 范围和优先级

### P0：必须交付

- 可人工登录及检测登录状态；未登录时能否搜索，以实际平台情况判定，不假设一定可用。
- 搜索：关键词，最多页数，排序，最低/最高价；地区筛选只在上游实际支持并经过验证时开放。
- 获取**本轮每件商品**的标题、价格原文、标准化金额、商品链接、图片链接、地区、卖家公开昵称（若可见）、采集时间；没有的字段设 `null`，不臆造。
- 稳定去重：同一商品跨关键词和多次搜索不得重复建商品实体，但每次实际观察应保留独立快照与本次任务关联。
- SQLite 持久化；任务状态、错误码、采集新鲜度；含 API 的 OpenAPI 文档。
- 商品相关性规则：模型/机身/套机/配件/故障/可疑低价；必须可追溯，不能由大模型无证据直接删数据。
- 统计：合格样本量、最低/最高报价、中位数、P25/P75、原始搜索数、排除数、异常价格数及代表商品链接。
- 本地启动、重启、备份和自动测试；至少一次真实搜索的人工交叉核验（如果环境与登录条件允许）。

### P1：仅在 P0 真实可用后

- 关键词保存、手工刷新、简单定时刷新（例如单机 APScheduler）。
- 同商品报价历史变化与“发现降价”标记；对下架状态只标记 `not_seen`，不要凭一次未见判定成交/下架。
- 机器间访问：优先 Tailscale/SSH 隧道，必要时加认证与反向代理。
- 最简网页：搜索框、结果表、价格分布、采集状态。**不要求构建完整管理后台**。

### 明确不做

闲鱼私信、自动议价、自动下单、批量账号管理、自动破解验证码、跨平台比价、从零训练商品识别模型、企业级集群、自动代理池、复杂 AI 工作流、以图片识别推定未公开的商品参数。

---

## 2. 参考项目和必须先确认的现实问题

### 首选评估对象

**[`superboyyy/xianyu_spider`](https://github.com/superboyyy/xianyu_spider)**

查询日 README 声称：FastAPI 搜索服务、`httpx` 搜索、Playwright 用于登录相关浏览器流程、Tortoise ORM、默认 SQLite、扫码登录、关键词/分页/排序/价格/地区筛选。文档显示：

- `python spider.py`：启动服务；
- `python spider.py login`：人工完成登录；
- `GET /auth/status`：检查登录态；
- `POST /search/`：触发搜索；
- 搜索结果示例只有 `total_results`、`new_records`、`new_record_ids` 等摘要，**不保证直接包含本轮所有商品对象**；
- 数据目录示例为 `data/`，登录态示例为 `data/session.json`。

**这些仅是核验时的文档记录，不是未经过测试就可依赖的代码契约。** Github 网页搜索缓存仍可能显示旧版 Playwright 搜索代码；执行时须以实际 checkout 的 commit、导入和 `/openapi.json` 为准。

**许可证红线：** 仓库页面的许可证表述并不清晰；执行 Agent 必须在 checkout 后检查仓库根目录 LICENSE/COPYING、README、源码头部、依赖许可和作者说明。不能仅因为 GitHub 仓库可见就假定可以复制、发布、商用。如许可无法确认：停止“复制上游源码并发布”的路径，保留独立评估记录并选择具有明确授权的替代实现或请求授权。

### 备选对象

**[`Usagi-org/ai-goofish-monitor`](https://github.com/Usagi-org/ai-goofish-monitor)**：提供完整 Web UI、任务监控、SQLite 与价格历史，仓库标识为 MIT；但原仓库在 **2026-06-09 已归档**。备选评估时须验证当前闲鱼采集可用性、模型密钥是否为必需及是否可关闭 AI。避免同时运行两套正式数据服务；只选一条采集路径用于 MVP。

### 平台合规与可靠性

低频、仅针对本人购物研究；尊重站点规则。遇到访问拒绝、风险控制、验证码、登录失效要停止自动操作并交还用户。不能承诺“稳定永久爬取”，因为网页与平台接口不受本项目控制。

资料：
- https://github.com/superboyyy/xianyu_spider
- https://github.com/Usagi-org/ai-goofish-monitor
- https://github.com/Usagi-org/ai-goofish-monitor/blob/master/docker-compose.yaml

---

## 3. 本次架构决策

```text
Codex / OpenCode / Cursor / 本地脚本 / （后续）Web UI
                       │
                       ▼
             Local Price API (FastAPI)
     POST /v1/search   GET /v1/search-runs/{id}
     GET  /v1/products GET /v1/stats
     GET  /health      GET /v1/auth/status
                       │
           Search orchestration + rate limits
                       │
              XianyuAdapter（唯一平台）
                       │
            已验证的上游闲鱼采集器
                       │
        商品标准化 / 匹配标记 / 价格解析
                       │
                 SQLite (WAL)
       products | observations | search_runs
       run_items | product_flags | saved_searches(P1)
```

**MVP 最低复杂度原则：** 一个 FastAPI 进程、一个 SQLite 数据库、一条已验证采集通路。上游采集器如必须作为独立进程运行，可形成两个本机进程；不得无理由拆成多个微服务。

### 与上游项目的集成选项（按优先顺序试）

A. 若上游当前源代码暴露稳定的 `search` Python 函数，可通过**明确许可允许的复用方式**包装，在一次调用中取得所有商品；要核对登录逻辑、异步生命周期和数据库行为。

B. 若只能通过 HTTP 搜索：用 `POST /search/` 启动采集，再通过**真实存在且验证过的**商品读接口取回该轮全部商品；若上游返回的是 `new_record_ids`，它们**仅代表新建记录**，不能用它们构成本轮所有商品。

C. 如上游没有本轮商品返回功能，经许可允许、且维护成本可控时增加最小只读 API 或结果回调，输出 `run_id` 对应所有商品。不要假定上游已有 `/products` 端点；不得依赖未验证的私有 ORM 表结构默默读取。

D. 如果 A–C 因登录、实时采集或许可问题不能走通，切换到 MIT 授权的备选采集器，再用同一 Adapter 契约测试。**禁止用几百行新造爬虫来掩盖复用失败。**

### Adapter 契约（在自有代码中定义，不要求上游已有）

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol

@dataclass
class RawListing:
    source_id: Optional[str]
    url: str
    title: str
    price_text: str
    seller_name: Optional[str] = None
    area: Optional[str] = None
    image_url: Optional[str] = None
    published_at: Optional[str] = None
    raw_payload: Optional[dict] = None

@dataclass
class CrawlResult:
    listings: list[RawListing]
    auth_mode: str                # logged_in | guest | unknown
    pages_requested: int
    pages_fetched: int
    warnings: list[str]

class XianyuAdapter(Protocol):
    async def search(
        self,
        keyword: str,
        max_pages: int,
        sort: str,
        min_price: Optional[Decimal],
        max_price: Optional[Decimal],
        city: Optional[str],
    ) -> CrawlResult: ...
```

不要把 `source_id` 伪造为数据库自增 ID；无法提取平台商品 ID 时使用规范化链接指纹作后备键，同时显式标记 ID 来源。

---

## 4. 目录布局（建议而非必须机械照搬）

```text
xianyu-price-service/
├── README.md
├── IMPLEMENTATION_REPORT.md       # Agent 每阶段更新
├── pyproject.toml                 # 本项目依赖及测试配置
├── .env.example                   # 无敏感值
├── .gitignore
├── src/xps/
│   ├── main.py                    # FastAPI 装配
│   ├── settings.py                # 环境变量及配置验证
│   ├── api/
│   │   ├── schemas.py             # 外部 API 数据类型
│   │   ├── search.py
│   │   ├── products.py
│   │   └── stats.py
│   ├── adapters/
│   │   ├── base.py
│   │   └── xianyu.py
│   ├── services/
│   │   ├── search_service.py
│   │   ├── normalize.py
│   │   ├── classify.py
│   │   └── statistics.py
│   └── storage/
│       ├── db.py
│       ├── schema.sql
│       └── repository.py
├── tests/
│   ├── fixtures/                  # **标注为合成/脱敏样本**
│   ├── test_price_normalize.py
│   ├── test_identity.py
│   ├── test_classify.py
│   ├── test_stats.py
│   ├── test_api_contract.py
│   └── test_smoke_live.py         # 手动标记，绝不默认运行
├── scripts/
│   ├── start-local.sh
│   ├── smoke-local.sh
│   └── backup-sqlite.sh
├── data/                           # gitignored
│   ├── price.sqlite3
│   └── backups/
└── upstream/                       # 单独 gitignored；许可待验，不 vendor 到发布包
    └── xianyu_spider/
```

不必为不确定的未来功能建立 `interfaces/ports/adapters/plugins/factories` 多级框架。源代码许可不允许的情况下，不能把 `upstream` 当成可随产品重新分发的目录。

---

## 5. 真实采集验证：第一道停止门槛

### 5.1 在有网络、可人工登录的电脑做（优先 MacBook Neo 或 Windows 11）

先用 macOS/Linux 命令示意。若实际为 Windows，Agent 应输出等价 PowerShell 操作并验证，不得把 Bash 命令直接当成 PowerShell 可用。

```bash
# 执行身份：当前普通用户（非 root）；macOS Terminal 或 Linux Bash。
# 创建工作目录。
mkdir -p "$HOME/projects/xianyu-price-service/upstream"
# 切到第三方代码目录。
cd "$HOME/projects/xianyu-price-service/upstream"
# 获取实际源代码；如网络受限，停止并报告，不伪造 clone 成功。
git clone https://github.com/superboyyy/xianyu_spider.git
# 进入上游仓库。
cd xianyu_spider
# 记录用于复现实验的精确提交。
git rev-parse HEAD
# 检查许可文件和当前目录；没有许可证时按本说明书许可门槛处理。
ls -la
# 检查 README 与代码是否一致，先确认 CLI / API 存在。
grep -nE 'login|search|DATABASE_URL|session.json|auth/status' README.md spider.py
# 仅观察当前仓库改动，不覆盖用户可能已有的工作。
git status --short
```

如果发现仓库根本不适合按许可要求集成，仍可记录公开接口和评估结论，但不得继续复制第三方内部实现进新仓库。

### 5.2 创建隔离环境和确认实有接口

```bash
# 执行身份：当前普通用户；在 upstream/xianyu_spider 目录。
# 创建项目独立虚拟环境；若 python3 版本不兼容，记录版本并选择上游支持版本。
python3 -m venv .venv
# 启用虚拟环境。
source .venv/bin/activate
# 打印版本作为环境证据。
python --version
# 安装上游已声明的依赖；若失败，保存完整错误。
python -m pip install -r requirements.txt
# 上游确实需要浏览器/扫码时再安装对应 Chromium，不为纯 HTTP 接口盲装浏览器。
python -m playwright install chromium
# 检查 CLI 参数及实际入口，不预设 README 永远正确。
python spider.py --help
```

若 `python spider.py --help` 启动了服务而非输出帮助，终止服务并记录实际用法；不要当成失败直接改写上游。

### 5.3 登录和搜索（必须观察真实结果）

- 登录：由用户在**本机终端**运行上游文档对应的 `python spider.py login`，用户自行扫码/验证；不可要求用户把 `data/session.json` 发给 Agent。
- 检查 `/auth/status` 是否有效；记录 `logged_in`/`guest` 状态即可，不要记录 Cookie 或 user_id 原值。
- 先在浏览器中人工搜索 `富士 X-T4`（或另一个用户指定关键词），确定该时段确有可见商品。
- 用上游 CLI 的搜索（若支持 `--no-save` 则优先使用）和/或 API 抓**一页**，确认真实标题、价格和可打开的链接。
- 再抓第二页；检查是否真的发生翻页、是否重复抓第一页。
- 重复同关键词；确认第二次搜索中老商品依然能进入**本轮结果**，不能只读 `new_record_ids`。
- 记录搜索用时、抓取数量、有效价格比例、是否有登陆失效/风控、上游版本，不记录会话秘密。

上游 README 给出的 API 请求示例如下，**先从实际 `/openapi.json` 核对请求体方式及字段后再调用**：

```bash
# 执行身份：当前普通用户；上游 API 已在本机启动，并已核验当前实际监听端口。
# 检查上游 OpenAPI 文档，不能默认它一定在 8000 或 8766。
curl -fsS http://127.0.0.1:8000/openapi.json
# 如果 OpenAPI 确认是 JSON Body，提交一页真实搜索；否则按实际契约修改。
curl -fsS -X POST http://127.0.0.1:8000/search/ -H 'Content-Type: application/json' -d '{"keyword":"富士 X-T4","max_pages":1,"sort":"newest"}'
```

**安全说明：** 若 `python spider.py` 默认绑定 `0.0.0.0`，在授权 LAN/WAN 规则验证之前，不要直接让其在公共或不可信网络上长期运行。可在验证代码允许的情况下用 `python -m uvicorn spider:app --host 127.0.0.1 --port 8766`，但必须核对上游是否依赖 `__main__` 做 ORM 注册/启动；**不能机械地认定这条命令一定可用**。如果只能原样启动，就先在受控网络中评估并完成本机监听修复再进入部署阶段。

### 5.4 Gate A 判定

满足以下条件才继续开发 API：

- [ ] checkout 的 commit 和许可证结论已记录。
- [ ] 已确认上游真实搜索调用方式及其返回形态。
- [ ] 至少成功获得一轮可与浏览器交叉核对的真实商品列表（需要人工登录时允许暂时标记阻塞）。
- [ ] 能够返回**本轮所有**商品，或有经许可允许的最小修改方案；不是只能返回“新增记录”。
- [ ] 相同商品跨两次搜索 ID 稳定，不会只因换搜索关键词就改变 ID。
- [ ] 登录失效、验证码、空结果、平台拒绝能够区分。

Gate A 不通过：**停止功能堆叠，按“问题—证据—可逆修复—复验”处理**。两条候选路径均不通过时给出阻塞报告，而不是伪造交付。

---

## 6. 数据模型：为正确的价格统计服务

推荐自己维护以下表；可用 `sqlite3` + 显式 SQL/轻量迁移。MVP 不必额外引入大型 ORM，如团队现有栈已使用 SQLAlchemy 也可合理复用。金额一律存**人民币分 INTEGER**，API 可返回保留两位的人民币字符串；不使用 SQLite `REAL` 做交易金额基础类型。

### `products`：商品实体（每平台商品一个）

| 字段 | 含义 |
|---|---|
| `id` INTEGER PRIMARY KEY | 自有内部主键 |
| `platform` TEXT NOT NULL | 当前固定 `xianyu` |
| `platform_item_id` TEXT NULL | 能真实提取到才填 |
| `identity_key` TEXT UNIQUE NOT NULL | `xianyu:item:<id>` 或规范化链接哈希 |
| `canonical_url` TEXT NOT NULL | 官方商品链接，主机域名白名单验证 |
| `title_latest` TEXT NOT NULL | 最新观察标题 |
| `first_seen_at`, `last_seen_at` | UTC 时间 |
| `latest_observation_id` | 最新快照引用（按需实现） |

### `observations`：本次实际观察记录

| 字段 | 含义 |
|---|---|
| `id`, `product_id`, `run_id` | 主键与关联 |
| `observed_at` | UTC 时间 |
| `title_raw`, `price_raw` | 原始标题和价格文字 |
| `price_fen` INTEGER NULL | 正常解析金额 |
| `currency` TEXT | 固定 `CNY`，如无法确认价格则金额为空 |
| `price_parse_status` | `valid` / `ambiguous` / `missing` / `invalid` |
| `area`, `seller_display_name`, `image_url` | 可观察的公开信息 |
| `published_at` NULL | 真实存在才填；不可用采集时间替代 |
| `raw_payload_json` NULL | 最小必要原始字段，脱敏，限长 |
| `flags_json` | 配件/定金/故障等标记及理由 |

### `search_runs`：每次真实搜索任务

| 字段 | 含义 |
|---|---|
| `id` TEXT | UUID/ULID |
| `keyword`, `filters_json` | 已提交搜索条件 |
| `status` | `pending/running/succeeded/partial/failed/blocked_login` |
| `started_at`, `ended_at` | UTC |
| `auth_mode` | `logged_in/guest/unknown` |
| `pages_requested`, `pages_fetched` | 区分抓了几页 |
| `raw_count`, `stored_count`, `eligible_count` | 层级统计 |
| `error_code`, `error_message`, `warnings_json` | 用户可解释的失败与降级 |
| `adapter_version`, `source_commit` | 本次真实采集来源 |

### `run_items`：本轮“见过该商品”的集合

`(run_id, product_id)` 唯一键。它解决一个关键 bug：**已入库老商品仍然属于本轮搜索结果**；`new_record_ids` 不等于 `run_items`。可让 `observations` 的 `(run_id, product_id)` 唯一键兼任关系，但必须处理同一轮重复分页，明确以首次/最后一次观察为准；不要双表重复储存没有用途的信息。

### 数据库约束

- 关键字段非空及唯一性约束；插入老商品时更新 `last_seen_at`，同轮重复商品去重。
- `PRAGMA foreign_keys=ON`；SQLite WAL；单进程串行写入或通过统一任务队列避免多个爬虫进程同时写数据库。
- 迁移脚本必须幂等且可回滚到上一个备份；不得依靠删除 SQLite 文件解决结构升级。
- 仅保存必要的公开卖家展示信息，不保存私人聊天、手机号、地址或 Cookie；敏感凭据保留在上游受限目录。

---

## 7. 商品 ID、价格解析与搜索质量

### 商品 ID

优先使用经实际响应确认的平台商品 ID；其次解析已验证的官方商品 URL 中稳定参数；最后以**去除跟踪参数后的规范化官方 URL**计算 SHA-256。禁止通过“取 URL 第一个 `&` 之前内容”想当然构造稳定主键：这会在不同参数顺序或 URL 形态下产生碰撞/漏匹配。

只接受已确认的闲鱼域名及链接形式（初步候选 `goofish.com`，具体以真实 URL 验证）；拒绝伪造短链、非 http(s) 和不可信主机。对不能识别的 URL 标 `invalid_identity`，不要将其自动放进可信统计。

### 价格

- 合法单价示例：`¥3500`、`3,500.00`、`0.35万`，解析成分并保留原文。
- 不能直接当成完整商品总价：`1元占位`、`定金200`、`价格私聊`、`面议`、`3500起`、`3500-4500`、`不包邮` 中未确认的运费；这些应标记 `ambiguous`，不参与合格样本统计。
- 非法/无法解析价格设置 `price_fen=null`，不得转换为 0。
- 不猜国补、优惠券、议价后的成交价；这里的价格口径为**采集时公开在售报价**。

### 相关性过滤（先做解释性规则）

每个商品可同时具有多个标签：

`exact_model`、`compatible_variant`、`bundle`、`accessory_only`、`repair_or_fault`、`wanted_to_buy`、`deposit_or_placeholder`、`suspicious_price`、`unknown_variant`。

针对 X-T4 需要测试的标题包括：

- `富士 X-T4 单机身`：可保留进“单机身”集合。
- `富士 X-T4 18-55 套机`：归入“套机”，不能与单机身直接求同一价格均值。
- `X-T4 电池/皮套/快门线`：排除机身统计。
- `X-T4 进水不开机`：归入故障机，排除正常机身统计。
- `收 X-T4` / `求购 X-T4`：非卖盘，排除。
- `XT4，详情见图，3500`：存在型号可疑歧义时标记待核验，不能由大模型把图片内容当已核实事实。

默认只对**能被规则明确判断的配件、求购、故障、异常价格**排除；不确定样本放入 `review`，统计端同时报告排除和待核验数量。允许搜索条件指定期望 `item_kind=body|kit|any`。后续 AI 仅可建议标签并附证据，不可无痕覆盖原始字段。

### 统计口径

仅使用当前 `run_id` 的、去重的、明确匹配商品范围且 `price_parse_status=valid` 的商品。输出：

- `raw_count`：本轮捕获的原始条目数；
- `distinct_count`：不同商品数；
- `eligible_count`：同配置且价格有效的样本数；
- `excluded_count` 和按原因分组的数量；
- `needs_review_count`：待人工判定；
- `min/median/p25/p75/max`；
- `lowest_items`：低价代表样本的链接，必须能追溯原始条目；
- `sample_quality`：样本过少、只抓一页、未登录、仅部分页成功等明确限制。

采用固定的可测试分位数算法，在 README 写明实现方式（例如 Python `statistics.quantiles` 或自定义线性插值并用单测固定），不要前后多次计算采用不同分位数定义。有效样本少于 **8 件**时标记 `insufficient_sample=true`；统计仍可展示已知样本，但不产生过度确定的“市场公允价”。8 件是 MVP 告警阈值，不是统计学保证。

**历史报价**只记录本服务观察到的报价变化。已下架商品、实际成交金额和商品真伪都不能靠搜索结果推断。

---

## 8. Agent 用 API 契约（本项目需要实现，不是上游现成接口）

### 8.1 `POST /v1/search`

提交搜索，立即返回 `202 Accepted` 和任务 ID；让慢速采集不占住 Agent 的 HTTP 会话。MVP 可以先用应用内异步任务 + 单实例串行锁和内存队列，但所有任务状态必须落库；进程崩溃后恢复为 `failed` 并明确 `interrupted`，不可永远停在 `running`。如需重启后自动继续再另行设计持久化队列，MVP 不需要。

```json
{
  "keyword": "富士 X-T4",
  "max_pages": 2,
  "sort": "newest",
  "min_price_yuan": "2500",
  "max_price_yuan": "6500",
  "city": null,
  "item_kind": "body"
}
```

校验：关键词非空且长度受限；`max_pages` 默认 1、上限 3（可配置，但默认保守）；金额字符串通过 Decimal 转换；`sort` 枚举与适配器实测支持能力保持一致。未验证支持的搜索条件应返回 `422 UNSUPPORTED_FILTER` 或明确标注仅后置本地过滤，不能暗称已由平台过滤。

成功接收示例（**接口设计样例，不是实抓结果**）：

```json
{
  "run_id": "6f87f013-bf1c-4b09-8edf-9a986ec91722",
  "status": "pending",
  "status_url": "/v1/search-runs/6f87f013-bf1c-4b09-8edf-9a986ec91722"
}
```

### 8.2 `GET /v1/search-runs/{run_id}`

```json
{
  "run_id": "6f87f013-bf1c-4b09-8edf-9a986ec91722",
  "status": "succeeded",
  "platform": "xianyu",
  "keyword": "富士 X-T4",
  "auth_mode": "logged_in",
  "pages_requested": 2,
  "pages_fetched": 2,
  "raw_count": 0,
  "distinct_count": 0,
  "eligible_count": 0,
  "started_at": "2026-09-22T00:00:00Z",
  "ended_at": "2026-09-22T00:00:10Z",
  "warnings": [],
  "error": null
}
```

> 以上数字仅演示 JSON 结构；测试数据必须明确在代码中标注为 fixture，不可返回给真实用户当作市场结果。

### 8.3 `GET /v1/products?run_id=...&eligible_only=true&limit=50&offset=0`

返回可分页的本轮真实商品，默认 `limit=50`、最大 100，附 `total`。每项包含 `product_id`、`title`、`canonical_url`、`price_text`、`price_yuan`（数字以字符串形式传递或返回 `null`）、`observed_at`、`published_at`、`area`、`flags`、`exclusion_reasons`、`source_run_id`。可见字段缺失即 `null`，不能插入“暂无”的占位伪数据。

### 8.4 `GET /v1/stats?run_id=...&item_kind=body`

输出本轮统计及有效样本 ID/链接。`run_id` 必填，默认绝不跨日期/跨关键词混合历史数据。只请求到 `partial` 的任务，API 必须同时返回 `partial=true` 和失败页数/原因。

### 8.5 `GET /health` 与 `GET /v1/auth/status`

- `/health`：应用和本地 SQLite 是否可访问，不能将登录态丢失直接当成进程不健康。
- `/v1/auth/status`：公开 `logged_in|guest|expired|unknown|human_action_required` 等机器可读信息；不回传 Cookie。

### 8.6 API 错误代码

`INVALID_QUERY`、`UNSUPPORTED_FILTER`、`AUTH_REQUIRED`、`AUTH_EXPIRED`、`CHALLENGE_REQUIRED`、`RATE_LIMITED`、`UPSTREAM_CHANGED`、`UPSTREAM_TIMEOUT`、`UPSTREAM_UNAVAILABLE`、`DB_ERROR`、`NO_VALID_RESULTS`、`RUN_INTERRUPTED`。

具体 HTTP 状态遵循常规约定（参数 `422`、认证受限 `401/409`、超时 `504`、上游不可用 `502/503`、已存在任务 `409` 或返回进行中 run ID）；统一输出 `code`、`message`、`run_id`（如有）、`retryable`、`requires_human_action`。`200 + []` 只能表示经过核验的真实无结果，不能表示上游失败。

### 8.7 Agent 调用流程

```text
POST /v1/search → 得到 run_id
    ↓
GET /v1/search-runs/{id} → pending/running 就等待并查询（有超时上限）
    ↓
succeeded/partial → GET /v1/products?run_id=...
    ↓
GET /v1/stats?run_id=...
    ↓
Agent 输出：统计口径 + 样本量 + 排除规则 + 报价区间 + 商品链接 + 采集时间/状态
```

认证受限、验证码或数据质量不足时，Agent 直报限制并给出用户可执行的重新登录操作；不能继续生成貌似完整的价格报告。

---

## 9. 安全、频率和本地部署

### 初始配置

建议的 `.env.example`：

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8765
DATABASE_PATH=./data/price.sqlite3
XIANYU_ADAPTER=upstream
XIANYU_UPSTREAM_URL=http://127.0.0.1:8766
MAX_SEARCH_PAGES=3
MAX_CONCURRENT_SEARCHES=1
MIN_SECONDS_BETWEEN_SEARCHES=30
ALLOW_REMOTE_ACCESS=false
LOG_LEVEL=INFO
```

这是**拟实现的新服务配置**，不是声称上游原本具有这些变量；Agent 必须由自有 `settings.py` 实现。`MIN_SECONDS_BETWEEN_SEARCHES=30` 仅是保守起点，不代表平台授权频率；出现风控即停止、必要时进一步降低频率。不能通过大量账号/代理绕过限制。

### 部署策略

- 首选本机运行，确认 Python 与 ARM64/Windows 依赖兼容。MacBook Neo 8GB 内存适合低并发、按需采集；不要无依据地宣称能承受长期多浏览器并行。
- 上游登录态与数据库分目录；`data/session.json`（若当前版本确实如此）权限仅限当前用户，可用 `chmod 600` 及安全目录权限；不得存入 `.env.example`、Git、日志或任务结果。
- 初始 API 仅监听 loopback。上游也要绑定 loopback；若无法保证，先修复绑定再部署。
- 将来需要手机/其他电脑访问时，优先可信组网 + 身份校验，**不得只改成 `0.0.0.0` 就宣布安全**。
- Python/浏览器依赖在联网机器安装好；用户的 SLES 无外网服务器不是首个验证环境。只有原生采集成功且确定平台架构后，再考虑容器镜像与离线分发。
- SQLite 每次迁移前备份，在线备份使用 SQLite backup API 或 `VACUUM INTO`（校验 SQLite 版本与实际写权限），避免在 WAL 模式下只拷贝主数据库文件造成不完整备份。

### 失败策略

- `CHALLENGE_REQUIRED`：停止任务、提示人工到合法客户端处理。
- `AUTH_EXPIRED`：提示人工重新登录并继续其他离线功能。
- `RATE_LIMITED`：停止自动重试，记下时间及响应迹象，不“换号+换 IP 硬撞”。
- `UPSTREAM_CHANGED`：保存**脱敏**结构样本与失败字段，记录适配器版本，更新解析测试。
- `UPSTREAM_TIMEOUT`：最多有限次重试，采用退避和总超时；不能并发重试造成放大。

---

## 10. 实施阶段与验收表

### Phase 0：环境与许可调查

**动作：** 克隆并记录 HEAD；核对 license、依赖、启动方式、真实 API；检查系统架构、Python、Playwright/Chromium 是否必要；确认是否能安全绑定 loopback。

**交付：** `IMPLEMENTATION_REPORT.md` 写清：源 commit、许可结论、操作系统、Python、实有启动/登录/API 命令、风险及是否可继续。

**验收：** 无关键信息凭推测填写；没有登录秘密入库。

### Phase 1：真实数据验证

**动作：** 1 页真实搜索；第二页；相同关键词复抓；真实浏览器抽样核对；验证码与未登录分支（不能主动制造大量风控请求）。

**验收：** 至少获得实际商品条目和可验证链接；有本轮完整列表；能识别失败和空结果；如用户未登录，写清 `BLOCKED_HUMAN_LOGIN`，并完成离线模块。

**严格 Gate：** Phase 1 不通过，不进入“市场价格已可用”宣告。

### Phase 2：数据标准化与数据库

**动作：** 写金额解析、ID 规范化、去重、搜索运行与商品快照，加入迁移/备份。

**验收：** 相同商品被两轮搜索发现时 `products` 只有一个实体、`observations` 能记录两次；重复同轮无重复；价格原文始终可还原；错误价格不进入数字统计。

### Phase 3：本地 API 和 Agent 集成

**动作：** 实现第 8 节全部 P0 接口；入参限制；任务生命周期；OpenAPI；实现普通脚本调用示例。

**验收：** 一个测试客户端通过 `POST → poll → products → stats` 获取同一 run 的结果；进程重启后旧 run 可查；崩溃中断不会永远 `running`。

### Phase 4：统计与相关性

**动作：** X-T4 单机身/套机/配件/故障识别；规格歧义提示；中位数/分位数及证据链接。

**验收：** 人工构造的固定 fixtures 有明确预期，相关规则不误删“正常 X-T4 单机身”；不把配件 50 元当成相机最低价；统计只来自同一轮且给出有效样本量。

### Phase 5：部署与现场验收

**动作：** 实际在目标电脑启动；执行真实搜索与人工抽样；检查端口、数据目录、日志脱敏、备份；编写复现操作说明。

**验收：** 用户可重启服务、手动重新登录、发起搜索、查看价格与源链接；`git status` 不出现 `.env`、session、SQLite、用户信息。若无真实登录条件则明确标记“尚未现场验收”。

### Phase 6：可选增量

仅在用户确认需要后，按实际使用频率加入简单定时刷新与轻量 UI；不自动进入多平台阶段。

---

## 11. 测试矩阵（必须真的运行）

| 场景 | 预期 |
|---|---|
| 正常金额 `3500`、`¥3,500`、`0.35万` | 都精确解析为 350000 分 |
| 占位价、定金、价格区间、面议 | `price_fen=null` 或明确 `ambiguous` |
| 相同商品不同跟踪参数 | 同一 `identity_key` |
| 不同商品有相同标题和价格 | 不得合并成同一 ID |
| 两次搜索返回同一商品 | 一个 `products`，两个不同 run 的观察记录 |
| 同一页同商品多次出现 | 本轮 `distinct_count` 去重，不夸大样本量 |
| 返回的新记录为 0，但真实搜索有老商品 | 本轮 `products` 非空；统计正常 |
| 关键词 `富士 X-T4` 混入电池、皮套 | `accessory_only` 并从单机身统计中排除 |
| `X-T4 进水` | `repair_or_fault`，不混入正常机身价 |
| 平台返回验证码/拒绝 | 任务异常/待人工，不伪装为 0 结果 |
| 运行途中进程重启 | 旧 `running` 任务可识别为中断 |
| API 中断、数据库锁、读写异常 | 明确错误码；失败快照不污染统计 |
| SQLite 备份后恢复至临时数据库 | `PRAGMA integrity_check` 通过、业务行数匹配 |
| 真实一页、两页与再次搜索 | 采集页面、商品 ID、价格和源链接经人工抽样核对 |

测试分层：单测 `pytest` 离线默认运行；API 测试用明确的 fixture adapter；真实闲鱼集成测试需显式 `--live` 或环境变量，低频手动运行。默认 CI 不访问用户账号、不调用真实闲鱼。

***禁止*** 在 README 放“测试通过”的结论，除非有相应命令、真实结果、运行环境和日期记录。

---

## 12. 建议 Agent 第一轮实际执行顺序

```text
[1] 查看机器系统、Python 与现有项目目录，防止覆盖已有工作
[2] clone 上游并记录 commit；检查许可、API 源码和 README 是否一致
[3] 复核实际启动/登录方式与回传全部商品的能力
[4] 使用用户本人登录态，运行一次真实低频搜索
[5] 若不具备登录条件，记录 BLOCKED_HUMAN_LOGIN 并做离线解析/测试
[6] 若采集器缺本轮完整数据，先解决最小 Adapter 契约；失败则评估 MIT 备选
[7] 创建本项目最简 FastAPI + SQLite，建立真实 run/observations 数据链
[8] 完成价钱解析、规则标签、报价统计与 API
[9] 运行离线测试 + 真实烟测，记录失败与覆盖边界
[10] 交付启动方式、OpenAPI、使用示例、测试报告和剩余风险
```

### 最终交付清单

- [ ] 可运行源码目录（自有代码与第三方依赖许可边界清楚）
- [ ] `README.md`（本地启动、人工登录、API、故障处理）
- [ ] `.env.example`、`.gitignore`、依赖及版本记录
- [ ] SQLite schema + 可复现的初始化/迁移/备份方式
- [ ] `GET /health`、搜索、任务、商品、统计、登录状态 API
- [ ] 单元与 API 契约测试 + 真实搜索核验记录
- [ ] `IMPLEMENTATION_REPORT.md`，写明哪些真实通过、哪些仅 fixture 通过
- [ ] 标注上游 commit、依赖许可、归档或维护状态及未来适配风险

### `IMPLEMENTATION_REPORT.md` 必填模板

```markdown
# 实施报告
- 日期/系统/设备：
- 上游项目与 commit：
- 许可证：已核验 / 未确认（附依据）
- 当前使用的采集路径：
- 实际登录：logged_in / guest / blocked（不粘贴 Cookie）
- 一页真实搜索：pass/fail/not-run；关键词、数量、抽样链接、异常
- 二页及重抓：pass/fail/not-run；是否包含老商品
- 商品标准化/数据库：pass/fail；测试命令与结果
- HTTP API：pass/fail；接口清单
- 统计：pass/fail；测试命令与结果
- 安全：loopback、敏感文件、gitignore 检查
- 真实已验证范围：
- 仅离线 fixture 验证范围：
- 阻塞项及下一次最小操作：
- 改动文件与运行方法：
```

---

## 13. 可直接交给 Coding Agent 的执行提示词

> 阅读并执行本仓库 `Xianyu_Agent_Price_Service_Implementation_Guide.md`。本轮**只交付闲鱼 P0 MVP**。先按 Phase 0/1 验证 `superboyyy/xianyu_spider` 的当前 commit、许可证、真实接口和完整商品采集；不要误将 `new_record_ids` 当成本轮结果，也不要根据旧源码假定它使用 Playwright 或 HTTP 的哪种搜索实现。验证后实现本地 FastAPI + SQLite + 单平台 Adapter + 商品标准化 + 有证据的价格统计 + Agent API。默认只监听 127.0.0.1；用户自行扫码/验证，绝不索取 Cookie；严禁 Mock 冒充实抓。每阶段运行测试并更新 `IMPLEMENTATION_REPORT.md`，完成可交付的代码与操作说明。遇到真正需要用户登录的阻塞时只指出具体动作，同时继续完成离线模块，不要改用未经核验的新爬虫硬闯。

---

## 附录：文档事实核验链接与时效说明

- `superboyyy/xianyu_spider` README（2026-09-22 查阅）：https://github.com/superboyyy/xianyu_spider/blob/main/README.md
- `superboyyy/xianyu_spider` 当前源码目录（应在 Agent 实际执行时重新拉取）：https://github.com/superboyyy/xianyu_spider
- `Usagi-org/ai-goofish-monitor` README、Docker Compose（原仓库 2026-06-09 归档）：https://github.com/Usagi-org/ai-goofish-monitor

**上述网页只能证明文档声称的功能，不能证明用户设备中目前可完成搜索。真实兼容性、账户权限、数据质量、登录机制和许可边界必须通过 Gate A 重新核实。**
