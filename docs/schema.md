# 虾人情报站 v3 — 数据模型说明

> 版本：v3.0  
> 设计时间：2026-03-18  
> 命名规范：所有表以 `t_` 开头，字段含注释

---

## 表关系总览

```
t_data_sources ──┐
                 ├──< t_channel_sources >── t_channels ──< t_push_destinations
                 │
                 └──< t_raw_items (source_id)
                           │
                           ├──< t_item_analysis (item_id)
                           │
                           └──< t_item_channel_routing (item_id)
                                         │
                                   t_channels (channel_id)

t_tasks ──< t_sub_tasks ──< t_task_logs
       └──< t_task_stats
```

---

## 完整 DDL

```sql
-- ============================================================
-- 虾人情报站 v3 数据库 Schema
-- 设计原则：t_ 前缀，task_id 贯穿全链路，多用户预留 user_id
-- ============================================================

-- ------------------------------------------------------------
-- 配置层
-- ------------------------------------------------------------

-- 数据源注册表：所有可接入的采集来源
CREATE TABLE t_data_sources (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE, -- 唯一标识，如 'techcrunch_rss', 'hn_api'
    type        VARCHAR(50)  NOT NULL,        -- 类型：rss | api | crawler
    config      JSONB        NOT NULL,        -- 采集配置（url/auth/params 等，按 type 结构不同）
    enabled     BOOLEAN      DEFAULT true,    -- false 时采集器跳过
    description TEXT,                         -- 数据源说明
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_data_sources            IS '数据源注册表，定义所有可接入的采集来源';
COMMENT ON COLUMN t_data_sources.config     IS 'JSONB 配置，rss:{url} / api:{url,auth} / crawler:{entry}';
COMMENT ON COLUMN t_data_sources.enabled    IS 'false 时采集器跳过此源';

-- 频道配置表：用户定义的兴趣频道
CREATE TABLE t_channels (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER      DEFAULT 1,    -- 多用户预留，当前固定为 1
    name            VARCHAR(100) NOT NULL,     -- 频道名称，如 'AI技术动态'
    filter_prompt   TEXT         NOT NULL,     -- 兴趣描述 Prompt，供 LLM 路由打分
    output_format   TEXT         DEFAULT NULL, -- NULL=统一格式；有值=频道专属输出模板（预留扩展）
    min_score       FLOAT        DEFAULT 0.7,  -- 路由分数阈值，低于此值不写入路由表
    enabled         BOOLEAN      DEFAULT true,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_channels                IS '用户兴趣频道，filter_prompt 定义过滤规则，output_format 预留自定义输出';
COMMENT ON COLUMN t_channels.user_id        IS '多用户扩展预留，当前默认 1';
COMMENT ON COLUMN t_channels.filter_prompt  IS 'LLM 路由打分用的兴趣描述，描述越具体效果越好';
COMMENT ON COLUMN t_channels.output_format  IS 'NULL=统一格式推送；后期可配置频道专属摘要模板';
COMMENT ON COLUMN t_channels.min_score      IS '路由阈值 0-1，建议 0.65-0.80，越高越精准';

-- 频道-数据源关联表：声明每个频道依赖哪些数据源
CREATE TABLE t_channel_sources (
    id          SERIAL PRIMARY KEY,
    channel_id  INTEGER NOT NULL REFERENCES t_channels(id)     ON DELETE CASCADE,
    source_id   INTEGER NOT NULL REFERENCES t_data_sources(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (channel_id, source_id)
);
COMMENT ON TABLE t_channel_sources IS '频道与数据源多对多关联，路由时按此表限定 item 范围';

-- 推送目标配置表：频道推到哪里
CREATE TABLE t_push_destinations (
    id          SERIAL PRIMARY KEY,
    channel_id  INTEGER      NOT NULL REFERENCES t_channels(id) ON DELETE CASCADE,
    type        VARCHAR(50)  NOT NULL,        -- 推送类型：wecom | telegram | webhook
    target      VARCHAR(300) NOT NULL,        -- 推送目标（企业微信群ID / bot token / URL）
    enabled     BOOLEAN      DEFAULT true,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_push_destinations        IS '频道推送目标，一个频道可配多个目标';
COMMENT ON COLUMN t_push_destinations.type   IS 'wecom=企业微信 | telegram=TG机器人 | webhook=HTTP回调';
COMMENT ON COLUMN t_push_destinations.target IS '企业微信群ID / TG chat_id / Webhook URL';

-- ------------------------------------------------------------
-- 任务追踪层
-- ------------------------------------------------------------

-- 主任务表：一次完整的 采集→分析→路由→推送 流程
CREATE TABLE t_tasks (
    id              SERIAL PRIMARY KEY,
    task_id         UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE, -- 全局唯一，贯穿全链路
    status          VARCHAR(20)  NOT NULL DEFAULT 'running',  -- running | done | failed | partial
    trigger_type    VARCHAR(50)  DEFAULT 'cron',              -- 触发方式：cron | manual | api
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),      -- 任务开始（采集阶段启动时）
    finished_at     TIMESTAMPTZ  DEFAULT NULL,                -- 任务结束（推送完成时）
    duration_ms     INTEGER      DEFAULT NULL,                -- 总耗时（ms）
    note            TEXT         DEFAULT NULL,                -- 备注（手动触发时可填说明）
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_tasks             IS '主任务，一个 task_id 贯穿采集/分析/路由/推送完整链路';
COMMENT ON COLUMN t_tasks.task_id     IS 'UUID，所有子表通过此字段关联，是全链路追踪的核心';
COMMENT ON COLUMN t_tasks.status      IS 'partial=部分阶段失败但整体继续';

-- 子任务表：每个阶段独立一条，通过 task_id 关联主任务
CREATE TABLE t_sub_tasks (
    id              SERIAL PRIMARY KEY,
    sub_task_id     UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE, -- 子任务唯一ID
    task_id         UUID         NOT NULL REFERENCES t_tasks(task_id),      -- 归属主任务
    phase           VARCHAR(20)  NOT NULL,   -- 阶段：collect | analyze | route | push
    source_id       INTEGER      REFERENCES t_data_sources(id), -- 采集阶段：关联数据源
    channel_id      INTEGER      REFERENCES t_channels(id),     -- 路由/推送阶段：关联频道
    status          VARCHAR(20)  NOT NULL DEFAULT 'running',    -- running | done | failed
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ  DEFAULT NULL,
    duration_ms     INTEGER      DEFAULT NULL,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_sub_tasks           IS '子任务，每个阶段×每个数据源/频道各一条记录';
COMMENT ON COLUMN t_sub_tasks.phase     IS 'collect=采集 | analyze=LLM分析 | route=路由分发 | push=推送';
COMMENT ON COLUMN t_sub_tasks.source_id IS '仅 collect 阶段填写，标识采集的数据源';
COMMENT ON COLUMN t_sub_tasks.channel_id IS '仅 route/push 阶段填写，标识针对的频道';
CREATE INDEX idx_subtask_task_id ON t_sub_tasks(task_id);
CREATE INDEX idx_subtask_phase   ON t_sub_tasks(phase, status);

-- ------------------------------------------------------------
-- 数据存储层
-- ------------------------------------------------------------

-- 原始条目表：统一存储所有来源的采集结果（合并原三表）
CREATE TABLE t_raw_items (
    id              SERIAL PRIMARY KEY,
    task_id         UUID         REFERENCES t_tasks(task_id),
    sub_task_id     UUID         REFERENCES t_sub_tasks(sub_task_id),
    source_id       INTEGER      NOT NULL REFERENCES t_data_sources(id),
    external_id     TEXT,                      -- 原始唯一标识（hn_id / url hash 等）
    title           TEXT         NOT NULL,
    url             TEXT         UNIQUE,        -- URL 去重，重复则忽略
    content         TEXT,                       -- 正文摘要或描述
    metadata        JSONB        DEFAULT '{}',  -- 来源特有字段（stars/score/language 等）
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_raw_items          IS '统一原始条目，合并原 github_trending/hackernews/ai_news 三表';
COMMENT ON COLUMN t_raw_items.metadata IS 'JSONB 存来源特有字段，github:{stars_today,stars_total,language}，hn:{score,comments}';
COMMENT ON COLUMN t_raw_items.url      IS 'UNIQUE 约束，重复 URL 忽略，实现幂等采集';
CREATE INDEX idx_raw_items_source    ON t_raw_items(source_id, fetched_at DESC);
CREATE INDEX idx_raw_items_task      ON t_raw_items(task_id);
CREATE INDEX idx_raw_items_published ON t_raw_items(published_at DESC);

-- 条目分析表：LLM 打标/评分/摘要结果
CREATE TABLE t_item_analysis (
    id              SERIAL PRIMARY KEY,
    task_id         UUID         REFERENCES t_tasks(task_id),
    sub_task_id     UUID         REFERENCES t_sub_tasks(sub_task_id),
    item_id         INTEGER      NOT NULL REFERENCES t_raw_items(id) UNIQUE,
    relevance_score FLOAT,                     -- 综合相关度 0-1
    tags            TEXT[]       DEFAULT '{}', -- LLM 打标，如 ['AI模型','腾讯','融资']
    summary         TEXT,                      -- 50字以内中文摘要（英文原文时翻译）
    analyzed_at     TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_item_analysis                IS 'LLM 分析结果，analyzed_at IS NULL 表示待分析（可补跑）';
COMMENT ON COLUMN t_item_analysis.relevance_score IS '综合相关度，0=完全无关，1=极度相关';
COMMENT ON COLUMN t_item_analysis.tags            IS 'LLM 提取的中文标签数组，3-5个';
COMMENT ON COLUMN t_item_analysis.summary         IS '50字以内中文摘要，英文内容自动翻译';
CREATE INDEX idx_analysis_task  ON t_item_analysis(task_id);
CREATE INDEX idx_analysis_score ON t_item_analysis(relevance_score DESC);

-- 多播路由结果表：item × channel 多对多
CREATE TABLE t_item_channel_routing (
    id              SERIAL PRIMARY KEY,
    task_id         UUID         REFERENCES t_tasks(task_id),
    sub_task_id     UUID         REFERENCES t_sub_tasks(sub_task_id),
    item_id         INTEGER      NOT NULL REFERENCES t_raw_items(id),
    channel_id      INTEGER      NOT NULL REFERENCES t_channels(id),
    score           FLOAT        NOT NULL,     -- 该频道视角下的相关度评分
    tags            TEXT[]       DEFAULT '{}', -- 频道视角下的标签（可与 analysis 不同）
    pushed          BOOLEAN      DEFAULT false,-- 是否已推送
    routed_at       TIMESTAMPTZ  DEFAULT NOW(),
    pushed_at       TIMESTAMPTZ  DEFAULT NULL,
    UNIQUE (item_id, channel_id)               -- 防止同一条目重复路由到同一频道
);
COMMENT ON TABLE  t_item_channel_routing        IS '多播路由结果，UNIQUE(item_id,channel_id) 防重，pushed 标记推送状态';
COMMENT ON COLUMN t_item_channel_routing.score  IS '该频道视角下的相关度，可与 t_item_analysis.relevance_score 不同';
COMMENT ON COLUMN t_item_channel_routing.pushed IS 'false=待推送，推送完成后置 true';
CREATE INDEX idx_routing_task    ON t_item_channel_routing(task_id);
CREATE INDEX idx_routing_channel ON t_item_channel_routing(channel_id, pushed);

-- ------------------------------------------------------------
-- 可观测性层
-- ------------------------------------------------------------

-- 任务日志表：全链路详细日志，用于回溯
CREATE TABLE t_task_logs (
    id              SERIAL PRIMARY KEY,
    task_id         UUID         NOT NULL REFERENCES t_tasks(task_id),
    sub_task_id     UUID         REFERENCES t_sub_tasks(sub_task_id),
    phase           VARCHAR(20)  NOT NULL,     -- collect | analyze | route | push
    level           VARCHAR(10)  NOT NULL DEFAULT 'info', -- info | warn | error
    message         TEXT         NOT NULL,     -- 日志内容
    error_type      VARCHAR(50)  DEFAULT NULL, -- 错误分类（见枚举说明）
    error_detail    TEXT         DEFAULT NULL, -- 原始异常堆栈或 API 响应体
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_task_logs              IS '全链路日志，error_type 枚举分类便于统计，error_detail 存原始堆栈';
COMMENT ON COLUMN t_task_logs.error_type   IS '枚举：timeout|parse_error|db_error|llm_error|push_error|auth_error';
COMMENT ON COLUMN t_task_logs.error_detail IS '原始异常信息，如 Python traceback 或 HTTP 响应体';
CREATE INDEX idx_logs_task  ON t_task_logs(task_id, phase);
CREATE INDEX idx_logs_level ON t_task_logs(level, created_at DESC);

-- 任务统计表：每个子任务阶段的聚合数据
CREATE TABLE t_task_stats (
    id              SERIAL PRIMARY KEY,
    task_id         UUID         NOT NULL REFERENCES t_tasks(task_id),
    sub_task_id     UUID         NOT NULL REFERENCES t_sub_tasks(sub_task_id),
    phase           VARCHAR(20)  NOT NULL,     -- collect | analyze | route | push
    source_id       INTEGER      REFERENCES t_data_sources(id), -- 采集阶段：来自哪个数据源
    channel_id      INTEGER      REFERENCES t_channels(id),     -- 路由/推送阶段：针对哪个频道
    total           INTEGER      DEFAULT 0,    -- 处理总条数
    success         INTEGER      DEFAULT 0,    -- 成功条数
    failed          INTEGER      DEFAULT 0,    -- 失败条数
    skipped         INTEGER      DEFAULT 0,    -- 跳过条数（重复/低分/已存在）
    error_summary   JSONB        DEFAULT '{}', -- 失败原因分布，如 {"timeout":3,"parse_error":2}
    extra           JSONB        DEFAULT '{}', -- 阶段特有统计（analyze:平均分，push:消息字数等）
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
COMMENT ON TABLE  t_task_stats                IS '阶段聚合统计，error_summary 按 error_type 汇总，extra 存阶段特有指标';
COMMENT ON COLUMN t_task_stats.error_summary  IS 'JSONB 格式，key=error_type，value=count，如 {"timeout":3}';
COMMENT ON COLUMN t_task_stats.extra          IS 'JSONB 扩展，analyze阶段存平均score，push阶段存推送字数等';
CREATE INDEX idx_stats_task   ON t_task_stats(task_id, phase);
CREATE INDEX idx_stats_source ON t_task_stats(source_id, created_at DESC);
```

