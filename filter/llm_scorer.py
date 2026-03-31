"""
filter/llm_scorer.py — LLM 单条/批量评分
v3 重构：
  - 使用 filter/llm_http.py 直接 HTTP 调用，替代 subprocess openclaw agent
  - 新增 score_single(item) 供并发 Worker 调用
  - score_batch() 保留（向后兼容，内部调 score_single）
  - token 用量上报保留
"""
import json
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from filter.llm_http import call_llm_http, get_token_usage

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 配置读取
# ─────────────────────────────────────────────

_DEFAULT_PROMPT = """分析以下新闻/技术条目，给出relevance_score(0-1)/tags(3个中文标签数组)/summary(30字内中文摘要)。
严格只输出JSON对象，无其他文字：
{item_json}"""


def _get_max_content_length() -> int:
    return int(os.getenv('LLM_MAX_CONTENT', 150))


def _get_single_timeout() -> int:
    return int(os.getenv('LLM_SINGLE_TIMEOUT', 30))


def _get_prompt_template() -> str:
    return os.getenv('LLM_SINGLE_PROMPT_TEMPLATE', _DEFAULT_PROMPT)


def _get_model() -> str:
    return os.getenv('LLM_MODEL', 'auto')


# ─────────────────────────────────────────────
# LLM 用量上报（保留原逻辑）
# ─────────────────────────────────────────────

def record_llm_usage(
    task_id,
    sub_task_id,
    phase: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str = 'gongfeng/auto',
):
    """将 LLM token 用量写入 t_llm_usage（保持原接口不变）"""
    total_tokens = prompt_tokens + completion_tokens
    cost_usd = round(total_tokens * 0.000002, 6)

    try:
        from db.db import execute as _execute
        _execute(
            """
            INSERT INTO t_llm_usage
                (task_id, sub_task_id, phase, model,
                 prompt_tokens, completion_tokens, total_tokens, cost_usd)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(task_id) if task_id else None,
                str(sub_task_id) if sub_task_id else None,
                phase,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost_usd,
            )
        )
        logger.debug(
            f'record_llm_usage: phase={phase} prompt={prompt_tokens} '
            f'completion={completion_tokens} total={total_tokens}'
        )
    except Exception as e:
        logger.error(f'record_llm_usage 写入 DB 失败: {e}')


# ─────────────────────────────────────────────
# JSON 解析工具
# ─────────────────────────────────────────────

def _extract_json_object(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象，兼容 markdown 代码块"""
    text = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()

    # 找 { ... }
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def _extract_json_array(text: str) -> list | None:
    """从 LLM 输出中提取 JSON 数组（向后兼容 score_batch）"""
    text = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()

    start = text.find('[')
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


# ─────────────────────────────────────────────
# 单条评分（并发 Worker 用）
# ─────────────────────────────────────────────

def score_single(
    item: dict,
    task_id=None,
    sub_task_id=None,
) -> dict:
    """
    对单条条目进行 LLM 评分。

    参数：
      item — {id, title, content, source_name}

    返回：
      {
        id:              int,
        relevance_score: float,
        tags:            list[str],
        summary:         str,
        ok:              bool,    # True=成功, False=失败
        error:           str,     # 失败时的错误描述
      }
    """
    max_content   = _get_max_content_length()
    timeout       = _get_single_timeout()
    prompt_tpl    = _get_prompt_template()
    model_name    = _get_model()
    item_id       = item['id']

    input_item = {
        'id':          item_id,
        'title':       (item.get('title') or '')[:max_content],
        'content':     (item.get('content') or '')[:max_content],
        'source_name': item.get('source_name', ''),
    }
    prompt = prompt_tpl.format(item_json=json.dumps(input_item, ensure_ascii=False))

    raw_output = call_llm_http(prompt, timeout=timeout, model=model_name)

    # token 上报
    try:
        pt, ct = get_token_usage(prompt, raw_output or '')
        record_llm_usage(
            task_id=task_id,
            sub_task_id=sub_task_id,
            phase='analyze',
            prompt_tokens=pt,
            completion_tokens=ct,
            model=model_name,
        )
    except Exception as e:
        logger.warning(f'token 用量上报失败 item_id={item_id}: {e}')

    if not raw_output:
        return {
            'id': item_id, 'relevance_score': 0.5,
            'tags': [], 'summary': '',
            'ok': False, 'error': 'LLM 返回空响应',
        }

    parsed = _extract_json_object(raw_output)
    if parsed is None:
        return {
            'id': item_id, 'relevance_score': 0.5,
            'tags': [], 'summary': '',
            'ok': False, 'error': f'JSON 解析失败，原始输出: {raw_output[:100]}',
        }

    try:
        score = float(parsed.get('relevance_score', 0.5))
        score = max(0.0, min(1.0, score))
    except (ValueError, TypeError):
        score = 0.5

    tags = parsed.get('tags', [])
    if not isinstance(tags, list):
        tags = []
    summary = parsed.get('summary', '')

    return {
        'id': item_id,
        'relevance_score': score,
        'tags':    tags,
        'summary': summary,
        'ok':    True,
        'error': '',
    }


# ─────────────────────────────────────────────
# 批量评分（向后兼容接口，内部调 score_single）
# ─────────────────────────────────────────────

def score_batch(items: list, task_id=None, sub_task_id=None) -> list:
    """
    批量评分（保留原接口，串行调 score_single）。
    新代码请直接用 score_single + ThreadPoolExecutor。

    返回：[{id, relevance_score, tags, summary}, ...]
    """
    results = []
    for item in items:
        r = score_single(item, task_id=task_id, sub_task_id=sub_task_id)
        results.append({
            'id':              r['id'],
            'relevance_score': r['relevance_score'],
            'tags':            r['tags'],
            'summary':         r['summary'],
        })
    return results
