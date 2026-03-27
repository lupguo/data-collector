-- schema.sql — 虾人情报站 v3 全量 Schema
-- t_ 前缀表，所有表和关键字段有 COMMENT
-- 支持 task_id UUID 贯穿全链路
-- 最后更新：2026-03-27（补全 t_llm_usage / t_schedule_config / push_window / source_filter）

-- ===== 删除旧表（v2 遗留）=====
DROP TABLE IF EXISTS ai_news        CASCADE;
DROP TABLE IF EXISTS hackernews     CASCADE;
DROP TABLE IF EXISTS github_trending CASCADE;

-- ===== 删除新表（幂等重建）=====
DROP TABLE IF EXISTS t_llm_usage            CASCADE;
DROP TABLE IF EXISTS t_schedule_config      CASCADE;
DROP TABLE IF EXISTS t_task_stats           CASCADE;
DROP TABLE IF EXISTS t_task_logs            CASCADE;
DROP TABLE IF EXISTS t_item_channel_routing CASCADE;
DROP TABLE IF EXISTS t_item_analysis        CASCADE;
DROP TABLE IF EXISTS t_raw_items            CASCADE;
DROP TABLE IF EXISTS t_sub_tasks            CASCADE;
DROP TABLE IF EXISTS t_tasks                CASCADE;
DROP TABLE IF EXISTS t_push_destinations    CASCADE;
DROP TABLE IF EXISTS t_channel_sources      CASCADE;
DROP TABLE IF EXISTS t_channels             CASCADE;
DROP TABLE IF EXISTS t_data_sources         CASCADE;

