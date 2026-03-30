"""
scheduler/daemon.py — 虾人情报站调度守护进程

功能：
  - 启动时从 t_schedule_config 读取所有 enabled=true 的任务并向 APScheduler 注册
  - 每 60 秒检查 DB 配置是否有变更（通过 updated_at 对比），有变更则热加载
  - 执行任务：用 subprocess.run 调用 venv/bin/python3 <cmd>，工作目录为项目根
  - 执行结果（exit_code, stdout 后100行, stderr 后50行）写入 t_task_logs
  - 守护进程日志写到 logs/scheduler.log

用法：
  # 正常启动守护进程（阻塞运行）
  python scheduler/daemon.py

  # 立即执行一次指定任务（手动触发，执行完退出）
  python scheduler/daemon.py --once collect
"""

import os
import sys
import time
import logging
import argparse
import subprocess
import threading
import shlex
import json
from datetime import datetime, timezone
from typing import Dict, Optional

# 项目根路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

VENV_PYTHON = os.path.join(PROJECT_ROOT, 'venv', 'bin', 'python3')
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# 日志配置：统一使用 utils.logging_setup，避免双写
# ─────────────────────────────────────────────
from utils.logging_setup import setup_logging
logger = setup_logging('scheduler.daemon', log_file='scheduler.log')

# ─────────────────────────────────────────────
# 数据库
# ─────────────────────────────────────────────
from db.db import execute_all, execute_one, execute, execute_returning

# ─────────────────────────────────────────────
# APScheduler
# ─────────────────────────────────────────────
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

# ─────────────────────────────────────────────
# 全局调度器实例
# ─────────────────────────────────────────────
_scheduler: Optional[BackgroundScheduler] = None
_config_snapshot: Dict[str, datetime] = {}  # job_id -> last known updated_at
_config_lock = threading.Lock()
_reload_lock = threading.Lock()  # 防止并发 reload


def _init_scheduler() -> BackgroundScheduler:
    """初始化并返回 APScheduler BackgroundScheduler"""
    executors = {
        'default': ThreadPoolExecutor(max_workers=4),
    }
    job_defaults = {
        'coalesce': True,        # 错过的任务只补跑一次
        'max_instances': 1,      # 同一 job 最多同时1个实例
        'misfire_grace_time': 300,
    }
    sched = BackgroundScheduler(
        executors=executors,
        job_defaults=job_defaults,
        timezone='Asia/Shanghai',
    )
    return sched


