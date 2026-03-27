# 虾人情报站 v3 — 整体架构设计

> 版本：v3.0  
> 设计时间：2026-03-18  
> 状态：已确认，待实现

---

## 目录

1. [项目背景与目标](#1-项目背景与目标)
2. [核心设计原则](#2-核心设计原则)
3. [整体架构](#3-整体架构)
4. [模块说明](#4-模块说明)
5. [数据流](#5-数据流)
6. [任务追踪体系](#6-任务追踪体系)
7. [调度规划](#7-调度规划)
8. [目录结构](#8-目录结构)
9. [扩展性设计](#9-扩展性设计)
10. [待实现 Roadmap](#10-待实现-roadmap)

---

## 1. 项目背景与目标

### 现状问题

当前系统（v2）存在以下问题：

| 问题 | 现象 |
|------|------|
| 采集与推送强耦合 | OpenClaw cron agentTurn 同时负责采集+推送，120s 超时频繁触发 |
| 无智能过滤 | 采集到的内容直接推送，`ai` 类内容仅占 8%，大量噪音 |
| 无可观测性 | 任务失败无法回溯原因，统计数据缺失 |
| 扩展性差 | 新增数据源或推送频道需修改多处代码 |
| 三张孤立表 | `github_trending` / `hackernews` / `ai_news` 结构重复，无法统一处理 |

### v3 目标

1. **四层解耦**：采集 → 分析 → 路由 → 推送，每层独立可替换
2. **智能 Filter**：基于用户兴趣 Prompt，LLM 批量评分，按相关度路由
3. **多频道多播**：一条内容可同时推送到多个匹配频道
4. **全链路可观测**：`task_id` 贯穿全程，日志/统计完整记录
5. **配置驱动**：数据源、频道、推送目标均为配置，无需改代码即可扩展

---

## 2. 核心设计原则

```
1. 单用户先行，多用户预留      所有表含 user_id，当前默认为 1
2. 配置驱动，代码不硬编码      数据源/频道/推送目标均在数据库或 YAML 中配置
3. 表名 t_ 前缀，字段有注释    统一命名规范，降低维护成本
4. task_id 贯穿全链路          采集/分析/路由/推送均绑定同一 task_id，支持全程回溯
5. 采集与推送完全解耦          OpenClaw cron 只做"读库→发消息"，不碰采集逻辑
6. 多播路由                    一条 item 可命中多个频道，通过路由表实现，防重复推送
7. LLM 仅参与分析层            推送链路零 LLM 调用，保证推送 < 15s
```

---

## 3. 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Layer 0: 配置层                            │
│   t_data_sources   t_channels   t_push_destinations          │
│   （数据源注册）    （频道定义）   （推送目标）                  │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│                    Layer 1: 采集层                            │
│   crawler/run.py  （系统 cron，每小时）                        │
│   读取 enabled 数据源 → 爬取原始内容 → 写入 t_raw_items        │
│   绑定 task_id + sub_task_id                                  │
└─────────────────────────┬────────────────────────────────────┘
                          │ 采集完成后触发（:20）
┌─────────────────────────▼────────────────────────────────────┐
│                    Layer 2: 分析层                            │
│   filter/run_filter.py  （系统 cron，每小时 :20）              │
│   读取未分析条目 → LLM 批量打标/评分/摘要 → 写入 t_item_analysis│
│   每批 20 条，降低 LLM 调用成本                               │
└─────────────────────────┬────────────────────────────────────┘
                          │ 分析完成后触发（:25）
┌─────────────────────────▼────────────────────────────────────┐
│                    Layer 3: 路由层                            │
│   router/run_router.py  （系统 cron，每小时 :25）              │
│   读取所有 enabled 频道的 filter_prompt                        │
│   × 读取高分 analysis 条目                                    │
│   → 多播写入 t_item_channel_routing（UNIQUE 防重）             │
└─────────────────────────┬────────────────────────────────────┘
                          │ 推送时间到（9:30 / 13:30 / 17:30）
┌─────────────────────────▼────────────────────────────────────┐
│                    Layer 4: 推送层                            │
│   push/run_push.py  （OpenClaw cron，3次/天）                  │
│   SELECT routing WHERE pushed=false AND channel_id=?          │
│   → formatter 格式化 → sender 发企业微信                       │
│   → 标记 pushed=true，写 t_task_logs                          │
└──────────────────────────────────────────────────────────────┘

贯穿所有层：
  t_tasks          主任务（一次完整流程）
  t_sub_tasks      子任务（每个阶段×每个数据源/频道）
  t_task_logs      全链路日志（可回溯）
  t_task_stats     阶段统计（成功/失败/跳过/原因分布）
```

---

## 4. 模块说明

### 4.1 采集层 `crawler/`

| 文件 | 职责 |
|------|------|
| `github_trending.py` | 抓取 GitHub Trending（Playwright / HTTP） |
| `hackernews.py` | 调用 HN API，获取 Top 30 |
| `rss_feeds.py` | 批量抓取 RSS 源，支持多源并发 |
| `run.py` | 调度入口，`--job github/hn/rss/all`，创建 task+sub_task |

**写入目标**：`t_raw_items`（URL 去重，idempotent 写入）

---

### 4.2 分析层 `filter/` ★ 新增

| 文件 | 职责 |
|------|------|
| `run_filter.py` | 入口，读取 `t_raw_items` 中未分析条目，批量提交 LLM |
| `llm_scorer.py` | LLM 批量评分逻辑，每批 20 条，返回 score + tags + summary |

**LLM Prompt 设计**：
```
给定以下新闻条目列表，对每条进行分析：
1. relevance_score (0-1)：与科技/AI/商业的综合相关度
2. tags：提取 3-5 个标签（中文），如 ['AI模型','腾讯','融资']
3. summary：50字以内中文摘要（原文为英文时翻译）

输出 JSON 数组格式...
```

**写入目标**：`t_item_analysis`

---

### 4.3 路由层 `router/` ★ 新增

| 文件 | 职责 |
|------|------|
| `run_router.py` | 读取所有 enabled 频道配置，对每条已分析 item 按频道 prompt 打分，写路由结果 |

**路由策略**：
- 读取 `t_channels` 所有 `enabled=true` 的频道及其 `filter_prompt`
- 对每条 `t_item_analysis` 条目，按频道 `filter_prompt` 计算匹配分
- 分数 ≥ `channel.min_score` 则写入 `t_item_channel_routing`
- 多播：同一条 item 可写入多个频道（`UNIQUE item_id + channel_id` 防重）

**写入目标**：`t_item_channel_routing`

---

### 4.4 推送层 `push/`

| 文件 | 职责 |
|------|------|
| `formatter.py` | 按频道格式化消息（当前统一格式，output_format 字段预留自定义） |
| `sender.py` | 调用 `openclaw message send` CLI 发送消息 |
| `run_push.py` | ★ 新增：独立推送入口，`--channel <name>`，读库发消息，零 LLM |

**run_push.py 核心逻辑**：
```python
# 1. 根据 channel name 查 t_channels + t_push_destinations
# 2. SELECT t_item_channel_routing WHERE channel_id=? AND pushed=false
#    JOIN t_raw_items + t_item_analysis
#    ORDER BY score DESC LIMIT 15
# 3. formatter.format(items, channel)
# 4. sender.send(text, destination)
# 5. UPDATE t_item_channel_routing SET pushed=true, pushed_at=NOW()
# 6. 写 t_task_logs + t_task_stats
```

---

### 4.5 可观测性

每个模块执行时：
1. 开始时 INSERT `t_sub_tasks`（status=running）
2. 过程中 INSERT `t_task_logs`（info/warn/error 级别）
3. 结束时 UPDATE `t_sub_tasks`（status=done/failed）+ INSERT `t_task_stats`
4. 所有阶段完成后 UPDATE `t_tasks`（status=done，finished_at，duration_ms）

---

## 5. 数据流

### 正常流程时序

```
时间线（每小时一个完整周期，推送在 9:30/13:30/17:30）

:00  crawler 启动
     → 创建 t_tasks（task_id=X，status=running）
     → 并行创建 sub_tasks（github/hn/rss 各一条）
     → 爬取数据写入 t_raw_items
     → 更新 sub_tasks status=done，写 t_task_stats

:20  filter 启动
     → 读取本轮 task_id=X 的未分析 raw_items
     → 批量 LLM 评分（20条/批）
     → 写入 t_item_analysis
     → 写 t_task_logs + t_task_stats

:25  router 启动
     → 读取分析完成的 items
     → 按各频道 filter_prompt 计算匹配分
     → 写入 t_item_channel_routing（多播）
     → 写 t_task_stats

:30  push 启动（仅 9/13/17 点）
     → 读取 routing WHERE pushed=false
     → 格式化 → 发企业微信
     → 标记 pushed=true
     → 更新 t_tasks status=done，finished_at

```

### 异常处理

| 阶段 | 异常 | 处理 |
|------|------|------|
| 采集 | 网络超时 | 记录 error_type=timeout，跳过该源，继续其他源 |
| 采集 | 解析失败 | 记录 error_type=parse_error，写 error_detail |
| 分析 | LLM 调用失败 | 重试3次，仍失败则 analyzed_at 置为 NULL，下次补跑 |
| 路由 | DB 写入冲突 | UNIQUE 约束忽略重复，幂等 |
| 推送 | 发送失败 | 记录 error_type=push_error，pushed 保持 false，下次重试 |

---

## 6. 任务追踪体系

### 主任务 vs 子任务关系

```
t_tasks（主任务）
  task_id: uuid-xxxx
  status: done
  started_at: 09:00:00
  finished_at: 09:32:15
  duration_ms: 1935000
  │
  ├── t_sub_tasks（子任务：采集阶段）
  │     sub_task_id: uuid-aaaa
  │     phase: collect
  │     source_id: 1 (techcrunch_rss)
  │     status: done
  │     success: 18, failed: 2
  │
  ├── t_sub_tasks（子任务：采集阶段）
  │     sub_task_id: uuid-bbbb
  │     phase: collect
  │     source_id: 2 (hn_api)
  │     status: done
  │
  ├── t_sub_tasks（子任务：分析阶段）
  │     sub_task_id: uuid-cccc
  │     phase: analyze
  │     status: done
  │     total: 85, success: 83, failed: 2
  │
  ├── t_sub_tasks（子任务：路由阶段）
  │     sub_task_id: uuid-dddd
  │     phase: route
  │     status: done
  │     total: 83, success: 45（超过阈值的条目）
  │
  └── t_sub_tasks（子任务：推送阶段）
        sub_task_id: uuid-eeee
        phase: push
        channel_id: 1 (AI技术动态)
        status: done
        total: 12, success: 12
```

### error_type 枚举值

| 值 | 说明 |
|----|------|
| `timeout` | 网络请求超时 |
| `parse_error` | HTML/JSON/RSS 解析失败 |
| `db_error` | 数据库写入失败 |
| `llm_error` | LLM API 调用失败或返回格式异常 |
| `push_error` | 推送消息失败（CLI 非0退出） |
| `auth_error` | 认证/鉴权失败（403/401） |

---

## 7. 调度规划

### 系统 crontab（纯 Python，不走 LLM）

```bash
# 采集：每小时整点
0  * * * *   /root/data/projects/github.com/datacollector/venv/bin/python3 \
             /root/data/projects/github.com/datacollector/crawler/run.py --job all

# 分析：每小时 :20（采集完成后）
20 * * * *   /root/data/projects/github.com/datacollector/venv/bin/python3 \
             /root/data/projects/github.com/datacollector/filter/run_filter.py

# 路由：每小时 :25（分析完成后）
25 * * * *   /root/data/projects/github.com/datacollector/venv/bin/python3 \
             /root/data/projects/github.com/datacollector/router/run_router.py

# Watchdog：每5分钟守护 Caddy/Flask
*/5 * * * *  /root/data/projects/github.com/caddy-fileserver/watchdog-cron.sh
```

### OpenClaw cron（只读库发消息，< 15s）

| 任务 | 调度 | 说明 |
|------|------|------|
| GitHub Trending 推送 | `30 9,13,17 * * *` | `run_push.py --channel github_trending` |
| AI 技术情报推送 | `32 9,13,17 * * *` | `run_push.py --channel ai_tech` |
| Cron 监控告警 | `35 9,13,17 * * *` | 检查任务状态，失败则告警 |

---

## 8. 目录结构

```
datacollector/
├── crawler/                    # 采集层（现有，小改）
│   ├── github_trending.py
│   ├── hackernews.py
│   ├── rss_feeds.py
│   └── run.py                  # --job github/hn/rss/all，绑定 task_id
│
├── filter/                     # 分析+打标层 ★ 新增
│   ├── __init__.py
│   ├── run_filter.py           # 入口：批量分析未打标条目
│   └── llm_scorer.py           # LLM 批量评分（20条/批）
│
├── router/                     # 路由层 ★ 新增
│   ├── __init__.py
│   └── run_router.py           # 按频道 prompt 多播路由
│
├── push/                       # 推送层（现有，扩展）
│   ├── formatter.py            # 现有，扩展支持频道格式
│   ├── sender.py               # 现有，保持不变
│   └── run_push.py             # ★ 新增：独立推送入口，零 LLM
│
├── db/
│   ├── db.py                   # 连接池（现有）
│   └── schema.sql              # ★ 重写：t_ 前缀 + 完整注释
│
├── config/                     # ★ 新增：配置管理
│   ├── channels.yaml           # 频道定义（可直接编辑，无需改代码）
│   └── sources.yaml            # 数据源定义
│
├── docs/                       # ★ 新增：架构文档
│   ├── architecture.md         # 本文档
│   ├── schema.md               # 数据模型详细说明
│   └── plans/                  # 每次重大改动的设计方案
│       └── 2026-03-18-v3-redesign.md
│
├── api/                        # Flask HTTP API（现有）
│   └── server.py
├── tests/
│   └── smoke_test.py
├── logs/
├── supervisor/
├── .env                        # DB/推送配置
├── requirements.txt
└── start.sh
```

---

## 9. 扩展性设计

### 新增数据源（无需改代码）

1. 在 `t_data_sources` INSERT 一条记录（type + config JSONB）
2. 实现对应 crawler 模块（如 `crawler/weibo.py`）
3. 在 `t_channel_sources` 关联到相关频道
4. 系统 cron 自动识别 `enabled=true` 的新源

### 新增频道（无需改代码）

1. 在 `t_channels` INSERT（name + filter_prompt + min_score）
2. 在 `t_channel_sources` 关联数据源
3. 在 `t_push_destinations` 配置推送目标
4. OpenClaw cron 新增 `run_push.py --channel <name>` 调用

**示例：新增"腾讯股价分析"频道**

```sql
INSERT INTO t_channels (name, filter_prompt, min_score) VALUES (
  '腾讯股价分析',
  '我关注腾讯公司相关的新闻，包括：腾讯业绩/财报、腾讯产品发布、腾讯投资并购、
   腾讯监管政策影响、游戏版号、微信生态、港股/ADR行情。
   不关注：与腾讯无关的其他公司新闻。',
  0.75
);
```

### 新增用户（多用户扩展）

- `t_channels.user_id` 区分不同用户的频道
- `t_push_destinations` 指向各用户自己的推送目标
- 采集层和分析层无需修改（数据共享）

---

## 10. 待实现 Roadmap

### Phase 1：解耦修复（紧急，解决当前超时问题）
- [ ] 新增 `push/run_push.py`（独立推送入口）
- [ ] 修改 OpenClaw cron payload，去除采集步骤
- [ ] 系统 crontab 补充 github 整点采集

### Phase 2：Schema 迁移
- [ ] 重写 `db/schema.sql`（t_ 前缀，task_id 字段）
- [ ] 数据迁移：旧三表数据迁入 `t_raw_items`
- [ ] 修改 crawler 写入逻辑适配新表

### Phase 3：分析层
- [ ] 实现 `filter/llm_scorer.py`（批量评分）
- [ ] 实现 `filter/run_filter.py`（入口+调度）
- [ ] 系统 crontab 新增 :20 分析任务

### Phase 4：路由层
- [ ] 实现 `router/run_router.py`（多播路由）
- [ ] 初始化频道配置（AI技术动态、GitHub Trending）
- [ ] 系统 crontab 新增 :25 路由任务

### Phase 5：可观测性
- [ ] 所有模块集成 task_id 写日志/统计
- [ ] Dashboard 展示任务链路状态

---

*文档维护：每次重大架构变更在 `docs/plans/YYYY-MM-DD-<topic>.md` 记录设计决策*
