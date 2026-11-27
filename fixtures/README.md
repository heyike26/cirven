# 原创夹具，不是企业样例

仅用来验证平台是否真的创建任务、调用底座、执行测试和保存证据。没有数据板、真实协议或业务性能含义。

`python scripts/make_fixture.py --out /指定空目录` 生成：
- `repo/src/samples.py`：有意不完整的简单函数，供 EEAS runtime 修改。
- `public_brief.md`：人工构造的短需求。
- `verification/test_samples.py`：独立测试，置于 Agent 可写范围之外。
- `FIXTURE.json`：夹具/源码/测试指纹，标注 synthetic。

Windows 的 --out 使用自己的合法路径即可。目标目录必须为空；生成器不覆盖已有文件，不联网、不创建模型调用，也不运行 Git。由平台建立工作区时初始化临时仓库并记录基线。

初始测试必须失败，这是夹具健康检查。随后由 EEAS 发起真实 Agent 任务仅修改 repo，再执行验证。保护 verification 目录及固定测试摘要；仅路径分开不够，平台要在执行权限上强制。

`--self-test` 在临时目录确认初始失败，并写入一份参考修正检验测试集本身，再清理。它不调用 Agent，不证明 EEAS 已实现。

其他功能测试用平台开发时新建的 JSON/短文本/事件脚本：正常、错误、取消、崩溃、重复消息、升级变化。不要把夹具输出复制进固定 PASS 报告。
