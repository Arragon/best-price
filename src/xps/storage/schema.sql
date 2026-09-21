-- 闲鱼本地价格服务 schema（指南 §6）
-- 幂等：全部对象用 IF NOT EXISTS，可反复执行；升级前先 scripts/backup-sqlite.sh。
-- 已存在的库由 db.py 的 additive 迁移补列，不会重建、不会丢数据。
--
-- 金额一律 INTEGER 分，绝不使用 REAL 做交易金额基础类型。
-- 时间一律 TEXT，ISO-8601 UTC（...Z），字典序即时间序。
--
-- observations 只存**平台真实给出的字段**，不存任何本服务的相关性判断。
-- 早先版本在这里存过 flags / excluded / needs_review / item_kind（规则分类器的输出），
-- 现已移除：那套词表只对相机成立，换个品类就大面积误杀，判断改由调用方做。
-- 老库里这些列可能仍然存在，属无害残留，代码不再读写。
--
-- 与指南 §6 表格的两处有意偏离（均为遵守更高优先级的 §0.4「严禁占位伪数据」）：
--   products.title_latest   指南标 NOT NULL，此处可空 —— 平台未给标题时存 NULL，不填「暂无」
--   products.canonical_url  指南标 NOT NULL，此处可空 —— 身份不可信的条目仍留档追溯

CREATE TABLE IF NOT EXISTS search_runs (
    id               TEXT PRIMARY KEY,
    platform         TEXT NOT NULL DEFAULT 'xianyu',
    keyword          TEXT NOT NULL,
    filters_json     TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','running','succeeded','partial','failed','blocked_login')),
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    auth_mode        TEXT CHECK (auth_mode IS NULL OR auth_mode IN
                     ('logged_in','guest','expired','unknown','human_action_required')),
    pages_requested  INTEGER NOT NULL DEFAULT 1,
    pages_fetched    INTEGER NOT NULL DEFAULT 0,
    raw_count        INTEGER NOT NULL DEFAULT 0,
    stored_count     INTEGER NOT NULL DEFAULT 0,
    -- 去重后价格可解析（price_parse_status='valid'）的条目数。
    -- 不叫 eligible：本服务不做合格性筛选，这个数只是「有几条能参与算术」。
    priced_count     INTEGER NOT NULL DEFAULT 0,
    error_code       TEXT,
    error_message    TEXT,
    warnings_json    TEXT NOT NULL DEFAULT '[]',
    adapter_version  TEXT,
    source_commit    TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id                     INTEGER PRIMARY KEY,
    platform               TEXT NOT NULL DEFAULT 'xianyu',
    platform_item_id       TEXT,
    id_source              TEXT NOT NULL
                           CHECK (id_source IN ('platform_item_id','url_fingerprint','invalid_identity')),
    identity_key           TEXT NOT NULL UNIQUE,
    canonical_url          TEXT,
    title_latest           TEXT,
    first_seen_at          TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL,
    -- 不加外键：observations 已引用 products，双向约束会成环
    latest_observation_id  INTEGER
);

CREATE TABLE IF NOT EXISTS observations (
    id                     INTEGER PRIMARY KEY,
    product_id             INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    run_id                 TEXT NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    observed_at            TEXT NOT NULL,
    page_number            INTEGER,

    -- 商品文本。title 是搜索页展示的单行标题；description 来自 detailParams.title，
    -- 同一篇文字但保留换行分段（实测 60 条中 42 条含换行，title 0 条含），最长约 1500 字。
    title_raw              TEXT,
    description            TEXT,

    -- 价格。price_raw 是平台价格控件原文，price_fen 是解析结果；解析不出时
    -- price_raw 照样保留、price_fen 为 NULL，条目不删除。
    price_raw              TEXT,
    price_fen              INTEGER,
    currency               TEXT,
    price_parse_status     TEXT NOT NULL
                           CHECK (price_parse_status IN ('valid','ambiguous','missing','invalid')),
    original_price_text    TEXT,
    -- 平台标签原文，如「券已抵50元」。展示价可能已扣券，属价格口径的一部分。
    coupon_text            TEXT,

    area                   TEXT,
    seller_display_name    TEXT,
    -- fishTags.r4 原文，如「卖家信用极好」「卖家信用优秀」
    seller_credit          TEXT,
    seller_review_count    INTEGER,
    -- 「好评率39%」→「39%」
    seller_positive_rate   TEXT,
    -- userIdentityShow，如「闲鱼严选卖家」
    seller_identity        TEXT,
    seller_avatar_url      TEXT,

    -- 搜索响应每个商品只给 1 张主图。多图需商品详情接口，实测 guest 身份调用
    -- mtop.taobao.idle.pc.detail 直接返回 RGV587 风控挑战，故此处只有单图。
    image_url              TEXT,
    has_video              INTEGER NOT NULL DEFAULT 0 CHECK (has_video IN (0,1)),

    published_at           TEXT,
    -- 平台自报的相对时间原文，如「8小时前发布」
    published_text         TEXT,
    want_count             INTEGER,
    free_shipping          INTEGER NOT NULL DEFAULT 0 CHECK (free_shipping IN (0,1)),
    -- fishTags.r1 除包邮图标外的徽标原文，实测取值「严选」「验货宝」
    labels_json            TEXT NOT NULL DEFAULT '[]',

    -- 平台事实标记，不是本服务的排除依据：起拍价与广告位都不是普通在售报价，
    -- 但要不要把它们算进分布由调用方决定。
    is_auction             INTEGER NOT NULL DEFAULT 0 CHECK (is_auction IN (0,1)),
    is_ad                  INTEGER NOT NULL DEFAULT 0 CHECK (is_ad IN (0,1)),

    raw_payload_json       TEXT,
    -- 兼任 run_items 关系（指南 §6 允许）；同轮重复分页由写入端 INSERT 前查重处理，首次观察为准
    UNIQUE (run_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_observations_product   ON observations(product_id);
CREATE INDEX IF NOT EXISTS idx_observations_run_price ON observations(run_id, price_fen);
CREATE INDEX IF NOT EXISTS idx_products_last_seen     ON products(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_runs_started           ON search_runs(started_at DESC);

-- §6 关键语义：已入库老商品重新出现时仍属于本轮搜索结果。
-- 用视图而非实表，避免双表重复储存没有用途的信息。
CREATE VIEW IF NOT EXISTS run_items AS
    SELECT run_id, product_id FROM observations;
