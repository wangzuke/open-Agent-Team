# open_teams 架构与技术分享

## 1. 项目简介

`open_teams` 是一套面向软件开发任务的 multi-agent 协作系统。它的核心目标不是简单地“让多个模型同时工作”，而是把一个复杂研发任务拆成可执行、可协调、可恢复的团队工作流：

- `team-lead` 负责理解需求、拆任务、派生队友、处理依赖、监控进度、汇总结果
- `coder / tester / reviewer / architect / researcher` 等 teammate 在独立进程中执行具体工作
- 团队共享状态不依赖数据库，而是落在项目根目录下的 `.open_teams/` 文件系统工作区

这套系统更像一个“轻量级多智能体协作框架”，而不是单纯的 prompt 拼接器。

## 2. 设计目标

`open_teams` 当前的设计重点有 5 个：

1. 把单个大任务拆成多个小任务，并尽可能并行执行。
2. 让任务依赖、任务状态和 agent 通信都可落盘、可追踪、可恢复。
3. 支持真实编码场景，而不是只做分析型对话。
4. 尽量减少无效 token 消耗，例如无意义轮询、重复读取、重复上下文堆积。
5. 对模型“尽量软约束”，对运行时“尽量硬约束”。

## 3. 整体架构

从结构上看，`open_teams` 可以拆成 6 层：

### 3.1 编排层

- 入口：`open_teams/main.py`
- 核心：`open_teams/agents/leader.py`

这一层负责：

- 接收用户需求
- 创建团队
- 规划任务图
- 派生队友
- 等待执行结束
- 做最终 synthesis

`team-lead` 是整个系统的 orchestrator，但它本身不应该成为主要编码者。

### 3.2 执行层

- 入口：`open_teams/agents/manager.py`

每个 teammate 都运行在独立子进程里。这样做的好处是：

- 进程间隔离清晰
- 生命周期容易管理
- agent 崩溃或退出时可以被 leader 感知
- 不需要引入更重的 actor runtime 或消息中间件

### 3.3 运行时层

- `open_teams/runtime/engine.py`
- `open_teams/runtime/context.py`
- `open_teams/runtime/llm_client.py`

这一层负责：

- prompt 拼装后的对话执行
- 工具调用循环
- tool result 回灌
- inbox 自动投递
- token usage 记录
- 上下文压缩和预算控制
- Anthropic / OpenAI-compatible provider 抽象

`QueryEngine` 是最核心的执行引擎。

### 3.4 协调层

- `open_teams/coordination/task_board.py`
- `open_teams/coordination/mailbox.py`
- `open_teams/coordination/team.py`

这是 `open_teams` 区别于普通 agent shell 的关键部分。

系统没有引入数据库，而是采用：

- 任务板：一个任务一个 JSON 文件
- 邮箱：一个 agent 一个 inbox JSON 文件
- 团队信息：文件化持久化

这种实现的优点是简单、透明、好调试、易恢复。

### 3.5 工具层

- `open_teams/tools/`

包括：

- 文件工具：`read_file` / `write_file` / `edit_file`
- 搜索工具：`glob_search` / `grep_search`
- shell 工具
- git 工具
- 任务工具：`task_create` / `task_get` / `task_list` / `task_update`
- agent 工具：`spawn_agent`
- 通信工具：`send_message`

工具层是模型和运行环境之间的桥梁。

### 3.6 Prompt 层

- `open_teams/prompts/roles.py`
- `open_teams/prompts/templates.py`
- `open_teams/prompts/builder.py`

Prompt 层负责：

- leader 行为约束
- teammate 角色约束
- 协作协议
- 环境信息注入
- 当前平台和 shell 约束提示

## 4. 核心机制

## 4.1 Leader + Worker 多进程模型

`team-lead` 在主进程中运行，teammate 在子进程中运行。`spawn_agent` 不是工具本身直接起进程，而是模型先发起工具调用意图，再由 `leader.py` 中的逻辑真正创建子进程。

这个设计的好处是：

