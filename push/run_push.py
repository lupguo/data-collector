"""
push/run_push.py — 推送层入口（零 LLM 调用）
读取指定频道的待推送条目 → 格式化 → 发送 → 标记已推送

用法:
  # 基于 task_id（推荐，可靠）：
  python push/run_push.py --channel "AI技术动态" --task-id <UUID>

  # 基于时间窗口（兜底）：
  python push/run_push.py --channel "AI技术动态" --hours 4

  # 调试：
  python push/run_push.py --channel "GitHub热门项目" --dry-run
"""
import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logging_setup import setup_logging
logger = setup_logging('push.run')

from db.db import execute_all, execute, execute_one, execute_returning
from db.task_lifecycle import create_sub_task, finish_sub_task, write_task_log
from push.formatter import format_channel_compact
from push.sender import send


def _build_window_str() -> str:
    return datetime.now().strftime('%Y-%m-%d_%H%M')


def _get_or_create_push_task() -> str:
    """获取当天最新 task_id，没有则新建 push 专用任务"""
    row = execute_one(
        """
        SELECT task_id FROM t_tasks
        WHERE DATE(started_at AT TIME ZONE 'Asia/Shanghai') = CURRENT_DATE
        ORDER BY started_at DESC LIMIT 1
        """
    )
    if row:
        return str(row['task_id'])
    row = execute_returning(
        "INSERT INTO t_tasks (status, trigger_type, note) VALUES ('running','manual','push') RETURNING task_id"
    )
    return str(row['task_id'])


def _fetch_routing_rows_by_task(channel_id: int, task_id: str, limit: int, push_window: str):
    """基于 task_id 查询待推送条目（可靠，无时间边界问题）"""
    return execute_all(
        """
        SELECT
            r.id AS routing_id, r.item_id, r.score, r.tags,
            ri.title, ri.url, ri.content, ri.metadata,
            ds.name AS source_name, ia.summary, ia.tags AS analysis_tags
        FROM t_item_channel_routing r
        JOIN t_raw_items ri      ON ri.id = r.item_id
        JOIN t_data_sources ds   ON ds.id = ri.source_id
        LEFT JOIN t_item_analysis ia ON ia.item_id = r.item_id
        WHERE r.channel_id = %s
          AND r.task_id = %s
          AND r.pushed = false
          AND (r.push_window IS NULL OR r.push_window != %s)
        ORDER BY r.score DESC
        LIMIT %s
        """,
        (channel_id, task_id, push_window, limit)
    )


def _fetch_routing_rows_by_hours(channel_id: int, hours: int, limit: int, push_window: str):
    """基于时间窗口查询待推送条目（兜底方案）"""
    return execute_all(
        """
        SELECT
            r.id AS routing_id, r.item_id, r.score, r.tags,
            ri.title, ri.url, ri.content, ri.metadata,
            ds.name AS source_name, ia.summary, ia.tags AS analysis_tags
        FROM t_item_channel_routing r
        JOIN t_raw_items ri      ON ri.id = r.item_id
        JOIN t_data_sources ds   ON ds.id = ri.source_id
        LEFT JOIN t_item_analysis ia ON ia.item_id = r.item_id
        WHERE r.channel_id = %s
          AND r.pushed = false
          AND r.routed_at >= NOW() - INTERVAL %s
          AND (r.push_window IS NULL OR r.push_window != %s)
        ORDER BY r.score DESC
        LIMIT %s
        """,
        (channel_id, f'{hours} hours', push_window, limit)
    )


