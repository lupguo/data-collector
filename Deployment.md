# Deployment.md — 部署文档

> 虾人情报站 v3 部署指南

## 环境要求

| 组件 | 版本要求 |
|------|----------|
| Python | 3.10+ |
| PostgreSQL | 14+ |
| OpenClaw | 已配置 `openclaw-wecom-bot` channel |
| 系统 | Linux（crontab 支持） |

---

## 一、首次部署

### 1. 克隆 / 进入项目目录

```bash
cd /root/data/projects/github.com/datacollector
```

### 2. 创建 Python 虚拟环境并安装依赖

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. 配置环境变量

编辑 `.env`：

```env
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=datacollector
DB_USER=datacrawler
DB_PASS=your_password

FLASK_HOST=127.0.0.1
FLASK_PORT=18180

WECOM_CHANNEL=openclaw-wecom-bot
WECOM_TARGET=your_group_id
```

### 4. 创建 PostgreSQL 数据库和用户

```bash
# 以 postgres 超级用户执行
psql -U postgres <<EOF
CREATE USER datacrawler WITH PASSWORD 'your_password';
CREATE DATABASE datacollector OWNER datacrawler;
GRANT ALL PRIVILEGES ON DATABASE datacollector TO datacrawler;
EOF
```

### 5. 初始化数据库（建表 + 插入初始数据）

```bash
venv/bin/python3 scripts/init_db.py
```

执行后会：
- 创建全部 11 张 `t_` 前缀表
- 插入 18 个数据源（RSS / HN / GitHub）
- 插入 2 个初始频道（AI技术动态、GitHub热门项目）
- 配置企业微信推送目标

### 6. 验证初始化

```bash
venv/bin/python3 -c "
from db.db import get_conn, release_conn
conn = get_conn()
cur = conn.cursor()
cur.execute(\"SELECT name FROM t_channels\")
print('频道:', cur.fetchall())
cur.execute(\"SELECT COUNT(*) FROM t_data_sources WHERE enabled=true\")
print('数据源:', cur.fetchone()[0], '个')
release_conn(conn)
"
```

---

## 二、配置系统 cron（采集 / 分析 / 路由）

```bash
crontab -e
```

添加以下内容：

```cron
# 虾人情报站 v3 — 采集层（每小时整点）
0  * * * * /root/data/projects/github.com/datacollector/venv/bin/python3 /root/data/projects/github.com/datacollector/crawler/run.py --job all >> /root/data/projects/github.com/datacollector/logs/collect.log 2>&1

# 分析层（每小时 :20，采集完成后）
20 * * * * /root/data/projects/github.com/datacollector/venv/bin/python3 /root/data/projects/github.com/datacollector/filter/run_filter.py --limit 50 >> /root/data/projects/github.com/datacollector/logs/filter.log 2>&1

# 路由层（每小时 :25，分析完成后）
25 * * * * /root/data/projects/github.com/datacollector/venv/bin/python3 /root/data/projects/github.com/datacollector/router/run_router.py >> /root/data/projects/github.com/datacollector/logs/router.log 2>&1

# Watchdog（每5分钟守护 Caddy/Flask）
*/5 * * * * /root/data/projects/github.com/caddy-fileserver/watchdog-cron.sh >> /root/data/projects/github.com/caddy-fileserver/logs/watchdog-cron.log 2>&1
```

---

## 三、配置 OpenClaw cron（推送层）

推送任务已在 OpenClaw 中配置，**只需确认以下三个 job 处于 enabled 状态**：

| Job | 调度 | 说明 |
|-----|------|------|
| `baaa7678` | `30 9,13,17 * * *` | GitHub热门项目推送 |
| `ea090c87` | `30 9,13,17 * * *` | AI技术动态推送 |
| `d587ab1b` | `35 9,13,17 * * *` | Cron监控告警 |

查看状态：
```bash
openclaw cron list
```

---

## 四、启动 Flask API（可选）

```bash
bash start.sh
```

或用 supervisor 管理（配置见 `supervisor/programs/flask_api.conf`）：

```bash
supervisord -c supervisor/supervisord.conf
supervisorctl status
```

API 默认监听 `127.0.0.1:18180`，可用接口：

