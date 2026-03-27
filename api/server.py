"""
api/server.py — 虾人情报站 v3 Flask API
提供系统状态、任务列表、频道条目查询接口

端点：
  GET /status                        — 系统状态（最近任务+各阶段统计）
  GET /tasks?limit=10                — 任务列表
  GET /tasks/<task_id>               — 任务详情（含子任务+统计）
  GET /channels                      — 频道列表
  GET /channels/<name>/items?limit=20 — 频道待推送条目
"""
import os
import sys
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from db.db import execute_all, execute_one, execute, execute_returning

app = Flask(__name__, static_folder=None)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('api')

WEB_DIR = Path(__file__).parent.parent / 'web'


# ─────────────────────────────────────────────
# 前端页面 serve
# ─────────────────────────────────────────────
@app.route('/')
@app.route('/index.html')
def index():
    return send_from_directory(WEB_DIR, 'index.html')


def _row_to_dict(row):
    """将 RealDictRow 转为普通 dict，处理 UUID/datetime 序列化"""
    if row is None:
        return None
    d = {}
    for k, v in row.items():
        if hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
        elif hasattr(v, '__str__') and type(v).__name__ == 'UUID':
            d[k] = str(v)
        else:
            d[k] = v
    return d


def _rows_to_list(rows):
    return [_row_to_dict(r) for r in rows] if rows else []


# ─────────────────────────────────────────────
# GET /status
# ─────────────────────────────────────────────
@app.route('/status')
def status():
    recent_tasks = execute_all(
        """
        SELECT task_id, status, trigger_type, started_at, finished_at, duration_ms, note
        FROM t_tasks ORDER BY started_at DESC LIMIT 5
        """
    )
    phase_stats = execute_all(
        """
        SELECT phase, SUM(total) AS total, SUM(success) AS success,
               SUM(failed) AS failed
        FROM t_task_stats
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        GROUP BY phase ORDER BY phase
        """
    )
    pending_counts = execute_all(
        """
        SELECT c.name AS channel_name, COUNT(*) AS pending
        FROM t_item_channel_routing r
        JOIN t_channels c ON c.id = r.channel_id
        WHERE r.pushed = false
        GROUP BY c.name
        """
    )
    return jsonify({
        'status': 'ok',
        'recent_tasks': _rows_to_list(recent_tasks),
        'phase_stats_24h': _rows_to_list(phase_stats),
        'pending_push': _rows_to_list(pending_counts),
    })


# ─────────────────────────────────────────────
# GET /tasks
# ─────────────────────────────────────────────
@app.route('/tasks')
def tasks():
    limit = min(int(request.args.get('limit', 10)), 100)
    rows = execute_all(
        """
        SELECT task_id, status, trigger_type, started_at, finished_at, duration_ms, note
        FROM t_tasks ORDER BY started_at DESC LIMIT %s
        """,
        (limit,)
    )
    return jsonify(_rows_to_list(rows))


# ─────────────────────────────────────────────
# GET /tasks/<task_id>
# ─────────────────────────────────────────────
@app.route('/tasks/<task_id>')
def task_detail(task_id):
    task = execute_one(
        "SELECT * FROM t_tasks WHERE task_id=%s", (task_id,)
    )
    if not task:
        return jsonify({'error': 'not found'}), 404

    sub_tasks = execute_all(
        """
        SELECT sub_task_id, phase, source_id, channel_id, status,
               started_at, finished_at, duration_ms
        FROM t_sub_tasks WHERE task_id=%s ORDER BY started_at
        """,
        (task_id,)
    )
    stats = execute_all(
        """
        SELECT phase, source_id, channel_id, total, success, failed, skipped
        FROM t_task_stats WHERE task_id=%s ORDER BY phase
        """,
        (task_id,)
    )
    logs = execute_all(
        """
        SELECT phase, level, message, error_type, created_at
        FROM t_task_logs WHERE task_id=%s ORDER BY created_at DESC LIMIT 50
        """,
        (task_id,)
    )
    return jsonify({
        'task':      _row_to_dict(task),
        'sub_tasks': _rows_to_list(sub_tasks),
        'stats':     _rows_to_list(stats),
        'logs':      _rows_to_list(logs),
    })


# ─────────────────────────────────────────────
# GET /channels
# ─────────────────────────────────────────────
@app.route('/channels')
def channels():
    rows = execute_all(
        """
        SELECT id, name, min_score, enabled, created_at,
               (SELECT COUNT(*) FROM t_item_channel_routing r
                WHERE r.channel_id = c.id AND r.pushed = false) AS pending
        FROM t_channels c ORDER BY id
        """
    )
    return jsonify(_rows_to_list(rows))


