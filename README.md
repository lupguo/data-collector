# 虾人情报站 v3

> 一个多频道智能信息路由平台——自动采集、LLM 过滤、按兴趣分发到企业微信群。

## 功能特性

- **多源采集**：GitHub Trending、Hacker News、16 个 RSS 源（TechCrunch、Wired、ArXiv、36Kr 等）
- **LLM 智能过滤**：批量评分 + 打标签 + 生成中文摘要，过滤无关噪音
- **多频道多播**：一条内容可同时路由到多个兴趣频道（AI 技术动态、GitHub 热门项目等）
- **全链路追踪**：`task_id` 贯穿采集 → 分析 → 路由 → 推送，日志/统计完整可查
- **配置驱动扩展**：新增数据源或频道只需改数据库，无需动代码
- **推送零 LLM**：推送链路纯读库发消息，执行 < 15s，稳定可靠

## 架构概览

```
采集层（系统 cron）→ 分析层（LLM 批量评分）→ 路由层（多播分发）→ 推送层（OpenClaw cron）
      ↓                      ↓                       ↓                    ↓
 t_raw_items          t_item_analysis       t_item_channel_routing    企业微信群
```

详见 [docs/architecture.md](docs/architecture.md)

## 目录结构

```
datacollector/
├── crawler/          # 采集层：GitHub / HN / RSS
├── filter/           # 分析层：LLM 批量评分打标
├── router/           # 路由层：多播分发到频道
├── push/             # 推送层：读库→格式化→发消息
├── api/              # Flask HTTP API（状态查询）
├── db/               # 数据库连接池 + Schema
├── config/           # 频道/数据源配置说明
├── scripts/          # 初始化 / 迁移脚本
└── docs/             # 架构文档
```

## 快速开始

详见 [Deployment.md](Deployment.md)

## 调度说明

| 任务 | 调度 | 方式 |
|------|------|------|
| 全源采集 | 每小时 :00 | 系统 cron |
| LLM 分析打标 | 每小时 :20 | 系统 cron |
| 频道路由分发 | 每小时 :25 | 系统 cron |
| GitHub 热门推送 | 9:30 / 13:30 / 17:30 | OpenClaw cron |
| AI 技术动态推送 | 9:30 / 13:30 / 17:30 | OpenClaw cron |
| Cron 监控告警 | 9:35 / 13:35 / 17:35 | OpenClaw cron |
| Watchdog 守护 | 每 5 分钟 | 系统 cron |

## 数据表

共 11 张表，均以 `t_` 开头，含完整字段注释。详见 [docs/schema.md](docs/schema.md)

| 表名 | 用途 |
|------|------|
| `t_data_sources` | 数据源注册 |
| `t_channels` | 兴趣频道配置 |
| `t_channel_sources` | 频道-数据源关联 |
| `t_push_destinations` | 推送目标配置 |
| `t_tasks` | 主任务（全链路） |
| `t_sub_tasks` | 子任务（各阶段） |
| `t_raw_items` | 原始采集条目 |
| `t_item_analysis` | LLM 分析结果 |
| `t_item_channel_routing` | 多播路由结果 |
| `t_task_logs` | 全链路日志 |
| `t_task_stats` | 阶段统计 |

## 新增频道示例

```sql
-- 新增"腾讯股价分析"频道
INSERT INTO t_channels (name, filter_prompt, min_score) VALUES (
  '腾讯股价分析',
  '我关注腾讯相关新闻：业绩财报、产品发布、投资并购、监管影响、游戏版号、微信生态。
   不关注与腾讯无关的其他公司新闻。',
  0.75
);
-- 关联数据源
INSERT INTO t_channel_sources (channel_id, source_id)
SELECT c.id, s.id FROM t_channels c, t_data_sources s
WHERE c.name='腾讯股价分析' AND s.name IN ('36kr_rss','huxiu_rss','bloomberg_rss');
-- 配置推送目标
INSERT INTO t_push_destinations (channel_id, type, target)
SELECT id, 'wecom', 'aibxNZEdpp-H68KFEX-qkUX5K8Yfpp8G_dV' FROM t_channels WHERE name='腾讯股价分析';
```

## 手动触发

```bash
cd /root/data/projects/github.com/datacollector

# 采集
venv/bin/python3 crawler/run.py --job all

# 分析（处理未打标条目，限制30条）
venv/bin/python3 filter/run_filter.py --limit 30

# 路由
venv/bin/python3 router/run_router.py

# 推送（dry-run 预览）
venv/bin/python3 push/run_push.py --channel "AI技术动态" --dry-run

# 推送（真实发送）
venv/bin/python3 push/run_push.py --channel "AI技术动态" --limit 10
venv/bin/python3 push/run_push.py --channel "GitHub热门项目" --limit 12
```
