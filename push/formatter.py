"""
push/formatter.py — 消息格式化（零 LLM 调用）
按频道名称/output_format 字段格式化推送消息

格式规范（简洁版）：
  N. 标题
     🔗 URL
     简要说明（一行，可选）
     #标签1 #标签2
"""
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# 每条消息最大字符数（企业微信单条限制约 2000 字）
MAX_CHARS = 1800


def _now_str():
    return datetime.now(CST).strftime('%Y-%m-%d %H:%M')


def _build_item_block(i: int, item: dict, channel_name: str = '') -> str:
    """
    构建单条推送块（简洁格式）：
      N. 标题
         🔗 URL
         简要说明（一行，<=60字）
         #标签1 #标签2
    """
    title   = (item.get('title') or '').strip()[:80]
    url     = (item.get('url') or '').strip()
    tags    = item.get('tags') or []
    summary = (item.get('summary') or '').strip()

    # 简要说明截断到 60 字，保留完整语义（按句截断优先）
    if summary and len(summary) > 60:
        # 尝试在60字内找句号/分号截断
        cut = 60
        for sep in ('。', '；', '；', '.', ',', '，'):
            idx = summary[:65].rfind(sep)
            if idx > 20:
                cut = idx + 1
                break
        summary = summary[:cut].rstrip('，,、 ')

    tag_str = ' '.join(f'#{t}' for t in tags[:3]) if tags else ''

    lines = [f'{i}. {title}']
    lines.append(f'   🔗 {url}')
    if summary:
        lines.append(f'   {summary}')
    if tag_str:
        lines.append(f'   {tag_str}')
    lines.append('')
    return '\n'.join(lines)


def format_channel(items: list, channel: dict) -> str:
    """
    通用频道格式化（单条消息，超长截断）。
    items: [{title, url, tags, summary, ...}, ...]
    """
    channel_name = channel.get('name', '情报频道')
    now = _now_str()

    lines = [
        f'📡 {channel_name}',
        f'🕐 {now}  共 {len(items)} 条',
        '─' * 28,
        '',
    ]

    for i, item in enumerate(items, 1):
        lines.append(_build_item_block(i, item, channel_name))

    text = '\n'.join(lines)

    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + '\n…（更多内容已省略）'

    return text


def format_channel_compact(items: list, channel: dict) -> list:
    """
    简洁分段格式化：超出 MAX_CHARS 时自动拆为多条。
    返回 list[str]，每条均在 MAX_CHARS 以内。
    """
    channel_name = channel.get('name', '情报频道')
    now = _now_str()
    header = f'📡 {channel_name} · {now}\n共 {len(items)} 条\n{"─"*28}\n'

    messages = []
    current = header

    for i, item in enumerate(items, 1):
        block = _build_item_block(i, item, channel_name)

        if len(current) + len(block) > MAX_CHARS:
            messages.append(current.rstrip())
            current = f'📡 {channel_name}（续）\n\n' + block
        else:
            current += block

    if current.strip():
        messages.append(current.rstrip())

    return messages