---

## 字段枚举说明

### `t_tasks.status` / `t_sub_tasks.status`

| 值 | 含义 |
|----|------|
| `running` | 执行中 |
| `done` | 成功完成 |
| `failed` | 全部失败 |
| `partial` | 部分失败但整体继续 |

### `t_sub_tasks.phase`

| 值 | 阶段 | 对应模块 |
|----|------|----------|
| `collect` | 采集 | `crawler/run.py` |
| `analyze` | LLM 分析打标 | `filter/run_filter.py` |
| `route` | 频道路由分发 | `router/run_router.py` |
| `push` | 推送发送 | `push/run_push.py` |

### `t_task_logs.error_type`

| 值 | 触发场景 |
|----|----------|
| `timeout` | 网络请求超时（requests/playwright） |
| `parse_error` | HTML/JSON/RSS 解析失败 |
| `db_error` | 数据库写入/查询失败 |
| `llm_error` | LLM API 调用失败或返回格式异常 |
| `push_error` | `openclaw message send` 非0退出 |
| `auth_error` | HTTP 403/401 鉴权失败 |

---

## 初始数据示例

### 数据源初始化

```sql
-- RSS 数据源
INSERT INTO t_data_sources (name, type, config, description) VALUES
('techcrunch_rss', 'rss', '{"url":"https://techcrunch.com/feed/"}', 'TechCrunch 科技新闻'),
('theverge_rss',   'rss', '{"url":"https://www.theverge.com/rss/index.xml"}', 'The Verge'),
('hn_api',         'api', '{"url":"https://hacker-news.firebaseio.com/v0/topstories.json","limit":30}', 'Hacker News Top30'),
('github_trending','crawler', '{"since":"daily","limit":25}', 'GitHub 每日 Trending');

-- 频道初始化
INSERT INTO t_channels (name, filter_prompt, min_score) VALUES
(
  'AI技术动态',
  '我关注AI技术领域：大模型能力/架构突破、AI产品发布与更新、AI企业融资收购、
   AI开发工具与框架、国内外AI公司动态。不关注：宏观经济、政治、非AI科技内容。',
  0.70
),
(
  'GitHub热门项目',
  '我关注GitHub每日trending中与AI/LLM/开发工具相关的开源项目，
   关注：star增长显著的AI框架、Agent工具、开发效率工具。',
  0.60
);
```

---

*最后更新：2026-03-18*
