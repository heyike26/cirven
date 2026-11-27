# TaskSpec / 运行契约

定义在 EEAS 自有 schema 中，不绑定 SDK 对话 JSON。schema_version 必填。

TaskSpec：task_id、project_id、task_revision、目标、requirement_refs、contract_refs、input_refs/hash、base_commit/source_hash、workspace、allowed_write_paths、dependencies、required_capabilities、runtime_profile、runner_profile、budget、acceptance_ref/hash、expected_artifacts。

TaskRun：run_id、attempt_id、task_revision、stack_lock_hash、session_id、runner_job_id、execution_status、verification_status、last_event_seq、开始/结束/心跳、错误类型、evidence_refs。session_id 可空且不全局唯一；使用底座/实例/会话组成引用。

Task DAG 拒绝循环、跨项目未授权依赖和指向已失效需求。依赖满足后由调度逻辑派发，不能靠 LLM 口头承诺顺序。输入变化建新 revision；已有运行继续使用旧快照或按策略取消，结果不得自动满足新版任务。

初始预算建议：每切片一个活动实现任务，修复最多 3 次，每次有新证据；外网 live 默认需明确预算配置。时间/步骤/Token/费用分别记录；用量不可得记 null，不能当作免费。未知金额时执行可强制的时限与步骤上限。

Agent 输出只是候选。验证规则必须在运行前确定；生成的开发测试不替代受保护验收。release verdict 由验证服务计算，不接受 Agent 自写 PASS 文件作为唯一依据。
