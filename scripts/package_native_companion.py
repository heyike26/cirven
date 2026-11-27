"""Copy the selected Windows payload into a relocatable development preview.

No pip/network, build tool, Windows execution, GUI, Git or ambient account cache.
New output/archive paths stay in the internal workspace .development directory.
Existing D: installations and inputs remain read-only to this command.
"""
from __future__ import annotations
import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import sys
import uuid
import zipfile
from urllib.parse import unquote, urlsplit

try:
    from scripts.native_state_protocols import metadata as state_protocol_metadata
except ModuleNotFoundError:
    from native_state_protocols import metadata as state_protocol_metadata

try:
    from scripts.native_package_bootstrap import PackageError, SCHEMA, read_manifest, relative_path, sha, verify_plugin_closures
except ModuleNotFoundError:
    from native_package_bootstrap import PackageError, SCHEMA, read_manifest, relative_path, sha, verify_plugin_closures

EXCLUDE_DIRS = {'__pycache__', '.git', '.venv', 'node_modules', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    'site-packages-catalogue-original-not-qualified', 'tests', 'probe-cache', 'runtime-probe-cache'}
INTERNAL_ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / '.development'
SOURCE_TREES = ('apps/api', 'apps/web', 'apps/desktop-host', 'apps/agent-shell/dist',
    'apps/agent-shell/src', 'packages', 'profiles', 'contracts', 'toolchain/opencode-plugins/dynamic-workflow',
    'toolchain/opencode-plugins/change-review', 'toolchain/opencode-plugins/native-review-context',
    'toolchain/opencode-plugins/native-message-context','toolchain/opencode-plugins/model-authority')
CHANGE_REVIEW_PREFIX = 'toolchain/opencode-plugins/change-review'
CHANGE_REVIEW_MEMBERS = frozenset({'index.mjs', 'files.mjs', 'intent.mjs', 'budget.mjs', 'lineage.mjs', 'package.json', 'README.md'})
LEGACY_CHANGE_REVIEW_MEMBERS = CHANGE_REVIEW_MEMBERS - {'lineage.mjs'}
LEGACY_CHANGE_REVIEW_MANIFEST_SHA256 = 'd0a6f26b98c122db8d3b43e0ade33a2ecbeef0a3126b99f73cbfcbc842f77cee'
LEGACY_CHANGE_REVIEW_MANIFEST_SHA256S = frozenset({LEGACY_CHANGE_REVIEW_MANIFEST_SHA256, 'ddbd8528ae0d61ee29dd0fd75e43b330a16da97f2348611b126d235edd356690'})
CHANGE_REVIEW_MANIFEST_SHA256 = '5b8acd4ab091cdb837b0b3c93f7523ccffab67131afbc43cb2180d4cc8084a6f'
NATIVE_REVIEW_PREFIX = 'toolchain/opencode-plugins/native-review-context'
NATIVE_REVIEW_MEMBERS = frozenset({'index.mjs', 'package.json', 'README.md'})
NATIVE_REVIEW_MANIFEST_SHA256 = 'd6e6e5c7528f153e0f35f01e5c53c0782c686c85a76c7f9b33727017354129a0'
MODEL_AUTHORITY_PREFIX = 'toolchain/opencode-plugins/model-authority'
NATIVE_MESSAGE_PREFIX = 'toolchain/opencode-plugins/native-message-context'
NATIVE_MESSAGE_MEMBERS = frozenset({'index.mjs', 'package.json', 'README.md'})
NATIVE_MESSAGE_MANIFEST_SHA256 = 'd90b06450ced34ee3af287c993b3efb31b5b0be23503d4ecaca2171a57f0bbfe'
ENTRYPOINTS = {'python': 'runtime/python/python.exe', 'electron': 'runtime/electron/electron.exe',
    'main': 'apps/desktop-host/main.cjs', 'component_manifest': 'toolchain/components/cua-driver-shell-v014/MANIFEST.json',
    'readonly_manifest': 'toolchain/policies/native-collaboration-readonly-v1.yaml'}
SOURCE_EXTRAS = ('plugins/vscode-companion/protocol.json','plugins/vscode-companion/work-input-wire.cjs','plugins/vscode-companion/work-maintenance-wire.cjs','plugins/vscode-companion/native-export-wire.cjs','plugins/vscode-companion/work-delivery-wire.cjs','plugins/vscode-companion/work-source-wire.cjs','plugins/vscode-companion/test-report-wire.cjs','plugins/vscode-companion/work-requirement-wire.cjs','plugins/vscode-companion/work-case-wire.cjs','plugins/vscode-companion/work-retest-wire.cjs','pyproject.toml','requirements.lock','requirements-core.lock','requirements-runtime.lock',
    'scripts/native_package_bootstrap.py','scripts/package_native_companion.py',
    'scripts/native_user_state.py','scripts/native_state_protocols.py','scripts/native_maintenance.py','scripts/native_maintenance.ps1','toolchain/ocr/MODELS.json')
INSTRUCTION_VERSION = 'eeas.portable-help.v8-selected-program-recovery'
INSTRUCTION_FILES = ('README_FIRST.md', 'INTRANET_SETUP.md', 'README_FIRST.en.md', 'INTRANET_SETUP.en.md')
LAUNCHER_FILES = ('START_EEAS.cmd','MAINTAIN_CIRVEN.cmd')
IDE_COMPANION_FILES = ('apps/desktop-host/ide-companion-bridge.cjs',
    'apps/desktop-host/ide-api-adapter.cjs', 'plugins/vscode-companion/protocol.json', 'apps/desktop-host/ide-tool-details.cjs', 'apps/desktop-host/ide-context-recovery.cjs', 'apps/desktop-host/ide-native-context-recovery.cjs', 'apps/desktop-host/ide-canonical-native-recovery.cjs', 'apps/desktop-host/ide-canonical-conversations.cjs', 'apps/desktop-host/ide-conversation-configuration.cjs', 'scripts/native_package_bootstrap.py',
    'apps/desktop-host/main.cjs', 'apps/desktop-host/editor-capture-feed.cjs',
    'apps/desktop-host/ide-command-test-reports.cjs','plugins/vscode-companion/test-report-wire.cjs',
    'apps/desktop-host/ide-work-requirements.cjs','plugins/vscode-companion/work-requirement-wire.cjs',
    'apps/desktop-host/ide-work-test-cases.cjs','plugins/vscode-companion/work-case-wire.cjs',
    'apps/desktop-host/ide-work-sources.cjs','plugins/vscode-companion/work-source-wire.cjs','apps/desktop-host/ide-work-retests.cjs','plugins/vscode-companion/work-retest-wire.cjs',
    'apps/desktop-host/ide-work-deliveries.cjs','plugins/vscode-companion/work-delivery-wire.cjs',
    'apps/desktop-host/ide-delivery-exports.cjs','plugins/vscode-companion/native-export-wire.cjs',
    'apps/desktop-host/ide-work-maintenance.cjs','plugins/vscode-companion/work-maintenance-wire.cjs','plugins/vscode-companion/work-input-wire.cjs')
RUNTIME79_PARENT = {
    'closure': ('provenance/base77/dependency-closure-windows310.json', 'e8142d83d381423c43c4d600bde0e02fb92a262f318ea9d0021dc23f92fc6bf9'),
    'lock': ('provenance/base77/requirements-focused-native-windows310.lock', '4655172ef2aa387507dbacf3e6c5f0fc2dc33712c3ba02b0664ec4c44e48a9ff'),
    'runtime_receipt': ('provenance/base77/RUNTIME_RECEIPT.json', '0aa1fe161db91733bcd157f6102af5adc33d46de2d6355d4bd7eae563273e77e')}
RUNTIME79_ADDITIONS = (
    {'name':'markdown-it-py','version':'4.0.0','filename':'markdown_it_py-4.0.0-py3-none-any.whl',
     'sha256':'87327c59b172c5011896038353a81343b6754500a08cd7a4973bb48c6d578147','bytes':87321},
    {'name':'mdurl','version':'0.1.2','filename':'mdurl-0.1.2-py3-none-any.whl',
     'sha256':'84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8','bytes':9979})
RUNTIME79_EVIDENCE = {
    'PIP_REPORT':'provenance/runtime79-pip-report.json',
    'INSTALL_LOG':'provenance/runtime79-install.log',
    'INSTALL_EXIT':'provenance/runtime79-install-exit.json',
    'COPY_RECEIPT':'provenance/runtime79-copy-receipt.json',
    'SMOKE':'provenance/runtime79-smoke.json'}


def ide_companion_metadata(names):
    """Declare bundled interfaces, never startup readiness or Windows qualification.

    Older structural plans/packages without IDE support remain readable. A
    partially copied new interface cannot acquire a capability declaration.
    """
    names = set(names)
    if not names.intersection(IDE_COMPANION_FILES[:2]):
        return None
    if not set(IDE_COMPANION_FILES).issubset(names):
        raise PackageError('IDE companion interface payload is incomplete')
    return {'protocol':'eeas.ide-companion.v1', 'background_bootstrap':True,
        'discovery_relative_path':'user-data/ide-companion/discovery.json',
        'transport':'MAIN_OWNED_LOOPBACK_HTTP', 'external_agent_owner':'DESKTOP_EEAS_ONLY'}


def package_launchers():
    # pythonw owns the visible verification UI. Do not leave a blank console
    # covering the app while its bundled files and local service start.
    return {'START_EEAS.cmd': b'@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nstart "" "%~dp0runtime\\python\\pythonw.exe" -B "%~dp0scripts\\native_package_bootstrap.py" --electron\r\nexit /b\r\n',
        'MAINTAIN_CIRVEN.cmd': b'@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nstart "" "%~dp0runtime\\python\\pythonw.exe" -B "%~dp0scripts\\native_maintenance.py"\r\nexit /b\r\n'}