# ─────────────────────────────────────────────
# GET /channels/<name>/items
# ─────────────────────────────────────────────
@app.route('/channels/<name>/items')
def channel_items(name):
    limit = min(int(request.args.get('limit', 20)), 100)
    pushed = request.args.get('pushed', 'false').lower() == 'true'

    channel = execute_one(
        "SELECT id FROM t_channels WHERE name=%s", (name,)
    )
    if not channel:
        return jsonify({'error': 'channel not found'}), 404

    channel_id = channel['id']
    rows = execute_all(
        """
        SELECT
            r.id          AS routing_id,
            r.score,
            r.tags        AS routing_tags,
            r.pushed,
            r.routed_at,
            r.pushed_at,
            ri.title,
            ri.url,
            ri.content,
            ri.metadata,
            ds.name       AS source_name,
            ia.relevance_score,
            ia.tags       AS analysis_tags,
            ia.summary
        FROM t_item_channel_routing r
        JOIN t_raw_items ri    ON ri.id = r.item_id
        JOIN t_data_sources ds ON ds.id = ri.source_id
        LEFT JOIN t_item_analysis ia ON ia.item_id = r.item_id
        WHERE r.channel_id = %s AND r.pushed = %s
        ORDER BY r.score DESC
        LIMIT %s
        """,
        (channel_id, pushed, limit)
    )
    return jsonify(_rows_to_list(rows))


# ─────────────────────────────────────────────
# GET /raw_items?limit=80&source=<source_name>
# ─────────────────────────────────────────────
@app.route('/raw_items')
def raw_items():
    limit  = min(int(request.args.get('limit', 80)), 200)
    source = request.args.get('source', '')
    if source:
        rows = execute_all(
            """
            SELECT ri.id, ri.title, ri.url, ri.content, ri.metadata,
                   ri.published_at, ri.fetched_at,
                   ds.name AS source_name,
                   ia.relevance_score, ia.tags AS analysis_tags, ia.summary
            FROM t_raw_items ri
            JOIN t_data_sources ds ON ds.id = ri.source_id
            LEFT JOIN t_item_analysis ia ON ia.item_id = ri.id
            WHERE ds.name = %s
            ORDER BY ri.fetched_at DESC LIMIT %s
            """,
            (source, limit)
        )
    else:
        rows = execute_all(
            """
            SELECT ri.id, ri.title, ri.url, ri.content, ri.metadata,
                   ri.published_at, ri.fetched_at,
                   ds.name AS source_name,
                   ia.relevance_score, ia.tags AS analysis_tags, ia.summary
            FROM t_raw_items ri
            JOIN t_data_sources ds ON ds.id = ri.source_id
            LEFT JOIN t_item_analysis ia ON ia.item_id = ri.id
            ORDER BY ri.fetched_at DESC LIMIT %s
            """,
            (limit,)
        )
    return jsonify(_rows_to_list(rows))


# ─────────────────────────────────────────────
# GET /api/health（探活）
# ─────────────────────────────────────────────
@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════
# Admin 页面 Serve
# ═══════════════════════════════════════════════════════════
@app.route('/admin')
@app.route('/admin.html')
def admin_page():
    return send_from_directory(WEB_DIR, 'admin.html')


# ═══════════════════════════════════════════════════════════
# Admin API — 公共工具
# ═══════════════════════════════════════════════════════════
import threading
import subprocess
import uuid
import time
import json as _json
from dotenv import set_key as _dotenv_set_key

_ENV_PATH = os.path.join(os.path.dirname(__file__), '..', '.env')

# 内存任务状态字典（task_id -> dict）
_admin_tasks: dict = {}
_admin_tasks_lock = threading.Lock()


def _make_task_id():
    return str(uuid.uuid4())


def _task_start(task_id: str, name: str):
    with _admin_tasks_lock:
        _admin_tasks[task_id] = {
            'task_id': task_id,
            'name': name,
            'status': 'running',
            'started_at': time.time(),
            'finished_at': None,
            'duration_ms': None,
            'result': None,
            'error': None,
        }


def _task_done(task_id: str, result=None):
    with _admin_tasks_lock:
        t = _admin_tasks.get(task_id, {})
        t['status'] = 'done'
        t['finished_at'] = time.time()
        t['duration_ms'] = int((t['finished_at'] - t['started_at']) * 1000)
        t['result'] = result


def _task_fail(task_id: str, error: str):
    with _admin_tasks_lock:
        t = _admin_tasks.get(task_id, {})
        t['status'] = 'failed'
        t['finished_at'] = time.time()
        t['duration_ms'] = int((t['finished_at'] - t['started_at']) * 1000)
        t['error'] = error


