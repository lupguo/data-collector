"""
filter/llm_scorer.py — 批量 LLM 评分
每批最多 20 条，调用 `openclaw agent` CLI
输出 relevance_score / tags / summary

v2 新增：
  - _call_llm 结束后估算/解析 token 用量
  - record_llm_usage() 写入 t_llm_usage 表
  - score_batch 接受 task_id / sub_task_id 参数（向后兼容默认 None）
"""
import json
import logging
import subprocess
import re
import os

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

logger = logging.getLogger(__name__)

# 从环境变量读取，fallback 到硬编码默认值
_DEFAULT_PROMPT = """分析以下{count}条新闻/技术条目，每条给出relevance_score(0-1)/tags(3个中文标签数组)/summary(30字内中文摘要)。
严格只输出JSON数组，无其他文字：
{items_json}"""


def _get_batch_size() -> int:
    return int(os.getenv('LLM_BATCH_SIZE', 20))


def _get_max_content_length() -> int:
    return int(os.getenv('LLM_MAX_CONTENT', 150))


def _get_timeout() -> int:
    return int(os.getenv('LLM_TIMEOUT', 120))


def _get_prompt_template() -> str:
    return os.getenv('LLM_PROMPT_TEMPLATE', _DEFAULT_PROMPT)


# 保留全局常量兼容老代码
BATCH_SIZE = 20
SCORE_PROMPT_TEMPLATE = _DEFAULT_PROMPT


# ─────────────────────────────────────────────
# LLM 调用
# ─────────────────────────────────────────────

def _call_llm(prompt: str) -> str:
    """调用 openclaw agent CLI，返回原始输出字符串"""
    timeout = _get_timeout()
    try:
        result = subprocess.run(
            ['openclaw', 'agent', '--agent', 'main', '--message', prompt],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            logger.error(f'openclaw agent 失败: {result.stderr[:200]}')
            return ''
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error('openclaw agent 超时')
        return ''
    except Exception as e:
        logger.error(f'openclaw agent 异常: {e}')
        return ''


def _estimate_tokens(prompt: str, output: str) -> tuple[int, int]:
    """
    估算 token 用量（无法从输出解析时的 fallback）。
    - 中文字符：约 1.5 字符/token（CJK 范围）
    - 英文/数字/符号：约 4 字符/token
    返回 (prompt_tokens, completion_tokens)
    """
    import re

    def _count(text: str) -> int:
        cjk = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
        other = len(text) - cjk
        return max(1, int(cjk / 1.5 + other / 4))

    return _count(prompt), _count(output)


def _parse_token_usage(output: str) -> tuple[int, int] | None:
    """
    尝试从 LLM 输出中解析 token 用量信息。
    openclaw agent 有时会在输出末尾附带形如：
      [usage: prompt=123 completion=456 total=579]
    或 JSON 中含 usage 字段。返回 (prompt_tokens, completion_tokens)，解析失败返回 None。
    """
    # 模式1：[usage: prompt=N completion=N total=N]
    m = re.search(
        r'\[usage:\s*prompt=(\d+)\s+completion=(\d+)',
        output, re.IGNORECASE
    )
    if m:
        return int(m.group(1)), int(m.group(2))

    # 模式2：JSON 对象含 usage.prompt_tokens / usage.completion_tokens
    m2 = re.search(r'"prompt_tokens"\s*:\s*(\d+)', output)
    m3 = re.search(r'"completion_tokens"\s*:\s*(\d+)', output)
    if m2 and m3:
        return int(m2.group(1)), int(m3.group(1))

    return None


# ─────────────────────────────────────────────
# LLM 用量上报
# ─────────────────────────────────────────────

def record_llm_usage(
    task_id,
    sub_task_id,
    phase: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str = 'openclaw/auto',
):
    """
    将 LLM token 用量记录写入 t_llm_usage。
    task_id / sub_task_id 允许为 None（向后兼容）。
    cost_usd 按 0.002 USD/1K token 粗估（可后期配置）。
    """
    total_tokens = prompt_tokens + completion_tokens
    # 粗估费用（可选，传 0 也无妨）
    cost_usd = round(total_tokens * 0.000002, 6)  # $0.002 / 1K tokens

    try:
        # 延迟导入，避免循环依赖
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
            f'record_llm_usage: phase={phase} model={model} '
            f'prompt={prompt_tokens} completion={completion_tokens} '
            f'total={total_tokens} cost={cost_usd:.6f} USD'
        )
    except Exception as e:
        logger.error(f'record_llm_usage 写入 DB 失败: {e}')


