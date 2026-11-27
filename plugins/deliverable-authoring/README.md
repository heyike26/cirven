# 可选汇报制作包 0.1.0

自有方法Skill＋官方MCP SDK stdio runner，复用EEAS现有ReportArtifacts/python-pptx1.0.2。**不是独立OpenCode即装即用包**：必须有匹配EEAS宿主源码、既有项目SQLite、官方MCP SDK及原离线PPT依赖closure。新包不默认启用，不改变核心依赖、安装器或模型loop。不含Office自动化、Excel制作、任意文件路径/源文件写入或视觉验证。

## 两步不同的接入

1. 用现Skill管理导入`skills/eeas-deliverable-authoring/SKILL.md`，id为`eeas-deliverable-authoring`。这只导入MD；模板、脚本和依赖不会自动安装。用manifest核对实际原件。
2. 在现本地MCP管理配置经确认的绝对argv：`<matching-python> -I <plugin>/office_mcp.py --data-dir <existing-data> --project-id <exact-project> --source-root <matching-eeas-source>`；可执行文件、runner、manifest、Skill及依赖closure按现规则固定SHA。当前主安装器不打包plugins目录；本包需独立复制/导入并准备既有optional依赖。配置文本不是实际加载/调用通过。

`--data-dir/--project-id/--source-root`由host配置冻结，不是模型参数。Python固定3.10，仅从manifest声明的宿主`runtime/site-packages`或开发`.venv/lib/python3.10/site-packages`布局加载已有依赖，不下载/另猜环境。数据目录和数据库必须已存在；runner不创建项目、不扫描用户目录、不读取账号。只读exact已注册资料，在既有registry写新PPT。固定源码不符拒启动；旧任务不改路径。

仅为`make_report_deck/get_report_artifact/get_report_operation`选择原session工具allowlist及必要PROCESS_START；技能导入不自动授执行权。MCP前缀依原CLI实际注册为准，需原会话load/call回执。没有自动npm/pip安装、网络资源抓取或隐式重试。

## 创建与核查

`make_report_deck({request})`的request沿用`ReportArtifactInput`：

```json
{"idempotency_key":"explicit-report-001","filename":"汇报.pptx","report":{"schema_version":"eeas.report-deck.v1","title":"本次结论","source_refs":[{"kind":"document","project_id":"HOST_BOUND_PROJECT","document_id":"REGISTERED_ID","revision":1,"sha256":"EXACT_SOURCE_SHA256"}],"sections":[{"title":"结果与边界","paragraphs":["仅依据已核资料。"],"bullets":["视觉与原生Office尚未验证"]}]}}
```

可选`template:{document_id,revision,sha256}`必须是本项目已有PPTX资料；宏、外链和不支持slot由原exporter拒绝。输出bounded `artifact_manifest`（标明摘要）、exact `artifact_ref`、真实source refs、下载URL及NOT_RUN视觉/NOT_PERFORMED语义验收。不传二进制给模型，不承诺文字fit。默认PPT只有现文本模板能力。

`get_report_artifact({artifact_ref})`只接受`{project_id,id,revision,sha256}`，核实际字节与OOXML，回有限slide文本与coverage；不是截图渲染。`get_report_operation({idempotency_key})`从原claim/产物核查：缺记录NOT_ADMITTED、已收未定UNKNOWN_NOT_REPLAYED、创建调用抛错CREATE_RETURNED_ERROR_NOT_REPLAYED；后者不是语义验收。都不会调用create。

首次创建先核结构/项目/来源，用既有SQLite锁＋O_EXCL/fsync写admission，释放锁后仅调用一次ReportArtifacts.create。重复键只查原请求/产物；不同输入拒绝，UNKNOWN不再执行。不完整claim保UNKNOWN，不删/重做。admission只记发送事实，不替代原artifact权威，不建新DB/owner。每项目最多1000项，满时拒新收件并保全部旧项；没有自动归档/清理，长期滚动仍待宿主能力。磁盘满不删原件。

## 依赖、许可与资格

制作库复用`vendor/office-pptx/{linux,windows}-py310/v018`固定closure，见manifest。依赖通过项目原有optional离线方式准备，本包不安装/拷贝到core，不含专有Office Skill脚本。自有代码/Skill采用MIT，原python-pptx MIT与其他closure原许可不变。

本包限定工具层真实生成与读取。通用plugin产物→App回复卡投影仍缺；原生打开/FileContinuity由独立宿主adapter处理，本包不能因此签完整闭环。Windows渲染、Office兼容、Pro/公司模型、未保存buffer须独立验收；WSL真实PPT与CLI测试不代签。