| 接口 | 说明 |
|------|------|
| `GET /status` | 系统状态（最近任务 + 统计） |
| `GET /tasks?limit=10` | 任务列表 |
| `GET /tasks/<task_id>` | 任务详情（含子任务） |
| `GET /channels` | 频道列表 |
| `GET /channels/<name>/items?limit=20` | 频道待推送条目 |

---

## 五、首次数据采集与推送

部署完成后，手动跑一次完整链路验证：

```bash
cd /root/data/projects/github.com/datacollector

# Step 1: 采集（约 40s）
venv/bin/python3 crawler/run.py --job all

# Step 2: LLM 分析（约 60s，处理 30 条）
venv/bin/python3 filter/run_filter.py --limit 30

# Step 3: 路由
venv/bin/python3 router/run_router.py

# Step 4: 预览推送内容
venv/bin/python3 push/run_push.py --channel "AI技术动态" --dry-run
venv/bin/python3 push/run_push.py --channel "GitHub热门项目" --dry-run

# Step 5: 真实推送
venv/bin/python3 push/run_push.py --channel "AI技术动态" --limit 10
venv/bin/python3 push/run_push.py --channel "GitHub热门项目" --limit 12
```

---

## 六、新增频道

### 方式一：SQL 直接插入

```sql
-- 1. 新建频道
INSERT INTO t_channels (name, filter_prompt, min_score) VALUES (
  '腾讯股价分析',
  '我关注腾讯相关新闻：业绩财报、产品发布、投资并购、监管影响、游戏版号、微信生态。',
  0.75
);

-- 2. 关联数据源
INSERT INTO t_channel_sources (channel_id, source_id)
SELECT c.id, s.id FROM t_channels c, t_data_sources s
WHERE c.name='腾讯股价分析'
  AND s.name IN ('36kr_rss', 'huxiu_rss', 'bloomberg_rss', 'wsj_rss');

-- 3. 配置推送目标
INSERT INTO t_push_destinations (channel_id, type, target)
SELECT id, 'wecom', 'aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV'
FROM t_channels WHERE name='腾讯股价分析';
```

### 方式二：在 OpenClaw cron 添加推送任务

```
调度：30 9,13,17 * * *
payload：
  cd /root/data/projects/github.com/datacollector && \
  venv/bin/python3 push/run_push.py --channel "腾讯股价分析" --limit 8
```

---

## 七、新增数据源

```sql
-- 注册新数据源
INSERT INTO t_data_sources (name, type, config, description) VALUES (
  'my_new_rss', 'rss',
  '{"url": "https://example.com/feed.xml"}',
  '新数据源说明'
);

-- 关联到频道
INSERT INTO t_channel_sources (channel_id, source_id)
SELECT c.id, s.id FROM t_channels c, t_data_sources s
WHERE c.name='AI技术动态' AND s.name='my_new_rss';
```

采集器下次运行时会自动识别并采集新数据源（`enabled=true`）。

---

## 八、日志位置

| 日志 | 路径 |
|------|------|
| 采集日志 | `logs/collect.log` |
| 分析日志 | `logs/filter.log` |
| 路由日志 | `logs/router.log` |
| Flask API | `logs/flask.log` |
| Watchdog | `/root/data/projects/github.com/caddy-fileserver/logs/watchdog-cron.log` |

查看最近错误：
```bash
grep -i error logs/collect.log | tail -20
grep -i error logs/filter.log | tail -20
```

---

## 九、常见问题

**Q: LLM 分析结果全是降级值（score=0.5，tags=[]）？**
A: `openclaw agent` 命令不可用或超时。检查：
```bash
openclaw agent --agent main --message "test" 
```

**Q: 推送没有内容？**
A: 检查路由表是否有未推送条目：
```bash
venv/bin/python3 -c "
from db.db import get_conn, release_conn
conn = get_conn(); cur = conn.cursor()
cur.execute('SELECT c.name, COUNT(*) FROM t_item_channel_routing r JOIN t_channels c ON r.channel_id=c.id WHERE r.pushed=false GROUP BY c.name')
print(cur.fetchall()); release_conn(conn)
"
```

**Q: 旧数据（v2 三张表）如何迁移？**
A: 执行迁移脚本：
```bash
venv/bin/python3 scripts/migrate_v2.py
```

---

*最后更新：2026-03-18*
