"""
tests/smoke_v4.py — 虾人情报站 v4 冒烟测试
覆盖：DB 结构 / 模块 import / 各阶段 dry-run / API 接口 / 调度配置
"""
import sys
import os
import json
import time
import subprocess
import requests
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.db import execute_all, execute_one, execute, execute_returning

BASE_URL = "http://127.0.0.1:18180"
PASS_CHAR = "✅"
FAIL_CHAR = "❌"
WARN_CHAR = "⚠️"

results = []

def check(name, fn):
    try:
        fn()
        results.append((PASS_CHAR, name))
        print(f"  {PASS_CHAR} {name}")
    except Exception as e:
        results.append((FAIL_CHAR, name, str(e)))
        print(f"  {FAIL_CHAR} {name}: {e}")
        traceback.print_exc()

# ─────────────────────────────────────────────
# 1. 数据库结构
# ─────────────────────────────────────────────
print("\n【1】数据库结构验证")

def check_tables():
    rows = execute_all("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    tbls = {r['tablename'] for r in rows}
    required = {'t_raw_items','t_item_analysis','t_item_channel_routing',
                't_tasks','t_sub_tasks','t_task_logs','t_task_stats',
                't_channels','t_data_sources','t_push_destinations',
                't_schedule_config','t_llm_usage'}
    missing = required - tbls
    old = {'push_log','github_trending','news_articles','crawl_logs'}
    still_exists = old & tbls
    assert not missing, f"缺少表: {missing}"
    assert not still_exists, f"旧表未清理: {still_exists}"

check("所有必要表存在，旧表已清理", check_tables)

def check_schedule_config():
    jobs = execute_all("SELECT job_id FROM t_schedule_config")
    job_ids = {j['job_id'] for j in jobs}
    assert {'collect','analyze','route'} <= job_ids, f"缺少默认调度任务: {job_ids}"

check("t_schedule_config 含默认三个调度任务", check_schedule_config)

def check_push_window_col():
    row = execute_one("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='t_item_channel_routing' AND column_name='push_window'
    """)
    assert row, "t_item_channel_routing 缺少 push_window 字段"

check("t_item_channel_routing.push_window 字段存在", check_push_window_col)

def check_llm_usage_cols():
    cols = execute_all("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='t_llm_usage'
    """)
    col_names = {c['column_name'] for c in cols}
    required = {'task_id','sub_task_id','phase','model','prompt_tokens','completion_tokens','total_tokens'}
    missing = required - col_names
    assert not missing, f"t_llm_usage 缺列: {missing}"

check("t_llm_usage 表结构完整", check_llm_usage_cols)

def check_url_index():
    row = execute_one("""
        SELECT indexname FROM pg_indexes
        WHERE tablename='t_raw_items' AND indexname='idx_raw_items_url'
    """)
    assert row, "t_raw_items url 去重索引不存在"

check("t_raw_items url 去重索引存在", check_url_index)

# ─────────────────────────────────────────────
# 2. 模块 import
# ─────────────────────────────────────────────
print("\n【2】模块 import 验证")

def check_import_crawler():
    from crawler import github_trending, hackernews, rss_feeds
    from crawler.run import create_task, finish_task

check("crawler 模块可正常 import", check_import_crawler)

def check_import_filter():
    from filter.llm_scorer import score_batch, record_llm_usage

check("filter.llm_scorer 模块可正常 import（含 record_llm_usage）", check_import_filter)

def check_import_router():
    from router.run_router import route_channel, get_or_create_task

check("router 模块可正常 import", check_import_router)

def check_import_push():
    from push.formatter import format_channel_compact
    from push.sender import send

check("push 模块可正常 import", check_import_push)

def check_import_scheduler():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "daemon",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scheduler", "daemon.py")
    )
    mod = importlib.util.module_from_spec(spec)
    # 只检查文件可解析，不执行主循环
    import ast
    src = open(spec.origin).read()
    ast.parse(src)

check("scheduler/daemon.py 语法正确可解析", check_import_scheduler)

# ─────────────────────────────────────────────
# 3. 采集层 dry-run（GitHub Trending 单源）
# ─────────────────────────────────────────────
print("\n【3】采集层 dry-run（GitHub Trending）")

def check_github_fetch():
    from crawler.github_trending import _fetch_raw
    results_list = _fetch_raw(since='daily', limit=5)
    assert len(results_list) >= 3, f"GitHub Trending 抓取结果过少: {len(results_list)}"
    for r in results_list:
        assert 'repo' in r and 'url' in r, f"字段缺失: {r}"

check("GitHub Trending _fetch_raw 可正常抓取", check_github_fetch)

# ─────────────────────────────────────────────
# 4. 推送层 dry-run
# ─────────────────────────────────────────────
print("\n【4】推送层 dry-run")

def check_push_dryrun():
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_py = os.path.join(proj, 'venv', 'bin', 'python3')
    result = subprocess.run(
        [venv_py, 'push/run_push.py', '--channel', 'AI技术动态', '--dry-run', '--hours', '48'],
        capture_output=True, text=True, timeout=30, cwd=proj
    )
    # dry-run 可能返回"没有待推送条目"，这也是正常的
    assert result.returncode == 0, f"推送 dry-run 失败: {result.stderr[-300:]}"

check("push/run_push.py --dry-run 正常退出", check_push_dryrun)

