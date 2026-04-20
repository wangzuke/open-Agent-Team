# open-teams

一个基于 Claude API 的多智能体协作编程系统。通过自然语言创建团队，由 Team Leader 自动拆解任务、派生队友、分配工作，多个 Agent 独立并行地完成软件开发任务。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                        用户                              │
│                    (自然语言输入)                          │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    Team Leader                           │
│              (主进程, claude-opus-4-6)                    │
│                                                         │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │ 理解需求 │→│ 拆解任务  │→│ 派生队友  │→│ 分配任务 │  │
│  └─────────┘  └──────────┘  └──────────┘  └─────────┘  │
│                      │              │            │       │
│              ┌───────┴──────┐       │      ┌─────┴────┐ │
│              │   任务面板    │       │      │   邮箱   │ │
│              │  (文件系统)   │       │      │ (文件系统)│ │
│              └───────┬──────┘       │      └─────┬────┘ │
└──────────────────────┼──────────────┼────────────┼──────┘
                       │              │            │
          ┌────────────┼──────────────┼────────────┼────────────┐
          │            ▼              ▼            ▼            │
          │  ┌──────────────┐ ┌──────────────┐ ┌────────────┐  │
          │  │  Coder Agent │ │Researcher    │ │Tester Agent│  │
          │  │  (独立进程)   │ │Agent(独立进程)│ │ (独立进程)  │  │
          │  │  sonnet-4-6  │ │ sonnet-4-6   │ │ sonnet-4-6 │  │
          │  └──────────────┘ └──────────────┘ └────────────┘  │
          │                    Teammates                        │
          └─────────────────────────────────────────────────────┘
```

## 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| **Runtime** | `runtime/` | LLM query loop、Agent 上下文管理、消息模型 |
| **Agents** | `agents/` | Agent 定义、多进程派生与生命周期、Leader 编排逻辑 |
| **Coordination** | `coordination/` | 任务面板、邮箱通信、团队配置 |
| **Tools** | `tools/` | 13 个工具：文件读写编辑、代码搜索、Shell、Agent 派生、通信、任务管理 |
| **Prompts** | `prompts/` | 分层提示词系统：基础 + 角色 + 环境 + 协作指令 |
| **Logging** | `logging/` | 按 Agent 独立的 JSONL 活动日志 |

## 安装

```bash
# Python >= 3.10

# 安装依赖
pip install anthropic>=0.40.0 openai>=1.0.0

# 或从项目安装
cd open_teams
pip install -e .
```

**依赖说明：**
- `anthropic` — Anthropic Claude API 客户端
- `openai` — OpenAI 兼容 API 客户端（覆盖 OpenAI、DeepSeek、Qwen、vLLM、Ollama 等）
- 标准库：`multiprocessing`, `subprocess`, `threading`, `json`, `pathlib`, `re`（无需额外安装）

## 配置

open-teams 支持三种配置方式，优先级从高到低：**命令行参数 > 配置文件 > 环境变量 > 默认值**。

### 配置文件（推荐）

生成默认配置文件：

```bash
python -m open_teams.main --init-config
```

这会在 `.open_teams/` 目录下创建 `open_teams.json`：

```json
{
  "provider": "anthropic",
  "api_key": "",
  "base_url": null,
  "leader_model": "claude-opus-4-6",
  "default_model": "claude-sonnet-4-6",
  "max_tokens": 16384,
  "max_turns": 200,
  "max_agent_turns": 50,
  "temperature": 0.0,
  "team_name": "default",
  "project_root": "."
}
```

也可以手动创建配置文件：在项目根目录下创建 `.open_teams/open_teams.json`，按上述格式填写配置即可。
```

**配置字段说明：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | string | `"anthropic"` | LLM 提供商：`"anthropic"` 或 `"openai"` |
| `api_key` | string | `""` | API 密钥 |
| `base_url` | string/null | `null` | API 地址覆盖（用于代理或第三方服务） |
| `leader_model` | string | `"claude-opus-4-6"` | Team Leader 使用的模型 |
| `default_model` | string | `"claude-sonnet-4-6"` | Teammate 默认模型 |
| `max_tokens` | int | `16384` | 单次 API 调用最大 token 数 |
| `max_turns` | int | `200` | Leader 最大对话轮次 |
| `max_agent_turns` | int | `50` | 每个 Teammate 最大对话轮次 |
| `temperature` | float | `0.0` | 采样温度 |
| `team_name` | string | `"default"` | 团队名称 |
| `project_root` | string | `"."` | 项目根目录 |

也可以指定配置文件路径：

```bash
python -m open_teams.main --config /path/to/my_config.json
```

### 环境变量

