---
type: "study"
title: "ZCode 全景研读：Electron、Web 与终端 Agent 的一体化装配"
updated: "2026-09-21"
status: "verified"
tags: ["agent", "electron", "rpc", "protocol", "typescript", "llm", "monorepo", "tui"]
sources: ["https://github.com/JS-banana/ZCode"]
watch: ["ZCode/apps/zcode-cli/packages/core/src", "ZCode/apps/zcode-cli/packages/bootstrap/src", "ZCode/packages/services/src/zcode-agent", "ZCode/packages/shared/src/zcode-protocol-v4", "ZCode/packages/desktop/src/host"]
---

# ZCode 全景研读：一个 AI 编程工作台如何把 Electron、Web 与终端 Agent 装配成一体

ZCode 是 AI 编程工作台，同一仓库交付三种形态：Electron 桌面应用、浏览器工作台、终端 `zcode` 命令（无参数进 TUI，`--web` 起 Web）。三者背后是同一个 Agent 内核：宿主进程按 workspaceKey 惰性 spawn 一个 `zcode app-server --stdio` 子进程，`@zcode/core` 的 AgentRuntime 在 while\(true\) 回合循环里驱动模型流式响应与工具调用，一切 I/O 收敛到 `@zcode/adapters`，业务只依赖 `@zcode/contracts` 声明的端口。协议分两代并在 stdio 上并存：legacy 是 LF 分帧 NDJSON 的四类消息；V4 是 conversation 等 three 主题的 snapshot\+delta 发布订阅，命令经 CommandInbox 按会话串行收口。客户端与宿主之间则用另一套 VS Code 风格的二进制 Channel RPC。安全边界靠权限模式判定 \+ 审批竞速 \+ alwaysAsk 前置，而不是内核沙箱（源码注释明言 sandbox 已撤除）；上下文安全靠两级压缩与明确的取消/超时条件而非轮数硬停止。本研读覆盖启动、主循环、工具、权限、压缩、记忆注入、持久化、子代理与双工作流、协议栈、桌面/Web 拓扑、模型链路、插件技能、命令执行、工程治理与前端渲染共 15 个机制。

## 目录

