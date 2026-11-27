# 最小架构

当前实现：Python3.10/FastAPI、SQLite与显式迁移、本地制品、Worker/Runner，前端使用本地HTML/CSS/JavaScript和字体。以实际依赖锁及源码为准，不把初期React/Vite建议当成已部署栈。

```text
Web / Terminal / API → EEAS API/项目域 → 资料/Brief/问答/任务/审批/事件
                         ├─ Worker → RuntimeAdapter → 单一选定Agent Loop
                         ├─ Runner → 本机或 Windows 命令
                         └─ Verifier → 测试记录/制品/放行
```

项目入口采用docs/WORKFLOW_HARNESS.md：资料整理与反向澄清先于执行，默认清晰后启动，用户可明确选择直接开始。新增Brief/Clarity/持久Question及依赖等待仍待实现；现有Requirement确认不等价于完整项目澄清。

复用底座文件工具、Agent Loop、上下文和原生子代理；EEAS管理项目域、证据与资源，不将LLM放入实时控制。下一RuntimeAdapter试点首选OpenCode serve+HTTP/SSE，现Claude Agent SDK已有实现与验证保留。每个run只用一个底座；既有批准、预算、冻结与search-only限制在EEAS保持权威。LangGraph等仅在已证实调度缺口时采用，不为组合模块叠加Loop。

当前代码边界：`apps/api`、`apps/web`、`packages/domain`、`packages/adapters`、`packages/runner`、`packages/verifier`、`tests`。新能力进入这些边界及版本化契约，不能只有空插件接口。

单节点持久任务仍须明确：数据库事务接纳/领取、单 Worker 所有权、稳定 operation_id、Runner 日志、启动后查证；进程内 Future/HTTP 后台任务不能成为唯一作业存储。先做到可靠单 Worker，不假装具备分布式 exactly-once。

项目域数据库拥有需求与验收；作业服务拥有执行状态；Harness 拥有会话；Runner 拥有进程事实。开发用 state/ 是人工接力，不作为软件运行数据库。

内外网同一核心；公开适配器与内网私有扩展分离。模块优先深融合优秀开源代码/插件，原源码、许可、离线依赖闭包、接口版本、升级迁移与回退一并交付。API、存储、任务、身份、资料、模型与设备留清晰接口。环境配置化，禁止硬编码公网URL、用户目录和凭证。