- 模型只负责决策
- 运行时负责执行
- 工具权限和生命周期管理都在代码层可控

## 4.2 文件系统任务板

任务板是系统的真相源。

每个任务记录：

- `id`
- `subject`
- `description`
- `status`
- `owner`
- `blockedBy`
- `blocks`
- `agentType`
- `priority`
- 以及结构化的 `goal / deliverables / acceptance / constraints / interfaces / handoff`

当前系统已将任务创建从“简单标题+描述”升级为结构化任务规范，目的是减少 teammate 对任务边界的主观猜测。

## 4.3 文件系统邮箱

系统的消息传递不是直接依赖模型自己轮询 `check_inbox`，而是运行时代码层自动读取 inbox，有新消息时在安全时机投递给 agent。

当前版本的特点：

- 只有有新消息时才注入
- 注入保留完整 `from + summary + content`
- 不再依赖 `check_inbox` 作为默认工作路径

这使得消息机制更接近“事件驱动”，而不是“模型自发轮询”。

## 4.4 Task Ready 驱动依赖解锁

当一个任务完成后，系统会：

1. 更新任务板
2. 移除下游任务的 `blockedBy`
3. 给下游 owner 发送 `[TASK READY]`

这套机制把任务依赖从“隐含关系”变成了显式的可执行信号。

## 4.5 Structured Spawn Brief

`spawn_agent` 已经从简单字段演进为结构化 briefing：

- `mission`
- `task_ids`
- `owned_paths`
- `required_reads`
- `deliverables`
- `definition_of_done`
- `quality_bar`
- `coordination_notes`
- `startup_checklist`

这本质上是把 “leader 也是 prompt engineer” 的思想落实到了运行时协议里。

## 5. 技术路线

## 5.1 Provider 路线

当前支持：

- Anthropic Messages API
- OpenAI-compatible API

通过 `runtime/llm_client.py` 统一抽象底层差异，上层编排逻辑不直接关心 provider。

## 5.2 Coordination 路线

没有选择数据库、Redis 或消息队列，而是优先采用文件系统：

- 简单
- 可直接观察
- 容易调试
- 不引入额外部署依赖

这使得 `open_teams` 非常适合做研究、试验和快速演进。

## 5.3 Prompt + Runtime 双层约束

`open_teams` 的整体思路不是完全相信 prompt，也不是完全硬编码。

当前做法是：

- Prompt 负责行为引导
- Runtime 负责状态管理、工具边界、自动协调和兜底

这是一个相对务实的技术路线。

## 6. 当前已做的关键优化

过去几轮开发里，系统已经完成了不少重要优化。

### 6.1 任务创建与团队创建优化

- Leader 的任务拆解能力增强
- `task_create` 支持结构化任务字段
- `spawn_agent` brief 更细化
- 新增 `architect` 角色
- 强化 contract-first / architect-first 路线

### 6.2 消息机制优化

- 不再依赖 agent 主动轮询邮箱
- 有新消息才注入
- inbox 注入保留完整消息体
- 去掉默认的 `check_inbox` 工作路径

### 6.3 任务 ID 与依赖规范化

- `blockedBy` / `blocks` 统一为纯数字 ID
- `task-1` / `#1` / `Task 1` 等引用会统一归一化
- 避免了 task board 与 tool 调用之间的 ID 漂移

### 6.4 日志体系增强

- 所有 tool call 都会入日志
- 使用本地递增的 `tool_call_id`
- teammate 启动日志会记录其任务职责、交付物、完成标准等信息

### 6.5 上下文与噪声控制

- `.open_teams`、`.git`、`node_modules` 等默认不进入搜索范围
- `task_list` 在无变化时短响应
- 重复读同一文件会被提示

### 6.6 环境感知优化

最近补充了一项很关键的能力：

- 自动检测当前平台与 shell family
- 在 prompt 中注入 `Platform / Shell family / Shell guidance / Shell examples`

这解决的不是“模型知不知道自己在 Windows”，而是“模型能不能据此选对 shell 命令风格”。

