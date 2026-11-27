'use strict';
const { contextBridge, ipcRenderer } = require('electron');
let closeFlushListener;
function replyTarget(value){
  const object=v=>!!v&&typeof v==='object'&&!Array.isArray(v),id=v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
  if(!object(value)||Object.keys(value).sort().join(',')!=='draftRef,kind,operationId,scope'||value.kind!=='QUICK_REPLY'||!id(value.operationId))return false;
  const ref=value.draftRef,scope=value.scope;
  return object(ref)&&Object.keys(ref).sort().join(',')==='id,revision,sha256'&&id(ref.id)&&Number.isSafeInteger(ref.revision)&&ref.revision>0&&typeof ref.sha256==='string'&&/^[a-f0-9]{64}$/.test(ref.sha256)&&object(scope)&&id(scope.conversationId)&&Object.keys(scope).every(key=>['projectId','workspaceId','sessionId','conversationId'].includes(key)&&id(scope[key]))&&(!(scope.workspaceId||scope.sessionId)||!!scope.projectId);
}
// No generic invoke, filesystem, shell, environment, HWND or process primitive.
contextBridge.exposeInMainWorld('eeasDesktop', Object.freeze({
  version: 2,
  ensureEditorCaptureChannel: () => ipcRenderer.invoke('eeas:editor-capture-channel-ensure'),
  getUILanguage: () => ipcRenderer.invoke('eeas:ui-language-get'),
  setUILanguage: (preference) => ipcRenderer.invoke('eeas:ui-language-set', preference),
  onUILanguageChanged: (listener) => {
    if(typeof listener!=='function')throw new TypeError('UI language listener required');
    const receive=(_event,value)=>listener(value);ipcRenderer.on('eeas:ui-language-changed',receive);
    return ()=>ipcRenderer.removeListener('eeas:ui-language-changed',receive);
  },
  getCollaborationState: () => ipcRenderer.invoke('eeas:collaboration-get'),
  captureCurrentContext: (body = {}) => ipcRenderer.invoke('eeas:context-capture', body),
  getConversationScope: (conversationId) => ipcRenderer.invoke('eeas:conversation-scope-get', conversationId),
  getConversationHistory: (body) => ipcRenderer.invoke('eeas:conversation-history-get', body),
  getNativeInputPreview: (body) => ipcRenderer.invoke('eeas:input-preview', body),
  saveDeliveryExport: (body) => ipcRenderer.invoke('eeas:delivery-export-save', body),
  inspectDeliveryExport: (body) => ipcRenderer.invoke('eeas:delivery-export-inspect', body),
  cancelDeliveryExport: (body) => ipcRenderer.invoke('eeas:delivery-export-cancel', body),
  recoverDeliveryExport: (body) => ipcRenderer.invoke('eeas:delivery-export-recover', body),
  listDeliveryExports: (body) => ipcRenderer.invoke('eeas:delivery-export-list', body),
  setCollaborationMode: (mode) => ipcRenderer.invoke('eeas:collaboration-mode', mode),
  setCollaborationFullscreen: (enabled) => ipcRenderer.invoke('eeas:collaboration-fullscreen', enabled),
  setCompanionPinned: (enabled) => ipcRenderer.invoke('eeas:companion-pinned', enabled),
  getAssistantOrbState: () => ipcRenderer.invoke('eeas:assistant-orb-get'),
  setAssistantOrbEnabled: (enabled) => ipcRenderer.invoke('eeas:assistant-orb-set', enabled),
  setWorkflowOrbScope: (scope) => ipcRenderer.invoke('eeas:workflow-orb-scope',scope),
  onWorkflowConversationOpen: (listener) => {
    if(typeof listener!=='function')throw new TypeError('Workflow conversation listener required');
    const receive=(_event,value)=>listener(value);
    ipcRenderer.on('eeas:workflow-conversation-open',receive);
    return ()=>ipcRenderer.removeListener('eeas:workflow-conversation-open',receive);
  },
  setQuickScope: (scope) => ipcRenderer.invoke('eeas:quick-scope', scope),
  getQuickDrafts: () => ipcRenderer.invoke('eeas:quick-drafts-get'),
  getQuickFileContext: () => ipcRenderer.invoke('eeas:file-context-get'),
  clearQuickFileContext: (body) => ipcRenderer.invoke('eeas:file-context-clear',body),
  selectQuickFileContext: (body) => ipcRenderer.invoke('eeas:file-context-select',body),
  getTaskActivity: () => ipcRenderer.invoke('eeas:task-activity-get'),
  getSupervisorConversation: () => ipcRenderer.invoke('eeas:supervisor-conversation-get'),
  getSupervisorHistoryPage: (body) => ipcRenderer.invoke('eeas:supervisor-history-read',body),
  getSupervisorOperation: (body) => ipcRenderer.invoke('eeas:supervisor-operation-read',body),
  saveSupervisorDraft: (body) => ipcRenderer.invoke('eeas:supervisor-draft-save',body),
  askSupervisor: (body) => ipcRenderer.invoke('eeas:supervisor-ask',body),
  inspectSupervisor: (operationId) => ipcRenderer.invoke('eeas:supervisor-inspect',operationId),
  saveSupervisorLocalAnswer: (body) => ipcRenderer.invoke('eeas:supervisor-local-save',body),
  getSupervisedTaskDetail: (key) => ipcRenderer.invoke('eeas:supervised-task-detail',key),
  getNativeConversation: (target) => ipcRenderer.invoke('eeas:native-conversation-get',target),
  getNativeConversations: (scope) => ipcRenderer.invoke('eeas:native-conversation-directory',scope),
  onSupervisorConversation: (listener) => {
    if(typeof listener!=='function')throw new TypeError('Supervisor listener required');
    const receive=(_event,value)=>listener(value);ipcRenderer.on('eeas:supervisor-conversation-changed',receive);
    return ()=>ipcRenderer.removeListener('eeas:supervisor-conversation-changed',receive);
  },
  onSupervisorConfigure: (listener) => {
    if(typeof listener!=='function')throw new TypeError('Supervisor listener required');
    const receive=()=>listener();ipcRenderer.on('eeas:supervisor-configure',receive);
    return ()=>ipcRenderer.removeListener('eeas:supervisor-configure',receive);
  },
  onSupervisorOpen: (listener) => {
    if(typeof listener!=='function')throw new TypeError('Supervisor listener required');
    const receive=()=>listener();ipcRenderer.on('eeas:supervisor-open',receive);
    return ()=>ipcRenderer.removeListener('eeas:supervisor-open',receive);
  },
  setVisibleTask: (key) => ipcRenderer.invoke('eeas:task-visible',key),
  onTaskActivity: (listener) => {
    if(typeof listener!=='function')throw new Error('Task activity listener required');
    const receive=(_event,value)=>listener(value);ipcRenderer.on('eeas:task-activity',receive);
    return ()=>ipcRenderer.removeListener('eeas:task-activity',receive);
  },
  onSupervisedTask: (listener) => {
    if(typeof listener!=='function')throw new Error('Task selection listener required');
    const receive=(_event,value)=>listener(value);ipcRenderer.on('eeas:supervised-task',receive);
    return ()=>ipcRenderer.removeListener('eeas:supervised-task',receive);
  },
  ackQuickDraft: (ref) => ipcRenderer.invoke('eeas:quick-draft-ack', ref),
  submitQuickDraft: (body) => ipcRenderer.invoke('eeas:quick-draft-submit', body),
  openQuickDraft: (ref) => ipcRenderer.invoke('eeas:quick-draft-open', ref),
  openNativeReplyLink: (body) => ipcRenderer.invoke('eeas:native-reply-link-open',body),
  onQuickDraftsChanged: (listener) => {
    if(typeof listener!=='function')throw new Error('Quick draft listener required');
    const receive=()=>listener();ipcRenderer.on('eeas:quick-drafts-changed',receive);
    return ()=>ipcRenderer.removeListener('eeas:quick-drafts-changed',receive);
  },
  onDraftFlushRequested: (listener) => {
    if(typeof listener!=='function'||closeFlushListener)throw new Error('One draft flush owner is required');
    const receive=(_event,body)=>{
      if(!body||typeof body.id!=='string'||! /^[a-f0-9]{32}$/.test(body.id))return;
      Promise.resolve().then(()=>listener()).then(
        ()=>ipcRenderer.invoke('eeas:close-flush-complete',{id:body.id,status:'SAVED'}),
        ()=>ipcRenderer.invoke('eeas:close-flush-complete',{id:body.id,status:'FAILED'}),
      ).catch(()=>{/* An unobserved flush never authorizes native shutdown. */});
    };
    closeFlushListener=receive;ipcRenderer.on('eeas:flush-drafts',receive);
    return ()=>{ipcRenderer.removeListener('eeas:flush-drafts',receive);if(closeFlushListener===receive)closeFlushListener=undefined;};
  },
  onAnnotationRequested: (listener) => {
    if(typeof listener!=='function')throw new Error('Annotation listener required');
    const receive=()=>listener();ipcRenderer.on('eeas:open-annotation',receive);
    return ()=>ipcRenderer.removeListener('eeas:open-annotation',receive);
  },
  onQuickQuestion: (listener) => {
    if(typeof listener!=='function')throw new Error('Quick question listener required');
    const receive=(_event,body)=>{if(body===undefined)listener();else if(replyTarget(body))listener(body);};ipcRenderer.on('eeas:quick-question',receive);
    return ()=>ipcRenderer.removeListener('eeas:quick-question',receive);
  },
  onCollaborationState: (listener) => {
    if(typeof listener!=='function')throw new Error('Collaboration state listener required');
    const receive=(_event,body)=>listener(body);ipcRenderer.on('eeas:collaboration-state',receive);
    return ()=>ipcRenderer.removeListener('eeas:collaboration-state',receive);
  },
  onNativeContext: (listener) => {
    if(typeof listener!=='function')throw new Error('Context listener required');
    const receive=(_event,body)=>listener(body);ipcRenderer.on('eeas:native-context',receive);
    return ()=>ipcRenderer.removeListener('eeas:native-context',receive);
  },
  getNativeReviewCatalog: (body) => ipcRenderer.invoke('eeas:native-review-catalog', body),
  controlNativeReview: (body) => ipcRenderer.invoke('eeas:native-review-control', body),
  submitNativeMessage: (body) => ipcRenderer.invoke('eeas:native-message-submit', body),
  inspectNativeMessage: (operationId) => ipcRenderer.invoke('eeas:native-message-inspect', operationId),
  cancelNativeMessage: (operationId) => ipcRenderer.invoke('eeas:native-message-cancel', operationId),
  prepareNativeContextRecovery: body=>ipcRenderer.invoke('eeas:native-context-recovery-prepare',body),
  restartNativeContextRecovery: body=>ipcRenderer.invoke('eeas:native-context-recovery-restart',body),
  inspectNativeContextRecovery: body=>ipcRenderer.invoke('eeas:native-context-recovery-operation',body),
  getDesktopControl: () => ipcRenderer.invoke('eeas:control-get'),
  delegateDesktopControl: (body) => ipcRenderer.invoke('eeas:control-delegate', body),
  takeoverDesktopControl: (body) => ipcRenderer.invoke('eeas:control-takeover', body),
  getNativeAppend: (contextRef, operationId) => ipcRenderer.invoke('eeas:native-append-get', {
    ...(contextRef===undefined?{}:{contextRef}),...(operationId===undefined?{}:{operationId}) }),
  prepareNativeAppend: (body) => ipcRenderer.invoke('eeas:native-append-prepare', body),
  approveNativeAppend: (body) => ipcRenderer.invoke('eeas:native-append-approve', body),
  startNativeAppend: (body) => ipcRenderer.invoke('eeas:native-append-start', body),
  revokeNativeAppend: (body) => ipcRenderer.invoke('eeas:native-append-revoke', body),
  chooseFolder: () => ipcRenderer.invoke('eeas:choose-folder'),
  chooseFiles: () => ipcRenderer.invoke('eeas:choose-files'),
  openSubscriptionLogin: (attemptId) => ipcRenderer.invoke('eeas:subscription-login-open', attemptId),
  nativeCapabilities: () => ipcRenderer.invoke('eeas:native-capabilities'),
  getAgentCapabilities: () => ipcRenderer.invoke('eeas:agent-capabilities'),
  getAgentTools: (scope) => ipcRenderer.invoke('eeas:agent-tools-read', scope),
  readAgentMCP: (id,configRef) => ipcRenderer.invoke('eeas:agent-mcp-read', id,configRef),
  inspectAgentCapabilityOperation: (id) => ipcRenderer.invoke('eeas:agent-capability-operation', id),
  readAgentSkill: (id) => ipcRenderer.invoke('eeas:agent-skill-read', id),
  importAgentSkill: (body) => ipcRenderer.invoke('eeas:agent-skill-import', body),
  setAgentSkillEnabled: (id, body) => ipcRenderer.invoke('eeas:agent-skill-enabled', id, body),
  configureAgentMCP: (body) => ipcRenderer.invoke('eeas:agent-mcp-configure', body),
  setAgentMCPEnabled: (id, body) => ipcRenderer.invoke('eeas:agent-mcp-enabled', id, body),
  probeAgentMCP: (id, body) => ipcRenderer.invoke('eeas:agent-mcp-probe', id, body),
  openDocument: (projectId, fileSourceRef, options={}) => ipcRenderer.invoke('eeas:open-document', { projectId, fileSourceRef,...options }),
  openLinkedFile: (fileLinkRef,scope) => ipcRenderer.invoke('eeas:open-linked-file',{fileLinkRef,scope}),
  openContextFile: (source,scope) => ipcRenderer.invoke('eeas:open-context-file',{source,scope}),
  openReportArtifact: (projectId, artifactId, sha256, downloadUrl, options={}) => ipcRenderer.invoke('eeas:open-report-artifact', { projectId, artifactId, sha256, downloadUrl,...options }),
}));
