# 公开参考索引

核查日期：2026-10-12。仅在实现对应接口时读取相关官方页；安装版本仍需 E00 锁定，不把动态官网内容当本地接口保证。以下链接为追溯定位，不要求每个任务全部联网读取。

| 编号 | 官方来源 | 本包仅据此采用的事实 |
|---|---|---|
| S01 | https://code.claude.com/docs/en/agent-sdk/overview | 程序化复用 Claude Code 的工具/循环/上下文；产品认证与开发者交互使用分开核查 |
| S02 | https://code.claude.com/docs/en/agent-sdk/plugins | 插件本地路径加载，运行时加载信息和实际行为要验证 |
| S03 | https://code.claude.com/docs/en/skills | 项目 Skills 位于 .claude/skills，正文按使用加载 |
| S04 | https://code.claude.com/docs/en/headless | CLI 程序化入口可作为具体接入备选，不与 SDK 语义自动等同 |
| S05 | https://fastapi.tiangolo.com/deployment/ | Python API 服务的部署参考，非本包已部署证明 |
| S06 | https://vite.dev/guide/build.html | 前端生产构建与本地静态产物参考 |
| S07 | https://pip.pypa.io/en/stable/cli/pip_download/ | 依赖下载及目标平台选择，不保证复制任意环境即可离线运行 |
| S08 | https://opencode.ai/docs/skills/ | 可读取 .claude/skills 的兼容路径；实际版本仍要核对 |
| S09 | https://code.claude.com/docs/en/llm-gateway | 网关适配边界；不能从外网 Claude 成功推定内网 GLM 支持 |
| S10 | https://code.claude.com/docs/en/memory | CLAUDE.md 导入和项目记忆；本包只导入短 AGENTS |

设计建议、里程碑和安全门禁是 EEAS 本项目要求，不冒充上游产品承诺。开发过程中新增依赖/插件应保存来源和具体版本；本包没有复制第三方实现或下载账号内容。
