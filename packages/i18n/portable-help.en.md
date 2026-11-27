# {product_display_name} for Windows: start here

[简体中文](README_FIRST.md) · [Intranet setup](INTRANET_SETUP.en.md)

This is a Windows x64 desktop development preview built on the original OpenCode 1.19.0. It includes Python 3.10, Electron, dependencies, support files, and component licenses. PACKAGE_MANIFEST.json records the exact production files and hashes. Hash verification checks integrity; it is not a publisher signature or a statement that every feature has been qualified.

## Start and choose a language

1. Extract or copy the entire package into a writable D: or E: folder. Keep all relative paths together. Spaces and Chinese characters are supported. Do not move START_EEAS.cmd or runtime on their own.
2. Double-click START_EEAS.cmd. Python, Node, and Electron are bundled; no pip/npm download or environment setup is required. File verification appears before the local service loads. A first start can take about a minute.
3. Open Settings to choose System, 简体中文, or English. The desktop app, quick chat, annotation controls, Orb menus, tray, and subsequent startup messages share this preference. Changing the interface language preserves the current task and draft. Your text, filenames, model answers, and existing reports are not translated.
4. Choose Sign in with ChatGPT or configure an API/intranet model. The two authorization paths are separate.

Local history, credentials, logs, temporary files, and settings belong to this installation's user-data folder. Exit before moving the package or updating it. A clean distribution contains registered production files, not developer accounts, histories, or qualification data. A successful clean move does not certify migration of an existing user's credentials or history. Check absolute paths for external MCP programs after a move.

For local maintenance, double-click MAINTAIN_CIRVEN.cmd in the package root, then choose Create backup, Check backup, or Restore backup. Exit the current app from its tray before backing up; choose a location and the tool creates a new private backup folder. To restore, extract a fresh complete package, run maintenance before its first app start, and select the saved backup. Existing user data is never replaced. Backups contain account files and stay private to the same Windows user on this computer; do not include them in software distributions. File verification does not prove account service usability or complete restored desktop qualification. Check history and drafts after starting the restored copy.

Maintenance enables actions that fit the installation's current state. After a successful backup or restore, choose Open app to continue directly.

## Connect an intranet model

Open the model selector in the composer, then choose Configure connection. Enter a unique connection ID, the actual service URL, exact model ID, and either the issued API key or an explicit no-key choice. Save and select the connection. See INTRANET_SETUP.en.md for advanced authentication, CA, proxy, and MCP setup.

NOT_VALIDATED means the configuration was saved; it does not prove connectivity or inference. Select that connection and send a small read-only request in the current chat to check the actual outcome. Saving a connection and successful inference are separate checks. The app does not silently substitute an external paid API. Strictly offline use requires a reachable, explicitly configured local or intranet model.

## Use a ChatGPT subscription outside the intranet

Choose Sign in with ChatGPT in the connection settings. Complete authorization on OpenAI's official page in your system browser. The app waits for the official result. Select an actual model returned by OpenCode and apply that subscription connection.

This uses the account's Codex subscription allowance, not an API key. Available models and limits depend on account entitlements; seeing a model catalog is not proof that inference succeeded. Authorization belongs to this installation and is excluded from a clean distribution. Initial login and subscription inference need internet access. Corporate model and real subscription qualification are recorded separately.

## Skills, MCP, and original OpenCode sessions

Open Tools and settings → Manage tools and MCP in the left sidebar. The Skills and MCP tabs manage the corresponding records; choose Import Skill or Add MCP to add your own. Eleven reviewed built-in SKILL.md originals are imported on first start: eeas-acceptance-review, eeas-clarify-gaps, competitive-evidence, diagram-evidence, engineering-requirements, needs-elicitation, problem-discovery, prompt-orchestration, eeas-report-handoff, eeas-source-review, and eeas-task-decomposition.

Existing user records, manual changes, and disabled declarations take precedence. An update does not silently overwrite or re-enable them. Missing or damaged originals are reported. Read the original record to inspect it, or import your own UTF-8 SKILL.md. This text import does not install scripts, references, dependencies, hooks, or an entire directory package.

For everyday work, select a model, attach sources, and type in the current chat. A dedicated original OpenCode session is available through Manage chats in the left sidebar, with creation, reading, reload, stop, questions, and permission replies. Select a connection and Plan or Build as needed. This entry accepts explicitly submitted text; a current page or selected file is not automatically attached. An enabled declaration, a capability loaded in a session, and a real invocation are distinct observations. Configuration changes need a new session; reconnecting an old session does not silently replace its configuration.

For an external local MCP server, prepare its complete offline runtime first. Store a real argument array and actual file hashes. Connect and read tools runs initialize/list_tools and closes the probe; it does not invoke a tool. Program startup can itself have effects. Actual tool execution needs the session's permissions and an invocation receipt.

## VS Code companion

Install the separately supplied companion VSIX when available. It is a client of the desktop app, sharing its local files, conversations, tasks, and recorded artifact versions. It does not run a separate agent or copy model credentials. Choose the complete desktop installation folder and approve the first connection in the desktop app. The app can start in the background; opening it explicitly reveals its window.

Code, Markdown, and exact before/after diffs open in the editor. Office artifacts open in their system application through their recorded references. The companion defaults to VS Code's language and has its own System/中文/English setting. WSL path mapping is a separate optional compatibility boundary; unsupported URIs are not guessed or treated as local Windows paths.

## Verification boundaries and recovery

Cirven does not change display resolution or DPI, reserve an AppBar area, or rearrange other applications. Observing a page, reading an explicitly selected file, opening an artifact, and editing an arbitrary application each require separate evidence. This preview does not claim universal desktop control, an OS network sandbox, or acceptance of every company service and third-party MCP program.

Close and minimize keep the app in the background with its enabled Orb. Explicit Exit is in the tray menu. Running operations and drafts follow the app's existing shutdown checks. If an outcome is unknown, check its original receipt rather than sending the operation again. Keep the visible error and user-data logs; downloading dependencies again is not the normal recovery path. Changing interface language never authorizes a model request, file write, tool action, or replay.
