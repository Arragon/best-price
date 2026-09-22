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
    source_commit    TEXT,
    request_fingerprint TEXT,
    exhausted        INTEGER NOT NULL DEFAULT 0 CHECK (exhausted IN (0,1))
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS researches (
    id                    TEXT PRIMARY KEY,
    mode                  TEXT NOT NULL CHECK (mode IN ('model_search','category_research','listing_check')),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','complete','blocked')),
    category              TEXT,
    goal                  TEXT NOT NULL,
    currency              TEXT NOT NULL DEFAULT 'CNY',
    target_budget_fen     INTEGER CHECK (target_budget_fen IS NULL OR target_budget_fen >= 0),
    hard_budget_fen       INTEGER CHECK (hard_budget_fen IS NULL OR hard_budget_fen >= 0),
    allow_alternatives    INTEGER NOT NULL DEFAULT 1 CHECK (allow_alternatives IN (0,1)),
    allow_extra           INTEGER NOT NULL DEFAULT 1 CHECK (allow_extra IN (0,1)),
    max_xianyu_requests   INTEGER NOT NULL CHECK (max_xianyu_requests >= 0),
    used_xianyu_requests  INTEGER NOT NULL DEFAULT 0 CHECK (used_xianyu_requests >= 0),
    pace                  TEXT NOT NULL DEFAULT 'balanced' CHECK (pace IN ('economy','balanced','fast')),
    stopping_reason       TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_profiles (
    id               TEXT PRIMARY KEY,
    research_id      TEXT NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
    profile_version  INTEGER NOT NULL,
    profile_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE(research_id, profile_version)
);

CREATE TABLE IF NOT EXISTS research_runs (
    research_id  TEXT NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
    run_id       TEXT NOT NULL REFERENCES search_runs(id) ON DELETE RESTRICT,
    purpose      TEXT NOT NULL DEFAULT 'focused' CHECK (purpose IN ('discovery','focused','listing_check')),
    linked_at    TEXT NOT NULL,
    PRIMARY KEY(research_id, run_id)
);

CREATE TABLE IF NOT EXISTS model_candidates (
    research_id      TEXT NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
    canonical_model  TEXT NOT NULL,
    variant           TEXT NOT NULL DEFAULT '',
    bucket            TEXT NOT NULL CHECK (bucket IN ('primary','extra','review','excluded')),
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('user_explicit','agent_proposed','market_discovered')),
    source_ref        TEXT,
    reason            TEXT NOT NULL,
    deviation         TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY(research_id, canonical_model, variant)
);

