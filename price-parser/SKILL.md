---
name: xianyu-price-api
description: 查闲鱼某商品的在售挂牌信息（完整描述、卖家信用、地址、价格、平台标记、可追溯链接）与未筛选的报价分布，通过本机常驻 HTTP 服务 127.0.0.1:8765 完成。当用户问"闲鱼上 X 多少钱"、二手行情、比价、某型号相机或显卡或 CPU 或显示器的挂牌价，或要拉起/重启该服务、诊断调用失败时使用。
---

# 闲鱼在售商品信息 API

## Overview

调用本机常驻的 bestprice 服务，对闲鱼做一次真实关键词搜索，去重入库，然后把平台给出的字段**原样透出**给你判断。仓库根固定为 `/Users/aragon_magic/projects/bestprice`（下称 `$REPO`），服务监听 `127.0.0.1:8765`。

```bash
REPO=/Users/aragon_magic/projects/bestprice
```

**这个服务不替你筛选。** 租赁盘、拍卖起拍价、配件、求购、广告位都会照常返回 —— 它给你完整描述、卖家信用、好评率、地址、想要人数、券抵扣、拍卖/广告标记，**由你判断哪条可比**。任何品类都是同一套用法，没有相机专属逻辑。

## 拉起服务

```bash
curl -sS -m 2 http://127.0.0.1:8765/health        # 在跑 → {"status":"ok",...}
nohup $REPO/scripts/start-local.sh > $REPO/data/service.log 2>&1 &   # 没在跑时
```

只起**一个**实例：串行锁与节流是进程内状态，多起一个＝对平台请求量翻倍。第二个实例会因端口占用起不来。日志 `tail -f $REPO/data/service.log`。

## 学用法

接口会演进，`/help` 从代码派生、不会漂移。**不要凭记忆拼参数**：

```bash
curl -sS "http://127.0.0.1:8765/help?format=text"   # 端点、透传字段清单、状态机、错误码→行动、转述纪律
```

## 查询

有 shell 权限就用这个（已封装 POST→轮询→stats→products、超时与错误分诊）：

```bash
$REPO/scripts/query-price.sh "富士 X-T4" --pages 2
$REPO/scripts/query-price.sh "RTX 4090" --pages 2
```

`--pages N`(≤3) `--sort newest|price_asc|price_desc|default` `--min-price` `--max-price` `--top N` `--timeout 300`

退出码 `0` 成功 / `2` 服务或参数问题 / `3` 采集失败 / `4` 轮询超时（**run 仍在跑，别重复提交**，稍后 `curl /v1/search-runs/{run_id}` 取）。

需要 JSON 时直接打 HTTP：

```bash
BASE=http://127.0.0.1:8765
RUN=$(curl -sS -X POST $BASE/v1/search -H 'Content-Type: application/json' \
      -d '{"keyword":"RTX 4090","max_pages":2,"sort":"newest"}' \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
curl -sS $BASE/v1/search-runs/$RUN                                  # 轮询到 succeeded/partial/failed/blocked_login
curl -sS "$BASE/v1/products?run_id=$RUN&limit=100"                  # 主要产出：完整字段 + 可打开的原始链接
curl -sS "$BASE/v1/stats?run_id=$RUN"                               # 未筛选的中位数/分位数
```

自己写 HTTP 客户端时**必须** `trust_env=False`（httpx）：macOS 系统代理会让本机请求拿到 502，看起来像服务挂了。curl 不受影响。

## 你能拿到的字段

`/v1/products` 每个条目（缺失即 `null`，不会有「暂无」这类占位值）：

| 字段 | 内容 |
|---|---|
| `title` / `description` | 单行标题 / **同一篇挂牌文字但保留换行分段**，最长约 1500 字。判断是不是租赁、配件、求购、故障机看 `description` |
| `price.raw` `.yuan` `.fen` `.parse_status` | 价格控件原文、解析结果、解析状态（`valid`/`ambiguous` 面议租金区间/`missing`/`invalid`） |
| `price.original_text` `.coupon_text` | 划线原价；**券标签原文如「券已抵50元」——展示价可能已扣券** |
| `seller.credit` | 平台信用标签原文，实测「卖家信用极好」「卖家信用优秀」 |
| `seller.positive_rate` `.review_count` | 好评率如「39%」、评价数 |
| `seller.identity` `.display_name` `.avatar_url` | 「闲鱼严选卖家」之类身份标识、昵称、头像 |
| `area` | 卖家所在地（**只到省市**，平台不给更细） |
| `signals.is_auction` `.is_ad` | 拍卖位（起拍价）/ 广告位（非自然结果） |
| `signals.want_count` `.published_text` | 想要人数、「8小时前发布」 |
| `signals.free_shipping` `.labels` | 是否包邮；徽标原文「严选」「验货宝」 |
| `media.image_url` `.has_video` | **只有 1 张主图**（见下）、是否带视频 |
| `canonical_url` | 已剥除跟踪参数、可直接点开的链接 |
| `published_at` / `observed_at` | 平台发布时间 / 本服务采集时刻 |

## 转述纪律

回答用户时**必须**带上，缺一不可：

1. 这是**采集时刻的公开在售报价**，不是成交价，不含国补/优惠券/议价结果
2. **样本未筛选**：`/v1/stats` 的分布里含租赁盘、拍卖起拍价、配件、广告位。要么说明你自己剔了哪些、按什么剔的，要么明说这是未筛选分布
3. `priced_count` 样本量，以及 `insufficient_sample` 是否为真
4. `unpriced_count` + `unpriced_by_status`（面议 / 平台没给价格 / 解析不了）、`auction_count`、`ad_count`
5. 你引用的那几件的 `canonical_url` 可追溯链接
6. `started_at`/`ended_at`/`status`/`auth_mode`/`partial`
7. `sample_quality` 数组原样转述（恒含 `unfiltered`）

