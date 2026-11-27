# 2026-10-16 对照经验

参考用户给的 ChatGPT Work 截图、本机空白对话截图、公开 ChatGPT/Claude 产品资料。只研究排布/字体/字级/间距/组件；Cirven 色彩、品牌与功能不变。物理截图192/144 DPI不能直接充当 CSS 尺寸。本轮第一构建证据D:/EEAS-qualification-20261015/ui-source13-layout/first-rendered-build，原截图/失败与版本记录在同根 source13-ui-layout-check；不把它们冒称最终构建或Windows资格。

| 已发现问题 | 原因与修正方向 | 必须实际查看 |
|---|---|---|
| 正文、按钮和说明挤成同一层 | 桌面原14/13/12且信息默认展开；桌面16/15/13、窄VS Code14/13/12并保24px正文行高和较大宿主字体，分组留白优先，低频详情收纳 | 实际中文/英文计算样式，回复与输入同阅读轴 |
| 输入框被关联、恢复、批注全文占满 | 把后台状态当常驻内容；改一行文件chip/引用汇总，保可见错误，详情锚定展开 | 重载/切作用域/移除引用后同草稿、同文件依据，未请求模型 |
| 模型选择多一层；重复保存造新版本 | 在菜单里又塞select，已保存状态仍走POST；直接选行，只有精确已保存id/model/ref一致才复用 | 选择关闭/方向键/清空保留；复用不借旧验证，不新增请求 |
| 长路径把删除也裁了 | flex裸文本上ellipsis无独立收缩边界；文本min-width0，按钮不缩，完整路径可查 | 320/430px、长英文和中文、逐项移除可达且不丢输入 |
| 小高度登录被composer遮住 | 只看window rect，没看祖先裁剪；短高收装饰与重复说明，保主要操作 | 620x480中英，按钮有效交集、滚动、输入区与正文无重叠 |
| 模型菜单重开焦点落关闭按钮 | 父RAF先设focus后被Primitive默认初始focus覆盖；交给onOpenAutoFocus | selected→Home/End→Esc回原按钮，IME不误提交 |
| 抽屉被误报右溢5px | 观察发生在180ms入场中；等待该元素有限动画完成再测稳定框 | 不关闭动效、不降边界标准；稳态与过渡证据分开 |
| 看似服务冷启动，实际子服务早退 | 包装脚本吞stdout后盲等端口；握住自有进程、保独立日志 | 启动/首屏/截图阶段准确标注，保第一次失败 |
| 门户弹层丢字级/颜色，VS Code白底割裂 | Portal不继承根class；VS Code是宿主主题表面 | Portal明确token，运行中light/dark/HC/自定义切换和焦点对比 |

新一轮用户反馈：布局间隔必须4px刻度；默认说明过量、重复模式/标题、过大字级同样造成拥挤。空态减重复说明，品牌字形只作一次轻表达；Ctrl+Enter、错误、权限边界等必要信息保留可达。源代码改动仍需新构建实测，不能沿用上一轮截图。

尚未确认的问题不能写成闭合经验；首次构建的其它失败与最终确认结果以独立回执为准。默认字体/间距是本产品选择，不能泛化为所有产品硬规定。

## 本批已验证的补充

Hallmark只读审查找到Provider的max-width被更高specificity旧规则压过，以及label/input margin与grid gap叠加。修正在同一表单scope定义760px单列与零margin，由单一gap表达8px标签关系。final-03真实生产界面/API的6组中英/宽窄实测760/390/580px、单列、8px和零margin全部通过；不是仅检查CSS声明。21项browser/API与79项前端测试通过，源前后一致。

Windows本批实际更新并查看companion及模型弹层，Cirven标题、本地Manrope/Noto字形、弹层边界/选中焦点/关闭还焦点有实证；D:/EEAS-qualification-20261015/integrated-interaction-r8-r4/windows-gui-v13/LAYOUT_RECEIPT.json。不据此签原生Office、真实订阅推理或全部Windows功能；VS Code新版仍以浏览器主题测试与VSIX包为本批证据。

## 同版本观测、确认与窗口往返

- **版本相同不代表画面数据不变。** Windows实测回答已入库，revision/SHA未变，旧宿主却不刷新。保不可变payload/key，只更新同project/id/basis的回答与来源观测，保同DOM、后续输入、焦点、滚动和图状态。`CONFIRMED`是历史确认；来源变旧时readiness仍可为false，不能丢弃这次观测或继续显示可规划。
- **确认回执是新版本。** 核原revision/SHA、候选/来源和无执行授权；没有新输入且原面板仍属当前作用域时转入精确新版本。等待中新增输入或切项目则保原稿、保新版本可查，不让迟回执抢导航；即使确认已提交而来源随后变旧，也应展示新版本及待复核状态。
- **尺寸要测连续变化。** 宽窗侧栏调大后缩至尚未进入compact的宽度，固定列总宽可能超视口、被overflow隐藏。核restore/拖动/resize同一预算，并在真实页面做宽→窄→宽与长标题检查；fresh窄窗截图不能覆盖这条路径。

