"""
router/run_router.py — 多播路由层
读取所有 enabled 频道 × t_item_analysis 已分析条目，
按频道 min_score 阈值过滤，写入 t_item_channel_routing（多播）

用法:
  python router/run_router.py [--task-id UUID]
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logging_setup import setup_logging
logger = setup_logging('router.run')

from db.db import execute_all, execute
from db.task_lifecycle import (
    get_or_create_task, finish_task,
    create_sub_task, finish_sub_task, write_task_log,
)


def route_channel(task_id: str, channel: dict):
    """对单个频道执行路由"""
    channel_id    = channel['id']
    channel_name  = channel['name']
    min_score     = float(channel['min_score'] or 0.7)
    source_filter = channel.get('source_filter')

    sub_task_id = create_sub_task(task_id, phase='route', channel_id=channel_id)

    if source_filter:
        candidates = execute_all(
            """
            SELECT a.item_id, a.relevance_score, a.tags
            FROM t_item_analysis a
            JOIN t_raw_items ri ON ri.id = a.item_id
            JOIN t_data_sources ds ON ds.id = ri.source_id
            WHERE a.relevance_score >= %s
              AND a.status = 'done'
              AND ds.name = ANY(%s)
              AND NOT EXISTS (
                  SELECT 1 FROM t_item_channel_routing r
                  WHERE r.item_id = a.item_id AND r.channel_id = %s
              )
            ORDER BY a.relevance_score DESC
            """,
            (min_score, source_filter, channel_id)
        )
    else:
        candidates = execute_all(
            """
            SELECT a.item_id, a.relevance_score, a.tags
            FROM t_item_analysis a
            WHERE a.relevance_score >= %s
              AND a.status = 'done'
              AND NOT EXISTS (
                  SELECT 1 FROM t_item_channel_routing r
                  WHERE r.item_id = a.item_id AND r.channel_id = %s
              )
            ORDER BY a.relevance_score DESC
            """,
            (min_score, channel_id)
        )

    routed = failed = 0
    for c in candidates:
        try:
            execute(
                """
                INSERT INTO t_item_channel_routing
                    (task_id, sub_task_id, item_id, channel_id, score, tags, pushed)
                VALUES (%s, %s, %s, %s, %s, %s, false)
                ON CONFLICT (item_id, channel_id) DO NOTHING
                """,
                (task_id, sub_task_id, c['item_id'], channel_id,
                 c['relevance_score'], list(c['tags']) if c['tags'] else [])
            )
            routed += 1
        except Exception as e:
            logger.warning(f'路由写入失败 item_id={c["item_id"]} channel={channel_name}: {e}')
            failed += 1

    finish_sub_task(sub_task_id, task_id, 'route',
                    len(candidates), routed, failed, channel_id=channel_id)
    write_task_log(task_id, sub_task_id, 'route',
                   f'[{channel_name}] 路由完成: routed={routed}, failed={failed}')
    logger.info(f'频道 [{channel_name}] 路由完成: routed={routed}, failed={failed}')
    return routed, failed


def main():
    parser = argparse.ArgumentParser(description='虾人情报站 v3 路由入口')
    parser.add_argument('--task-id', dest='task_id', default=None, help='绑定 task_id')
    args = parser.parse_args()

    task_id = get_or_create_task(args.task_id, note='route')
    logger.info(f'路由任务开始: task_id={task_id}')

    channels = execute_all(
        "SELECT id, name, min_score, source_filter FROM t_channels WHERE enabled=true ORDER BY id"
    )
    if not channels:
        logger.warning('没有 enabled 的频道')
        return

    logger.info(f'处理频道数: {len(channels)}')
    total_routed = 0
    for ch in channels:
        routed, _ = route_channel(task_id, ch)
        total_routed += routed

    finish_task(task_id)
    logger.info(f'路由完成 ✅ 总路由条数: {total_routed}')


if __name__ == '__main__':
    main()
