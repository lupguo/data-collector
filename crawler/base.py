"""
crawler/base.py — BaseCrawler 基类
封装 task/sub_task 生命周期管理、日志写入、统计汇总
"""
import time
import logging
import traceback
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.db import execute_one, execute, execute_returning

logger = logging.getLogger(__name__)


class BaseCrawler:
    """
    所有采集器的基类。
    负责：
    - 在 t_data_sources 查询 source_id
    - 创建 t_sub_tasks 记录（phase='collect'）
    - 提供 log / finish / fail 方法写入 t_task_logs / t_task_stats
    """

    def __init__(self, task_id: str, source_name: str):
        self.task_id = task_id
        self.source_name = source_name
        self.sub_task_id = None
        self.source_id = None
        self._started_at = time.monotonic()

        # 查询数据源
        try:
            row = execute_one(
                "SELECT id FROM t_data_sources WHERE name = %s",
                (source_name,)
            )
            if row:
                self.source_id = row['id']
            else:
                logger.warning(f'数据源未找到: {source_name}，将以 source_id=NULL 运行')
        except Exception as e:
            logger.error(f'查询数据源失败: {e}')

        # 创建子任务
        try:
            row = execute_returning(
                """
                INSERT INTO t_sub_tasks (task_id, phase, source_id, status)
                VALUES (%s, 'collect', %s, 'running')
                RETURNING sub_task_id
                """,
                (task_id, self.source_id)
            )
            if row:
                self.sub_task_id = str(row['sub_task_id'])
        except Exception as e:
            logger.error(f'创建子任务失败: {e}')

    def log(self, level: str, message: str, error_type: str = None, error_detail: str = None):
        """写入 t_task_logs"""
        try:
            execute(
                """
                INSERT INTO t_task_logs
                    (task_id, sub_task_id, phase, level, message, error_type, error_detail)
                VALUES (%s, %s, 'collect', %s, %s, %s, %s)
                """,
                (self.task_id, self.sub_task_id, level, message, error_type, error_detail)
            )
        except Exception as e:
            logger.error(f'写日志失败: {e}')

    def finish(self, total: int, success: int, failed: int, skipped: int = 0, extra: dict = None):
        """更新子任务为 done，并写入统计"""
        if not self.sub_task_id:
            return
        elapsed_ms = int((time.monotonic() - self._started_at) * 1000)
        try:
            execute(
                """
                UPDATE t_sub_tasks
                SET status='done', finished_at=NOW(), duration_ms=%s
                WHERE sub_task_id=%s
                """,
                (elapsed_ms, self.sub_task_id)
            )
            execute(
                """
                INSERT INTO t_task_stats
                    (task_id, sub_task_id, phase, source_id, total, success, failed, skipped, extra)
                VALUES (%s, %s, 'collect', %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.task_id, self.sub_task_id, self.source_id,
                    total, success, failed, skipped,
                    __import__('json').dumps(extra or {})
                )
            )
        except Exception as e:
            logger.error(f'finish 失败: {e}')

    def fail(self, error_type: str, error_detail: str = None):
        """将子任务标记为 failed，并写错误日志"""
        if not self.sub_task_id:
            return
        elapsed_ms = int((time.monotonic() - self._started_at) * 1000)
        if error_detail is None:
            error_detail = traceback.format_exc()
        try:
            execute(
                """
                UPDATE t_sub_tasks
                SET status='failed', finished_at=NOW(), duration_ms=%s
                WHERE sub_task_id=%s
                """,
                (elapsed_ms, self.sub_task_id)
            )
            self.log('error', f'采集失败: {error_type}', error_type=error_type, error_detail=error_detail)
        except Exception as e:
            logger.error(f'fail 标记失败: {e}')
