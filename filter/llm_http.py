"""
filter/llm_http.py — 直接 HTTP 调用工蜂 AI LLM API
替代原来的 subprocess openclaw agent，消除进程启动开销

认证方式：读取 auth-profiles.json 的 OAuth token（gongfeng）
API：OpenAI-compatible chat/completions
"""
import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

# API 端点
_BASE_URL = "https://copilot.code.woa.com/server/openclaw/copilot-gateway/v1"
_AUTH_PROFILES_PATH = "/projects/.openclaw/agents/main/agent/auth-profiles.json"

# 缓存（进程级，避免每次磁盘 IO；token 有效期极长可安全缓存）
_auth_cache: dict | None = None
_auth_cache_mtime: float = 0.0


def _load_auth() -> dict:
    """
    读取 auth-profiles.json，返回 {access, username, deviceId}。
    文件有更新时自动重新加载。
    """
    global _auth_cache, _auth_cache_mtime

    try:
        mtime = os.path.getmtime(_AUTH_PROFILES_PATH)
    except FileNotFoundError:
        raise RuntimeError(f"auth-profiles.json 不存在: {_AUTH_PROFILES_PATH}")

    if _auth_cache is None or mtime != _auth_cache_mtime:
        with open(_AUTH_PROFILES_PATH, 'r') as f:
            data = json.load(f)
        profile = data['profiles'].get('gongfeng:default', {})
        _auth_cache = {
            'access':   profile['access'],
            'username': profile['username'],
            'deviceId': profile['deviceId'],
        }
        _auth_cache_mtime = mtime
        logger.debug('auth-profiles.json 已加载/重新加载')

    return _auth_cache


def call_llm_http(prompt: str, timeout: int = 30, model: str = 'auto') -> str:
    """
    调用工蜂 AI Chat Completions API，返回 assistant 消息文本。

    参数：
      prompt  — 用户 prompt 字符串
      timeout — HTTP 请求超时秒数（默认 30）
      model   — 模型 ID（默认 'auto'）

    返回：
      assistant 回复文本（str），失败时返回空字符串

    副作用：
      - 失败时 logger.error
    """
    auth = _load_auth()
    headers = {
        'Content-Type':  'application/json',
        'Authorization': f"Bearer {auth['access']}",
        'X-Username':    auth['username'],
        'OAUTH-TOKEN':   auth['access'],
        'DEVICE-ID':     auth['deviceId'],
        'Accept':        'application/json',
    }
    payload = {
        'model':      model,
        'messages':   [{'role': 'user', 'content': prompt}],
        'max_tokens': 300,   # 单条评分：JSON 对象最多 ~150 字节，300 绰绰有余
    }

    t0 = time.time()
    try:
        resp = requests.post(
            f"{_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        elapsed = time.time() - t0

        if resp.status_code == 429:
            logger.warning(f'LLM API 限流 (429)，耗时 {elapsed:.1f}s')
            return ''

        if resp.status_code != 200:
            logger.error(f'LLM API 错误 HTTP {resp.status_code}: {resp.text[:200]}，耗时 {elapsed:.1f}s')
            return ''

        data = resp.json()
        content = data['choices'][0]['message']['content']
        logger.debug(f'LLM API 成功，耗时 {elapsed:.1f}s，tokens={data.get("usage", {}).get("total_tokens", "?")}')
        return content

    except requests.Timeout:
        elapsed = time.time() - t0
        logger.error(f'LLM API 超时（{elapsed:.1f}s > {timeout}s）')
        return ''
    except requests.RequestException as e:
        elapsed = time.time() - t0
        logger.error(f'LLM API 请求异常（{elapsed:.1f}s）: {e}')
        return ''
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f'LLM API 响应解析失败: {e}，响应前200字: {resp.text[:200] if "resp" in dir() else "N/A"}')
        return ''


def get_token_usage(prompt: str, response: str, api_response: dict | None = None) -> tuple[int, int]:
    """
    从 API 响应获取 token 用量，失败时估算。
    返回 (prompt_tokens, completion_tokens)
    """
    if api_response and 'usage' in api_response:
        u = api_response['usage']
        return u.get('prompt_tokens', 0), u.get('completion_tokens', 0)

    # 估算 fallback
    import re
    def _count(text: str) -> int:
        cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
        other = len(text) - cjk
        return max(1, int(cjk / 1.5 + other / 4))

    return _count(prompt), _count(response)
