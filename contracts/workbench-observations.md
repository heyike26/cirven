# 工作台图片、离线 OCR 与原软件窗口

这些接口观察实际像素，不把 OCR 文字当作已核实的文档或工程事实。模型、库和模型许可随安装包冻结；启动和识别不下载。Windows 与每个已有软件的窗口嵌入、后台捕获须分别实机验收。

位图必须是当前项目已绑定来源：LOCAL_FILE{file_source_ref:FileSource,sha256}，或 NATIVE_CAPTURE{view_ref,frame_revision,sha256,binding_sha256}，并含 kind。不接受任意路径、URL 或客户端自报图片。local admission 只读冻结版本及安全句柄内的当前原件，不能在反馈写事务内另开观察写事务。变化拒绝，历史记录保留。

GET /api/workbench/ocr/capabilities 返回 CONFIGURED 或 UNAVAILABLE 及原因。CONFIGURED 只表示组件准备好。POST /api/projects/{pid}/workbench/ocr{bitmap_ref} 运行实际 CPU OCR，返回 observation_ref{id,revision:1,sha256} 和 payload{project_id,bitmap_ref,recognition,authority,semantic_correctness}；GET /ocr/{id} 恢复原观察。同一项目、位图和组件摘要幂等。

recognition 含真实 text/lines/confidence、四点 polygon 和可用字符框；坐标为原始解码图片像素，不能当作页面文本坐标。空图为 NO_TEXT，不造字；语义需用户检查。OCR_LINE{observation_ref,line_id,char_start,char_end} 用 Unicode codepoint 定位，选文须附原文 SHA。selection 必须同时含同一 observation_ref 的 ocr_ref 和对应 bitmap_ref；跨图片、跨项目、伪造或过期原件拒绝。

截图圈画或 OCR 选文只记录反馈，保留“加入聊天草稿”和“直接发送”两条路径。前者积累附件与现有消息，明确发送后才请求 Agent。后者保存反馈后发一次原 OpenCode 修订。文本模型使用实际本地 OCR；配置明确支持图片的模型才接收同一个原 Loop 的原生 file part。缺组件或不支持的格式明确失败，不暗转其他付费模型。

原软件由受信 Electron main 经单次动作授权打开；renderer 不提供命令、PID 或 HWND。后端绑定主进程真实 HWND/PID/session/DPI 与精确 FileSource。只嵌入可确认归属的新窗口；复用已有进程且归属无法确定时保留外部软件打开，不强行接管。capture 仅抓指定窗口，返回固定 frame_ref 与实际 PNG 同源下载 URL；不退回全桌面、前台猜测或仅按标题匹配。

编辑原软件与捕获批注是独立模式。关闭、退出全屏或退出 EEAS 先恢复原窗口与布局，不杀用户软件；恢复失败保留 lease 并明确重试。窗口身份不等于活动文档内容身份。正文 Word 选文使用实际 DOCX SHA 与 OOXML 段落位置；复杂字段、页眉页脚和无法唯一定位的文字保留为待核查。