## 7. open_teams 的特别之处

相比普通 agent 框架，`open_teams` 有几个很鲜明的特点。

### 7.1 真正有状态的多 agent 协作

它不是把多个 agent 名字写进 prompt 里模拟团队，而是真正有：

- 独立进程
- 独立 inbox
- 独立日志
- 共享任务板

### 7.2 文件系统透明性

所有状态都可以在 `.open_teams/` 下直接看到：

- 谁被创建了
- 任务是什么
- 谁收到过消息
- 哪一步卡住了

这对调试 multi-agent 系统非常重要。

### 7.3 强调编排而不是“全能 leader”

系统的理想模式不是让 leader 自己写代码，而是让 leader 做：

- 任务图规划
- 依赖管理
- 资源调度
- 异常处理

这比单 agent 模式更接近真实团队协作。

### 7.4 强调 contract-first

对于前后端并行开发问题，`open_teams` 已经明确走向：

- 先搭共享契约
- 再并行开发
- 再做集成验证

这是系统可持续演进的重要基础。

## 8. 当前仍存在的问题

虽然系统已经能跑通完整链路，但仍有不少需要继续打磨的点。

### 8.1 任务完成速度仍偏慢

当前慢的原因主要包括：

- leader 启动阶段还会做一些低价值探索
- agent 有时会先试错 shell 命令，再修正为当前平台兼容写法
- 某些任务仍会生成过长文档或过大单轮输出
- leader 在尾部有时仍会过度介入

### 8.2 等待与恢复策略仍需继续完善

目前已经去掉了“阻塞 worker 固定超时自动退出”，这是正确方向。  
但后续还需要继续优化：

- 长依赖链场景下的等待体验
- 进程恢复与重派策略
- leader 对 pending / blocked / orphaned task 的更细粒度管理

### 8.3 synthesis 边界仍需更严

此前系统出现过“任务未全部完成，但 leader 已经开始 synthesis”的问题。  
这一点已做修复，但后续仍应持续审查：

- synthesis 触发条件
- 未完成任务的处理策略
- leader 是否越权代替队友工作

### 8.4 shell 安全边界还不够强

当前 shell 工具仍然偏启发式，更多是“规则限制”，而不是严格沙箱。  
这意味着：

- 平台兼容性问题还会出现
- 命令安全边界还需要继续收紧

### 8.5 leader 的任务规划仍有波动

虽然已经增强了任务和 spawn brief 的结构化，但不同测试任务下，leader 对：

- 并行度
- 编队规模
- 任务颗粒度
- 文档产出范围

的控制还不完全稳定。

## 9. 下一步优化方向

结合当前问题，建议后续重点放在 5 个方向：

1. **继续压缩 leader 的低价值探索行为**  
   让新项目启动更快，更少无效扫描。

2. **进一步收紧 contract-first 产物范围**  
   目标是“足够解锁”，而不是“写完整规格书”。

3. **强化环境执行能力**  
   在 shell、路径、安装、测试等动作上更贴合当前终端环境。

4. **增强 leader 的恢复与重派能力**  
   当 agent 退出、卡住、阻塞时，让 leader 更像真正的项目经理，而不是被动旁观者。

5. **补更系统的回归测试**  
   尤其是对 multi-agent 执行日志、任务板状态和最终产物一致性的测试。

## 10. 总结

`open_teams` 的价值不只是“能让多个 agent 干活”，而是它已经逐步具备了一个协作系统的核心特征：

- 有团队角色
- 有任务图
- 有依赖管理
- 有消息机制
- 有状态持久化
- 有运行时约束
- 有可调试性

它当前仍处于持续快速演进阶段，但整体方向已经比较清晰：

从“多模型同时工作”走向“一个可编排、可恢复、可观测的多智能体研发系统”。

如果从技术分享的角度概括一句话：

**`open_teams` 的重点不是把一个模型拆成很多模型，而是把软件研发流程拆成可以被多智能体真实协作执行的状态机。**