**禁止**：把挂牌价说成成交价或"市场行情/公允价"；把未筛选的中位数当成该型号的行情直接报出去（实测某轮「富士 X-T4」未筛选中位数被 ¥90 的日租盘拉到 ¥80）；把 `status=failed` 读成"平台没货"；跨 `run_id` 混历史数据凑样本；`insufficient_sample=true` 时给确定性结论；用图片或大模型推断未公开的商品参数补规格；`requires_human_action=true` 时自动重试。

**你自己做筛选时**：依据只能是上面这些字段里的文本证据（`description` 写明租赁/配件/求购、`signals.is_auction` 为真等），并要在回答里说明剔除了几条、为什么。不得凭空推断。

## 能做

关键词搜索在售商品 → 去重入库 → 原样透出平台字段 → 未筛选的中位数/P25/P75/min/max + 最低与最高各 3 件的可追溯链接。**任意品类同一套用法**。未登录(guest)即可搜。

## 不能做（别试错撞墙）

| 想要的 | 现状 |
|---|---|
| 服务端相关性筛选（`item_kind` / `eligible_only` / `flags`） | **已移除**，传了会 `422 INVALID_QUERY`，不会被静默忽略。筛选在你这边做 |
| 商品**多张**图片 | 搜索响应每件只给 1 张主图。详情接口 `mtop.taobao.idle.pc.detail`（入参 `{itemId}`）确实存在，但 2026-09-22 实测 guest 身份调用直接返回 `RGV587_ERROR` 风控挑战。未登录拿不到，也不要绕过 |
| 成交价 / 历史价格趋势 / 降价提醒 | 没有。`/v1/stats` 必须带单个 `run_id`，且明令不得跨 run 混样本 |
| `city` / `province` / `publish_days` 筛选 | 传非 null 一律 `422 UNSUPPORTED_FILTER`（平台是否真过滤未实测） |
| 抓指定商品链接 / 指定卖家 | 没有。只有关键词搜索结果页一种数据源 |
| 精确到区县的地址 | 平台只给到省市（`area`） |
| `max_pages > 3`、并发采集 | 上限 3；`max_concurrent_searches` 强制为 1，串行 |
| 批量关键词轮询 | 架构上不为批量设计（每轮 ≥30s 节流） |

需要以上任一项＝**扩功能**，不是调参，先与用户确认。

## 问题处理

完整错误码及行动指引看 `/help`。最常撞的：

| 现象 | 处理 |
|---|---|
| `priced_count` 为 0 | 看 `unpriced_by_status`：`ambiguous` 多是面议/租金，`missing` 是平台没给价格控件。用 `/v1/products`（不带 `priced_only`）读原文自行判断 |
| `RATE_LIMITED` | 停手。不要重试、不要换账号或代理。调大 `MIN_SECONDS_BETWEEN_SEARCHES`，由用户决定何时再跑 |
| `CHALLENGE_REQUIRED` | 平台要人工验证。交还用户本人到闲鱼 App/网页完成，不自动重试、不绕过 |
| `AUTH_REQUIRED` / `AUTH_EXPIRED` | 用户本人跑 `$REPO/scripts/login.sh` 扫码 → `curl -X POST $BASE/v1/auth/reload`（免重启）。guest 搜索通常仍可继续 |
| `UPSTREAM_CHANGED` | 上游/平台结构变了。重跑 `$REPO/scripts/verify_upstream.py` 核对字段路径 |
| `UPSTREAM_UNAVAILABLE` | 查网络、`upstream/` checkout、依赖（`$REPO/scripts/setup.sh`） |
| 服务起不来 | `APP_HOST` 非 loopback 且未设 `ALLOW_REMOTE_ACCESS=true`，或 `MAX_CONCURRENT_SEARCHES != 1` → 校验器直接拒绝启动，看日志报错 |
| 连不上但进程在 | 先排系统代理（见上），再看端口 `lsof -nP -iTCP:8765 -sTCP:LISTEN` |
| `RUN_INTERRUPTED` | 服务在采集中途重启过，重新发起搜索 |
| warnings 里有 `untrusted_identity:N` | 这 N 件已入库但主机不在实测白名单，`canonical_url` 为 null，没法给用户可点开的链接 |

改配置前备份：`$REPO/scripts/backup-sqlite.sh`（`VACUUM INTO` + 立即校验，不覆盖已有文件）。

## 红线

- **授权范围**：目前只授权**低频验证/烟测**。批量抓取、定时高频刷新、多关键词轮询**先征得用户同意**再动。
- 节流参数只能调大不能调小；不用多账号、代理池绕风控。`RGV587` 之类风控挑战一律停手交还用户。
- 绝不索取或打印 Cookie、密码、短信验证码。登录只能由用户本人在本机终端完成。
- `upstream/xianyu_spider` 许可未确认（仓库无 LICENSE 文件）：**不要**把它 vendor 进项目，也不要建议这么做。`upstream/` 与 `data/` 必须保持 gitignored。
- 服务只监听 loopback。跨设备访问用 Tailscale / SSH 隧道，不要直接改 `0.0.0.0`。

## 延伸阅读

`$REPO/README.md`、`$REPO/IMPLEMENTATION_REPORT.md`（含 `NOT_VERIFIED_LIVE` 清单：验证码分支、登录态差异、其他 sort 的真实排序效果、登录后详情接口能否拿到多图，均未实测）。