# ─────────────────────────────────────────────
# JSON 解析工具
# ─────────────────────────────────────────────

def _extract_json_array(text: str):
    """从 LLM 输出中提取 JSON 数组，兼容 markdown 代码块"""
    # 去掉 markdown 代码块
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = text.rstrip('`').strip()

    # 找到第一个 [ ... ] 范围
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
# 批量评分入口
# ─────────────────────────────────────────────

def score_batch(items: list, task_id=None, sub_task_id=None) -> list:
    """
    批量 LLM 评分，每批最多 BATCH_SIZE 条。

    参数：
      items        — [{id, title, content, source_name}, ...]
      task_id      — 关联的 task UUID（可选，默认 None，向后兼容）
      sub_task_id  — 关联的 sub_task UUID（可选，默认 None，向后兼容）

    返回：[{id, relevance_score, tags, summary}, ...]
    解析失败时该条 score=0.5, tags=[], summary=''
    """
    if not items:
        return []

    batch_size    = _get_batch_size()
    max_content   = _get_max_content_length()
    prompt_template = _get_prompt_template()
    model_name    = os.getenv('LLM_MODEL', 'openclaw/auto')

    results = []

    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start: batch_start + batch_size]

        # 构建精简输入
        input_items = []
        for it in batch:
            input_items.append({
                'id':          it['id'],
                'title':       it.get('title', '')[:max_content],
                'content':     (it.get('content') or '')[:max_content],
                'source_name': it.get('source_name', ''),
            })

        prompt = prompt_template.format(
            count=len(batch),
            items_json=json.dumps(input_items, ensure_ascii=False)
        )

        logger.info(f'LLM 评分批次: {batch_start//batch_size + 1}，条数: {len(batch)}')
        raw_output = _call_llm(prompt)

        # ── token 用量上报 ──
        parsed_usage = _parse_token_usage(raw_output) if raw_output else None
        if parsed_usage:
            prompt_tokens, completion_tokens = parsed_usage
            logger.debug(f'从输出解析到 token 用量: prompt={prompt_tokens} completion={completion_tokens}')
        else:
            prompt_tokens, completion_tokens = _estimate_tokens(prompt, raw_output or '')
            logger.debug(f'估算 token 用量: prompt={prompt_tokens} completion={completion_tokens}')

        record_llm_usage(
            task_id=task_id,
            sub_task_id=sub_task_id,
            phase='analyze',
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model_name,
        )

        # 解析 JSON
        parsed = None
        if raw_output:
            parsed = _extract_json_array(raw_output)
            if parsed is None:
                logger.warning(f'LLM 输出 JSON 解析失败，原始输出前200字: {raw_output[:200]}')

        # 建立 id -> 解析结果的映射
        parsed_map = {}
        if parsed:
            for p in parsed:
                try:
                    pid = int(p.get('id', -1))
                    parsed_map[pid] = p
                except (ValueError, TypeError):
                    pass

        # 合并结果，解析失败时降级
        for it in batch:
            item_id = it['id']
            p = parsed_map.get(item_id)
            if p:
                try:
                    score = float(p.get('relevance_score', 0.5))
                    score = max(0.0, min(1.0, score))
                except (ValueError, TypeError):
                    score = 0.5
                tags    = p.get('tags', [])
                summary = p.get('summary', '')
                if not isinstance(tags, list):
                    tags = []
            else:
                logger.warning(f'条目 id={item_id} 未在 LLM 返回中找到，使用降级值')
                score   = 0.5
                tags    = []
                summary = ''

            results.append({
                'id':              item_id,
                'relevance_score': score,
                'tags':            tags,
                'summary':         summary,
            })

    return results
