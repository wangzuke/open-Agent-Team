# 🤝 open-teams

**真正的多智能体协作（multi-agent orchestration runtime）**


[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square\&logo=python\&logoColor=white)](https://python.org)
[![Anthropic](https://img.shields.io/badge/Anthropic-Claude-D97757?style=flat-square\&logo=anthropic\&logoColor=white)](https://anthropic.com)
[![OpenAI](https://img.shields.io/badge/OpenAI-Compatible-412991?style=flat-square\&logo=openai\&logoColor=white)](https://openai.com)
[![License](https://img.shields.io/badge/License-MIT-22C55E?style=flat-square)](LICENSE)

---

## 这是什么

`open-teams` 是一个面向真实软件开发任务的多智能体协作框架，灵感来自 Claude Code 的 agent-teams 模式。

它让一个 `team-lead` 真正具备**组织团队的能力**：组建团队、拆解任务、派发工作、管理依赖、监控进度，并将整个协作过程落盘到一个**可追踪的运行时工作区**中。

```
you> 帮我做一个带登录和文章管理的 CMS
```

```
team-lead  ▶  分析需求，构建任务图
           ▶  派生队友（architect / coder-backend / coder-frontend / tester 等）
           ▶  并行执行，依赖自动调度
           ▶  汇总结果
```

---

## 核心架构

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

---

## 核心设计

**四个核心原则：**

| 原则               | 说明                                                |
| ---------------- | ------------------------------------------------- |
| 🎯 Leader 编排，不包办 | Leader 负责规划和协调，不直接参与具体实现                          |
| ⚡ Worker 真正并行    | 每个 teammate 运行在独立子进程，拥有独立 prompt / 工具集 / inbox    |
| 📁 文件系统是真相源      | 所有运行态落盘到 `.open_teams/`，作为 single source of truth |
| 🔒 Runtime 是约束层  | Prompt 负责行为引导，Runtime 负责状态管理、工具边界和自动协调            |

---

## 关键机制

**📋 结构化任务派发**
`spawn_agent` 不是简单传递一句 prompt，而是完整的任务描述（briefing）：
`mission` / `task_ids` / `owned_paths` / `deliverables` / `definition_of_done` / `quality_bar`

**🔗 依赖自动解锁**
任务完成后，系统自动检测下游依赖，并向对应 owner 发送 `[TASK READY]`，无需依赖 prompt 层的自觉协作

**📬 Inbox 自动投递**
后台线程负责投递未读消息，并在安全时机注入上下文，使通信更接近事件驱动模型，而非高频轮询

**⏳ Worker 预等待**
Worker 启动后会等待任务真正 ready 再进入 LLM loop，避免无效 token 消耗

**🗜️ 上下文压缩**
内建 token budget 与上下文压缩机制，保证长任务执行过程中上下文不会失控

---

## Agent Teams vs Subagent

`open-teams` 基于 **Agent Teams 架构**，而非传统的 Subagent 模式。

两者核心区别：

* Subagent 更接近工具调用（tool invocation）
* Agent Teams 更接近多角色协作系统（multi-agent orchestration）

简单理解：

* Subagent：一个人使用多个工具
* Agent Teams：一组 agent 分工协作

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
  "base_url": "you-base-url",
  "api_key": "your-api-key",
  "leader_model": "chose_your_model_for_leader",
  "default_model": "chose_your_model_for_team"
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

## 运行时目录

每次运行会在项目目录下生成 `.open_teams/`，这是调试的第一现场，也是系统的**真实状态来源（single source of truth）**：

```
.open_teams/
├── teams/{team_name}/config.json       # 团队信息
├── tasks/{team_name}/task_*.json       # 任务看板
├── inboxes/{team_name}/{agent}.json    # 邮箱
└── logs/{team_name}/{agent}.jsonl      # 活动日志
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

* [AGENT.md](AGENT.md) - 面向开发者以及vibecoding初始化的源码导览
* [Agent_teams_introduction.md](docs\Agent_teams_introduction.md) — Agent-teams 架构设计介绍

---