def package_instructions():
    """Current portable instructions, not the historical .venv/Web handoff."""
    source_root = Path(__file__).resolve().parents[1]
    brand = json.loads((source_root / 'packages/branding/product.json').read_text('utf-8'))['display_name']
    readme = r'''# EEAS Windows 内网初版：先读这里

这是 Windows x64 桌面 App 的开发预览包，使用原 OpenCode 1.19.0。
包内包含 Python 3.10、Electron、依赖、模型辅助文件和原组件许可。
当前包的生产文件与哈希见 PACKAGE_MANIFEST.json；校验哈希代表字节完整性，不是发布者签名。

## 放置与启动

1. 将整个包解压或复制到有写入权限的 D: 或 E: 普通目录。例如
   `D:\EEAS\EEAS 内网初版 20261015`；目录可以包含空格和中文。
   保留所有相对目录，不单独移动 START_EEAS.cmd 或 runtime。
2. 双击根目录的 `START_EEAS.cmd`。无需另装 Python、Node、Electron，
   用户无需运行 pip/npm 安装，也不要求先设置环境变量。启动会显示安装包校验进度，随后显示本地服务的加载页面；首次启动可能需要约一分钟。
3. 首次进入软件，可在首页选择“账号登录”或“配置 API / 内网模型”；之后也可从输入框的模型选择菜单打开“配置连接”。

默认本机数据、日志、临时文件与配置位于本包的 `user-data` 中；首次使用时创建。
退出 App 后再移动目录。此分发包只含 manifest 登记的生产文件，不含开发者历史、账号缓存或 QA 数据。
本机维护使用根目录的 `MAINTAIN_CIRVEN.cmd`，打开后选择“创建备份”“校验备份”或“恢复备份”。
备份前从托盘正常退出当前安装，再选择存放位置；工具自动创建新的私有目录，保留历史、草稿与账号文件。
恢复时将完整分发包解压为新副本，先运行维护工具并选择已有备份，再首次启动 App 检查历史与草稿。
恢复仅限同一 Windows 用户本机，已有用户数据不覆盖；备份含账号资料，不能作为干净软件包对外分发。
校验通过不等于账号服务可用；恢复后的桌面完整流程与授权须按验收记录单独检查。
窗口只启用当前状态可用的操作；备份或恢复成功后，可点“打开 App”继续。
若要使用已保留的另一程序版本，可点维护窗口下方“程序回退”，选择完整程序目录和私有备份。
目标须尚无用户数据且未运行；兼容核对通过后，确认恢复，再点“打开目标 App”。
核对与恢复绑定同一程序和快照；不兼容或中途变化会拒绝，不覆盖当前安装，也不逆向修改数据库。
不要覆盖正在运行的目录。安装位置或外部 MCP 文件位置变动后，需要核对对应绝对路径配置。

## 第一次连接内网模型

打开输入框的模型选择菜单，再点“配置连接”，填写“连接标识”“API 地址”“模型标识”，选择 API Key 或免 API Key 认证，
点“保存连接”，再在“模型连接”列表选择它。具体格式和验证步骤见 [INTRANET_SETUP.md](INTRANET_SETUP.md)。
保存显示 `NOT_VALIDATED` 表示配置已保存，尚未证明服务可用；
选中该连接后，在当前聊天输入一个小型只读请求，查看实际结果；配置保存和真实推理分别验收。
此包没有自动切换到外部付费 API 的后备逻辑。

## 外网使用 ChatGPT Pro 订阅

在首页或“配置连接”的“使用 ChatGPT 订阅”中点击“登录 ChatGPT 账号”。
在系统浏览器的 OpenAI 官方页面完成登录；App 自动等待授权结果，不需要手动点击完成。
登录后选择原 OpenCode 返回的模型目录项，点击“使用此订阅模型”，检查输入框旁已选中该连接。
这是账号的 Codex 订阅用量路径，不需要 API Key，也不会自动切换到付费 API。
订阅用量和可用模型受账号权益限制，模型目录本身不证明实际推理可用。
登录记录只保存在本安装自己的 user-data 目录，不随干净安装包分发。
首次登录和实际推理需在外网完成，严格内网继续使用上面的本地模型或内网网关连接。

## 内置与自己的技能

从左侧“工具与设置 → 管理工具与 MCP”打开能力管理，选择“技能”或“MCP”分类。
首次启动会将以下 11 份受审原始 SKILL.md 按原字节导入本机注册表，声明启用：

- eeas-acceptance-review：验收审阅
- eeas-clarify-gaps：澄清缺口
- competitive-evidence：竞品资料与证据
- diagram-evidence：图示证据
- engineering-requirements：工程要求
- needs-elicitation：需求访谈
- problem-discovery：问题探索
- prompt-orchestration：提示与协作编排
- eeas-report-handoff：报告与交接
- eeas-source-review：来源审阅
- eeas-task-decomposition：任务拆解

已有同 ID、人工修改或停用记录会保留；升级不会静默覆盖或重新启用。
原件损坏、缺失或安装回执异常会报告缺口，不以静态工具目录冒充导入成功。
点击已有技能可查看原文；导入自己的原件时，在“技能”分类点“导入 Skill”。
本轮导入 UTF-8 SKILL.md 文本，不自动导入其 scripts、references、依赖、安装钩子或目录包。

## 原 OpenCode 会话与 MCP

日常工作直接在当前聊天中选择模型、添加资料并输入；不必先打开会话管理页。
需要原 OpenCode 专用会话时，从左侧“会话管理”进入；该入口提供会话的创建、读取、重新加载、停止及问题/权限回复。专用纯文字会话不自动继承当前页面或所选文件。
选择模型连接后创建会话，按需要选择 Plan/Build。Skill 与 MCP 的“启用声明”“会话已加载”
和“实际调用”是不同状态。变更配置后创建新会话并核对实际能力回执；
“重新连接”仅在原配置仍有效时恢复该会话，不会把新配置悄悄替换进旧会话。

自己的 MCP stdio 程序先准备完整离线可运行包，再在“MCP”分类点“新增 MCP”，填写真实参数数组和文件 SHA256。
“连接并读取工具”会实际启动该程序、初始化并读取工具列表，但不调用工具。
工具执行需要原会话中的明确权限和真实执行结果；具体步骤及当前限制见 [INTRANET_SETUP.md](INTRANET_SETUP.md)。

## 当前验证边界

不改变桌面分辨率或 DPI，不预留 AppBar 区域，不重排其他软件窗口。
页面观察、明确选定文件的读取和产物在原软件打开，与任意软件的编辑能力分别验证。
此包未取得普适原生输入/坐标控制资格；单文件修改仅限界面明确显示的已接入格式和大小范围。
组件导入、MCP 握手、协议夹具模型回应及自身文件样例通过，不等于公司模型、任意业务软件或企业环境已验收。
ChatGPT Pro/订阅登录、本机 OS 网络沙箱、任意第三方 MCP 程序行为没有随包取得资格。

包内 App 冷启动与内置组件不需要下载。模型请求可访问用户明确配置的内网服务；
第三方工具与原程序的自身网络行为需分别验证，此包不声称操作系统已阻断外网。
严格断外网时 ChatGPT Pro 不可用，应明确配置可达的内网/本地模型。
运行异常先保留窗口中的错误与实际回执、`user-data` 日志；无需重新联网安装组件。
'''
    intranet = r'''# 内网模型、Skill 与本地 MCP 设置

本说明对应 START_EEAS.cmd 启动的 Windows 桌面预览包。
无需安装脚本、.venv 或浏览器入口；已有历史交付说明不适用于本包。

## 模型连接

打开输入框的模型选择菜单，点“配置连接”后填写：

| 字段 | 当前要求 |
| --- | --- |
| 连接标识 | 本机唯一标识，例如 intranet-model |
| API 地址 | HTTP/HTTPS 的 OpenAI-compatible Chat Completions 地址，必须以 /v1 结尾；不在 URL 中放凭证、查询参数或片段 |
| 模型标识 | 服务实际提供的完整 model ID；界面营销名称不能代替该 ID |
| 认证方式 | API Key 或明确免 API Key；API Key 模式填写服务发放的实际密钥，不读取环境变量 |
| 高级连接 | 可明确配置私有 CA、代理、私密或普通请求头及请求超时；省略时使用系统 CA、直连与默认超时 |
| 图片输入 | 只有服务实际支持时勾选；勾选是用户声明，服务响应仍须验证 |

示例地址 `http://intranet-host:8000/v1` 只是格式示例，不是包内存在的服务。
支持明确免密、Bearer、自定义认证请求头、PEM CA 与 HTTP/HTTPS 代理。具体公司网关仍需逐台验证；其他模型协议单独适配。
高级设置在修改时完整替换。旧密钥和私密请求头不回填；编辑已有高级连接时，要明确重填或重置，避免误清配置。
CA 可在本机选择 PEM 文件或粘贴证书；本地服务保存证书，不向模型发送。TLS 校验始终开启。
没有隐式环境代理、环境密钥、跨地址重定向或付费后备。保存失败或超时后先回查原操作，不自动重复提交。
当前设置保存新增/修订版本；没有专门的连通性测试、删除连接或 EEAS 自建 OAuth 登录界面。
保存不会请求模型，也不证明可达。选择保存的连接后，在当前聊天中输入并发送一个不带文件、不修改内容的简短文字请求，
核对实际完成或错误回执、准确模型 ID 和服务端实际响应。
图片、工具调用、上下文长度和公司服务计费规则需要另验。
凭证保存在安装目录的私有本机数据中，不返回到配置列表或生产分发压缩包；保护该用户数据的访问权限。

此包不会默认配置 ChatGPT Pro，不导入开发者订阅缓存，也不会暗切付费 API。
原 OpenCode 的官方订阅授权与 EEAS 自建 OAuth 客户端是不同集成；此版本未完成桌面授权资格验收。
严格断外网的环境不能使用 Pro；请用户明确配置可达的内网或本地服务，不会自动替换模型或计费方式。

## 导入自己的 Skill

从左侧“工具与设置 → 管理工具与 MCP”进入“技能”，点“导入 Skill”，选择原始 `SKILL.md` 或粘贴原文。
文件要求 1–65536 字节 UTF-8 文本，开头包含 YAML 元数据，例如：

```markdown
---
name: my-review
description: 审阅选定材料并返回有来源的修改建议。
---
先核对用户提供的材料和范围，再进行审阅；缺少依据时说明缺口。
```

“Skill 标识”应与原文 name 完全相同，使用小写字母、数字和连字符。
按需勾选“启用”后点“导入原文”；保存后检查版本与来源完整性。
点击已有技能可编辑原文并保存新版本；关闭启用开关保留原件和历史，不授权执行。
导入不会运行脚本、装依赖、启动 MCP 或请求模型；外部脚本、资料目录与目录包尚不自动导入。
内置 11 份技能的来源原件位于 `packages/planning/skills/*/SKILL.md`，注册记录在本机数据内。
同 ID 用户记录和停用状态优先保留，损坏原件不会自动修复或覆盖。

## 导入本地 MCP stdio 程序

1. 选择已有的供应方完整 MCP 程序及其所需运行时和依赖。
   可使用自包含 exe，或供应方提供的本机运行时与脚本；不要使用会下载的 npx/pip/uv 启动形式。
   本包的 Python bootstrap 只开放自己的已登记模块，不是任意第三方 Python 包安装或启动器。
2. 从左侧“工具与设置 → 管理工具与 MCP”进入“MCP”，点“新增 MCP”，选择“本地 stdio 进程”，填写唯一 MCP 标识。
   `eeas_desktop` 是 App 保留的执行命名空间；不同服务标识也不能发生下划线分隔的前缀歧义。
3. “命令参数数组（JSON）”写绝对可执行文件路径和参数，不写一整条 shell 命令。
   “文件路径与 SHA256（JSON）”至少登记可执行文件和所有作为绝对文件参数的脚本/配置。
   每个文件须实际存在且是普通文件，SHA256 使用实际小写 64 位摘要；不是占位字符串。

例如可执行程序已存在于 `D:\MyMCP\server.exe` 时，命令数组的格式为：

```json
["D:\\MyMCP\\server.exe", "--stdio"]
```

在 PowerShell 使用以下命令获取实际文件摘要，再填写以完整绝对路径为键的 JSON 对象：

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath 'D:\MyMCP\server.exe').Hash.ToLowerInvariant()
```

4. 设连接超时 100–120000 毫秒，首次连接建议 60000 毫秒，再按需勾选“启用”并“保存配置”。
   真实 Windows 包的冷启动握手曾超过 10 秒；60000 毫秒也是当前界面的默认值。
   当前只接收参数数组、文件哈希、启用状态与超时；不导入环境变量或凭证。
5. 对保存记录点“连接并读取工具”。它会实际运行本地程序，完成 MCP initialize/list_tools 后关闭探测子进程；
   该步骤不调用任何工具。程序启动本身可有副作用，握手成功不代表业务工具已执行或离线行为合格。
6. 创建原会话时按当前配置核对真实会话加载状态。需要本地 MCP 时，明确允许启动本地进程；
   需要工具时填写明确工具名的白名单，按实际权限问题回复，并查看真正调用的回执。
   配置改变后应创建新会话，旧探测或旧会话结果不会证明新版本可用。

移动程序目录后必须按新路径更新配置和摘要。文件摘要校验不覆盖第三方程序的一切动态依赖或自身行为。
当前不自动解压、安装或导入 npm/Python 依赖包；程序应在无下载条件下完整运行。
远程 HTTPS MCP 可保存端点声明；管理面板没有远程连接/认证探测。
原会话的远程无认证连接还需明确允许和真实状态核验；OAuth、认证 Header、环境凭证导入不在本轮接口中。

## 验证状态与权限

“配置已保存”“声明已启用”“初始化并读取工具”“当前原会话已加载”“有真实调用”分别有独立回执。
导入 Skill 或保存 MCP 不自动授予工具、文件写入、进程启动或模型请求权限。
原会话提供 Plan/Build、原 Task/subagent、问题和权限回复、事件读取及停止接口；
是否可用看当前会话真实工具清单和权限。此原会话入口现在是纯文字，当前页面和所选文件不会自动继承。
目录权限、工具权限或目标身份不足时保留未适配/拒绝状态，不用预览截图证明任意程序可精确编辑。

包内 Python/Electron/OpenCode/ripgrep/字体/OCR 等实际文件已随包提供，冷启动不下载缺失组件。
无模型配置时保持未配置；没有公司模型的成功回执时不宣称公司服务通过。
网络模式显示与工具准入不是 OS 级网络沙箱；第三方程序和用户配置的模型服务须单独验证网络行为。
对企业环境验收应记录具体机器、包 manifest SHA、目标程序、模型实际 ID、请求结果与允许的权限范围。
不要把组件导入、协议夹具响应或自身文件样例回执当作企业普适资格。
'''
    readme = readme.replace('# EEAS Windows', '# ' + brand + ' Windows')
    readme = readme.replace('EEAS 自建', brand + ' 自建')
    intranet = intranet.replace('EEAS 自建', brand + ' 自建')
    readme = readme.replace('\n\n', '\n\n[English](README_FIRST.en.md) · [内网设置](INTRANET_SETUP.md)\n\n', 1)
    intranet = intranet.replace('\n\n', '\n\n[English](INTRANET_SETUP.en.md) · [启动说明](README_FIRST.md)\n\n', 1)
    language_help = '\n## 界面语言\n\n在设置中可选跟随系统、简体中文或 English。切换保留当前任务、输入与草稿；浮窗、浮标、托盘与后续启动提示共享桌面偏好。VS Code 插件默认跟随 VS Code，可单独选择。用户正文、文件名、模型回答与历史报告不随界面语言翻译。\n'
    result = {INSTRUCTION_FILES[0]: (readme + language_help).encode('utf-8'),
        INSTRUCTION_FILES[1]: (intranet + language_help).encode('utf-8')}
    for target, source in [(INSTRUCTION_FILES[2], 'portable-help.en.md'), (INSTRUCTION_FILES[3], 'intranet-setup.en.md')]:
        text = (source_root / 'packages/i18n' / source).read_text('utf-8')
        if target == INSTRUCTION_FILES[2]:
            text += ('\n## Use a retained program version\n\n'
                'In maintenance, choose Other version, then select the complete retained program and a private backup. '
                'The target must be stopped with no user-data folder. Review the compatibility check, confirm restoration, '
                'then choose Open target app. The check, restore, and launch bind the same program version and snapshot. '
                'Incompatible or changed targets are refused. The current installation stays intact and databases are never reverse-migrated.\n')
        result[target] = text.replace('{product_display_name}', brand).encode('utf-8')
    return result


