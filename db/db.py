"""
db.py — 数据库连接池（单例）+ 工具函数
提供 execute_one / execute_many / execute 便捷封装
"""
import os
import logging
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

logger = logging.getLogger(__name__)

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        db_pass = os.getenv('DB_PASS')
        if not db_pass:
            raise RuntimeError(
                'DB_PASS 未配置，请在 .env 文件中设置 DB_PASS=<password>'
            )
        _pool = pool.ThreadedConnectionPool(
            minconn=1, maxconn=10,
            host=os.getenv('DB_HOST', '127.0.0.1'),
            port=int(os.getenv('DB_PORT', 5432)),
            dbname=os.getenv('DB_NAME', 'datacollector'),
            user=os.getenv('DB_USER', 'datacrawler'),
            password=db_pass,
        )
    return _pool


def get_conn():
    return get_pool().getconn()


def release_conn(conn):
    get_pool().putconn(conn)


def execute_one(sql, params=None):
    """执行 SQL，返回第一行结果（SELECT用）；无结果返回 None"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    except Exception as e:
        conn.rollback()
        logger.error(f'execute_one failed: {e}')
        raise
    finally:
        release_conn(conn)


def execute_many(sql, params_list):
    """批量执行 SQL（INSERT/UPDATE用），返回影响行数"""
    if not params_list:
        return 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            rowcount = cur.rowcount
        conn.commit()
        return rowcount
    except Exception as e:
        conn.rollback()
        logger.error(f'execute_many failed: {e}')
        raise
    finally:
        release_conn(conn)


def execute(sql, params=None):
    """执行 SQL，返回 rowcount（INSERT/UPDATE/DELETE用）"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rowcount = cur.rowcount
        conn.commit()
        return rowcount
    except Exception as e:
        conn.rollback()
        logger.error(f'execute failed: {e}')
        raise
    finally:
        release_conn(conn)


def execute_returning(sql, params=None):
    """执行 INSERT ... RETURNING，返回第一行结果（dict）"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
        return row
    except Exception as e:
        conn.rollback()
        logger.error(f'execute_returning failed: {e}')
        raise
    finally:
        release_conn(conn)


def execute_all(sql, params=None):
    """执行 SQL，返回所有行（SELECT用）"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception as e:
        conn.rollback()
        logger.error(f'execute_all failed: {e}')
        raise
    finally:
        release_conn(conn)