```bash
# Anthropic
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_BASE_URL="https://your-proxy.example.com"  # 可选

# 或 OpenAI 兼容（自动切换 provider 为 openai）
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.deepseek.com/v1"  # 可选
```

### 命令行参数

```
--config          配置文件路径（默认 .open_teams/open_teams.json）
--init-config     在 .open_teams/ 下生成默认配置文件并退出
--project-root    项目根目录（默认当前目录）
--team-name       团队名称（默认 "default"）
--provider        LLM 提供商：anthropic / openai
--leader-model    Leader 使用的模型
--teammate-model  Teammate 使用的模型
--api-key         API 密钥
--base-url        API 地址覆盖
--max-tokens      单次 API 调用最大 token 数
--max-agent-turns 每个 Agent 最大轮次
--temperature     采样温度
-m, --message     单次执行模式，传入消息后自动退出
```

## 第三方 API 支持

open-teams 支持两大类 LLM 提供商：

### Anthropic（默认）

```json
{
  "provider": "anthropic",
  "api_key": "sk-ant-xxx",
  "leader_model": "claude-opus-4-6",
  "default_model": "claude-sonnet-4-6"
}
```

### OpenAI 兼容

支持任何实现了 OpenAI Chat Completions API 的服务：

**OpenAI:**
```json
{
  "provider": "openai",
  "api_key": "sk-xxx",
  "leader_model": "gpt-4o",
  "default_model": "gpt-4o-mini"
}
```

**DeepSeek:**
```json
{
  "provider": "openai",
  "api_key": "sk-xxx",
  "base_url": "https://api.deepseek.com",
  "leader_model": "deepseek-chat",
  "default_model": "deepseek-chat"
}
```

**本地模型（Ollama / vLLM）：**
```json
{
  "provider": "openai",
  "api_key": "not-needed",
  "base_url": "http://localhost:11434/v1",
  "leader_model": "qwen2.5:72b",
  "default_model": "qwen2.5:32b"
}
```

系统自动处理 Anthropic 和 OpenAI 之间的消息格式、工具调用格式的转换，上层代码无需关心底层差异。

## 使用

### 交互模式

```bash
python -m open_teams.main --project-root /path/to/your/project
```

进入交互式 REPL 后，直接用自然语言描述需求：

```
you> 帮我实现一个 REST API，包含用户注册、登录和个人信息管理功能

team-lead> 我来分析这个需求并组建团队...
           [创建任务面板]
           [派生 coder-backend, coder-auth, tester-api]
           [分配任务并监控进度]
           ...
```

### 内置命令

| 命令 | 说明 |
|------|------|
| `/status` | 查看团队总览（任务统计、Agent 状态） |
| `/tasks` | 查看任务面板 |
| `/agents` | 查看各 Agent 运行状态 |
| `/config` | 查看当前配置 |
| `/quit` | 退出 |

### 单次执行模式

```bash
python -m open_teams.main -m "为项目添加单元测试" --project-root ./my-project
```

## 工作原理

### 1. 用户提交需求

用户用自然语言描述开发任务，Team Leader 接收并分析。

### 2. Leader 规划

Leader 使用工具探索项目结构，将需求拆解为多个子任务，建立任务依赖关系：

```json
{
  "id": "1",
  "subject": "实现用户模型",
  "description": "在 src/models/ 下创建 User 模型...",
  "status": "pending",
  "owner": null,
  "blocks": [],
  "blockedBy": []
}
```

### 3. 派生队友

Leader 根据任务类型派生合适的 Agent，每个 Agent 运行在独立进程中：

- **Coder** — 编写代码实现
- **Researcher** — 探索代码库、分析架构
- **Tester** — 编写测试、验证功能
- **Reviewer** — 代码审查、质量保证

### 4. 并行执行

每个 Teammate 独立运行自己的 LLM query loop：

1. 从任务面板领取分配的任务
2. 标记任务为 `in_progress`
3. 使用工具完成工作（读写文件、搜索代码、执行命令）
4. 标记任务为 `completed`
5. 通过邮箱向 Leader 报告进度

### 5. 协调与通信

```
Coder-A ──邮箱──→ Team Leader ──邮箱──→ Coder-B
                      │
                 任务面板(共享)
                      │
              ┌───────┴───────┐
              ▼               ▼
         task_1.json     task_2.json
```

- **任务面板**：基于文件系统的共享任务板，FileLock 保证并发安全
- **邮箱系统**：每个 Agent 独立的 JSON 收件箱，支持点对点和广播
- **依赖管理**：任务的 `blockedBy` 字段自动追踪，完成时自动解除阻塞

### 6. 结果汇总

所有任务完成后，Leader 读取产出物、运行验证，向用户呈现最终结果。

## 工具集

