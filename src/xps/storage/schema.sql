-- 闲鱼本地价格服务 schema（指南 §6）
-- 幂等：全部对象用 IF NOT EXISTS，可反复执行；升级前先 scripts/backup-sqlite.sh。
--
-- 金额一律 INTEGER 分，绝不使用 REAL 做交易金额基础类型。
-- 时间一律 TEXT，ISO-8601 UTC（...Z），字典序即时间序。
--
-- 与指南 §6 表格的两处有意偏离（均为遵守更高优先级的 §0.4「严禁占位伪数据」）：
--   products.title_latest   指南标 NOT NULL，此处可空 —— 平台未给标题时存 NULL，不填「暂无」
--   products.canonical_url  指南标 NOT NULL，此处可空 —— 身份不可信的条目仍留档追溯，
--                             但由 id_source='invalid_identity' + 标签排除出可信统计

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
    eligible_count   INTEGER NOT NULL DEFAULT 0,
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
    title_raw              TEXT,
    price_raw              TEXT,
    price_fen              INTEGER,
    currency               TEXT,
    price_parse_status     TEXT NOT NULL
                           CHECK (price_parse_status IN ('valid','ambiguous','missing','invalid')),
    area                   TEXT,
    seller_display_name    TEXT,
    image_url              TEXT,
    published_at           TEXT,
    raw_payload_json       TEXT,
    flags_json             TEXT NOT NULL DEFAULT '[]',
    item_kind              TEXT,
    excluded               INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0,1)),
    exclusion_reasons_json TEXT NOT NULL DEFAULT '[]',
    needs_review           INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0,1)),
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
