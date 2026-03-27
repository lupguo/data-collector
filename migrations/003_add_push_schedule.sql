-- migrations/003_add_push_schedule.sql
-- 补充 push 调度任务（2026-03-27 执行）

INSERT INTO t_schedule_config (job_id, name, cron_expr, cmd, timeout_sec, description, enabled)
VALUES
  ('push_ai',     'AI技术动态推送',     '50 8,17 * * *',
   'push/run_push.py --channel "AI技术动态" --hours 4',
   120, '每天8:50/17:50推送AI技术动态频道', true),
  ('push_github', 'GitHub热门项目推送', '50 8,17 * * *',
   'push/run_push.py --channel "GitHub热门项目" --hours 4',
   120, '每天8:50/17:50推送GitHub热门项目频道', true)
ON CONFLICT (job_id) DO NOTHING;
