"""
filter/run_filter.py — 分析层入口 v3（生产-消费重构版）
读取 pending 条目（无分析记录 or failed+retry<3）
→ ThreadPoolExecutor 并发调 LLM HTTP API
→ 成功写 status=done，失败写 status=failed+retry_count+1
→ retry>=3 写 status=abandoned

用法:
  python filter/run_filter.py [--task-id UUID] [--limit 50] [--concurrency 5]
"""
import sys
import os
import argparse
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logging_setup import setup_logging
logger = setup_logging('filter.run')

from db.db import execute_all, execute
from db.task_lifecycle import (
    get_or_create_task, finish_task,
    create_sub_task, finish_sub_task, write_task_log,
)
from filter.llm_scorer import score_single

MAX_RETRY = int(os.getenv('LLM_MAX_RETRY', 3))


def _fetch_pending(limit: int) -> list:
    """
    取待分析条目：
      1. 从未有分析记录的条目
      2. 已有分析记录但 status='failed' 且 retry_count < MAX_RETRY 的条目
    """
    rows = execute_all(
        """
        SELECT r.id, r.title, r.content, ds.name AS source_name
        FROM t_raw_items r
        JOIN t_data_sources ds ON ds.id = r.source_id
        WHERE NOT EXISTS (
            SELECT 1 FROM t_item_analysis a WHERE a.item_id = r.id
        )
        ORDER BY r.fetched_at DESC
        LIMIT %s
        """,
        (limit,)
    )

    # 补充 failed+可重试 的条目（若 limit 未填满）
    remaining = limit - len(rows)
    if remaining > 0:
        retry_rows = execute_all(
            """
            SELECT r.id, r.title, r.content, ds.name AS source_name
            FROM t_raw_items r
            JOIN t_data_sources ds ON ds.id = r.source_id
            JOIN t_item_analysis a ON a.item_id = r.id
            WHERE a.status = 'failed' AND a.retry_count < %s
            ORDER BY a.retry_count ASC, r.fetched_at DESC
            LIMIT %s
            """,
            (MAX_RETRY, remaining)
        )
        rows = list(rows) + list(retry_rows)

    return rows


def _write_success(task_id, sub_task_id, item_id: int, score: float, tags: list, summary: str):
    """写入分析成功结果"""
    execute(
        """
        INSERT INTO t_item_analysis
            (task_id, sub_task_id, item_id, relevance_score, tags, summary,
             analyzed_at, status, retry_count, error_msg)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), 'done', 0, NULL)
        ON CONFLICT (item_id) DO UPDATE SET
            task_id         = EXCLUDED.task_id,
            sub_task_id     = EXCLUDED.sub_task_id,
            relevance_score = EXCLUDED.relevance_score,
            tags            = EXCLUDED.tags,
            summary         = EXCLUDED.summary,
            analyzed_at     = NOW(),
            status          = 'done',
            retry_count     = 0,
            error_msg       = NULL
        """,
        (task_id, sub_task_id, item_id, score, tags, summary)
    )


def _write_failure(task_id, sub_task_id, item_id: int, error_msg: str):
    """写入分析失败（retry_count+1，满 3 次改 abandoned）"""
    execute(
        """
        INSERT INTO t_item_analysis
            (task_id, sub_task_id, item_id, relevance_score, tags, summary,
             analyzed_at, status, retry_count, error_msg)
        VALUES (%s, %s, %s, 0.5, '{}', '', NOW(),
                CASE WHEN 1 >= %s THEN 'abandoned' ELSE 'failed' END,
                1, %s)
        ON CONFLICT (item_id) DO UPDATE SET
            task_id     = EXCLUDED.task_id,
            sub_task_id = EXCLUDED.sub_task_id,
            analyzed_at = NOW(),
            retry_count = t_item_analysis.retry_count + 1,
            status      = CASE
                WHEN t_item_analysis.retry_count + 1 >= %s THEN 'abandoned'
                ELSE 'failed'
            END,
            error_msg   = EXCLUDED.error_msg
        """,
        (task_id, sub_task_id, item_id, MAX_RETRY, error_msg, MAX_RETRY)
    )


