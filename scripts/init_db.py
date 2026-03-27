"""
scripts/init_db.py — 数据库初始化脚本
执行 schema.sql 建表，然后插入初始数据源和频道。
会先 DROP 旧表（github_trending/hackernews/ai_news）和新 t_ 表。

用法:
  python scripts/init_db.py
  python scripts/init_db.py --no-data   # 只建表，不插初始数据
"""
import sys
import os
import argparse
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('init_db')

from db.db import get_conn, release_conn

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'schema.sql')

# ─────────────────────────────────────────────
# 初始数据源
# ─────────────────────────────────────────────
SOURCES = [
    # (name, type, config_dict, description)
    ('techcrunch_rss', 'rss', {'url': 'https://techcrunch.com/feed/'},                                   'TechCrunch 科技新闻'),
    ('theverge_rss',   'rss', {'url': 'https://www.theverge.com/rss/index.xml'},                         'The Verge'),
    ('mit_rss',        'rss', {'url': 'https://www.technologyreview.com/feed/'},                         'MIT Tech Review'),
    ('wired_rss',      'rss', {'url': 'https://www.wired.com/feed/rss'},                                 'Wired'),
    ('ars_rss',        'rss', {'url': 'https://feeds.arstechnica.com/arstechnica/index'},                'Ars Technica'),
    ('arxiv_ai_rss',   'rss', {'url': 'https://export.arxiv.org/rss/cs.AI'},                            'ArXiv cs.AI'),
    ('arxiv_lg_rss',   'rss', {'url': 'https://export.arxiv.org/rss/cs.LG'},                            'ArXiv cs.LG'),
    ('36kr_rss',       'rss', {'url': 'https://36kr.com/feed'},                                         '36Kr'),
    ('infoq_rss',      'rss', {'url': 'https://feed.infoq.com/'},                                       'InfoQ'),
    ('bloomberg_rss',  'rss', {'url': 'https://feeds.bloomberg.com/markets/news.rss'},                  'Bloomberg Markets'),
    ('wsj_rss',        'rss', {'url': 'https://feeds.a.dj.com/rss/RSSMarketsMain.xml'},                 'WSJ Markets'),
    ('skynews_rss',    'rss', {'url': 'https://feeds.skynews.com/feeds/rss/world.xml'},                 'Sky News World'),
    ('nytimes_rss',    'rss', {'url': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml'},        'NYTimes World'),
    ('huxiu_rss',      'rss', {'url': 'https://www.huxiu.com/rss/0.xml'},                               '虎嗅'),
    ('ifanr_rss',      'rss', {'url': 'https://www.ifanr.com/feed'},                                    'iFanr 爱范儿'),
    ('economist_rss',  'rss', {'url': 'https://www.economist.com/finance-and-economics/rss.xml'},       'The Economist'),
    ('hn_api',         'api',     {'url': 'https://hacker-news.firebaseio.com/v0/topstories.json', 'limit': 30}, 'Hacker News Top30'),
    ('github_trending','crawler', {'since': 'daily', 'limit': 25},                                      'GitHub 每日 Trending'),
]

# ─────────────────────────────────────────────
# 初始频道
# ─────────────────────────────────────────────
CHANNELS = [
    {
        'name': 'AI技术动态',
        'filter_prompt': (
            '我关注AI技术领域的内容，包括：\n'
            '1. 大模型技术突破（架构创新/训练方法/推理能力）\n'
            '2. AI产品发布与重要更新（ChatGPT/Claude/Gemini/国内大模型等）\n'
            '3. AI企业重要动态（融资/收购/战略调整）\n'
            '4. AI开发工具与框架（LangChain/vLLM/Hugging Face等）\n'
            '5. 国内AI生态动态（百度/腾讯/阿里/字节/创业公司）\n'
            '不关注：宏观经济、政治新闻、非AI相关科技内容、自然灾害。\n'
            '请给出0-1的相关度分数，1=完全符合，0=完全无关。'
        ),
        'min_score': 0.70,
        'wecom_target': 'aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV',
    },
    {
        'name': 'GitHub热门项目',
        'filter_prompt': (
            '我关注GitHub每日trending中值得关注的开源项目，包括：\n'
            '1. AI/LLM相关框架和工具（优先）\n'
            '2. 开发效率工具（编辑器/CLI/自动化）\n'
            '3. 创新性技术项目（star增长显著）\n'
            '不关注：纯文档类仓库、教程合集、与技术无关的项目。\n'
            '请给出0-1的相关度分数。'
        ),
        'min_score': 0.60,
        'wecom_target': 'aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV',
    },
]


def run_schema(conn):
    logger.info(f'执行 schema: {SCHEMA_PATH}')
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        sql = f.read()
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    cur.close()
    logger.info('Schema 执行完成 ✅')


def insert_sources(conn):
    cur = conn.cursor()
    count = 0
    for name, stype, config, desc in SOURCES:
        cur.execute(
            """
            INSERT INTO t_data_sources (name, type, config, description, enabled)
            VALUES (%s, %s, %s, %s, true)
            ON CONFLICT (name) DO UPDATE SET
                type=EXCLUDED.type, config=EXCLUDED.config,
                description=EXCLUDED.description, updated_at=NOW()
            """,
            (name, stype, json.dumps(config), desc)
        )
        count += 1
    conn.commit()
    cur.close()
    logger.info(f'插入数据源: {count} 条 ✅')


def insert_channels(conn):
    cur = conn.cursor()
    for ch in CHANNELS:
        cur.execute(
            """
            INSERT INTO t_channels (name, filter_prompt, min_score, enabled)
            VALUES (%s, %s, %s, true)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (ch['name'], ch['filter_prompt'], ch['min_score'])
        )
        row = cur.fetchone()
        if row:
            channel_id = row[0]
            # 插入推送目标
            cur.execute(
                """
                INSERT INTO t_push_destinations (channel_id, type, target, enabled)
                VALUES (%s, 'wecom', %s, true)
                ON CONFLICT DO NOTHING
                """,
                (channel_id, ch['wecom_target'])
            )
    conn.commit()
    cur.close()
    logger.info(f'插入频道: {len(CHANNELS)} 条 ✅')


def main():
    parser = argparse.ArgumentParser(description='初始化数据库')
    parser.add_argument('--no-data', action='store_true', help='只建表，不插初始数据')
    args = parser.parse_args()

    conn = get_conn()
    try:
        run_schema(conn)
        if not args.no_data:
            insert_sources(conn)
            insert_channels(conn)
        logger.info('数据库初始化完成 🎉')
    except Exception as e:
        logger.exception(f'初始化失败: {e}')
        sys.exit(1)
    finally:
        release_conn(conn)


if __name__ == '__main__':
    main()