# ═══════════════════════════════════════════════════════════
# Admin API — 信息源管理
# ═══════════════════════════════════════════════════════════
@app.route('/admin/sources', methods=['GET'])
def admin_sources_list():
    try:
        rows = execute_all(
            """
            SELECT
                ds.id, ds.name, ds.type, ds.description, ds.config, ds.enabled,
                ds.created_at, ds.updated_at,
                MAX(ri.fetched_at) AS last_fetch,
                COUNT(CASE WHEN ri.fetched_at >= NOW() - INTERVAL '24 hours' THEN 1 END) AS items_24h,
                COUNT(ri.id) AS items_total
            FROM t_data_sources ds
            LEFT JOIN t_raw_items ri ON ri.source_id = ds.id
            GROUP BY ds.id
            ORDER BY ds.id
            """
        )
        result = []
        for r in rows:
            d = _row_to_dict(r)
            result.append(d)
        return jsonify(result)
    except Exception as e:
        logger.exception('admin_sources_list error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/sources', methods=['POST'])
def admin_sources_create():
    try:
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        stype = data.get('type', 'rss').strip()
        description = data.get('description', '').strip()
        config = data.get('config', {})
        enabled = bool(data.get('enabled', True))
        if not name:
            return jsonify({'error': 'name is required'}), 400
        row = execute_returning(
            """
            INSERT INTO t_data_sources (name, type, description, config, enabled)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, type, description, config, enabled, created_at, updated_at
            """,
            (name, stype, description, _json.dumps(config), enabled)
        )
        return jsonify(_row_to_dict(row)), 201
    except Exception as e:
        logger.exception('admin_sources_create error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/sources/<int:source_id>', methods=['PUT'])
def admin_sources_update(source_id):
    try:
        data = request.get_json() or {}
        fields = []
        params = []
        if 'name' in data:
            fields.append('name=%s'); params.append(data['name'])
        if 'description' in data:
            fields.append('description=%s'); params.append(data['description'])
        if 'config' in data:
            fields.append('config=%s'); params.append(_json.dumps(data['config']))
        if 'enabled' in data:
            fields.append('enabled=%s'); params.append(bool(data['enabled']))
        if not fields:
            return jsonify({'error': 'no fields to update'}), 400
        fields.append('updated_at=NOW()')
        params.append(source_id)
        execute(f"UPDATE t_data_sources SET {', '.join(fields)} WHERE id=%s", params)
        row = execute_one(
            "SELECT id, name, type, description, config, enabled, created_at, updated_at FROM t_data_sources WHERE id=%s",
            (source_id,)
        )
        if not row:
            return jsonify({'error': 'not found'}), 404
        return jsonify(_row_to_dict(row))
    except Exception as e:
        logger.exception('admin_sources_update error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/sources/<int:source_id>', methods=['DELETE'])
def admin_sources_delete(source_id):
    try:
        execute(
            "UPDATE t_data_sources SET enabled=false, updated_at=NOW() WHERE id=%s",
            (source_id,)
        )
        return jsonify({'ok': True})
    except Exception as e:
        logger.exception('admin_sources_delete error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/sources/<int:source_id>/crawl', methods=['POST'])
