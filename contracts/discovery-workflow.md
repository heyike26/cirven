# 需求发现工作台API v1

前缀/api/projects/{pid}，Discovery在/discovery。本地认证/CSRF；变更operation_id幂等、expected_revision CAS；历史不可变。既有Id/Anchor/Material/WorkspaceRef类型沿用。

POST /sessions：{operation_id,title,entry_mode:PROBLEM|REQUIREMENTS|TASK_FILE,statement,materials:[]最多19,workspace_ref?:WorkspaceRef}。无资料可起步，原始statement存真实USER_STATEMENT文档。返回SessionView{id,project_id,revision,current_revision,sha256,payload,answers,source_current,readiness}；payload含entry_mode/workspace_ref/materials/source_receipt/candidate/history/status:DRAFT|CONFIRMED/accepted_claim_ids/resolutions/proposal_ref?。

GET /sessions、/sessions/{id}?revision；POST /{id}/advance：{operation_id,expected_revision,message,provider_profile_ref,workspace_ref?,budget,materials?:[],target_phase?:DISCOVER|DEFINE|RESEARCH|SPECIFY|PLAN}，原OpenCode结构化建议，无第二Loop；POST调用可同时轮询GET /operations/{operation_id}?after获取真实native_execution/events；POST /operations/{op}/cancel。断网/owner丢失明确状态，不自动重发。

Candidate：{title,phase,summary,claims,questions,competitors,graphs,next_actions}；精确类型以discovery_schema.py为准。claim含需求分类、事实/用户/建议origin、来源anchors、acceptance；question含缺料/冲突/不合理/决策分类、阻塞与影响；competitor分直接/间接/人工/不采用，observation区分OFFICIAL_CLAIM/USER_TESTED/INFERENCE/UNKNOWN并绑定来源。

graph{id,kind:ARCHITECTURE|FLOW|MINDMAP,title,nodes:[{id,label,description,claim_ids:[],source_anchors:[],origin:SOURCE_FACT|USER_STATEMENT|MODEL_SUGGESTION}],edges:[{id,source,target,relation,claim_ids:[],source_anchors:[],origin}]}；架构/流程不强制DAG。next_action{id,method,action,reason,expected_artifact}。模型输出不等于确认；来源锚点必须真实选定文档精确位置/SHA；结构校验由后端负责。

PUT /{id}：{operation_id,expected_revision,candidate,change_reason}；POST /{id}/answers：{operation_id,expected_revision,question_id,text}；POST /{id}/confirm：{operation_id,expected_revision,session_sha256,user_confirmed:true,accepted_claim_ids:[],accepted_graph_ids:[],resolutions:QuestionResolution[]}。确认需至少1个有验收的REQUIREMENT及阻塞问题显式裁决；后续改动新DRAFT。POST /{id}/plan：{operation_id,expected_revision,provider_profile_ref,budget}复用原PlanningProposals产生任务网络，返回proposal_ref与proposal，不启动执行。旧proposal Word/确认/harnessAPI继续消费同一网络。GET /{id}/pending、/pending/markdown可在任何阶段下载。

模板/template-profiles：sources[{role:TEMPLATE|WRITING_EXAMPLE,file_source_ref:FileSource}]，规则review后复用profile_ref三元组；报告experience_ref可选，经验仅形式。图/discovery/sessions/{id}/diagrams/{graph_id}依精确revision/SHA生成SVG/PNG/模型，artifact_ref固定SHA；Root组合，不另建Loop。

原软件窗口、图片/OCR引用与Word选文接口见contracts/workbench-observations.md；观察不是已核实事实。

批注/workbench/feedback：POST{operation_id,artifact_ref:{kind:DISCOVERY,id,revision,sha256},tool:COMMENT|RECTANGLE|FREEHAND|TEXT_SELECTION|TEXT_EDIT,comment,selection,edited_candidate?}；GET?session_id恢复。selection包含graph_id/node_ids/normalized_points/viewport/page_number/text_selection；原文定位细则见workbench_feedback类型。record不调模型；返回feedback_ref三元组、revision_target_ref、session。聊天草稿可积累附件，/advance的feedback_refs最多20，与单feedback_ref互斥，一次Loop处理；直接发送也用独立发送operation，拒过期引用。TEXT_EDIT实际保存新DRAFT；选文批注不自动改字。全屏恢复布局及草稿，不重建产物。
