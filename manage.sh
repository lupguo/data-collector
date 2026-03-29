#!/bin/bash
# 虾人情报站 V3 — 进程管理快捷脚本
# 用法: ./manage.sh [start|stop|restart|status|log]

PROJ="/root/data/projects/github.com/datacollector"
SUPERCTL="$PROJ/venv/bin/supervisorctl -c $PROJ/supervisor/supervisord.conf"
SUPERVISORD="$PROJ/venv/bin/supervisord -c $PROJ/supervisor/supervisord.conf"

case "$1" in
  start)
    if pgrep -f "supervisord" > /dev/null; then
      echo "supervisord 已在运行，直接启动 shrimp-daemon..."
      $SUPERCTL start shrimp-daemon
    else
      echo "启动 supervisord..."
      $SUPERVISORD
      sleep 2
      $SUPERCTL status
    fi
    ;;
  stop)
    $SUPERCTL stop shrimp-daemon
    ;;
  restart)
    $SUPERCTL restart shrimp-daemon
    ;;
  status)
    $SUPERCTL status
    echo ""
    echo "--- 最近10行日志 ---"
    tail -10 "$PROJ/logs/scheduler.log"
    ;;
  log)
    tail -50 "$PROJ/logs/scheduler.log"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|log}"
    exit 1
    ;;
esac