- [启动与分流](#mechanism-cli-startup)
- [回合主循环](#mechanism-agent-loop)
- [工具管线](#mechanism-tool-pipeline)
- [权限与审批](#mechanism-permission)
- [上下文压缩](#mechanism-context-compact)
- [记忆与注入](#mechanism-memory-reminder)
- [会话持久化](#mechanism-session-state)
- [子代理与工作流](#mechanism-subagent-workflow)
- [协议栈](#mechanism-protocol)
- [桌面与 Web 拓扑](#mechanism-desktop-web)
- [模型链路](#mechanism-model-provider)
- [插件与技能](#mechanism-plugin-skill)
- [命令执行](#mechanism-bash-exec)
- [工程治理](#mechanism-ops)
- [前端渲染](#mechanism-ui-render)

ZCode 要解决的问题是一个 Agent 同时服务三种界面：桌面用户要原生窗口与系统能力，浏览器用户要零安装访问，终端用户要键盘优先的 TUI。仓库的答案是把"界面"与"会话"彻底分离：所有会话事实（消息、工具调用、权限、队列）都活在 Agent CLI 子进程与 SQLite 里，三种前端只是同一份会话流的不同投影。

读这张地图可以任选入口。终端线：`bin/zcode.mjs` 把无参数的调用交给 Agent CLI，CLI 的 `run.ts` 按命令名分发——无参数进 TUI，`-p` 走 headless，`app-server`/`agent-server` 变成协议服务器；`@zcode/bootstrap` 作为组合根装配配置、SQLite、插件、技能与 AgentRuntime。桌面线：Electron Main 用 `utilityProcess.fork` 给每个窗口拉起一个 窗口 Host，Host 装配全部业务服务并经 MessagePort Channel RPC 暴露给 Renderer。Web 线：同一个 Hono 服务器静态托管前端产物，又在 `/ws` 上开 Channel RPC。

三条线最终汇到同一处：services 层的进程管理器按 workspaceKey 惰性 spawn `zcode app-server --stdio`，之后一切交互都走 stdio 上的 ZCode Protocol（legacy）与 ZCode Protocol V4。Agent 子进程内部，`@zcode/core` 的回合主循环每轮先做压缩检查、初始化 MCP、组装工具面，再向模型发起流式请求；模型发出的工具调用经调度器并发执行，结果写回历史后进入下一轮，直到没有工具调用为止。

两条纪律贯穿全仓。第一是 端口-适配器：core 从不 import Node 内建，外部副作用全部收敛在 adapters 的 19 个子适配器里，这让子代理、工作流 actor、cron 轮能复用同一套 runtime。第二是"业务状态不落 UI"：TUI 只保留 useState 镜像，Web/Desktop 的 V4 投影存储是只读的 snapshot\+delta 应用器——权限审批、AskUserQuestion、计划确认都是协议层的 interaction 请求，客户端只做呈现。

### 客户端薄壳

桌面与 Web 前端是同一套 React 应用（@zcode/ui）的薄宿主，只负责传输与平台桥，不持有业务状态。

### 服务与宿主进程

承载业务服务、RPC 通道与 Agent 子进程管理的常驻进程层：桌面窗口 Host、HTTP/WS 服务器、守护进程形态。

### Agent CLI 运行时

apps/zcode-cli 子工作区：从命令入口、组合根装配、AgentRuntime 回合循环到 I/O 适配器的完整内核。

### 协议与基座

跨进程共享的接缝：两代协议 schema、Channel RPC 框架、端口与工具契约、SQLite 持久化、模型 Provider 层。客户端 SDK 与工作流引擎等次要接入面在机制章节展开。

### 在输入框发送一条消息

Web/Desktop 前端把输入 dispatch 为 V4 sendText 命令信封；草稿首发先 createSession 拿 sessionId，命令 ACK accepted 即清空输入框，未确认前只显示乐观 overlay。

### 命令经 Channel RPC 到达宿主

命令在 /ws（或桌面 MessagePort）的 ChannelServer 上调用 zcodeAgentService；desktop-continuous 与 web-remote-replayable 客户端不能互相伪装。

### service 层找到或拉起 Agent 子进程

进程管理器按 workspaceKey 复用或 spawn `zcode app-server --stdio`；POSIX 下 detached 进程组便于整树回收，stdio 三管道分别承载协议帧、stderr 与进程控制。

[ev-spawn](#evidence-ev-spawn)

### V4 网关串行收口命令

ConversationV4Gateway 把 v4/command 交给 CommandInbox：同会话按 admission 顺序串行、幂等去重，随后交宿主 executor 执行。

### AgentRuntime 排队并开回合

runtime 命令队列按 now/next/later 优先级准入；空闲开新 turn，busy 则 steering 排队。executeTurn 建模型、初始化上下文、跑 SessionStart/UserPromptSubmit 钩子后进入主循环。

[ev-turn-loop](#evidence-ev-turn-loop)

### 流式响应与工具批次

模型事件流（text\_delta/tool\_call…）经有序写队列持久化；流式期间只读工具可提前执行。流结束后工具调用按依赖分组并发执行（默认并发 10），结果按预算序列化回灌。

### 事件回流成投影行

会话事件写入 SQLite 并推给订阅者；V4 以 topic 帧按 delivery profile 节流推送 snapshot/delta，UI 投影存储应用后渲染为行模型时间线。

### 回合收尾与自治维护

无工具调用即完成回合并调度后台记忆抽取；Edit/Write 成功后产生文件检查点供 rewind；上下文逼近阈值时下一轮请求前自动 compact。

### 发行入口 bin/zcode.mjs

发行包统一入口：`--web` 拉起 server/entry-http.js，其余调用把 argv\[1\] 指向 agent/zcode.cjs 并动态 import。

[ev-runner-web](#evidence-ev-runner-web)

### @zcode/cli 命令层

main.ts 清洗进程环境并保护 stdout 通道；run.ts 解析全局参数并 switch 命令（TUI/headless/协议服务器/login/plugins 等）。

[ev-cli-main](#evidence-ev-cli-main), [ev-cli-run](#evidence-ev-cli-run)

### @zcode/bootstrap 组合根

createApp 五层合并配置、打开 SQLite、发现插件/技能/子代理 profile，构造 AgentRuntime 并注入全部端口；同时实现 legacy\+V4 协议服务器。

[ev-create-app](#evidence-ev-create-app), [ev-runtime-tools](#evidence-ev-runtime-tools)

### @zcode/core AgentRuntime

Agent 本体：回合状态机、while\(true\) 主循环、流式模型请求、工具调度与执行管线、压缩、记忆、子代理端口。方法以 prototype 装配进类。

[ev-turn-loop](#evidence-ev-turn-loop)

### @zcode/tui 终端界面

React 19 \+ OpenTUI 自定义渲染器的 TUI；业务状态全在 core，TUI 只保留 useState 镜像与临时交互态。

[ev-tui-renderer](#evidence-ev-tui-renderer)

### @zcode/contracts 契约

29 个 \*Port 端口接口、每个工具一份 zod\+JSON schema、SessionEvent 事件类型、配置键常量——core 与 adapters 之间的全部接缝。

### @zcode/adapters 适配器

19 个子适配器：exec/fs/http/mcp/skills/plugins/storage/model（Vercel AI SDK）/browser（CDP）等，是全仓唯一触碰 Node API 的 CLI 层。

### @zcode/shared 协议

两代协议的 zod schema 单一来源：3717 行单文件的 legacy 协议，与只放 schema\+纯函数的 zcode-protocol-v4 目录。

[ev-v4-discipline](#evidence-ev-v4-discipline)

### @zcode/services 业务服务

ServiceDescriptor/channelName 组织的 30\+ 业务服务域；ZCodeAgentProcessManager 按 workspaceKey 管理 Agent 子进程的生老病死。

[ev-spawn](#evidence-ev-spawn)

### @zcode/server HTTP/WS

静态托管 Web 前端 \+ /api \+ 三个 WS 端点（/ws、/ws/host、/ws/remote/:id）；同进程创建全部 services。

[ev-server-ws](#evidence-ev-server-ws)

### @zcode/zcode-server-cli

serve/status/stop/update 子命令：Supervisor（OS 服务/崩溃预算/control socket）\+ Core（强制 loopback 的 HTTP/WS）。

[ev-server-cli](#evidence-ev-server-cli)

### @zcode/rpc Channel RPC

七层 IPC 框架：序列化、分帧传输、ChannelServer/Client（call/listen）、IPCServer 多播、ProxyChannel 自动代理、远程连接。

[ev-rpc-layers](#evidence-ev-rpc-layers)

### Electron Main

窗口生命周期、托盘、自动更新、深链、电源、内嵌浏览器 guest 管理；不承载业务状态，只编排 Host 子进程。

### 窗口 Host（UtilityProcess）

Renderer 与手机都 attachment 到它：本地 services \+ 远程连接注册表；renderer reload 不杀 Host，保会话存活。

[ev-host-diagram](#evidence-ev-host-diagram)

### Electron Renderer

仅 13 个文件：拿到 MessagePort 后 connectViaMessagePort，渲染 @zcode/ui 的同一个 &lt;Root&gt;。

### @zcode/ui 共享 React 应用

全部业务 UI：shadcn 风格原语、业务组件、useServices/usePlatform hooks、Zustand store、V4 会话投影与交互弹窗、i18n。

[ev-projection](#evidence-ev-projection)

### @zcode/web Web 宿主

Vite 应用：connectViaWebSocket 连 /ws（或 ?remote= 远程桥），以 Web 版 IPlatformService 渲染同一个 &lt;Root&gt;。

### SQLite 会话库与工件

默认 ~/.zcode/cli/db/db.sqlite：session/event/message part/项目权限规则/local\_setting；超预算工具输出落为 artifact 文件。

[ev-sqlite-store](#evidence-ev-sqlite-store)

### Provider 层 \+ AI SDK

provider 纯域包（三种 API 类型\+zhipu 鉴权\+模型目录）、provider-node 配置运行时，以及 adapters 里基于 Vercel AI SDK 的 AiSdkModelAdapter。

[ev-model-provider](#evidence-ev-model-provider), [ev-provider-transport](#evidence-ev-provider-transport)

发行入口 bin/zcode.mjs — --web 时 spawn server/entry-http.js 并注入静态目录与鉴权令牌 → @zcode/server HTTP/WS

[ev-runner-web](#evidence-ev-runner-web)

发行入口 bin/zcode.mjs — 其余调用把 argv\[1\] 指向 agent bundle 后动态 import → @zcode/cli 命令层

[ev-runner-web](#evidence-ev-runner-web)

@zcode/cli 命令层 — 无参数默认进 TUI，加载 @zcode/tui 渲染 → @zcode/tui 终端界面

[ev-cli-run](#evidence-ev-cli-run)

@zcode/cli 命令层 — headless/协议模式经 bootstrap 创建 ZCodeApp 或协议服务器 → @zcode/bootstrap 组合根

[ev-cli-run](#evidence-ev-cli-run)

@zcode/bootstrap 组合根 — 构造 AgentRuntime，注入端口并装配内置工具/钩子/执行器 → @zcode/core AgentRuntime

[ev-runtime-tools](#evidence-ev-runtime-tools)

@zcode/tui 终端界面 — 经注入回调（submitPrompt/事件订阅/权限应答）驱动会话 → @zcode/bootstrap 组合根

@zcode/core AgentRuntime — 依赖端口接口、工具 schema 与 SessionEvent 契约 → @zcode/contracts 契约

@zcode/core AgentRuntime — 经端口调用 Node I/O（exec/fs/http/mcp/storage 等） → @zcode/adapters 适配器

@zcode/core AgentRuntime — model.streamText 经 AiSdkModelAdapter 与 AI SDK 发起请求 → Provider 层 \+ AI SDK

[ev-model-stream](#evidence-ev-model-stream)

@zcode/core AgentRuntime — 会话/事件/消息 part/权限规则持久化，工具输出落工件 → SQLite 会话库与工件

[ev-sqlite-store](#evidence-ev-sqlite-store)

Electron Main — utilityProcess.fork 拉起每窗口 Host，转发 ServicePort → 窗口 Host（UtilityProcess）

[ev-host-diagram](#evidence-ev-host-diagram)

Electron Renderer — MessagePort RPC attachment（reload 只重挂端口） → 窗口 Host（UtilityProcess）

[ev-host-diagram](#evidence-ev-host-diagram)

窗口 Host（UtilityProcess） — createLocalServices 装配全部本地服务 → @zcode/services 业务服务

@zcode/services 业务服务 — 按 workspaceKey spawn zcode app-server --stdio（detached 进程组） → @zcode/cli 命令层

[ev-spawn](#evidence-ev-spawn)

@zcode/web Web 宿主 — WebSocket /ws 建立 Channel RPC（web-remote-replayable） → @zcode/server HTTP/WS

[ev-server-ws](#evidence-ev-server-ws)

@zcode/rpc Channel RPC — ProxyChannel.fromService 把服务暴露为 channel → @zcode/services 业务服务

[ev-rpc-layers](#evidence-ev-rpc-layers)

@zcode/server HTTP/WS — 同进程 ServiceCollection 托管，单进程双职责 → @zcode/services 业务服务

[ev-server-ws](#evidence-ev-server-ws)

@zcode/zcode-server-cli — serve 子命令 fork server-core（强制 loopback） → @zcode/server HTTP/WS

[ev-server-cli](#evidence-ev-server-cli)

@zcode/web Web 宿主 — 渲染共享 &lt;Root&gt;，注入 Web 平台桥 → @zcode/ui 共享 React 应用

Electron Renderer — 渲染共享 &lt;Root&gt;，注入桌面平台桥 → @zcode/ui 共享 React 应用

@zcode/bootstrap 组合根 — 协议服务器按 shared schema 校验 legacy\+V4 消息 → @zcode/shared 协议

[ev-v4-discipline](#evidence-ev-v4-discipline)

### Web 会话链路（静态走读）

从浏览器输入到 Agent 子进程再到持久化与协议回流的解释性边序，覆盖发起与装配，不含渲染回流；这是源码走读顺序，不是执行测量。

1. WebSocket /ws 建立 Channel RPC（web-remote-replayable）
2. 同进程 ServiceCollection 托管，单进程双职责
3. 按 workspaceKey spawn zcode app-server --stdio（detached 进程组）
4. 其余调用把 argv\[1\] 指向 agent bundle 后动态 import
5. headless/协议模式经 bootstrap 创建 ZCodeApp 或协议服务器
6. 构造 AgentRuntime，注入端口并装配内置工具/钩子/执行器
7. model.streamText 经 AiSdkModelAdapter 与 AI SDK 发起请求
8. 会话/事件/消息 part/权限规则持久化，工具输出落工件
9. 协议服务器按 shared schema 校验 legacy\+V4 消息

### 终端 TUI 会话链路（静态走读）

发行入口到 CLI 分发、TUI 渲染与内核装配的解释性边序；TUI 与内核之间是注入回调而非进程边界。

1. 其余调用把 argv\[1\] 指向 agent bundle 后动态 import
2. 无参数默认进 TUI，加载 @zcode/tui 渲染
3. 经注入回调（submitPrompt/事件订阅/权限应答）驱动会话
4. headless/协议模式经 bootstrap 创建 ZCodeApp 或协议服务器
5. 构造 AgentRuntime，注入端口并装配内置工具/钩子/执行器
6. 经端口调用 Node I/O（exec/fs/http/mcp/storage 等）
7. 会话/事件/消息 part/权限规则持久化，工具输出落工件

### 桌面端装配

Electron Main 拉起窗口 Host、Renderer attachment、Host 装配服务并 spawn Agent 子进程的解释性边序。

1. utilityProcess.fork 拉起每窗口 Host，转发 ServicePort
2. MessagePort RPC attachment（reload 只重挂端口）
3. createLocalServices 装配全部本地服务
4. 按 workspaceKey spawn zcode app-server --stdio（detached 进程组）

<a id="mechanism-cli-startup"></a>

## zcode 命令的三分流与组合根装配

\`zcode\` 敲下后发生什么？同一个二进制如何变成 TUI、Web 服务和协议服务器三种形态？

分流发生在两层：发行包的 `bin/zcode.mjs`（runner.mjs）只区分 `--web`（拉起 HTTP 服务器进程）与其它（原样进入 Agent CLI）；Agent CLI 内部的 `run.ts` 再按命令名分发——无参数进 TUI、`-p/--target` 走 headless 单轮、`app-server`/`agent-server` 变成 stdio 协议服务器。所有形态最终都经过 bootstrap 的 `createApp` 组合根：五层合并配置、打开 SQLite、发现插件/技能/子代理，再构造注入了全部端口的 AgentRuntime。

### 发行入口按 argv 分流

发行包入口 `bin/zcode.mjs`（源码 scripts/zcode-distribution/runner.mjs）里 `--web` 分支解析 host/port/workspace/token 后 `serve()`——spawn `server/entry-http.js` 并注入 `ZCODE_AGENT_SERVER_COMMAND`、`ZCODE_WEB_STATIC_ROOT` 等环境；非 localhost 监听默认生成随机访问令牌。其余调用把 `process.argv[1]` 重写为 agent bundle 路径后动态 import，保留 TTY 与全部原始参数。

**输出**: --web 得到托管 Web\+Agent 的服务器进程；默认得到 Agent CLI 进程

[ev-runner-web](#evidence-ev-runner-web)

### main.ts 进程边界处理

Agent CLI 的 main.ts 先清洗运行时环境变量，再判断本次调用是否协议模式（首参 `app-server`/`agent-server`）或 TUI 模式；这两种模式下整个 console 被重定向到 stderr——stdout 是严格协议帧通道（协议模式）或 TUI 专属（TUI 模式），绝不允许日志污染。

[ev-cli-main](#evidence-ev-cli-main)

### run.ts 命令分发

`run()` 解析全局参数后分流：`-p/--prompt` 与 `--target` 进入 headless agent（target 包装成 /goal 命令）；switch 命令名处理 help/version/login/logout/doctor/plugins/skills/tui 等，`agent-server|app-server` 进入 bootstrap 的协议服务器；默认命令名即 `tui`——无参数就是 TUI。

[ev-cli-run](#evidence-ev-cli-run)

### 组合根装配一切

bootstrap 的 createApp 按优先级合并配置（内置默认 → 用户 ~/.zcode/cli/config.json → 项目逐级 → 环境变量 → CLI 覆盖），打开 SQLite 会话库，扫描用户/项目/插件三处来源的子代理 profile、插件与技能根，然后把 executionPort/fileSystemPort/skillPort/mcpPort/modelFactory/permissionBroker 等端口一次性注入 AgentRuntime，并装配内置工具、HookRunner 与工具执行器。

[ev-create-app](#evidence-ev-create-app), [ev-runtime-tools](#evidence-ev-runtime-tools)

### 指令源的加载顺序

启动期只做发现与装配；AGENTS.md 等用户指令是懒加载的——ContextBuilder 在构建回合上下文时才经 contextSourcePort 读取（用户级 ~/.zcode/AGENTS.md 优先，其次项目根，上限 100KB）。技能根顺序：config 显式 roots → 用户 ~/.zcode/skills 与 ~/.agents/skills → 项目各级 .zcode/skills 与 .agents/skills → 插件 skillRoots。

### 原理与设计取舍

组合根集中装配、业务不摸 `process.env`：AGENTS.md 明文要求外部 I/O 全部收敛到 adapter，环境差异（桌面/远程、三个操作系统）靠依赖注入而非分支散落。

stdout 通道纪律让同一二进制可以被安全托管：协议模式下 stdout 只有协议帧，宿主才能放心按行解析。

### 适用条件与边界

--web 分流在发行包 runner 而非 CLI 包内，源码态 `pnpm --filter @zcode/cli dev` 没有 Web 形态。

TUI 的加载在 SEA 发行态要先解压 zcode-tui-runtime，开发态直接 import，两条路径不同。

[ev-runner-web](#evidence-ev-runner-web), [ev-cli-main](#evidence-ev-cli-main), [ev-cli-run](#evidence-ev-cli-run), [ev-create-app](#evidence-ev-create-app), [ev-runtime-tools](#evidence-ev-runtime-tools)

<a id="mechanism-agent-loop"></a>

## 一个回合的生命周期：从准入到 TurnComplete

一条用户消息如何变成模型回合？主循环靠什么条件停下来？

输入先经 admitPrompt 准入：空闲开新回合，busy 则作为 Steering（插话） 插话或排队。executeTurn 编排一个回合：创建回合模型、初始化上下文、跑会话钩子、持久化用户消息，然后进入 while\(true\) 主循环——每轮 drain 插话、microcompact、autoCompact，初始化 MCP，组装工具面，重建 provider 消息后发起一次流式模型请求；模型再发工具调用就并发执行并回到循环，没有工具调用就跑 Stop 钩子后完成。主循环没有轮数硬停止：退出条件是模型自然结束、取消、权限拒绝、工具超时或不可恢复错误。

### 命令队列准入

runtime 命令队列的每条命令带 now/next/later 优先级与六种模式（prompt、target-continuation、task-notification、subagent-message 等）；admission 阶段建立的 turn 保留权在执行阶段复用，避免二次竞争。

**状态与归属**: AgentRuntime 持有命令队列与 active turn 状态

[ev-command-queue](#evidence-ev-command-queue)

### executeTurn 回合编排

executeTurnCommand 依次：创建 turnModel → 初始化/重建 Context → SessionStart 钩子 → /compact、/rewind 命令分流 → beginActiveTurn → 确保会话已落库 → 发 TurnStarted 事件 → UserPromptSubmit 钩子 → 持久化用户消息。每个阶段都有 TTFT 埋点。

[ev-turn-orchestrate](#evidence-ev-turn-orchestrate)

### while\(true\) 主循环

每轮循环开头 throwIfTurnAborted 守卫取消；模型步数大于 0 时先 drain 排队的运行时命令（后台子代理结果、工作流结果、插话），随后 microcompact 与 autoCompact（rapid-refill 熔断会直接抛错终止），再初始化 MCP、按 disallowlist 过滤工具面。

abortSignal 已触发: 抛 TurnCancelled，回合以 cancelled 结果收尾而非错误 → finish

连续 rapid-refill 超阈值: 抛 compact 熔断错误终止回合 → finish

[ev-turn-loop](#evidence-ev-turn-loop), [ev-turn-compact-hook](#evidence-ev-turn-compact-hook), [ev-turn-preparation](#evidence-ev-turn-preparation)

### 流式模型请求

runModelTextRequest 做媒体/能力投影后调 `model.streamText`，for await 消费 start/text\_start/text\_delta/reasoning\_\*/tool\_input\_\*/tool\_call/finish/error 事件；每个 delta 经有序写队列（高水位 128 条背压）持久化为 ModelStreaming 事件。工具输入增量按行缓冲，遇换行或 4096 字符兜底 flush。

[ev-model-stream](#evidence-ev-model-stream)

### 工具批次与回灌

流结束后提取工具调用，交给调度器按依赖与只读/并发安全分组并发执行（默认并发 10）；每个结果写回消息历史、必要时产出文件检查点，然后 model step 返回 continue——主循环进入下一轮，把工具结果作为 provider 消息的一部分重新投影。

本步有工具调用: 返回 continue，回到循环开头 → loop

[ev-tools-schedule](#evidence-ev-tools-schedule)

### 结束条件与收尾

模型返回且无工具调用时进入 finishModelStepWithoutToolCalls：先尝试 inline guide 续跑一次，再给 Stop 钩子一次续跑机会，否则 turnMachine.complete 并 break。之后发 TurnComplete、turnNumber 自增、重建投影、调度项目记忆抽取。取消被归一为 TurnComplete\(cancelled\)，部分流快照仍会持久化。

[ev-turn-stop](#evidence-ev-turn-stop)

### 原理与设计取舍

用明确条件承担安全边界：AGENTS.md 写明"核心 agent loop 默认面向可持续运行的复杂任务设计，不用 tool call 次数做硬停止"，资源约束由自动 compact、取消、权限拒绝、超时、输出截断、provider 重试上限分担。源码与之一致：AgentRuntimeConfig.maxTurns 只对子代理 runner 与记忆循环生效，主循环中未见强制点。

流式与持久化同构：每个流式 delta 都先落库再广播，因此崩溃/取消后的部分输出可恢复、可重放。

### 适用条件与边界

断流恢复预算 STREAM\_RECOVERY\_MAX\_RETRIES=10，超过则回合失败；恢复语义（以已提交工具结果为锚重放）未逐行验证。

本机制全部结论来自静态走读，未实际运行回合观察事件序列。

[ev-command-queue](#evidence-ev-command-queue), [ev-turn-orchestrate](#evidence-ev-turn-orchestrate), [ev-turn-loop](#evidence-ev-turn-loop), [ev-turn-compact-hook](#evidence-ev-turn-compact-hook), [ev-turn-preparation](#evidence-ev-turn-preparation), [ev-model-stream](#evidence-ev-model-stream), [ev-tools-schedule](#evidence-ev-tools-schedule), [ev-turn-stop](#evidence-ev-turn-stop)

<a id="mechanism-tool-pipeline"></a>

## 工具契约、调度与单次调用管线

一次工具调用从模型输出到结果回灌要经过哪些关卡？

工具用三层结构定义：contracts 放 inputSchema/outputSchema、权限声明、结果预算与超时策略；core 的 ToolEntry 补 handler、元数据与输入归一化；bootstrap/core 按端口门控注册内置工具（40\+ 个，MCP 工具运行时动态注册为 mcp\_\_&lt;server&gt;\_\_&lt;tool&gt;）。单次调用走固定管线：schema 校验 → 工具语义校验 → resolveInput 归一化 → PreToolUse 钩子 → 权限判定 → 带超时执行 → 输出校验 → 序列化（预算/工件）→ PostToolUse 钩子。

### 三层契约与门控注册

ToolPermissionSpec 声明 riskLevel/sideEffectScope/needsApproval/alwaysAsk；ToolResultBudget 声明 maxInlineBytes/maxModelBytes 与 inline/truncate/artifact 策略；ToolTimeoutPolicy 区分 timed/untimed。initializeRuntimeTooling 按端口与配置门控注册：Skill 需要 skillPort、Agent 需要 subagentPort、submit\_result/escalate 需要 workflow 提交端口、自动化工具在子代理会话不暴露、node\_repl 由官方插件启停推导。

[ev-runtime-tools](#evidence-ev-runtime-tools)

### 依赖感知的并发调度

ToolScheduler 从工具元数据推导依赖（如写文件依赖同文件的读），把无依赖的只读并发安全工具分到同一批并行执行，默认最大并发 10；每批产生 batch\_start/tool\_complete 事件驱动 UI 进度。

[ev-tools-schedule](#evidence-ev-tools-schedule)

### 归一化在钩子之前

resolveInput 把模型给的入参换成"将要发生的执行事实"（如把相对路径解析为绝对路径、展开命令字符串）。源码注释解释了这个位置为何刻意放在钩子之前：此后钩子、权限规则、确认窗载荷与 handler 读的是同一份输入——"策略看得到真正的脚本""确认与执行同字节"。

[ev-tool-normalize](#evidence-ev-tool-normalize)

### 结果预算与工件落盘

serializeOutput 按预算处理结果：未超限原样返回；strategy=artifact 且工件启用时全量写入 artifactStore，模型只收到 &lt;persisted-output&gt; 预览与 persistedPath；否则截断并追加 \[Tool output truncated by resultBudget: …\] 标记。Bash 默认预算 30KB artifact 策略，MCP 工具默认 50KB truncate。

[ev-tool-artifact](#evidence-ev-tool-artifact)

### 流式期间的提前执行

streamingToolCoordinator 在模型还在生成时就可执行满足准入的工具：readOnly 且 concurrentSafe 且非 destructive 且不需要审批且 sideEffectScope=none；流失败时以已提交的工具结果为锚恢复，避免重放副作用。

### 原理与设计取舍

副作用声明先行：readOnly/destructive/concurrentSafe/sideEffectScope 是工具元数据的一部分，调度、权限、流式准入、压缩保护都读声明而非在调用点猜测。

"确认与执行同字节"：归一化后单一输入事实贯穿钩子、权限、确认窗与 handler，消除了"批准的是 A 执行的是 B"这类竞态。

### 适用条件与边界

大结果落盘依赖 artifactStore 的保留策略（session 级），跨会话检索工件的能力未深读。

MCP 工具的权限固定 needsApproval=true，但 plan 模式对非破坏 MCP 工具放行，是权限机制里值得注意的特例。

[ev-runtime-tools](#evidence-ev-runtime-tools), [ev-tools-schedule](#evidence-ev-tools-schedule), [ev-tool-normalize](#evidence-ev-tool-normalize), [ev-tool-artifact](#evidence-ev-tool-artifact)

<a id="mechanism-permission"></a>

## 五种协作模式的判定顺序与审批竞速

plan/build/edit/yolo/auto 模式如何裁决一次工具调用？被拒的请求如何回到模型？

checkPermission 按固定顺序短路：plan 模式转换工具 → requiresUserInteraction 一律 ask → alwaysAsk 工具（连 yolo 都绕不过）→ yolo 直通 → auto 拒绝（保留未实现）→ disallowedTools 与项目 deny 规则 → plan 模式检查（只读放行）→ 项目 allow 规则与预批通道 → 配置 allowedTools → edit 模式 → build 兜底（只读放行、高风险 ask、其余按副作用）。需要用户审批时，客户端确认窗（PermissionBroker）与 PermissionRequest 钩子链并发竞速，先到者生效；deny 以权限错误作为工具结果回灌模型。

### 固定顺序的短路判定

源码顺序即语义优先级：alwaysAsk 的判定在 yolo 直通之前，注释明言"声明 alwaysAsk 的工具必须经过用户确认，不能被权限模式的放行分支绕过"；auto 模式命中即 deny（reserved but not implemented）。规则匹配支持 command/url/file\_path 等主体提取与 prefix:\*/通配。

[ev-perm-order](#evidence-ev-perm-order)

### 审批竞速：确认窗 vs 钩子链

需要审批时先发 PermissionRequested 事件渲染确认窗，然后 racePermissionResponders 让 PermissionBroker 与 PermissionRequest 钩子链并发应答。源码注释记录了这里的一次真实修复：曾经串行 await 钩子链，同步钩子阻塞期间确认窗已渲染但应答 deferred 未注册，用户每次点击都被幂等语义静默丢弃——确认窗永久死亡。修法是并发竞速，钩子链故障只令其退赛而不替用户做拒绝决定。

[ev-perm-race](#evidence-ev-perm-race)

### 拒绝的流转与规则持久化

deny 先发 PermissionDenied 事件，再以权限错误作为 tool result 返回给模型（模型可以换方式重试）；allow 时可携带 permissionUpdates。项目级规则持久化在 SQLite local\_setting 表（namespace=permission）；会话级"总是允许"只存内存随会话消亡。无客户端时默认 DenyPermissionBroker 直接拒绝——fail-closed。

broker 先应答: 用户决定生效（allow once/always/deny）

钩子链先应答: 钩子决定生效；modify 会改写输入并重新校验\+重查权限

### 原理与设计取舍

权限模型以"声明 \+ 顺序短路"实现可预测性：每种模式的放行/拦截范围都能从 checkPermission 的分支顺序直接读出。

审批应答方竞速而非串行，是把"外部审批桥接"与"本地确认窗"两个时序不可控的应答源统一起来的最小方案。

### 适用条件与边界

auto 模式保留未实现，命中即拒绝。

没有内核级沙箱：dangerouslyDisableSandbox 字段全仓无行为消费者，exec adapter 注释直言 sandbox 已撤除——隔离实际由审批与规则承担（详见命令执行机制）。

[ev-perm-order](#evidence-ev-perm-order), [ev-perm-race](#evidence-ev-perm-race)

<a id="mechanism-context-compact"></a>

## 上下文预算与两级压缩

上下文接近模型上限时系统如何自救？压缩保留了什么、丢掉了什么？

token 统计优先锚定 provider 返回的真实 usage，再叠加其后消息的本地估算（中文按 2 倍字符计）。auto compact 阈值 = contextWindow − min\(maxOutputTokens, 21K\) − 13K buffer，默认 200K 窗口即约 166K 触发，连续失败熔断 3 次。压缩分两级：microcompact 无模型调用，只清理旧工具结果（保留最近 5 组，节省不足 256 token 放弃）；全量 compact 用当前会话模型生成结构化摘要，保留 context prefix 与最近一组 assistant 轮，然后整体替换历史并写 CompactBoundary 事件。provider 报上下文超限后还有反应式 compact 兜底。

### token 估算与 provider 锚定

本地估算是字符近似（除数 3，中文双倍），只用于增量；基数优先取最近一次已提交 assistant 的真实 usage（contextUsageTokens ?? inputTokens）。两者合成 tokenCount = providerBase \+ incremental，避免纯估算漂移。

### 阈值公式与输出预留

源码把"输出预留"与"压缩窗口"收敛为同一套常量：有效窗口 = contextWindow − min\(maxOutputTokens, 21K\)；阈值 = 有效窗口 − 13K buffer。注释解释了动机：provider 的窗口是输入输出共享的，自动压缩只能让出输入侧，分母必须先扣掉输出预留；旧 legacy 分支按完整输出预留会过早压缩。

[ev-compact-const](#evidence-ev-compact-const), [ev-compact-formula](#evidence-ev-compact-formula)

### microcompact：无模型清理

阈值更早（约 autoThreshold 的 90% 或减 2000），只清理 Read/Bash/Grep/Glob/WebFetch/WebSearch/Edit/Write/ApplyPatch 的旧工具结果，保留最近 5 组，替换为 \[Old tool result content cleared\]；媒体结果受保护，预计节省不足 256 token 就放弃。copy-on-write 应用到历史。

### 全量 compact：同一模型生成摘要

compactActiveConversation 用会话当前模型（非独立摘要模型）发起无工具约束的摘要请求，prompt 要求输出 &lt;analysis&gt;\+&lt;summary&gt; 九段结构，输出上限 min\(模型上限, 20K\)。保留 context prefix（system 消息\+技能清单等附件）与触发时最近一组 assistant 轮；其余全部进摘要。prompt-too-long 时逐级降级：按 token 差额多保留几组 → 截断最旧轮次，最多重试 3 次。完成后写 CompactBoundary（含前后 token 数）、replaceMessages 并清空已读文件状态。

**状态与归属**: messageHistory 整体替换为 \[prefix, 摘要消息, 保留轮, 后置 reminder\]

[ev-compact-active](#evidence-ev-compact-active)

### 反应式压缩与熔断

若 provider 仍报上下文超限（finishReason 判定），不再抛流异常而是触发反应式 compact 后重试；rapid-refill 熔断（连续 3 次）防止"压缩→立刻填满"死循环。

### 原理与设计取舍

一套常量管两种预算：输出预留同时约束请求 max\_tokens 与压缩阈值，源码注释明言否则"请求预算和压缩窗口会继续按两套常量计算"而漂移。

压缩后历史仍是普通消息序列（prefix\+摘要\+保留轮），对 provider 与持久化完全透明，不需要特殊分支。

### 适用条件与边界

摘要质量依赖同一模型，长会话摘要可能丢失细节；摘要是"文档化意图"层面的描述，实际保真度未运行验证。

microcompact 的空闲 60 分钟触发路径未逐行验证。

[ev-compact-const](#evidence-ev-compact-const), [ev-compact-formula](#evidence-ev-compact-formula), [ev-compact-active](#evidence-ev-compact-active)

<a id="mechanism-memory-reminder"></a>

## 指令、记忆与元信息的上下文注入面

AGENTS.md、自动记忆、技能清单这些"模型看不见文件系统就该知道的事"如何进入上下文？

上下文是预算化的投影：ContextBuilder 把多路来源合成到请求里。AGENTS.md（用户级\+项目级，上限 100KB）与项目记忆索引 MEMORY.md 合成 \# agentsMd meta\_user 块；&lt;system-reminder&gt; 通道分 prefix/persisted/per-request 三类共 30 个来源（todo 提醒、插话包装、附件伪装等），带严格转义防嵌套伪造；技能清单按字母序注入，超预算退化为纯名字列表；Bash 的 shell prelude 还会把 find\(\)/grep\(\) 路由到打包的快速搜索二进制。

### AGENTS.md 与记忆索引合成

\# agentsMd 块以固定声明开头（OVERRIDE any default behavior），下面拼 AGENTS.md 内容；项目记忆另有格式化标题 "Contents of &lt;root&gt;/MEMORY.md \(user's auto-memory, persists across conversations\)"。索引有 200 行/25000 字符的截断阈值。

[ev-agentsmd-memory](#evidence-ev-agentsmd-memory)

### 自动记忆的后台抽取

每轮成功完成后调度后台子代理（同一主模型但压到最低推理档、输出 ≤5K、最多 5 轮）读取最近消息，决定是否把值得长期记住的事实写入 memories/projects/&lt;slug&gt;-&lt;hash&gt;/memory/。写权限收敛：只允许 Write/Edit 目录内 .md、只读 Bash \+ 目录内 rm；敏感路径段黑名单；写入时补 metadata.originSessionId。无价值时只输出 Nothing to save.

### system-reminder 三类通道

prefix 类（context\_prefix、skills\_listing）进请求前缀；persisted 类（todo\_reminder、goal\_state\_change、rewind\_notice 等 15 种）持久进历史；per-request 类（runtime\_mode、incoming\_message、hook\_context 等 10 种）每轮重注入。wrapSystemReminderForSource 统一包裹并防伪造；哪些来源可被 provider 移到 system 侧由 MCS 集合决定。

[ev-sysreminder](#evidence-ev-sysreminder)

### 技能清单注入与 Skill 工具

技能发现按根优先级扫描 SKILL.md（插件范围不跟随符号链接防逃逸），清单以 "The following skills are available for use with the Skill tool:" 开头逐条列名称\+描述\+路径；总长超预算（20000 字符）就退化为纯名字列表。模型需要内容时调 Skill 工具，loadSkill 上限 100KB，输出包裹 &lt;skill\_content&gt; 并把 $ZCODE\_SKILL\_DIR 替换为技能目录。

[ev-skills-listing](#evidence-ev-skills-listing)

### 嵌入式搜索替换 find/grep

CLI 打包 bfs/ugrep/ripgrep 二进制，通过 Bash shell prelude 注入函数：find\(\) 映射到 bfs -S dfs，grep\(\) 映射到 ugrep -G --ignore-files…，rg 不存在时定义 fallback；ugrep 的 -z/-Z 语义不同会绕回系统 grep。启用后独立的 Glob/Grep 工具注销并从模型可见工具表过滤。

[ev-embedded-search](#evidence-ev-embedded-search)

### 原理与设计取舍

每类注入都有字符预算与退化路径（截断/纯名字列表），元信息永远不会挤占工作上下文。

写入路径与读取路径同样收敛：自动记忆只能写指定目录的 .md，敏感路径黑名单在路径解析层拦截。

### 适用条件与边界

记忆抽取的判定质量取决于模型本身；抽取 prompt 只看最近若干条消息。

system-reminder 伪造成防护依赖转义与 MCS 集合，属于纵深防御而非硬隔离。

[ev-agentsmd-memory](#evidence-ev-agentsmd-memory), [ev-sysreminder](#evidence-ev-sysreminder), [ev-skills-listing](#evidence-ev-skills-listing), [ev-embedded-search](#evidence-ev-embedded-search), [ev-memory](#evidence-ev-memory)

<a id="mechanism-session-state"></a>

## 事件溯源、投影与文件检查点

会话、消息与文件改动如何持久化？fork/rewind 为什么是可能的？

一切会话状态变更先写 SQLite 事件存储，投影（UI 可见的消息序列）随时可以由 getEvents\+reduce 重建。会话首轮 ensureSessionPersisted 落库（projectID 由目录派生、标题取首条输入）；用户/助手消息与 part 级更新经原子事务持久化；Edit/Write 成功后把 originalFile\+structuredPatch 存为 Workspace 工件并发 CheckCreated 事件，rewind 按消息或 checkpointId 定位恢复。

### 会话落库

AgentRuntime 构造时不落库；首轮 ensureSessionPersisted 才写 session 行（id、projectID、workspaceID、directory、title=首条输入、permission mode 等），并同步持久化模型选型、shell 快照与执行状态 entry，补发 SessionTitleUpdated 事件。

[ev-session-create](#evidence-ev-session-create)

### 事件先于一切

事件存储 append 先落库再通知 sink；流式/进度类高频事件按 100 条聚合写摘要日志。投影 rebuild = eventStore.getEvents \+ eventReducer.reduce，因此 compact 替换历史、fork 派生、UI 重放都是同一套事件语义。

[ev-event-store](#evidence-ev-event-store)

### 消息与 part 的原子持久化

persistUserPrompt 把排队晋升做成原子事务；persistAssistantMessage 与 persistPart 覆盖流式过程中的 assistant 消息与每种 part（14 种 union）。SQLite 实现在 adapters/storage/session-store（迁移\+仓储分层）。

[ev-sqlite-store](#evidence-ev-sqlite-store)

### 文件检查点与 rewind

每次成功的 Edit/Write 后，runtime 从工具输出提取 filePath/structuredPatch/originalFile 序列化为 WorkspaceCheckpointArtifact 写入 artifactStore 并发 CheckpointCreated 事件。rewind 入口按 checkpointId 或消息位置选择检查点恢复文件内容；不可恢复时返回明确的原因文案。

[ev-rewind](#evidence-ev-rewind)

### 原理与设计取舍

事件溯源让 fork、compact、rewind、UI 重放共享同一份事实来源，避免多写路径——AGENTS.md 明令"避免重复状态和多条写入路径"。

检查点记录的是 patch\+原始内容而非整库快照，存储成本随改动量而非项目大小增长。

### 适用条件与边界

SQLite migrations 的具体表结构未逐一核对。

session-fork（1487 行）与 rewind 执行入口未逐行阅读，结论来自事件流与辅助函数证据。

[ev-session-create](#evidence-ev-session-create), [ev-event-store](#evidence-ev-event-store), [ev-sqlite-store](#evidence-ev-sqlite-store), [ev-rewind](#evidence-ev-rewind)

<a id="mechanism-subagent-workflow"></a>

## 子代理派发与双工作流体系

主代理如何派发子代理？TS 脚本工作流如何跑在沙箱里？

Agent（别名 Task）工具经 subagentPort.launch 派发子代理：每个子代理是全新的 child AgentRuntime \+ 独立 sessionId，只继承父的指令快照与灰度门，不继承 Project Context；前台运行可被用户转后台或超时自动转后台，后台完成经 runtime 命令队列以合成消息回流父会话。动态工作流（dwf）是同一思想的编排化：CreateWorkflow 对 TS 脚本做类型检查后，把脚本放进受控 node 子进程（vm 裸 context）运行，脚本的 agent.ask 调用经 NDJSON 桥接回宿主 WorkflowEngine，每个 actor 同样是一个持久 child runtime。旧 Workflow（/expert 斜杠命令、声明式 JSON）与 dwf 并存。

### Agent/Task 工具与端口

Agent 工具 handler 调 context.subagentPort.launch，声明 concurrentSafe=true 并在描述里鼓励同一条消息发多个调用实现并发；Task 是 Claude Code 兼容别名（providerVisible:false）。缺省 subagent\_type 为 general-purpose。

[ev-agent-tool](#evidence-ev-agent-tool)

### child runtime 的隔离边界

子代理拿到 new AgentRuntime\(childSessionId, …\)：模型选型克隆自父回合快照、流式配置继承、指令只复用父已解析的快照（"Project Context 仍不继承"）、maxTurns 默认 4、taskType=subagent\_child、动态工作流灰度门结构性继承防止用子代理绕过灰度。

[ev-subagent-child](#evidence-ev-subagent-child), [ev-subagent-isolation](#evidence-ev-subagent-isolation)

### 前台/后台与结果回传

前台 run 用 Promise.race 在完成/用户转后台/autoBackground 超时之间裁决，超时自动转后台返回 async\_launched；后台完成把结果写入 runtime task registry，经后台通知管线（priority=next）排空时合成一条 model-only 消息进入父会话下一轮。父只看到子的最终文本与少量镜像工具事件，raw 子正文不污染父时间线。SendMessage 可对运行中的子代理 steering，对已终态的子代理后台 resume。

[ev-sub-lifecycle](#evidence-ev-sub-lifecycle)

### dwf：脚本沙箱 \+ 宿主引擎

CreateWorkflow 把脚本针对内嵌 facade d.ts 类型检查 → 用户确认 → lowering 成仅依赖 \_\_host 的 async 函数体 → 写入 &lt;cwd&gt;/.zcode/workflow-runs/&lt;runId&gt;.mjs → spawn\(node, 256MB 堆上限\) 独立子进程。子进程里 vm.createContext 裸 context 无 process/require/fetch、禁动态 import；脚本的 \_\_host.ask/enterPhase/report 等调用经 NDJSON 桥接到父进程 WorkflowEngine，引擎再驱动各 actor 的持久 child runtime。abort 是唯一"真取消"，initiator 区分 model/user/interrupted/superseded。

脚本抛错/子进程崩溃/墙钟超时/JSON 解析失败: engine.fail，结算 failed → dwf-engine

abort 信号: engine.stop\(initiator\)，结算 stopped → dwf-engine

[ev-dwf-harness](#evidence-ev-dwf-harness)

### legacy Workflow 与并发

旧 Workflow 是声明式 JSON 定义 \+ DAG 调度器（maxConcurrentLoops 默认 2），触发面是 /expert、/workflow 斜杠命令；runtime-task registry 刻意区分 local\_workflow 与 local\_dynamic\_workflow（前者不可取消）。子代理并发没有全局信号量，实际受单回合工具调度并发（10）约束。

### 原理与设计取舍

一切编排单元都是会话：子代理、dwf actor、workflow child、cron 轮复用同一 AgentRuntime/事件/权限设施，新增编排形态不需要新的执行内核。

沙箱分层：脚本沙箱（vm 裸 context\+一次性进程）防的是意外与资源滥用；安全边界仍由权限审批承担——源码注释明言"vm 不是安全沙箱，隔离仍由上层权限与审批 gate 承担"。

### 适用条件与边界

子代理结果回传的打分与截断细节未逐行验证。

引擎 AIMD 并发（429 降 25%、连续成功\+1）的参数只在注释与实现中确认，未实测。

[ev-agent-tool](#evidence-ev-agent-tool), [ev-subagent-child](#evidence-ev-subagent-child), [ev-subagent-isolation](#evidence-ev-subagent-isolation), [ev-sub-lifecycle](#evidence-ev-sub-lifecycle), [ev-dwf-harness](#evidence-ev-dwf-harness)

<a id="mechanism-protocol"></a>

## 两代协议、命令收口与 Channel RPC

宿主与 Agent 子进程、客户端与宿主之间分别说什么协议？命令如何被串行收口？

仓库有三层协议：Agent 子进程与宿主之间的 stdio NDJSON（legacy ZCode Protocol，四类消息\+zod 校验）；其上的 V4——conversation/sessions-index/workspace-config 三个主题的 snapshot\+delta 发布订阅，物理帧 &gt;1MiB 分片\+crc32，订阅复用同一条 NDJSON 管道；客户端（浏览器/桌面 Renderer）与 server/Host 之间则是另一套 VS Code 风格的二进制 Channel RPC（call/listen \+ ProxyChannel 自动代理）。V4 命令经 CommandInbox 按会话串行 admission 并幂等去重。

### legacy：stdio 上的 NDJSON

发送即 JSON.stringify \+ \\n；接收按行切分、逐行 zod 校验后分发，解析错误回 JSON-RPC 风格的 -32700/-32600。方法全集覆盖 session/workspace/provider/plugins/workflows/automation/offPeak/mcp 等约 60 个方法与反向请求（requestPermission/requestUserInput 等）。

[ev-ndjson](#evidence-ev-ndjson)

### V4：主题化 snapshot\+delta

V4 把会话状态建模为三个主题，订阅先回 ACK，initial snapshot 与后续 delta 走 owned notification v4/conversation/frame。wire 层区分 complete 与 fragment 帧（&gt;1MiB 逻辑帧按 UTF-8 字节分片，crc32\+base64，分片上限 1024）。包纪律严格："只放 schema 类型 \+ 纯函数，禁止任何运行时/IO/传输逻辑"；wire 版本 3 且标注未冻结。

[ev-v4-discipline](#evidence-ev-v4-discipline), [ev-v4-transport](#evidence-ev-v4-transport)

### 两种 delivery profile

continuous（桌面）：30ms flush 窗口、流式文本全开、256KiB 流式输出上限、不推工具进度；replayable（Web/手机）：150ms、只推文本行与工具进度、无流式正文。客户端代码禁止出现 profile 变量——profile 只存在于 CLI flush 管线的参数表。

[ev-v4-profiles](#evidence-ev-v4-profiles)

### CommandInbox 串行 admission

V4 网关的命令收口保证：同一会话不同 commandId 按 CLI 实际 admission 顺序串行（key gate → per-session admission gate 的固定锁序，session gate 持有到 settle 才释放）；幂等表 in-flight/live 永久 pinned、settled 进每会话 512 条 LRU；重复命令共享同一 final promise，duplicate ACK 不覆盖 failed 终态。

[ev-inbox](#evidence-ev-inbox)

### 客户端侧的 Channel RPC

rpc 包七层：VQL 序列化、ISocket 抽象 \+ 13 字节帧头分帧、PersistentProtocol（ACK/keepAlive/断线重放）、ChannelServer/Client 的 call/listen、IPCServer 1:N、ProxyChannel fromService/toService 自动代理、远程连接。服务端把 ServiceCollection 里的服务逐个暴露为 channel，客户端 RemoteServiceAccess 用 ES6 Proxy 生成类型安全访问器。

[ev-rpc-layers](#evidence-ev-rpc-layers)

### 原理与设计取舍

schema 与运行时严格分层：协议包只有 zod schema 与纯函数，通道缓冲、订阅注册表、调度都在 bootstrap 网关——协议演进不拖累传输实现。

legacy 与 V4 并存同管道，文件头注释直言 legacy 栈删除时"本文件整体删除"——旧协议进入维护态，新能力只进 V4。

### 适用条件与边界

V4 wire v3 自述"未冻结，schema 定型以黄金测试为准"。

v4-gateway（约 3400 行）的内部调度细节只读了头部与关键路径，未逐行核对。

[ev-ndjson](#evidence-ev-ndjson), [ev-v4-discipline](#evidence-ev-v4-discipline), [ev-v4-transport](#evidence-ev-v4-transport), [ev-v4-profiles](#evidence-ev-v4-profiles), [ev-inbox](#evidence-ev-inbox), [ev-rpc-layers](#evidence-ev-rpc-layers)

<a id="mechanism-desktop-web"></a>

## 三进程桌面、单进程 Web 与远程 workspace

桌面三进程如何分工？Web 形态如何做到单进程托管页面与 Agent？远程 workspace 如何接入？

桌面是四类进程：Electron Main（窗口与原生能力）、每窗口一个的 Host UtilityProcess（全部业务服务\+远程连接注册表）、Renderer（薄壳）、以及 cron/off-peak 调度进程。Agent CLI 子进程由 services 层按 workspaceKey 惰性 spawn，崩溃或超时被回收后下次调用重建（惰性换新而非自动 respawn）。Web 形态是一个 Hono 进程：静态托管前端 \+ /api \+ 三个 WS 端点；/ws 永远是 terminal-client，受信桌面 Host 才能凭一次性 capability ticket 走 /ws/host。远程 workspace 经 SSH/WSL/Docker 三后端部署远端 server 并做 hello 握手，浏览器经 /ws/remote/:id 只桥接四个文件面服务。

### 每窗口一个 Host

Host 入口文件头画出了拓扑：同一窗口的 Renderer 和手机都 attachment 到 Host，Host 内是 local services \+ remote connection registry。Main 用 utilityProcess.fork 创建 Host 并只发一次 init-local；ServicePort 经 MessageChannel 转交 preload 再 transfer 进 renderer。Host/CLI 生命周期属于窗口而非 renderer 加载周期——reload 只给存活 Host 补挂新端口，否则运行中的 CLI 会话直接消失。

[ev-host-diagram](#evidence-ev-host-diagram)

### Agent 子进程的生老病死

spawn 命令解析优先级：ZCODE\_AGENT\_SERVER\_COMMAND env → monorepo dev（node zcode.cjs app-server --stdio）→ 桌面打包态（Electron 内置 Node，ELECTRON\_RUN\_AS\_NODE=1，体积 ~180MB→~16MB）→ 已部署原生 binary。POSIX 下 detached 独立进程组便于整树回收；崩溃（stdio close）与请求超时（视为链路不可信）都把 workspace 从进程池删除，等下次 getClient 重建；空闲回收、代际（generation）防旧启动请求复活。

[ev-spawn](#evidence-ev-spawn)

### 单进程双职责与鉴权

entry-http 里 createLocalServices 创建全部服务，createHttpServer 同时静态托管 Web 产物（SPA fallback\+目录穿越防护）并暴露 /api 与 WS。token 中间件只保护 /ws 与 /api/\*；/ws 永远按 web-remote-replayable 建连（注释：浏览器设置旧 mode header 也不能把自己提升为 trusted host）；/ws/host 需要一次性 30 秒 TTL 的 capability ticket。独立守护形态 zcode-server-cli 的 Core 更保守：强制 loopback，非 loopback 直接抛错 fail-closed。

[ev-server-ws](#evidence-ev-server-ws), [ev-server-cli](#evidence-ev-server-cli)

### 远程 workspace：detect→deploy→exec→hello

IRemoteBackend 三实现（ssh2/wsl/docker）；连接流程：detect 远端平台架构 → deployServer 按需上传 Node runtime、server bundle、node-pty、Agent runtime（组件级 CDN 缓存）→ exec 启动远端 entry-stdio 并注入 desktop-attached-remote 身份 → 逐行读 stdout 直到 zcode-hello JSON 握手（跳过 SSH banner）→ stdio 包装成 ChannelClient。浏览器经 /api/connect-remote \+ /ws/remote/:id 桥接，一个 id 只允许一个 WS 客户端，只注册 file/git/system/terminal 四个服务。

[ev-remote](#evidence-ev-remote)

### desktop-continuous vs web-remote-replayable

两种 clientMode 决定 V4 hello 的 delivery profile 与能力位（nativeDialogs/localTerminal 仅 continuous）。可信载体用 \_\_zcodeTrustedV4Connection 私有字段覆盖 UI 入参，Renderer/手机不能伪造 clientMode。AGENTS.md 明令：修改 stream/snapshot/queue/重连时必须同时验证两种语义。

### 原理与设计取舍

会话属于宿主而非窗口加载周期：Renderer 可死可重载，Host 与 Agent 子进程不受影响——这是桌面端把 services 从 Main 拆到 Host 的根本理由。

Main 与外部 relay 只做鉴权、配对、转发与 attachment 调度，不保存任务队列/快照等业务状态（AGENTS.md 契约）。

### 适用条件与边界

/ws/host 在仓库内未检索到直接消费方，"Desktop Host 附加入口"是推断；手机桥接入口 attachRemoteWorkspaceSessionHost 在 desktop 内也无活跃调用方。

Host 自身意外退出没有自动 respawn（exit 回调只做清理），恢复依赖用户刷新窗口重挂。

[ev-host-diagram](#evidence-ev-host-diagram), [ev-spawn](#evidence-ev-spawn), [ev-server-ws](#evidence-ev-server-ws), [ev-server-cli](#evidence-ev-server-cli), [ev-remote](#evidence-ev-remote)

<a id="mechanism-model-provider"></a>

## 从模型目录到 HTTP 请求

provider/模型配置如何变成一次带正确鉴权与参数的 HTTP 请求？

provider 纯域包定义三种 API 类型（anthropic-messages/openai-chat-completions/openai-responses）与两类鉴权（api-key、zhipu 账号族），模型目录带 contextWindow、输入格式与 optionSpecs（reasoningLevel/maxOutputTokens）。运行时由 adapters 的 AiSdkModelAdapter（基于 Vercel AI SDK）执行：bindModel 冻结 Provider 事实，请求经过一条 fetch 洋葱——Coding Plan 网关改写 → 代理感知 fetch（httpProxy/caCert）→ 业务错误归一 → option map body 改写（非文本 body 一律 fail-closed）→ SDK compat fetch。

### Provider 配置与模型目录

provider-data-schema 用 zod 定义 API 类型三选一、分组（standard-personal/zai-family/bigmodel-family）、鉴权判别联合（含 zhipu-account 的 start/individual/team/off-peak 模式）；模型 properties 含 contextWindow、inputFormat（image/video/audio/pdf）、supportsToolCall 等。内置目录可从官方 CDN 下载并物化缓存（20s 总预算）。

### AiSdkModelAdapter 与 Model 契约

core 只依赖 contracts 的 Model 接口（generateText/streamText 返回 AsyncIterable&lt;ModelEvent&gt;）；bootstrap 注入 createModelAdapter 薄包装 AiSdkModelAdapter。AI SDK 的 TextStreamPart 经 toModelStreamEvent 归一化为内部事件（text-delta/reasoning/tool-call）。

[ev-model-stream](#evidence-ev-model-stream)

### fetch 洋葱与 provider 工厂

API 类型映射到 SDK 工厂：anthropic→createAnthropic（带 compat fetch）、openai-responses→createOpenAI、openai-compatible→createOpenAICompatible（includeUsage:true、supportsStructuredOutputs 按 binding 能力）。传输层按 provider 缓存：官方 Coding Plan 端点先替换为平台网关端点，再进入用户 HTTP 代理 fetch——httpProxy/noProxy 按实际发送地址判定。

[ev-model-provider](#evidence-ev-model-provider), [ev-provider-transport](#evidence-ev-provider-transport)

### Option Map DSL 改写请求体

reasoningLevel/maxOutputTokens 是仅有的两个模型 Option，由受限 CEL DSL 编译（tokenizer/parser/evaluator \+ 三级缓存），按 option 顺序对 JSON body 做 RFC-7386 Merge Patch；改写发生在自定义 fetch 里，注释明言 Option Map 是这两项的唯一请求字段权威，非文本 Body 必须 fail-closed。

[ev-option-map](#evidence-ev-option-map)

### 对 AI SDK 的补丁

根 package.json 的 patchedDependencies 给 @ai-sdk/anthropic 与 @ai-sdk/openai-compatible 打补丁：在消息转换中新增 video/\* 内容块（Anthropic 官方尚无 video block，按 image-block 同构形状兼容）。第三个补丁与 LLM 链路无关（ARMS RUM 监控）。

### 原理与设计取舍

模型能力用声明目录而非硬编码分支：contextWindow 驱动压缩阈值，inputFormat 驱动媒体投影，optionSpecs 驱动请求改写——新增模型不改执行代码。

跨 provider 行为差异收敛在 fetch 层与补丁层，执行核心不感知供应商。

### 适用条件与边界

OTel 观测点（turn/step/tool/model span）在 telemetry 包，默认采样 0.1、无 endpoint 时完全不加载 SDK。

流式重试与失败分类（failure-classifier）未逐行阅读。

[ev-model-provider](#evidence-ev-model-provider), [ev-provider-transport](#evidence-ev-provider-transport), [ev-option-map](#evidence-ev-option-map), [ev-model-stream](#evidence-ev-model-stream)

<a id="mechanism-plugin-skill"></a>

## 插件市场、内置播种与技能生态

插件从哪里来、如何播种？"卸载内置插件"为什么不删缓存？

plugin.json 声明 commands/agents/skills/hooks/mcpServers/userConfig（兼容 .zcode/.claude/.codex 三套 manifest 路径）。发现层从四类候选来源收集插件（inline、官方市场缓存、installed.json 安装记录、config dirs），id=&lt;name&gt;@&lt;marketplace&gt;，启停读 enabledPlugins ?? defaultEnabled。内置官方插件启动时播种进官方市场缓存（sha256 校验、15 秒文件锁预算、失败回滚）；"卸载"只写 suppressedBuiltins 抑制标记——缓存是不可变产品资产，恢复内置插件就是清除标记重新播种。官方市场由 bundled\+CDN 两分片合并，CDN zip 强制 sha256。

### manifest 与组件装配

PluginManifest 含 name/version/agents/commands/hooks/mcpServers/userConfig/dependencies；channels/lspServers/outputStyles/settings 仅诊断用，运行时拒绝并告警。启用态下组件物化：skills/commands roots 并入发现、manifest commands 生成为 generated-commands 根、hooks 按 schema 校验、MCP server 定义并入配置。

[ev-plugin-manifest](#evidence-ev-plugin-manifest)

### 内置插件播种

seedBundledOfficialPlugins 从文件系统或 SEA 资产读取插件（sha256 校验）写入 storage/cache/zcode-plugins-official/&lt;name&gt;/&lt;version&gt;/，带播种 marker 与文件锁；注释记录了两个教训——不能在 seed 阶段过滤"已卸载"插件（否则恢复功能失去数据源），且全部插件共享同一 15 秒锁预算（否则成组遗留锁会让启动同步冻结 N×15 秒）。一个残缺插件只拒绝自身写缓存并回退旧缓存，不再炸掉会话恢复。

[ev-plugin-seed](#evidence-ev-plugin-seed)

### 抑制、恢复与孤立插件

用户"卸载"内置插件写 user config 的 suppressedBuiltins；发现层按 id 过滤不重播种（Restorable Builtin），恢复动作清除标记并立即重新播种。个人来源被删除后，已安装插件目录与数据保留、仍可用可卸载（Orphaned Installed Plugin），来源重新添加后恢复目录关联——CONTEXT.md 词汇表逐项对应代码实现。

### 市场与安装事务

官方市场合并分片：bundled-marketplace.json \+ cdn-marketplace.json → marketplace.json，同名以 CDN 为准且不删内置缓存；CDN zip 源强制 sha256。installMarketplacePlugin 解析依赖闭包 → 原子目录激活 → 失败回滚 → installed.json 持久化；市场来源支持 url/github/git/npm/file/directory 六种。

### 原理与设计取舍

目录缓存是不可变产品资产，运行时态（抑制/启停）只存用户配置——"恢复内置插件"因此是纯数据操作，不需要重新分发。

安装是事务：依赖闭包解析、原子激活、失败回滚、持久化四步，配合文件锁与 sha256 校验。

### 适用条件与边界

三方插件 hooks 默认放行执行——源码注释自认放弃了"仅官方可执行 hook"的信任边界，依赖用户对插件的信任。

插件生态的安全审查不在本仓库范围内。

[ev-plugin-manifest](#evidence-ev-plugin-manifest), [ev-plugin-seed](#evidence-ev-plugin-seed)

<a id="mechanism-bash-exec"></a>

## Bash 执行的三层超时、输出预算与跨平台

命令执行如何保证可控：超时、输出、进程树与三个操作系统？

Bash 工具经 ExecutionPort 到 NodeExecutionAdapter：普通 child\_process.spawn，POSIX 下 detached 独立进程组、Windows 保持非 detached 交给 taskkill /T。超时三层：工具策略默认 120s/上限 600s（env 可覆盖）→ executor 的 ToolDeadline（cleanupGrace 6s）→ adapter 默认 300s 到点 requestStop。输出双层预算：core 30KB artifact 策略（超限全量落盘、模型收 tail 预览），adapter 端 30KB/上限 150KB 截断；后台任务输出始终落盘。必须指出：这里没有内核沙箱——dangerouslyDisableSandbox 字段全仓无行为消费者。

### spawn 与进程组

spawn 时注入清洗过的环境（去掉 NODE\_ENV/代理变量避免泄漏进 Bash 工具）、workspaceIdentity 身份隔离 env；detached 注释明言动机：agent 可能再派生 runtime/MCP 子进程，独立进程组才能按树回收。进程树终止：POSIX 用进程组 -pid 发 SIGTERM/SIGKILL，Windows 用 taskkill /pid X /T /F；MCP 子进程另有 Windows Job Object 原生方案。

[ev-spawn](#evidence-ev-spawn)

### 三层超时

策略层 resolveBashTimeoutMs = min\(input \|\| 120s 默认, 600s 上限\)，BASH\_DEFAULT\_TIMEOUT\_MS/BASH\_MAX\_TIMEOUT\_MS 可覆盖；executor 层 ToolDeadline 可暂停（审批等待时暂停计时）、cleanupGraceMs=6s 给清理钩子；adapter 层默认 300s 到点 requestStop\("timeout"\)。

[ev-bash-timeout](#evidence-ev-bash-timeout)

### 输出预算与落盘

core 侧 MAX\_INLINE\_OUTPUT\_BYTES=30000，resultBudget maxModelBytes 30000 \+ artifact 策略 \+ session 保留——超限全量写工件、模型收 tail 预览；adapter 侧 BASH\_MAX\_OUTPUT\_LENGTH 默认 30000 上限 150000。后台任务 persistOutput:"always"，供 TaskOutput 消费。

### 后台任务

显式 run\_in\_background 或超时自动转后台（auto\_on\_timeout；sleep 命令无资格、闲时回合禁止后台）。后台化后注册 runtime task、发 BackgroundTaskStarted 事件；TaskOutput/TaskStop 读写任务状态；完成通知走 runtime 命令队列回流会话。

### 跨平台细节

Windows 下 .cmd/.bat 强制经 cmd.exe /d /s /c 执行；shell 探测区分 posix-bash（bash -c -l）与 Git Bash（保留系统 find，不覆盖用户定义）；嵌入式搜索 prelude 只在 posix/git-bash 注入。AGENTS.md 要求终端交互基于能力检测而非假设固定特性。

### 原理与设计取舍

超时/预算的分层让不同层关注不同事：策略层管用户意图，executor 管审批暂停与清理，adapter 管进程事实。

身份与路径分离：workspaceIdentity 用于身份隔离与去重，workspacePath 只用于文件操作与 cwd。

### 适用条件与边界

无沙箱是本研读的结论性判断：dangerouslyDisableSandbox 仅是 schema 声明/输出回显字段，全仓 grep 无行为消费者，exec adapter 注释直言"sandbox 撤除后准备阶段只剩 shell snapshot"；不排除历史实现已移除。

隔离实际依赖权限审批与规则（见权限机制），Bash 的风险等级为 high、needsApproval=true。

[ev-spawn](#evidence-ev-spawn), [ev-bash-timeout](#evidence-ev-bash-timeout)

<a id="mechanism-ops"></a>

## 架构检查、发行打包与遥测

约 95 万行的 monorepo 如何自保工程质量与可发布性？

治理是可执行命令而非 wiki：architecture-check 强制循环依赖、单文件 400 行（契约 300）、contract 公开方法 ≤12、分层方向（低层不得 import 高层）、domain 层禁止 IO、managed 模块必须 module.ts\+contract.ts、跨模块只能走公开入口，存量违规靠 baseline 指纹豁免。发行有两条线：build-zcode 组装 CLI/TUI/server/web 的统一发行包（runner 分流 --web），桌面用 tsup\+vite\+electron-builder 并做 asar 闭包校验与体积审计。遥测用 OpenTelemetry OTLP（trace 采样 0.1、metric DELTA），未配置 endpoint 时完全不加载 SDK。

### 架构检查器

scripts/architecture/index.mjs 实现九类检查：forbidCycles、maxFileLines=400（契约 300）、lint-disable 计数、contract.ts 公开方法 ≤12（TS AST 计数）、layer-direction、domain-io（禁 node:fs/net/child\_process/fetch/setTimeout）、missing-module-artifact（managed 模块必须 module.ts\+contract.ts）、deep-import（只能走公开入口）、ui-implementation-import；--changed 只看变更文件反向依赖闭包；baseline sha256 指纹豁免存量。

[ev-archcheck](#evidence-ev-archcheck)

### policy 的覆盖现状

architecture-policy.yaml 声明了模块清单与 global 阈值，但多数模块 managed:false——目前只有 storage 是 managed 模块（domain/app/adapters 三层）。治理框架已就位但分层约束对多数包尚未生效，exceptions 清单为空。

### 发行包组装

build-zcode.mjs 依次构建 @zcode/cli、@zcode/server、@zcode/web，组装 dist/zcode/.work/zcode/（web/ 静态资源、server/entry-http.js、agent/zcode.cjs\+provider、TUI runtime 与原生库、bin/zcode.mjs runner），产出 tar.gz\+sha256.txt\+latest.json\+install.sh；安装脚本默认装到 ~/.zcode/runtime 并在 ~/.local/bin 创建 zcode。

[ev-runner-web](#evidence-ev-runner-web)

### 桌面打包

tsup 产出 main/preload/host/scheduler 四个 bundle（workspace 包内联，undici/ssh2/node-pty external），vite 构建 renderer，electron-builder 三平台打包；afterPack 用 asar 扫描补齐缺失 runtime modules 后 repack；产物做 asar 闭包机械校验与体积审计。Agent 本体另有 V8 字节码构建路径。

### OTLP 遥测

telemetry 包动态加载 OTel SDK：OTLP HTTP 导出器 GZIP 压缩、trace 批处理（100/批、队列 2000、5s）、采样 ParentBased\+0.1；metric DELTA temporality（注释：CLI 进程短生命周期，须避免累积语义冲突）、显式直方图桶与基数上限 250。身份是匿名 deviceMid；OTEL\_EXPORTER\_OTLP\_\* 未配置或显式关闭时完全不初始化。

### 原理与设计取舍

AGENTS.md 是给 AI 贡献者的宪法：spec 先行、测试证明结果、I/O 收敛、单一状态所有者——本仓库的治理对象首先是用 Agent 写代码的 Agent。

治理可执行化：规则进 CI 命令（verify:pre-push = lint \+ architecture:check --changed），而非文档约定。

### 适用条件与边界

managed:false 占多数意味着分层检查对多数模块是空转；治理的实效随 policy 收紧而变化。

遥测的默认采样与端点为代码事实，生产配置可能不同。

[ev-archcheck](#evidence-ev-archcheck), [ev-runner-web](#evidence-ev-runner-web)

<a id="mechanism-ui-render"></a>

## 三个前端如何共享同一份会话流

TUI、Web、桌面 Renderer 如何各自呈现同一个会话？

三个前端消费同一协议但用两种投影：TUI 是 React 19 \+ OpenTUI 自定义渲染器（30fps 帧循环、scrollbox viewport 裁剪、粘底滚动），业务状态全在 core，TUI 只保留 useState 镜像与临时交互态，事件 reducer 是纯函数；Web/Desktop Renderer 用 V4 投影存储——snapshot 整体替换、delta 顺序应用、断档携水位重订阅——把会话渲染成虚拟化时间线。权限审批/AskUserQuestion/计划确认在协议层都是 pendingInteractions，客户端只做呈现与 resolveInteraction 应答。

### OpenTUI 渲染器

createCliRenderer 配置 targetFps:30、useMouse、禁用默认 Ctrl-C 退出（SIGPIPE 保护）；FRAME 事件在首帧原生输出后才挂载真正的 App——"No runtime work can block the first paint"。transcript 用 scrollbox \+ stickyScroll \+ viewportCulling 处理长会话。OpenTUI 内部的差量/双缓冲实现是预编译二进制，仓库不可见。

[ev-tui-renderer](#evidence-ev-tui-renderer)

### TUI 的镜像状态与事件闸门

TuiOptions 注入一切后端能力（submitPrompt/subscribeSessionEvents/setMode/listModelOptions…）；messages/draft/busy/mode/approvalQueue 全是 useState 镜像，数据源是 SessionEvent 与 submitPrompt 结果。常驻订阅与每回合投递由 2048 条 id 记忆窗去重；mode 切换乐观更新失败回滚，model 选择不落 TUI 权威态（随 prompt 提交，展示值由 ModelSelected 事件回写）。

[ev-tui-events](#evidence-ev-tui-events)

### V4 投影与行模型

projection store 是只读投影，唯一写入方是订阅推送：snapshot 整体替换、delta 仅在 fromSeq 衔接时应用，断档携水位重订阅（重试 250ms/1s/3s）。行模型把会话建模为 userInput/assistantText/reasoning/toolCall/artifact/subagent 行，文本行带 streaming/complete/interrupted 状态；React 用 useSyncExternalStore 订阅，时间线虚拟化 \+ live tail 防跳动。

[ev-projection](#evidence-ev-projection)

### 交互请求的统一收口

会话 snapshot 的 pendingInteractions 分 permission/userInput/workspaceHookReview 三类；权限弹窗渲染选项并 resolveInteraction\(\{optionId\}\)，AskUserQuestion 渲染问答表单（草稿按 requestId 存、snapshot 为权威清理），ExitPlanMode 复用问答组件但 interaction=plan\_approval（空答案即拒绝、裸 Enter 才能提交）。TUI 侧对应 ApprovalPanel：审批队列非空时替换整个输入区并吞掉全部按键，Esc 即 deny。

[ev-interactions](#evidence-ev-interactions)

### 自研 i18n 与主题

轻量 IntlProvider：静态字典 \+ \{key\} 占位符替换，仅 zh-CN/en-US 双语（各约 6000 行），locale 经 localStorage\+settingService\+跨窗口 broadcast 三层同步。主题经 documentElement class 切换（dark/zai-light/zai-dark），DESIGN.md 的 text-ui-\* 刻度实现为 Tailwind v4 @theme 里的 calc\(\) 变量，全部派生自 --ui-font-size。

[ev-i18n](#evidence-ev-i18n)

### 原理与设计取舍

"不同客户端只是呈现与传输适配层"（AGENTS.md）：交互流程（确认/提问/计划审批）在协议层建模为 interaction 请求，而不是写死在某个前端。

UI 状态皆为镜像、权威在 core/session：这使 Web/Desktop/TUI 三个前端可以独立演进而不分裂会话事实。

### 适用条件与边界

OpenTUI 内部渲染机制（差量提交）是推断，预编译依赖不可见。

时间线虚拟化与终态聚合的全部细节未逐行核实。

[ev-tui-renderer](#evidence-ev-tui-renderer), [ev-tui-events](#evidence-ev-tui-events), [ev-projection](#evidence-ev-projection), [ev-interactions](#evidence-ev-interactions), [ev-i18n](#evidence-ev-i18n)

### ZCode Protocol

宿主/服务端与 Agent CLI 子进程之间跑在 stdio 上的 NDJSON 协议：Request/Notification/Response/Error 四类消息，zod schema 校验。

### ZCode Protocol V4

第二代协议：conversation/sessions-index/workspace-config 三个主题的 snapshot\+delta 发布订阅，与 legacy 并存于同一条 stdio 管道。

### Channel RPC

客户端与 server/host 之间的 VS Code 风格 RPC：二进制分帧 \+ call/listen 两种原语 \+ ProxyChannel 把服务自动代理成方法调用。

### 窗口 Host

每个桌面窗口一个的 Electron UtilityProcess，承载全部业务服务与远程连接注册表；Renderer 与手机都 attachment 到它。

### 回合（turn）

一次完整的模型回合：从输入准入排队开始，经过若干模型步（每次请求\+工具批次），直到 TurnComplete 的执行单元，由 TurnMachine 状态机约束。

### Steering（插话）

回合 busy 时新输入不丢弃：排队进 runtime 命令队列，在下一轮模型请求前 drain 进上下文。

### Compact（压缩）

上下文压缩。microcompact 无模型调用、只清旧工具结果；全量 compact 用同一模型生成结构化摘要替换历史。

### Artifact（工件）

超预算工具输出的落盘副本；模型只收到持久化路径与预览，避免大结果回灌上下文。

### 动态工作流（dwf）

dynamic workflow：用 TypeScript 脚本描述多代理编排，脚本在受控子进程沙箱运行，经 NDJSON 桥接调用宿主 WorkflowEngine 调度各 actor。

### 端口-适配器

contracts 包定义 \*Port 接口，adapters 包提供唯一 Node 实现；core 业务只依赖端口，跨进程/跨实现可替换。

### 内置插件

随应用分发的官方插件：启动时播种进官方市场缓存；"卸载"只写抑制标记（suppressedBuiltins），不删缓存，可随时恢复。

### CommandInbox

V4 网关的命令收口：同会话按 admission 顺序串行、幂等去重（512 条 LRU），重复命令共享同一终态。

### delivery profile

V4 帧推送节奏：desktop-continuous 30ms 连续流（含流式文本）；web-remote-replayable 150ms 可重放（仅文本行\+工具进度）。

### MCS

mid-conversation system：投影时允许被移到 system 侧的 system-reminder 来源集合。

## 接下来读哪里

运行时验证：按 README 用 pnpm bootstrap 后跑 `pnpm dev:web`，抓取 /ws 上的 Channel RPC 帧与 Agent 子进程的 stdio NDJSON 帧，验证本研读的静态协议结论（delivery profile 节流、CommandInbox 串行、流式事件持久化）。

深读 packages/ui/src/v4/ 的时间线虚拟化、live tail 与乐观 overlay 的完整实现（SessionPane.tsx 约 4700 行，本次只确认了入口与投影规则）。

追 session-fork（core/src/runtime/methods/session-fork.ts，1487 行）与 steering 的完整语义：fork 后的投影 lineage、stale run 防护、owner/lease 路由。

细读 SQLite migrations 与 eventReducer 的归约规则，补全会话持久化机制的 schema 级证据。

OpenTUI（@mbears/opentui-core）与 Vercel AI SDK 的内部机制在预编译依赖中，本研读只确认了集成面；如需渲染差量与流解析细节需另读上游源码。

## 研究范围

872ad960de7ec172591f7e1952f7849229f94521（main 分支，提交 "feat: open source"，工作树干净）

静态走读了全部约 95 万行 TypeScript：apps/zcode-cli 子工作区（cli/core/bootstrap/adapters/contracts/tui/dynamic-workflow 等全部 16 个子包）与 packages/（desktop/web/server/services/ui/shared/rpc/client/provider/provider-node/zcode-server-cli 等）。核心链路（启动分流、回合主循环、工具管线、权限判定、压缩阈值、NDJSON/V4 协议、Agent 子进程拉起、桌面 Host、发行分流）由主笔者逐行复核并摘录原文；大型客户端包（ui/services/desktop）为主笔复核决定性位置、子代理扫读其余。未运行系统；OpenTUI、AI SDK 等预编译依赖的内部机制不可见，相关结论已标注推断。

## 源码依据

<a id="evidence-ev-runner-web"></a>

### 发行入口 --web 分流 · 源码已阅读

README 宣称的"统一 zcode 命令、--web 启动 Web"的确切实现位置：分流在发行包 runner 而非 CLI 包内；serve\(\) 会 spawn server/entry-http.js 并注入静态目录与令牌。

scripts/zcode-distribution/runner.mjs : 256–267

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/scripts/zcode-distribution/runner.mjs#L256-L267)

```
  } else if (argv[0] === "--web") {
    const options = parseArgs(argv.slice(1));
    if (options.command === "help") console.log(usage());
    else if (options.command === "version") console.log(version);
    else await serve(options);
  } else {
    if (argv.length === 1 && ["--help", "-h"].includes(argv[0])) {
      console.log("Web mode: zcode --web [options] (zcode --web --help for details)\n");
    }
    // CLI 自启动子进程依赖 argv[1]；统一指向真正的 Agent 入口，保留 TTY 与所有原始参数。
    process.argv[1] = agentEntry;
    await import(pathToFileURL(agentEntry).href);
```

<a id="evidence-ev-cli-main"></a>

### CLI 进程边界 · 源码已阅读

main\(\) 清洗运行时环境、判断协议/TUI 模式、协议或 TUI 模式把 console 引到 stderr（stdout 是严格协议帧通道/TUI 专属）。结论来自子代理逐行阅读，主笔未复核原文摘录。

apps/zcode-cli/packages/cli/src/main.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/cli/src/main.ts)

<a id="evidence-ev-cli-run"></a>

### 命令分发 switch · 源码已阅读

run\(\) 解析全局参数后分发：-p/--target 进 headless，app-server\|agent-server 进协议服务器，默认命令名 tui（无参数进 TUI）。结论来自子代理逐行阅读（run.ts:486-575）。

apps/zcode-cli/packages/cli/src/run.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/cli/src/run.ts)

<a id="evidence-ev-create-app"></a>

### 组合根 createApp · 源码已阅读

五层合并配置、打开 SQLite 会话库、扫描子代理/插件/技能，构造 AgentRuntime 并注入端口（约 1292 行的组合根）。结论来自子代理逐行阅读（create-app.ts:146-797）。

apps/zcode-cli/packages/bootstrap/src/app/create-app.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/bootstrap/src/app/create-app.ts)

<a id="evidence-ev-runtime-tools"></a>

### 运行时工具装配 · 源码已阅读

AgentRuntime 的工具/钩子/执行器在装配点一次性收口；下游 registerRuntimeBuiltInTools（46-86 行）按端口与灰度门控决定每个内置工具是否注册。

apps/zcode-cli/packages/core/src/runtime/helpers/runtime-tools.ts : 33–44

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/helpers/runtime-tools.ts#L33-L44)

```
export function initializeRuntimeTooling(
  runtime: AgentRuntimeInternal,
  deps: AgentRuntimeDeps,
  sessionId: SessionId,
): { executor: ToolExecutor; hookRunner?: HookRunner } {
  registerRuntimeBuiltInTools(runtime, deps);
  const hookRunner = createRuntimeHookRunner(runtime, deps, sessionId);
  return {
    executor: deps.toolExecutor ?? createRuntimeToolExecutor(runtime, deps, hookRunner),
    hookRunner,
  };
}
```

<a id="evidence-ev-command-queue"></a>

### runtime 命令队列类型 · 源码已阅读

回合准入与排队的统一命令模型：用户输入、后台通知、子代理消息都进同一条队列，优先级 now/next/later 决定插队语义。

apps/zcode-cli/packages/core/src/runtime/command-queue.ts : 10–28

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/command-queue.ts#L10-L28)

```
export type RuntimeCommandPriority = "now" | "next" | "later";
export type RuntimeCommandMode =
  | "prompt"
  | "target-continuation"
  | "target-continuation-loop"
  | "task-notification"
  | "subagent-message"
  | "control-only-turn";
export type RuntimeCommandId = string & {
  readonly __runtimeCommandId: unique symbol;
};

export interface RuntimeCommandBase {
  readonly createdAt: Date;
  readonly id: RuntimeCommandId;
  readonly mode: RuntimeCommandMode;
  readonly priority: RuntimeCommandPriority;
  readonly traceContext: TraceContext;
}
```

<a id="evidence-ev-turn-orchestrate"></a>

### executeTurn 编排 · 源码已阅读

回合编排顺序：创建 turnModel\(:189\) → Context 初始化\(:215\) → SessionStart 钩子\(:227\) → /compact /rewind 分流\(:240\) → 发 TurnStarted\(:304\) → UserPromptSubmit\(:362\) → 持久化用户消息\(:477\)。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/methods/turn.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/turn.ts)

<a id="evidence-ev-turn-loop"></a>

### 回合主循环开头 · 源码已阅读

主循环是显式 while\(true\)：没有轮数计数硬停止，循环顶部只检查取消并 drain 排队插话/后台结果。退出由模型自然结束、取消、不可恢复错误等明确条件决定。

apps/zcode-cli/packages/core/src/runtime/methods/turn-loop.ts : 47–56

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/turn-loop.ts#L47-L56)

```
    while (true) {
      throwIfTurnAborted(state.turnAbortSignal);
      const outputTokenRecoveryActive = state.turnRequestState.outputTokenContinuationCount > 0;
      // guide 只允许由完整 tool result batch 设置这个一次性诊断；普通 queue 不在
      // model roundtrip 起点消费，避免把未来 turn 错并入当前 product turn。
      const drainedSteerForNextRequest = state.drainedSteerForNextRequest;
      state.drainedSteerForNextRequest = undefined;

      if (state.modelStepCount > 0 && !outputTokenRecoveryActive) {
        const drainedRuntimeCommands = await this.drainPendingRuntimeCommandsForActiveLoop();
```

<a id="evidence-ev-turn-compact-hook"></a>

### 每轮 microcompact 调用 · 源码已阅读

每个模型步请求前都会评估 microcompact（首轮 PreRequest、中途 MidTurn），随后（77-102 行）再评估 autoCompact 与 rapid-refill 熔断。

apps/zcode-cli/packages/core/src/runtime/methods/turn-loop.ts : 67–74

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/turn-loop.ts#L67-L74)

```
    const compactPhase =
      state.modelStepCount === 0 ? CompactPhase.PreRequest : CompactPhase.MidTurn;
    await this.microcompactIfNeeded(state.turnTraceContext, state.events, state.turnAbortSignal, {
      model: state.model,
      modelStepIndex: state.modelStepCount,
      phase: compactPhase,
      turnRequestState: state.turnRequestState,
    });
```

<a id="evidence-ev-turn-preparation"></a>

### MCP 初始化与工具面组装 · 源码已阅读

每轮循环在模型请求前初始化 MCP、按 disallowlist 过滤工具面；automation 派发轮按 queryId 再硬过滤写工具，防止模型看到并改写任务定义——工具面是每轮动态投影而非静态注入。

apps/zcode-cli/packages/core/src/runtime/methods/turn-loop.ts : 105–118

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/turn-loop.ts#L105-L118)

```
    const finishMcp = beginLocalTurnPreparation(state.turnTraceContext, "mcp");
    await this.initializeMcp(state.turnTraceContext);
    finishMcp();
    throwIfTurnAborted(state.turnAbortSignal);
    const finishTools = beginLocalTurnPreparation(state.turnTraceContext, "tools");
    const turnDisallowedTools = buildTurnDisallowedTools(state);
    // automation 派发到已 active 会话或重试恢复时，入口 metadata 可能没有带到
    // loop state；但 queryId 仍是 automation-*。provider 请求边界必须按 queryId 再硬过滤
    // automation 写工具，否则模型会先看到并创建、修改或删除任务定义。
    const tools = state.automationCreateLimitReached
      ? []
      : turnDisallowedTools
        ? this.getTools(state.model).filter((tool) => !turnDisallowedTools.has(tool.name))
        : this.getTools(state.model);
```

<a id="evidence-ev-model-stream"></a>

### 流式事件消费 · 源码已阅读

模型请求以 streamText 异步可迭代形式消费；每个事件经 enqueueStreamingEvent 的有序写队列持久化为 ModelStreaming 事件（高水位 128 条背压），流式与持久化同构。

apps/zcode-cli/packages/core/src/runtime/methods/model.ts : 228–243

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/model.ts#L228-L243)

```
  const modelStream = runWithModelInvocationContext(modelInvocationContext, () =>
    model.streamText(modelRequest),
  );
  finishAssembly();
  try {
    for await (const event of modelStream) {
      switch (event.type) {
        case "start": {
          await enqueueStreamingEvent({
            assistantMessageId: options.assistantMessageId,
            delta: "",
            done: false,
            kind: "start",
          });
          break;
        }
```

<a id="evidence-ev-tools-schedule"></a>

### 工具调度 · 源码已阅读

ToolScheduler 从工具元数据推导依赖并拓扑分组并行执行，DEFAULT\_MAX\_CONCURRENCY=10（scheduler.ts:34/48）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/tool/scheduler.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/tool/scheduler.ts)

<a id="evidence-ev-tool-normalize"></a>

### 调用管线：归一化在钩子之前 · 源码已阅读

单调用管线中 resolveInput 归一化的位置与理由：此后钩子、权限规则、确认窗与 handler 读同一份输入；解析失败在钩子/权限之前早退，不弹注定失败的确认窗。

apps/zcode-cli/packages/core/src/tool/executor/call-runner.ts : 204–230

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/tool/executor/call-runner.ts#L204-L230)

```
  // 归一化：把模型发出的入参换成「将要发生的执行事实」。位置刻意在 hook **之前**——此后
  // hook、权限规则、确认窗载荷、prepareApproval 与 handler 读的都是同一份输入，于是
  // 「策略看得到真正的脚本」「跨版本可见」「确认与执行同字节」三件事一次到位。
  if (entry.resolveInput) {
    const workingDirectory = deps.getWorkingDirectory?.();
    const resolution = await entry.resolveInput(executionInput, {
      ...(workingDirectory === undefined ? {} : { workingDirectory }),
      runtimeTaskRegistry: deps.runtimeTaskRegistry,
      ...(deps.dynamicWorkflowRunPort === undefined
        ? {}
        : { dynamicWorkflowRunPort: deps.dynamicWorkflowRunPort }),
      ...(deps.modelCatalogPort === undefined ? {} : { modelCatalogPort: deps.modelCatalogPort }),
      sessionId: deps.sessionId,
    });
    if (isToolHandlerFailure(resolution)) {
      // 与 validateInput 同一条生命周期出口：解析不出来是模型该立刻拿回去修的东西，
      // 不该先弹一次注定失败的确认窗。
      const result = createErrorResult(
        canonicalToolCall,
        createToolHandlerFailureError(canonicalToolCall, resolution),
      );
      await emitToolCallError(deps, canonicalToolCall.id, traceContext, turnId, result.error);
      telemetry?.finishFailed("validation", "parse", result.error);
      return result;
    }
    executionInput = resolution.input;
  }
```

<a id="evidence-ev-tool-artifact"></a>

### 结果预算：工件与截断 · 源码已阅读

超预算结果的两条路径：artifact 策略全量落盘、模型只收持久化预览；否则截断并追加带原始字节数与策略的标记。大结果不回灌上下文由机制保证。

apps/zcode-cli/packages/core/src/tool/executor/result-serialization.ts : 178–201

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/tool/executor/result-serialization.ts#L178-L201)

```
  if (effectiveBudget.strategy === "artifact" && effectiveBudget.artifact?.enabled === true) {
    if (artifact && resolvedArtifactPath) {
      const persistedOutputContent = formatPersistedOutputContent({
        content,
        entry,
        originalBytes,
        output,
        persistedPath: resolvedArtifactPath,
      });
      const persistedContent = stringifyModelContentForSerialization(persistedOutputContent);
      return {
        content: persistedContent,
        modelContent: persistedOutputContent,
        originalBytes,
        returnedBytes: Buffer.byteLength(persistedContent, "utf8"),
        truncated: true,
        budgetStrategy: effectiveBudget.strategy,
        artifactPath: resolvedArtifactPath,
      };
    }
  }

  const artifactHint = resolvedArtifactPath ? `artifactPath=${resolvedArtifactPath}, ` : "";
  const suffix = `\n\n[Tool output truncated by resultBudget: ${artifactHint}originalBytes=${originalBytes}, maxModelBytes=${maxModelBytes}, strategy=${effectiveBudget.strategy}]`;
```

<a id="evidence-ev-perm-order"></a>

### 权限判定顺序 · 源码已阅读

checkPermission 的判定顺序即语义优先级：alwaysAsk 在 yolo 直通之前（注释明言不可绕过），auto 模式保留未实现命中即 deny，disallowedTools 随后。

apps/zcode-cli/packages/core/src/permission/service.ts : 130–156

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/permission/service.ts#L130-L156)

```
    // 声明 alwaysAsk 的工具必须经过用户确认，不能被权限模式的放行分支绕过。
    if (capability.alwaysAsk) {
      return this.checkAlwaysAsk(context, capability, projectRules, rulePolicy);
    }

    const planEnabled = context.planEnabled ?? context.mode === "plan";
    if (context.mode === "yolo" && !planEnabled) {
      return this.allow(context, capability, "mode.yolo", "Yolo mode bypasses permission prompts");
    }

    if (context.mode === "auto") {
      return this.deny(
        context,
        capability,
        "mode.auto.unimplemented",
        "Auto mode is reserved but not implemented yet",
      );
    }

    if (this.config.disallowedTools.has(context.toolName)) {
      return this.deny(
        context,
        capability,
        "rule.disallowedTools",
        `Tool ${context.toolName} is explicitly disallowed`,
      );
    }
```

<a id="evidence-ev-perm-race"></a>

### 审批竞速及其修复注释 · 源码已阅读

需要审批时 PermissionBroker（确认窗）与 PermissionRequest 钩子链并发竞速；注释记录了串行实现导致确认窗永久死亡的真实缺陷与修复依据。

apps/zcode-cli/packages/core/src/tool/executor/permission-flow.ts : 179–184

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/tool/executor/permission-flow.ts#L179-L184)

```
    // 这里曾经串行 `await runPermissionRequestHooks(...)`，
    // broker 要等 hook 链返回才启动。同步 PermissionRequest hook（外部审批桥接）阻塞期间，
    // 确认窗已经渲染（上面的 emitPermissionRequested），但应答 deferred 尚未注册，用户的
    // 每一次点击都被 resolveInteraction 按幂等语义静默丢弃——确认窗永久死亡。
    // 修法：hook 链与 broker 并发竞速，先到的决定生效，败者被 abort 且不被等待。
    const raceOutcome = await racePermissionResponders({
```

<a id="evidence-ev-compact-const"></a>

### 压缩预算常量 · 源码已阅读

压缩阈值的核心常量：默认窗口 200K、输出预留收敛到 21K（preflight）、buffer 13K、摘要上限 20K、连续失败熔断 3 次；注释明言请求预算与压缩窗口必须共用一套常量。

apps/zcode-cli/packages/core/src/compact/policy.ts : 6–14

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/compact/policy.ts#L6-L14)

```
export const DEFAULT_COMPACT_CONTEXT_WINDOW = 200_000;
// 正常请求默认输出已收敛到 32K，auto compact 必须预留同一目标；
// 否则请求预算和压缩窗口会继续按两套常量计算。
export const DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS = 32_000;
const PREFLIGHT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS = 21_000;
export const MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000;
export const AUTOCOMPACT_BUFFER_TOKENS = 13_000;
export const DEFAULT_AUTOCOMPACT_THRESHOLD_PERCENT = 100;
export const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3;
```

<a id="evidence-ev-compact-formula"></a>

### 阈值公式 · 源码已阅读

阈值计算：有效窗口 = contextWindow − min\(maxOutputTokens, 21K\)；阈值 = 有效窗口 − 13K。200K 窗口即约 166K 触发 auto compact。

apps/zcode-cli/packages/core/src/compact/policy.ts : 67–88

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/compact/policy.ts#L67-L88)

```
export function getEffectiveContextWindowSize(config: AutoCompactPolicyConfig = {}): number {
  const contextWindow = positiveInt(config.contextWindow) ?? DEFAULT_COMPACT_CONTEXT_WINDOW;
  // provider 的 context window 是 input + output 共享窗口；自动压缩只能让出输入侧，
  // 因此阈值分母必须先扣掉当前模型允许的 output token，而不是继续吃完整 contextWindow。
  const reserve = Math.min(getAutoCompactOutputReserveTokens(config), contextWindow);
  return Math.max(0, contextWindow - reserve);
}

export function getAutoCompactOutputReserveTokens(config: AutoCompactPolicyConfig = {}): number {
  const maxOutputTokens = positiveInt(config.maxOutputTokens);
  // 旧 legacy 分支为完整模型输出预留窗口，既过早压缩又要求远端选择；现在统一保留至多 21K。
  return Math.min(
    maxOutputTokens ?? DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS,
    PREFLIGHT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS,
  );
}

export function getAutoCompactThreshold(config: AutoCompactPolicyConfig = {}): number {
  const effectiveContextWindow = getEffectiveContextWindowSize(config);
  const buffer = positiveInt(config.bufferTokens) ?? AUTOCOMPACT_BUFFER_TOKENS;
  return Math.max(0, effectiveContextWindow - buffer);
}
```

<a id="evidence-ev-compact-active"></a>

### 全量 compact 编排 · 源码已阅读

compactModel 默认取会话当前模型（:174-178，非独立摘要模型）；摘要输出上限 min\(模型上限,20K\)（:716-725）；完成后 replaceMessages 并清 readFileState（:615-625）；prompt-too-long 逐级降级重试 3 次。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/methods/compact-active.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/compact-active.ts)

<a id="evidence-ev-agentsmd-memory"></a>

### AGENTS.md 与记忆索引注入 · 源码已阅读

AGENTS.md 以带 OVERRIDE 声明的 \# agentsMd 块注入；项目记忆索引 MEMORY.md 以固定标题格式注入为相邻 meta\_user 块。

apps/zcode-cli/packages/core/src/context/sections/request-user-context.ts : 63–86

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/context/sections/request-user-context.ts#L63-L86)

```
  return [
    // 聚合字段标题不能绑定到 AGENTS.md，否则仅有 Project Memory 时缺少标题。

    "# agentsMd",
    "Codebase and user instructions are shown below. Be sure to adhere to these instructions. IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written.",
    "",
    sections.join("\n\n"),
  ].join("\n");
}

function buildProjectMemoryIndexContent(
  memoryRoot: string | undefined,
  indexContent: string | undefined,
): string | null {
  if (!memoryRoot || indexContent === undefined) return null;
  const formatted = formatProjectMemoryIndexContent(indexContent);
  if (!formatted) return null;

  return [
    `Contents of ${join(memoryRoot, "MEMORY.md")} (user's auto-memory, persists across conversations):`,
    "",
    formatted,
  ].join("\n");
}
```

<a id="evidence-ev-memory"></a>

### 自动记忆抽取与写权限 · 源码已阅读

后台记忆子代理：同一主模型但最低推理档、输出 ≤5K、最多 5 轮；写权限收敛为 Write/Edit memory 目录内 .md \+ 只读 Bash \+ 目录内 rm；敏感路径段黑名单。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/memory/memory-agent-loop.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/memory/memory-agent-loop.ts)

<a id="evidence-ev-sysreminder"></a>

### system-reminder 三类来源 · 源码已阅读

三类共 30 个 reminder 来源（prefix/persisted/per-request，:20-51）、统一包裹与严格转义防伪造（:73-74, :233-237）、MCS 集合决定可移到 system 侧的来源（:76-86）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/system-reminder/source.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/system-reminder/source.ts)

<a id="evidence-ev-skills-listing"></a>

### 技能清单注入 · 源码已阅读

技能清单按字母序注入并带预算退化：全文超预算时退化为纯名字\+路径列表，保证元信息永不撑爆上下文。

apps/zcode-cli/packages/core/src/context/sections/skills.ts : 39–58

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/context/sections/skills.ts#L39-L58)

```
function buildSkillsContent(skills: SkillMetadata[], budget: number): string {
  const lines = [
    "The following skills are available for use with the Skill tool:",
    "",
  ];

  const sortedSkills = [...skills].sort((a, b) =>
    skillDisplayName(a).localeCompare(skillDisplayName(b)),
  );
  const skillLines = sortedSkills.map((skill) => formatSkillLine(skill, MAX_DESCRIPTION_CHARS));
  const full = [...lines, ...skillLines].join("\n");
  if (full.length <= budget) {
    return full;
  }

  const namesOnly = sortedSkills.map(
    (skill) => `- ${skillDisplayName(skill)}${bareAliasSuffix(skill)} (file: ${skill.path})`,
  );
  return [...lines, ...namesOnly].join("\n");
}
```

<a id="evidence-ev-embedded-search"></a>

### 嵌入式搜索 prelude · 源码已阅读

Bash shell prelude 注入 find\(\)/grep\(\) 函数把搜索路由到打包的 bfs/ugrep（ripgrep 兜底）；Git Bash 保留系统 find、cmd/legacy-shell 不注入——能力检测而非假设。

apps/zcode-cli/packages/adapters/src/exec/embedded-search-prelude.ts : 34–56

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/adapters/src/exec/embedded-search-prelude.ts#L34-L56)

```
export function buildEmbeddedSearchPreludeContent(
  prelude?: ExecutionEmbeddedSearchPrelude,
  options: EmbeddedSearchPreludeOptions = {},
): string | undefined {
  if (prelude?.kind !== "embedded-search") return undefined;
  if (!supportsPosixShellFunctionPrelude(options.shellDialect)) return undefined;

  const backend = normalizeBackendForShell(prelude.backend, options.shellDialect);
  // Windows 不分发 bfs；Git Bash 必须保留系统 find，不能只因存在 fallback 就覆盖用户定义。
  const shouldWrapFind = options.shellDialect !== "git-bash";
  const content =
    prelude.findAndGrepEnabled === false
      ? []
      : [
          ...(shouldWrapFind ? ["unalias find 2>/dev/null || true"] : []),
          "unalias grep 2>/dev/null || true",
          ...(shouldWrapFind ? [createFindFunction(backend)] : []),
          createGrepFunction(backend),
        ];
  const ripgrepFallback = createRipgrepFallback(backend);
  if (ripgrepFallback) content.push(ripgrepFallback);
  return content.join("\n");
}
```

<a id="evidence-ev-session-create"></a>

### 会话落库 · 源码已阅读

ensureSessionPersisted（:558-660）在首轮落库 session（projectID 由目录派生、title 取首条输入、permission mode）并同步持久化模型选型/shell 快照。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/methods/events.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/events.ts)

<a id="evidence-ev-event-store"></a>

### 事件存储与投影 · 源码已阅读

事件 append 先落库再通知 sink；rebuildProjection = getEvents \+ eventReducer.reduce（:385-388）；用户消息持久化含 queue promotion 原子事务（:142-148）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/methods/message-persistence.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/message-persistence.ts)

<a id="evidence-ev-sqlite-store"></a>

### SQLite 会话库 · 源码已阅读

SessionStorePort 的生产实现是 SqliteSessionStore（:225），默认库路径 ~/.zcode/cli/db/db.sqlite（paths.ts:6-8），bootstrap 启动时 open\+migrate。结论来自子代理逐行阅读。

packages/adapters/src/storage/session-store/sqlite-session-store.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/adapters/src/storage/session-store/sqlite-session-store.ts)

<a id="evidence-ev-rewind"></a>

### 文件检查点与 rewind · 源码已阅读

Edit/Write 成功后从工具输出提取 filePath/structuredPatch/originalFile 序列化为 WorkspaceCheckpointArtifact 并发 CheckpointCreated（tools.ts:169-251 调用；rewind.ts:38-81 构建；:83-135 按 checkpointId 或消息选择）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/helpers/rewind.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/helpers/rewind.ts)

<a id="evidence-ev-agent-tool"></a>

### Agent/Task 工具 · 源码已阅读

Agent 工具 handler 调 subagentPort.launch（:174-222），concurrentSafe:true（:231）；Task 是 providerVisible:false 的兼容别名（:287-300）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/tool/handlers/agent.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/tool/handlers/agent.ts)

<a id="evidence-ev-subagent-child"></a>

### child runtime 创建 · 源码已阅读

子代理是全新的 AgentRuntime 实例（独立会话身份），权限模式继承父的完整状态而非回退。

apps/zcode-cli/packages/core/src/runtime/methods/subagent.ts : 239–244

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/subagent.ts#L239-L244)

```
      const childRuntime = new AgentRuntime(
        request.sessionId,
        {
          // 旧 plan 枚举不包含基础权限；拆分后继承完整状态，避免被构造器回退成 build。
          mode: childMode === "plan" ? this.config.mode : childMode,
          planEnabled: childMode === "plan",
```

<a id="evidence-ev-subagent-isolation"></a>

### 子代理隔离边界 · 源码已阅读

隔离边界的精确事实：子代理只复用父已解析的指令快照（不继承 Project Context）、maxTurns 默认 4（仅子代理 runner 强制）、taskType=subagent\_child、父会话 id 作 parentSessionId。

apps/zcode-cli/packages/core/src/runtime/methods/subagent.ts : 262–271

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/subagent.ts#L262-L271)

```
          // child 只复用父 runtime 已解析的 instructions snapshot；Project Context 仍不继承。
          currentDate: this.contextSourceSnapshot?.currentDate ?? this.config.currentDate,
          subagentContext: {
            agentPrompt: agentPrompt ?? "",
            ...(agentsMdInstructions ? { userInstructions: agentsMdInstructions } : {}),
          },
          agentName: `zcode-${request.agentType}`,
          maxTurns: request.maxTurns ?? this.config.subagents?.maxTurns ?? 4,
          parentSessionId: this.sessionId,
          taskType: "subagent_child",
```

<a id="evidence-ev-sub-lifecycle"></a>

### 子代理后台通知管线 · 源码已阅读

后台子代理/任务完成通知入 runtime 命令队列（mode=task-notification、priority=next、写 durable admission 账本），排空时合成一条 model-only 消息进入父会话下一轮（:10-83, :166-218）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/methods/background-notifications.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/background-notifications.ts)

<a id="evidence-ev-dwf-harness"></a>

### dwf 沙箱 harness 设计注释 · 文档描述

dwf 的完整执行模型（源码设计注释）：脚本写入 .zcode/workflow-runs 后 spawn 独立 node 子进程，NDJSON 桥接 \_\_host.\* 到宿主引擎；终结失败与 abort（唯一真取消）的结算语义；runtime 包零业务依赖可独立测试。

apps/zcode-cli/packages/dynamic-workflow-runtime/src/harness.ts : 1–28

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/dynamic-workflow-runtime/src/harness.ts#L1-L28)

```
/**
 * 父进程 harness（Boundary A 的宿主侧 + 沙箱进程编排）。
 *
 * 职责：把一份 workflow 脚本（或已 lowered 的函数体）在受控子进程里跑起来，用 NDJSON 桥接
 * 子进程的 `__host.*` 调用到一个 {@link WorkflowEngine} 实例，最终返回引擎的 {@link RunSettlement}。
 * 本包**只**依赖 `@zcode/dynamic-workflow` 与 node 内建——证明整条管线 app-free 可跑，
 * 绝不 import `@zcode/core`/`@zcode/contracts`/`@zcode/bootstrap`/`@zcode/adapters`。
 *
 * 时序（happy path）：
 *
 *   parent                         child(vm)
 *     │  write <cwd>/.zcode/workflow-runs/<runId>.mjs（payload: lowered+args 内嵌）
 *     │  spawn(node <entry>)
 *     │──────────────────────────▶│  build __host in context
 *     │◀── create-actor(local#1) ──│  createActor 同步返回 local#1
 *     │  map local#1 → ActorId     │
 *     │◀── request ask(local#1) ───│  await __host.ask(...)
 *     │  engine.ask → driver.startAsk … settle
 *     │── response(value) ────────▶│  resolve
 *     │◀────── complete(ok,value) ──│  脚本 return
 *     │  engine.complete(value) → settled=completed
 *
 * 失败/取消：run 的裁决归引擎所有。终结失败（脚本抛错 error-complete、子进程崩溃/非零退出、
 * 墙钟超时、子进程行 JSON 解析失败）都调 `engine.fail(error)`（结算 failed + journal failure_json）；
 * abort 信号是唯一的"真取消"，调 `engine.stop(initiator)`（结算 stopped；`signal.reason` 为
 * `"model"` 即主代理 TaskStop，`"interrupted"` 即宿主 App 关闭时停下自己拥有的 run，否则算用户）。引擎自身的 run 级失败
 * （reportCap/inputHash/unknownActor）同样经 engine.settled 冒出。harness 侧的 first-wins
 * finalize 只管子进程清理（清 timer、关 stdin、kill child），不自造结算。
```

<a id="evidence-ev-ndjson"></a>

### NDJSON 传输实现 · 源码已阅读

legacy 协议的物理形态：发送即 JSON\+换行；接收按 LF 切帧逐行分发（dispatchLine 内做 zod 校验，解析错误回 -32700/-32600）。

apps/zcode-cli/packages/bootstrap/src/zcode-protocol/transport.ts : 74–95

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/bootstrap/src/zcode-protocol/transport.ts#L74-L95)

```
  send(message: ZCodeProtocolOutgoingMessage): void {
    if (this.terminal) return;
    try {
      this.options.output.write(`${JSON.stringify(message)}\n`);
    } catch (error) {
      this.onError(error instanceof Error ? error : new Error(String(error)));
    }
  }

  private readonly onData = (chunk: Buffer | string): void => {
    if (this.terminal || this.draining) return;
    this.buffer += typeof chunk === "string" ? chunk : chunk.toString("utf8");
    let newlineIndex = this.buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = this.buffer.slice(0, newlineIndex).trim();
      this.buffer = this.buffer.slice(newlineIndex + 1);
      if (line.length > 0) {
        this.dispatchLine(line);
      }
      newlineIndex = this.buffer.indexOf("\n");
    }
  };
```

<a id="evidence-ev-v4-discipline"></a>

### V4 包纪律与 wire 版本 · 源码已阅读

V4 的定位与工程纪律：数据模型草稿未冻结、schema 包零运行时、wire v3 与 legacy 版本常量并存互不改写。

packages/shared/src/zcode-protocol-v4/core.ts : 1–7

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/shared/src/zcode-protocol-v4/core.ts#L1-L7)

```
// ZCode Protocol v4 —— 数据模型草稿（未冻结，schema 定型以黄金测试为准）。
// 本包纪律：只放 schema 类型 + 纯函数，禁止任何运行时/IO/传输逻辑。
import { z } from "zod";
import { VIDEO_INPUT_MAX_BYTES } from "../zcode-media-policy.js";

/** V4 物理 wire 协议版本；projection snapshot 继续独立使用 protocolVersion=1。 */
export const V4_WIRE_PROTOCOL_VERSION = 3 as const;
```

<a id="evidence-ev-v4-profiles"></a>

### V4 delivery profiles · 源码已阅读

两种推送节奏的精确参数：桌面 continuous 30ms 全量流式（256KiB 上限）；Web/手机 replayable 150ms、只有文本行\+工具进度、无流式正文——两种客户端语义由 profile 数据而非分支代码区分。

packages/shared/src/zcode-protocol-v4/core.ts : 34–59

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/shared/src/zcode-protocol-v4/core.ts#L34-L59)

```
export const DELIVERY_PROFILES = {
  continuous: {
    desktopOnlyRows: true,
    flushWindowMs: 30,
    streamPaths: {
      text: true,
      inputText: true,
      "output.text": true,
      summaryText: true,
    },
    streamOutputCapBytes: 262144,
    toolProgress: false,
  },
  replayable: {
    desktopOnlyRows: false,
    flushWindowMs: 150,
    streamPaths: {
      text: true,
      inputText: false,
      "output.text": false,
      summaryText: false,
    },
    streamOutputCapBytes: 0,
    toolProgress: true,
  },
} as const satisfies Record<string, DeliveryProfile>;
```

<a id="evidence-ev-v4-transport"></a>

### V4 主题与帧 · 源码已阅读

三个主题 topic（conversation/sessions-index/workspace-config，:86-240）、TopicFrame= snapshot\+delta（:164-166）、&gt;1MiB 分片\+crc32 的物理帧（wire.ts:15-49）、订阅走同管道 owned notification v4/conversation/frame（:382-411）。结论来自子代理逐行阅读。

packages/shared/src/zcode-protocol-v4/transport.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/shared/src/zcode-protocol-v4/transport.ts)

<a id="evidence-ev-inbox"></a>

### CommandInbox 串行 admission · 源码已阅读

三层事实分离（in-flight/live pinned、settled 进 512 条 LRU）、AsyncGateRegistry FIFO \+ 固定锁序串行 admission（:79-141）、admissionSeq 分配权威、重复命令共享 final promise 且 duplicate ACK 不覆盖 failed 终态（:196-199, :446-449）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/command-inbox.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/command-inbox.ts)

<a id="evidence-ev-rpc-layers"></a>

### Channel RPC 七层架构注释 · 文档描述

客户端侧 RPC 框架的分层总览（源码自述）：序列化、传输抽象、Channel call/listen、连接管理、ProxyChannel 自动代理、远程连接——与 Agent 子进程侧的 NDJSON 协议是两套独立协议。

packages/rpc/src/index.ts : 4–28

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/rpc/src/index.ts#L4-L28)

```
 * 架构总览（从底到顶）：
 *
 * ┌──────────────────────────────────────────────────────────────┐
 * │  Layer 6: Remote 远程连接                                     │
 * │  RemoteAuthorityResolver → SocketFactory → PersistentProtocol │
 * │  → IPCClient → channel.call()                                │
 * ├──────────────────────────────────────────────────────────────┤
 * │  Layer 5: ProxyChannel 服务自动代理                            │
 * │  fromService(service) ↔ toService(channel)                   │
 * ├──────────────────────────────────────────────────────────────┤
 * │  Layer 4: IPCServer(1:N) / IPCClient(1:1 双向)               │
 * │  连接管理、路由、多播                                          │
 * ├──────────────────────────────────────────────────────────────┤
 * │  Layer 3: ChannelServer / ChannelClient                      │
 * │  基于 Channel 的 RPC (call/listen)                            │
 * ├──────────────────────────────────────────────────────────────┤
 * │  Layer 2: IMessagePassingProtocol                            │
 * │  send(buffer) / onMessage: Event<buffer>                     │
 * ├──────────────────────────────────────────────────────────────┤
 * │  Layer 1: 序列化 (VQL + 类型标签)                             │
 * │  serialize() / deserialize()                                  │
 * ├──────────────────────────────────────────────────────────────┤
 * │  Layer 0: 基础设施                                            │
 * │  Event / Emitter / Disposable / VSBuffer / CancellationToken │
 * └──────────────────────────────────────────────────────────────┘
```

<a id="evidence-ev-host-diagram"></a>

### 窗口 Host 拓扑注释 · 文档描述

桌面进程拓扑的权威自述：每窗口一个 Host（UtilityProcess），Renderer 与手机都 attachment 到它，Host 内是本地服务\+远程连接注册表；Main 只负责 fork 与一次性初始化。

packages/desktop/src/host/index.ts : 3–14

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/desktop/src/host/index.ts#L3-L14)

```
/**
 * Host Process 入口 —— 每个窗口对应一个独立的 host process
 *
 * 同一窗口的 Renderer 和手机 都 attachment 到这个 Host：
 *   Renderer / Mobile ←MessagePort→ Window Host
 *                                      ├─ local services
 *                                      └─ remote connection registry
 *
 * 启动流程：
 * 1. main 进程通过 Electron `utilityProcess.fork()` 创建本进程
 * 2. main 进程只发送一次 init-local 初始化窗口 Host
 * 3. 后续远端 connect / scoped attachment 都由同一 Host 处理
```

<a id="evidence-ev-spawn"></a>

### Agent 子进程 spawn · 源码已阅读

Agent CLI 子进程的 spawn 事实：stdio 三管道（协议帧/控制/stderr）、POSIX detached 进程组便于整树回收、环境清洗、workspaceIdentity 身份与 workspacePath 分离。

packages/services/src/zcode-agent/zcodeAgentProcessManager.ts : 1019–1034

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/services/src/zcode-agent/zcodeAgentProcessManager.ts#L1019-L1034)

```
    const child = spawn(effectiveCommand.command, spawnPreflight.args, {
      cwd: spawnPreflight.cwd,
      // agent 可能再派生实际 runtime/MCP 子进程。POSIX 下让 wrapper 进入独立进程组，
      // 关闭时才能按进程树整体回收；Windows 保持非 detached，交给 taskkill /T 处理。
      detached: shouldSpawnInDetachedProcessGroup(),
      env: {
        ...sanitizeZCodeRuntimeEnv(process.env),
        [ZCODE_RUNTIME_ENV_KEY]: runtimeEnv,
        ...spawnEnv,
        ...effectiveCommand.env,
        // 身份/隔离语义使用 workspaceIdentity；cwd 继续使用 workspacePath。
        ...buildAgentWorkspaceIdentityEnv(params.workspaceIdentity),
        ...buildE2EAgentCoverageEnv(),
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
```

<a id="evidence-ev-server-ws"></a>

### server 路由与鉴权 · 源码已阅读

Web 形态的服务器事实：token 中间件只保护 /ws 与 /api/\*；/api/rpc-host-capability 签发一次性 capability；/ws 永远按 web-remote-replayable 建连，浏览器不能伪装 trusted host。

packages/server/src/http.ts : 304–329

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/server/src/http.ts#L304-L329)

```
  const authToken = options.authToken?.trim();
  if (authToken) {
    app.use("*", async (c, next) => {
      const pathname = new URL(c.req.url).pathname;
      const validToken = hasValidLiteToken(c, authToken);
      if (!isTokenProtectedPath(pathname) || validToken) {
        await next();
        return;
      }
      return c.json({ error: "Unauthorized" }, 401);
    });
  }

  app.get("/api/server-info", (c) => c.json(createServerInfo(options)));
  app.post("/api/rpc-host-capability", (c) => c.json(hostCapabilities.issue()));

  // 普通 `/ws` 永远是 terminal-client；浏览器/任意客户端设置旧 mode header
  // 都不能再把自己提升为 trusted host。
  app.get(
    "/ws",
    upgradeWebSocket(() => ({
      onOpen(_event, ws) {
        setupChannelServer(ws.raw as WebSocket, services, "web-remote-replayable");
      },
    })),
  );
```

<a id="evidence-ev-server-cli"></a>

### 守护进程化 server · 源码已阅读

Core 形态复用同一路由结构但强制 loopback（非 loopback 直接抛错 fail-closed，:126-132）；Supervisor 有崩溃预算退避与 control socket。结论来自子代理逐行阅读。

packages/zcode-server-cli/src/server-core/http.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/zcode-server-cli/src/server-core/http.ts)

<a id="evidence-ev-remote"></a>

### 远程连接流程 · 源码已阅读

远程 workspace 连接五步：detect 校验支持矩阵（:172）→ deployServer 上传组件（deploy.ts:89）→ exec 启动远端 entry-stdio（:359-392）→ hello 握手逐行读 stdout（handshake.ts:20-126）→ ChannelClient（:217-220）；浏览器经 /ws/remote/:id 只桥接 file/git/system/terminal 四服务（http.ts:387-391）。结论来自子代理逐行阅读。

packages/server/src/remote/connect.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/server/src/remote/connect.ts)

<a id="evidence-ev-model-provider"></a>

### openai-compatible 工厂装配 · 源码已阅读

API 类型到 AI SDK 工厂的映射与两处能力装配注释：includeUsage 显式请求流式 usage；supportsStructuredOutputs 用 binding 冻结的模型事实而非实时查表。

apps/zcode-cli/packages/adapters/src/model/model-execution.ts : 302–316

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/adapters/src/model/model-execution.ts#L302-L316)

```
      case "openai-compatible": {
        const provider = createOpenAICompatible<string, string, string, string>({
          name: providerConfig.name ?? providerId,
          baseURL: providerConfig.baseURL,
          apiKey,
          fetch: optionFetch,
          headers,
          // OpenAI Compatible 流式 usage 需要显式请求，Usage 是执行结果的一部分。
          includeUsage: true,
          // 缺少这一装配时 SDK 默认 false，会把已声明支持的 Schema 静默降为 JSON object。
          // 使用 binding 冻结的模型事实，不按供应商或实时 Registry 另查一套能力。
          supportsStructuredOutputs: supportsJsonSchemaOutput,
        });
        return provider as LanguageModelFactory;
      }
```

<a id="evidence-ev-provider-transport"></a>

### fetch 洋葱的传输层 · 源码已阅读

请求发出的最后一段：Coding Plan 网关改写先于代理 fetch，httpProxy/noProxy/caCert 按实际发送地址生效；transport 按 provider 缓存。

apps/zcode-cli/packages/adapters/src/model/model-execution.ts : 329–338

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/adapters/src/model/model-execution.ts#L329-L338)

```
    // 官方 Coding Plan 端点先替换为平台网关端点，再进入用户 HTTP 代理 fetch，
    // httpProxy / noProxy 按实际发送地址判定。
    const transport = createProviderTransportFetch({
      caCertFile: this.network.caCertFile,
      env: this.env,
      fetch: this.baseTransport,
      httpProxy: this.network.httpProxy,
      noProxy: this.network.noProxy,
    });
    this.providerTransports.set(providerId, transport);
```

<a id="evidence-ev-option-map"></a>

### Option Map fail-closed 注释 · 源码已阅读

自定义 fetch 在发出前用编译好的 Merge Patch 改写 JSON body；注释明言 Option Map 是 reasoning/max-output 的唯一请求字段权威，非文本 Body 必须 fail-closed（:15-48）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/adapters/src/model/model-option-map-fetch.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/adapters/src/model/model-option-map-fetch.ts)

<a id="evidence-ev-plugin-manifest"></a>

### 插件 manifest 结构 · 源码已阅读

PluginManifest 字段（:141-161）与官方市场 id（:7）；发现层四类候选来源、id=&lt;name&gt;@&lt;marketplace&gt;（adapters/plugins/index.ts:921）、suppressedBuiltins 抑制过滤（:155-161）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/contracts/src/plugins/index.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/contracts/src/plugins/index.ts)

<a id="evidence-ev-plugin-seed"></a>

### 内置插件播种 · 源码已阅读

内置插件播种的两条设计约束（源码注释自述）：缓存是不可变产品资产、卸载态由发现层抑制而非删除；全部插件共享 15 秒锁预算防止启动冻结。

apps/zcode-cli/packages/bootstrap/src/app/bundled-plugins.ts : 98–108

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/bootstrap/src/app/bundled-plugins.ts#L98-L108)

```
  // Catalog/cache 是内置插件的不可变产品资产；Runtime 是否加载由 discovery 的抑制态决定，
  // 不能在 seed 阶段删除或过滤，否则卸载后详情页无法读取组件，也无法恢复。
  writeOfficialMarketplace(input.storageRoot, source);
  const retryBudget = createOfficialPluginCacheRetryBudget();
  // 等锁超时降级后循环会继续；若每个插件独立重置 15s 等待预算，成组遗留的
  // 锁会让启动同步冻结 N×15s。全部插件共享同一截止时间：无争用的锁仍瞬时获取（mkdir
  // 一次成功不查预算），预算耗尽后有争用的锁立即降级，seeding 总等待封顶 15s。
  const seedLockDeadlineAt = Date.now() + SEED_LOCK_TOTAL_BUDGET_MS;
  const failedSeeds: OfficialPluginDefinition[] = [];
  for (const plugin of source.plugins) {
    const pluginId = `${plugin.definition.name}@${OFFICIAL_PLUGIN_MARKETPLACE}`;
```

<a id="evidence-ev-bash-timeout"></a>

### Bash 超时策略 · 源码已阅读

默认 120s、上限 600s，BASH\_DEFAULT\_TIMEOUT\_MS/BASH\_MAX\_TIMEOUT\_MS 可覆盖；resolveBashTimeoutMs = min\(input \|\| default, max\)（:6-35）。executor ToolDeadline 与 adapter 300s 默认构成三层。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/tool/bash-timeout-policy.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/tool/bash-timeout-policy.ts)

<a id="evidence-ev-archcheck"></a>

### 架构检查规则 · 源码已阅读

九类检查：forbidCycles\(:36-71\)、maxFileLines 400/契约 300\(:135-156\)、contract 公开方法 ≤12\(:171-184\)、layer-direction\(:206-225\)、domain-io\(:188-201\)、missing-module-artifact\(:113-127\)、deep-import\(:252-266\)、ui-implementation-import\(:226-236\)、baseline 指纹豁免\(:282-306\)。结论来自子代理逐行阅读。

scripts/architecture/index.mjs

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/scripts/architecture/index.mjs)

<a id="evidence-ev-tui-renderer"></a>

### OpenTUI 渲染器配置 · 源码已阅读

TUI 技术栈事实：OpenTUI createCliRenderer、30fps 帧循环、鼠标支持、禁用默认 Ctrl-C 退出并显式管理退出信号（SIGPIPE 会话替换保护）、剪贴板选择复制。技术栈为 React 19 \+ @mbears/opentui（非 Ink）。

apps/zcode-cli/packages/tui/src/tui.tsx : 23–51

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/tui/src/tui.tsx#L23-L51)

```
  const renderer = await createCliRenderer({
    backgroundColor: activeTuiTheme(startupThemeMode).background,
    autoFocus: false,
    consoleMode: "disabled",
    enableMouseMovement: true,
    exitOnCtrlC: false,
    // Session replacement can raise SIGPIPE while closing MCP pipes. OpenTUI's
    // default exit signals include it and would destroy the entire TUI.
    exitSignals: ["SIGINT", "SIGTERM", "SIGQUIT", "SIGABRT", "SIGHUP", "SIGBREAK", "SIGBUS"],
    consoleOptions: {
      keyBindings: [
        {
          action: "copy-selection",
          ctrl: true,
          name: "y",
        },
      ],
      onCopySelection: (text) => {
        if (!hasCopyableSelectionText(text) || !options.writeClipboardText) return;
        void Promise.resolve(options.writeClipboardText(text)).finally(() =>
          renderer.clearSelection(),
        );
      },
    },
    stdin: options.stdin,
    stdout: options.stdout,
    targetFps: 30,
    useMouse: true,
  });
```

<a id="evidence-ev-tui-events"></a>

### TUI 事件 reducer 与去重 · 源码已阅读

常驻订阅与 per-turn 投递由 2048 条 id 记忆窗去重（:19-72），子会话事件按主会话闸门过滤；TUI 状态全是 useState 镜像（app.tsx:60-98），mode 切换乐观更新失败回滚（app-mode.ts:41-55）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/tui/src/app-session-event-handler.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/tui/src/app-session-event-handler.ts)

<a id="evidence-ev-projection"></a>

### V4 投影存储规则 · 源码已阅读

只读投影、唯一写入方是订阅推送：snapshot 整体替换、delta 仅在 fromSeq 衔接时应用、断档携水位重订阅（重试 250ms/1s/3s，:1-6, :23-27）；行模型见 shared/src/zcode-protocol-v4/rows.ts（userInput/assistantText/reasoning/toolCall 等行）。结论来自子代理逐行阅读。

packages/ui/src/v4/conversationProjectionStore.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/ui/src/v4/conversationProjectionStore.ts)

<a id="evidence-ev-interactions"></a>

### 交互弹窗统一收口 · 源码已阅读

pendingInteractions 三类（permission/userInput/workspaceHookReview）分别接 PermissionDialog/ElicitationDialog/Hook 信任流；应答统一发 v4 resolveInteraction 命令并等 ACK（:78-80, :104-111, :175-213, :310-388）。结论来自子代理逐行阅读。

packages/ui/src/v4/V4InteractionDialogs.tsx

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/ui/src/v4/V4InteractionDialogs.tsx)

<a id="evidence-ev-i18n"></a>

### i18n 与主题实现 · 源码已阅读

自研轻量 IntlProvider：静态字典\+占位符替换、zh-CN/en-US 双语、locale 三层同步（localStorage/settingService/broadcast）；text-ui-\* 刻度为 styles.css @theme 中派生自 --ui-font-size 的 calc\(\) 变量（styles.css:143-151）。结论来自子代理逐行阅读。

packages/ui/src/i18n/IntlProvider.tsx

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/packages/ui/src/i18n/IntlProvider.tsx)

<a id="evidence-ev-turn-stop"></a>

### 回合结束条件 · 源码已阅读

finishModelStepWithoutToolCalls（:157-237）：先尝试 inline guide 续跑一次（:195-200），再给 Stop 钩子一次续跑机会（:201-217），否则 turnMachine.complete \+ break；取消改发 TurnComplete\(cancelled\) 而非 TurnError（appendTurnOutcomeEvent，注释 :139-142）。结论来自子代理逐行阅读。

apps/zcode-cli/packages/core/src/runtime/methods/turn-stop.ts

[打开源码](https://github.com/JS-banana/ZCode/blob/872ad960de7ec172591f7e1952f7849229f94521/apps/zcode-cli/packages/core/src/runtime/methods/turn-stop.ts)
