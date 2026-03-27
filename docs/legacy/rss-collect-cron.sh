#!/bin/bash
# ⚠️  已废弃（DEPRECATED）：调度已由 APScheduler daemon 接管（scheduler/daemon.py）
# 请勿加入 crontab，保留此文件仅供历史参考。

# rss-collect-cron.sh — 本地 cron 版资讯采集（每小时静默运行）
# 纯数据采集，不推送，不依赖 OpenClaw cron agent
# crontab 建议：0 * * * * /root/data/projects/github.com/datacollector/rss-collect-cron.sh
export PATH="/root/bin:/usr/local/lib/.nvm/versions/node/v22.17.0/bin:/usr/bin:/bin:$PATH"

PROJ=/root/data/projects/github.com/datacollector
LOG=$PROJ/logs/rss-cron.log
CHANNEL="openclaw-wecom-bot"
TARGET="aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV"

cd "$PROJ" || exit 1

# 静默采集 RSS（v3 新入口）
OUTPUT=$(venv/bin/python3 crawler/run.py --job rss 2>&1)
EXIT_CODE=$?

# 追加日志
echo "[$(date '+%Y-%m-%d %H:%M:%S')] exit=$EXIT_CODE" >> "$LOG"
echo "$OUTPUT" >> "$LOG"
echo "---" >> "$LOG"

if [ $EXIT_CODE -ne 0 ]; then
    # 采集失败才发告警
    MSG="⚠️ 资讯采集失败（RSS），请检查。\n时间：$(date '+%Y-%m-%d %H:%M:%S')\n错误：$(echo "$OUTPUT" | tail -5)"
    openclaw message send \
        --channel "$CHANNEL" \
        --target  "$TARGET" \
        --message "$MSG"
fi