def check_push_window_param():
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_py = os.path.join(proj, 'venv', 'bin', 'python3')
    result = subprocess.run(
        [venv_py, 'push/run_push.py', '--channel', 'AI技术动态',
         '--dry-run', '--hours', '48', '--window', 'smoke_test_window'],
        capture_output=True, text=True, timeout=30, cwd=proj
    )
    assert result.returncode == 0, f"--window 参数失败: {result.stderr[-300:]}"

check("push/run_push.py --window 参数正常", check_push_window_param)

# ─────────────────────────────────────────────
# 5. 路由层 dry-run（只验证可执行）
# ─────────────────────────────────────────────
print("\n【5】路由层验证")

def check_router_channels():
    from db.db import execute_all
    channels = execute_all("SELECT id, name, enabled FROM t_channels WHERE enabled=true")
    assert len(channels) > 0, "没有 enabled 频道"
    names = [c['name'] for c in channels]
    assert 'AI技术动态' in names, f"缺少 AI技术动态 频道: {names}"
    assert 'GitHub热门项目' in names, f"缺少 GitHub热门项目 频道: {names}"

check("t_channels 含必要频道配置", check_router_channels)

# ─────────────────────────────────────────────
# 6. 调度配置 DB 接口
# ─────────────────────────────────────────────
print("\n【6】调度配置 DB 操作")

def check_schedule_update():
    # 模拟 admin 禁用再启用 collect 任务
    execute("UPDATE t_schedule_config SET enabled=false, updated_at=NOW() WHERE job_id='collect'")
    row = execute_one("SELECT enabled FROM t_schedule_config WHERE job_id='collect'")
    assert not row['enabled'], "禁用操作未生效"
    execute("UPDATE t_schedule_config SET enabled=true, updated_at=NOW() WHERE job_id='collect'")
    row = execute_one("SELECT enabled FROM t_schedule_config WHERE job_id='collect'")
    assert row['enabled'], "重新启用操作未生效"

check("调度配置 enabled 字段可更新（热加载基础）", check_schedule_update)

def check_llm_usage_insert():
    execute("""
        INSERT INTO t_llm_usage (phase, model, prompt_tokens, completion_tokens, total_tokens)
        VALUES ('smoke_test', 'test/model', 100, 50, 150)
    """)
    row = execute_one("SELECT total_tokens FROM t_llm_usage WHERE phase='smoke_test' ORDER BY id DESC LIMIT 1")
    assert row and row['total_tokens'] == 150, "t_llm_usage 写入验证失败"
    # 清理测试数据
    execute("DELETE FROM t_llm_usage WHERE phase='smoke_test'")

check("t_llm_usage 写入/读取正常", check_llm_usage_insert)

# ─────────────────────────────────────────────
# 7. API 接口验证（需要 Flask 在跑）
# ─────────────────────────────────────────────
print("\n【7】Flask API 接口验证")

def check_api_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=5)
    assert r.status_code == 200, f"health 返回 {r.status_code}"

check("GET /api/health 返回 200", check_api_health)

def check_api_status():
    r = requests.get(f"{BASE_URL}/status", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert 'recent_tasks' in data

check("GET /status 返回正确结构", check_api_status)

def check_api_channels():
    r = requests.get(f"{BASE_URL}/channels", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) > 0

check("GET /channels 返回频道列表", check_api_channels)

def check_api_schedule_list():
    r = requests.get(f"{BASE_URL}/admin/schedule", timeout=5)
    assert r.status_code == 200, f"返回 {r.status_code}: {r.text[:200]}"
    data = r.json()
    # 接口返回数组或含 jobs 字段的对象均可
    jobs = data if isinstance(data, list) else data.get('jobs', data)
    assert len(jobs) >= 3, f"调度任务数量不足: {jobs}"
    job_ids = {j['job_id'] for j in jobs}
    assert {'collect', 'analyze', 'route'} <= job_ids, f"缺少必要 job_id: {job_ids}"

check("GET /admin/schedule 返回调度任务列表", check_api_schedule_list)

def check_api_schedule_update():
    r = requests.put(
        f"{BASE_URL}/admin/schedule/collect",
        json={"enabled": True, "cron_expr": "0 * * * *", "timeout_sec": 300},
        timeout=5
    )
    assert r.status_code == 200, f"返回 {r.status_code}: {r.text[:200]}"

check("PUT /admin/schedule/collect 更新成功", check_api_schedule_update)

def check_api_llm_usage():
    r = requests.get(f"{BASE_URL}/admin/llm/usage?days=7", timeout=5)
    assert r.status_code == 200, f"返回 {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert 'daily_summary' in data or 'summary' in data or 'details' in data, \
        f"响应结构不符预期: {list(data.keys())}"

check("GET /admin/llm/usage 返回用量统计", check_api_llm_usage)

def check_api_tasks():
    r = requests.get(f"{BASE_URL}/tasks?limit=5", timeout=5)
    assert r.status_code == 200

check("GET /tasks 正常返回", check_api_tasks)

# ─────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────
print("\n" + "="*55)
passed = sum(1 for r in results if r[0] == PASS_CHAR)
failed = sum(1 for r in results if r[0] == FAIL_CHAR)
total  = len(results)
print(f"冒烟测试完成: {passed}/{total} 通过  {'🎉' if failed == 0 else '⚠️'}")
if failed:
    print("\n失败项：")
    for r in results:
        if r[0] == FAIL_CHAR:
            print(f"  {r[1]}: {r[2] if len(r) > 2 else ''}")
sys.exit(0 if failed == 0 else 1)
