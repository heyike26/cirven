# Cirven

<!-- impeccable:product-schema 1 -->

## Platform

Windows desktop App，Windows x64/Python 3.10。Web是同一renderer的开发和测试入口，不代替原生验收。

## Users

在本机软件中阅读、编辑、研究与开发的人。用户可能同时处理多个工程及直接会话，需要少切换地提问、批注、查看产物和纠正执行。

## Product Purpose

把当前页面和文件反馈准确接回对应讨论与工程；在需求变化及人工编辑交错时保护有效内容，选择必要的局部返工。具体首轮范围以docs/NATIVE_COLLABORATION_HARNESS.md为准。

## Operating Context

优先Windows，WSL可选。内网包须包含依赖、字体、许可与诊断；内网使用用户配置的兼容模型网关，外网使用本人授权的订阅连接。两者分别实测，不暗切API。保持原OpenCode单loop，新增能力通过可更新的插件、工具和Skill接入。

## Capabilities and Constraints

轻量对话与悬浮入口服务原生软件，复杂图和审阅按需展开工作台。工作台左目录、中内容、右Agent；不缩放或重排Windows桌面，不改变DPI/分辨率。生成文件从回答直接交给默认软件打开，文件关联以精确工程及讨论为范围。另一路本地资料→必要澄清/确认→任务书/报告→交互图→harness。能力、已保存配置、模型回复与任务验收是不同状态。

## Brand Commitments

可见品牌为Cirven，主窗矩形、内部控件圆角，石墨与银灰三片C/V标志。保持中性银白、连续版面、节制卡片。用户要求高级动效，开屏有编排，常用交互短促可打断，加载用标志从下向上填充。日常界面只写简洁功能名，不暴露框架名或开发提示词；真实模型/文件标识及许可原文不篡改。

## Product Principles

每项功能依docs/FEATURE_VALUE_GATE.md从用户目的、痛点原因、前后步骤及相邻场景出发，以完整真实链验证。保护手改、草稿和原操作，未知结果不自动重放；优先本地确定性检查，避免常驻模型轮询和不必要token消耗。证据区分源码、浏览器、真实Windows、本人订阅及企业模型资格，未测不称通过。
