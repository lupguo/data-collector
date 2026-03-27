"""
crawler/hackernews.py — Hacker News Top Stories 采集
使用官方 Firebase JSON API，无反爬风险
适配 v3 新表：t_raw_items，绑定 task_id / sub_task_id
"""
import json
import logging
import requests
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.db import execute
from crawler.base import BaseCrawler

logger = logging.getLogger(__name__)

HN_TOP_URL  = 'https://hacker-news.firebaseio.com/v0/topstories.json'
HN_ITEM_URL = 'https://hacker-news.firebaseio.com/v0/item/{}.json'
SOURCE_NAME = 'hn_api'


def _fetch_raw(limit=30):
    """从 HN Firebase API 抓取 Top Stories，返回原始 list[dict]。
    获取异常只记录日志，不重试，不向上抛出，返回空列表。
    """
    try:
        resp = requests.get(HN_TOP_URL, timeout=10)
        resp.raise_for_status()
        ids = resp.json()[:limit]
    except Exception as e:
        logger.error(f'HN top list fetch failed: {e}')
        return []  # 不重试，直接返回空

    results = []
    for hn_id in ids:
        try:
            r = requests.get(HN_ITEM_URL.format(hn_id), timeout=8)
            item = r.json()
            if not item or item.get('type') != 'story':
                continue
            results.append({
                'hn_id':    str(item.get('id')),
                'title':    item.get('title', ''),
                'url':      item.get('url', f'https://news.ycombinator.com/item?id={hn_id}'),
                'score':    item.get('score', 0),
                'comments': item.get('descendants', 0),
            })
        except Exception as e:
            logger.warning(f'HN item {hn_id} fetch failed (skipped): {e}')
            # 单条失败跳过，不影响其余条目

    logger.info(f'HN 采集完成: {len(results)} 条')
    return results


def run(task_id: str, limit=30):
    """
    采集 HN Top Stories 并写入 t_raw_items。
    返回 (inserted, skipped) 条数。
    """
    crawler = BaseCrawler(task_id, SOURCE_NAME)
    total = success = failed = skipped = 0

    rows = _fetch_raw(limit=limit)  # 异常已在内部处理，返回空列表即可
    total = len(rows)
    if total == 0:
        crawler.log('error', 'HN 采集返回空结果，可能为网络异常，已跳过本次执行')
        crawler.finish(0, 0, 0, 0)
        return 0, 0

    for r in rows:
        hn_url = r['url']
        fallback_url = f'https://news.ycombinator.com/item?id={r["hn_id"]}'
        metadata = {
            'score':    r['score'],
            'comments': r['comments'],
            'hn_id':    r['hn_id'],
        }
        try:
            execute(
                """
                INSERT INTO t_raw_items
                    (task_id, sub_task_id, source_id, external_id, title, url,
                     metadata, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (url) DO UPDATE SET
                    metadata   = EXCLUDED.metadata,
                    fetched_at = EXCLUDED.fetched_at
                """,
                (
                    task_id,
                    crawler.sub_task_id,
                    crawler.source_id,
                    r['hn_id'],
                    r['title'],
                    hn_url or fallback_url,
                    json.dumps(metadata),
                )
            )
            success += 1
        except Exception as e:
            logger.warning(f'写入 HN {r["hn_id"]} 失败: {e}')
            failed += 1

    crawler.log('info', f'采集完成: total={total}, success={success}, failed={failed}')
    crawler.finish(total, success, failed, skipped)
    return success, failed
