"""
filter/run_filter.py — 分析层入口
读取未分析的 t_raw_items → 调用 LLM 批量评分 → 写入 t_item_analysis

用法:
  python filter/run_filter.py [--task-id UUID] [--limit 100]
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logging_setup import setup_logging
logger = setup_logging('filter.run')

from db.db import execute_all, execute
from db.task_lifecycle import (
    get_or_create_task, finish_task,
    create_sub_task, finish_sub_task, write_task_log,
)
from filter.llm_scorer import score_batch


def main():
    parser = argparse.ArgumentParser(description='虾人情报站 v3 分析入口')
    parser.add_argument('--task-id', dest='task_id', default=None, help='绑定 task_id')
    parser.add_argument('--limit', type=int, default=200, help='最多处理条数（默认200）')
    args = parser.parse_args()

    task_id     = get_or_create_task(args.task_id, note='filter')
    sub_task_id = create_sub_task(task_id, phase='analyze')
    logger.info(f'分析任务开始: task_id={task_id}, sub_task_id={sub_task_id}')

    # 查询未分析的条目
    unanalyzed = execute_all(
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
        (args.limit,)
    )

    if not unanalyzed:
        logger.info('没有待分析的条目')
        finish_sub_task(sub_task_id, task_id, 'analyze', 0, 0, 0)
        return

    logger.info(f'待分析条目: {len(unanalyzed)} 条')

    items_input = [
        {'id': row['id'], 'title': row['title'],
         'content': row['content'], 'source_name': row['source_name']}
        for row in unanalyzed
    ]

    scored = score_batch(items_input, task_id=task_id, sub_task_id=sub_task_id)

    success = failed = 0
    for s in scored:
        try:
            execute(
                """
                INSERT INTO t_item_analysis
                    (task_id, sub_task_id, item_id, relevance_score, tags, summary, analyzed_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (item_id) DO UPDATE SET
                    relevance_score = EXCLUDED.relevance_score,
                    tags            = EXCLUDED.tags,
                    summary         = EXCLUDED.summary,
                    analyzed_at     = NOW()
                """,
                (task_id, sub_task_id, s['id'], s['relevance_score'], s['tags'], s['summary'])
            )
            success += 1
        except Exception as e:
            logger.warning(f'写入 item_id={s["id"]} 分析结果失败: {e}')
            failed += 1

    logger.info(f'分析完成: success={success}, failed={failed}')
    finish_sub_task(sub_task_id, task_id, 'analyze', len(unanalyzed), success, failed)
    write_task_log(task_id, sub_task_id, 'analyze',
                   f'分析完成: total={len(unanalyzed)}, success={success}, failed={failed}')
    finish_task(task_id)


if __name__ == '__main__':
    main()