def admin_sources_crawl(source_id):
    try:
        source = execute_one(
            "SELECT id, name, type FROM t_data_sources WHERE id=%s AND enabled=true",
            (source_id,)
        )
        if not source:
            return jsonify({'error': 'source not found or disabled'}), 404

        task_id = _make_task_id()
        source_name = source['name']
        source_type = source['type']

        def _run():
            try:
                import sys as _sys
                _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from db.db import execute_returning as _er, execute as _ex, execute_all as _ea, execute as _ex2
                from db.db import execute as _execute
                # 创建 DB 任务记录
                row = _er(
                    "INSERT INTO t_tasks (status, trigger_type, note) VALUES ('running', 'manual', %s) RETURNING task_id",
                    (f'crawl:source={source_name}',)
                )
                db_task_id = str(row['task_id'])
                count = 0
                try:
                    if source_type == 'rss':
                        # 单源 rss 采集
                        import json as _json2
                        from crawler.rss_feeds import _fetch_one_source
                        from crawler.base import BaseCrawler
                        src_row = _ea(
                            "SELECT id, name, config FROM t_data_sources WHERE id=%s",
                            (source_id,)
                        )
                        if src_row:
                            src = src_row[0]
                            cfg = src['config'] if isinstance(src['config'], dict) else _json2.loads(src['config'])
                            url = cfg.get('url', '')
                            crawler = BaseCrawler(db_task_id, source_name)
                            items = _fetch_one_source(source_id, source_name, url)
                            for item in items:
                                try:
                                    _execute(
                                        """
                                        INSERT INTO t_raw_items
                                            (task_id, sub_task_id, source_id, title, url, content, published_at, fetched_at)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                                        ON CONFLICT (url) DO NOTHING
                                        """,
                                        (db_task_id, crawler.sub_task_id, source_id,
                                         item['title'], item['url'], item['content'], item['published_at'])
                                    )
                                    count += 1
                                except Exception:
                                    pass
                            crawler.finish(len(items), count, len(items) - count)
                    elif source_type == 'hn_api':
                        from crawler.hackernews import run
                        s, f = run(db_task_id)
                        count = s
                    elif source_type == 'github_trending':
                        from crawler.github_trending import run
                        s, f = run(db_task_id)
                        count = s
                    _ex(
                        "UPDATE t_tasks SET status='done', finished_at=NOW() WHERE task_id=%s",
                        (db_task_id,)
                    )
                    _task_done(task_id, {'db_task_id': db_task_id, 'items_collected': count})
                except Exception as e2:
                    logger.exception(f'crawl source error: {e2}')
                    _ex(
                        "UPDATE t_tasks SET status='failed', finished_at=NOW() WHERE task_id=%s",
                        (db_task_id,)
                    )
                    _task_fail(task_id, str(e2))
            except Exception as e:
                logger.exception(f'crawl outer error: {e}')
                _task_fail(task_id, str(e))

        _task_start(task_id, f'crawl:{source_name}')
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({'task_id': task_id, 'status': 'started'})
    except Exception as e:
        logger.exception('admin_sources_crawl error')
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════
# Admin API — 渠道管理
# ═══════════════════════════════════════════════════════════
@app.route('/admin/channels', methods=['GET'])
def admin_channels_list():
    try:
        channels = execute_all(
            """
            SELECT id, name, filter_prompt, min_score, source_filter, enabled, created_at
            FROM t_channels ORDER BY id
            """
        )
        result = []
        for ch in channels:
            d = _row_to_dict(ch)
            ch_id = ch['id']
            # 待推数量
            pending_row = execute_one(
                "SELECT COUNT(*) AS cnt FROM t_item_channel_routing WHERE channel_id=%s AND pushed=false",
                (ch_id,)
            )
            d['pending'] = int(pending_row['cnt']) if pending_row else 0
            # 已推总数
            pushed_row = execute_one(
                "SELECT COUNT(*) AS cnt FROM t_item_channel_routing WHERE channel_id=%s AND pushed=true",
                (ch_id,)
            )
            d['pushed_total'] = int(pushed_row['cnt']) if pushed_row else 0
            # 推送目标
            dests = execute_all(
                "SELECT type, target, enabled FROM t_push_destinations WHERE channel_id=%s",
                (ch_id,)
            )
            d['destinations'] = _rows_to_list(dests)
            result.append(d)
        return jsonify(result)
    except Exception as e:
        logger.exception('admin_channels_list error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/channels', methods=['POST'])
def admin_channels_create():
    try:
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'error': 'name is required'}), 400
        filter_prompt = data.get('filter_prompt', '')
        min_score = float(data.get('min_score', 0.7))
        source_filter = data.get('source_filter', None)
        enabled = bool(data.get('enabled', True))
        row = execute_returning(
            """
            INSERT INTO t_channels (name, filter_prompt, min_score, source_filter, enabled)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, filter_prompt, min_score, source_filter, enabled, created_at
            """,
            (name, filter_prompt, min_score, source_filter, enabled)
        )
        return jsonify(_row_to_dict(row)), 201
    except Exception as e:
        logger.exception('admin_channels_create error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/channels/<int:channel_id>', methods=['PUT'])
def admin_channels_update(channel_id):
    try:
        data = request.get_json() or {}
        fields = []
        params = []
        if 'name' in data:
            fields.append('name=%s'); params.append(data['name'])
        if 'filter_prompt' in data:
            fields.append('filter_prompt=%s'); params.append(data['filter_prompt'])
        if 'min_score' in data:
            fields.append('min_score=%s'); params.append(float(data['min_score']))
        if 'source_filter' in data:
            fields.append('source_filter=%s'); params.append(data['source_filter'] or None)
        if 'enabled' in data:
            fields.append('enabled=%s'); params.append(bool(data['enabled']))
        if not fields:
            return jsonify({'error': 'no fields to update'}), 400
        fields.append('updated_at=NOW()')
        params.append(channel_id)
        execute(f"UPDATE t_channels SET {', '.join(fields)} WHERE id=%s", params)
        row = execute_one(
            "SELECT id, name, filter_prompt, min_score, source_filter, enabled, created_at FROM t_channels WHERE id=%s",
            (channel_id,)
        )
        if not row:
            return jsonify({'error': 'not found'}), 404
        return jsonify(_row_to_dict(row))
    except Exception as e:
        logger.exception('admin_channels_update error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/channels/<int:channel_id>', methods=['DELETE'])
