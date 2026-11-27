---
name: eeas-report-handoff
description: 输出Word报告与harness提示词
---

# 输出Word报告与harness提示词

用户本地模板优先，否则使用固定许可的优质默认排版。按精确计划修订生成Word并附独立pending-confirmation文件，标待确认事项和用户决议。未答不阻止阶段报告；提示词正式写项目文件夹须明确确认且来源CURRENT。输出SHA和版本路径，不替换旧产物。

内容结构参考随附 GitHub Spec Kit v1.1.3（MIT），EEAS 共创与来源规则优先。模板只读取，不启动上游 hooks/CLI。
