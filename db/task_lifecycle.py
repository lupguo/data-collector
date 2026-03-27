"""
db/task_lifecycle.py — 子任务生命周期公共工具
统一封装 create_sub_task / finish_sub_task / fail_sub_task / write_task_log
避免各模块重复实现

用法：
    from db.task_lifecycle import create_sub_task, finish_sub_task, fail_sub_task, write_task_log
"""
import logging
from db.db import execute, execute_returning, execute_one

logger = logging.getLogger(__name__)


def get_or_create_task(task_id: str = None, trigger_type: str = 'manual', note: str = '') -> str:
    """获取已有 task 或新建一个，返回 task_id (str)"""
    if task_id:
        row = execute_one("SELECT task_id FROM t_tasks WHERE task_id=%s", (task_id,))
        if row:
            return str(row['task_id'])
        logger.warning(f'task_id {task_id} 不存在，将创建新任务')
    row = execute_returning(
        "INSERT INTO t_tasks (status, trigger_type, note) VALUES ('running',%s,%s) RETURNING task_id",
        (trigger_type, note)
    )
    return str(row['task_id'])


def finish_task(task_id: str, status: str = 'done'):
    """更新主任务状态"""
    execute(
        """
        UPDATE t_tasks
        SET status=%s, finished_at=NOW(),
            duration_ms=EXTRACT(EPOCH FROM (NOW()-started_at))::INTEGER * 1000
        WHERE task_id=%s
        """,
        (status, task_id)
    )


def create_sub_task(task_id: str, phase: str,
                    source_id: int = None, channel_id: int = None) -> str:
    """创建子任务，返回 sub_task_id (str)"""
    row = execute_returning(
        """
        INSERT INTO t_sub_tasks (task_id, phase, source_id, channel_id, status)
        VALUES (%s, %s, %s, %s, 'running')
        RETURNING sub_task_id
        """,
        (task_id, phase, source_id, channel_id)
    )
    return str(row['sub_task_id'])


def finish_sub_task(sub_task_id: str, task_id: str, phase: str,
                    total: int, success: int, failed: int, skipped: int = 0,
                    source_id: int = None, channel_id: int = None):
    """完成子任务：更新状态 + 写统计"""
    execute(
        "UPDATE t_sub_tasks SET status='done', finished_at=NOW() WHERE sub_task_id=%s",
        (sub_task_id,)
    )
    execute(
        """
        INSERT INTO t_task_stats
            (task_id, sub_task_id, phase, source_id, channel_id, total, success, failed, skipped)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (task_id, sub_task_id, phase, source_id, channel_id, total, success, failed, skipped)
    )


def fail_sub_task(sub_task_id: str):
    """将子任务标记为失败"""
    execute(
        "UPDATE t_sub_tasks SET status='failed', finished_at=NOW() WHERE sub_task_id=%s",
        (sub_task_id,)
    )


def write_task_log(task_id: str, sub_task_id: str, phase: str,
                   message: str, level: str = 'info',
                   error_type: str = None, error_detail: str = None):
    """写入任务日志"""
    try:
        execute(
            """
            INSERT INTO t_task_logs
                (task_id, sub_task_id, phase, level, message, error_type, error_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, sub_task_id, phase, level, message, error_type, error_detail)
        )
    except Exception as e:
        logger.error(f'write_task_log 失败: {e}')