def instruction_manifest():
    return {'version': INSTRUCTION_VERSION, 'files': list(INSTRUCTION_FILES),
        'scope': 'CURRENT_PORTABLE_ENTRYPOINT_AND_ACTUAL_CONFIGURATION_BOUNDARIES'}


def normalized(name):
    return re.sub('[-_.]+', '-', name).lower()


def files_below(root):
    if root.is_symlink():
        raise PackageError('Source directory is a symlink')
    for directory, names, files in os.walk(root, followlinks=False):
        for name in list(names):
            path = Path(directory) / name
            if path.is_symlink():
                raise PackageError('Source contains a directory link')
        names[:] = sorted(name for name in names if name not in EXCLUDE_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                raise PackageError('Source contains a file link')
            if path.suffix.lower() in {'.pyc', '.pyo'} or name in {'sitecustomize.py', 'usercustomize.py'}:
                continue
            yield path


def check_record(site_dir, distribution):
    record = distribution / 'RECORD'
    if not record.is_file():
        raise PackageError('Installed distribution has no RECORD')
    count = 0
    for name, encoded, size in csv.reader(io.StringIO(record.read_text('utf-8'))):
        if not encoded or name.endswith(('.pyc','.pyo')):
            continue
        member = (site_dir / name).resolve()
        # pip --target retains scheme-relative RECORD names although the files
        # are moved below target/bin and target/share. Verify the actual moved
        # bytes, not a guessed success or a path outside the selected payload.
        if name.startswith(('../../bin/', '../../share/')):
            member = (site_dir / name[6:]).resolve()
        if not member.is_relative_to(site_dir.resolve()) or not member.is_file() or member.is_symlink():
            raise PackageError('Installed RECORD is missing or escapes selected site-packages: ' + name)
        if not encoded.startswith('sha256='):
            raise PackageError('Unknown installed RECORD hash')
        expected = base64.urlsafe_b64decode(encoded[7:] + '=' * (-len(encoded[7:]) % 4)).hex()
        if sha(member) != expected or (size and member.stat().st_size != int(size)):
            raise PackageError('Installed RECORD bytes changed: ' + name)
        count += 1
    return count


def _runtime79_file(root, name, expected_sha=None, maximum=16*1024*1024):
    path = relative_path(root, name)
    if (not path.is_file() or path.is_symlink() or path.stat().st_size > maximum
            or (expected_sha is not None and sha(path) != expected_sha)):
        raise PackageError('Runtime79 provenance is missing, linked or changed: ' + name)
    return path


def _runtime79_admission(root, closure):
    """One exact additive closure, never permission for arbitrary package counts.

    Installation receipts are bound evidence, not a Windows qualification signed
    by this file auditor. Existing RECORD/wheel/executable/engine checks still run.
    """
    try:
        extension_path = _runtime79_file(root, 'runtime-extension-windows310.json', maximum=256*1024)
        extension = json.loads(extension_path.read_bytes())
        if (set(extension) != {'schema','target','parent','additions','installation_receipt'}
                or extension['schema'] != 'eeas.runtime-extension-windows310.v1'
                or extension['target'] != 'windows-x64-py310'
                or extension['additions'] != list(RUNTIME79_ADDITIONS)
                or set(extension['parent']) != set(RUNTIME79_PARENT)):
            raise ValueError('Extension declaration differs')
        provenance = [{'source_relative':'runtime-extension-windows310.json',
                       'package_relative':'provenance/runtime-extension-windows310.json',
                       'sha256':sha(extension_path),'bytes':extension_path.stat().st_size}]
        parent_bytes = {}
        for key, (name, expected_sha) in RUNTIME79_PARENT.items():
            if extension['parent'][key] != {'path':name,'sha256':expected_sha}:
                raise ValueError('Parent reference differs')
            path = _runtime79_file(root, name, expected_sha)
            parent_bytes[key] = path.read_bytes()
            provenance.append({'source_relative':name,'package_relative':name,'sha256':expected_sha,'bytes':path.stat().st_size})
        parent = json.loads(parent_bytes['closure'])
        original = {normalized(p['name']):p for p in parent['packages']}
        selected = {normalized(p['name']):p for p in closure['packages']}
        if (parent['target'] != 'windows-x64-py310' or len(original) != 77
                or len(selected) != 79 or len(closure['packages']) != 79
                or set(selected) != set(original) | {p['name'] for p in RUNTIME79_ADDITIONS}
                or any(selected[name] != item for name,item in original.items())
                or closure.get('parent_closure_sha256') != RUNTIME79_PARENT['closure'][1]
                or closure.get('parent_root_requirements_sha256') != parent['root_requirements_sha256']):
            raise ValueError('The exact parent77 records must remain unchanged')
        for key in ('target','marker_environment','enabled_extras','explicit_exclusions'):
            if closure.get(key) != parent.get(key):
                raise ValueError('The parent dependency policy changed')
        edge = {'from':'markdown-it-py','requires':'mdurl~=0.1','resolved':'mdurl==0.1.2'}
        if closure['dependency_edges'] != parent['dependency_edges'] + [edge]:
            raise ValueError('Only the actual new runtime dependency is admitted')
        if sha(root/'RUNTIME_RECEIPT.json') != RUNTIME79_PARENT['runtime_receipt'][1]:
            raise ValueError('Original executable receipt must remain unchanged')
        lock = ''.join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n"
                       for p in sorted(closure['packages'],key=lambda p:normalized(p['name'])))
        if (_runtime79_file(root,'requirements-focused-native-windows310.lock').read_bytes() != lock.encode()
                or closure.get('root_requirements_sha256') != hashlib.sha256(lock.encode()).hexdigest()):
            raise ValueError('The exact79 hash-locked requirements differ')
        added_metadata = {}
        for wanted in RUNTIME79_ADDITIONS:
            item = selected[wanted['name']]
            if any(item.get(k) != v for k,v in wanted.items()):
                raise ValueError('Only the two pinned official additions are admitted')
            wheel = _runtime79_file(root,'toolchain/runtime-wheels/'+wanted['filename'],wanted['sha256'])
            if wheel.stat().st_size != wanted['bytes']:
                raise ValueError('Added wheel size differs')
            with zipfile.ZipFile(wheel) as archive:
                prefix = wanted['filename'].split('-')[0]+'-'+wanted['version']+'.dist-info/'
                raw = archive.read(prefix+'METADATA')
                metadata = BytesParser().parsebytes(raw)
                wheel_metadata = BytesParser().parsebytes(archive.read(prefix+'WHEEL'))
                licenses = [{'member':n,'bytes':len(archive.read(n)),
                             'sha256':hashlib.sha256(archive.read(n)).hexdigest()}
                            for n in archive.namelist() if n.startswith(prefix) and 'LICENSE' in n.upper()]
                # pip rewrites RECORD and adds installer/script files. Every
                # original payload member must nevertheless retain wheel bytes.
                for name in archive.namelist():
                    if name.endswith('/') or name == prefix+'RECORD':
                        continue
                    installed_path = _runtime79_file(root,'runtime/site-packages/'+name)
                    if installed_path.read_bytes() != archive.read(name):
                        raise ValueError('Installed addition differs from its official wheel')
            if (normalized(metadata['Name']) != wanted['name'] or metadata['Version'] != wanted['version']
                    or wheel_metadata.get_all('Tag') != ['py3-none-any']
                    or item.get('tags') != ['py3-none-any']
                    or item.get('requires_dist') != metadata.get_all('Requires-Dist',[])
                    or item.get('requires_python') != metadata['Requires-Python']
                    or item.get('licenses') != licenses or not licenses):
                raise ValueError('Added wheel metadata or license provenance differs')
            added_metadata[wanted['name']] = hashlib.sha256(raw).hexdigest()
        ref = extension['installation_receipt']
        if set(ref) != {'path','sha256'} or ref['path'] != 'provenance/runtime79-installation.json' or not re.fullmatch('[0-9a-f]{64}',ref['sha256']):
            raise ValueError('Installation receipt reference differs')
        install_path = _runtime79_file(root,ref['path'],ref['sha256'],256*1024)
        installation = json.loads(install_path.read_bytes())
        py = installation['python'];command = installation['command']
        python_sha = next(p['executable_sha256'] for p in json.loads(parent_bytes['runtime_receipt'])['records'] if p['component']=='python')
        if (installation['schema'] != 'eeas.runtime79-installation.v1'
                or installation['target'] != 'windows-x64-py310'
                or installation['status'] != 'WINDOWS_OFFLINE_STAGE_INSTALLED_AND_VERIFIED'
                or not isinstance(py['version'],list) or len(py['version']) != 3
                or not all(type(v)is int and v>=0 for v in py['version']) or py['version'][:2] != [3,10]
                or py['machine'] != 'AMD64' or py['executable_sha256'] != python_sha
                or not isinstance(command,list) or not all(isinstance(v,str) for v in command)
                or not PureWindowsPath(command[0]).is_absolute() or PureWindowsPath(command[0]).name.lower() != 'python.exe'
                or not {'-I','-B','-m','pip','--isolated','install','--no-index','--no-deps','--no-compile','--require-hashes','--no-cache-dir','--only-binary=:all:'}.issubset(command)
                or command.index('-B') > command.index('-m')
                or command[command.index('-m')+1] != 'pip'
                or any(v in {'--upgrade','-U','--extra-index-url','--index-url'} or v.lower().startswith(('http:','https:')) for v in command)):
            raise ValueError('Actual Windows offline installation facts are incomplete')
        installed = {p['name']:p for p in installation['installed']}
        if len(installation['installed']) != 2 or set(installed) != set(added_metadata):
            raise ValueError('Installation must describe exactly the two additions')
        for wanted in RUNTIME79_ADDITIONS:
            row = installed[wanted['name']]
            if (row['version'] != wanted['version'] or row['metadata_sha256'] != added_metadata[wanted['name']]
                    or type(row['record_files_checked'])is not int or row['record_files_checked'] <= 0):
                raise ValueError('Installed addition evidence differs')
            folder = root/'runtime/site-packages'/(wanted['filename'].split('-')[0]+'-'+wanted['version']+'.dist-info')
            if (sha(folder/'METADATA') != row['metadata_sha256']
                    or check_record(root/'runtime/site-packages',folder) != row['record_files_checked']):
                raise ValueError('Actual installed addition RECORD/metadata differs')
        evidence = installation['evidence']
        if len(evidence) != len(RUNTIME79_EVIDENCE) or {v['role'] for v in evidence} != set(RUNTIME79_EVIDENCE):
            raise ValueError('Installation evidence is incomplete')
        for row in evidence:
            if (set(row) != {'role','path','sha256','bytes'} or row['path'] != RUNTIME79_EVIDENCE[row['role']]
                    or type(row['bytes'])is not int or row['bytes']<0 or not re.fullmatch('[0-9a-f]{64}',row['sha256'])):
                raise ValueError('Installation evidence path or hash differs')
            path = _runtime79_file(root,row['path'],row['sha256'])
            if path.stat().st_size != row['bytes']:
                raise ValueError('Installation evidence size differs')
            provenance.append({'source_relative':row['path'],'package_relative':row['path'],'sha256':row['sha256'],'bytes':row['bytes']})
        report = json.loads(_runtime79_file(root,RUNTIME79_EVIDENCE['PIP_REPORT']).read_bytes())
        pip_rows = report['install']
        if len(pip_rows) != 2 or {normalized(p['metadata']['name']) for p in pip_rows} != set(added_metadata):
            raise ValueError('Actual pip report membership differs')
        for row in pip_rows:
            wanted = next(p for p in RUNTIME79_ADDITIONS if p['name']==normalized(row['metadata']['name']))
            url = urlsplit(row['download_info']['url'])
            if (url.scheme != 'file' or url.netloc or url.query or url.fragment
                    or unquote(url.path).replace('\\','/').split('/')[-1] != wanted['filename']
                    or row['metadata']['version'] != wanted['version']
                    or row['download_info']['archive_info']['hashes']['sha256'] != wanted['sha256']):
                raise ValueError('Actual pip report is not the exact local wheel installation')
        exit_evidence = json.loads(_runtime79_file(root,RUNTIME79_EVIDENCE['INSTALL_EXIT']).read_bytes())
        if type(exit_evidence.get('exit_code'))is not int or exit_evidence['exit_code'] != 0:
            raise ValueError('The recorded offline installer did not exit zero')
        provenance.append({'source_relative':ref['path'],'package_relative':ref['path'],
                           'sha256':ref['sha256'],'bytes':install_path.stat().st_size})
        return {'provenance_files':provenance,'addition_installation':installed,
                'runtime_extension':{'schema':extension['schema'],'parent_closure_sha256':RUNTIME79_PARENT['closure'][1],
                    'manifest_sha256':sha(extension_path),'installation_receipt_sha256':ref['sha256'],
                    'qualification':'BOUND_INSTALLATION_EVIDENCE_NOT_WINDOWS_QUALIFICATION'}}
    except (OSError,ValueError,TypeError,KeyError,StopIteration,IndexError,zipfile.BadZipFile) as exc:
        raise PackageError('The exact runtime79 extension/provenance failed: '+str(exc)) from exc


