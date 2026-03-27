"""
push/sender.py — 多渠道推送（wecom / webhook 可扩展）
通过 BaseSender 抽象，按 destination.type 分发

支持类型：
  wecom   — 企业微信（通过 openclaw message send CLI）
  webhook — HTTP POST JSON（通用 webhook）
"""
import os
import json
import subprocess
import logging

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

logger = logging.getLogger(__name__)

CHANNEL = os.getenv('WECOM_CHANNEL', 'openclaw-wecom-bot')
TARGET  = os.getenv('WECOM_TARGET',  'aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV')


# ─────────────────────────────────────────────
# 基类
# ─────────────────────────────────────────────

class BaseSender:
    def send(self, text: str, destination: dict) -> bool:
        raise NotImplementedError


# ─────────────────────────────────────────────
# 企业微信
# ─────────────────────────────────────────────

class WecomSender(BaseSender):
    def send(self, text: str, destination: dict) -> bool:
        target = destination.get('target', TARGET)
        try:
            result = subprocess.run(
                ['openclaw', 'message', 'send',
                 '--channel', CHANNEL,
                 '--target',  target,
                 '--message', text],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.error(f'WecomSender 推送失败: {result.stderr[:200]}')
                return False
            logger.info(f'WecomSender 推送成功 → {target[:30]}')
            return True
        except subprocess.TimeoutExpired:
            logger.error('WecomSender 推送超时')
            return False
        except Exception as e:
            logger.error(f'WecomSender 推送异常: {e}')
            return False


# ─────────────────────────────────────────────
# Webhook（通用 HTTP POST）
# ─────────────────────────────────────────────

class WebhookSender(BaseSender):
    def send(self, text: str, destination: dict) -> bool:
        if not _HAS_REQUESTS:
            logger.error('WebhookSender 需要安装 requests 库')
            return False
        url = destination.get('target', '')
        if not url:
            logger.error('WebhookSender: target URL 为空')
            return False
        try:
            resp = _requests.post(url, json={'text': text}, timeout=15)
            if resp.status_code >= 400:
                logger.error(f'WebhookSender HTTP {resp.status_code}: {resp.text[:200]}')
                return False
            logger.info(f'WebhookSender 推送成功 → {url[:50]}')
            return True
        except Exception as e:
            logger.error(f'WebhookSender 异常: {e}')
            return False


# ─────────────────────────────────────────────
# 工厂 & 公共入口
# ─────────────────────────────────────────────

_SENDERS: dict[str, BaseSender] = {
    'wecom':   WecomSender(),
    'webhook': WebhookSender(),
}


def send(text: str, destination: dict = None) -> bool:
    """
    发送消息。
    destination: {type, target} — 来自 t_push_destinations
    若 destination 为 None，使用 .env 默认 wecom 配置。
    """
    if destination is None:
        destination = {'type': 'wecom', 'target': TARGET}

    dest_type = destination.get('type', 'wecom')
    sender = _SENDERS.get(dest_type)
    if sender is None:
        logger.warning(f'不支持的推送类型: {dest_type}，可选: {list(_SENDERS.keys())}')
        return False

    return sender.send(text, destination)
