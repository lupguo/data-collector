"""
scripts/migrate_v2.py — 数据迁移（可选执行）
将旧版三表数据迁移到新 t_raw_items 表。
旧表：github_trending / hackernews / ai_news
注：schema.sql 执行后旧表已 DROP，本脚本应在 DROP 前执行，
    或从备份恢复后执行。

用法:
  python scripts/migrate_v2.py [--dry-run]
"""
import sys
import os
import argparse
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('migrate_v2')

from db.db import get_conn, release_conn, execute_one, execute_returning


def table_exists(conn, table_name):
    cur = conn.cursor()
    cur.execute(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)",
        (table_name,)
    )
    result = cur.fetchone()[0]
    cur.close()
    return result


def get_source_id(conn, source_name):
    cur = conn.cursor()
    cur.execute("SELECT id FROM t_data_sources WHERE name=%s", (source_name,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def migrate_github(conn, task_id, dry_run=False):
    """迁移 github_trending → t_raw_items"""
    if not table_exists(conn, 'github_trending'):
        logger.info('旧表 github_trending 不存在，跳过')
        return 0

    source_id = get_source_id(conn, 'github_trending')
    if not source_id:
        logger.warning('找不到 github_trending 数据源，跳过')
        return 0

    cur = conn.cursor()
    cur.execute("SELECT repo, description, language, stars_total, stars_today, url, fetched_at FROM github_trending")
    rows = cur.fetchall()
    cur.close()

    count = 0
    for repo, desc, lang, stars_total, stars_today, url, fetched_at in rows:
        metadata = json.dumps({
            'stars_today': stars_today,
            'stars_total': stars_total,
            'language':    lang or '',
        })
        if not dry_run:
            cur2 = conn.cursor()
            cur2.execute(
                """
                INSERT INTO t_raw_items
                    (task_id, source_id, external_id, title, url, content, metadata, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (task_id, source_id, repo, repo, url, desc, metadata, fetched_at)
            )
            cur2.close()
        count += 1

    if not dry_run:
        conn.commit()
    logger.info(f'github_trending 迁移: {count} 条' + (' [dry-run]' if dry_run else ''))
    return count


def migrate_hackernews(conn, task_id, dry_run=False):
    """迁移 hackernews → t_raw_items"""
    if not table_exists(conn, 'hackernews'):
        logger.info('旧表 hackernews 不存在，跳过')
        return 0

    source_id = get_source_id(conn, 'hn_api')
    if not source_id:
        logger.warning('找不到 hn_api 数据源，跳过')
        return 0

    cur = conn.cursor()
    cur.execute("SELECT hn_id, title, url, score, comments, fetched_at FROM hackernews")
    rows = cur.fetchall()
    cur.close()

    count = 0
    for hn_id, title, url, score, comments, fetched_at in rows:
        metadata = json.dumps({'hn_id': str(hn_id), 'score': score, 'comments': comments})
        if not dry_run:
            cur2 = conn.cursor()
            cur2.execute(
                """
                INSERT INTO t_raw_items
                    (task_id, source_id, external_id, title, url, metadata, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (task_id, source_id, str(hn_id), title, url, metadata, fetched_at)
            )
            cur2.close()
        count += 1

    if not dry_run:
        conn.commit()
    logger.info(f'hackernews 迁移: {count} 条' + (' [dry-run]' if dry_run else ''))
    return count


def migrate_ai_news(conn, task_id, dry_run=False):
    """迁移 ai_news → t_raw_items（按 source 匹配 t_data_sources）"""
    if not table_exists(conn, 'ai_news'):
        logger.info('旧表 ai_news 不存在，跳过')
        return 0

    # source 字段映射（旧 source_id → 新 source name）
    SOURCE_MAP = {
        'techcrunch': 'techcrunch_rss',
        'theverge':   'theverge_rss',
        'mit':        'mit_rss',
        'wired':      'wired_rss',
        'ars':        'ars_rss',
        'arxiv_ai':   'arxiv_ai_rss',
        'arxiv_lg':   'arxiv_lg_rss',
        '36kr':       '36kr_rss',
        'infoq':      'infoq_rss',
        'bloomberg':  'bloomberg_rss',
        'wsj':        'wsj_rss',
        'skynews':    'skynews_rss',
        'nytimes':    'nytimes_rss',
        'huxiu':      'huxiu_rss',
        'ifanr':      'ifanr_rss',
        'economist':  'economist_rss',
    }

    cur = conn.cursor()
    cur.execute("SELECT source, title, url, summary, published_at, fetched_at FROM ai_news")
    rows = cur.fetchall()
    cur.close()

    count = 0
    for src, title, url, summary, published_at, fetched_at in rows:
        new_src = SOURCE_MAP.get(src)
        if not new_src:
            logger.warning(f'未知 source: {src}，跳过')
            continue
        source_id = get_source_id(conn, new_src)
        if not source_id:
            continue
        if not dry_run:
            cur2 = conn.cursor()
            cur2.execute(
                """
                INSERT INTO t_raw_items
                    (task_id, source_id, title, url, content, published_at, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (task_id, source_id, title, url, summary, published_at, fetched_at)
            )
            cur2.close()
        count += 1

    if not dry_run:
        conn.commit()
    logger.info(f'ai_news 迁移: {count} 条' + (' [dry-run]' if dry_run else ''))
    return count


def main():
    parser = argparse.ArgumentParser(description='v2 → v3 数据迁移')
    parser.add_argument('--dry-run', action='store_true', help='只统计，不写入')
    args = parser.parse_args()

    # 创建迁移主任务
    row = execute_returning(
        "INSERT INTO t_tasks (status, trigger_type, note) VALUES ('running','manual','migrate_v2') RETURNING task_id"
    )
    task_id = str(row['task_id'])
    logger.info(f'迁移任务: task_id={task_id}')

    conn = get_conn()
    try:
        n1 = migrate_github(conn, task_id, args.dry_run)
        n2 = migrate_hackernews(conn, task_id, args.dry_run)
        n3 = migrate_ai_news(conn, task_id, args.dry_run)
        total = n1 + n2 + n3
        logger.info(f'迁移完成: 共 {total} 条' + (' [dry-run]' if args.dry_run else ''))
    except Exception as e:
        logger.exception(f'迁移失败: {e}')
        sys.exit(1)
    finally:
        release_conn(conn)


if __name__ == '__main__':
    main()
