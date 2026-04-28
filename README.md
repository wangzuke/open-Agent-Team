# 🤝 open-Agent-Team

**真正的多智能体协作（multi-agent orchestration runtime）**


[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square\&logo=python\&logoColor=white)](https://python.org)
[![Anthropic](https://img.shields.io/badge/Anthropic-Claude-D97757?style=flat-square\&logo=anthropic\&logoColor=white)](https://anthropic.com)
[![OpenAI](https://img.shields.io/badge/OpenAI-Compatible-412991?style=flat-square\&logo=openai\&logoColor=white)](https://openai.com)

---

## What's open-Agent-Team

`open-Agent-Team` 是一个基于 Claude Code 的 Agent-Teams 模式所搭建的一个mulit-agent系统。
它让一个 `team-lead` 真正具备**组织团队的能力**：组建团队、拆解任务、派发工作、管理依赖、监控进度，并将整个协作过程落盘到一个**可追踪的运行时工作区**中。

参考文档：* [Agent_teams_introduction.md](docs/Agent_teams_introduction.md) — Agent-teams 架构设计介绍
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

Agent Teams 不是"让 AI 更聪明"，而是构建一个**可协作、可扩展的多智能体工作系统**，解决以下问题：

- 单 Agent 难以处理复杂、多步骤任务
- 上下文窗口有限，容易溢出
- 缺乏任务分工与并行执行能力



### 系统实现

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

### 核心角色

**Lead Agent（协调者）** — 项目经理 + 调度中心

| 职责 | 说明 |
| ---- | ---- |
| 任务拆解（Task Decomposition） | 将用户需求分解为可执行的子任务 |
| 任务分发（Task Assignment） | 根据角色和能力分配工作 |
| 协调执行（Orchestration） | 管理依赖关系，监控执行进度 |
| 结果汇总（Aggregation） | 整合所有 Agent 的输出，交付最终结果 |

**Teammate Agents（执行者）** — 专业分工的工程师

| 特性 | 说明 |
| ---- | ---- |
| 上下文隔离（Context Isolation） | 每个 Agent 拥有独立上下文，避免干扰 |
| 并行执行（Parallel Execution） | 多个 Agent 同时工作，提升效率 |
| 点对点通信（Direct Communication） | Agent 之间可直接通信，无需中转 |

### 执行模型

```
🧠 Plan  →  ⚡ Parallel  →  📊 Merge
```

1. Lead 接收需求，完成任务拆解
2. 多 Agent 并行执行各自子任务
3. Agent 间按需协作与通信
4. Lead 汇总输出，交付给用户


### 通信机制

Agent Teams 是**多向通信网络**，而非单向结构：

```
Agent A  ⇄  Agent B
   ⇅            ⇅
      Lead Agent
```

支持：点对点通信 / 广播消息 / 共享任务状态

### 设计思想

| 思想 | 说明 |
| ---- | ---- |
| **分治（Divide & Conquer）** | 将复杂问题拆解为多个子问题，分别处理 |
| **上下文隔离（Context Isolation）** | 每个 Agent 独立上下文，扩展整体有效上下文容量 |
| **并行优先（Parallel-first）** | 默认并行执行，提升效率，缩短响应时间 |
| **角色专业化（Specialization）** | 不同 Agent 承担不同职责（架构/后端/前端/测试等） |

### 与单 Agent 对比

| 维度 | 单 Agent | Agent Teams |
| ---- | -------- | ----------- |
| 执行方式 | 串行 | 并行 |
| 上下文 | 单一 | 多上下文隔离 |
| 结构 | 中心式 | 分布式 |
| 扩展性 | 较弱 | 强 |
| 适用任务 | 简单/中等 | 复杂系统任务 |

---

## 核心设计

**四个核心原则：**

| 原则               | 说明                                                |
| ---------------- | ------------------------------------------------- |
| 🎯 Leader 编排，不包办 | Leader 负责规划和协调，不直接参与具体实现                          |
| ⚡ Worker 真正并行    | 每个 teammate 运行在独立子进程，拥有独立 prompt / 工具集 / inbox    |
| 📁 文件系统是真相源      | 所有运行态落盘到 `.open_Agent_Team/`，作为 single source of truth |
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

`open_Agent_Team` 基于 Claude Code**Agent Teams 架构**，而非传统的 Subagent 模式。

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

安装后可直接使用命令：

```bash
open_Agent_Team --help
```

### 配置

```bash
python main.py --init-config #创建配置文件

# 或者 python -m main --init-config
```

编辑生成的 `open_Agent_Team.json`：

```json
{
  "provider": "anthropic",
  "api_key": "your-api-key",
  "base_url": "your_base_url",
  "leader_model": "choose_your_model_for_leader",
  "default_model": "choose_your_model_for_team",
  "team_name": "set_your_team_name",
  "project_root": "workspace"
}
```

说明：

- 默认配置文件名是 `open_Agent_Team.json`
- 如果 `project_root` 保持为默认的 `workspace`，运行时会自动创建 `workspace/proj_<timestamp>/`

### 启动

```bash
# 交互模式
python main.py
```
you> 输入你的prompt

```bash
# 单次任务
python main.py -m "帮我实现一个 REST API 服务"
```

如果你想把结果写到指定项目目录，而不是自动创建 `workspace/proj_<timestamp>/`，可以显式传入：

```bash
python main.py --project-root ./my_project
open_Agent_Team --project-root ./my_project
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

每次运行会在项目目录下生成 `.open_Agent_Team/`，这是调试的第一现场，也是系统的真实状态来源：

```
.open_Agent_Team/
├── teams/{team_name}/config.json       # 团队信息
├── tasks/{team_name}/task_*.json       # 任务看板
├── inboxes/{team_name}/{agent}.json    # 邮箱
└── logs/{team_name}/{agent}.jsonl      # 活动日志
```

任务卡住了？Worker 没响应？依赖没解锁？直接看这里。

---

## 仓库结构

```
open_Agent_Team/
├── agents/          # team-lead 与 worker 生命周期管理
├── coordination/    # task board / mailbox / team metadata
├── prompts/         # leader / teammate prompt 组装
├── runtime/         # query engine / context / llm client
├── tools/           # 文件、搜索、shell、git、任务、通信、spawn
├── team_logging/    # JSONL 活动日志
├── utils/           # 文件锁、安全与辅助逻辑
├── docs/            # 架构说明
├── workspace/       # 默认运行输出容器
├── AGENT.md         # 面向开发者的源码导览
├── config.py        # 全局配置
├── main.py          # CLI 入口
├── requirements.txt # 运行依赖
└── setup.py
```

---

## 文档

* [AGENT.md](AGENT.md) - 面向开发者以及vibecoding初始化的源码导览

---