证据：`D:/EEAS-qualification-20261015/source15-discovery-answer-browser-v1/frozen-r2/FINAL_RECEIPT.json`为真实built App＋API/SQLite三尺寸六问、等待中改稿和确认3/3通过，0模型调用，非Windows；Windows原反例保留在`integrated-interaction-r8-r4/actions-v14/v14-b-ui-answer-inspect.json`。`source15-artifact-observation/r3-readonly-review/FINAL_RECEIPT_R3.json`为stale语义28项Client回归及类型检查，冻结R2两反例均失败、R3均通过。后续独立`source15-discovery-answer-browser-v1/frozen-r4/FINAL_RECEIPT.json`为5/5真实built App＋API/SQLite：含confirmed后资料变更、同pane补充保存保稿，以及1800px调宽两侧再缩950px（235/320/383px三栏、右边950px）无裁切；源码前后一致、0模型。此为WSL/Web调试资格，仍不代签Windows原生交互。

## 目录装饰与恢复卡死

`MutationObserver`观察子节点时，派生装饰重复赋同值`textContent`仍会替换文本节点，产生新通知；再用microtask重绘会自激，使心跳与正常退出受阻。装饰文字、title和aria仅在值确实变化时写，仍保真正行状态与语言更新。回归用真实受限目录先建baseline，再新增、修改、删除，经过刷新和重启核心跳与CPU；不能用fresh UNCHANGED目录或仅API响应替代已有文件状态的恢复路径。

反例：`D:/EEAS-qualification-20261015/source15-explorer-marker-repro-v1/frozen-before/FINAL_RECEIPT.json`绑定旧构建，实际App＋API/文件系统中UNCHANGED正常，ADDED/MODIFIED工作台与MODIFIED轻浮窗均卡住、同值装饰写入达到10000次，35秒自有watchdog终止测试；原失败保留。`source16-explorer-observer-fix/FINAL_RECEIPT.json`仅证明源码提取的真实Chromium DOM回归6N/6O及原Explorer相邻10项通过，冻结旧源码同测试2项失败；修后完整App重启与Windows CPU/退出仍须分别实测，不据此签已恢复。


## 关联控件与图的可读性

窄栏不重叠仍可能读错关系：等待选项若跟确认按钮同排、生成按钮掉到下一行，就让选项看似属于确认。把选项和其作用的按钮作为同一局部分组，中英分别查看自然换行和实际阅读顺序。`D:/EEAS-qualification-20261015/planning-wait-budget-implementation-v1/frozen-r2/FINAL_RECEIPT.json`为320/760px实际组件N/O证据；不是完整App或Windows资格。

图的默认可读视口与显式全图概览分开，不能只靠fit让全部节点挤进画布。初始化等持久视图和非零canvas就绪；中文换行、节点详情和关系全文实测。保存的坐标/缩放与未发送勾画优先，缩窗不重排。实际几何通过后还需等待非空像素并看截图；`D:/EEAS-qualification-20261015/source18-graph-api-browser/frozen/FINAL_RECEIPT.json`验证新9节点与旧7节点的真实App/API持久化及缩窗。图仍需平移，未证明全部边文字零碰撞或Windows图交互。

## 按真实产品参考，避免套模板

重要页面先选同类任务界面，区分真实App/官方截图/源码/营销页；抽取阅读轴、字级角色、组内与组间留白、主次动作及信息显隐，记录直接观察与推测。参考不能替换Cirven配色/功能或VS Code宿主主题，也不能拿物理截图像素冒充CSS尺寸。删除重复教程、装饰标题/标签、卡片套卡片与无理由图标底板；个性放在统一品牌与有目的动效，不靠堆装饰。