### Leader 专属工具（13 个）

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容（支持行号范围） |
| `write_file` | 写入文件（自动创建目录） |
| `edit_file` | 精确字符串替换 |
| `glob_search` | 按 glob 模式搜索文件 |
| `grep_search` | 按正则搜索文件内容 |
| `shell` | 执行 Shell 命令 |
| `spawn_agent` | **派生新的 Teammate Agent** |
| `team_create` | **创建团队** |
| `send_message` | 向其他 Agent 发送消息 |
| `task_create` | 创建任务 |
| `task_update` | 更新任务状态/分配 |
| `task_list` | 列出所有任务 |
| `task_get` | 获取任务详情 |

### Teammate 工具（11 个）

与 Leader 相同，但**不包含** `spawn_agent` 和 `team_create`。

## 项目结构

```
open_teams/
├── __init__.py
├── main.py                  # CLI 入口 & REPL
├── config.py                # 全局配置
├── setup.py                 # 包安装配置
├── requirements.txt
│
├── runtime/                 # 核心运行时
│   ├── engine.py            # QueryEngine — LLM 交互循环
│   ├── context.py           # RuntimeContext — 可克隆的 Agent 上下文
│   ├── llm_client.py        # LLM 客户端抽象层（Anthropic + OpenAI）
│   └── models.py            # Message, ToolCall, ToolResult 等数据模型
│
├── agents/                  # Agent 系统
│   ├── definition.py        # AgentDefinition 定义规格
│   ├── manager.py           # AgentManager — 多进程派生与管理
│   └── leader.py            # TeamLeader — 主编排 Agent
│
├── coordination/            # 协调层
│   ├── task_board.py        # TaskBoard — 文件系统任务面板
│   ├── mailbox.py           # Mailbox — 文件系统邮箱通信
│   └── team.py              # TeamManager — 团队配置管理
│
├── tools/                   # 工具集
│   ├── base.py              # Tool 抽象基类 & ToolRegistry
│   ├── file_tools.py        # ReadFile / WriteFile / EditFile
│   ├── search_tools.py      # Glob / Grep
│   ├── shell_tool.py        # Shell 命令执行
│   ├── agent_tool.py        # SpawnAgent（Leader 专属）
│   ├── message_tool.py      # SendMessage 通信
│   ├── task_tools.py        # TaskCreate / Update / List / Get
│   ├── team_tool.py         # TeamCreate
│   └── registry.py          # 工具注册工厂
│
├── prompts/                 # 提示词系统
│   ├── templates.py         # 基础/环境/工具/协作模板
│   ├── roles.py             # Leader & 各角色提示词
│   └── builder.py           # 分层提示词拼装器
│
├── logging/                 # 日志系统
│   └── activity_logger.py   # JSONL 活动日志（线程安全）
│
└── utils/                   # 工具函数
    ├── file_lock.py         # 跨进程文件锁
    └── helpers.py           # ID 生成、时间戳、JSON 读写
```

## 关键设计决策

### 独立进程隔离

每个 Teammate 运行在独立的 `multiprocessing.Process` 中，拥有：
- 独立的 LLM query loop
- 独立的消息历史
- 独立的工具上下文
- 独立的 abort 控制

这保证了 Agent 间不会互相阻塞或干扰。

### 文件系统作为共享状态

任务面板和邮箱都基于文件系统实现，而非内存共享：
- 天然支持多进程并发
- `FileLock` 保证原子操作
- 状态持久化，进程崩溃不丢失
- 调试友好，可直接查看 JSON 文件

### 分层提示词

```
最终 System Prompt = 基础提示词
                   + 角色提示词 (Leader / Coder / Tester / ...)
                   + 环境信息 (路径、平台、日期、工具列表)
                   + 工具使用指南
                   + 协作协议
```

### 工具权限隔离

Leader 拥有 `spawn_agent` 和 `team_create` 等管理工具，Teammate 只能使用开发工具和通信工具，防止 Agent 越权操作。

## 运行时数据

系统运行时在项目根目录下创建 `.open_teams/` 目录。每次运行会自动生成一个以 `<项目名>_<时间戳>` 命名的会话目录，实现不同项目和不同运行之间的隔离：

```
.open_teams/
├── open_teams.json                              # 配置文件（用户手动创建）
├── my-project_20260420_153045/                  # 会话 1
│   ├── teams/{team_name}/config.json            # 团队配置
│   ├── tasks/{team_name}/task_*.json            # 任务文件
│   ├── inboxes/{team_name}/{agent}.json         # Agent 邮箱
│   └── logs/{team_name}/{agent}.jsonl           # 活动日志
├── my-project_20260420_160000/                  # 会话 2
│   ├── teams/...
│   ├── tasks/...
│   ├── inboxes/...
│   └── logs/...
```

## 许可证

MIT
