# Runner / 设备扩展

Runner 接口：capabilities、submit_job、inspect_job、cancel_job、fetch_artifacts。输入：job_id、operation_id、workspace/source_hash、argv、允许环境字段、timeout、output_limits、资源和验收引用。调度端重复提交同一 operation_id 时查现有操作；不同 payload 用同 ID 拒绝。

实际执行与 journal 独立于 HTTP 请求存活；先记请求，启动/结束/取消分别记事实。派发中断不能一律重新启动，先 inspect。进程句柄、pid＋创建时间等用于本机查证，不能仅凭 PID 断言是原作业。

Windows Native 与 WSL2 是不同 Runner profile。命令用参数数组，记录 shell/编码/退出码；中文/空格路径、换行、超时及进程树停止都要测试。Python/CMake/MSBuild 只接入实际安装者，不假装通用 Runner 已支持全部工具链。

取消构建可强制停止进程树；取消实物操作未来需动作策略，不能通用 kill。读取不到结果标 UNKNOWN_OUTCOME；资源在确认停止/查证完成前不释放。

外网仅定义 DeviceAdapter 的 identify、health、claim/release、read_state、execute_typed_action 和操作记录；默认 NOT_CONNECTED，写操作拒绝。不做虚构板卡驱动，不用假硬件 PASS 填补空白。

平台运行的测试也是任意代码：在与 Agent 分离的受控环境执行并限制资源；原始输出可截断展示但完整受限存储或明确记录截断。
