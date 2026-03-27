"""
crawler/run.py — 采集调度入口（v3）
用法:
  python crawler/run.py --job github    # 只跑 GitHub Trending
  python crawler/run.py --job hn        # 只跑 HN
  python crawler/run.py --job rss       # 只跑 RSS
  python crawler/run.py --job all       # 全部采集

task_id（UUID）由本脚本创建，贯穿全链路。
"""
import sys
import os
import argparse
# 将项目根加入 PATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from utils.logging_setup import setup_logging
logger = setup_logging('crawler.run')

from db.db import execute_returning, execute
from crawler import github_trending, hackernews, rss_feeds


def create_task(trigger_type='cron', note=None):
    """在 t_tasks 创建主任务，返回 task_id (str)"""
    row = execute_returning(
        """
        INSERT INTO t_tasks (status, trigger_type, note)
        VALUES ('running', %s, %s)
        RETURNING task_id
        """,
        (trigger_type, note)
    )
    return str(row['task_id'])


def finish_task(task_id: str, status='done'):
    execute(
        """
        UPDATE t_tasks
        SET status=%s, finished_at=NOW(),
            duration_ms=EXTRACT(EPOCH FROM (NOW()-started_at))::INTEGER * 1000
        WHERE task_id=%s
        """,
        (status, task_id)
    )


def main():
    parser = argparse.ArgumentParser(description='虾人情报站 v3 采集入口')
    parser.add_argument('--job', choices=['github', 'hn', 'rss', 'all'], default='all',
                        help='指定采集任务（默认 all）')
    parser.add_argument('--task-id', dest='task_id', default=None,
                        help='复用已有 task_id（可选）')
    args = parser.parse_args()

    # 创建主任务
    if args.task_id:
        task_id = args.task_id
        logger.info(f'复用 task_id: {task_id}')
    else:
        task_id = create_task(note=f'job={args.job}')
        logger.info(f'新建 task_id: {task_id}')

    overall_ok = True

    try:
        if args.job in ('github', 'all'):
            logger.info('=== 开始采集 GitHub Trending ===')
            s, f = github_trending.run(task_id)
            logger.info(f'GitHub Trending: success={s}, failed={f}')
            if f > s:
                overall_ok = False

        if args.job in ('hn', 'all'):
            logger.info('=== 开始采集 Hacker News ===')
            s, f = hackernews.run(task_id)
            logger.info(f'HN: success={s}, failed={f}')

        if args.job in ('rss', 'all'):
            logger.info('=== 开始采集 RSS ===')
            s, f = rss_feeds.run(task_id)
            logger.info(f'RSS: success={s}, failed={f}')

        finish_task(task_id, 'done' if overall_ok else 'partial')
        logger.info(f'采集完成 ✅ task_id={task_id}')

    except Exception as e:
        logger.exception(f'采集异常: {e}')
        finish_task(task_id, 'failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
