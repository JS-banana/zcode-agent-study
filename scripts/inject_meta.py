#!/usr/bin/env python3
"""向 index.html 注入 SEO meta（幂等：以 <!-- seo-meta --> 标记判重）。

用法：python3 scripts/inject_meta.py   （在仓库根目录执行）
仅需 Python 3.10+ 标准库，与 learn-project 渲染器的依赖约束一致。
"""

from pathlib import Path
import re

SITE_URL = "https://js-banana.github.io/zcode-agent-study/"
DESCRIPTION = (
    "ZCode（智谱 AI 编程工作台：Electron + Web + 终端 Agent）全仓源码深度研读，"
    "可交互架构导读：回合主循环、工具管线、权限模型、上下文压缩、双协议栈、"
    "子代理与工作流。基于 rev 872ad96，30+ 条源码摘录证据逐行核对。"
)
OG_TITLE = (
    "ZCode 全景研读：一个 AI 编程工作台如何把 Electron、Web 与终端 Agent 装配成一体"
)

META = f"""<!-- seo-meta -->
    <meta name="description" content="{DESCRIPTION}" />
    <meta property="og:title" content="{OG_TITLE}" />
    <meta property="og:description" content="{DESCRIPTION}" />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="{SITE_URL}" />
    <meta name="twitter:card" content="summary" />"""


def main() -> None:
    path = Path(__file__).resolve().parent.parent / "index.html"
    html = path.read_text(encoding="utf-8")
    if "<!-- seo-meta -->" in html:
        print("seo meta already present, skip")
        return
    new_html, count = re.subn(r"(<title>[^<]*</title>)", lambda m: m.group(1) + "\n    " + META, html, count=1)
    if count != 1:
        raise SystemExit("<title> not found in index.html")
    path.write_text(new_html, encoding="utf-8")
    print("seo meta injected")


if __name__ == "__main__":
    main()
