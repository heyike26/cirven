# 开发与验证

## 目录

| 路径 | 说明 |
| --- | --- |
| `apps/agent-shell` | SolidJS 工作台、工程工作流和浮窗 |
| `apps/api` | FastAPI 本地服务、HTTP 接口和命令行入口 |
| `apps/desktop-host` | Electron 桌面主进程、浮窗及本地桥接 |
| `packages/dynamic_workflow` | 阶段上下文、审核闸门、覆盖核查、执行与证据 |
| `packages/agent_sessions` | 项目和会话服务 |
| `plugins/vscode-companion` | VS Code 扩展源码及测试 |
| `toolchain/opencode-plugins` | OpenCode 插件源码及上游依赖记录 |
| `contracts`、`tests` | 接口合同及测试 |

## 本地启动

按 [README](../README.md) 准备上游源码和构建依赖。首次准备上游会核对归档和关键文件校验值。重跑会覆盖依赖目录里与固定上游不同的文件。

要把临时项目数据与开发数据分开，启动前将 `EEAS_DATA_DIR` 指向单独的空目录。端口可用 `EEAS_PORT` 修改。项目应选择单独的可写工作目录。按需在工作台配置企业网关，凭据由使用者在各自环境配置。

Windows 下可用 `py -3.10 -m venv .venv` 和 `.\.venv\Scripts\Activate.ps1` 创建、激活环境。Node.js 和依赖准备命令与 README 一致。Electron 主进程和 VS Code 扩展均提供源码。独立桌面安装、Office 自动化、硬件联调与企业内网需要按各自运行环境验收。

## 工作流开发测试

在仓库根目录执行。

```bash
python -m pip install -r requirements-dev.txt
python scripts/prepare_source_checkout.py
python -m pytest -q tests/test_stage_workflow_runs.py tests/test_stage_kernel.py tests/test_stage_connector_calls.py tests/test_stage_native_chain.py
npm --prefix apps/agent-shell ci
npm --prefix apps/agent-shell run build
```

Playwright 浏览器依赖按需运行 `python -m playwright install chromium` 安装。浏览器用例入口与合成数据位于 `apps/agent-shell/tests/`。Windows 原生、模型服务与部分集成用例要求配置相应运行时。

## 验证记录

公开源码快照在 WSL 使用 Python 3.10 和 Node.js 22 复核。固定上游包恢复成功，Agent Shell 构建完成，健康检查、工作台首页与内置字体资源均通过 HTTP 验证。上述工作流用例 74 项通过，11 项因缺少原生内核或受控容器环境跳过。
