#!/bin/bash
# ⚠️  已废弃（DEPRECATED）：调度已由 APScheduler daemon 接管（scheduler/daemon.py）
# 请勿加入 crontab，保留此文件仅供历史参考。

# scripts/github-collect-cron.sh — GitHub Trending 整点采集 cron 包装脚本
# 建议加入 crontab：0 * * * * /root/data/projects/github.com/datacollector/scripts/github-collect-cron.sh
export PATH="/root/bin:/usr/local/lib/.nvm/versions/node/v22.17.0/bin:/usr/bin:/bin:$PATH"

PROJ=/root/data/projects/github.com/datacollector
LOG=$PROJ/logs/github-cron.log
CHANNEL="openclaw-wecom-bot"
TARGET="aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV"

cd "$PROJ" || exit 1

OUTPUT=$(venv/bin/python3 crawler/run.py --job github 2>&1)
EXIT_CODE=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S')] exit=$EXIT_CODE" >> "$LOG"
echo "$OUTPUT" >> "$LOG"
echo "---" >> "$LOG"

if [ $EXIT_CODE -ne 0 ]; then
    MSG="⚠️ GitHub Trending 采集失败，请检查。\n时间：$(date '+%Y-%m-%d %H:%M:%S')\n错误：$(echo "$OUTPUT" | tail -5)"
    openclaw message send \
        --channel "$CHANNEL" \
        --target  "$TARGET" \
        --message "$MSG"
fi
