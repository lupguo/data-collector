"""
crawler/rss_feeds.py — 多源 RSS 采集
数据源从 t_data_sources 表读取（type='rss', enabled=true）
适配 v3 新表：t_raw_items，绑定 task_id / sub_task_id
"""
import json
import logging
import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import calendar

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.db import execute, execute_all
from crawler.base import BaseCrawler

logger = logging.getLogger(__name__)


def _parse_time(entry):
    """解析 RSS entry 的发布时间，返回 datetime(UTC) 或 None"""
    for attr in ('published', 'updated'):
        val = getattr(entry, attr, None)
        if val:
            try:
                return parsedate_to_datetime(val).astimezone(timezone.utc)
            except Exception:
                pass
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        ts = calendar.timegm(entry.published_parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _fetch_one_source(source_id_int, source_name, url, limit=20):
    """
    采集单个 RSS 源，返回 (list[dict], bozo_failed: bool)。
    不写 DB，由调用方统一处理。
    bozo_failed=True 表示 bozo=True 且 entries 为空（完全失败）。
    """
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning(f'RSS parse warning [{source_name}]: {feed.bozo_exception}')
            return [], True
    except Exception as e:
        logger.error(f'RSS fetch failed [{source_name}]: {e}')
        return [], True

    results = []
    for entry in feed.entries[:limit]:
        title   = getattr(entry, 'title', '').strip()
        link    = getattr(entry, 'link', '').strip()
        summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '') or ''
        if len(summary) > 1000:
            summary = summary[:1000] + '...'
        if not title or not link:
            continue
        results.append({
            'source_db_id': source_id_int,
            'source_name':  source_name,
            'title':        title,
            'url':          link,
            'content':      summary,
            'published_at': _parse_time(entry),
        })

    logger.info(f'RSS [{source_name}] 采集完成: {len(results)} 条')
    return results, False


def _record_source_failure(source_id_int: int, source_name: str):
    """
    记录 RSS 源的完全失败（bozo=True 且 entries 为空）。
    在 t_data_sources.config 中更新 fail_count 和 last_fail_at。
    当 fail_count >= 5 时自动将源标记 enabled=false 并记录告警。
    """
    from datetime import datetime, timezone, timedelta
    now_str = datetime.now(timezone(timedelta(hours=8))).isoformat()
    FAIL_THRESHOLD = 5

    try:
        row = execute_all(
            "SELECT config FROM t_data_sources WHERE id=%s",
            (source_id_int,)
        )
        if not row:
            return
        config = row[0]['config'] if isinstance(row[0]['config'], dict) else json.loads(row[0]['config'])

        fail_count = int(config.get('fail_count', 0)) + 1
        config['fail_count'] = fail_count
        config['last_fail_at'] = now_str

        if fail_count >= FAIL_THRESHOLD:
            # 禁用该源
            execute(
                "UPDATE t_data_sources SET config=%s, enabled=false, updated_at=NOW() WHERE id=%s",
                (json.dumps(config), source_id_int)
            )
            logger.warning(
                f'RSS 源 [{source_name}] 连续失败 {fail_count} 次，已自动禁用（enabled=false）'
            )
        else:
            execute(
                "UPDATE t_data_sources SET config=%s, updated_at=NOW() WHERE id=%s",
                (json.dumps(config), source_id_int)
            )
            logger.info(
                f'RSS 源 [{source_name}] 记录失败 fail_count={fail_count}（阈值 {FAIL_THRESHOLD}）'
            )
    except Exception as e:
        logger.error(f'记录 [{source_name}] 失败信息异常: {e}')


def run(task_id: str, limit_per_source=20):
    """
    从 t_data_sources 读取所有 enabled RSS 源，逐一采集写入 t_raw_items。
    返回 (total_success, total_failed)
    """
    # 取所有 enabled rss 源
    try:
        sources = execute_all(
            "SELECT id, name, config FROM t_data_sources WHERE type='rss' AND enabled=true ORDER BY id"
        )
    except Exception as e:
        logger.error(f'查询数据源失败: {e}')
        return 0, 0

    if not sources:
        logger.warning('没有 enabled 的 RSS 数据源')
        return 0, 0

    total_success = 0
    total_failed  = 0

    for src in sources:
        source_id_int = src['id']
        source_name   = src['name']
        config        = src['config'] if isinstance(src['config'], dict) else json.loads(src['config'])
        url           = config.get('url', '')
        if not url:
            logger.warning(f'数据源 {source_name} 没有 url，跳过')
            continue

        crawler = BaseCrawler(task_id, source_name)
        items, bozo_failed = _fetch_one_source(source_id_int, source_name, url, limit=limit_per_source)

        # P1-3：记录完全失败的源（bozo=True 且 entries 为空）
        if bozo_failed:
            _record_source_failure(source_id_int, source_name)
        s_success = s_failed = 0

        for item in items:
            try:
                execute(
                    """
                    INSERT INTO t_raw_items
                        (task_id, sub_task_id, source_id, title, url, content,
                         published_at, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (url) DO NOTHING
                    """,
                    (
                        task_id,
                        crawler.sub_task_id,
                        source_id_int,
                        item['title'],
                        item['url'],
                        item['content'],
                        item['published_at'],
                    )
                )
                s_success += 1
            except Exception as e:
                logger.warning(f'写入 [{source_name}] 条目失败: {e}')
                s_failed += 1

        crawler.log('info', f'[{source_name}] 采集完成: {s_success} 条')
        crawler.finish(len(items), s_success, s_failed)
        total_success += s_success
        total_failed  += s_failed

    return total_success, total_failed
