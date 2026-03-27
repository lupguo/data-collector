-- migrations/002_add_llm_usage_and_schedule_config.sql
-- 补充 t_llm_usage 和 t_schedule_config 表（已在生产 DB 执行，此文件仅作记录）
-- 执行时间：2026-03 v3 迭代

-- ===== LLM 用量记录表 =====
CREATE TABLE IF NOT EXISTS t_llm_usage (
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
COMMENT ON TABLE  t_llm_usage                   IS 'LLM token 用量记录，支持成本核算';
COMMENT ON COLUMN t_llm_usage.phase             IS 'analyze|filter|other';
COMMENT ON COLUMN t_llm_usage.cost_usd          IS '粗估费用，$0.002/1K tokens';
CREATE INDEX IF NOT EXISTS idx_llm_usage_task    ON t_llm_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON t_llm_usage(created_at DESC);

-- ===== 调度配置表 =====
CREATE TABLE IF NOT EXISTS t_schedule_config (
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
COMMENT ON TABLE  t_schedule_config             IS '调度任务配置，支持热加载';
COMMENT ON COLUMN t_schedule_config.job_id      IS '任务唯一标识，如 collect/analyze/route/push_ai';
COMMENT ON COLUMN t_schedule_config.cron_expr   IS '5字段 cron：分 时 日 月 周';
COMMENT ON COLUMN t_schedule_config.cmd         IS '相对项目根的 python 脚本路径+参数';
