# AgentAdapter

最小接口：describe_capabilities、submit、inspect、stream_events、request_cancel、export_result。可选：resume、steer、fork、原生 subagents、plugins、hooks；支持性按实际版本和实验登记，不伪造等价能力。

提交绑定 task/run/revision、工作区、输入资料、权限、预算与组合锁。返回稳定执行引用；事件保留标准部分和脱敏的 native_payload_ref。不屏蔽更强底座的原生能力，也不把原始配置无检查透传成任意权限。

外网先用获准的 Claude Agent SDK 认证与运行路径。[S01] 开发者能在 Claude Code 聊天，不代表 EEAS 程序已有同一额度或授权；产品鉴权按官方支持方式验证，不复制个人登录缓存/订阅 Token。不存在凭证时 NO_CREDENTIALS；Mock 开发继续。

EEAS 发起请求→底座真实读写临时项目→事件/差异→独立测试，这才是 LIVE 接入通过。手工在本包外改好文件再上传日志不算。

底座内部结束只记 RETURNED。恢复会话不等于恢复项目或外部动作；新指令区分补充、排队、steer 和需求变更。cancel 是请求，只有底座/进程确认停止才记 CANCELLED。

版本锁包含实际二进制路径/版本、SDK、模型/网关配置标识、插件树哈希、配置源和平台。新插件按 E08 处理。内网保留 Claude Code，通过公司 settings 路由到用户指定的 DeepSeek“4.1flash”；实际 model ID、认证、API 协议、多模态和费用须在内网独立资格验证，不由外网结果推定。[S09]
