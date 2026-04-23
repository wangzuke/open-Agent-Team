# open-teams 项目文档

## 项目概述

open-teams 是一个基于 Python 的多 Agent 协作编程系统。用户提交任务后，系统自动创建一个由 Team Leader 领导的 Agent 团队，通过任务分解、并行执行、消息通信来协同完成软件工程任务。

支持 Anthropic Claude API 和任意 OpenAI 兼容 API（DeepSeek、Qwen、vLLM 等）。

---

## 系统架构

```
用户输入
   │
   ▼
TeamLeader (主进程, leader_model)
   ├── 理解需求 → 分解任务 → 创建 Task Board
   ├── spawn_agent → AgentManager 启动 worker 子进程
   ├── 监控进度 → 处理阻塞 → 综合结果
   │
   ├── Worker Process: coder-backend (default_model)
   ├── Worker Process: coder-frontend (default_model)
   ├── Worker Process: tester-api (default_model)
   └── Worker Process: researcher-deps (default_model)
```

### 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| **config** | `open_teams/config.py` | 全局配置，配置文件位于 `open_teams/open_teams.json` |
| **leader** | `open_teams/agents/leader.py` | Team Leader，主进程运行，编排团队 |
| **manager** | `open_teams/agents/manager.py` | Agent 生命周期管理，multiprocessing 子进程 |
| **engine** | `open_teams/runtime/engine.py` | LLM 请求循环 + 工具执行 + inbox 轮询 |
| **llm_client** | `open_teams/runtime/llm_client.py` | Anthropic/OpenAI 双 provider 抽象 |
| **task_board** | `open_teams/coordination/task_board.py` | 文件持久化任务板，FileLock 并发安全 |
| **mailbox** | `open_teams/coordination/mailbox.py` | 基于文件的消息系统 |
| **tools/** | `open_teams/tools/` | Agent 可用的工具集（文件/搜索/Shell/消息/任务） |
| **prompts/** | `open_teams/prompts/` | 系统提示词模板和角色定义 |
| **logging** | `open_teams/logging/` | 结构化活动日志（JSONL 格式） |

---

## 运行时数据目录

运行时产物存放在目标项目的 `.open_teams/` 下（无时间戳子目录）：

```
project_root/.open_teams/
├── teams/{team_name}/config.json    # 团队配置和成员列表
├── tasks/{team_name}/task_*.json    # 任务文件（每任务一个 JSON）
├── inboxes/{team_name}/{agent}.json # 各 Agent 的消息收件箱
└── logs/{team_name}/{agent}.jsonl   # 各 Agent 的活动日志
```

---

## 关键流程

### 1. 启动与团队创建

```
main.py → load_and_init_config() → TeamLeader.__init__()
    → _setup_team()         # 程序化创建团队，非 LLM 决策
    → create_leader_tools() # 注册工具集（不含 team_create）
    → QueryEngine           # 初始化 LLM 循环
```

团队创建是纯程序化的（`_setup_team`），不依赖 LLM 决策。Leader 的工具集中不包含 `team_create`，防止 LLM 创建重复团队。

### 2. 任务分配与 Agent 派生

Leader 通过 LLM 决策调用 `spawn_agent` 工具 → `_handle_spawn_request` 钩子拦截 → `AgentManager.spawn_agent` 启动子进程。

每个 Worker 子进程的生命周期：
1. 初始化配置、工具集、系统提示词
2. **预等待**（`_wait_for_actionable_task`）：纯 Python sleep，零 LLM 成本
3. 有可执行任务后进入 `engine.run_loop()` LLM 循环
4. 完成后发送状态报告给 team-lead

### 3. 消息驱动的任务系统

系统采用消息驱动而非轮询驱动的任务发现机制：

- **后台 inbox 轮询线程**：daemon 线程每 1s 轮询 inbox → `queue.Queue` → 主循环每轮 drain 注入为 `[INBOX]` user turn
- **任务就绪通知**：Agent 调用 `task_update(status="completed")` → `update_task_with_deps` 检测新解锁的下游任务 → 自动发送 `[TASK READY]` 消息到 owner 的 inbox
- **预等待**：Worker 进程启动后先阻塞等待可执行任务，不消耗 LLM turns

---

## 历史关键问题与解决方案

### 问题 1：Agent 反复轮询 task_list 浪费大量 turns

**现象**：test_proj_5 中 coder-auth 调用 task_list 超过 30 次。

**根因**：提示词中的工作流指令要求"从 step 1 循环"，而 step 1 包含 task_list 调用。加上缺少推送机制，Agent 只能通过轮询发现任务状态变化。

**解决方案**：
1. 引入消息驱动机制：任务完成时自动通过 `[TASK READY]` 通知下游 Agent
2. 后台 daemon 线程轮询 inbox（1s 间隔），通过 `queue.Queue` 缓冲消息
3. Worker 预等待（`_wait_for_actionable_task`）：任务未就绪时纯 sleep，零 LLM 成本
4. 更新所有提示词：从"轮询 task_list"改为"等待 [TASK READY] 消息"

**涉及文件**：`engine.py`、`task_board.py`、`task_tools.py`、`manager.py`、`roles.py`、`templates.py`、`registry.py`

### 问题 2：重复的团队目录的创建

**现象**：`.open_teams/teams` 下出现两个团队目录。

**根因**：Leader 的工具集中包含 `team_create` 工具，LLM 在规划时额外调用了一次，导致创建了第二个团队。

**解决方案**：从 Leader 工具集中移除 `team_create`。团队创建完全由 `_setup_team()` 程序化完成。

**涉及文件**：`registry.py`

### 问题 3：.open_teams 下多余的时间戳子目录

**现象**：每次运行在 `.open_teams` 下创建 `{team_name}_{timestamp}` 子目录。

**解决方案**：移除 `session_dir` 概念，teams/tasks/logs/inboxes 直接放在 `.open_teams/` 下。

**涉及文件**：`config.py`

---
## 现存待优化问题
### 生命周期结束的控制  
已解决
- main.py：添加 atexit.register(leader.shutdown) 兜底，确保任何退出路径都清理子进程。等待阶段 Ctrl+C 仅中断等待回到 REPL（agent 继续），REPL 输入阶段 Ctrl+C 执行完整 shutdown
- manager.py：terminate_all() 改为并行发 SIGTERM 再统一 join（最坏 3s 而非 N×5s）
### leader安排的并行度不够
### 创建团队成员数量太多，token消耗太大
通过prompt限制
### teammates的自主性可以再提高，可以二次拆解任务（可选）

## Agent 角色类型

| 角色 | 能力 | 典型任务 |
|------|------|----------|
| **leader** | 全部工具 + spawn_agent | 规划、分配、监控、综合 |
| **coder** | 文件读写 + 搜索 + Shell + 消息 + 任务 | 编码实现 |
| **researcher** | 同 coder（不含 spawn_agent） | 代码分析、文档研究 |
| **tester** | 同 coder | 测试编写与验证 |
| **reviewer** | 同 coder | 代码审查、质量保障 |

所有 Teammate 共享同一工具集（不含 `spawn_agent` 和 `team_create`）。区别在于系统提示词中的角色指令不同。

---

## 配置说明

配置文件位于 `open_teams/open_teams.json`：

```json
{
  "provider": "anthropic",
  "api_key": "sk-...",
  "base_url": null,
  "leader_model": "claude-opus-4-6",
  "default_model": "claude-sonnet-4-6",
  "max_tokens": 16384,
  "max_turns": 200,
  "max_agent_turns": 200,
  "temperature": 0.0
}
```

配置优先级：CLI 参数 > 配置文件 > 环境变量 > 默认值

## 使用方式

```bash
# 交互模式
python -m open_teams.main

# 单消息模式
python -m open_teams.main -m "创建一个 Flask REST API 项目"

# 生成默认配置
python -m open_teams.main --init-config

# 指定 OpenAI 兼容 provider
python -m open_teams.main --provider openai --base-url https://api.deepseek.com/v1
```