CREATE TABLE IF NOT EXISTS model_evaluations (
    id               TEXT PRIMARY KEY,
    research_id      TEXT NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
    canonical_model  TEXT NOT NULL,
    verdict          TEXT NOT NULL CHECK (verdict IN ('primary','extra','review','excluded')),
    fit_score        INTEGER CHECK (fit_score IS NULL OR (fit_score >= 0 AND fit_score <= 100)),
    evidence_json    TEXT NOT NULL DEFAULT '[]',
    source_summary   TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS text_analyses (
    id                 TEXT PRIMARY KEY,
    observation_id     INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    text_hash           TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    schema_version      TEXT NOT NULL,
    analysis_status     TEXT NOT NULL CHECK (analysis_status IN ('rules_only','succeeded','unavailable','invalid_output')),
    result_json         TEXT NOT NULL,
    error_code          TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(observation_id, text_hash, model_id, prompt_version, schema_version)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id                  TEXT PRIMARY KEY,
    research_id         TEXT NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
    profile_id          TEXT NOT NULL REFERENCES search_profiles(id) ON DELETE RESTRICT,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku_key             TEXT,
    eligibility         TEXT NOT NULL CHECK (eligibility IN ('eligible','extra','review','excluded')),
    price_comparable    INTEGER NOT NULL DEFAULT 0 CHECK (price_comparable IN (0,1)),
    score               INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    score_status        TEXT NOT NULL CHECK (score_status IN ('final','provisional','insufficient_data','not_applicable')),
    subscores_json      TEXT NOT NULL DEFAULT '{}',
    evidence_coverage   INTEGER NOT NULL CHECK (evidence_coverage >= 0 AND evidence_coverage <= 100),
    rule_version        TEXT NOT NULL,
    text_analysis_id    TEXT REFERENCES text_analyses(id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(research_id, profile_id, product_id, rule_version)
);

CREATE TABLE IF NOT EXISTS evaluation_evidence (
    evaluation_id   TEXT NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    evidence_seq    INTEGER NOT NULL,
    code            TEXT NOT NULL,
    source_field    TEXT NOT NULL,
    evidence_text   TEXT NOT NULL,
    start_offset    INTEGER,
    end_offset      INTEGER,
    verified        INTEGER NOT NULL CHECK (verified IN (0,1)),
    PRIMARY KEY(evaluation_id, evidence_seq)
);

CREATE TABLE IF NOT EXISTS risk_flags (
    id                INTEGER PRIMARY KEY,
    evaluation_id     TEXT NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    code              TEXT NOT NULL,
    severity          TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    effect            TEXT NOT NULL CHECK (effect IN ('exclude_from_price','suspend_score','limit_recommendation','inform_only')),
    requires_review   INTEGER NOT NULL CHECK (requires_review IN (0,1)),
    evidence_seq      INTEGER
);

CREATE TABLE IF NOT EXISTS retail_price_runs (
    id             TEXT PRIMARY KEY,
    source_kind    TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('succeeded','partial','failed')),
    error_code     TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS new_price_quotes (
    id                     TEXT PRIMARY KEY,
    retail_run_id          TEXT NOT NULL REFERENCES retail_price_runs(id) ON DELETE CASCADE,
    platform               TEXT NOT NULL CHECK (platform IN ('jd','taobao','tmall','pdd')),
    source_kind            TEXT NOT NULL CHECK (source_kind IN ('official_api','api_verified','browser_observed','agent_submitted','manual_user')),
    external_listing_id    TEXT,
    canonical_product_url  TEXT NOT NULL,
    sku_key                TEXT NOT NULL,
    brand                  TEXT,
    model                  TEXT NOT NULL,
    variant                TEXT,
    bundle_json            TEXT NOT NULL DEFAULT '[]',
    listed_price_fen       INTEGER CHECK (listed_price_fen IS NULL OR listed_price_fen >= 0),
    payable_price_fen      INTEGER CHECK (payable_price_fen IS NULL OR payable_price_fen >= 0),
    shipping_price_fen     INTEGER CHECK (shipping_price_fen IS NULL OR shipping_price_fen >= 0),
    price_conditions_json  TEXT NOT NULL DEFAULT '[]',
    eligibility_json       TEXT NOT NULL DEFAULT '[]',
    stock_status           TEXT NOT NULL DEFAULT 'unknown',
    verification_status    TEXT NOT NULL CHECK (verification_status IN ('verified','conditional','unverified','stale')),
    observed_at            TEXT NOT NULL,
    created_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_matches (
    evaluation_id  TEXT NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    quote_id       TEXT NOT NULL REFERENCES new_price_quotes(id) ON DELETE CASCADE,
    match_status   TEXT NOT NULL CHECK (match_status IN ('exact','equivalent_adjusted','incomparable','unknown')),
    used_total_fen INTEGER,
    new_total_fen  INTEGER,
    saving_fen     INTEGER,
    saving_ratio   TEXT,
    PRIMARY KEY(evaluation_id, quote_id)
);

CREATE TABLE IF NOT EXISTS evaluation_feedback (
    id             TEXT PRIMARY KEY,
    evaluation_id  TEXT NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    verdict        TEXT NOT NULL,
    note           TEXT NOT NULL,
    created_at     TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_research_run           ON research_runs(research_id, run_id);
CREATE INDEX IF NOT EXISTS idx_analyses_observation_version ON text_analyses(observation_id, prompt_version, schema_version);
CREATE INDEX IF NOT EXISTS idx_eval_profile_product   ON evaluations(profile_id, product_id, rule_version);
CREATE INDEX IF NOT EXISTS idx_quotes_sku_observed_at ON new_price_quotes(sku_key, observed_at DESC);

-- §6 关键语义：已入库老商品重新出现时仍属于本轮搜索结果。
-- 用视图而非实表，避免双表重复储存没有用途的信息。
CREATE VIEW IF NOT EXISTS run_items AS
    SELECT run_id, product_id FROM observations;