def admin_channels_delete(channel_id):
    try:
        execute("DELETE FROM t_channels WHERE id=%s", (channel_id,))
        return jsonify({'ok': True})
    except Exception as e:
        logger.exception('admin_channels_delete error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/channels/<int:channel_id>/preview', methods=['GET'])
def admin_channels_preview(channel_id):
    try:
        rows = execute_all(
            """
            SELECT
                r.id AS routing_id, r.score,
                ri.title, ri.url,
                ia.summary, ia.relevance_score, ia.tags AS analysis_tags
            FROM t_item_channel_routing r
            JOIN t_raw_items ri ON ri.id = r.item_id
            LEFT JOIN t_item_analysis ia ON ia.item_id = r.item_id
            WHERE r.channel_id = %s AND r.pushed = false
            ORDER BY r.score DESC
            LIMIT 10
            """,
            (channel_id,)
        )
        return jsonify(_rows_to_list(rows))
    except Exception as e:
        logger.exception('admin_channels_preview error')
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════
# Admin API — LLM 配置
# ═══════════════════════════════════════════════════════════
_DEFAULT_SCORE_PROMPT = """分析以下{count}条新闻/技术条目，每条给出relevance_score(0-1)/tags(3个中文标签数组)/summary(30字内中文摘要)。
严格只输出JSON数组，无其他文字：
{items_json}"""


@app.route('/admin/llm/config', methods=['GET'])
def admin_llm_config_get():
    try:
        config = {
            'batch_size': int(os.getenv('LLM_BATCH_SIZE', 20)),
            'max_content_length': int(os.getenv('LLM_MAX_CONTENT', 150)),
            'llm_timeout': int(os.getenv('LLM_TIMEOUT', 120)),
            'score_prompt_template': os.getenv('LLM_PROMPT_TEMPLATE', _DEFAULT_SCORE_PROMPT),
        }
        return jsonify(config)
    except Exception as e:
        logger.exception('admin_llm_config_get error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/llm/config', methods=['PUT'])
def admin_llm_config_put():
    try:
        data = request.get_json() or {}
        env_path = os.path.abspath(_ENV_PATH)
        if 'batch_size' in data:
            val = str(int(data['batch_size']))
            _dotenv_set_key(env_path, 'LLM_BATCH_SIZE', val)
            os.environ['LLM_BATCH_SIZE'] = val
        if 'max_content_length' in data:
            val = str(int(data['max_content_length']))
            _dotenv_set_key(env_path, 'LLM_MAX_CONTENT', val)
            os.environ['LLM_MAX_CONTENT'] = val
        if 'llm_timeout' in data:
            val = str(int(data['llm_timeout']))
            _dotenv_set_key(env_path, 'LLM_TIMEOUT', val)
            os.environ['LLM_TIMEOUT'] = val
        if 'score_prompt_template' in data:
            val = str(data['score_prompt_template'])
            _dotenv_set_key(env_path, 'LLM_PROMPT_TEMPLATE', val)
            os.environ['LLM_PROMPT_TEMPLATE'] = val
        return jsonify({'ok': True})
    except Exception as e:
        logger.exception('admin_llm_config_put error')
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════
# Admin API — 调度触发
# ═══════════════════════════════════════════════════════════
def _setup_project_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


