# 无样例的验证策略

三轴记录每项验证：source=synthetic/public/internal，runtime=none/mock/live，environment=local/WSL2/Windows/intranet/hardware。不使用一个含糊的 PASS 混淆它们。

1. 单元/契约测试：真实数据库、状态迁移、事件排序、版本校验；固定输入不调用 LLM。
2. 集成/E2E：API＋UI＋MockAgent＋真实本地测试命令；Mock 只替代模型侧，不替代存储/Runner/Verifier。
3. LIVE：EEAS 发起 SDK/CLI 任务，在新建小仓库中产生修改，再由独立测试验证；外部 Claude Code 手工完成不能充当此记录。
4. 故障注入：取消/失联/重复消息/损坏产物/旧版本证据/未授权动作/升级失败。
5. 离线：干净的匹配目标环境中，禁外网启动/安装/恢复；模型调用禁用或指向获准内网服务。

夹具见 fixtures/README.md；不要追求“看起来像真实板卡”。每个功能至少正常、失败、恢复或边界用例。公共代码若采用，应记录许可和固定提交；本包提供的是原创测试数据。

验收基线在该次运行前冻结，测试在不同目录/权限下执行；修正错误测试须独立记录并重跑，不删除失败用例让版本通过。测试代码也会执行任意代码，Runner 必须隔离。

测试失败原始输出保留，不只保存 AI 总结。BLOCKED 与 FAIL、SKIPPED 分别计数；框架发布条件见 specs/release.md。
