"""
utils/logging_setup.py — 统一日志初始化
避免各模块重复写 logging.basicConfig

用法:
    from utils.logging_setup import setup_logging
    logger = setup_logging('crawler.run')
"""
import os
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

_initialized = False


def setup_logging(name: str, log_file: str = 'run.log', level=logging.INFO) -> logging.Logger:
    """
    初始化全局 logging（只执行一次），返回指定名称的 logger。

    在 supervisord 管理的进程中（通过 stdout_logfile 捕获输出），
    若再添加 FileHandler 直写同一文件，会导致每条日志写两次。
    因此：由 supervisord 管理时只写 stdout；独立运行时同时写 stdout + 文件。

    判断依据：supervisord 启动子进程时会注入 SUPERVISOR_PROCESS_NAME 环境变量。
    """
    global _initialized
    if not _initialized:
        # supervisord 启动子进程时会自动注入此环境变量
        under_supervisor = os.getenv('SUPERVISOR_PROCESS_NAME') is not None

        handlers: list[logging.Handler] = [logging.StreamHandler()]

        if not under_supervisor:
            # 独立运行时额外写文件（避免 supervisord 场景双写）
            handlers.append(
                logging.FileHandler(os.path.join(LOG_DIR, log_file), encoding='utf-8')
            )

        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            handlers=handlers,
        )
        _initialized = True
    return logging.getLogger(name)