def audit_selected_runtime(root):
    root = Path(root).resolve()
    closure_path = root / 'dependency-closure-windows310.json'
    closure = json.loads(closure_path.read_bytes())
    count = len(closure.get('packages', []))
    if closure.get('target') != 'windows-x64-py310' or count not in (77,79):
        raise PackageError('The selected closure requires original77 or the exact admitted79 extension')
    extension = _runtime79_admission(root,closure) if count == 79 else {}
    expected = {normalized(p['name']): p for p in closure['packages']}
    if len(expected) != count or 'claude-agent-sdk' in expected:
        raise PackageError('Duplicate/Claude dependency in focused closure')
    installed, rows = {}, []
    site_dir = root / 'runtime/site-packages'
    for folder in sorted(site_dir.glob('*.dist-info')):
        metadata = BytesParser().parsebytes((folder / 'METADATA').read_bytes())
        name, version = normalized(metadata['Name']), metadata['Version']
        if name in installed:
            raise PackageError('Duplicate installed distribution')
        installed[name] = version
        if name not in expected or version != expected[name]['version']:
            raise PackageError('Installed dependency differs from selected closure: ' + name)
        rows.append({'name': name, 'version': version, 'record_files_checked': check_record(site_dir, folder),
            'metadata_sha256': sha(folder / 'METADATA')})
    if set(installed) != set(expected):
        raise PackageError('Selected dependency installation is incomplete')
    for name, value in extension.get('addition_installation',{}).items():
        observed = next(row for row in rows if row['name']==name)
        if any(observed[key] != value[key] for key in ('version','metadata_sha256','record_files_checked')):
            raise PackageError('Actual installed runtime79 RECORD/metadata differs from its installation receipt')
    wheels = []
    for item in closure['packages']:
        filename = item['filename']
        if Path(filename).name != filename:
            raise PackageError('Wheel filename escapes selected directory')
        path = root / 'toolchain/runtime-wheels' / filename
        if not path.is_file() or sha(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise PackageError('Selected offline wheel bytes changed: ' + filename)
        wheels.append({'path': path, 'relative': 'toolchain/runtime-wheels/' + filename})
    receipt = json.loads((root / 'RUNTIME_RECEIPT.json').read_bytes())
    for component in ('python', 'electron'):
        records = [r for r in receipt['records'] if r['component'] == component]
        if len(records) != 1 or sha(root / ENTRYPOINTS[component]) != records[0]['executable_sha256']:
            raise PackageError('Selected executable differs from its original receipt: ' + component)
    component_path = root / ENTRYPOINTS['component_manifest']
    if sha(component_path) != receipt['component_manifest_sha256']:
        raise PackageError('Selected Cua component manifest changed')
    component = json.loads(component_path.read_bytes())
    for wheel in component['wheels']:
        for member, expected_sha in wheel['engine_files'].items():
            path = relative_path(site_dir, member)
            if not path.is_file() or sha(path) != expected_sha:
                raise PackageError('Installed native component engine differs from pinned original')
    return {'target':'windows-x64-py310', 'dependency_count':len(rows), 'installed':rows,
        'closure_sha256':sha(closure_path), 'component_manifest_sha256':sha(component_path),
        'claude_agent_sdk':'ABSENT', 'qualification':'ACTUAL_FILES_RECORDS_WHEELS_ONLY_WINDOWS_EXECUTION_NOT_RUN',
        'wheel_jobs':wheels, **{k:v for k,v in extension.items() if k!='addition_installation'}}


def installed_record_inventory(root, audit):
    """Select the audited distributions, including their registered test files.

    The source-code walker deliberately prunes tests. Dependency RECORDs are a
    different boundary: every hashed member and the RECORD itself must travel
    together; adjacent unregistered files and generated bytecode do not.
    """
    site = Path(root).resolve() / 'runtime/site-packages'
    expected = {row['name'] for row in audit['installed']}
    observed, files, aliases = set(), {}, {}
    for folder in sorted(site.glob('*.dist-info')):
        metadata = BytesParser().parsebytes((folder/'METADATA').read_bytes())
        name = normalized(metadata['Name'])
        if name not in expected or name in observed:
            raise PackageError('Installed RECORD selection differs from the audited closure')
        observed.add(name)
        record = folder/'RECORD'
        members = [(record.relative_to(site).as_posix(), None, '')]
        try:
            for member, encoded, size in csv.reader(io.StringIO(record.read_text('utf-8'))):
                if member.endswith(('.pyc','.pyo')):
                    continue
                if member.startswith(('../../bin/','../../share/')):
                    member = member[6:]
                if not encoded:
                    if member != record.relative_to(site).as_posix():
                        raise PackageError('Unhashed dependency member is not its own RECORD')
                    continue
                if not encoded.startswith('sha256='):
                    raise PackageError('Unknown dependency RECORD hash')
                digest = base64.urlsafe_b64decode(encoded[7:]+'='*(-len(encoded[7:])%4)).hex()
                if not re.fullmatch('[0-9a-f]{64}',digest):
                    raise PackageError('Invalid dependency RECORD hash')
                members.append((member,digest,size))
        except (OSError,ValueError,TypeError) as exc:
            raise PackageError('Installed dependency RECORD selection failed') from exc
        for member,digest,size in members:
            path = relative_path(site,member)
            if not path.is_file() or path.is_symlink() or (size and path.stat().st_size != int(size)):
                raise PackageError('Installed dependency member is missing, linked or changed')
            target = 'runtime/site-packages/'+member
            pin = {'source':path,'sha256':digest or sha(path),'bytes':path.stat().st_size}
            if target.casefold() in aliases and aliases[target.casefold()] != target:
                raise PackageError('Windows-aliased dependency RECORD member')
            if target in files and files[target] != pin:
                raise PackageError('Dependency RECORDs disagree about a shared file')
            aliases[target.casefold()] = target
            files[target] = pin
    if observed != expected:
        raise PackageError('Audited distribution disappeared while selecting RECORD members')
    return {'files':files,'distribution_count':len(observed)}


def ocr_runtime_inventory(manifest_path, runtime_root):
    """Read every pinned Windows OCR engine member, including wheel tests.

    Source-tree pruning is not a dependency-file policy. The unchanged OCR
    manifest, rather than directory names, determines this exact closure.
    """
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024:
        raise PackageError('OCR engine manifest missing, linked or oversized')
    try:
        raw = manifest_path.read_bytes()
        wrapper = json.loads(raw)
        manifest_sha = hashlib.sha256(raw).hexdigest()
        payload = wrapper['payload']
        actual = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,
            separators=(',', ':'),allow_nan=False).encode('utf-8')).hexdigest()
        if (set(wrapper) != {'payload','sha256'} or wrapper['sha256'] != actual
                or payload['schema_version'] != 'eeas.offline-ocr-models.v1'):
            raise ValueError()
        packages = payload['engine_files']['windows']
        if not isinstance(packages,dict) or not packages:
            raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise PackageError('OCR engine manifest integrity failed') from None
    site_dir = Path(runtime_root).resolve() / 'runtime/site-packages'
    files,seen = {},set()
    for package, members in sorted(packages.items()):
        if not isinstance(package,str) or not package or not isinstance(members,dict):
            raise PackageError('Invalid OCR engine package members')
        for member, expected in sorted(members.items()):
            if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected):
                raise PackageError('Invalid pinned OCR engine hash')
            source = relative_path(site_dir,member)
            name = 'runtime/site-packages/' + member
            if name.casefold() in seen:
                raise PackageError('Windows-aliased OCR engine member')
            seen.add(name.casefold())
            if not source.is_file() or sha(source) != expected:
                raise PackageError('Selected OCR engine file missing or changed: ' + member)
            files[name] = {'source':source,'sha256':expected,'bytes':source.stat().st_size}
    if not files:
        raise PackageError('OCR engine closure is empty')
    if sha(manifest_path) != manifest_sha:
        raise PackageError('OCR engine manifest changed while selecting its closure')
    return {'manifest_sha256':manifest_sha,'payload_sha256':actual,
        'member_count':len(files),'package_count':len(packages),'files':files}


