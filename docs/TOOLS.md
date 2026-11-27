# 开发工具与依赖来源

| 工具 | 在 Cirven 中的用途 |
| --- | --- |
| [Python 3.10](https://www.python.org/downloads/) / [FastAPI](https://fastapi.tiangolo.com/) | 本地 API、工作流执行和证据校验。版本见 `requirements-source.txt`。 |
| [Node.js](https://nodejs.org/) / [npm](https://docs.npmjs.com/) / [Vite](https://vite.dev/) / [SolidJS](https://docs.solidjs.com/) | 工作台前端构建。Node.js 22.22.2，前端依赖见 `apps/agent-shell/package-lock.json`。 |
| [Electron](https://www.electronjs.org/) / [VS Code](https://code.visualstudio.com/api) | Windows 桌面桥接源码和 VS Code 扩展源码。 |
| [OpenCode](https://opencode.ai/docs/) | 可选执行内核。前端使用已固定版本的上游源码；原生 CLI 由使用者按官方文档安装。自研插件位于 `toolchain/opencode-plugins/`。 |
| [Claude Code](https://code.claude.com/docs/) / [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview) | 可选执行内核和企业模型服务接入。Python SDK 版本见依赖表，原生 CLI 按官方文档安装。 |
| [Model Context Protocol](https://modelcontextprotocol.io/) | 按工作流阶段配置连接器与工具。项目自身的成果物撰写技能位于 `plugins/deliverable-authoring/`。 |
| [pytest](https://docs.pytest.org/) / [Playwright](https://playwright.dev/python/) | 工作流合同、API、浏览器布局与交互验证。 |
| [Ghostty Web](https://github.com/coder/ghostty-web) / [ELK](https://github.com/kieler/elkjs) / [Marked](https://github.com/markedjs/marked) / [Zod](https://zod.dev/) | 终端、图布局、Markdown 与校验。使用 `scripts/prepare_source_checkout.py` 拉取并核对固定版本。 |

日常开发使用 Codex 和 VS Code。UI 打磨采用组件层级、键盘焦点、浮层和动效检查，浏览器截图使用 Playwright。仓库包含项目依赖锁、上游清单、补丁、自研插件、测试与相关脚本。

初次构建的 GitHub 资源通过 [OpenCode 上游提交](https://github.com/anomalyco/opencode/tree/733b2c627e5aec39cb33cc22373bdce33053ef2c)固定，其文件哈希位于 `toolchain/source-inputs/opencode.json`。前端依赖许可保存在 `apps/agent-shell/public/licenses/`。

可直接阅读 [项目开发技能](../toolchain/dev-skills)，包含实现、排查、设计决策、证据核验和 Cirven 的界面开发规范。开发验收规则见 [完整交互与验收](FEATURE_VALUE_GATE.md)。
