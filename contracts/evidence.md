# Evidence / 验收

字段：evidence_id、run/attempt/task_revision、test_id/acceptance_hash、source_hash/commit、artifact_hash、stack_lock_hash、runner/environment、source_kind、runtime_kind、started_at/ended_at、command/exit_code、原始日志位置、结果和限制。缺失字段显式 null/原因，不编造。

所有批次依[统一功能门槛](../docs/FEATURE_VALUE_GATE.md)关联目的、痛点/原因依据、方案及前后过程，记录按影响选中的场景/反例及不适用理由。设计风险、已执行结果、未覆盖限制分列；资源预算的计划/估算/实际与硬软边界分开，覆盖主/子Agent、工具、重试及后台总成本，不用某层计数代签全链。复用仍适用的证据，相关变化或未决事实才按需复核；必要本地权限/CAS检查不省略，保存/轮询不自动唤模型。此要求不追写或伪造旧回执，也不证明现有实现已满足所有门槛。

Verifier 检查：确实执行；必需用例数量与标识匹配；无隐藏跳过；结果针对当前版本与实际产物；可重放的测试命令；测试基线未被 Agent 擅改。构建成功与运行通过分开。

所有功能的验收记录同时采用[统一功能门槛的工作验收记录](../docs/FEATURE_VALUE_GATE.md#工作验收记录供所有功能复用)：从用户目标走到真实结果及继续/恢复，记录实际交互成本、整合位置和页面/入口必要性。接口/组件测试、完整工作验收、视觉审查、Windows/内网资格分列；受影响链的已知多余切页、重复输入、冗余入口或默认内部JSON未解决时，不以底层PASS签发该链的功能验收。无界面能力核调用、结果和错误交接；不适用项写依据。旧证据保留原结论与范围，不追签新标准。

来源分类 synthetic/public/internal；运行分类 none/mock/live；环境标本机/WSL2/Windows/内网/实物。一次人工夹具上的 live 成功，不证明复杂工程自主开发率。

日志和报告由受控 Runner 上传，制品内容哈希校验。SHA256 只证明字节一致，不能独立证明来源可信；权限、执行身份和受控上传同样必要。验证器读不可变结果，Agent 不能写验收结论表。

证据关联被修改时返回 STALE_EVIDENCE，源制品被破坏返回 INTEGRITY_ERROR；不得自动重命名旧证据冒充新运行。

外网功能报告列：PASS/FAIL/BLOCKED/NOT_RUN，保留恢复和安全失败记录。报告只能有已执行证据；只生成了测试文件则标 NOT_RUN。