@app.route('/admin/trigger/collect', methods=['POST'])
def admin_trigger_collect():
    task_id = _make_task_id()

    def _run():
        try:
            _setup_project_path()
            from crawler.run import create_task, finish_task
            from crawler import github_trending, hackernews, rss_feeds
            db_task_id = create_task(trigger_type='manual', note='admin:collect')
            s_total = f_total = 0
            for mod in [github_trending, hackernews, rss_feeds]:
                try:
                    s, f = mod.run(db_task_id)
                    s_total += s; f_total += f
                except Exception as e2:
                    logger.warning(f'collect module error: {e2}')
            finish_task(db_task_id, 'done' if f_total == 0 else 'partial')
            _task_done(task_id, {'db_task_id': db_task_id, 'success': s_total, 'failed': f_total})
        except Exception as e:
            logger.exception('trigger/collect error')
            _task_fail(task_id, str(e))

    _task_start(task_id, 'collect:all')
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/admin/trigger/analyze', methods=['POST'])
def admin_trigger_analyze():
    task_id = _make_task_id()

    def _run():
        try:
            _setup_project_path()
            from filter.run_filter import get_or_create_task, create_sub_task, finish_sub_task
            from filter.llm_scorer import score_batch
            from db.db import execute_all as _ea, execute as _ex, execute_returning as _er, execute_one as _eo

            db_task_id = get_or_create_task()
            sub_task_id = create_sub_task(db_task_id)

            unanalyzed = _ea(
                """
                SELECT r.id, r.title, r.content, ds.name AS source_name
                FROM t_raw_items r
                JOIN t_data_sources ds ON ds.id = r.source_id
                WHERE NOT EXISTS (SELECT 1 FROM t_item_analysis a WHERE a.item_id = r.id)
                ORDER BY r.fetched_at DESC LIMIT 200
                """
            )
            if not unanalyzed:
                finish_sub_task(sub_task_id, db_task_id, 0, 0, 0)
                _task_done(task_id, {'db_task_id': db_task_id, 'analyzed': 0})
                return

            items_input = [{'id': r['id'], 'title': r['title'], 'content': r['content'], 'source_name': r['source_name']} for r in unanalyzed]
            scored = score_batch(items_input)

            success = failed = 0
            for s in scored:
                try:
                    _ex(
                        """
                        INSERT INTO t_item_analysis (task_id, sub_task_id, item_id, relevance_score, tags, summary, analyzed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (item_id) DO UPDATE SET
                            relevance_score=EXCLUDED.relevance_score, tags=EXCLUDED.tags,
                            summary=EXCLUDED.summary, analyzed_at=NOW()
                        """,
                        (db_task_id, sub_task_id, s['id'], s['relevance_score'], s['tags'], s['summary'])
                    )
                    success += 1
                except Exception:
                    failed += 1

            finish_sub_task(sub_task_id, db_task_id, len(unanalyzed), success, failed)
            _ex("UPDATE t_tasks SET status='done', finished_at=NOW() WHERE task_id=%s AND status='running'", (db_task_id,))
            _task_done(task_id, {'db_task_id': db_task_id, 'analyzed': success, 'failed': failed})
        except Exception as e:
            logger.exception('trigger/analyze error')
            _task_fail(task_id, str(e))

    _task_start(task_id, 'analyze')
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/admin/trigger/route', methods=['POST'])
def admin_trigger_route():
    task_id = _make_task_id()

    def _run():
        try:
            _setup_project_path()
            from router.run_router import get_or_create_task, route_channel
            from db.db import execute_all as _ea, execute as _ex

            db_task_id = get_or_create_task()
            channels = _ea("SELECT id, name, min_score, source_filter FROM t_channels WHERE enabled=true ORDER BY id")
            total_routed = 0
            for ch in channels:
                try:
                    routed, failed = route_channel(db_task_id, ch)
                    total_routed += routed
                except Exception as e2:
                    logger.warning(f'route channel {ch["name"]} error: {e2}')
            _ex("UPDATE t_tasks SET status='done', finished_at=NOW() WHERE task_id=%s AND status='running'", (db_task_id,))
            _task_done(task_id, {'db_task_id': db_task_id, 'routed': total_routed})
        except Exception as e:
            logger.exception('trigger/route error')
            _task_fail(task_id, str(e))

    _task_start(task_id, 'route')
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/admin/trigger/push', methods=['POST'])
def admin_trigger_push():
    task_id = _make_task_id()
    data = request.get_json() or {}
    channel_name = data.get('channel', '')
    limit = int(data.get('limit', 10))

    if not channel_name:
        return jsonify({'error': 'channel is required'}), 400

    def _run():
        try:
            _setup_project_path()
            from db.db import execute_all as _ea, execute as _ex, execute_one as _eo, execute_returning as _er
            from push.formatter import format_channel_compact
            from push.sender import send

            channel = _eo("SELECT * FROM t_channels WHERE name=%s AND enabled=true", (channel_name,))
            if not channel:
                _task_fail(task_id, f'channel not found: {channel_name}')
                return

            channel_id = channel['id']
            destinations = _ea(
                "SELECT type, target FROM t_push_destinations WHERE channel_id=%s AND enabled=true",
                (channel_id,)
            )

            routing_rows = _ea(
                """
                SELECT r.id AS routing_id, r.item_id, r.score, r.tags,
                       ri.title, ri.url, ri.content, ri.metadata, ds.name AS source_name,
                       ia.summary, ia.tags AS analysis_tags
                FROM t_item_channel_routing r
                JOIN t_raw_items ri ON ri.id = r.item_id
                JOIN t_data_sources ds ON ds.id = ri.source_id
                LEFT JOIN t_item_analysis ia ON ia.item_id = r.item_id
                WHERE r.channel_id = %s AND r.pushed = false
                ORDER BY r.score DESC LIMIT %s
                """,
                (channel_id, limit)
            )

            if not routing_rows:
                _task_done(task_id, {'pushed': 0, 'message': 'no pending items'})
                return

            items = [{
                'routing_id': r['routing_id'], 'title': r['title'], 'url': r['url'],
                'score': r['score'], 'tags': list(r['analysis_tags'] or r['tags'] or []),
                'summary': r['summary'] or '', 'source_name': r['source_name'],
            } for r in routing_rows]

            messages = format_channel_compact(items, dict(channel))

            push_ok = False
            if destinations:
                for dest in destinations:
                    for msg in messages:
                        ok = send(msg, dict(dest))
                        if ok:
                            push_ok = True
            else:
                for msg in messages:
                    ok = send(msg)
                    if ok:
                        push_ok = True

            if push_ok:
                for r in routing_rows:
                    _ex("UPDATE t_item_channel_routing SET pushed=true, pushed_at=NOW() WHERE id=%s", (r['routing_id'],))
                _task_done(task_id, {'pushed': len(routing_rows)})
            else:
                _task_fail(task_id, 'push failed')
        except Exception as e:
            logger.exception('trigger/push error')
            _task_fail(task_id, str(e))

    _task_start(task_id, f'push:{channel_name}')
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/admin/trigger/<task_id>/status', methods=['GET'])
def admin_trigger_status(task_id):
    with _admin_tasks_lock:
        task = _admin_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'task not found'}), 404
    return jsonify(dict(task))