def _process_item(item: dict, task_id, sub_task_id) -> dict:
    """
    Worker 函数：评分单条条目并写回 DB。
    返回 {'item_id', 'ok', 'error'}。
    """
    item_id = item['id']
    try:
        result = score_single(item, task_id=task_id, sub_task_id=sub_task_id)
        if result['ok']:
            _write_success(
                task_id, sub_task_id, item_id,
                result['relevance_score'], result['tags'], result['summary']
            )
            logger.info(f'[item {item_id}] ✓ score={result["relevance_score"]:.2f} summary={result["summary"][:30]}')
            return {'item_id': item_id, 'ok': True, 'error': ''}
        else:
            _write_failure(task_id, sub_task_id, item_id, result['error'])
            logger.warning(f'[item {item_id}] ✗ LLM 评分失败: {result["error"][:80]}')
            return {'item_id': item_id, 'ok': False, 'error': result['error']}
    except Exception as e:
        err = f'{type(e).__name__}: {e}\n{traceback.format_exc()[-300:]}'
        try:
            _write_failure(task_id, sub_task_id, item_id, err[:500])
        except Exception as db_err:
            logger.error(f'[item {item_id}] 写失败记录也失败了: {db_err}')
        logger.error(f'[item {item_id}] ✗ 异常: {err[:100]}')
        return {'item_id': item_id, 'ok': False, 'error': str(e)}


def main():
    parser = argparse.ArgumentParser(description='虾仁情报站 v3 分析入口（生产-消费版）')
    parser.add_argument('--task-id',     dest='task_id',    default=None, help='绑定 task_id')
    parser.add_argument('--limit',       type=int, default=50,            help='每次最多处理条数（默认50）')
    parser.add_argument('--concurrency', type=int, default=None,          help='并发 Worker 数（默认读 LLM_CONCURRENCY，fallback=5）')
    args = parser.parse_args()

    concurrency = args.concurrency or int(os.getenv('LLM_CONCURRENCY', 5))

    task_id     = get_or_create_task(args.task_id, note='filter')
    sub_task_id = create_sub_task(task_id, phase='analyze')
    logger.info(f'分析任务开始: task_id={task_id}, sub_task_id={sub_task_id}, '
                f'limit={args.limit}, concurrency={concurrency}')

    pending = _fetch_pending(args.limit)
    if not pending:
        logger.info('没有待分析的条目（无 pending 或 failed 可重试）')
        finish_sub_task(sub_task_id, task_id, 'analyze', 0, 0, 0)
        finish_task(task_id)
        return

    total = len(pending)
    logger.info(f'待分析条目: {total} 条，启动 {concurrency} 并发 Worker')

    success = failed = abandoned = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_process_item, item, task_id, sub_task_id): item['id']
            for item in pending
        }
        for future in as_completed(futures):
            item_id = futures[future]
            try:
                result = future.result()
                if result['ok']:
                    success += 1
                else:
                    # 判断是否已 abandoned（DB 写入时判定）
                    row = execute_all(
                        "SELECT retry_count, status FROM t_item_analysis WHERE item_id=%s",
                        (item_id,)
                    )
                    if row and row[0]['status'] == 'abandoned':
                        abandoned += 1
                        logger.warning(f'[item {item_id}] 已达最大重试次数，标记为 abandoned')
                    else:
                        failed += 1
            except Exception as e:
                logger.error(f'[item {item_id}] future 异常: {e}')
                failed += 1

    summary_msg = (f'分析完成: total={total}, success={success}, '
                   f'failed={failed}, abandoned={abandoned}')
    logger.info(summary_msg)
    finish_sub_task(sub_task_id, task_id, 'analyze', total, success, failed + abandoned)
    write_task_log(task_id, sub_task_id, 'analyze', summary_msg)
    finish_task(task_id)


if __name__ == '__main__':
    main()
