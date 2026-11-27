# 状态与恢复

执行状态：QUEUED→RUNNING→RETURNED/FAILED；可进入 AWAITING_APPROVAL、CANCEL_REQUESTED、CANCELLED、INTERRUPTED、UNKNOWN_OUTCOME。
验证状态独立：NOT_RUN/RUNNING/PASSED/FAILED/BLOCKED/NOT_APPLICABLE。
任务完成要求相应 revision 的强制验证 PASSED；RETURNED 不等于任务通过。Mock 结果不满足 LIVE 门禁。

接纳/领取用数据库事务和比较更新；首先仅支持单 Worker。作业/审批/事件持久化，重启后扫描未终结记录并向 Runner/底座 inspect：已结束则取回证据；仍活动则续观察；确定未执行才可重派；不能确定则结果未知。

事件 `(run_id, event_seq)` 顺序、去重、续传。请求 idempotency_key 与 payload hash 绑定。重复审批只产生一次效果；过期/旧 revision 的批准拒绝。超时不证明副作用未发生。

修复是新 attempt，保留失败原证据；不能覆写旧 RUN，不能无限重试。失效输入取消未开始任务，运行中按动作安全边界停止。状态表与 UI 只从权威服务读取。

state/SESSION.md 只服务开发会话：当前代码、里程碑、已验证、阻塞、下一条具体动作。软件本体必须从自己的数据库恢复，不需要读开发者旧聊天。接力文件与数据库不得互相冒充。
