# Intranet models, Skills, and local MCP setup

[简体中文](INTRANET_SETUP.md) · [Start here](README_FIRST.en.md)

These instructions apply to the Windows desktop package launched by START_EEAS.cmd, with its bundled runtime. Earlier .venv or browser-only handoff instructions do not apply to this package.

## Model connection

Open the model selector in the composer, choose Configure connection, and enter:

| Field | Requirement |
| --- | --- |
| Connection ID | A unique local identifier, such as intranet-model |
| API URL | The actual HTTP/HTTPS OpenAI-compatible Chat Completions endpoint, ending in /v1; no credentials, query, or fragment in the URL |
| Model ID | The exact ID supplied by the service, not a display or marketing name |
| Authentication | An issued API key or an explicit no-key choice; ambient environment keys are not imported |
| Advanced connection | Optional private PEM CA, proxy, ordinary/private headers, and timeout; otherwise system CA, direct connection, and default timeout |
| Image input | Enable only when the service supports it; this is a declaration that still needs a real response check |

http://intranet-host:8000/v1 is an example format, not a service included in the package. Supported explicit settings include no-key, Bearer, custom authentication headers, PEM CA, and HTTP/HTTPS proxies. The actual corporate gateway and other protocols need separate validation.

Advanced settings are replaced as a whole when edited. Old secrets and private headers are not returned to the form. Re-enter or explicitly reset them to avoid accidentally clearing a connection. A selected or pasted CA is saved locally and is not sent to the model. TLS verification remains enabled. There is no implicit environment proxy/key, cross-address redirect, or paid fallback.

Saving is not a model request or connectivity check. After saving and selecting the connection, send a small text request in the current chat with no attached files or changes. You do not need to open session management for this check. Inspect the actual completion/error, model ID, and response. Image input, tool calling, context length, and corporate billing rules need their own checks. If saving times out, inspect the original operation before repeating it.

Credentials remain in the installation's private local data, not the model list or clean production archive. Protect that user-data folder. The app does not preconfigure a developer ChatGPT subscription. OpenCode's official subscription authorization is a separate path from API settings and requires internet access; strict offline use needs an explicitly configured reachable model.

## Import a Skill

Open Tools and settings → Manage tools and MCP in the left sidebar, select Skills, then Import Skill. Select or paste an original 1–65536-byte UTF-8 SKILL.md with YAML metadata, for example:

```markdown
---
name: my-review
description: Review the selected sources and return evidence-backed suggestions.
---
Check the supplied sources and scope before reviewing. State any missing evidence.
```

The Skill ID must exactly match name, using lowercase letters, numbers, and hyphens. Save the original, then inspect its revision and source integrity. An enabled declaration does not authorize execution. Reading, editing, or disabling a record preserves version history. Import does not run scripts, install dependencies, start MCP, or request a model.

The eleven built-in originals are in packages/planning/skills/*/SKILL.md; registry records are in local data. Existing user records and disabled declarations are preserved. Damaged originals are not silently repaired or overwritten. Directory packages, scripts, references, and their dependencies are not automatically imported with a single SKILL.md.

## Register a local MCP stdio program

1. Place the supplier's complete offline MCP program, runtime, and dependencies on D: or E:. A self-contained executable or supplied runtime/script can be used. Avoid npx/pip/uv launch commands that download missing components. The bundled bootstrap only launches its own registered modules, not arbitrary third-party Python packages.
2. In Tools and settings → Manage tools and MCP, select MCP, then Add MCP. Choose Local stdio process and a unique MCP ID. eeas_desktop is reserved; service names must also avoid ambiguous underscore-separated prefixes.
3. Supply a JSON argument array with an absolute executable path and individual arguments, not a shell command string. Register the executable and every absolute script/configuration argument with its real lowercase SHA256. Files must exist and be ordinary files.

```json
["D:\\MyMCP\\server.exe", "--stdio"]
```

Get an actual hash in PowerShell and enter an object whose key is the complete absolute file path:

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath 'D:\MyMCP\server.exe').Hash.ToLowerInvariant()
```

4. Set a timeout from 100 to 120000 ms. The default and suggested first connection timeout is 60000 ms; a real Windows cold-start handshake has exceeded ten seconds. Save the enabled declaration. This interface accepts arguments, hashes, enabled state, and timeout; it does not import environment credentials.
5. Choose Connect and read tools. This starts the local process, runs MCP initialize/list_tools, and closes the probe child. It does not invoke a tool. Startup can have effects; a successful handshake does not certify tool results or offline behavior.
6. Create an original session against the current configuration. Explicitly allow the local process when needed and select actual tool names in the allowlist. Reply to the session's permissions and inspect real invocation receipts. A changed configuration needs a new session; old probes and sessions do not certify it.

After moving the external program, update its paths and hashes. File hashes do not cover every dynamic dependency or arbitrary program behavior. The app does not automatically unpack or install third-party npm/Python dependency packages.

Remote HTTPS MCP endpoint declarations can be saved. The management panel does not provide remote authorization/connection probing; the original session's unauthenticated remote connection needs explicit permission and observed status. OAuth, authentication-header management, and ambient credential import for remote MCP are separate boundaries.

## Evidence, permissions, and interface language

Configuration saved, declaration enabled, probe initialized/listed, original session loaded, and actual tool invoked each have separate receipts. Saving a Skill or MCP configuration grants no tool, file-write, process-start, or model permission.

Plan/Build, original Task/subagents, questions, permission replies, event reading, and stop remain available through the original session according to its actual capability list. This text-session entry does not silently inherit a current page or selected file. Missing authority or an unsupported target remains explicit.

The core Python/Electron/OpenCode/ripgrep/font/OCR files are bundled; startup does not download missing components. A selected network mode or tool admission is not an OS network sandbox. Third-party tools and configured services need separate network validation.

Record the actual machine, package manifest hash, target application, exact model ID, observed result, and permitted scope when qualifying an intranet installation. Component imports, protocol fixtures, and synthetic files do not establish universal enterprise acceptance.

Choose the interface language in desktop Settings. Startup, tray, and quick surfaces reuse it; the VS Code companion has an independent language setting that follows VS Code by default. Language switching is local and uses no model tokens. It never translates your inputs, paths, original Skill text, tool output, generated reports, or saved evidence. Choose a report language as part of the task or template requirements.
