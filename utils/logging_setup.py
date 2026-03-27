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
    - 同时输出到 stdout 和 logs/<log_file>
    - 重复调用只更新 logger name，不重复添加 handler
    """
    global _initialized
    if not _initialized:
        handlers = [
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(LOG_DIR, log_file), encoding='utf-8'),
        ]
        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            handlers=handlers,
        )
        _initialized = True
    return logging.getLogger(name)
