# 产品工作流与扩展接口

目标：多资料→共同澄清→模板任务书→task harness→可交互开发/debug→归档/文档/PPT→能力升级。清单docs/PRODUCT_CAPABILITIES.json记录开发目标，不作为运行时资格。

## 版本与权威对象

- InputBatch：同项目多个document revision/rawSHA；逐份状态、错误与原件引用。批次不伪造原子上传保证，断连UNKNOWN要先核列表，不能自动重传已保存项。
- ProjectBrief/TaskBook：目标/约束/风险/缺口/注意项/来源、模板revision/SHA、后台MD与段落索引。expected_revision编辑追加版本；用户事实/模型建议/接受的假设区分，不改原件。
- Template：原文件/章节字段/编号/布局映射与支持范围。MD任务书、DOCX说明书、PPTX母版各自适配；抽出文本不称保留版式。
- ProjectHarness：taskbook精确版本、验收、TaskSpec refs、DAG、角色/工具/产物/检查点/预算/批准scope。拒绝环、跨项目与失效来源；任务/产物能互相追溯。
- Question/Answer/Resolution/StartDecision：精确source/question/answer/task refs，迟答保存与影响复核；答案不是批准，用户明确开始不绕其它门禁。
- WorkspaceBinding：项目/run、实际受限路径、内容SHA快照（Git/worktree为用户项目可选能力，本软件当前禁用）、native session、组件组合。文件/终端/diff/诊断/纠正/cancel/continue/events统一API；独立目录不宣称OS沙箱。
- ArtifactRevision/ArchiveManifest：文稿/代码/图/测试/说明书/PPT原件、来源与valid_for_revision；可阅读编辑、比较、下载。归档成员SHA、真实调用/费用、失败/NOT_RUN；经验与skill追加版本，旧证据不覆盖。

## 原生底座与自定义能力

优先复用原OpenCode Web/UI/SDK。固定OpenAPI逐method/path/operation→原UI或EEAS入口→权限/依赖→真实case/回执映射；声明/安装/资格分开，路径数量不签全部功能通过。EEAS保持项目源/预算/批准/验收，原底座负责唯一Agent Loop。

ProviderProfile可配置协议/baseURL/model/headers/代理/context/stream/tool/vision/价格和secret refs；复用官方provider/auth插件，API key与OAuth分别实测。未知费用保留UNKNOWN，仍执行可强制调用/步骤/时限。credential/路由版本冻结且日志脱敏。内网公司API与搜索MCP可配，WebFetch仍禁用；外网开发下载是另一权限场景。

CapabilityRequest→ImplementationCandidate→Qualification→ComponentSet。来源为成熟模块/plugin/MCP/client/skill或用户需求生成的代码；manifest保留接口schema、permissions、source/LICENSE/hash、Windows/Py310/Node/ABI完整依赖、tests/migration。沿现有extension/component_upgrade门禁，导入不等于启用，不运行时npx在线补包。外网download与内网local-import同格式；自适配产出可审阅patch和实际回归，失败保留候选、不改活动组件。用户所需授权走实际认证流程及精确scope，不能把可配接口称为服务已授权。

## 页面与验收

文稿以章节/表格/来源侧注呈现，任务显示依赖/状态/可展开行动，开发显示会话/文件/终端/测试/diff；MD仅显式导出/高级编辑。按钮必须有真实处理中/失败/重试边界，多窗口同API；长产物有目录/分块和易懂摘要。键盘/移动/reduced-motion与银白磨砂视觉逐流程验证。

每片实际代码＋正向/错误或重启/版本反例＋输入/源码/标准冻结＋接力；不改原tests/上限/失败。所有新增功能进入10公开项目逐功能审计，不止旧41case。native Windows/company/OpenCode未测能力保持明确资格边界。

原OpenCode与EEAS全部页面统一银白磨砂、已本地字体/字号/配色/间距、自动导航/弹窗/微动效；通过ThemeAdapter共享semantic tokens，原Solid组件与行为保留，禁止只嵌默认原UI宣称视觉适配完成。

最终产品是Windows桌面App，Web仅调试；优先复用OpenCode Electron壳，EEAS Python3.10本地sidecar与共享renderer，原生窗口/菜单/文件/多窗口/启动关闭/本地安装更新要实测。默认丰富完整动效，不提供手动减少动效入口；OS级偏好另验收。细则docs/UI_CRAFT_CONTRACT.md。

开发策略：docs/DYNAMIC_WORKFLOW.md；用户最新指令优先，当前直接文件修改，不创建Git/worktree，基础能力全完后才做一次Git备份。来源/模板/任务/验收依旧按SHA追溯；这不阻止实现面向用户项目的可选VCS适配。
