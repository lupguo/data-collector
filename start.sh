#!/bin/bash
# start.sh — 启动虾人情报站 v3 Flask API + 调度守护进程
PROJ=/root/data/projects/github.com/datacollector
VENV=$PROJ/venv/bin/python3
PID_FILE=$PROJ/flask.pid
SCHEDULER_PID=$PROJ/scheduler.pid
LOG=$PROJ/logs/flask.log
SCHED_LOG=$PROJ/logs/scheduler.log

mkdir -p $PROJ/logs

# ── PostgreSQL ──
if ! su - postgres -c "/usr/bin/pg_ctl -D /root/database/postgresql/ status" 2>/dev/null | grep -q "running"; then
    echo "启动 PostgreSQL..."
    mkdir -p /var/run/postgresql && chown postgres:postgres /var/run/postgresql
    su - postgres -c "/usr/bin/pg_ctl -D /root/database/postgresql/ -l /root/database/postgresql/logs/startup.log start"
    sleep 2
fi

# ── Flask API ──
if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
    echo "Flask 已在运行 (PID: $(cat $PID_FILE))"
else
    cd $PROJ
    nohup $VENV api/server.py >> $LOG 2>&1 &
    echo $! > $PID_FILE
    echo "Flask 已启动 (PID: $!), 日志: $LOG"
    sleep 1
    curl -s http://127.0.0.1:18180/api/health && echo " ✅" || echo " ⚠️ 等待启动..."
fi

# ── 调度守护进程 ──
if [ -f "$SCHEDULER_PID" ] && kill -0 "$(cat $SCHEDULER_PID)" 2>/dev/null; then
    echo "调度守护进程已在运行 (PID: $(cat $SCHEDULER_PID))"
else
    cd $PROJ
    nohup $VENV scheduler/daemon.py > $SCHED_LOG 2>&1 &
    SCHED_PID=$!
    echo $SCHED_PID > $SCHEDULER_PID
    echo "调度守护进程已启动 (PID: $SCHED_PID), 日志: $SCHED_LOG"
fi
