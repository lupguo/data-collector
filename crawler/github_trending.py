"""
crawler/github_trending.py — GitHub Trending 采集
采用纯 HTTP + BeautifulSoup，随机 UA，无 Playwright 依赖
适配 v3 新表：t_raw_items，绑定 task_id / sub_task_id
"""
import re
import time
import json
import random
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.db import execute, execute_one
from crawler.base import BaseCrawler

logger = logging.getLogger(__name__)

HEADERS_POOL = [
    {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'},
    {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'},
    {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'},
]

SOURCE_NAME = 'github_trending'


def _fetch_raw(since='daily', language='', limit=25):
    """抓取 GitHub Trending 页面，返回原始 list[dict]。
    获取异常只记录日志，不重试，不向上抛出，返回空列表。
    """
    url = f'https://github.com/trending/{language}?since={since}'
    headers = random.choice(HEADERS_POOL).copy()
    headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    headers['Accept-Language'] = 'en-US,en;q=0.9'

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f'GitHub Trending fetch failed: {e}')
        return []  # 不重试，直接返回空

    soup = BeautifulSoup(resp.text, 'html.parser')
    articles = soup.select('article.Box-row')
    results = []

    for article in articles[:limit]:
        try:
            h2 = article.select_one('h2 a')
            if not h2:
                continue
            repo_path = h2.get('href', '').strip('/')
            repo_url = f'https://github.com/{repo_path}'

            p = article.select_one('p')
            description = p.get_text(strip=True) if p else ''

            lang_span = article.select_one('[itemprop="programmingLanguage"]')
            language_name = lang_span.get_text(strip=True) if lang_span else ''

            stars_link = article.select('a.Link--muted')
            stars_total = 0
            if stars_link:
                raw = stars_link[0].get_text(strip=True).replace(',', '')
                try:
                    stars_total = int(raw)
                except ValueError:
                    pass

            stars_today = 0
            today_span = article.select_one('span.d-inline-block.float-sm-right')
            if today_span:
                raw = today_span.get_text(strip=True)
                m = re.search(r'([\d,]+)\s+stars\s+today', raw)
                if m:
                    stars_today = int(m.group(1).replace(',', ''))

            results.append({
                'repo': repo_path,
                'title': repo_path,
                'description': description,
                'language': language_name,
                'stars_total': stars_total,
                'stars_today': stars_today,
                'url': repo_url,
            })
        except Exception as e:
            logger.warning(f'解析单条 trending 失败: {e}')
            continue

        time.sleep(random.uniform(0.05, 0.15))

    logger.info(f'GitHub Trending 采集完成: {len(results)} 条')
    return results


def run(task_id: str, since='daily', limit=25):
    """
    采集 GitHub Trending 并写入 t_raw_items。
    返回 (inserted, skipped) 条数。
    """
    crawler = BaseCrawler(task_id, SOURCE_NAME)
    total = success = failed = skipped = 0

    rows = _fetch_raw(since=since, limit=limit)  # 异常已在内部处理，返回空列表即可
    total = len(rows)
    if total == 0:
        crawler.log('error', 'GitHub Trending 采集返回空结果，可能为网络异常，已跳过本次执行')
        crawler.finish(0, 0, 0, 0)
        return 0, 0

    for r in rows:
        metadata = {
            'stars_today': r['stars_today'],
            'stars_total': r['stars_total'],
            'language':    r['language'],
        }
        try:
            execute(
                """
                INSERT INTO t_raw_items
                    (task_id, sub_task_id, source_id, external_id, title, url,
                     content, metadata, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (url) DO NOTHING
                """,
                (
                    task_id,
                    crawler.sub_task_id,
                    crawler.source_id,
                    r['repo'],
                    r['repo'],
                    r['url'],
                    r['description'],
                    json.dumps(metadata),
                )
            )
            success += 1
        except Exception as e:
            logger.warning(f'写入 {r["repo"]} 失败: {e}')
            failed += 1

    crawler.log('info', f'采集完成: total={total}, success={success}, failed={failed}')
    crawler.finish(total, success, failed, skipped)
    return success, failed