def ocr_inventory_summary(inventory):
    return {key:inventory[key] for key in ('manifest_sha256','payload_sha256','member_count','package_count')}


def _support_ordinary_member(root, name, *, required=True):
    """One fixed support member, with fresh ancestry and Windows-alias checks."""
    current, identities = root, []
    for part in Path(name).parts:
        value = current.lstat()
        if (stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400
                or not stat.S_ISDIR(value.st_mode)):
            raise PackageError('OpenCode support archive has a linked/unsafe ancestor')
        identities.append((value.st_dev, value.st_ino))
        aliases = [entry.name for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
        if not aliases and not required:
            return None
        if aliases != [part]:
            raise PackageError('OpenCode support archive is missing or Windows-aliased: ' + name)
        current = current / part
    value = current.lstat()
    if (not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode)
            or getattr(value, 'st_file_attributes', 0) & 0x400 or value.st_nlink != 1):
        raise PackageError('OpenCode support archive must be an ordinary single-link file')
    identities.append((value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns))
    return current, tuple(identities)


def _refresh_support_archives(runtime, selected):
    """Portable packages use their canonical manifest-bound original ZIP.

    Raw build runtimes without PACKAGE_MANIFEST retain the historical support/
    ZIP layout. A portable package never falls back to an unregistered ZIP.
    Identical, registered legacy duplicates are preserved, never silently removed.
    """
    filename = 'ripgrep-15.1.0-x86_64-pc-windows-msvc.zip'
    canonical = 'toolchain/opencode-support/' + filename
    legacy = 'runtime/opencode-support/' + filename
    support_name = 'runtime/opencode-support/SUPPORT_MANIFEST.json'
    expected = selected.get('archive_sha256')
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise PackageError('Frozen OpenCode support archive SHA is invalid')
    package = _support_ordinary_member(runtime, 'PACKAGE_MANIFEST.json', required=False)
    original = _support_ordinary_member(runtime, canonical, required=False)
    historical = _support_ordinary_member(runtime, legacy, required=False)
    bindings = {}
    if package is not None:
        manifest = read_manifest(runtime)
        for row in manifest['files']:
            name = row.get('path') if isinstance(row, dict) else None
            if isinstance(name, str) and name.casefold() in {canonical.casefold(), legacy.casefold(), support_name.casefold()}:
                if name not in {canonical, legacy, support_name} or name in bindings:
                    raise PackageError('OpenCode support archive manifest is Windows-aliased')
                if (type(row.get('bytes')) is not int or row['bytes'] <= 0
                        or not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
                    raise PackageError('OpenCode support archive manifest binding is invalid')
                bindings[name] = row
        if canonical not in bindings or support_name not in bindings or original is None:
            raise PackageError('Portable OpenCode support archive requires its canonical package manifest binding')
        if (historical is not None) != (legacy in bindings):
            raise PackageError('Portable legacy support archive is unregistered or missing')
        sources = {canonical: original, support_name: _support_ordinary_member(runtime, support_name)}
        if historical is not None:
            sources[legacy] = historical
        for name, (path, identity) in sources.items():
            row = bindings[name]
            if path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
                raise PackageError('Portable OpenCode support archive/manifest bytes changed: ' + name)
            if name != support_name and row['sha256'] != expected:
                raise PackageError('Conflicting original OpenCode support archive bindings')
            if _support_ordinary_member(runtime, name)[1] != identity:
                raise PackageError('OpenCode support archive ancestry/bytes changed during selection')
        if _support_ordinary_member(runtime, 'PACKAGE_MANIFEST.json')[1] != package[1]:
            raise PackageError('Package manifest changed during support archive selection')
        result = {canonical: original[0]}
        if historical is not None:
            result[legacy] = historical[0]
        return result
    if historical is None:
        raise PackageError('Raw runtime requires its original legacy OpenCode support archive')
    for name, entry in ((legacy, historical), (canonical, original)):
        if entry is None:
            continue
        path, identity = entry
        if sha(path) != expected or _support_ordinary_member(runtime, name)[1] != identity:
            raise PackageError('Raw runtime original support archives differ or changed')
    return {canonical: historical[0]}


def add_workflow_plugin_closure(source, jobs, add):
    """Retain the exact reviewed vendor closure, including upstream test files.

    The broad production walker excludes tests. That must not silently remove
    files bound by the standalone plugin's manifest and break offline admission.
    """
    prefix = 'toolchain/opencode-plugins/dynamic-workflow'
    root = source / prefix
    manifest_path = root / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PackageError('Dynamic Workflow plugin manifest is missing')
    manifest = json.loads(manifest_path.read_bytes())
    members = manifest.get('files')
    if manifest.get('id') != 'eeas.dynamic-workflow' or not isinstance(members, list) or len(members) > 4096:
        raise PackageError('Dynamic Workflow manifest is not a bounded reviewed closure')
    seen = set()
    for item in members:
        name = item['path']
        path = relative_path(root, name)
        if name.casefold() in seen or path.is_symlink() or not path.is_file():
            raise PackageError('Dynamic Workflow closure member is missing or aliased')
        seen.add(name.casefold())
        if path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise PackageError('Dynamic Workflow closure bytes differ from its manifest')
        target = prefix + '/' + name
        if target not in jobs:
            add(path, target)


def add_change_review_plugin_closure(source, jobs, add):
    """Only the exact admitted six-file observer plus its frozen manifest.

    This is a required production tree, not a generic plugin installer. Its
    tests, journals and unlisted files never enter a package via a broad walk.
    """
    root = relative_path(source, CHANGE_REVIEW_PREFIX)
    if not root.is_dir():
        raise PackageError('Change Review production directory is missing')
    manifest_path = relative_path(root, 'manifest.json')
    if (not manifest_path.is_file() or manifest_path.stat().st_nlink != 1
            or manifest_path.stat().st_size > 64 * 1024):
        raise PackageError('Change Review plugin manifest is missing or aliased')
    manifest_hash = sha(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_bytes())
        members = manifest['files']
        names = [item['path'] for item in members]
    except (ValueError, KeyError, TypeError):
        raise PackageError('Change Review manifest is not the admitted closure') from None
    selected_members = CHANGE_REVIEW_MEMBERS if manifest.get('version') == '0.1.1' else LEGACY_CHANGE_REVIEW_MEMBERS
    selected_manifest_shas = {CHANGE_REVIEW_MANIFEST_SHA256} if manifest.get('version') == '0.1.1' else LEGACY_CHANGE_REVIEW_MANIFEST_SHA256S
    if (not isinstance(members, list) or len(members) != len(selected_members)
            or not all(isinstance(name, str) for name in names)
            or len({name.casefold() for name in names}) != len(names)
            or set(names) != selected_members):
        raise PackageError('Change Review closure members are missing or aliased')
    if (manifest.get('schema') != 'eeas.opencode-plugin-bundle.v1'
            or manifest.get('id') != 'eeas.change-review' or manifest.get('version') not in {'0.1.0','0.1.1'}
            or manifest.get('entry') != 'index.mjs' or manifest.get('new_dependencies') != []
            or manifest.get('runtime') != {'version':'1.19.0',
                'source_commit':'733b2c627e5aec39cb33cc22373bdce33053ef2c'}
            or manifest_hash not in selected_manifest_shas):
        raise PackageError('Change Review manifest differs from the reviewed bundle')
    folded = {name.casefold():name for name in (*selected_members, 'manifest.json')}
    entries = list(root.iterdir())
    if len(entries) > 1024:
        raise PackageError('Change Review source inventory exceeds its bound')
    for entry in entries:
        if entry.is_symlink() or (entry.name.casefold() in folded and folded[entry.name.casefold()] != entry.name):
            raise PackageError('Change Review source contains a link or Windows alias')
    selected = [(manifest_path, CHANGE_REVIEW_PREFIX + '/manifest.json')]
    for item in members:
        member = relative_path(root, item['path'])
        if not member.is_file() or member.stat().st_nlink != 1:
            raise PackageError('Change Review closure member is missing or aliased')
        if member.stat().st_size != item['bytes'] or sha(member) != item['sha256']:
            raise PackageError('Change Review closure bytes differ from its manifest')
        selected.append((member, CHANGE_REVIEW_PREFIX + '/' + item['path']))
    if sha(manifest_path) != manifest_hash:
        raise PackageError('Change Review manifest changed during selection')
    for member, target in selected:
        if target in jobs:
            raise PackageError('Change Review closure was selected more than once')
        add(member, target)


def change_review_job_pins(jobs):
    """Recheck a selected observer at copy time; old plans without it still read."""
    prefix = CHANGE_REVIEW_PREFIX + '/'
    chosen = {name:path for name,path in jobs.items() if name.startswith(prefix)}
    if not chosen:
        return {}
    expected = {prefix+name for name in (*CHANGE_REVIEW_MEMBERS, 'manifest.json')}
    legacy = {prefix+name for name in (*LEGACY_CHANGE_REVIEW_MEMBERS, 'manifest.json')}
    if set(chosen) not in (expected,legacy):
        raise PackageError('Change Review selected payload is incomplete or unreviewed')
    source = chosen[prefix+'manifest.json'].parents[3]
    verified = {}
    add_change_review_plugin_closure(source, {}, lambda path,name: verified.update({name:path}))
    if set(chosen)!=set(verified) or any(chosen[name] != verified[name] for name in chosen):
        raise PackageError('Change Review selected source paths differ from their closure')
    return {name:{'sha256':sha(path),'bytes':path.stat().st_size} for name,path in verified.items()}


def add_native_review_plugin_closure(source, jobs, add):
    """The required, fixed page reader; never walk its tests or accept another plugin."""
    root = relative_path(source, NATIVE_REVIEW_PREFIX)
    if not root.is_dir():
        raise PackageError('Native review production directory is missing')
    manifest_path = relative_path(root, 'manifest.json')
    if not manifest_path.is_file() or manifest_path.stat().st_nlink != 1 or manifest_path.stat().st_size > 65536:
        raise PackageError('Native review manifest is missing or aliased')
    raw_sha = sha(manifest_path)
    try:
        value = json.loads(manifest_path.read_bytes())
        members = value['files']; names = [item['path'] for item in members]
    except (ValueError, KeyError, TypeError):
        raise PackageError('Native review manifest is invalid') from None
    if (raw_sha != NATIVE_REVIEW_MANIFEST_SHA256 or value.get('schema') != 'eeas.native-review-plugin.v1'
            or value.get('id') != 'eeas.native-review-context' or value.get('version') != '1.0.0'
            or value.get('dependencies') != [] or not isinstance(members, list)
            or len(members) != len(NATIVE_REVIEW_MEMBERS) or set(names) != NATIVE_REVIEW_MEMBERS
            or len({name.casefold() for name in names}) != len(names)):
        raise PackageError('Native review manifest differs from the reviewed closure')
    exact = {name.casefold(): name for name in (*NATIVE_REVIEW_MEMBERS, 'manifest.json')}
    entries = list(root.iterdir())
    if len(entries) > 128 or any(entry.is_symlink() or entry.name.casefold() in exact and entry.name != exact[entry.name.casefold()] for entry in entries):
        raise PackageError('Native review source contains a link or Windows alias')
    chosen = [(manifest_path, NATIVE_REVIEW_PREFIX + '/manifest.json')]
    for row in members:
        member = relative_path(root, row['path'])
        if (not member.is_file() or member.stat().st_nlink != 1 or type(row['bytes']) is not int
                or member.stat().st_size != row['bytes'] or sha(member) != row['sha256']):
            raise PackageError('Native review closure member differs')
        chosen.append((member, NATIVE_REVIEW_PREFIX + '/' + row['path']))
    if sha(manifest_path) != raw_sha:
        raise PackageError('Native review manifest changed during selection')
    for path, target in chosen:
        if target in jobs: raise PackageError('Native review member selected twice')
        add(path, target)


def native_review_job_pins(jobs):
    prefix = NATIVE_REVIEW_PREFIX + '/'
    chosen = {name: path for name, path in jobs.items() if name.startswith(prefix)}
    if not chosen: return {}
    if set(chosen) != {prefix + name for name in (*NATIVE_REVIEW_MEMBERS, 'manifest.json')}:
        raise PackageError('Native review selected closure is incomplete')
    source = chosen[prefix + 'manifest.json'].parents[3]
    verified = {}
    add_native_review_plugin_closure(source, {}, lambda path, name: verified.update({name: path}))
    if chosen != verified:
        raise PackageError('Native review selected source paths differ')
    return {name: {'bytes': path.stat().st_size, 'sha256': sha(path)} for name, path in verified.items()}


def add_native_message_plugin_closure(source, jobs, add):
    """Include the ordinary reader and its existing pinned shared Zod files."""
    root = relative_path(source, NATIVE_MESSAGE_PREFIX)
    manifest_path = relative_path(root, 'manifest.json')
    if not manifest_path.is_file() or manifest_path.stat().st_nlink != 1 or manifest_path.stat().st_size > 65536:
        raise PackageError('Ordinary reader manifest is missing or aliased')
    try:
        value = json.loads(manifest_path.read_bytes())
        members = value['files']
        names = [row['path'] for row in members]
    except (ValueError, KeyError, TypeError):
        raise PackageError('Ordinary reader manifest is invalid') from None
    dependency = {'path': 'toolchain/opencode-plugins/dynamic-workflow/manifest.json',
        'sha256': '86f5301c99b6ecf10a6263c08dd6dc898bc6a238a61c60ac2e1a02be7ddb41b5',
        'prefix': 'vendor/zod/', 'version': '4.1.8',
        'entry_sha256': 'c733a1897d6b4b30dad6998597f6896b265b094a65534359ada34b08ecf8932c'}
    if (sha(manifest_path) != NATIVE_MESSAGE_MANIFEST_SHA256
            or value.get('schema') != 'eeas.native-message-plugin.v1'
            or value.get('id') != 'eeas.native-message-context' or value.get('version') != '1.0.0'
            or not isinstance(members, list) or len(members) != len(NATIVE_MESSAGE_MEMBERS)
            or set(names) != NATIVE_MESSAGE_MEMBERS or len(set(names)) != len(names)
            or value.get('dependencies') != [dependency]):
        raise PackageError('Ordinary reader differs from the reviewed closure')
    exact = {name.casefold(): name for name in (*NATIVE_MESSAGE_MEMBERS, 'manifest.json')}
    entries = list(root.iterdir())
    if len(entries) > 128 or any(entry.is_symlink() or entry.name.casefold() in exact
            and entry.name != exact[entry.name.casefold()] for entry in entries):
        raise PackageError('Ordinary reader contains a link or Windows alias')
    chosen = [(manifest_path, NATIVE_MESSAGE_PREFIX + '/manifest.json')]
    for row in members:
        member = relative_path(root, row['path'])
        if (not member.is_file() or member.stat().st_nlink != 1 or type(row['bytes']) is not int
                or member.stat().st_size != row['bytes'] or sha(member) != row['sha256']):
            raise PackageError('Ordinary reader member differs')
        chosen.append((member, NATIVE_MESSAGE_PREFIX + '/' + row['path']))
    vendor_manifest = relative_path(source, dependency['path'])
    if (not vendor_manifest.is_file() or vendor_manifest.stat().st_nlink != 1
            or vendor_manifest.stat().st_size > 262144 or sha(vendor_manifest) != dependency['sha256']):
        raise PackageError('Ordinary shared vendor manifest differs')
    vendor = json.loads(vendor_manifest.read_bytes())
    rows = [row for row in vendor['files'] if row['path'].startswith(dependency['prefix'])]
    if not rows or not any(row['path'] == 'vendor/zod/index.js'
            and row['sha256'] == dependency['entry_sha256'] for row in rows):
        raise PackageError('Ordinary shared vendor entry is missing')
    for row in rows:
        member = relative_path(vendor_manifest.parent, row['path'])
        if (not member.is_file() or member.stat().st_nlink != 1 or type(row['bytes']) is not int
                or member.stat().st_size != row['bytes'] or sha(member) != row['sha256']):
            raise PackageError('Ordinary shared vendor member differs')
        chosen.append((member, vendor_manifest.parent.relative_to(source).as_posix() + '/' + row['path']))
    chosen.append((vendor_manifest, dependency['path']))
    for path, target in chosen:
        if target in jobs:
            if jobs[target] != path:
                raise PackageError('Ordinary shared dependency has another selected source')
        else:
            add(path, target)


def native_message_job_pins(jobs):
    prefix = NATIVE_MESSAGE_PREFIX + '/'
    chosen = {name: path for name, path in jobs.items() if name.casefold().startswith(prefix.casefold())}
    if not chosen:
        return {}
    if set(chosen) != {prefix + name for name in (*NATIVE_MESSAGE_MEMBERS, 'manifest.json')}:
        raise PackageError('Ordinary reader selected closure is incomplete')
    source = chosen[prefix + 'manifest.json'].parents[3]
    verified = {}
    add_native_message_plugin_closure(source, {}, lambda path, name: verified.update({name: path}))
    vendor_prefix = 'toolchain/opencode-plugins/dynamic-workflow/vendor/zod/'
    vendor_chosen = {name for name in jobs if name.casefold().startswith(vendor_prefix.casefold())}
    vendor_expected = {name for name in verified if name.startswith(vendor_prefix)}
    if vendor_chosen != vendor_expected:
        raise PackageError('Ordinary shared vendor selection has unreviewed or aliased members')
    if any(jobs.get(name) != path for name, path in verified.items()):
        raise PackageError('Ordinary reader selected shared closure is incomplete or changed')
    return {name: {'bytes': path.stat().st_size, 'sha256': sha(path)} for name, path in verified.items()}


def add_model_authority_plugin_closure(source, jobs, add):
    from packages.adapters.opencode_model_authority import verify_bundle
    try:bundle=verify_bundle(source)
    except Exception as exc:raise PackageError('Original model authority closure differs') from exc
    for path,name in bundle['files']:add(path,name)


def model_authority_job_pins(jobs):
    from packages.adapters.opencode_model_authority import PLUGIN_FILES
    prefix=MODEL_AUTHORITY_PREFIX+'/'
    selected={name:path for name,path in jobs.items() if name.casefold().startswith(prefix.casefold())}
    if not selected:return {}
    expected={prefix+name for name in (*PLUGIN_FILES,'manifest.json')}
    if set(selected)!=expected:raise PackageError('Original model authority selected closure is incomplete')
    source=selected[prefix+'manifest.json'].parents[3];verified={}
    add_model_authority_plugin_closure(source,{},lambda path,name:verified.update({name:path}))
    if selected!=verified:raise PackageError('Original model authority selected closure differs')
    return {name:{'bytes':path.stat().st_size,'sha256':sha(path)} for name,path in verified.items()}


def add_dist_dependency_licenses(source, jobs, add):
    """Keep emitted license text beneath nested dependency-name directories.

    The normal source/runtime walker must continue excluding node_modules.
    Only the frontend's explicit distribution-license root gets this bounded
    text-only exception; no package code or dependency installation is included.
    """
    prefix = 'apps/agent-shell/dist/licenses/dependencies'
    root = relative_path(source, prefix)
    if not root.exists():
        return
    if not root.is_dir():
        raise PackageError('Frontend dependency-license root is not a directory')
    count, total, entries = 0, 0, 0
    for directory, names, files in os.walk(root, followlinks=False):
        for name in names + files:
            if (Path(directory) / name).is_symlink():
                raise PackageError('Frontend dependency licenses contain a link')
        names[:] = sorted(name for name in names if name not in EXCLUDE_DIRS or name == 'node_modules')
        entries += len(names) + len(files)
        if entries > 8192:
            raise PackageError('Frontend dependency-license inventory exceeds its bound')
        for name in sorted(files):
            path = Path(directory) / name
            member = path.relative_to(root)
            if ('node_modules' not in member.parts[:-1]
                    or name.casefold() not in {'license', 'license.md', 'license.txt'}):
                continue
            path = relative_path(root, member.as_posix())
            if not path.is_file() or path.stat().st_size > 256 * 1024:
                raise PackageError('Frontend dependency license is not bounded ordinary text')
            with path.open('rb') as stream:
                raw = stream.read(256 * 1024 + 1)
            count += 1
            total += len(raw)
            if len(raw) > 256 * 1024 or count > 4096 or total > 8 * 1024 * 1024:
                raise PackageError('Frontend dependency-license text exceeds its bound')
            try:
                if '\0' in raw.decode('utf-8-sig'):
                    raise UnicodeError
            except UnicodeError:
                raise PackageError('Frontend dependency license must be UTF-8 text') from None
            target = prefix + '/' + member.as_posix()
            if target not in jobs:
                add(path, target)


def make_plan(source_root, runtime_root):
    source, runtime = Path(source_root).resolve(), Path(runtime_root).resolve()
    audit = audit_selected_runtime(runtime)
    jobs, destinations = {}, set()
    def add(path, name):
        if path.is_symlink() or not path.is_file():
            raise PackageError('Missing ordinary production payload: ' + str(path))
        relative_path(Path('/package-plan'), name)
        if name.casefold() in destinations:
            raise PackageError('Duplicate/Windows-aliased payload destination')
        jobs[name] = path
        destinations.add(name.casefold())
    def tree(base, name):
        if not base.is_dir():
            raise PackageError('Production directory missing: ' + str(base))
        for path in files_below(base):
            add(path, name + '/' + path.relative_to(base).as_posix())
    for name in SOURCE_TREES:
        if name not in {CHANGE_REVIEW_PREFIX, NATIVE_REVIEW_PREFIX, NATIVE_MESSAGE_PREFIX, MODEL_AUTHORITY_PREFIX}:
            tree(source / name, name)
    add_dist_dependency_licenses(source, jobs, add)
    add_workflow_plugin_closure(source, jobs, add)
    add_change_review_plugin_closure(source, jobs, add)
    add_native_review_plugin_closure(source, jobs, add)
    add_native_message_plugin_closure(source, jobs, add)
    add_model_authority_plugin_closure(source, jobs, add)
    for name in (*SOURCE_EXTRAS, 'apps/desktop-host/package.json', 'apps/agent-shell/package.json'):
        if name not in jobs:
            add(source / name, name)
    for name in ('runtime/python', 'runtime/electron', 'runtime/opencode'):
        tree(runtime / name, name)
    dependency_runtime = installed_record_inventory(runtime,audit)
    for name,item in dependency_runtime['files'].items():
        add(item['source'],name)
    audit['installed_runtime_closure'] = {'distribution_count':dependency_runtime['distribution_count'],
        'member_count':len(dependency_runtime['files']), 'selection':'AUDITED_RECORD_MEMBERS_AND_RECORDS_NO_DEVELOPER_WALK'}
    ocr = ocr_runtime_inventory(source / 'toolchain/ocr/MODELS.json',runtime)
    for name,item in ocr['files'].items():
        if name not in jobs:
            add(item['source'],name)
    audit['ocr_engine_closure'] = ocr_inventory_summary(ocr)
    support = runtime / 'runtime/opencode-support'
    support_manifest = support / 'SUPPORT_MANIFEST.json'
    selected = json.loads(support_manifest.read_bytes())
    if (selected.get('schema') != 'eeas.opencode-support.v1'
            or selected.get('platform') != 'windows-x64' or selected.get('version') != '15.1.0'):
        raise PackageError('Actual frozen Windows OpenCode support is required')
    add(support_manifest, 'runtime/opencode-support/SUPPORT_MANIFEST.json')
    for item in selected['files']:
        path = relative_path(support, item['path'])
        if not path.is_file() or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise PackageError('Frozen OpenCode support payload changed')
        add(path, 'runtime/opencode-support/' + item['path'])
    archive = support / 'ripgrep-15.1.0-x86_64-pc-windows-msvc.zip'
    if not archive.is_file() or sha(archive) != selected['archive_sha256']:
        raise PackageError('Original frozen ripgrep archive is missing or changed')
    add(archive, 'toolchain/opencode-support/' + archive.name)
    audit['opencode_support_manifest_sha256'] = sha(support_manifest)
    # Include only the focused closure's actual wheel originals, never leftover Claude wheels.
    for item in audit.pop('wheel_jobs'):
        add(item['path'], item['relative'])
    for name in ('dependency-closure-windows310.json', 'requirements-focused-native-windows310.lock', 'RUNTIME_RECEIPT.json'):
        add(runtime / name, 'provenance/' + name)
    for item in audit.get('provenance_files', []):
        path = _runtime79_file(runtime,item['source_relative'],item['sha256'])
        if path.stat().st_size != item['bytes']:
            raise PackageError('Runtime79 provenance changed while selecting payload')
        add(path,item['package_relative'])
    for name in ('toolchain/components/cua-driver-shell-v014', 'toolchain/components/windows-capture-2.0.1',
                 'toolchain/ocr/models', 'toolchain/ocr/licenses', 'toolchain/fonts', 'toolchain/ui-fonts/noto-serif-sc'):
        base = runtime / name if (runtime / name).is_dir() else source / name
        tree(base, name)
    add(runtime / ENTRYPOINTS['readonly_manifest'], ENTRYPOINTS['readonly_manifest'])
    # Preserve the original OpenCode manifest; Linux binary is intentionally not needed.
    component = source / 'toolchain/components/opencode-v1.19.0'
    add(component / 'manifest.json', 'toolchain/components/opencode-v1.19.0/manifest.json')
    records = json.loads((component / 'manifest.json').read_bytes())['files']
    for item in records:
        if item['role'] == 'linux-x64-cli':
            continue
        path = component / item['path']
        if not path.is_file() or sha(path) != item['sha256'] or path.stat().st_size != item['byte_length']:
            raise PackageError('Original OpenCode archive/source failed its pinned manifest')
        add(path, 'toolchain/components/opencode-v1.19.0/' + item['path'])
    return {'jobs':jobs, 'runtime_audit':audit, 'source_root':str(source), 'runtime_root':str(runtime),
        'ocr_runtime':ocr, 'dependency_runtime':dependency_runtime}


def make_refresh_plan(source_root, runtime_root, *, repair_ocr_runtime=False):
    """Select only real source/support; no re-copy or claim about dependencies."""
    source, runtime = Path(source_root).resolve(), Path(runtime_root).resolve()
    jobs, destinations = {}, set()
    def add(path,name):
        if not path.is_file() or path.is_symlink():
            raise PackageError('Missing actual source/support refresh file')
        relative_path(Path('/package-plan'),name)
        if name.casefold() in destinations:
            raise PackageError('Windows-aliased refresh destination')
        destinations.add(name.casefold());jobs[name]=path
    for name in SOURCE_TREES:
        if name in {CHANGE_REVIEW_PREFIX, NATIVE_REVIEW_PREFIX, NATIVE_MESSAGE_PREFIX, MODEL_AUTHORITY_PREFIX}:
            continue
        base=source/name
        if not base.is_dir():raise PackageError('Current production source directory missing: '+name)
        for path in files_below(base):add(path,name+'/'+path.relative_to(base).as_posix())
    add_dist_dependency_licenses(source, jobs, add)
    add_workflow_plugin_closure(source, jobs, add)
    add_change_review_plugin_closure(source, jobs, add)
    add_native_review_plugin_closure(source, jobs, add)
    add_native_message_plugin_closure(source, jobs, add)
    add_model_authority_plugin_closure(source, jobs, add)
    for name in SOURCE_EXTRAS:
        add(source/name,name)
    support=runtime/'runtime/opencode-support'
    manifest=_support_ordinary_member(runtime,'runtime/opencode-support/SUPPORT_MANIFEST.json')[0]
    selected=json.loads(manifest.read_bytes())
    if (selected.get('schema') != 'eeas.opencode-support.v1' or selected.get('platform') != 'windows-x64'
            or selected.get('version') != '15.1.0'):
        raise PackageError('Frozen Windows OpenCode support manifest required for refresh')
    add(manifest,'runtime/opencode-support/SUPPORT_MANIFEST.json')
    for item in selected['files']:
        path=relative_path(support,item['path'])
        if not path.is_file() or sha(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:
            raise PackageError('Actual frozen support file differs during refresh')
        add(path,'runtime/opencode-support/'+item['path'])
    for name,archive in _refresh_support_archives(runtime,selected).items():
        add(archive,name)
    plan = {'jobs':jobs,'source_root':str(source),'runtime_root':str(runtime),
        'runtime_audit':{'scope':'SOURCE_SUPPORT_ONLY_PRESERVED_DEPENDENCIES_NOT_RECOPIED'}}
    if repair_ocr_runtime:
        plan['ocr_runtime_repair'] = ocr_runtime_inventory(source / 'toolchain/ocr/MODELS.json',runtime)
    return plan


def copy_plan(plan, output):
    output = Path(output).absolute()
    if output.exists():
        raise PackageError('Output must be a new directory; previous packages/evidence are preserved')
    output.mkdir(parents=True)
    inventory = []
    try:
        change_review = change_review_job_pins(plan['jobs'])
        change_review.update(native_review_job_pins(plan['jobs']))
        change_review.update(native_message_job_pins(plan['jobs']))
        change_review.update(model_authority_job_pins(plan['jobs']))
        for name, source in sorted(plan['jobs'].items()):
            target = relative_path(output, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            before = sha(source)
            if name in change_review and (before != change_review[name]['sha256'] or source.stat().st_size != change_review[name]['bytes']):
                raise PackageError('Pinned Change Review source changed before copy: ' + name)
            ocr = plan.get('ocr_runtime')
            pin = ocr['files'].get(name) if ocr is not None else None
            if pin is not None and before != pin['sha256']:
                raise PackageError('Pinned OCR engine source changed before copy: ' + name)
            if ocr is not None and name == 'toolchain/ocr/MODELS.json' and before != ocr['manifest_sha256']:
                raise PackageError('OCR manifest changed after selecting its engine closure')
            dependency_pin = plan.get('dependency_runtime',{}).get('files',{}).get(name)
            if dependency_pin is not None and (before != dependency_pin['sha256'] or source.stat().st_size != dependency_pin['bytes']):
                raise PackageError('Pinned dependency RECORD source changed before copy: ' + name)
            shutil.copyfile(source, target)
            after = sha(target)
            if before != after or sha(source) != before:
                raise PackageError('Source changed while copying: ' + name)
            inventory.append({'path':name, 'sha256':after, 'bytes':target.stat().st_size,
                'startup_critical':not (name.startswith(('toolchain/runtime-wheels/', 'toolchain/ocr/models/', 'apps/agent-shell/dist/'))
                    or name.endswith(('.zip','.tar.gz','.whl','.ttc','.woff2','.onnx')))})
        # Override copied development paths with a per-installation relative interpreter path list.
        pth = output / 'runtime/python/python310._pth'
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text('.\nLib\nDLLs\n../site-packages\n../site-packages/win32\n../site-packages/win32/lib\n../site-packages/pythonwin\n../..\n', encoding='utf-8')
        inventory = [x for x in inventory if x['path'] != 'runtime/python/python310._pth']
        inventory.append({'path':'runtime/python/python310._pth', 'sha256':sha(pth), 'bytes':pth.stat().st_size, 'startup_critical':True})
        for name, content in package_launchers().items():
            launcher = output / name
            launcher.write_bytes(content)
            inventory.append({'path':name, 'sha256':sha(launcher), 'bytes':len(content), 'startup_critical':True})
        for name, content in package_instructions().items():
            if any(item['path'].casefold() == name.casefold() for item in inventory):
                raise PackageError('Generated portable instructions collide with a source payload')
            target = relative_path(output, name)
            target.write_bytes(content)
            inventory.append({'path':name, 'sha256':sha(target), 'bytes':len(content), 'startup_critical':False})
        pth_files = sorted(x['path'] for x in inventory if x['path'].startswith('runtime/site-packages/') and x['path'].endswith('.pth') and '/' not in x['path'][len('runtime/site-packages/'):])
        licenses = [x['path'] for x in inventory if any(word in Path(x['path']).name.lower() for word in ('license','licence','copyright','notice','copying','unlicense'))]
        manifest = {'schema':SCHEMA, 'target':'windows-x64-py310', 'state':'DEVELOPMENT_PREVIEW',
            'entrypoints':ENTRYPOINTS, 'files':sorted(inventory,key=lambda x:x['path']), 'pth_files':pth_files,
            'runtime_audit':plan['runtime_audit'], 'license_files':sorted(licenses),
            'instructions':instruction_manifest(),
            'durable_state_protocols':state_protocol_metadata(inventory),
            'license_completeness':'COPIED_AVAILABLE_ORIGINAL_NOTICES_NOT_A_LEGAL_COMPLETENESS_CERTIFICATION',
            'qualification':{'windows_cold_boot':'NOT_RUN','relocated_windows_child_import':'NOT_RUN',
                'model_access':'NOT_CONFIGURED','chatgpt_pro':'NOT_VALIDATED','arbitrary_app_editing':'NOT_VALIDATED','os_network_sandbox':'NOT_QUALIFIED'},
            'excluded':['node_modules','.venv','developer/account caches','legacy absolute sitecustomize','old catalogue site-packages','claude-agent-sdk wheel'],
            'provenance':{'source_root':plan.get('source_root'), 'selected_runtime_root':plan.get('runtime_root')},
            'source_mutation':'SOURCE_BYTES_CHECKED_BEFORE_AND_AFTER_EACH_COPY',
            'startup':'START_EEAS.cmd; no install/download or user environment configuration'}
        ide = ide_companion_metadata(item['path'] for item in inventory)
        if ide is not None:
            manifest['ide_companion'] = ide
        verify_plugin_closures(output, manifest)
        (output / 'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        return manifest
    except BaseException as error:
        # Do not delete an interrupted/failed copy; preserve its actual partial payload.
        (output / 'PACKAGE_FAILED.json').write_text(json.dumps({'status':'FAILED','error_type':type(error).__name__,'copied_files':len(inventory)}))
        raise


def refresh_source(plan, output):
    """Reviewed final source/support refresh; preserve the exact prior payload.

    Call only after stopping the package. Existing dependencies stay bound to
    their copied hashes; the following normal bootstrap verifies them again.
    An interrupted refresh fails closed against its previous manifest.
    """
    output = Path(output).resolve()
    manifest = read_manifest(output)
    old = {item['path']:item for item in manifest['files']}
    prefixes = tuple(name + '/' for name in SOURCE_TREES) + ('scripts/',
        'runtime/opencode-support/', 'toolchain/opencode-support/')
    explicit = set(SOURCE_EXTRAS) | set(INSTRUCTION_FILES) | set(LAUNCHER_FILES)
    managed = lambda name: name.startswith(prefixes) or name in explicit
    selected = {name:path for name,path in plan['jobs'].items() if managed(name)}
    change_review = change_review_job_pins(selected)
    change_review.update(native_review_job_pins(selected))
    change_review.update(native_message_job_pins(selected))
    change_review.update(model_authority_job_pins(selected))
    repair = plan.get('ocr_runtime_repair')
    repair_members = repair['files'] if repair is not None else {}
    repair_added,repair_restored,repair_registered,repair_verified = [],[],[],0
    if repair is not None:
        pinned = old.get('toolchain/ocr/MODELS.json')
        current = relative_path(output,'toolchain/ocr/MODELS.json')
        source_manifest = selected.get('toolchain/ocr/MODELS.json')
        if (pinned is None or source_manifest is None or not current.is_file()
                or sha(current) != pinned['sha256'] or pinned['sha256'] != repair['manifest_sha256']
                or sha(source_manifest) != repair['manifest_sha256']):
            raise PackageError('OCR repair requires the unchanged existing OCR manifest binding')
        aliases = {name.casefold():name for name in old}
        for name,item in repair_members.items():
            if name.casefold() in aliases and aliases[name.casefold()] != name:
                raise PackageError('OCR repair aliases an existing Windows runtime path')
            if not name.startswith('runtime/site-packages/'):
                raise PackageError('OCR repair member escaped the selected engine closure')
            target = relative_path(output,name)
            if name in old and (old[name]['sha256'] != item['sha256'] or old[name]['bytes'] != item['bytes']):
                raise PackageError('Existing runtime binding differs from pinned OCR member: ' + name)
            if target.exists():
                if not target.is_file() or target.stat().st_size != item['bytes'] or sha(target) != item['sha256']:
                    raise PackageError('Existing OCR runtime bytes differ; refusing overwrite: ' + name)
                if name in old:repair_verified += 1
                else:repair_registered.append(name)
            elif name in old:repair_restored.append(name)
            else:repair_added.append(name)
            selected[name] = item['source']
    if not selected:
        raise PackageError('No real current source/support was selected for refresh')
    if any(name.casefold() in {item.casefold() for item in INSTRUCTION_FILES} for name in selected):
        raise PackageError('Generated portable instructions collide with a source refresh payload')
    selected.update(package_instructions())
    selected.update(package_launchers())
    # Refuse an incomplete new bridge before changing a stopped installation.
    ide = ide_companion_metadata({name for name in old if not managed(name)} | set(selected))
    affected = {name for name in old if managed(name)} | set(selected)
    # Before any mutation, refuse externally changed original package files.
    for name in affected:
        target = relative_path(output,name)
        if name in old:
            if name in repair_restored:
                continue
            if not target.is_file() or target.stat().st_size != old[name]['bytes'] or sha(target) != old[name]['sha256']:
                raise PackageError('Prior source/support bytes changed before refresh: ' + name)
        elif target.exists() and name not in repair_registered:
            raise PackageError('An unregistered package file would be overwritten')
    previous_sha = sha(output / 'PACKAGE_MANIFEST.json')
    history = output / 'package-history' / (previous_sha + '-' + uuid.uuid4().hex)
    history.mkdir(parents=True)
    shutil.copyfile(output / 'PACKAGE_MANIFEST.json',history / 'PACKAGE_MANIFEST.before.json')
    new = {name:item for name,item in old.items() if not managed(name)}
    changed, staged = [], {}
    try:
        for name,source in sorted(selected.items()):
            generated = isinstance(source, bytes)
            before = hashlib.sha256(source).hexdigest() if generated else sha(source)
            if name in change_review and (before != change_review[name]['sha256'] or source.stat().st_size != change_review[name]['bytes']):
                raise PackageError('Pinned Change Review source changed during refresh: ' + name)
            if name in repair_members and before != repair_members[name]['sha256']:
                raise PackageError('Pinned OCR source changed during refresh: ' + name)
            if name in repair_registered:
                current = relative_path(output,name)
                if not current.is_file() or sha(current) != before:
                    raise PackageError('Unregistered pinned OCR bytes changed before registration')
                new[name] = {'path':name,'sha256':before,'bytes':current.stat().st_size,'startup_critical':True}
                continue
            if name in old and before == old[name]['sha256'] and name not in repair_restored:
                new[name] = old[name]
                continue
            target = relative_path(history / 'incoming',name)
            target.parent.mkdir(parents=True,exist_ok=True)
            if generated:
                target.write_bytes(source)
            else:
                shutil.copyfile(source,target)
            if sha(target) != before or (not generated and sha(source) != before):
                raise PackageError('Current source changed during refresh: ' + name)
            staged[name] = target
            new[name] = {'path':name,'sha256':before,'bytes':target.stat().st_size,
                'startup_critical':name in LAUNCHER_FILES or not (generated or name.startswith('apps/agent-shell/dist/') or name.endswith('.zip'))}
        removed = set(old) - set(new)
        for name in sorted(set(staged) | removed):
            target = relative_path(output,name)
            if name in old and name not in repair_restored:
                backup = relative_path(history / 'payload.before',name)
                backup.parent.mkdir(parents=True,exist_ok=True)
                target.replace(backup)
            if name in staged:
                target.parent.mkdir(parents=True,exist_ok=True)
                if name in repair_members:
                    # Exclusive creation never overwrites a concurrently appeared
                    # file, including one containing different user/runtime bytes.
                    with staged[name].open('rb') as incoming, target.open('xb') as destination:
                        shutil.copyfileobj(incoming,destination)
                    if sha(target) != repair_members[name]['sha256']:
                        raise PackageError('Copied pinned OCR engine member failed its original hash')
                else:
                    staged[name].replace(target)
            changed.append(name)
        manifest['files'] = sorted(new.values(),key=lambda item:item['path'])
        manifest['durable_state_protocols'] = state_protocol_metadata(manifest['files'])
        manifest['instructions'] = instruction_manifest()
        if ide is None:
            manifest.pop('ide_companion',None)
        else:
            manifest['ide_companion'] = ide
        manifest['license_files'] = sorted(name for name in new if any(word in Path(name).name.lower()
            for word in ('license','licence','copyright','notice','copying','unlicense')))
        manifest['qualification']['windows_cold_boot'] = 'NOT_RUN_AFTER_SOURCE_REFRESH'
        manifest['qualification']['relocated_windows_child_import'] = 'NOT_RUN_AFTER_SOURCE_REFRESH'
        manifest['source_refresh'] = {'previous_manifest_sha256':previous_sha,
            'history_relative_path':history.relative_to(output).as_posix(),'changed_files':changed,
            'source_root':plan.get('source_root'),'dependency_bytes':'PRESERVED_PREVIOUS_HASH_BINDINGS'}
        if repair is not None:
            repaired = sorted(set(repair_added + repair_restored + repair_registered))
            report = {**ocr_inventory_summary(repair),'selected_runtime_root':plan.get('runtime_root'),
                'added_files':repair_added,'restored_files':repair_restored,
                'registered_identical_files':repair_registered,'verified_existing_members':repair_verified,
                'members':[{key:value for key,value in {'path':name,**repair_members[name]}.items() if key != 'source'}
                    for name in repaired], 'existing_runtime_bytes':'NEVER_OVERWRITTEN',
                'previous_runtime_hash_bindings':'PRESERVED', 'windows_ocr_inference':'NOT_RUN'}
            manifest['source_refresh']['dependency_bytes'] = 'PRESERVED_PREVIOUS_HASH_BINDINGS_WITH_PINNED_OCR_ADDITIONS'
            manifest['source_refresh']['ocr_runtime_repair'] = report
            (history / 'OCR_RUNTIME_REPAIR.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        verify_plugin_closures(output, manifest)
        temp = history / 'PACKAGE_MANIFEST.after.json'
        temp.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        shutil.copyfile(temp,output / 'PACKAGE_MANIFEST.next.json')
        (output / 'PACKAGE_MANIFEST.next.json').replace(output / 'PACKAGE_MANIFEST.json')
        receipt = {'status':'DEVELOPMENT_PREVIEW_SOURCE_SUPPORT_REFRESHED','previous_manifest_sha256':previous_sha,
            'manifest_sha256':sha(output / 'PACKAGE_MANIFEST.json'),'changed_files':changed}
        (history / 'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
        return manifest
    except BaseException as error:
        (history / 'REFRESH_FAILED.json').write_text(json.dumps({'status':'FAILED_CLOSED','error_type':type(error).__name__,
            'changed_files':changed,'previous_manifest_sha256':previous_sha}))
        raise


def disk_target(path):
    # This is the development packager's storage policy, not a product-user setting.
    if PureWindowsPath(str(path)).drive:
        raise PackageError('Package output requires internal WSL storage; Windows drives are read-only inputs')
    supplied = Path(path).absolute()
    allowed_root = INTERNAL_ARTIFACT_ROOT.absolute()
    if any(parent.is_symlink() for base in (supplied, allowed_root) for parent in (base,*base.parents)):
        raise PackageError('Large package output cannot follow a directory link')
    path = supplied.resolve()
    allowed_root = allowed_root.resolve()
    if (os.name == 'nt' or Path('/mnt') in allowed_root.parents
            or path == allowed_root or allowed_root not in path.parents):
        raise PackageError('Package/archive output must be below the internal workspace .development directory')
    return path


def archive_payload(output, archive, manifest):
    """Archive the hashed distributable only, never local data/account history."""
    output,archive=Path(output),Path(archive)
    if archive.exists():raise PackageError('Archive path already exists')
    archive.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as package:
        package.write(output/'PACKAGE_MANIFEST.json','PACKAGE_MANIFEST.json')
        for item in manifest['files']:
            path=relative_path(output,item['path'])
            if not path.is_file() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:
                raise PackageError('Actual payload changed before archiving')
            package.write(path,item['path'])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,help='Explicit package directory below internal workspace .development')
    parser.add_argument('--archive',type=Path,help='New archive below internal workspace .development')
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--refresh-source',action='store_true',help='Refresh a stopped preview; preserve prior source/manifest')
    parser.add_argument('--repair-ocr-runtime',action='store_true',
        help='With --refresh-source only: restore missing pinned OCR wheel members without changing existing runtime bytes')
    options=parser.parse_args(argv)
    if options.repair_ocr_runtime and (not options.refresh_source or options.audit_only):
        raise PackageError('--repair-ocr-runtime requires explicit --refresh-source on a stopped preview')
    if options.audit_only:
        audit=audit_selected_runtime(options.runtime_root);audit.pop('wheel_jobs')
        print(json.dumps(audit,ensure_ascii=False,indent=2));return 0
    if options.output is None:
        raise PackageError('An explicit internal workspace .development output directory is required')
    output=disk_target(options.output)
    archive=disk_target(options.archive) if options.archive else None
    if archive is not None and (archive == output or archive in output.parents or archive.exists()):
        raise PackageError('Archive must be a new file distinct from the package directory and its parents')
    plan=(make_refresh_plan(options.source_root,options.runtime_root,repair_ocr_runtime=options.repair_ocr_runtime)
        if options.refresh_source else make_plan(options.source_root,options.runtime_root))
    manifest=refresh_source(plan,output) if options.refresh_source else copy_plan(plan,output)
    receipt={'status':'DEVELOPMENT_PREVIEW_SOURCE_REFRESHED' if options.refresh_source else 'DEVELOPMENT_PREVIEW_PACKAGE_COPIED','output':str(output),'files':len(manifest['files']),
        'manifest_sha256':sha(output/'PACKAGE_MANIFEST.json'),'windows_runtime':'NOT_EXECUTED'}
    if archive is not None:
        archive_payload(output,archive,manifest)
        receipt.update(archive=str(archive),archive_sha256=sha(archive),archive_bytes=archive.stat().st_size)
    print(json.dumps(receipt,ensure_ascii=False,indent=2));return 0


if __name__ == '__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('Package build failed: '+type(error).__name__+': '+str(error),file=sys.stderr);raise SystemExit(1)