def _parse_cron_expr(cron_expr: str) -> dict:
    """
    解析 cron 表达式（支持5字段：分 时 日 月 周）
    例：'20 * * * *' -> {'minute': '20', 'hour': '*', ...}
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f'无效 cron 表达式（需要5个字段）: {cron_expr!r}')
    minute, hour, day, month, day_of_week = parts
    return {
        'minute': minute,
        'hour': hour,
        'day': day,
        'month': month,
        'day_of_week': day_of_week,
    }


def _send_alert(job_id: str, cmd: str, exit_code: int, elapsed: float, stderr_tail: str):
    """任务失败时发企业微信告警"""
    channel = os.getenv('WECOM_CHANNEL', 'openclaw-wecom-bot')
    target  = os.getenv('WECOM_TARGET', '')
    if not target:
        logger.warning('WECOM_TARGET 未配置，跳过告警推送')
        return

    from datetime import datetime, timezone, timedelta
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    msg = (
        f'⚠️ 虾仁情报站任务失败\n'
        f'任务: {job_id}  exit={exit_code}\n'
        f'命令: {cmd}\n'
        f'耗时: {elapsed:.1f}s\n'
        f'时间: {now_str}\n'
        f'错误:\n{stderr_tail[-300:] if stderr_tail else "(无 stderr)"}'
    )
    try:
        result = subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', channel, '--target', target, '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            logger.info(f'[{job_id}] 告警已发送')
        else:
            logger.error(f'[{job_id}] 告警发送失败: {result.stderr[:100]}')
    except Exception as e:
        logger.error(f'[{job_id}] 告警发送异常: {e}')


def _run_job(job_id: str, cmd: str, timeout_sec: int):
    """
    实际执行调度任务。
    - 调用 venv/bin/python3 <cmd>，工作目录为项目根
    - 将执行结果写入 t_task_logs
    """
    logger.info(f'[{job_id}] 开始执行: {cmd}  (timeout={timeout_sec}s)')
    start_ts = time.time()

    # 创建 task 记录
    try:
        task_row = execute_returning(
            """
            INSERT INTO t_tasks (status, trigger_type, note)
            VALUES ('running', 'scheduled', %s)
            RETURNING task_id
            """,
            (f'scheduler:{job_id}',)
        )
        task_id = str(task_row['task_id'])
    except Exception as e:
        logger.error(f'[{job_id}] 创建 task 记录失败: {e}')
        task_id = None

    # 拼接命令：venv/bin/python3 <cmd>（用 shlex.split 正确处理带引号的参数）
    cmd_parts = [VENV_PYTHON] + shlex.split(cmd)

    try:
        proc = subprocess.run(
            cmd_parts,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        exit_code = proc.returncode
        stdout_lines = proc.stdout.splitlines()
        stderr_lines = proc.stderr.splitlines()

        # 截取最后 100 行 stdout，50 行 stderr
        stdout_tail = '\n'.join(stdout_lines[-100:]) if stdout_lines else ''
        stderr_tail = '\n'.join(stderr_lines[-50:]) if stderr_lines else ''

        elapsed = time.time() - start_ts
        status_word = 'done' if exit_code == 0 else 'failed'
        level_word  = 'info' if exit_code == 0 else 'error'

        log_msg = (
            f'[{job_id}] exit_code={exit_code}, elapsed={elapsed:.1f}s\n'
            f'--- STDOUT (last 100 lines) ---\n{stdout_tail}\n'
            f'--- STDERR (last 50 lines) ---\n{stderr_tail}'
        )
        logger.info(f'[{job_id}] 执行完毕 exit_code={exit_code} elapsed={elapsed:.1f}s')

    except subprocess.TimeoutExpired as e:
        exit_code = -1
        elapsed   = time.time() - start_ts
        status_word = 'failed'
        level_word  = 'error'
        stderr_tail = f'超时 timeout={timeout_sec}s'
        log_msg = f'[{job_id}] 超时 timeout={timeout_sec}s'
        logger.error(f'[{job_id}] 执行超时: {e}')

    except Exception as e:
        exit_code = -2
        elapsed   = time.time() - start_ts
        status_word = 'failed'
        level_word  = 'error'
        stderr_tail = str(e)
        log_msg = f'[{job_id}] 执行异常: {e}'
        logger.error(f'[{job_id}] 执行异常: {e}', exc_info=True)

    # 写入 t_task_logs
    if task_id:
        try:
            execute(
                """
                INSERT INTO t_task_logs (task_id, phase, level, message)
                VALUES (%s, 'scheduler', %s, %s)
                """,
                (task_id, level_word, log_msg[:10000])
            )
            execute(
                """
                UPDATE t_tasks
                SET status=%s, finished_at=NOW(),
                    duration_ms=%s
                WHERE task_id=%s
                """,
                (status_word, int(elapsed * 1000), task_id)
            )
        except Exception as db_err:
            logger.error(f'[{job_id}] 写入 DB 日志失败: {db_err}')

    # 任务失败时发企业微信告警
    if status_word == 'failed':
        _send_alert(job_id, cmd, exit_code, elapsed, stderr_tail)


def _load_configs_from_db() -> Dict[str, dict]:
    """从 DB 读取所有 enabled=true 的调度配置，返回 {job_id: config_dict}"""
    try:
        rows = execute_all(
            """
            SELECT job_id, name, enabled, cron_expr, cmd, timeout_sec, description, updated_at
            FROM t_schedule_config
            WHERE enabled = true
            ORDER BY id
            """
        )
        result = {}
        for row in rows:
            result[row['job_id']] = {
                'job_id':      row['job_id'],
                'name':        row['name'],
                'cron_expr':   row['cron_expr'],
                'cmd':         row['cmd'],
                'timeout_sec': row['timeout_sec'],
                'updated_at':  row['updated_at'],
            }
        return result
    except Exception as e:
        logger.error(f'读取调度配置失败: {e}')
        return {}


def _register_job(sched: BackgroundScheduler, cfg: dict):
    """向 APScheduler 注册（或替换）一个任务"""
    job_id     = cfg['job_id']
    cron_expr  = cfg['cron_expr']
    cmd        = cfg['cmd']
    timeout_sec = cfg['timeout_sec']
    name       = cfg['name']

    try:
        trigger_kwargs = _parse_cron_expr(cron_expr)
        trigger = CronTrigger(**trigger_kwargs, timezone='Asia/Shanghai')

        # 如果已存在则先移除
        if sched.get_job(job_id):
            sched.remove_job(job_id)

        sched.add_job(
            func=_run_job,
            trigger=trigger,
            args=[job_id, cmd, timeout_sec],
            id=job_id,
            name=name,
            replace_existing=True,
        )
        logger.info(f'已注册任务: {job_id} ({name}) cron={cron_expr!r}')
    except Exception as e:
        logger.error(f'注册任务 {job_id} 失败: {e}', exc_info=True)


def _reload_jobs(sched: BackgroundScheduler):
    """
    热加载：对比 DB 配置的 updated_at 与快照，
    有变更的任务重新注册，已禁用/删除的任务移除。
    使用 _reload_lock 防止并发调用。
    """
    global _config_snapshot

    if not _reload_lock.acquire(blocking=False):
        logger.debug('_reload_jobs 已在运行，跳过本次')
        return

    try:
        new_configs = _load_configs_from_db()

        with _config_lock:
            snapshot_copy = dict(_config_snapshot)

        changed = []
        removed = []

        # 检测新增或变更
        for job_id, cfg in new_configs.items():
            old_ts = snapshot_copy.get(job_id)
            new_ts = cfg['updated_at']
            if old_ts is None or new_ts != old_ts:
                changed.append(cfg)

        # 检测已移除或禁用（在快照中存在，但新配置中不存在）
        for job_id in snapshot_copy:
            if job_id not in new_configs:
                removed.append(job_id)

        # 执行变更
        for cfg in changed:
            logger.info(f'热加载任务变更: {cfg["job_id"]}')
            _register_job(sched, cfg)

        for job_id in removed:
            if sched.get_job(job_id):
                sched.remove_job(job_id)
                logger.info(f'移除已禁用/删除任务: {job_id}')

        # 更新快照
        with _config_lock:
            _config_snapshot = {jid: cfg['updated_at'] for jid, cfg in new_configs.items()}

        if changed or removed:
            logger.info(f'热加载完成: 变更={len(changed)} 移除={len(removed)}')
    finally:
        _reload_lock.release()


def _config_watcher(sched: BackgroundScheduler, interval_sec: int = 60):
    """
    后台线程：每 interval_sec 秒检查一次 DB 配置变更。
    """
    logger.info(f'配置热加载监控启动，间隔 {interval_sec}s')
    while True:
        time.sleep(interval_sec)
        try:
            _reload_jobs(sched)
        except Exception as e:
            logger.error(f'配置热加载异常: {e}', exc_info=True)


def run_once(job_id: str):
    """
    立即执行一次指定任务（--once 模式），执行完后退出。
    """
    logger.info(f'--once 模式：立即执行 job_id={job_id!r}')

    row = execute_one(
        "SELECT job_id, cmd, timeout_sec FROM t_schedule_config WHERE job_id=%s",
        (job_id,)
    )
    if not row:
        logger.error(f'任务不存在: {job_id}')
        sys.exit(1)

    _run_job(row['job_id'], row['cmd'], row['timeout_sec'])
    logger.info(f'--once 执行完毕，退出')


def main():
    parser = argparse.ArgumentParser(description='虾人情报站调度守护进程')
    parser.add_argument(
        '--once',
        metavar='JOB_ID',
        help='立即执行一次指定任务并退出（如：--once collect）',
    )
    parser.add_argument(
        '--config-interval',
        type=int,
        default=60,
        help='配置热加载检查间隔（秒，默认60）',
    )
    args = parser.parse_args()

    # --once 模式：直接执行，不启动调度器
    if args.once:
        run_once(args.once)
        return

    # ── 正常守护进程模式 ──
    logger.info('=' * 60)
    logger.info('虾人情报站 调度守护进程启动')
    logger.info(f'项目根目录: {PROJECT_ROOT}')
    logger.info(f'Python 解释器: {VENV_PYTHON}')
    logger.info('=' * 60)

    # 初始化调度器
    global _scheduler
    _scheduler = _init_scheduler()
    _scheduler.start()
    logger.info('APScheduler 已启动')

    # 初次加载全量配置
    configs = _load_configs_from_db()
    if not configs:
        logger.warning('没有从 DB 读取到任何 enabled=true 的调度任务，调度器将空转')
    for cfg in configs.values():
        _register_job(_scheduler, cfg)

    # 先更新快照，再启动 watcher，避免启动后首次 reload 误判全部为"变更"
    with _config_lock:
        _config_snapshot.update({jid: cfg['updated_at'] for jid, cfg in configs.items()})

    logger.info(f'已注册 {len(configs)} 个调度任务: {list(configs.keys())}')

    # 启动配置热加载监控线程
    watcher_thread = threading.Thread(
        target=_config_watcher,
        args=(_scheduler, args.config_interval),
        daemon=True,
        name='config-watcher',
    )
    watcher_thread.start()

    # 主线程阻塞，等待信号
    import signal

    def _shutdown(signum, frame):
        logger.info(f'收到信号 {signum}，正在关闭调度器...')
        _scheduler.shutdown(wait=True)
        logger.info('调度器已关闭，退出')
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info('调度守护进程运行中，按 Ctrl+C 或发送 SIGTERM 停止')
    try:
        while True:
            time.sleep(10)
            # 心跳日志（每5分钟一次）
            if int(time.time()) % 300 < 10:
                jobs = _scheduler.get_jobs()
                for job in jobs:
                    next_run = job.next_run_time
                    logger.debug(
                        f'任务 {job.id}: 下次运行 {next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "N/A"}'
                    )
    except (KeyboardInterrupt, SystemExit):
        logger.info('调度守护进程退出')
        if _scheduler.running:
            _scheduler.shutdown(wait=False)


if __name__ == '__main__':
    main()
