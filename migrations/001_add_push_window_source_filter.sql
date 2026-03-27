-- migrations/001_add_push_window_source_filter.sql
-- 补充 schema.sql 遗漏字段（已在生产 DB 执行，此文件仅作记录）
-- 执行时间：2026-03 初版上线时补丁

-- t_item_channel_routing 补充 push_window
ALTER TABLE t_item_channel_routing
  ADD COLUMN IF NOT EXISTS push_window VARCHAR(30) DEFAULT NULL;
COMMENT ON COLUMN t_item_channel_routing.push_window IS '推送窗口标识，如 2026-03-24_1400，防同一窗口重推';

-- t_channels 补充 source_filter
ALTER TABLE t_channels
  ADD COLUMN IF NOT EXISTS source_filter TEXT[] DEFAULT NULL;
COMMENT ON COLUMN t_channels.source_filter IS '数据源名称过滤白名单（NULL=不限），如 {github_trending}';