@app.route('/admin/trigger/tasks', methods=['GET'])
def admin_trigger_tasks():
    """返回最近10条任务记录"""
    with _admin_tasks_lock:
        tasks = sorted(_admin_tasks.values(), key=lambda t: t.get('started_at', 0), reverse=True)[:10]
    return jsonify(list(tasks))


# ═══════════════════════════════════════════════════════════
# Admin API — 调度配置管理
# ═══════════════════════════════════════════════════════════

@app.route('/admin/schedule', methods=['GET'])
def admin_schedule_list():
    """列出所有调度任务配置"""
    try:
        rows = execute_all(
            """
            SELECT id, job_id, name, enabled, cron_expr, cmd,
                   timeout_sec, description, extra, created_at, updated_at
            FROM t_schedule_config
            ORDER BY id
            """
        )
        return jsonify(_rows_to_list(rows))
    except Exception as e:
        logger.exception('admin_schedule_list error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/schedule/<job_id>', methods=['PUT'])
def admin_schedule_update(job_id):
    """
    更新调度配置（enabled / cron_expr / timeout_sec）。
    同时更新 updated_at=NOW() 以触发守护进程热加载。
    """
    try:
        data = request.get_json() or {}
        fields = []
        params = []

        if 'enabled' in data:
            fields.append('enabled=%s')
            params.append(bool(data['enabled']))
        if 'cron_expr' in data:
            cron_val = str(data['cron_expr']).strip()
            if not cron_val:
                return jsonify({'error': 'cron_expr cannot be empty'}), 400
            fields.append('cron_expr=%s')
            params.append(cron_val)
        if 'timeout_sec' in data:
            fields.append('timeout_sec=%s')
            params.append(int(data['timeout_sec']))
        if 'name' in data:
            fields.append('name=%s')
            params.append(str(data['name']))
        if 'description' in data:
            fields.append('description=%s')
            params.append(str(data['description']))

        if not fields:
            return jsonify({'error': 'no valid fields to update'}), 400

        # 强制更新 updated_at，让守护进程热加载感知到
        fields.append('updated_at=NOW()')
        params.append(job_id)

        rowcount = execute(
            f"UPDATE t_schedule_config SET {', '.join(fields)} WHERE job_id=%s",
            params
        )
        if rowcount == 0:
            return jsonify({'error': f'job_id not found: {job_id}'}), 404

        row = execute_one(
            """
            SELECT id, job_id, name, enabled, cron_expr, cmd,
                   timeout_sec, description, extra, created_at, updated_at
            FROM t_schedule_config WHERE job_id=%s
            """,
            (job_id,)
        )
        return jsonify(_row_to_dict(row))
    except Exception as e:
        logger.exception('admin_schedule_update error')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/schedule/<job_id>/trigger', methods=['POST'])