-- ===== 数据源注册表 =====
CREATE TABLE t_data_sources (
    id          SERIAL      PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    type        VARCHAR(50)  NOT NULL,
    config      JSONB        NOT NULL,
    enabled     BOOLEAN      DEFAULT true,
    description TEXT,
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_data_sources        IS '数据源注册表，定义所有可接入的采集来源';
COMMENT ON COLUMN t_data_sources.type   IS 'rss | api | crawler';
COMMENT ON COLUMN t_data_sources.config IS 'JSONB配置，rss:{url} / api:{url,auth} / crawler:{since,limit}';

-- ===== 频道配置表 =====
CREATE TABLE t_channels (
    id              SERIAL      PRIMARY KEY,
    user_id         INTEGER      DEFAULT 1,
    name            VARCHAR(100) NOT NULL,
    filter_prompt   TEXT         NOT NULL,
    output_format   TEXT         DEFAULT NULL,
    min_score       FLOAT        DEFAULT 0.7,
    source_filter   TEXT[]       DEFAULT NULL,
    enabled         BOOLEAN      DEFAULT true,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_channels               IS '用户兴趣频道，filter_prompt定义过滤规则';
COMMENT ON COLUMN t_channels.user_id       IS '多用户扩展预留，当前默认1';
COMMENT ON COLUMN t_channels.filter_prompt IS 'LLM路由打分用的兴趣描述';
COMMENT ON COLUMN t_channels.min_score     IS '路由阈值0-1，建议0.65-0.80';
COMMENT ON COLUMN t_channels.source_filter IS '数据源名称过滤白名单（NULL=不限），如 {github_trending}';

-- ===== 频道-数据源关联 =====
CREATE TABLE t_channel_sources (
    id         SERIAL  PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES t_channels(id)     ON DELETE CASCADE,
    source_id  INTEGER NOT NULL REFERENCES t_data_sources(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (channel_id, source_id)
);
COMMENT ON TABLE t_channel_sources IS '频道与数据源多对多关联';

-- ===== 推送目标 =====
CREATE TABLE t_push_destinations (
    id         SERIAL       PRIMARY KEY,
    channel_id INTEGER      NOT NULL REFERENCES t_channels(id) ON DELETE CASCADE,
    type       VARCHAR(50)  NOT NULL,
    target     VARCHAR(300) NOT NULL,
    enabled    BOOLEAN      DEFAULT true,
    created_at TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_push_destinations      IS '频道推送目标，一个频道可配多个目标';
COMMENT ON COLUMN t_push_destinations.type IS 'wecom=企业微信 | telegram | webhook';

-- ===== 主任务表 =====
CREATE TABLE t_tasks (
    id           SERIAL      PRIMARY KEY,
    task_id      UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    status       VARCHAR(20)  NOT NULL DEFAULT 'running',
    trigger_type VARCHAR(50)  DEFAULT 'cron',
    started_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at  TIMESTAMPTZ  DEFAULT NULL,
    duration_ms  INTEGER      DEFAULT NULL,
    note         TEXT         DEFAULT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_tasks            IS '主任务，一个task_id贯穿采集/分析/路由/推送完整链路';
COMMENT ON COLUMN t_tasks.task_id    IS 'UUID全局唯一，所有子表通过此字段关联';
COMMENT ON COLUMN t_tasks.status     IS 'running|done|failed|partial';

-- ===== 子任务表 =====
CREATE TABLE t_sub_tasks (
    id          SERIAL      PRIMARY KEY,
    sub_task_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    task_id     UUID         NOT NULL REFERENCES t_tasks(task_id),
    phase       VARCHAR(20)  NOT NULL,
    source_id   INTEGER      REFERENCES t_data_sources(id),
    channel_id  INTEGER      REFERENCES t_channels(id),
    status      VARCHAR(20)  NOT NULL DEFAULT 'running',
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ  DEFAULT NULL,
    duration_ms INTEGER      DEFAULT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_sub_tasks       IS '子任务，每个阶段×每个数据源/频道各一条记录';
COMMENT ON COLUMN t_sub_tasks.phase IS 'collect=采集 | analyze=LLM分析 | route=路由 | push=推送';
CREATE INDEX idx_subtask_task_id ON t_sub_tasks(task_id);
CREATE INDEX idx_subtask_phase   ON t_sub_tasks(phase, status);

-- ===== 原始条目（合并原三表）=====
CREATE TABLE t_raw_items (
    id           SERIAL      PRIMARY KEY,
    task_id      UUID         REFERENCES t_tasks(task_id),
    sub_task_id  UUID         REFERENCES t_sub_tasks(sub_task_id),
    source_id    INTEGER      NOT NULL REFERENCES t_data_sources(id),
    external_id  TEXT,
    title        TEXT         NOT NULL,
    url          TEXT         UNIQUE,
    content      TEXT,
    metadata     JSONB        DEFAULT '{}',
    published_at TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_raw_items          IS '统一原始条目，合并原github_trending/hackernews/ai_news三表';
COMMENT ON COLUMN t_raw_items.metadata IS 'JSONB存来源特有字段，github:{stars_today,stars_total,language}，hn:{score,comments}';
CREATE INDEX idx_raw_items_source    ON t_raw_items(source_id, fetched_at DESC);
CREATE INDEX idx_raw_items_task      ON t_raw_items(task_id);
CREATE INDEX idx_raw_items_published ON t_raw_items(published_at DESC);

-- ===== 分析结果 =====
CREATE TABLE t_item_analysis (
    id              SERIAL  PRIMARY KEY,
    task_id         UUID     REFERENCES t_tasks(task_id),
    sub_task_id     UUID     REFERENCES t_sub_tasks(sub_task_id),
    item_id         INTEGER  NOT NULL REFERENCES t_raw_items(id) UNIQUE,
    relevance_score FLOAT,
    tags            TEXT[]   DEFAULT '{}',
    summary         TEXT,
    analyzed_at     TIMESTAMPTZ DEFAULT NOW()
);
COMMENT ON TABLE  t_item_analysis               IS 'LLM分析结果，analyzed_at IS NULL表示待分析';
COMMENT ON COLUMN t_item_analysis.tags          IS 'LLM提取的中文标签数组，3-5个';
COMMENT ON COLUMN t_item_analysis.summary       IS '50字以内中文摘要';
CREATE INDEX idx_analysis_task  ON t_item_analysis(task_id);
CREATE INDEX idx_analysis_score ON t_item_analysis(relevance_score DESC);

-- ===== 多播路由结果 =====
CREATE TABLE t_item_channel_routing (
    id          SERIAL  PRIMARY KEY,
    task_id     UUID     REFERENCES t_tasks(task_id),
    sub_task_id UUID     REFERENCES t_sub_tasks(sub_task_id),
    item_id     INTEGER  NOT NULL REFERENCES t_raw_items(id),
    channel_id  INTEGER  NOT NULL REFERENCES t_channels(id),
    score       FLOAT    NOT NULL,
    tags        TEXT[]   DEFAULT '{}',
    pushed      BOOLEAN  DEFAULT false,
    push_window VARCHAR(30) DEFAULT NULL,
    routed_at   TIMESTAMPTZ DEFAULT NOW(),
    pushed_at   TIMESTAMPTZ DEFAULT NULL,
    UNIQUE (item_id, channel_id)
);
COMMENT ON TABLE  t_item_channel_routing            IS '多播路由结果，UNIQUE(item_id,channel_id)防重';
COMMENT ON COLUMN t_item_channel_routing.pushed     IS 'false=待推送，推送完成后置true';
COMMENT ON COLUMN t_item_channel_routing.push_window IS '推送窗口标识，如 2026-03-24_1400，防同一窗口重推';
CREATE INDEX idx_routing_task    ON t_item_channel_routing(task_id);
CREATE INDEX idx_routing_channel ON t_item_channel_routing(channel_id, pushed);

-- ===== 任务日志 =====
CREATE TABLE t_task_logs (
    id           SERIAL      PRIMARY KEY,
    task_id      UUID         NOT NULL REFERENCES t_tasks(task_id),
    sub_task_id  UUID         REFERENCES t_sub_tasks(sub_task_id),
    phase        VARCHAR(20)  NOT NULL,
    level        VARCHAR(10)  NOT NULL DEFAULT 'info',
    message      TEXT         NOT NULL,
    error_type   VARCHAR(50)  DEFAULT NULL,
    error_detail TEXT         DEFAULT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_task_logs            IS '全链路日志，error_type枚举分类，error_detail存原始堆栈';
COMMENT ON COLUMN t_task_logs.error_type IS '枚举：timeout|parse_error|db_error|llm_error|push_error|auth_error';
CREATE INDEX idx_logs_task  ON t_task_logs(task_id, phase);
CREATE INDEX idx_logs_level ON t_task_logs(level, created_at DESC);

-- ===== 任务统计 =====
CREATE TABLE t_task_stats (
    id            SERIAL  PRIMARY KEY,
    task_id       UUID     NOT NULL REFERENCES t_tasks(task_id),
    sub_task_id   UUID     NOT NULL REFERENCES t_sub_tasks(sub_task_id),
    phase         VARCHAR(20) NOT NULL,
    source_id     INTEGER  REFERENCES t_data_sources(id),
    channel_id    INTEGER  REFERENCES t_channels(id),
    total         INTEGER  DEFAULT 0,
    success       INTEGER  DEFAULT 0,
    failed        INTEGER  DEFAULT 0,
    skipped       INTEGER  DEFAULT 0,
    error_summary JSONB    DEFAULT '{}',
    extra         JSONB    DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
COMMENT ON TABLE  t_task_stats               IS '阶段聚合统计，error_summary按error_type汇总';
COMMENT ON COLUMN t_task_stats.error_summary IS 'JSONB格式，key=error_type，value=count';
CREATE INDEX idx_stats_task   ON t_task_stats(task_id, phase);
CREATE INDEX idx_stats_source ON t_task_stats(source_id, created_at DESC);

-- ===== LLM 用量记录表 =====
CREATE TABLE t_llm_usage (
    id                SERIAL      PRIMARY KEY,
    task_id           UUID         REFERENCES t_tasks(task_id),
    sub_task_id       UUID         REFERENCES t_sub_tasks(sub_task_id),
    phase             VARCHAR(50),
    model             VARCHAR(100),
    prompt_tokens     INTEGER      NOT NULL DEFAULT 0,
    completion_tokens INTEGER      NOT NULL DEFAULT 0,
    total_tokens      INTEGER      NOT NULL DEFAULT 0,
    cost_usd          NUMERIC(10,6),
    extra             JSONB        DEFAULT '{}',
    created_at        TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_llm_usage          IS 'LLM token 用量记录，支持成本核算';
COMMENT ON COLUMN t_llm_usage.phase    IS 'analyze|filter|other';
COMMENT ON COLUMN t_llm_usage.cost_usd IS '粗估费用，$0.002/1K tokens';
CREATE INDEX idx_llm_usage_task    ON t_llm_usage(task_id);
CREATE INDEX idx_llm_usage_created ON t_llm_usage(created_at DESC);

-- ===== 调度配置表 =====
CREATE TABLE t_schedule_config (
    id          SERIAL       PRIMARY KEY,
    job_id      VARCHAR(100) NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    enabled     BOOLEAN      NOT NULL DEFAULT true,
    cron_expr   VARCHAR(100) NOT NULL,
    cmd         VARCHAR(500) NOT NULL,
    timeout_sec INTEGER      NOT NULL DEFAULT 300,
    description TEXT,
    extra       JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_schedule_config          IS '调度任务配置，支持热加载';
COMMENT ON COLUMN t_schedule_config.job_id   IS '任务唯一标识，如 collect/analyze/route/push_ai';
COMMENT ON COLUMN t_schedule_config.cron_expr IS '5字段 cron：分 时 日 月 周';
COMMENT ON COLUMN t_schedule_config.cmd      IS '相对项目根的 python 脚本路径+参数';
