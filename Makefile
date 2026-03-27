# Makefile — 虾仁情报站常用命令
.PHONY: smoke test collect analyze route push-ai push-github status restart

PYTHON = venv/bin/python3

# ── 测试 ──
smoke:
	$(PYTHON) tests/smoke_v5.py

# ── 手动触发各阶段 ──
collect:
	$(PYTHON) crawler/run.py --job all

analyze:
	$(PYTHON) filter/run_filter.py --limit 50

route:
	$(PYTHON) router/run_router.py

push-ai:
	$(PYTHON) push/run_push.py --channel "AI技术动态" --hours 4

push-github:
	$(PYTHON) push/run_push.py --channel "GitHub热门项目" --hours 4

push-ai-dry:
	$(PYTHON) push/run_push.py --channel "AI技术动态" --dry-run

push-github-dry:
	$(PYTHON) push/run_push.py --channel "GitHub热门项目" --dry-run

# ── 全链路跑一遍（开发调试用）──
pipeline: collect analyze route push-ai-dry push-github-dry

# ── 服务管理 ──
status:
	supervisorctl -c supervisor/supervisord.conf status

restart:
	supervisorctl -c supervisor/supervisord.conf restart all

restart-scheduler:
	supervisorctl -c supervisor/supervisord.conf restart datacollector-scheduler
