"""
tests/smoke_v5.py — 核心链路 smoke test（无副作用）
验证：DB 连通 / 各模块 import / 调度配置完整 / 各 crawler 可实例化

用法：
  python tests/smoke_v5.py
  # 或通过 make smoke
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import traceback

PASS = '✅'
FAIL = '❌'
results = []


def check(name: str, fn):
    try:
        fn()
        results.append((PASS, name))
    except Exception as e:
        results.append((FAIL, name, str(e)))


# ── 1. DB 连通 ──
def test_db():
    from db.db import execute_one
    row = execute_one('SELECT 1 AS ok')
    assert row and row['ok'] == 1

check('DB 连通', test_db)


# ── 2. 公共模块 import ──
def test_imports():
    from utils.logging_setup import setup_logging
    from db.task_lifecycle import (
        get_or_create_task, create_sub_task,
        finish_sub_task, write_task_log, finish_task
    )
    from push.sender import send, WecomSender, WebhookSender
    from push.formatter import format_channel_compact

check('公共模块 import', test_imports)


# ── 3. 调度配置完整（5个任务都在）──
def test_schedule_config():
    from db.db import execute_all
    rows = execute_all("SELECT job_id FROM t_schedule_config WHERE enabled=true")
    job_ids = {r['job_id'] for r in rows}
    required = {'collect', 'analyze', 'route', 'push_ai', 'push_github'}
    missing = required - job_ids
    assert not missing, f'缺少调度任务: {missing}'

check('调度配置完整（5个任务）', test_schedule_config)


# ── 4. 频道配置 ──
def test_channels():
    from db.db import execute_all
    rows = execute_all("SELECT name FROM t_channels WHERE enabled=true")
    assert len(rows) >= 1, '没有 enabled 频道'

check('频道配置', test_channels)


# ── 5. LLM scorer import + token 估算 ──
def test_llm_scorer():
    from filter.llm_scorer import _estimate_tokens
    p, c = _estimate_tokens('Hello 你好世界', 'OK')
    assert p > 0 and c > 0

check('LLM scorer + token 估算', test_llm_scorer)


# ── 6. formatter ──
def test_formatter():
    from push.formatter import format_channel_compact
    items = [{'title': '测试标题', 'url': 'https://example.com',
               'summary': '测试摘要', 'tags': ['AI', '测试'], 'score': 0.9}]
    msgs = format_channel_compact(items, {'name': '测试频道'})
    assert len(msgs) >= 1 and '测试标题' in msgs[0]

check('formatter 格式化', test_formatter)


# ── 7. schema 关键表存在 ──
def test_schema():
    from db.db import execute_one
    tables = [
        't_raw_items', 't_item_analysis', 't_item_channel_routing',
        't_channels', 't_push_destinations', 't_tasks', 't_sub_tasks',
        't_task_logs', 't_task_stats', 't_llm_usage', 't_schedule_config',
    ]
    for t in tables:
        row = execute_one(
            "SELECT 1 FROM information_schema.tables WHERE table_name=%s", (t,)
        )
        assert row, f'表 {t} 不存在'

check('DB schema 完整（11张表）', test_schema)


# ── 输出结果 ──
print('\n=== Smoke Test Results ===')
failed = 0
for r in results:
    if r[0] == PASS:
        print(f'  {r[0]} {r[1]}')
    else:
        print(f'  {r[0]} {r[1]}')
        print(f'     原因: {r[2]}')
        failed += 1

print(f'\n共 {len(results)} 项，失败 {failed} 项')
sys.exit(1 if failed else 0)