def main():
    parser = argparse.ArgumentParser(description='虾人情报站 v3 推送入口')
    parser.add_argument('--channel',  required=True,  help='频道名称')
    parser.add_argument('--limit',    type=int, default=15, help='最多推送条数（默认15）')
    parser.add_argument('--dry-run',  action='store_true',  help='只打印，不实际推送')
    parser.add_argument('--task-id',  dest='task_id', default=None,
                        help='按 task_id 推送（推荐，精确关联当次采集任务）')
    parser.add_argument('--hours',    type=int, default=4,
                        help='兜底：推送最近 N 小时内路由的条目（task-id 未指定时生效）')
    parser.add_argument('--window',   type=str, default=None,
                        help='推送窗口标识，如 2026-03-24_1330，不传则自动生成')
    args = parser.parse_args()

    push_window = args.window or _build_window_str()

    # 1. 查询频道
    channel = execute_one(
        "SELECT * FROM t_channels WHERE name=%s AND enabled=true", (args.channel,)
    )
    if not channel:
        logger.error(f'频道未找到或未启用: {args.channel}')
        sys.exit(1)

    channel_id = channel['id']
    logger.info(f'推送频道: {args.channel} (id={channel_id})')

    # 2. 查询推送目标
    destinations = execute_all(
        "SELECT type, target FROM t_push_destinations WHERE channel_id=%s AND enabled=true",
        (channel_id,)
    )
    if not destinations and not args.dry_run:
        logger.warning(f'频道 [{args.channel}] 没有 enabled 推送目标，将用默认配置')

    # 3. 查询待推送条目（task_id 优先，否则按小时窗口）
    if args.task_id:
        routing_rows = _fetch_routing_rows_by_task(
            channel_id, args.task_id, args.limit, push_window)
        logger.info(f'模式=task_id({args.task_id[:8]}…) 窗口={push_window} 待推送{len(routing_rows)}条')
    else:
        routing_rows = _fetch_routing_rows_by_hours(
            channel_id, args.hours, args.limit, push_window)
        logger.info(f'模式=hours({args.hours}h) 窗口={push_window} 待推送{len(routing_rows)}条')

    if not routing_rows:
        logger.info(f'[{args.channel}] 没有待推送条目')
        return

    # 4. 格式化
    items = [
        {
            'routing_id':  row['routing_id'],
            'title':       row['title'],
            'url':         row['url'],
            'score':       row['score'],
            'tags':        list(row['analysis_tags'] or row['tags'] or []),
            'summary':     row['summary'] or '',
            'source_name': row['source_name'],
        }
        for row in routing_rows
    ]
    messages = format_channel_compact(items, dict(channel))

    # 5. DRY RUN
    if args.dry_run:
        logger.info(f'[DRY RUN] 条目数={len(routing_rows)} 消息段数={len(messages)}')
        for i, msg in enumerate(messages, 1):
            print(f'\n===== 消息 {i}/{len(messages)} =====')
            print(msg)
        return

    # 6. 发送
    task_id     = _get_or_create_push_task()
    sub_task_id = create_sub_task(task_id, phase='push', channel_id=channel_id)

    push_success = False
    dest_list = destinations if destinations else [None]
    for dest in dest_list:
        for msg in messages:
            ok = send(msg, dict(dest) if dest else None)
            if ok:
                push_success = True

    if not push_success:
        logger.error('推送失败')
        execute("UPDATE t_sub_tasks SET status='failed', finished_at=NOW() WHERE sub_task_id=%s",
                (sub_task_id,))
        sys.exit(1)

    # 7. 标记已推送
    routing_ids = [row['routing_id'] for row in routing_rows]
    for rid in routing_ids:
        execute(
            "UPDATE t_item_channel_routing SET pushed=true, pushed_at=NOW(), push_window=%s WHERE id=%s",
            (push_window, rid)
        )

    # 8. 记录统计和日志
    finish_sub_task(sub_task_id, task_id, 'push',
                    len(routing_rows), len(routing_ids), 0, channel_id=channel_id)
    write_task_log(task_id, sub_task_id, 'push',
                   f'[{args.channel}] 推送完成: {len(routing_ids)} 条，窗口={push_window}')
    logger.info(f'推送完成 ✅ 共 {len(routing_ids)} 条，窗口={push_window}')


if __name__ == '__main__':
    main()
