# zcode-agent-study · ZCode 深度研读

> An interactive, evidence-backed source-code deep dive into **ZCode**, an AI coding workbench (Electron desktop + Web + terminal agent).

对智谱 **ZCode**（AI 编程工作台：Electron 桌面 + Web + 终端 Agent CLI）的一次全仓源码深度研读，产出这份**可交互的架构导读页**。

**📖 在线阅读**：<https://js-banana.github.io/zcode-agent-study/>

单文件、离线可用，无需安装任何东西；支持架构图关系追踪、机制深链、源码摘录行级定位与全文搜索。

## 覆盖内容

- **15 个核心机制**：启动分流、回合主循环、工具管线、权限与审批、上下文压缩、记忆注入、会话持久化、子代理与双工作流、协议栈、桌面/Web 进程拓扑、模型请求链路、插件与技能生态、命令执行、工程治理、前端渲染
- **30+ 条源码摘录证据**：逐行核对，附修订锚定链接（rev `872ad96`，"feat: open source"）
- **范围**：约 95 万行 TypeScript —— `apps/zcode-cli` Agent 内核、Electron 桌面、HTTP/WS 服务端与共享协议层

## 文件

| 文件 | 用途 |
| --- | --- |
| `index.html` | 可交互研读页（推荐从这里开始） |
| `study.md` | 纯 Markdown 导出，适合离线阅读与引用 |
| `study.json` | 结构化数据源，可修改后重新渲染 |

## 它是怎么做出来的

研读与排版由自研 Agent 技能 **[learn-project](https://github.com/JS-banana/jkk-skills/tree/main/skills/learn-project)** 完成：多代理并行扫读全仓、主笔逐行复核关键路径，单一 JSON 数据源渲染为自包含 HTML（仅需 Python 标准库，无网络依赖）。

这个技能及其它 Agent Skills 都开源于 **[jkk-skills](https://github.com/JS-banana/jkk-skills)** ，欢迎 Star ✨。

## 声明

- 非官方研读笔记，与 ZCode / 智谱官方无关；分析基于公开源码 [JS-banana/ZCode@872ad96](https://github.com/JS-banana/ZCode)
- 结论来自静态源码走读，未做运行时验证的边界已在文内逐条标注

## License

[MIT](LICENSE)
