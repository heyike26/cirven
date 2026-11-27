'use strict';
const { randomUUID } = require('node:crypto');
const {QuickNotSubmittedError}=require('./quick-not-submitted.cjs');
const {submissionObservation,firstSubmissionObservation}=require('./quick-submission-observation.cjs');
const {canonicalQuick,canonicalQuickMetadataStatus,canonicalQuickRoot,canonicalQuickInput,canonicalQuickProjection,canonicalQuickInputFingerprint}=require('./canonical-quick.cjs');
const { quickScope, quickRef, quickDigest, quickMarks, quickNativeReview, quickResetNativeReview, quickAssociationScope, quickAssociationKey, quickFileLinkRef, quickControlBody } = require('./quick-journal.cjs');
const PENDING = new Set(['PROCESSING','REQUESTING','RUNNING','QUEUED','PENDING','CANCEL_REQUESTED']);
const TERMINAL = new Set(['RETURNED','COMPLETED','SUCCESS','SUCCEEDED','FAILED','CANCELLED','CANCELED','REJECTED','NOT_CONFIGURED','INTERRUPTED','TIMED_OUT']);
const copy=value=>JSON.parse(JSON.stringify(value));

function geometryForSnapshot(snapshot) {
  const frame=snapshot?.observation?.observations?.visual,preview=snapshot?.preview;
  if(snapshot?.current!==true || preview?.status!=='AVAILABLE' || !frame ||
    !/^[a-f0-9]{64}$/.test(frame.image_ref?.sha256??'')||
    frame.image_ref?.sha256!==preview.image_ref?.sha256 || frame.width!==preview.image_ref?.width ||
    frame.height!==preview.image_ref?.height) return undefined;
  const proof=frame.pixel_source_proof;
  // SDK metadata alone is not a pixel-origin proof. The visible-frame adapter
  // has a distinct, read-only proof and never grants Cua coordinate input.
  const verified=frame.pixel_source==='VERIFIED_TARGET_PIXELS' && proof?.status==='VERIFIED_TARGET_PIXELS' &&
    proof.self_exclusion_verified===true && proof.image_sha256===frame.image_ref.sha256;
  const visible=frame.pixel_source==='VISIBLE_TARGET_RECTANGLE' && proof?.status==='VISIBLE_TARGET_RECTANGLE' &&
    proof.capture_method==='PILLOW_ACTUAL_DESKTOP_CROP' && proof.coordinate_space==='PHYSICAL_PIXELS_OWN_THREAD_PMV2' &&
    proof.image_sha256===frame.image_ref.sha256;
  const exact=frame.pixel_source==='EXACT_TARGET_WINDOW'&&proof?.status==='EXACT_TARGET_WINDOW'&&
    proof.capture_method==='WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND'&&proof.coordinate_space==='PHYSICAL_PIXELS_OWN_THREAD_PMV2'&&
    proof.self_exclusion_verified===true&&proof.image_sha256===frame.image_ref.sha256&&
    /^[a-f0-9]{64}$/.test(proof.target_identity_sha256??'')&&
    proof.target_identity_sha256===snapshot.observation.window_lifetime_sha256&&
    frame.target_ref&&snapshot.observation.window_ref&&quickDigest(frame.target_ref)===quickDigest(snapshot.observation.window_ref);
  if(!verified&&!visible&&!exact||exact&&!snapshot.observation.window?.bounds)return undefined;
  const bounds=exact?proof.capture_bounds:visible?(proof.capture_bounds??proof.target_bounds):proof.target_bounds;
  const windowBounds=snapshot.observation.window?.bounds;
  if(windowBounds&&quickDigest(windowBounds)!==quickDigest(proof.target_bounds))return undefined;
  if(!bounds||!['x','y','width','height'].every(key=>Number.isSafeInteger(bounds[key])) ||
    bounds.width!==frame.width||bounds.height!==frame.height||bounds.width<1||bounds.height<1)return undefined;
  if(exact){
    const target=proof.target_bounds;
    if(!target||!['x','y','width','height'].every(key=>Number.isSafeInteger(target[key]))||target.width<1||target.height<1||
      ![bounds.x+bounds.width,bounds.y+bounds.height,target.x+target.width,target.y+target.height].every(Number.isSafeInteger)||
      bounds.x<target.x||bounds.y<target.y||bounds.x+bounds.width>target.x+target.width||bounds.y+bounds.height>target.y+target.height)return undefined;
  }
  return {physicalBounds:{...bounds},pixelWidth:frame.width,pixelHeight:frame.height,verified:true,
    pixelSource:frame.pixel_source};
}

function questionText(record,extra='') {
  if(typeof extra!=='string')throw new Error('补充内容需要是文字。');
  const marks=quickMarks(record.marks ?? []);
  const textIntent=marks.some(mark=>mark.kind==='text'&&mark.text.trim());
  if(!extra.trim()&&!record.comment.trim()&&!textIntent)throw new Error('请补充要解释或修改什么，标记已保留；本次没有提交。');
  const work=!!record.scope?.originalWorkChoice,parts=work?[extra,record.comment]:[extra.trim(),record.comment.trim()];
  if(canonicalQuick(record)){
    parts.push(...marks.filter(mark=>mark.kind==='text').map(mark=>mark.text));
    const text=parts.filter(Boolean).join('\n\n');
    if(Array.from(text).length>16000)throw new Error('请缩小本条问题的范围，原稿保留。');
    if((marks.length||record.regions.length)&&!record.contextRef&&!canonicalQuickMetadataStatus(record,text))throw new Error('图上批注缺少原页面依据，请重新引用；本次没有提交。');
    return text;
  }
  if((marks.length||record.regions.length)&&!record.contextRef)throw new Error('图上批注缺少原页面依据，请重新引用；本次没有提交。');
  if(record.scope?.fileLinkRef&&!record.nativeReview)parts.push('本条引用明确关联的项目文稿：'+(record.scope.fileLabel||'项目文稿')+'。这不表示已识别当前窗口文档，也不授予写入权限。');
  const title=record.snapshot?.observation?.window?.title;
  if(record.contextRef) {
    parts.push(`本条意见引用已冻结页面：${title||'原页面'}\n依据：${record.contextRef.id} / v${record.contextRef.revision} / ${record.contextRef.sha256}`);
    if(record.regions.length)parts.push('用户圈选位置（相对这次冻结画面的归一化坐标）：'+JSON.stringify(record.regions));
    if(marks.length)parts.push('用户图上批注（位置相对同一冻结画面；文字是用户反馈，图形不是自动识别的控件）：'+JSON.stringify(marks));
  } else parts.push('本条没有引用可靠的当前页面；不能假定看到了用户屏幕。');
  const text=parts.filter(Boolean).join('\n\n');
  if(Array.from(text).length>16000)throw new Error('请保留意见并缩小本条范围，再发送；本次没有提交。');
  return text;
}

