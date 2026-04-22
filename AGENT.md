# AGENT.md

面向后续开发者的源码导览。目标不是替代 `README.md`，而是帮助你快速建立对 `open_teams` 当前实现的正确心智模型，知道核心链路在哪里、哪些约束是代码层硬实现、哪些只是 prompt 约束。

## 1. 项目定位

`open_teams` 是一个多 Agent 协作编程框架，核心模式是：

1. `team-lead` 在主进程中运行，负责理解需求、拆任务、派生队友、监控进度、处理异常、汇总结果。
2. `coder / researcher / tester / reviewer` 等 teammate 运行在独立子进程中，并行执行任务。
3. 共享状态不走数据库，而是落在项目根目录下的 `.open_teams/` 文件系统目录。
4. Agent 的工作循环本质上是“LLM 回合 + 工具调用 + 文件状态协调”，不是 actor framework，也不是事件总线。

## 2. 先看哪些文件

如果你第一次读代码，建议按这个顺序：

1. [main.py](main.py)
2. [config.py](config.py)
3. [agents/leader.py](agents/leader.py)
4. [agents/manager.py](agents/manager.py)
5. [runtime/engine.py](runtime/engine.py)
6. [runtime/context.py](runtime/context.py)
7. [coordination/task_board.py](coordination/task_board.py)
8. [coordination/mailbox.py](coordination/mailbox.py)
9. [tools/registry.py](tools/registry.py)
10. [prompts/roles.py](prompts/roles.py)

这 10 个文件基本覆盖了主链路。

## 3. 主链路

### 3.1 启动

入口在 [main.py](main.py)。

主流程是：

1. 解析 CLI 参数。
2. 调用 `load_and_init_config()` 生成配置和工作目录。
3. 创建 `TeamLeader`。
4. 进入单次执行模式或 REPL。

几个重要事实：

- 运行时目录固定在 `project_root/.open_teams/`。
- `max_turns` 约束 leader。
- `max_agent_turns` 约束 teammate。
- 默认支持 `anthropic` 和任意 `OpenAI-compatible API`。

### 3.2 Team Leader

[agents/leader.py](agents/leader.py) 负责主进程侧编排。

Leader 初始化时会：

1. 创建 `TeamManager / TaskBoard / Mailbox / AgentManager`。
2. 执行 `_setup_team()`，程序化创建团队配置和 leader inbox。
3. 构建 leader 专属工具集。
4. 构建 leader system prompt。
5. 创建自己的 `RuntimeContext` 和 `QueryEngine`。

一个关键设计点：

- `spawn_agent` 是 LLM 发起的意图。
- 真正起进程的是 `TeamLeader._handle_spawn_request()`。
- 也就是“模型决定要派生谁”，但“代码负责真正派生”。

### 3.3 Worker 子进程

[agents/manager.py](agents/manager.py) 负责 teammate 的多进程生命周期。

每个子进程会：

1. 重建配置、身份、工具和 prompt。
2. 先进入 `_wait_for_actionable_task()` 预等待。
3. 只有拿到可执行任务后才真正进入 `QueryEngine.run_loop()`。

这个“预等待”很重要，因为它避免了 worker 在任务未就绪时空耗 LLM turn。

### 3.4 LLM 循环

核心循环在 [runtime/engine.py](runtime/engine.py)。

单轮大致是：

1. 把当前 `RuntimeContext` 组装成 API 消息。
2. 调用 `LLMClient.create_message()`。
3. 解析文本输出和工具调用。
4. 把 assistant 内容写回上下文。
5. 执行工具并把 `tool_result` 回灌给模型。
6. 直到 `end_turn` 或回合上限。

代码层的几个附加机制也在这里：

- inbox 后台轮询和消息投递。
- token 预算控制。
- 上下文压缩。
- 接近 turn limit 时自动插入系统警告。

## 4. 协作层设计

### 4.1 Task Board

[coordination/task_board.py](coordination/task_board.py) 是任务真相源。

特点：

- 每个任务一个 JSON 文件。
- 支持状态过滤、owner 过滤、依赖解锁。
- `completed` 后会解除其它任务的 `blockedBy`。

下游通知不是轮询发现的，而是由 `task_update(status="completed")` 触发：

- 逻辑在 [tools/task_tools.py](tools/task_tools.py)。
- newly unblocked 的任务会通过 mailbox 自动给 owner 发 `[TASK READY]`。

### 4.2 Mailbox

[coordination/mailbox.py](coordination/mailbox.py) 是基于文件的消息系统。

当前实现要点：

1. mailbox 仍是文件存储，每个 agent 一个 inbox JSON。
2. runtime 代码层后台轮询 unread 消息。
3. 有新消息时，先进入进程内缓冲队列。
4. 在安全时机统一注入给 agent。
5. 注入内容保留完整 `from + summary + content`。
6. 注入后写入正式对话历史，不做“临时注入”。

重要变化：

- `check_inbox` 不再是默认工作路径。
- leader 和 teammate 的默认工具集中都不注册 `check_inbox`。
- inbox 消费由 runtime 自动完成，不再依赖模型自己轮询邮箱。

### 4.3 Team Leader 的等待和恢复

[agents/leader.py](agents/leader.py) 里还有两套重要的 orchestration 逻辑：

1. `wait_for_completion()`
2. `_recover_orphaned_tasks()`

当前行为：