def admin_schedule_trigger(job_id):
    """
    立即触发一次指定调度任务（异步执行，不阻塞请求）。
    通过 subprocess + threading 调用 scheduler/daemon.py --once <job_id>。
    执行状态写入 _admin_tasks。
    """
    try:
        # 检查 job_id 是否存在
        row = execute_one(
            "SELECT job_id, name FROM t_schedule_config WHERE job_id=%s",
            (job_id,)
        )
        if not row:
            return jsonify({'error': f'job_id not found: {job_id}'}), 404

        task_id = _make_task_id()
        job_name = row['name']

        def _run():
            _setup_project_path()
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            venv_python = os.path.join(root, 'venv', 'bin', 'python3')
            daemon_script = os.path.join(root, 'scheduler', 'daemon.py')
            try:
                proc = subprocess.run(
                    [venv_python, daemon_script, '--once', job_id],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=600,  # 最多10分钟
                )
                result_info = {
                    'exit_code': proc.returncode,
                    'stdout': proc.stdout[-2000:] if proc.stdout else '',
                    'stderr': proc.stderr[-1000:] if proc.stderr else '',
                }
                if proc.returncode == 0:
                    _task_done(task_id, result_info)
                    logger.info(f'admin trigger {job_id} 完成')
                else:
                    _task_fail(task_id, f'exit_code={proc.returncode}: {proc.stderr[-500:]}')
                    logger.error(f'admin trigger {job_id} 失败 exit_code={proc.returncode}')
            except subprocess.TimeoutExpired:
                _task_fail(task_id, 'timeout after 600s')
                logger.error(f'admin trigger {job_id} 超时')
            except Exception as e2:
                _task_fail(task_id, str(e2))
                logger.exception(f'admin trigger {job_id} 异常: {e2}')

        _task_start(task_id, f'trigger:{job_id}({job_name})')
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        logger.info(f'admin trigger {job_id} 已异步启动 task_id={task_id}')
        return jsonify({'task_id': task_id, 'status': 'started', 'job_id': job_id})
    except Exception as e:
        logger.exception('admin_schedule_trigger error')
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════
# Admin API — LLM 用量统计
# ═══════════════════════════════════════════════════════════

@app.route('/admin/llm/usage', methods=['GET'])
def admin_llm_usage():
    """
    查询 LLM 用量统计。
    ?days=7  — 查询最近 N 天（默认7）

    返回：
      daily_summary  — 按天+phase 汇总的 total_tokens, cost_usd_sum
      model_summary  — 按 model 汇总
      recent_details — 最近 50 条明细记录
    """
    try:
        days = max(1, min(int(request.args.get('days', 7)), 365))

        daily_summary = execute_all(
            """
            SELECT
                DATE(created_at AT TIME ZONE 'Asia/Shanghai') AS day,
                phase,
                model,
                COUNT(*)                         AS call_count,
                SUM(prompt_tokens)               AS prompt_tokens_sum,
                SUM(completion_tokens)           AS completion_tokens_sum,
                SUM(total_tokens)                AS total_tokens_sum,
                SUM(COALESCE(cost_usd, 0))       AS cost_usd_sum
            FROM t_llm_usage
            WHERE created_at >= NOW() - INTERVAL %s
            GROUP BY day, phase, model
            ORDER BY day DESC, phase, model
            """,
            (f'{days} days',)
        )

        model_summary = execute_all(
            """
            SELECT
                model,
                COUNT(*)                         AS call_count,
                SUM(total_tokens)                AS total_tokens_sum,
                SUM(COALESCE(cost_usd, 0))       AS cost_usd_sum
            FROM t_llm_usage
            WHERE created_at >= NOW() - INTERVAL %s
            GROUP BY model
            ORDER BY total_tokens_sum DESC
            """,
            (f'{days} days',)
        )

        recent_details = execute_all(
            """
            SELECT
                id, task_id, sub_task_id, phase, model,
                prompt_tokens, completion_tokens, total_tokens,
                cost_usd, created_at
            FROM t_llm_usage
            ORDER BY created_at DESC
            LIMIT 50
            """
        )

        return jsonify({
            'days':            days,
            'daily_summary':   _rows_to_list(daily_summary),
            'model_summary':   _rows_to_list(model_summary),
            'recent_details':  _rows_to_list(recent_details),
        })
    except Exception as e:
        logger.exception('admin_llm_usage error')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 18180))
    logger.info(f'虾人情报站 v3 API 启动: http://{host}:{port}')
    app.run(host=host, port=port, debug=False)