class QuickHost {
  constructor({journal,getCompanion,request,onChanged=()=>{},onResult=()=>{},isClosing=()=>false,loadSnapshot,
    beforeCapture=async()=>{},afterCapture=async()=>{}}) {
    this.journal=journal;this.getCompanion=getCompanion;this.request=request;this.onChanged=onChanged;
    this.onResult=onResult;this.isClosing=isClosing;this.loadSnapshot=loadSnapshot;
    this.beforeCapture=beforeCapture;this.afterCapture=afterCapture;this.scope={};this.generation=0;
    this.captures=new WeakSet();this.timers=new Map();this.inFlight=new Map();this.cancelInFlight=new Map();this.cancelObserved=new Map();this.observed=new Map();this.closed=false;
    this.inputProofs=new Map();
    this.setScope({});
  }
  setScope(raw) {
    const next=quickScope(raw);
    next.conversationId??='native_'+quickDigest([next.projectId??'unbound',next.workspaceId??'',next.sessionId??'']).slice(0,48);
    const file=this.journal.fileContext(next);
    if(next.fileLinkRef&&(!file||quickDigest(next.fileLinkRef)!==quickDigest(file.fileLinkRef)))throw new Error('文件引用尚未在这段讨论中明确选择。');
    delete next.fileLinkRef;delete next.fileLabel;
    if(file){next.fileLinkRef=file.fileLinkRef;if(file.filename)next.fileLabel=file.filename;}
    const {generation:oldGeneration,...previous}=this.scope;
    const {generation:requestedGeneration,...identity}=next;
    if(oldGeneration!==undefined&&quickDigest(previous)===quickDigest(identity))return copy(this.scope);
    next.generation=++this.generation;this.scope=next;return copy(next);
  }
  fileContextState(){
    const selected=this.journal.fileContext(this.scope),legacy=this.scope.projectId?this.journal.legacyFileContext(this.scope.projectId):undefined;
    return {scope:quickAssociationScope(this.scope),selected:selected??null,legacy_candidates:legacy?[legacy]:[]};
  }
  assertFileScope(value){
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，文件引用未更新。');
    const expected=quickAssociationScope(value);
    if(quickDigest(expected)!==quickDigest(quickAssociationScope(this.scope)))throw new Error('当前讨论已变化，原文件选择保留。');
    quickAssociationKey(expected);return expected;
  }
  clearFileContext({scope,expectedRef}){
    const expected=this.assertFileScope(scope);
    this.journal.clearFileContext({scope:expected,expectedRef});
    delete this.scope.fileLinkRef;delete this.scope.fileLabel;this.changed();return this.fileContextState();
  }
  setFileContext(input,{scope:target=this.scope}={}){
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，文件引用未更新。');
    const file=this.journal.setFileContext({...input,scope:quickAssociationScope(target)});
    if(quickDigest(quickAssociationScope(this.scope))===quickDigest(file.scope)){this.scope=quickScope({...this.scope,fileLinkRef:file.fileLinkRef,...file.filename?{fileLabel:file.filename}:{}});if(!file.filename)delete this.scope.fileLabel;}
    this.changed();return copy(file);
  }
  clearDraftFileContext(capture){
    this.authorized(capture);if(this.closed||this.isClosing())throw new Error('应用正在关闭。');
    const scope={...capture.scope};delete scope.fileLinkRef;delete scope.fileLabel;
    const cleared={...capture,scope:quickScope(scope)};this.captures.add(cleared);return cleared;
  }
  notify(callback,...args) {
    try {Promise.resolve(callback(...args)).catch(error=>{this.notificationError=error?.name??'NotificationError';});}
    catch(error) {this.notificationError=error?.name??'NotificationError';}
  }
  changed() { if(!this.closed&&!this.isClosing())this.notify(this.onChanged); }
  async capture({intent,kind}) {
    if(this.closed||this.isClosing())throw new Error('应用正在关闭。');
    const scope=copy(this.scope),companion=this.getCompanion();
    if(!companion)throw new Error('原生协作尚未就绪。');
    let snapshot;
    try {
      await this.beforeCapture();
      snapshot=await companion.capture({intent,need:{metadata:true,accessibility:true,visual:true,ocr:false}});
    } catch(error) {
      snapshot={operation_id:'',current:false,observation:{status:'UNAVAILABLE',
        reason:'暂未取得当前页面，可重新引用，或先提一般问题。'}};
    } finally { await this.afterCapture(); }
    if(this.closed||this.isClosing())throw new Error('应用正在关闭。');
    const capture={snapshot,scope,kind,hostGeneration:this.journal.hostGeneration,
      ...(geometryForSnapshot(snapshot)?{geometry:geometryForSnapshot(snapshot)}:{})};
    this.captures.add(capture);return capture;
  }
  authorized(capture) {
    if(!capture||!this.captures.has(capture))throw new Error('页面依据不属于这次原生交互，请重新引用。');
    return capture;
  }
  save(input) {
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，草稿尚未保存。');
    const capture=input.capture?this.authorized(input.capture):this.emptyCapture();
    const item=this.journal.save({...input,capture});this.rememberInputProof(item,capture.snapshot);this.changed();
    return {...item,status:item.delivery==='DRAFT'?'SAVED':'EDITING'};
  }
  emptyCapture() {
    const capture={scope:copy(this.scope),hostGeneration:this.journal.hostGeneration,
      snapshot:{operation_id:'',current:false,observation:{status:'UNAVAILABLE'}}};
    this.captures.add(capture);return capture;
  }
  inputProof(item,snapshot){
    const observation=snapshot?.observation,image=item.snapshot?.preview?.image_ref?.sha256;
    const exact=observation?.context_ref&&quickDigest(observation.context_ref)===quickDigest(item.contextRef);
    const ready=snapshot?.current===true&&['READY_OBSERVATION','OBSERVED','PARTIAL'].includes(observation?.status)&&!observation.questions?.length;
    return !!(exact&&ready&&(image?observation.coverage?.visual===true&&!!geometryForSnapshot(snapshot)&&snapshot.preview?.image_ref?.sha256===image:observation.coverage?.accessibility===true));
  }
  rememberInputProof(item,snapshot){
    if(!item.contextRef||!canonicalQuick(item))return;
    // Only bounded identity/boolean metadata: never retain frame pixels here.
    this.inputProofs.set(item.id,{sha256:item.sha256,valid:this.inputProof(item,snapshot)});
    if(this.inputProofs.size>200)this.inputProofs.delete(this.inputProofs.keys().next().value);
  }
  async verifyInputs(item,text=questionText(item)){
    if(item.scope.workInputUnavailable)throw Error('所选原工作的范围需要核对，原稿保留，请返回对话。');
    if(item.scope.originalWorkChoice&&!canonicalQuick(item))throw Error('原工作须从共享对话继续，当前文稿或能力任务选择已保留。');
    if(canonicalQuickMetadataStatus(item,text)||!canonicalQuick(item)||!item.contextRef)return;
    if(item.hostGeneration!==this.journal.hostGeneration)throw Error('这是保存的旧页面，请重新引用；原稿保留。');
    let proof=this.inputProofs.get(item.id);
    if(!proof||proof.sha256!==item.sha256){
      if(!this.loadSnapshot)throw Error('原页面来源尚未核对，请重新引用；原稿保留。');
      const snapshot=await this.loadSnapshot(item.contextRef);
      this.rememberInputProof(item,snapshot);proof=this.inputProofs.get(item.id);
    }
    if(!proof?.valid)throw Error('暂未取得来源明确的页面或截图，请重新引用；原稿与批注保留。');
  }
  saveContinuation(input){
    if(this.closed||this.isClosing())throw Error('应用正在关闭，补充尚未保存。');
    this.journal.command(input.parentOperationId);
    const saved=this.journal.saveContinuation(input);this.changed();return saved;
  }
  async load({draftId,draftRef}={}) {
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，保存的内容未打开。');
    const generation=this.generation,scopeSignature=quickDigest(this.scope);
    const original=draftRef?this.journal.get(quickRef(draftRef)):draftId?this.journal.find(draftId):
      this.journal.list({editing:true}).filter(item=>(item.delivery==='EDITING'||item.delivery==='SEND'&&(()=>{const saved=this.journal.continuationDraft(item.operationId);return !!(saved?.text||saved?.request);})())&&
        ['projectId','workspaceId','sessionId','conversationId'].every(key=>(item.scope[key]??null)===(this.scope[key]??null))&&
        quickDigest(item.scope.fileLinkRef??null)===quickDigest(this.scope.fileLinkRef??null)&&quickDigest(item.scope.originalWorkChoice??null)===quickDigest(this.scope.originalWorkChoice??null)&&(!item.scope.originalWorkChoice||quickDigest(item.scope.originalSource??null)===quickDigest(this.scope.originalSource??null))&&!!item.scope.workInputUnavailable===!!this.scope.workInputUnavailable)
        .sort((a,b)=>(this.journal.continuationDraft(a.operationId)?.updatedAt??a.updatedAt).localeCompare(this.journal.continuationDraft(b.operationId)?.updatedAt??b.updatedAt)).at(-1);
    if(!original)return undefined;
    if(original.delivery==='SEND') {
      const message=this.loadReadonlyResult(original.operationId,original.id);
      const continuationDraft=!draftRef?this.journal.continuationDraft(original.operationId):null;
      if(continuationDraft?.request){const key=continuationDraft.request.body.operation_id;continuationDraft.request.inspect=Object.hasOwn(this.journal.state.reviewControls??{},key);}
      // A submitted question is a durable historical fact. Its old snapshot
      // is display-only and must not become a new authorized capture or edit.
      const capture={snapshot:original.snapshot,scope:original.scope,hostGeneration:original.hostGeneration,restored:true};
      const marks=original.marks.map(({kind,...mark})=>({type:kind==='pen'?'freehand':kind,...mark}));
      return {id:original.id,revision:original.revision,comment:original.comment,regions:original.regions,marks,
        snapshot:original.snapshot,scope:original.scope,capture,restored:true,status:continuationDraft?'CONTINUATION':'READONLY',readonly:!continuationDraft,continuationDraft,
        operationId:original.operationId,message,...(original.nativeReview?{nativeReview:original.nativeReview}:{})};
    }
    const hasPage=original.contextRef||original.regions.length||original.marks.length;
    let capture={snapshot:original.snapshot,scope:original.scope,hostGeneration:original.hostGeneration,restored:!!hasPage};
    if(!canonicalQuickMetadataStatus(original,[original.comment,...(original.marks??[]).filter(m=>m.kind==='text').map(m=>m.text)].filter(Boolean).join('\n\n'))&&original.hostGeneration===this.journal.hostGeneration&&this.loadSnapshot&&original.contextRef) {
      try {
        const snapshot=await this.loadSnapshot(original.contextRef);
        if(snapshot?.observation?.context_ref?.sha256===original.contextRef.sha256&&snapshot.current===true)
          capture={snapshot,scope:original.scope,hostGeneration:original.hostGeneration,restored:false,
            ...(geometryForSnapshot(snapshot)?{geometry:geometryForSnapshot(snapshot)}:{})};
      }catch{/* Preserve readable old intent; unavailable authority cannot be promoted. */}
    }
    if(this.closed||this.isClosing()||generation!==this.generation||scopeSignature!==quickDigest(this.scope))
      throw new Error('当前讨论已变化，保存的内容未替换当前草稿。');
    this.captures.add(capture);
    let item=original;const head=this.journal.find(original.id),currentWorkEditing=!!(original.scope.originalWorkChoice||original.scope.workInputUnavailable)&&original.delivery==='EDITING'&&head?.revision===original.revision&&head.sha256===original.sha256;
    if(draftRef&&!currentWorkEditing||original.delivery!=='EDITING'||original.nativeReview?.process_start_consent||original.nativeReview?.remote_mcp_connect_consent) {
      // Confirmed inbox entries and submitted commands remain historical facts.
      // Reopening makes a fresh private edit, never removes that old entry.
      item=this.journal.save({draftId:'quick_'+randomUUID(),expectedRevision:0,capture,comment:original.comment,
        regions:original.regions,marks:original.marks,delivery:'EDITING',...(original.nativeReview?{nativeReview:quickResetNativeReview(original.nativeReview)}:{})});
    }
    const editableMarks=item.marks.map(({kind,...mark})=>({type:kind==='pen'?'freehand':kind,...mark}));
    return {id:item.id,revision:item.revision,comment:item.comment,regions:item.regions,marks:editableMarks,
      capture,restored:capture.restored,status:'EDITING',...(item.nativeReview?{nativeReview:item.nativeReview}:{})};
  }
  loadReadonlyResult(operationId,draftId) {
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，原操作回执未打开。');
    const command=this.journal.command(operationId),item=this.journal.find(command.draftId);
    if(command.operationId!==operationId||draftId!==undefined&&command.draftId!==draftId||
        item?.delivery!=='SEND'||item.operationId!==operationId)
      throw new Error('Saved question and original operation differ');
    if(quickDigest(quickAssociationScope(item.scope))!==quickDigest(quickAssociationScope(this.scope)))
      throw new Error('Saved question belongs to another discussion');
    if(command.message) {
      if(command.message.operation_id!==operationId||typeof command.message.status!=='string')
        throw new Error('Saved reply and original operation differ');
      return copy(this.decorateReviewControls(command.message));
    }
    // The in-memory observation may contain an unpersisted terminal reply.
    // Only the durable command projection can advance this read-only view.
    return {operation_id:operationId,status:'UNKNOWN_OUTCOME',automatic_retry:false};
  }
  replyTarget({draftId,operationId}){
    this.loadReadonlyResult(operationId,draftId);
    const item=this.journal.find(draftId),scope=quickAssociationScope(item.scope);
    if(!scope.conversationId)throw new Error('原讨论标识未能核对，原回答保留。');
    return {kind:'QUICK_REPLY',operationId,draftRef:quickRef({id:item.id,revision:item.revision,sha256:item.sha256}),scope};
  }
  retainSubmitted(input){
    const first=this.observed.get(input.operationId)?.first_submission_observation;
    if(first&&quickDigest(first)!==quickDigest(this.journal.command(input.operationId).first_submission_observation??null))
      throw new Error('Submission diagnostic has not been durably saved');
    return this.journal.retainedSubmitted(input);
  }
  _notSubmitted(error,item,operationId){
    const current=item&&this.journal.find(item.id);
    if(!current||current.operationId||this.journal.ownsCommand(operationId)||current.sha256!==item.sha256||current.revision!==item.revision)return error;
    return new QuickNotSubmittedError(operationId,{id:current.id,revision:current.revision,sha256:current.sha256},error);
  }
  async _checkedSend(input) {
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，本次没有提交。');
    let item=this.journal.find(input.draftId);
    if(item?.operationId)throw new Error('原操作已提交，请读取原结果；不会重复发送。');
    if(!item)throw new Error('意见尚未持久保存，本次没有提交。');
    const text=questionText(item),metadataOnly=canonicalQuickMetadataStatus(item,text);
    if(input.capture)this.authorized(input.capture);
    else if(!metadataOnly&&(item.contextRef||item.regions.length||item.marks.length))throw new Error('圈选依据尚未核对，本次没有提交。');
    if(input.capture?.restored&&!metadataOnly)throw new Error('这是保存的旧页面，请先重新引用再发送。');
    if(item.comment!==input.comment||quickDigest(item.regions)!==quickDigest(input.regions??[])||
      quickDigest(item.marks)!==quickDigest(quickMarks(input.marks??[]))||
      quickDigest(item.nativeReview??null)!==quickDigest(quickNativeReview(input.nativeReview))||
      input.capture&&quickDigest(item.scope)!==quickDigest(input.capture.scope))throw new Error('已保存的意见与待发送内容不一致，请重新保存。');
    await this.verifyInputs(item,text);
    if(canonicalQuick(item))canonicalQuickInput(item,text);
    return {item,text};
  }
  async preflightSend(input){
    const {item}=await this._checkedSend(input);
    return {status:'READY_TO_SUBMIT',draft_ref:{id:item.id,revision:item.revision,sha256:item.sha256}};
  }
  async send(input){
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，本次没有提交。');
    const original=this.journal.find(input.draftId);
    if(original?.operationId)return this.inspect(original.operationId);
    let checked;
    try{checked=await this._checkedSend(input);}
    catch(error){throw this._notSubmitted(error,original,input.operationId);}
    return this.dispatch(checked.item,input.operationId,checked.text);
  }
  async submitDraft({ref,text=''}) {
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，本次没有提交。');
    const item=this.journal.get(quickRef(ref));
    if(item.operationId)return this.inspect(item.operationId);
    if(item.delivery!=='DRAFT')throw new Error('只能发送已确认的这条批注。');
    // Merely changing Main's project must never move this feedback. An explicit
    // send uses the immutable record's own conversation and provider selection.
    return this.dispatch(item,'quick_'+randomUUID(),questionText(item,text));
  }
  async dispatch(item,operationId,text) {
    const shared=canonicalQuick(item);let selected;
    try{
      await this.verifyInputs(item,text);
      if(this.closed||this.isClosing())throw Error('应用正在关闭，输入已保留，本次没有提交。');
      selected=shared?canonicalQuickInput(item,text):null;
    }catch(error){throw this._notSubmitted(error,item,operationId);}
    const prepared=this.journal.prepareSend({id:item.id,revision:item.revision,sha256:item.sha256},{operationId,text,...(item.scope.originalWorkChoice?{originalWorkInputSHA256:canonicalQuickInputFingerprint({operation_id:operationId,...selected})}:{})});
    this.changed();
    if(!prepared.dispatchable)return this.inspect(operationId);
    const body={operation_id:operationId,project_id:item.scope.projectId??null,raw_text:text,context_ref:item.contextRef??null,
      provider_profile_ref:item.scope.providerProfileRef??null,file_source_ref:null,file_link_ref:item.nativeReview?.version===1?null:item.scope.fileLinkRef??null,
      conversation_id:item.scope.conversationId??'native_unbound',...(item.scope.contextSelectionId?{context_selection_id:item.scope.contextSelectionId}:{}),...(item.scope.conversationRef?{conversation_ref:item.scope.conversationRef}:{}),...(item.nativeReview?{native_review:quickNativeReview(item.nativeReview)}:{})};
    // The durable DISPATCHING entry exists before the only POST. Reload,
    // timeout, duplicate delivery and ack have no route to repeat this POST.
    let result;
    try {
      result=shared?canonicalQuickProjection(await this.request(canonicalQuickRoot(item.scope),{operation_id:operationId,...selected}),prepared):await this.request('/api/desktop-host/native-messages',body);
      if(!result||result.operation_id!==operationId||typeof result.status!=='string')throw Object.assign(new Error('Result identity differs'),{quick_receipt_identity_mismatch:true});
    }catch(error) {
      result={operation_id:operationId,status:'UNKNOWN_OUTCOME',
        ...(shared?{canonical_original:true,conversation_ref:item.scope.conversationRef,conversation_id:item.scope.conversationId??null,project_id:item.scope.projectId??null}:{}),
        first_submission_observation:firstSubmissionObservation(error,this.journal.clock),
        error:{message:'发送结果尚未确认，内容已保留；可查看原操作，未自动重发。'}};
    }
    result=this.recordResult(operationId,result);
    if(PENDING.has(result.status))this.watch(operationId);
    return result;
  }
  recordResult(operationId,result) {
    if(!result||result.operation_id!==operationId||typeof result.status!=='string')throw new Error('Result identity differs');
    const command=this.journal.command(operationId);
    if(command.input.scope.contextSelectionId&&result.status!=='UNKNOWN_OUTCOME'&&
        result.status!=='FAILED_NOT_DISPATCHED'&&result.dispatch_state!=='NOT_DISPATCHED'&&
        result.context_selection_id!==command.input.scope.contextSelectionId){
      const known=this.observed.get(operationId)??command.message;
      if(known?.context_selection_id===command.input.scope.contextSelectionId&&TERMINAL.has(known.status))return copy(known);
      result={operation_id:operationId,status:'UNKNOWN_OUTCOME',context_selection_mismatch:true,
        error:{message:'原回执的资料与记忆选择不一致，输入和原操作保留，请核查；没有重发。'}};
    }
    const first=command.first_submission_observation??submissionObservation(result.first_submission_observation);
    result=this.decorateReviewControls({...result});delete result.first_submission_observation;
    if(first)result.first_submission_observation=first;
    const cancellation=this.cancelObserved.get(operationId)??command.cancel?.status;
    const sameStopTarget=!result.canonical_original||!result.canonical_stop_ref||!command.cancel?.target_ref||quickDigest(result.canonical_stop_ref)===quickDigest(command.cancel.target_ref);
    if(cancellation&&sameStopTarget)result={...result,cancellation_status:result.owner_stop_confirmed===true||TERMINAL.has(result.status)&&!result.canonical_stop_ref?'SETTLED':cancellation==='DISPATCHING'?'UNKNOWN':cancellation};
    else if(!sameStopTarget)delete result.cancellation_status;
    let observed=result;
    try {this.journal.observe(operationId,result);}
    catch(error) {observed={...result,local_persistence_status:'FAILED',
      local_persistence_message:'已收到这次执行回执，但本地保存未成功；原操作编号已保留，请核查原操作，勿重复发送。'};}
    const previous=this.observed.get(operationId)??command.message;
    const publicObserved=copy(observed);this.observed.set(operationId,publicObserved);
    if(quickDigest(previous??null)!==quickDigest(publicObserved)){this.changed();if(!this.closed)this.notify(this.onResult,observed);}return observed;
  }
  decorateReviewControls(result){
    if(result.canonical_original&&result.control_capabilities?.can_cancel&&result.canonical_message_ref)result={...result,canonical_stop_ref:result.canonical_message_ref};
    const controls=this.journal.pendingReviewControls(result.operation_id),canonical=this.journal.canonicalControls(result.operation_id).slice(-16);
    if(result.canonical_original&&canonical.length){
      result={...result,canonical_controls:canonical.map(row=>({operation_id:row.operationId,raw_text:row.input.body.raw_text,
        status:row.result?.status??row.status,text:row.result?.text??'',canonical_message_ref:row.result?.canonical_message_ref??null,
        control_capabilities:row.result?.control_capabilities??null,canonical_processing:row.result?.canonical_processing===true}))};
      const latest=canonical.at(-1)?.result;
      if(latest?.control_capabilities?.can_cancel&&latest.canonical_message_ref)result={...result,canonical_stop_ref:latest.canonical_message_ref,
        control_capabilities:{...result.control_capabilities,can_cancel:true}};
      if(latest?.canonical_message_ref)result={...result,canonical_input_ref:latest.canonical_message_ref,
        control_capabilities:{...result.control_capabilities,can_guide:latest.control_capabilities?.can_guide===true,can_status:latest.control_capabilities?.can_status===true}};
    }
    return controls.length?{...result,local_review_controls:controls.map(row=>({...row.input,status:row.status}))}:result;
  }
  async canonicalContinuation({parentOperationId,kind,body,inspect=false}){
    quickControlBody(kind,body);
    const command=this.journal.command(parentOperationId);
    if(!canonicalQuick({scope:command.input.scope,nativeReview:command.input.nativeReview}))throw Error('原输入不是共享会话，补充已保留。');
    const reference=body.expected_canonical_message_ref;
    const known=[command.message?.canonical_message_ref,...this.journal.canonicalControls(parentOperationId).map(row=>row.result?.canonical_message_ref)].filter(Boolean);
    if(!known.some(value=>quickDigest(value)===quickDigest(reference)))throw Error('原输入引用尚未核对，补充已保留。');
    const unresolved=this.journal.pendingReviewControls(parentOperationId).find(row=>row.input.body.expected_canonical_message_ref);
    if(unresolved&&unresolved.operationId!==body.operation_id)throw Error('结果尚未确认，请核查原操作');
    const prepared=inspect?{dispatchable:false,...this.journal.reviewControl(body.operation_id)}:
      this.journal.prepareReviewControl(parentOperationId,{operationId:body.operation_id,kind,body});
    if(quickDigest(prepared.input)!==quickDigest({parentOperationId,kind,body}))throw Error('原补充请求不同，未重新发送。');
    const root=canonicalQuickRoot(command.input.scope),key=body.operation_id;
    let result;
    try{
      const value=await this.request(prepared.dispatchable?root+'/'+encodeURIComponent(reference.id)+'/continuations':root+'/'+encodeURIComponent(key),
        prepared.dispatchable?{operation_id:key,expected_message_ref:reference,raw_text:body.raw_text}:undefined);
      result=canonicalQuickProjection(value,{...command,operationId:key},{continuation:true,text:body.raw_text});
      const original=value.operation;
      if(original){result.status=original.status;if(!command.input.scope.originalWorkChoice){result.role=original.role;result.local_status=original.local_status;}}
      result.canonical_processing=['ACCEPTED','OBSERVED','NEXT_TURN_ASSOCIATED','DISPATCHING','PREPARED'].includes(result.status);
    }catch{result={operation_id:key,status:'UNKNOWN_OUTCOME',automatic_retry:false};}
    const previous=this.journal.reviewControl(key).result;
    result=this.journal.observeReviewControl(key,result);
    if(result.canonical_processing)this.watch(parentOperationId);
    if(quickDigest(previous??null)!==quickDigest(result))this.changed();return result;
  }
  async reviewCatalog({scope=this.scope,selection=null,kind}={}){
    const checked=quickScope(scope);
    if(!checked.conversationId||!checked.providerProfileRef)throw new Error('请先配置模型连接');
    if(kind!==undefined&&kind!=='CAPABILITY_TASK')throw new Error('Invalid capability purpose');
    const task=kind==='CAPABILITY_TASK',isTask=!!selection&&Object.hasOwn(selection,'mcp_tool_selection');
    if(selection)quickNativeReview(isTask?{version:2,kind:'CAPABILITY_TASK',selection,process_start_consent:false,remote_mcp_connect_consent:false,task_root_operation_id:null}:{version:1,selection,process_start_consent:false});
    if(!task&&isTask)throw new Error('Capability purpose differs');
    const read=async(capability,selected)=>{
      const query=new URLSearchParams({conversation_id:checked.conversationId,provider_profile_ref:JSON.stringify(checked.providerProfileRef)});
      if(checked.projectId)query.set('project_id',checked.projectId);
      if(checked.fileLinkRef)query.set('file_link_ref',JSON.stringify(checked.fileLinkRef));
      if(capability)query.set('kind','CAPABILITY_TASK');
      if(selected)query.set('selection',JSON.stringify(selected));
      const result=await this.request('/api/desktop-host/native-messages/review-capabilities?'+query);
      if(result?.schema!==(capability?'eeas.native-capability-task-capabilities.v2':'eeas.native-review-capabilities.v1')||Buffer.byteLength(JSON.stringify(result))>524288||!Array.isArray(result.skills)||!(capability?Array.isArray(result.mcp_catalog?.mcp_servers):Array.isArray(result.tools)))throw new Error('技能与工具目录暂不可用');
      return result;
    };
    if(!task)return read(false,selection);
    const [result,legacy]=await Promise.all([read(true,isTask?selection:null),checked.fileLinkRef?read(false,selection&&!isTask?selection:null):null]);
    return legacy?{...result,legacy_review_catalog:legacy}:result;
  }
  async reviewControl({parentOperationId,kind,body,inspect=false}){
    if(this.closed||this.isClosing())throw new Error('应用正在关闭。');
    if(body?.expected_canonical_message_ref)return this.canonicalContinuation({parentOperationId,kind,body,inspect});
    const original=this.journal.ownsCommand(parentOperationId)?this.journal.command(parentOperationId):null;
    if(original&&canonicalQuick({scope:original.input.scope,nativeReview:original.input.nativeReview}))throw Error('共享会话须使用原canonical输入引用，未发送其他任务控制。');
    const allowed={guidance:['raw_text'],permission:['request_ref','reply'],question:['request_ref','answers'],'question-reject':['request_ref']};
    quickControlBody(kind,body);
    const ordinary=Object.hasOwn(body,'expected_native_message_ref');
    if(!allowed[kind]||!ordinary&&Object.keys(body).sort().join(',')!==['operation_id','expected_review_ref',...allowed[kind]].sort().join(','))throw new Error('Exact review control required');
    if(kind==='guidance'&&(typeof body.raw_text!=='string'||!body.raw_text.trim()||Array.from(body.raw_text).length>16000))throw new Error('请输入补充内容');
    if(kind==='permission'&&!['once','reject'].includes(body.reply))throw new Error('Only once or reject is permitted');
    if(kind!=='guidance'&&(!body.request_ref||!/^(que|per)_[A-Za-z0-9_-]{1,100}$/.test(body.request_ref.id)||!Number.isSafeInteger(body.request_ref.generation)||body.request_ref.generation<1||! /^[a-f0-9]{64}$/.test(body.request_ref.sha256)))throw new Error('Exact pending request required');
    if(kind==='question'&&(!Array.isArray(body.answers)||body.answers.length<1||body.answers.length>16||body.answers.some(a=>!Array.isArray(a)||a.length>32||a.some(t=>typeof t!=='string'||!t.trim()||t.length>2000))))throw new Error('Invalid bounded answers');
    const unresolved=this.journal.pendingReviewControls(parentOperationId).find(row=>row.input.kind===kind);
    if(unresolved&&unresolved.operationId!==body.operation_id)throw new Error('结果尚未确认，请核查原操作');
    const prepared=inspect?{dispatchable:false,...this.journal.reviewControl(body.operation_id)}:this.journal.prepareReviewControl(parentOperationId,{operationId:body.operation_id,kind,body});
    if(prepared.input.parentOperationId!==parentOperationId||prepared.input.kind!==kind||quickDigest(prepared.input.body)!==quickDigest(body))throw new Error('Original review control differs');
    const root='/api/desktop-host/native-messages/'+encodeURIComponent(parentOperationId);
    const suffix=kind==='guidance'?'/guidance':'/'+(kind==='permission'?'permissions':'questions')+'/'+encodeURIComponent(body.request_ref.id)+(kind==='question-reject'?'/reject':'/reply');
    let result;
    try{result=await this.request(root+(prepared.dispatchable?suffix:'/controls/'+encodeURIComponent(body.operation_id)),prepared.dispatchable?body:undefined);
      if(result?.operation_id!==body.operation_id||typeof result.status!=='string')throw new Error('Review control receipt differs');
    }catch{return {operation_id:body.operation_id,status:'UNKNOWN_OUTCOME',automatic_retry:false};}
    return this.journal.observeReviewControl(body.operation_id,result);
  }
  async inspect(operationId) {
    this.journal.command(operationId);
    if(this.inFlight.has(operationId))return this.inFlight.get(operationId);
    const work=(async()=>{
      let result;
      const command=this.journal.command(operationId);
      if(canonicalQuick({scope:command.input.scope,nativeReview:command.input.nativeReview})){
        for(const row of this.journal.canonicalControls(operationId).filter(row=>row.result?.canonical_processing).slice(-16))
          await this.canonicalContinuation({...row.input,inspect:true});
      }
      try { result=canonicalQuick({scope:command.input.scope,nativeReview:command.input.nativeReview})?
        canonicalQuickProjection(await this.request(canonicalQuickRoot(command.input.scope)+'/'+encodeURIComponent(operationId)),command):
        await this.request('/api/desktop-host/native-messages/'+operationId); }
      catch(error) {
        if(error.status!==404)throw error;
        const known=this.observed.get(operationId)??this.journal.command(operationId).message;
        result=known&&TERMINAL.has(known.status)?known:{operation_id:operationId,status:'UNKNOWN_OUTCOME',
          ...(canonicalQuick({scope:command.input.scope,nativeReview:command.input.nativeReview})?{canonical_original:true,conversation_ref:command.input.scope.conversationRef,conversation_id:command.input.scope.conversationId??null}:{}),
          error:{message:'尚未查到原操作记录，不能据此认定未执行；未自动重发。'}};
      }
      return this.recordResult(operationId,result);
    })();
    this.inFlight.set(operationId,work);
    try {return await work;}finally{this.inFlight.delete(operationId);}
  }
  async cancel(operationId){
    if(this.closed||this.isClosing())throw new Error('应用正在关闭，未发出停止请求。');
    const command=this.journal.command(operationId);
    if(command.message?.canonical_original===true)return this.cancelCanonical(operationId,command);
    if(this.cancelInFlight.has(operationId))return this.cancelInFlight.get(operationId);
    // Unknown execution/cancel outcomes are inspected, never replayed. This
    // always addresses the journal's original operation, not current Main scope.
    const reviewStop=command.status==='UNKNOWN_OUTCOME'&&command.message?.operation_id===operationId&&(command.input.nativeReview&&command.message?.review?.can_cancel===true||command.message?.native_message_ref&&command.message?.control_capabilities?.can_cancel===true);
    if(command.cancel||command.message?.review?.stop_control||command.status==='CANCEL_REQUESTED'||!PENDING.has(command.status)&&!reviewStop)return this.inspect(operationId);
    this.journal.prepareCancel(operationId);
    const work=(async()=>{
      let result;
      try{result=canonicalQuick({scope:command.input.scope,nativeReview:command.input.nativeReview})?
        canonicalQuickProjection(await this.request(canonicalQuickRoot(command.input.scope)+'/'+encodeURIComponent(operationId)+'/cancel',{}),command):
        await this.request('/api/desktop-host/native-messages/'+operationId+'/cancel',{});
        if(!result||result.operation_id!==operationId||typeof result.status!=='string')throw new Error('Cancellation result identity differs');
        this.cancelObserved.set(operationId,'REQUESTED');try{this.journal.observeCancel(operationId,'REQUESTED');}catch{}
      }catch{
        this.cancelObserved.set(operationId,'UNKNOWN');try{this.journal.observeCancel(operationId,'UNKNOWN');}catch{}
        result=this.observed.get(operationId)??command.message??{operation_id:operationId,status:command.status};
      }
      const known=this.observed.get(operationId);
      if(known&&TERMINAL.has(known.status)&&PENDING.has(result.status))result=known;
      result=this.recordResult(operationId,result);if(PENDING.has(result.status))this.watch(operationId);return result;
    })();
    this.cancelInFlight.set(operationId,work);
    try{return await work;}finally{this.cancelInFlight.delete(operationId);}
  }
  async cancelCanonical(operationId,command){
    if(this.cancelInFlight.has(operationId))return this.cancelInFlight.get(operationId);
    const target=command.message?.canonical_stop_ref;
    if(!target)return this.inspect(operationId);
    const prepared=this.journal.prepareCanonicalCancel(operationId,target);
    if(!prepared.dispatchable)return this.inspect(operationId);
    this.cancelObserved.delete(operationId);
    const work=(async()=>{
      let status='UNKNOWN';
      try{
        const value=await this.request(canonicalQuickRoot(command.input.scope)+'/'+encodeURIComponent(target.id)+'/cancel',{});
        const selected=target.id===command.operationId?null:this.journal.canonicalControls(operationId).find(row=>row.result?.canonical_message_ref&&quickDigest(row.result.canonical_message_ref)===quickDigest(target));
        canonicalQuickProjection(value,{...command,operationId:target.id},{continuation:true,text:selected?.input.body.raw_text??(target.id===command.operationId?command.input.text:undefined)});status='REQUESTED';
      }catch{/* The frozen stop is queried, never replayed. */}
      this.cancelObserved.set(operationId,status);this.journal.observeCancel(operationId,status);
      const result=await this.inspect(operationId);
      if(result.canonical_stop_ref)this.watch(operationId);
      return result;
    })();this.cancelInFlight.set(operationId,work);
    try{return await work;}finally{this.cancelInFlight.delete(operationId);}
  }
  watch(operationId) {
    if(this.closed||this.isClosing()||this.timers.has(operationId))return;
    const timer=setTimeout(async()=>{
      this.timers.delete(operationId);if(this.closed||this.isClosing())return;
      try {const result=await this.inspect(operationId);if(PENDING.has(result.status)||this.journal.canonicalControls(operationId).some(row=>row.result?.canonical_processing))this.watch(operationId);}
      catch{/* A failed GET does not turn into a POST or steal focus. Explicit inspect remains available. */}
    },1200);
    timer.unref?.();this.timers.set(operationId,timer);
  }
  list(){return {records:this.journal.list({editing:true}).filter(item=>item.delivery!=='EDITING'||(item.scope.originalWorkChoice||item.scope.workInputUnavailable)&&(item.comment.trim()||item.marks.some(mark=>mark.kind==='text'&&mark.text.trim()))).map(item=>{
    if(item.operationId&&this.observed.has(item.operationId))return {...item,message:this.decorateReviewControls(copy(this.observed.get(item.operationId)))};
    if(item.delivery==='SEND'&&item.operationId&&!item.message)return {...item,message:{operation_id:item.operationId,
      conversation_id:item.scope.conversationId??null,status:'UNKNOWN_OUTCOME',automatic_retry:false,
      text:'上次发送的结果尚未核查，原问题和操作编号已保留。',
      error:{message:'请核查这次原操作；没有回执不证明未执行，未自动重新发送。'}}};
    return item.message?{...item,message:this.decorateReviewControls(item.message)}:item;
  })};}
  async restoreObservations(){
    const records=this.journal.list().filter(item=>item.delivery==='SEND'&&item.operationId&&
      (!item.message||PENDING.has(item.message.status)||item.message.status==='UNKNOWN_OUTCOME')).slice(-30);
    for(const item of records) {
      if(this.closed||this.isClosing())return;
      try {const result=await this.inspect(item.operationId);if(PENDING.has(result.status))this.watch(item.operationId);}
      catch{/* Keep the visible unresolved operation; no mutation or POST is retried. */}
    }
  }
  acknowledge(reference){return this.journal.acknowledge(quickRef(reference));}
  ownsArtifact(artifact) {
    const serialized=quickDigest(artifact);
    return this.list().records.some(item=>item.message?.artifacts?.some(value=>quickDigest(value)===serialized));
  }
  close(){this.closed=true;for(const timer of this.timers.values())clearTimeout(timer);this.timers.clear();this.inputProofs.clear();}
}
module.exports={QuickHost,geometryForSnapshot,questionText};