- leader 在等待阶段做代码层监控，而不是反复驱动模型去 `task_list`。
- 如果某个 agent 进程退出，leader 会把它名下仍处于 `in_progress` 的任务改成 `blocked`。
- 长时间没有任务板进展的任务会触发 status check nudge。

## 5. 工具体系

工具注册入口在 [tools/registry.py](tools/registry.py)。

### 5.1 Leader 工具

Leader 默认拥有：

- 文件工具：`read_file`、`write_file`、`edit_file`
- 搜索工具：`glob_search`、`grep_search`
- shell 工具
- git 工具：`git_status`、`git_diff`、`git_log`、`git_commit`
- `spawn_agent`
- `send_message`
- 任务工具：`task_create`、`task_list`、`task_get`、`task_update`

### 5.2 Teammate 工具

Teammate 和 leader 的差异主要是：

- 没有 `spawn_agent`
- 没有 `team_create`
- 默认也没有 `check_inbox`

### 5.3 工具层的实际约束

当前仓库里有一些不是 prompt，而是代码层硬约束的行为：

- shell 受沙箱规则限制，见 [utils/security.py](utils/security.py)。
- `glob_search` / `grep_search` 默认排除 `.open_teams`、`.git`、`node_modules`、`__pycache__` 等高噪声目录。
- `task_list` 有状态指纹去重，任务板无变化时只返回短响应。
- `read_file` 会对重复读取同一片段给出提醒，减少上下文浪费。

## 6. Prompt 体系

prompt 组装在 [prompts/builder.py](prompts/builder.py)。

常见入口：

- `build_leader_prompt()`
- `build_teammate_prompt()`

角色规则主要在：

- [prompts/roles.py](prompts/roles.py)
- [prompts/templates.py](prompts/templates.py)

要注意：

- 这里很多规则只是“行为引导”，不是权限隔离。
- 例如 leader prompt 倾向于要求 leader 做编排而不是亲自写代码，但 leader 在工具层依然有文件和 shell 能力。
- 真正的能力边界主要看 `registry.py`，不是只看 prompt。

## 7. Provider 抽象

[runtime/llm_client.py](runtime/llm_client.py) 屏蔽了不同 API provider 的差异。

当前支持：

- Anthropic Messages API
- OpenAI 兼容 Chat Completions 风格接口

它处理的核心问题有：

- 消息格式转换
- tool schema 转换
- tool call 和 tool result 回写
- 流式输出
- 重试

如果你要接新的 provider，优先从这里入手，而不是直接改 engine。

## 8. 运行时目录

所有运行态产物都在目标项目的 `.open_teams/` 下：

```text
.open_teams/
├── teams/{team_name}/config.json
├── tasks/{team_name}/task_*.json
├── inboxes/{team_name}/{agent_name}.json
└── logs/{team_name}/{agent_name}.jsonl
```

调试时重点看两个地方：

1. `logs/{team}/team-lead.jsonl`
2. `logs/{team}/{worker}.jsonl`

如果要定位任务依赖和卡死问题，再看：

1. `tasks/{team}/task_*.json`
2. `inboxes/{team}/*.json`

## 9. 当前代码库里最容易踩的坑


### 9.1 `.open_teams` 不是业务输入

`.open_teams` 是运行态元数据，不是业务代码。

如果某个改动让 agent 把 `.open_teams` 重新扫进上下文，通常是在制造噪声和额外 token 消耗。


### 9.2 inbox 机制现在是代码层投递，不是工具式拉取

如果后续开发又把 `check_inbox` 放回默认工具集，或者 prompt 重新鼓励手动查信，通常会重新引入无意义的邮箱轮询成本。

## 10. 典型调试路径

### 10.1 leader 不停 `task_list`

先看：

1. [prompts/roles.py](prompts/roles.py) 有没有重新写成轮询导向。
2. [tools/task_tools.py](tools/task_tools.py) 的去重保护是否还在。
3. [agents/leader.py](agents/leader.py) 是否在等待阶段用代码层事件驱动替代了模型轮询。

### 10.2 worker 频繁 `context_compressed`

先看：

1. 是否在重复大范围 `read_file`。
2. 搜索工具是否又把 `.open_teams` 扫进来了。
3. mailbox 是否被错误地重复注入。
4. `context.py` 的压缩摘要是否保留了足够的任务上下文。

### 10.3 任务明明完成了但下游没动

先看：

1. 上游有没有正确调用 `task_update(status="completed")`。
2. task board 里该任务是否真的变成 `completed`。
3. newly-unblocked 任务有没有 owner。
4. mailbox 对应的 `[TASK READY]` 是否写入。
5. worker inbox poller 是否正在运行。

## 11. 扩展建议

如果你要继续增强这个系统，优先考虑这些方向：

1. 更强的 leader stall detection 和自动重规划。
2. 更细粒度的 token / cost 观测。
3. 更可靠的 end-to-end 回放测试，而不是只看单元测试。
4. 更明确的“任务契约 -> 测试断言 -> 产物验证”闭环。

不建议优先做的方向：

1. 单纯堆更多角色类型。
2. 只靠 prompt 提高自主性而不加代码层保护。
3. 在没有回放验证前引入更复杂的消息协议。

## 12. 一句话总结

`open_teams` 当前的正确理解方式是：

这是一个“Leader 编排 + Worker 多进程执行 + 文件系统共享状态 + LLM 工具循环”的多 Agent 框架。稳定性主要取决于 `engine / leader / task_board / mailbox / registry` 这几层是否协同，而不是单纯取决于 prompt 写得多聪明。