2026-10-16实际参考：用户ChatGPT Work实屏；[Linear官方界面更新](https://linear.app/now/behind-the-latest-design-refresh)的三张前后图已亲看；[Claude产物面板键盘文档](https://support.claude.com/en/articles/17457950-use-artifacts-with-a-keyboard-or-screen-reader)仅证明焦点交互、不证明其视觉尺寸；OpenCode固定UI源。范围与取舍见D:/EEAS-qualification-20261015/source28-reference-study/REVIEW.md。浮标是独立紧凑点按界面，不使用正文宽松半径。

## 启动与等待的连续性

启动候选须用同一个真实 Logo、窗口比例和可播放动画对照。短入场只播一次，等待反馈单独循环；真正就绪可随时结束，不添加假百分比、技术阶段或重复教程。阶段变化保同一文档，避免每个阶段重载导致动效从头播放；后台恢复不重播开屏。视觉改善与冷启动耗时分别记录。

独立复建除了 JS/TS 输入，还须绑定 CSS url 引用的字体、SVG 等资源。构建 exit0 仍可能包含未解析字体的警告；必须检查警告与实际资源闭包，不能以输出数量相同认定排版一致。Source32 首轮缺两个旧字体的构建保留但未选用，补齐原字节后才冻结新版。

## 启动与等待的连续性

启动候选须用同一个真实 Logo、窗口比例和可播放动画对照。短入场只播一次，等待反馈单独循环；真正就绪可随时结束，不添加假百分比、技术阶段或重复教程。阶段变化保同一文档，避免每个阶段重载导致动效从头播放；后台恢复不重播开屏。视觉改善与冷启动耗时分别记录。

独立复建除了 JS/TS 输入，还须绑定 CSS url 引用的字体、SVG 等资源。构建 exit0 仍可能包含未解析字体的警告；必须检查警告与实际资源闭包，不能以输出数量相同认定排版一致。Source32 首轮缺两个旧字体的构建保留但未选用，补齐原字节后才冻结新版。

## VS Code 图标随主题适配

- 编辑器工具栏把命令 SVG 当背景图加载时，外部 SVG 的 `currentColor` 不继承按钮前景；不能只核按钮 computed color。命令用官方 `icon.light/dark` 资源，内嵌 Webview SVG 继承宿主前景，容器图标按其实际 mask 合同处理，不把不同入口混为一种加载方式。
- 同批核对打包器的资源复制和 manifest 重写；源文件正确不代表已安装图标正确。用实际 Code 的深浅与高对比主题核所加载资源、可见像素和即时切换；悬停、禁用等测有限过渡后的稳态。保留宿主主题、品牌几何及原窗口和草稿。

反例与修复：0.1.10 工具栏前景为浅灰，外部 `currentColor` SVG 仍呈黑；0.1.11 分主题资源并改 Webview 内嵌单色标志。实际 Code 对照在 D:/EEAS-qualification-20261015/source35-root-native-code/，源码与限定浏览器检查在 D:/EEAS-qualification-20261015/vscode-theme-icon-v0111/；模拟宿主夹具不替代实际 Code。


## 2026-10-16：长历史、原生窗口与资源成本

- 工具状态只更新变化的消息；按消息及其精确文件/token复用当前投影DOM，移出投影或切作用域即释放引用。不能因为数组更新就replace全部历史，导致文件链接焦点丢失。输入ACK、无内容cursor不应触发重建；不为提速另造无界Markdown缓存。实际依据D/vscode-history-performance-r4，headless样本不代Windows或RSS收益。
- 展示历史diff必须来自原任务完整快照或明确标注的patch片段。当前磁盘、编辑器dirty buffer不能冒充任务旧版本；查看不写源文件、不增加模型调用。精确会话/来源代次与快照SHA同时绑定，过期迟回不抢页。
- 正常打开App的大窗、浮标唤出的小窗、原生文档打开是不同展示意图；恢复与迟回不得暗自最大化、缩窗或置顶。pin是单独可选状态，图标须显示真实pressed并持久到同一偏好源。浮标/批注覆盖层的功能topmost不等于聊天永远置顶。
- 浮窗从真实浮标位置连贯展开，按同一DIP显示器向内翻转、有边界净空；有限transform/opacity可中断。Electron setBounds的animate不能假定Windows可用；不要每帧跨进程搬HWND或新建常驻动画窗。仅原生/实际透明surface能证明外框形变，opaque窗口中的CSS揭示不代签。
- 先测空闲进程树、private bytes与交互耗时，再决定是否懒加载冷门页面。RSS求和包含共享页，不称独占物理RAM；接口限长不等于进程内存上限。存储可依空间配置，缓存/队列/在途数量与清理仍需有界。
- UI字正确时，不用文稿字体问题重做UI。中文Word新增内容需核eastAsia字体、主题与语言地域；英文默认en-US可能让正确Unicode显示出不合适字形。模板明确选择优先，字体修正与原文字错别字分开，最终用本机Word渲染验证。

## 浮标要按连续操作验收

浮标的目的，是留在原页面完成提问、批注和功能切换。验证必须包括悬停展开、点功能、再换功能、点外部同一页面、拖到边角、迟到捕获和后台结果刷新；不能只测按钮能调用。

- 原生窗口始终visible也可能画出空白帧。改变窗口bounds后异步更新DOM位置，会短暂把中心图标挪出client。用固定屏幕坐标逐帧观察中心像素，不能只截最终稳态或查IsWindowVisible。
- 原生非激活窗不可靠blur识别外点，点击同一外窗也未必改变foreground。外点应收菜单且让那次点击正常到达原软件；不能铺全屏透明拦截层或抢焦点补事件。
- 原生setShape可让稳定canvas的透明空隙穿透；CSS pointer-events只约束自己的页面。展开/收起不重建窗口；本机DPI、实际region及底层应用命中要分别核对。
- 浮标截图排除不应靠隐藏再显示。系统capture exclusion实际成功与否，要查原生状态及真实截图；多实例重叠时先隔离自有测试对象，避免把另一个浮标误判为排除失败。
- 只有菜单展开时才监听外点；收起、断流、失败、退出都要释放，零模型调用、无常驻高频轮询。监听回调不读文件、不请求网络、不吞鼠标事件。
- 功能切换不能被自己的弹窗盖住；只有用户主动展开才调整自身层级，后台状态不抢焦点、不取消拖动。四边四角、负坐标和缩放比例都要实测，同组入口保持等角、同尺寸、完整标签。

2026-10-16的原生反例：D:/EEAS-qualification-20261015/orb-capture-stability-v3-before/NATIVE_MOTION_FRAMES.json。私有测试窗中固定世界坐标64帧，hover/action有4帧中心图标像素消失，HWND仍visible。测试临时关闭自有浮标的capture protection仅用于观察像素；不表示产品取消排除。修后结果须另绑源码复测，不追签此旧证据。

屏顶实测还发现 Chromium 的原生 `title` 提示会生成另一个同进程窗口并盖住子按钮；DOM和region都正确，真实WindowFromPoint仍命中提示窗。带文字入口删重复title，保aria-label/aria-description；必要提示须避让操作区，不能只看renderer截图。反例为D:/EEAS-qualification-20261015/orb-native-interaction-v6-diagnostic的两张failed-hit/own-renderer图；修后v7真实外点API、四功能、8位置命中、64帧中心连续与hook释放通过。这是自有原生窗口整链，完整安装版仍须另验。

- 浮标的连续切换须核整条异步路径：Tasks GET、窗口 ready、草稿保存和收起动画都可能晚到。最新意图检查必须贯穿到最终展示前；旧 hide 返回 STALE/BUSY/CLOSING 后不能继续 show。D/orb-latest-intent-v1 中真实 Actions+Quick 复现旧 Tasks 抢新 Ask/批注，修复后244项相邻测试通过，17个新定向反例不增加轮询或窗口。
- 界面模式回执先于 DOM 完成渲染时，测试要等实际可见控件；不要把过早选择器断言当产品故障。确认批注会隐藏 Overlay，此后抓该隐藏窗口的截图可能超时；用新的 Quick 读取核确认结果，不重放确认。D/source36-full-app-native-qa 保留两个此类失败及后续独立核验。


### 2026-10-16 浮标与真实弹层的边界

- 单独测圆弧不能证明打开真实批注层后仍可点：后显示的同级置顶窗口会盖住旧浮标。按实际显示的弹层身份变化校正一次层级，不反复置顶、不抢焦点；以原生命中和连续切换验证。
- 显示器几何版本须跨过截图的异步等待；旧截图迟回不能恢复圈选资格。保草稿，显式重读后恢复。
- 固定面板高度须算边框、padding、行高、次操作和gap；长文件卡在滚动容器中不应被flex压扁。极短原窗口批注工具与文字确认必须可滚达，不能只验窗口外框。

## 复杂关系图不能用fit掩盖语义与阅读问题

- 先核对象/关系角色：依赖用于工序分层，问题影响与归属不能一起当执行边。无真实分组时不编阶段改善外观。
- 对照相同真实数据的全局、可读流程与局部焦点；实际canvas像素要能读，百分比必须来自core。显式概览与默认可读视口分开验，定位不能只是center。
- 详情必须在图旁可读，包含前置/后续与原编辑/问答入口；开大图不丢详情，窄图区按可用画布宽度重排。小地图与控件要验实际矩形不重叠。
- 画布批量更新先加入全部节点再加边，覆盖前向引用。受控selection一次batch更新，外部ID监听只追外部ID，避免父级回声把本地刚展开的详情收回。
- 语言/状态/正文变化不算拓扑变化。新增节点保旧坐标和视口，旧异步布局必须取消；旧用户布局保留。规划状态的依据包含整条依赖和问题，不借局部节点相同冒旧结论。
- 复用固定官方Worker/协议与许可，独立线程不等于无成本；结束/取消/超时释放。不因pan逐帧序列化全部节点，不把数据准入上限当性能资格。
# 聊天、工作台与能力管理

- 先比较同任务的真实可操作结构，再决定聊天位置；聊天高频、工作台低频时，默认把空间还给聊天，复杂产物显式展开。窄窗单区切换，保同一份会话 DOM、草稿、选择和阅读锚点；隐藏不能触发新会话、重读文稿或再次生成。
- 模块目录保名称、短用途、必要状态和主操作。工具、MCP 服务、Skill 是不同对象；长配置和工具详情进入同一弹窗详情页，不把一张网格卡撑成长表单。先复用现有同栈 Dialog/Switch，避免为外观引入第二套框架。
- 配置开关、临时探测、会话加载和一次调用授权分别取实际依据。工具选择只传确切版本到明确的生效动作，不用模型请求的 tools:true 冒充选用；未知保存仅核原操作，不能点第二次造成重复。
- 全局展示偏好与项目内容状态各有一个权威来源。旧的窄侧栏宽度不能在新布局恢复时覆盖聊天主区；修改保存字段与恢复字段须同批检查。

- 自动恢复历史内容不能借用显式打开的导航副作用：旧 active_key 不得 abort 当前聊天加载、切会话或强开工作台；自动恢复与用户点击分开，以真实整 App 的旧状态恢复核验。
- 阅读锚点用不含瞬态 transform 的布局坐标；display:none 后旧消息入场动画会重启，不能把 translateY(7px) 累积进 scrollTop。覆盖两次往返、宽度重排、嵌套 offsetParent/边框/滚动、贴底与连续切换；测稳态须等待元素实际 Animation.finished，不靠初始 DOM 后的固定 sleep。

## 表单类型与模式切换（2026-10-17）

- 文本输入框的 width/min-height/padding 不能套到 checkbox/radio。用完整生产 CSS 顺序渲染实际组件；核原生稳态尺寸、文字同行与点击标签/Space/多选，不能靠元素存在或通用输入框测试。Source40实际反例见 D:/EEAS-qualification-20261015/source40-installed-ui/actions/root40-select-tool-handoff-01.png。
- 简短模式标签和选择器作为一组换行，文字本身不被 select 挤成两行；容器够宽时仍断词是约束错误。已在模型选择器显示的信息不在输入框再重复技术连接ID。
- 高频模式切换放在输入框旁，不要求重进完整设置；只改现有模式状态，不另造会话或擅改权限。目录是写入选择的前置条件，取消目录即清写入勾选，重新选择后需重新明确；反应式 effect 先读取依赖，再作短路判断。
- Source41的组件中英/宽窄证据与原生部署资格分开。修改前失败、R1和最终R2均保留，见 D:/EEAS-qualification-20261015/source41-session-controls-v1/HANDOFF_R2.md；不得把组件图当Windows全链成功。

## 原会话阅读列与停止恢复（2026-10-17）

- 阅读列核实际消息根节点，不能只验普通消息的 div/article。原会话使用 section.as-original-transcript，漏选会造成正文铺满、输入框居中；同批比较普通对话、原会话与输入框的稳态位置，兼顾窄窗。Source42原生反例与Source43实际CSS四宽度对照见 D:/EEAS-qualification-20261015/source43-native-reading-column-v1/RECEIPT.json；后者不代签完整App或Windows。
- 结果未知时，按明确的原执行可停止投影提供入口，不能一概藏按钮，也不能把所有未知请求都开放取消。检查后端、Host持久日志和主窗/Quick的整条链；先保存停止意图，丢回执后只核原停止，原提问仍未知。小窗恢复后保留必要动作，去掉重复的不可操作按钮，并实测按钮净空。Source43组件实测见 D:/EEAS-qualification-20261015/source43-native-page-review-ui-v1/frozen-v2/FINAL_RECEIPT.json；Windows资格另核。

## 原生浮窗完整回复（2026-10-17）

- 用真实已完成长答复检查最终浮窗。Source47的1860字回复虽有订阅/Skill/MCP成功回执，却在P.textContent中暴露原始Markdown，不能以执行通过代替显示可用。按已有marked/DOMPurify策略呈现语义段落、标题、表格和代码；不要另写解析器或为了短窗默认截去结论。实际反例：D:/EEAS-qualification-20261015/source47-installed-ui/actions/root47-returned-review-visible-ui-01.png。
- 完整回复同时检查运行视图与持久投影；只放开展示层而日志仍截断，会在重开时丢尾。保传输/日志总量上限，超限应明确失败并保护内容，不用静默截断冒充完整。正文/表格局部滚动，最后内容和footer都要真正到达；同正文状态刷新保scroll、focus和selection。
- 看真实logical CSS尺寸与DPR，不能凭放大的位图一味缩字；浮窗外轮廓依当前矩形合同，内部组件可有圆角。安全外链需净化、协议白名单、明确点击和当前正文/操作关联；不因渲染Markdown放开远程媒体、任意本地文件或主窗口导航。
- 异步选择尚在确认时，控件短暂可见/启用不能当作新选择已提交。先核稳定选择再授权，检查忙碌发布与回执顺序、保草稿；合成快速动作得到false不足以证明人类点击丢失。带提示文字和按钮的同组用明确gap/wrap，不依赖文本偶然留空格。

## 完整回答阅读链（2026-10-17，Source49修正中）

- 改回答排版前列出同一回答实际经过的呈现面：主对话、浮窗、任务详情、原会话历史与VS Code。按assistant/user/status/error区分；复用成熟安全渲染，不把模型正文与错误或用户原文一并解释为HTML。完整内容可读，table/pre局部横滚，不以无artifact链接集合为由回退整段raw Markdown。
- 用同一已保存operation做“返回主对话→重启找回→浮窗查看→回到草稿”的完整场景；新页面的demo、静态源码和已有进程中的回复，不能代签新进程的历史可达性。真实Source48只读getQuickDrafts确认RETURNED/text1860仍在，而Main未出现；原因必须分别检查scope和异步restore，不能直接声称数据丢失。
- 历史查看与编辑旧意图是两种动作。signed SEND只读不clone、不自动inspect/watch/send、不激活旧grant；DRAFT继续编辑保原语义。查看前保最后输入、IME和图上未提交文字，退出返回原草稿，迟回执不能抢新scope/新输入。历史inspect的NO_RECORD/UNAVAILABLE也不能无条件覆盖更可靠的同操作已保存结果。
- Source49尚未最终冻结或Windows验收；以上为审核规则与已发现缺口，不是完成声明。


### Source49 Windows完整阅读链复核（2026-10-17）

实际同一Windows App重启后恢复原47真实回复：主对话与浮窗各两张语义表、13个任务行，末尾完整，原text SHA不变；14px正文/23.8px行高，表格局部横滚。保存的SEND回看只读，不继承旧执行权限、不克隆发送、不重新调用模型。实际446字本地草稿经过回看/返回，原textarea、值、4:19反向选区、滚动和焦点保留；恢复原空稿后正常收起。管理3tab有界，工作台往返保原Main输入对象，收起聊天宽1488/1715。原生Main PrintWindow与Windows renderer截图均由Root亲看，完整NormalExit的8owner另验；不能把组件截图、真实模型资格、UI阅读、公司网关或可选VSIX15本机运行混称一项。证据：D:/EEAS-qualification-20261015/source49-installed-ui/ROOT_NATIVE_UI_RESULT.json。

真实回复夹具还应带完整原问题、关联文稿、Skill选择、历史依据与控件显隐；仅把回答文本放入空壳，不能推断真实阅读区的可用高度。格式不合规的原始Markdown不应为了截图美观擅改内容。打包验证也要走已填写的真实metadata路径；占位替换按完整赋值行，不靠子串；相同引用的读取限额应一致且取严格值，保留失败与旧版本，不放松SHA/路径/身份校验来让脚本通过。

## Source49 最终移动首启（2026-10-17）

归档/解压通过不能替代正常启动界面。实际把完整包解压到新D盘中文空格路径，用字面START无诊断参数启动，Root亲看完整最大化Cirven内容与未配置入口，再用同实例正常原生菜单退出；Main/API0、8owners CLOSED/pending0。最终索引：D:/EEAS-qualification-20261015/source49-offline-delivery/evidence/FINAL_DELIVERY.json。该资格仅覆盖此启动/界面/关闭，不代签公司网关、所有页面或可选VSIX15。


## 完整上下文与弹层回程（2026-10-17）

- 抽取的生产 JSX 仍不等于完整页面。缺少实际祖先类、布局方向或 transcript 容器时，局部夹具会把普通按钮拉成大块；先对照生产 DOM/CSS 再决定是否改代码，不向生产追加补丁去修夹具。Source50 的 pending 按钮反例只证明局部夹具缺上下文，完整 Main 无对应故障。
- 焦点回执为 null 不是通过。对实际 Dialog 用键盘打开/Escape 与指针打开/关闭，核回到同一触发器；同时核原输入节点、文本 SHA、选区方向和滚动。隐藏输入应以原句柄读状态，不能把 role 查询默认排除隐藏项当成节点丢失。
- 聊天位置按任务选择；既有默认聊天、按需并排和工作台集中视图已满足目的时，复用它们，避免为布局选项增加菜单。完整 Main 浏览器补证见 D:/EEAS-qualification-20261015/source50-plan-continuity/frontend/full-main-browser-v1/FINAL_RECEIPT.json，Root图审见 ROOT_FULL_MAIN_REVIEW.json；不代签 Windows 焦点或原生软件交互。


## Source53：布局与连续交互实测

依据：D:/EEAS-qualification-20261015/source53-main-conversation/ROOT_WINDOWS_QUALIFICATION.json（f8b018ad…bd4d），真实Windows、2次本地外部模型夹具；不是Pro/公司语义验收。

- 布局不能决定消息语义：全窗/浮窗同样的普通问题走同一既有普通消息入口，不因工作台模式强迫选文件。入口可见不代表全窗首问真的成功，需从fresh无file/project到真实返回。
- 空状态要看当前作用域的真实历史与运行对象；收到消息/Quick SEND后撤去大块欢迎页，草稿和其他讨论不误触发。
- rail在浮窗被隐藏时，同批核低频能力入口是否一起消失；复用同一modal即可。会消失的menu item不宜作为Dialog最终回焦对象，先衔接稳定trigger，Popover关闭不抢Dialog焦点。
- 恢复最终小窗正确仍不证明过程无闪。53仍捕捉到1次max大矩形中间metadata样本；需要窗口可见性/失败恢复的完整链，避免用永久透明来掩盖。
- 固定在页面角落的旧面板恢复chip在窄浮窗会压到composer；同时核有效裁剪、恢复路径与阅读空间，不能仅看宽屏。53此项仍待修。
- 从具体回答显式展开，应验证这条回答已进入可读视口；DOM含文本不代表用户直接读到。后台完成与阅读旧记录不可套用同一强制滚到底规则。53此项仍待修。
- 自动化内部wait不能超过传输层请求期限；长任务使用后续只读快照检查原operation。53最初readonly观察UNKNOWN后仅核原RETURNED并继续，未重新发送。验证工具错误不冒充产品失败，也不抹掉原失败。

## Source62：短窗阅读与图视口

- 必要状态在 DOM 中仍可能被旧响应式 CSS 隐藏。用最终样式顺序检查权限理由、失效引用的 computed 样式和祖先有效交集；精确覆盖必要状态，不全局放开所有辅助文字。r3 的真实隐藏反例与 r4 修复见 D:/EEAS-qualification-20261015/source62-chat-reading/frontend/frozen-v2/FAILURES.md。
- 普通态、长草稿与风险叠加分别量阅读和输入空间，不把单一高度当全态保证。r4 普通短稿正文102.5→170.5px、字号不变；版本变化叠加明确错误时仅77.609375px，仍保留风险、输入与发送。长理由有界摘要，原文末字和原按钮须能到达。94px是旧r2值，最终以原始r4和 skill-lessons-preparation/HANDOFF_ERRATUM.json 为准。
- 量稳态先等当前有限动画 finished 与布局帧；保留失败和原容差，不用固定睡眠或放宽门槛掩盖变化。r1锚点反例未独立定位某个动画，不能写成已证明的动画缺陷。
- 工作台往返核原消息锚点、像素偏移、输入节点、草稿与选区；仅 scrollTop 相同不够。r4原锚点2/偏移−82.84375/scroll460与选区4–11均保持。没有现存非零历史时，原生检查应明确未验长阅读锚点，不能注入假历史代签。
- 图缩放窗保模型中心、用户缩放和节点位置；隐藏时的零尺寸不覆盖最后非零视口。旧记录没有尺寸依据时不猜、不自动fit，复用可见的恢复入口。恢复按钮在画布底部仍可能处于整页折叠线下；检查真实可见坐标和祖先裁剪，不靠自动滚动把它变成可达。图的概览与可读定位分别验收。

本节源于隔离真实组件 Chromium 与真实ASGI/SQLite检查，不代签完整Windows或用户审美。原始记录分别见 D:/EEAS-qualification-20261015/source62-chat-reading/frontend/runs/r4/BROWSER_RECEIPT.json 和 graph-camera/frozen-v2/VALIDATION_SCOPE.json；Windows结果独立记录。

Source62后续Windows局部复核见 D:/EEAS-qualification-20261015/source62-chat-reading/ROOT_SOURCE62_NATIVE_QUALIFICATION.json：实际短窗正文156.269px（含失效引用提示），原21字草稿与非零历史锚点往返保留，图恢复与持久viewport另验，正常退出及保护进程生存另有回执。DPR可能让scrollTop为小数；建立阅读位置时记录实际值，锚点往返仍按原容差核，不要求滚动整数。界面库getter可能返回可变引用，前值应复制；误存live pan/positions的旧证据不能追签为不可变快照，可用独立父版持久记录与新回执补核，不重复已发生的点击。上述为候选同源App局部资格，不能替代原安装目录升级后的启动与数据检查。


## Source64：异步反馈与验证前提

- 先在发起面验证失败是否可见：工作台收起后，聊天目录请求的错误若只进入工作台 banner，用户仍不知道结果。必要前置条件就地给出简短提示及既有纠正入口；不暴露 IPC 包装和内部调用名，不因此重发原操作。Source64 实际截图已发现该缺口，修复另属 Source65，不能追签旧图。
- 相同 class 可属于不同语义区域。工作台“对话”按钮会随工作台隐藏，聊天栏的工作台按钮才是持续可见的切换入口。核稳定容器、实际显隐与 ARIA；超时后先只读观察，不重复点击。该次实际 hide 成功，原失败来自宽泛 selector，不能写成产品切换故障。
- 弹窗正向场景要有真实前置条件。隔离数据无模型时，目录正确拒绝并不证明正常选择器可用；不借账号或造配置去抹掉限制。空目录、已配置目录、弹窗往返与任务执行分别说明范围；scrollTop 0→0不代签长阅读锚点。

原始限定 Windows 结论：D:/EEAS-qualification-20261015/source64-current-page-capability-tasks/ROOT_SOURCE64_NATIVE_QUALIFICATION.json。Source65 浏览器与后续 Windows 结果各自记录，开发预览不冒原生验收。


## 原位核查与焦点交接（Source66）

原操作核查只需简短状态、核查入口及按需详情；编号与后台诊断不占主阅读区，用户原文不参与系统文案清理。核查不生成新操作或重发请求。

需保持键盘焦点的处理中按钮，直接设置 disabled 会导致 Chromium 撤焦；可用 aria-disabled/aria-busy 与同步防重入保护，所有点击和键盘触发仍须遵守保护。成功回执可能使旧按钮消失，此时仅当用户仍聚焦该按钮、来源未变且卡片仍属于原操作，才把焦点交给同卡的实际产物入口。用户已继续输入、切换项目或后台恢复时，不主动移焦。按稳定原操作 ID 保留卡片节点；当前列表索引应可释放，避免跨导航缓存整个历史。

Source66 分阶段真实生产片段覆盖不可用回执、两个独立原操作、长诊断滚动、成功产物入口及继续输入；Root查看成功截图。源码与范围见 D:/EEAS-qualification-20261015/source66-concise-delivery-feedback/frontend/frozen-v1/FINAL.json。它是浏览器片段证据，不代签当前67全App、真实文件打开或Windows。


## 失败投影也要保原消息身份

Source68 独立审查发现：原消息key为 project/conversation/operation，而读取失败助手只返回operation，替换后变为null/null/operation。真实scoped反例出现整行重建、焦点与未提交回答丢失；原空作用域夹具没有触发。修复只从请求发起时的原行复制两个身份字段，错误期间不携旧review/权限。后续原GET恢复相同pending时，原custom与选项仍保留，跨作用域迟回继续拒绝。

遇到错误、加载、恢复、权限变化等投影时，同批核用于key的完整身份和组件本地状态；夹具使用实际工程/会话归属及有内容的子控件。恢复后值仍在、错误期间旧权限未生效，两者都需验证。证据：D:/EEAS-qualification-20261015/source68-observed-response-and-status/frontend/frozen-v2/FINAL.json 与 native-status-peer-review/INDEPENDENT_REVIEW.r2.md；为生产片段的真实浏览器/Node/类型检查与静态独审，非Windows或真实后端资格。

## Windows 滚动条与实际可用宽度（Source72）

Linux 的覆盖式滚动条不占内宽，Windows 经典滚动条会占容器 client box。先核完整 App 的实际 cascade、viewport/DPR、offset/client/border/padding 和所有裁剪祖先，再比较文本左轴、内边距与所属容器的内容右沿。Source72 实际 metadata 占用 10 CSS px 滚动条；引文与主输入左轴、12px 16px 内边距一致，外框右沿差约 10.119px 可由该占位解释。保留原测量和失败，只修有明确依据的 QA 判据，仍用原 2px 取整容差，不为测试误判改产品、重复输入或重做构建。

宽窄检查还须实际滚到长引文末端，核清除入口、主稿和发送按钮的有效可见交集；已合法滚出 metadata 的文件摘要不要求同时可见。Root 本轮实际窗口及 360px 栏宽核查另见 D:/EEAS-qualification-20261015/source72-native-feedback-composer-layout/ROOT_SOURCE72_NATIVE_LAYOUT_QUALIFICATION.r1.json；受控 DOM 拖柄不冒物理鼠标资格，旧 Linux 组件证据也不追签为原生。此经验在 Source72 构建冻结后补入，仅指导后续开发。

## 已选文件与当前保存版本

Source73 Windows 实测：局部返工已应用到 v3，但树组件只在 selectionVersion 改变时发出 onSelectionChange；再次点已选文件没有重新读取。文件上下文的刷新也未更新内置编辑器，因此显示仍停在已保存的 v2。切到另一份 QA 文件再返回能显示 v3，证明应分别核对保存结果、引用版本与实际阅读内容，不能把这个交互缺口误判成写入失败。

后续在文件视图提供明确重读/打开语义，重读新版本前处理 dirty 草稿，并用 revision/SHA 区分面板；不要让重选别的文件成为用户必经步骤。本例已登记待改，尚未修复。实际记录在 D:/EEAS-qualification-20261015/source73-native-rework-default-contracts/INTERNAL_EDITOR_RELOAD_FOLLOWUP.md 和同轮原生 QA 的 v3 显示回执。


## 2026-10-19 当前页与总管接线复核

- 收纳到输入工具区时，窄容器不能用 `trigger > span {display:none}` 一并隐藏风险提醒。标题与状态分开选择，核真实图标净空和所有祖先裁剪。
- 批注入口的打开焦点交给Popover自动聚焦生命周期；另起RAF会与库竞争。已打开后的显式再批注可就地聚焦，不重开/清稿。
- 异步反馈有本地单飞保护，不能只依赖父层pending。保存拥有原编辑版本；回执到达时的新字、新圈选和重新定位都不能被清掉。
- 无有效页面时禁重定位；先在无目标时写了批注、随后目标恢复，也必须有显式重定位路径。版本提示只拼实际有效ref，不能显示空v或自动借latest。
- 标题栏要量子按钮/图案而不只量外框，保系统caption区。极窄时收为单目录图标，打开目录动作仍在同菜单可达；正常宽度保一键入口。
- 总管本地计数不伪装模型回复；持久问题/草稿与内部wire分开，UNKNOWN不盲重发也不永久锁死其他新问题。历史展示分页不能让原任务正文失联。

证据D:/EEAS-qualification-20261015/source81-native-ide-file-rework/{current-page-entry-browser,root-toolbar-browser,root-supervisor-integration}；具体源码、初失败、后修复与Windows/浏览器范围分别见各回执，不用此经验文字代签产品可用。


## 2026-10-19 总管多入口与旧问定位

发送按钮与快捷键应读取同一client的即时准入结果，不能只凭近期历史判定：原请求可能已在途而快照尚无记录；明确终态到达后也不能被旧缓存永久禁发。本地状态查询先分流，阻止重复操作应发生在生成新操作id之前。

旧问选中是显示意图，不是新执行或新权限。用户返回最近讨论后，普通store广播不能重新展开旧问；再次主动点同一旧问仍须能展开。用单独的有界显示revision区分主动选择与数据更新，草稿和原操作身份继续由原store/client管理。


总管320px英文实测补充：短中文同排通过，不代表英文长操作名也可达。核查UNKNOWN时同时出现的次要动作会挤出发送；先验证实际有字宽度和有效裁剪，再按剩余宽度换行，保持原字号和主次。Root真实HTML/CSS三宽度反例/修后与新源7条交互见D81/root-supervisor-integration，不能只看默认宽截图。
