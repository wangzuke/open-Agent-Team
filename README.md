<div align="center">

# 🤝 open-teams

**真正的多智能体协作运行时**

*不是"一个 prompt 里假装有多个人"，而是真正的多进程并行执行*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Anthropic](https://img.shields.io/badge/Anthropic-Claude-D97757?style=flat-square&logo=anthropic&logoColor=white)](https://anthropic.com)
[![OpenAI](https://img.shields.io/badge/OpenAI-Compatible-412991?style=flat-square&logo=openai&logoColor=white)](https://openai.com)
[![License](https://img.shields.io/badge/License-MIT-22C55E?style=flat-square)](LICENSE)

</div>

---

## 这是什么

`open-teams` 是一个面向真实软件开发任务的多智能体协作框架，灵感来自 Claude Code 的 agent-teams 模式。

它让一个 `team-lead` 真正去**组建团队、拆解任务、派发工作、处理依赖、监控进度**，并把整个协作过程落盘到可追踪的运行时工作区。

```
you> 帮我做一个带登录和文章管理的 CMS
```

```
team-lead  ▶  分析需求，创建任务图
           ▶  派生 architect / coder-backend / coder-frontend / tester
           ▶  并行执行，依赖自动解锁
           ▶  汇总结果
```

---

## 核心设计

```
┌─────────────────────────────────────────────────────────────┐
│                           User                              │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                        Team Leader                          │
│                      (main process)                         │
│                                                             │
│   Understand → Plan → Spawn → Assign → Monitor → Synthesize │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
     ┌──────────────────┐            ┌──────────────────┐
     │    Task Board    │            │     Mailbox      │
     │   (file-based)   │            │   (file-based)   │
     └────────┬─────────┘            └────────┬─────────┘
              │                               │
              └──────────────┬────────────────┘
                             ▼
        ┌────────────────────────────────────────────────┐
        │           Teammates (subprocesses)             │
        │                                                │
        │  architect   coder-backend   coder-frontend    │
        │  tester      reviewer        researcher        │
        └────────────────────────────────────────────────┘
```

**四个核心原则：**

| 原则 | 说明 |
|------|------|
| 🎯 Leader 编排，不包办 | Leader 负责规划和协调，不亲自写实现代码 |
| ⚡ Worker 真正并行 | 每个 teammate 运行在独立子进程，有自己的 prompt / 工具集 / inbox |
| 📁 文件系统是真相源 | 所有运行态落盘到 `.open_teams/`，无需数据库或消息队列 |
| 🔒 Runtime 是约束层 | Prompt 做引导，Runtime 做状态管理、工具边界和自动协调 |

---

## 关键机制

**📋 结构化任务派发**  
`spawn_agent` 不是传一句话，而是完整的 briefing：`mission` / `task_ids` / `owned_paths` / `deliverables` / `definition_of_done` / `quality_bar`

**🔗 依赖自动解锁**  
任务完成 → 系统自动检查下游 → 向对应 owner 发送 `[TASK READY]` → 无需 prompt 自觉

**📬 Inbox 自动投递**  
后台线程轮询 unread 消息，在安全时机注入上下文，接近事件驱动而非高成本轮询

**⏳ Worker 预等待**  
Worker 启动后先等任务真正 ready 再进入 LLM loop，避免无效 token 消耗

**🗜️ 上下文压缩**  
内建 token budget + 上下文压缩，长任务不失控

---

## 快速开始

### 安装

```bash
pip install -e .
```

### 配置

```bash
python -m open_teams.main --init-config
```

编辑生成的 `open_teams.json`：

```json
{
  "provider": "anthropic",
  "api_key": "your-api-key",
  "leader_model": "claude-opus-4-6",
  "default_model": "claude-sonnet-4-6"
}
```

### 启动

```bash
# 交互模式
python -m open_teams.main --project-root ./workspace

# 单次任务
python -m open_teams.main -m "帮我实现一个 REST API 服务" --project-root ./workspace
```

### 内置命令

```
/status   团队总览
/tasks    任务板
/agents   agent 状态
/config   当前配置
/quit     退出
```

---

## Provider 支持

`openai` provider 兼容所有 OpenAI-compatible API：

<table>
<tr>
<td>

**Anthropic**
```json
{
  "provider": "anthropic",
  "api_key": "sk-ant-xxx",
  "leader_model": "claude-opus-4-6",
  "default_model": "claude-sonnet-4-6"
}
```

</td>
<td>

**DeepSeek**
```json
{
  "provider": "openai",
  "api_key": "sk-xxx",
  "base_url": "https://api.deepseek.com",
  "leader_model": "deepseek-chat",
  "default_model": "deepseek-chat"
}
```

</td>
</tr>
<tr>
<td>

**OpenAI**
```json
{
  "provider": "openai",
  "api_key": "sk-xxx",
  "leader_model": "gpt-4o",
  "default_model": "gpt-4o-mini"
}
```

</td>
<td>

**本地模型 (Ollama)**
```json
{
  "provider": "openai",
  "api_key": "not-needed",
  "base_url": "http://localhost:11434/v1",
  "leader_model": "qwen2.5:72b",
  "default_model": "qwen2.5:32b"
}
```

</td>
</tr>
</table>

---

## 运行时目录

每次运行在项目目录下生成 `.open_teams/`，这是调试的第一现场：

```
.open_teams/
├── teams/{team_name}/config.json       # 团队元数据
├── tasks/{team_name}/task_*.json       # 任务板（每任务一个 JSON）
├── inboxes/{team_name}/{agent}.json    # agent 邮箱
└── logs/{team_name}/{agent}.jsonl      # JSONL 活动日志
```

任务卡住了？Worker 没响应？依赖没解锁？直接看这里。

---

## 仓库结构

```
open_teams/
├── agents/          # team-lead 与 worker 生命周期管理
├── coordination/    # task board / mailbox / team metadata
├── prompts/         # leader / teammate prompt 组装
├── runtime/         # query engine / context / llm client
├── tools/           # 文件、搜索、shell、git、任务、通信、spawn
├── utils/           # 文件锁、安全与辅助逻辑
├── logging/         # JSONL 活动日志
├── doc/             # 架构说明
├── AGENT.md         # 面向开发者的源码导览
├── config.py        # 全局配置
├── main.py          # CLI 入口
└── setup.py
```

---

## 文档

- [AGENT.md](AGENT.md) — 面向开发者的源码导览
- [架构与技术分享](doc/open_teams架构与技术分享.md) — 设计决策说明

---

<div align="center">

如果你对 **multi-agent orchestration**、**task coordination**、**agent collaboration protocol** 感兴趣，这个项目值得深入看一遍。

</div>
