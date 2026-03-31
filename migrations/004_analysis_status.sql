-- migrations/004_analysis_status.sql
-- analyze 阶段生产-消费重构：t_item_analysis 新增状态字段
-- 执行时间：2026-03-31

ALTER TABLE t_item_analysis
  ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'done',
  ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS error_msg TEXT;

CREATE INDEX IF NOT EXISTS idx_analysis_status ON t_item_analysis(status);

COMMENT ON COLUMN t_item_analysis.status IS
  'done=分析成功, failed=失败待重试(retry_count<3), abandoned=放弃重试(retry_count>=3)';
COMMENT ON COLUMN t_item_analysis.retry_count IS '累计失败次数，>=3 时置为 abandoned';
COMMENT ON COLUMN t_item_analysis.error_msg IS '最近一次失败的错误信息';
