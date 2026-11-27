'use strict';
const { app, BrowserWindow, dialog, ipcMain, session, globalShortcut, shell, screen, Tray, Menu, nativeImage, Notification } = require('electron');
const { NativeCompanion, revealOwnWindow } = require('./native-companion.cjs');
const {inputPreview}=require('./native-input-attachments.cjs');
const {conversationIdentity}=require('./conversation-reference.cjs');
const {conversationHistoryPath}=require('./conversation-history.cjs');
const { controllerPort } = require('./controller-port.cjs');
const {delegateInput,takeoverInput}=require('./desktop-control-policy.cjs');
const { spawn } = require('node:child_process');
const { randomBytes, createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { trustedSender, localOrigin, exactKeys } = require('./ipc-policy.cjs');
const { NativeAppendHost } = require('./native-append-host.cjs');
const { createNativeAppendActivation } = require('./native-append-activation.cjs');
const { createSubscriptionLoginOpener } = require('./subscription-login.cjs');
const { AssistantOrb } = require('./assistant-orb.cjs');
const { createOrbPointerWatch } = require('./orb-pointer-watch.cjs');
const { createAssistantOrbActions } = require('./assistant-orb-actions.cjs');
const { QuickSurface } = require('./quick-surface.cjs');
const {readTimeout:activationReadTimeout}=require('./quick-activation.cjs');
const { QuickJournal, quickAssociationScope, quickDigest, quickFileLinkRef } = require('./quick-journal.cjs');
const { QuickHost } = require('./quick-host.cjs');
const {replyLinkTargets}=require('./quick-reply-links.bundle.cjs');
const { openQuickArtifact } = require('./quick-artifact-open.cjs');
const { TaskActivityHost } = require('./task-activity-host.cjs');
const { CoordinatorTaskObserver } = require('./coordinator-task-observer.cjs');
const { SupervisorConversation } = require('./supervisor-conversation.cjs');
const {readNativeConversation,readNativeConversationDirectory}=require('./native-discussion-reader.cjs');
const { projectSupervisorSnapshot, readSupervisorTaskDetail, ownedSupervisorOperation } = require('./supervisor-host-integration.cjs');
const { createTaskNotifications } = require('./task-notifications.cjs');
const { BackgroundPresence } = require('./background-presence.cjs');
const { DISPLAY_NAME, SHORT_NAME, ICON_SVG, brandPNG } = require('./product-brand.cjs');
const { startupMotion } = require('./startup-motion.cjs');
const { resolveLocale, t } = require('../../packages/i18n/index.cjs');
const { UI_LANGUAGES, readPreferences, writePreference } = require('./desktop-preferences.cjs');
const { OwnedWindowCorners } = require('./window-corners.cjs');
const { IdeCompanionBridge } = require('./ide-companion-bridge.cjs');
const { createIdeApiAdapter } = require('./ide-api-adapter.cjs');
const {NativeDeliveryExportsHost,METHODS:NATIVE_EXPORT_METHODS}=require('./ide-delivery-exports.cjs');
const { DesktopEditorCaptureFeed } = require('./editor-capture-feed.cjs');
const { recoverRendererStorage } = require('./renderer-storage-recovery.cjs');

const root = path.resolve(__dirname, '../..');
const backgroundLaunch = process.argv?.includes('--eeas-background') === true;
const dataDir = path.resolve(process.env.EEAS_DATA_DIR || path.join(root, 'var', 'desktop'));
fs.mkdirSync(dataDir, { recursive: true });
for (const [key, directory] of [['userData','shell'],['sessionData','session'],['crashDumps','crashes'],['downloads','downloads']]) {
  const target = path.join(dataDir, directory); fs.mkdirSync(target, { recursive: true }); app.setPath(key, target);
}
const desktopToken = randomBytes(32).toString('hex');
let controller, origin, cookie, csrf, window, companion, nativeAppend, assistantOrb, quickSurface, quickHost, quickJournal, backgroundPresence;
let workflowOrbScope = null;
let quitting = false, stopped = false,lastChosenFolder, shortcutBusy = false, closePreparing=false, closeFlush, taskActivity;
let quickUnread=false, visibleTaskKey=null, supervisorConversation, coordinatorTaskObserver;
let taskAlertWindow=new Set();
let windowCorners;
let ideBridge;
let desktopCaptureFeed;
let taskNotifications;
const STARTUP_BUDGET_MS = 180000;
let startupAbort, startupCancelled = false, startupStartedAt, controllerSpawnedAt, startupPromise;
let controllerShutdown;
let startupPresentationLoaded = false;
let documentOpenEpoch = 0;

const orbPreferencesPath = path.join(dataDir, 'desktop-preferences.json');
function languageState() {
  const preference=readPreferences(orbPreferencesPath).ui_language;
  let systemLocale;try{systemLocale=app.getLocale?.();}catch{}
  return {preference:UI_LANGUAGES.includes(preference)?preference:'system',
    resolved_locale:resolveLocale(UI_LANGUAGES.includes(preference)?preference:'system',systemLocale||Intl.DateTimeFormat().resolvedOptions().locale)};
}
let uiLanguage=languageState();
const tr=(key,params={})=>t('main',key,uiLanguage.resolved_locale,{brand:DISPLAY_NAME,...params});
const nativeDeliveryExportsHost=new NativeDeliveryExportsHost({request,isClosing:()=>quitting||stopped||closePreparing,
  chooseSave:({filename})=>{
    const options={title:tr('nativeDeliverySaveTitle'),defaultPath:filename,buttonLabel:tr('nativeDeliverySaveButton'),
      filters:[{name:'ZIP',extensions:['zip']}],properties:['dontAddToRecent'],securityScopedBookmarks:false};
    return window&&!window.isDestroyed()&&window.isVisible()?dialog.showSaveDialog(window,options):dialog.showSaveDialog(options);
  }});
function orbEnabledPreference() { return readPreferences(orbPreferencesPath).assistant_orb_enabled!==false; }
function saveOrbPreference(enabled) { writePreference(orbPreferencesPath,'assistant_orb_enabled',enabled); }
function companionPinnedPreference() { return readPreferences(orbPreferencesPath).companion_pinned===true; }
function saveCompanionPinned(enabled) {
  if(quitting||closePreparing||stopped)throw new Error(tr('settingsClosing'));
  try{writePreference(orbPreferencesPath,'companion_pinned',enabled);}catch{throw new Error(tr('preferencesFailed'));}
  quickSurface?.setPinned(enabled);
}
async function setChatPinned(enabled) {
  if(quitting||closePreparing||stopped)throw new Error(tr('settingsClosing'));
  const state=ownWindowState(await companion.setCompanionPinned(enabled));
  if(!quitting&&!window.isDestroyed())window.webContents.send('eeas:collaboration-state',state);
  return state;
}
function setUILanguage(preference) {
  if(!UI_LANGUAGES.includes(preference))throw new Error(tr('languageUnsupported'));
  if(quitting||closePreparing||stopped)throw new Error(tr('settingsClosing'));
  try{writePreference(orbPreferencesPath,'ui_language',preference);}catch{throw new Error(tr('preferencesFailed'));}
  uiLanguage=languageState();
  for(const surface of [assistantOrb,quickSurface,backgroundPresence,taskNotifications])surface?.setLocale?.(uiLanguage.resolved_locale);
  if(window&&!window.isDestroyed())window.webContents.send('eeas:ui-language-changed',{...uiLanguage});
  return {...uiLanguage};
}
function orbFailure(error) {
  // Retain diagnostics locally; native/IPC implementation text is not product copy.
  fs.appendFileSync(path.join(dataDir, 'controller-boot.log'), JSON.stringify({ scope: 'ASSISTANT_ORB',
    status: 'UNAVAILABLE', error_type: error?.name || 'Error' }) + '\n');
}
const orbAction = createAssistantOrbActions({ getCompanion: () => companion, getWindow: () => window,
  getQuickSurface: () => quickSurface, getAnchor: () => assistantOrb?.getAnchor(),
  getTaskActivity:async()=>supervisedSnapshot(taskActivity?(await taskActivity.refresh()).snapshot:await request('/api/supervisor')),
  reveal: owned => revealOwnWindow(owned, screen), isClosing: () => quitting || stopped || !window || window.isDestroyed(),
  logFailure: orbFailure });

function validateWorkflowOrbScope(value) {
  if(value===null)return null;
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='node_id,project_id,run_id,stage_session_id')throw new TypeError('Invalid workflow scope');
  const {project_id,run_id,node_id,stage_session_id}=value;
  const valid=(text,pattern)=>typeof text==='string'&&pattern.test(text);
  if(!valid(project_id,/^[A-Za-z0-9_-]{1,100}$/)||!valid(node_id,/^[A-Za-z0-9_-]{1,100}$/)||
     !valid(run_id,/^wfr_[a-f0-9]{32}$/)||!valid(stage_session_id,/^native:[a-f0-9]{64}$/))throw new TypeError('Invalid workflow identity');
  return {project_id,run_id,node_id,stage_session_id};
}

async function routeOwnedOrbAction(action,anchor){
  if(!workflowOrbScope||!['ask','show-agent','annotate'].includes(action))return orbAction(action,anchor);
  if(quitting||stopped||!window||window.isDestroyed())return {status:'CLOSING'};
  if(window.isMinimized()||!window.isVisible()||window.isFocused?.()===false)revealOwnWindow(window,screen);
  window.webContents.send('eeas:workflow-conversation-open',{...workflowOrbScope,intent:action==='annotate'?'annotate':'chat'});
  return {status:'WORKFLOW_CONVERSATION'};
}

const watchOrbOutside=createOrbPointerWatch({isClosing:()=>quitting||stopped, onDiagnostic:orbFailure,
  openStream:signal=>{
    if(!origin||!cookie||!csrf)throw new Error(tr('controllerNotReady'));
    return fetch(origin+'/api/desktop-host/collaboration/orb-pointer-events',{redirect:'error',signal,
      headers:{Cookie:cookie,Origin:origin,'X-EEAS-CSRF':csrf,'X-EEAS-Desktop-Host':desktopToken,Accept:'application/x-ndjson'}});
  }});

function syncQuickIndicator(state=quickSurface?.get()) {
  if(!assistantOrb)return;
  const surfaceId=state?.overlayVisible?'annotationOverlay':state?.visible?'quickPopover':null;
  assistantOrb.setSurfaceActive(surfaceId!==null,surfaceId);
  const pending=(quickHost?.list().records??quickJournal?.list())?.some(item=>item.hostGeneration===quickJournal.hostGeneration&&item.delivery==='SEND'&&
    (!item.message||['PROCESSING','REQUESTING','RUNNING','QUEUED','CANCEL_REQUESTED'].includes(item.message.status)));
  if(state?.visible&&state.presentation!=='ACTIVITY')quickUnread=false;
  const global=taskActivity?.get().snapshot;
  const globalBusy=global?.settings.enabled&&global.tasks.some(task=>['RUNNING','CANCEL_REQUESTED'].includes(task.classification));
  const globalUnread=global?.settings.enabled&&global.alerts.some(alert=>!alert.acknowledged&&alert.category!=='RUNNING');
  assistantOrb.setFeedback({busy:!!(pending||state?.capturePending||globalBusy),unread:!!(quickUnread||state?.unread||globalUnread)});
}

async function openSupervisedTask(key,options={}) {
  if(quitting||closePreparing||stopped||!window||window.isDestroyed())return {status:'UNAVAILABLE'};
  const snapshot=(await taskActivity?.refresh())?.snapshot;
  const identity=value=>JSON.stringify([value?.kind,value?.project_id??null,value?.object_id,value?.operation_id??null,value?.generation]);
  const exact=key?snapshot?.tasks.find(task=>identity(task.key)===identity(key)):null;
  if(key&&!exact)throw new Error(tr('taskReference'));
  if(options.surface==='quick'&&exact&&ownedSupervisorOperation(supervisorConversation,exact)){
    const detail=await readSupervisorTaskDetail({store:coordinatorStore(),key:exact.key,snapshot,request,label:tr('supervisorQuestion')});
    const target=detail.continuation,operation=target?coordinatorStore().lookupOperation(target.operation_id):undefined;
    if(!operation||operation.request_sha256!==target.request_sha256||quitting||closePreparing||stopped)throw new Error(tr('taskReference'));
    return {status:'SUPERVISOR',selection:{operation_id:target.operation_id,request_sha256:target.request_sha256,question:detail.input_text,answer_text:detail.answer_text,status:operation.status==='CLAIMED'?'UNKNOWN':operation.status}};
  }
  const ownedWindow=window,state=await companion.setMode('companion');
  if(quitting||closePreparing||stopped||window!==ownedWindow||window.isDestroyed()||state.presentation_epoch!==companion.getPresentationEpoch())return {status:'UNAVAILABLE'};
  backgroundPresence?.restore();
  window.webContents.send('eeas:supervised-task',exact?.key??null);
  return {status:'OPENED'};
}
function supervisedSnapshot(snapshot){return projectSupervisorSnapshot(supervisorConversation,snapshot,tr('supervisorQuestion'));}
function coordinatorStore(){if(!supervisorConversation||quitting||stopped)throw new Error(tr('controllerNotReady'));return supervisorConversation;}
function checkedSupervisorAsk(body){exactKeys(body,['operationId','text','providerProfileRef',...body&&Object.hasOwn(body,'followupRef')?['followupRef']:[]]);return body;}
function publishSupervisorConversation(value=supervisorConversation?.snapshot()){
  if(!value)return;
  const selected=quickSurface?.supervisorSelection;let selectionUpdate;
  if(selected){
    try{
      const display=supervisorConversation.displayForOperation(selected.operation_id,selected.request_sha256);
      selectionUpdate={operation_id:selected.operation_id,request_sha256:selected.request_sha256,question:display.question,answer_text:display.text,status:display.status};
    }catch{selectionUpdate=null;}
  }
  quickSurface?.updateSupervisorConversation?.(value,quickHost?.scope.providerProfileRef??null,selectionUpdate);
}
async function openSupervisor(options={}){
  if(quitting||closePreparing||stopped||!companion||!window||window.isDestroyed())return {status:'UNAVAILABLE'};
  const ownedWindow=window,state=await companion.setMode('companion',{anchor:assistantOrb?.getAnchor()});
  if(quitting||closePreparing||stopped||window!==ownedWindow||window.isDestroyed()||state.presentation_epoch!==companion.getPresentationEpoch())return {status:'UNAVAILABLE'};
  backgroundPresence?.restore();
  window.webContents.send(options.configure===true?'eeas:supervisor-configure':'eeas:supervisor-open');
  return {status:'OPENED'};
}
function quickDraftsChanged() {
  if(!quitting&&window&&!window.isDestroyed())window.webContents.send('eeas:quick-drafts-changed');
  syncQuickIndicator();
}
async function loadQuickSnapshot(contextRef) {
  const snapshot=await request('/api/desktop-host/context/get',{context_ref:contextRef});
  if(snapshot.preview?.status==='AVAILABLE') {
    const image=snapshot.preview.image_ref;
    if(typeof image?.download_url!=='string'||! /^\/api\/desktop-host\/context\/images\/[A-Za-z0-9_-]{1,100}\?revision=[1-9][0-9]*&sha256=[a-f0-9]{64}$/.test(image.download_url))
      throw new Error(tr('frameUnavailable'));
    snapshot.preview.data_url=await imageReader(image.download_url,image);
    delete image.download_url;
  }
  return snapshot;
}

function startupError(message, code) { const error=new Error(message);error.code=code;return error; }
function checkStartup() {
  if(startupCancelled||quitting||stopped||window?.isDestroyed()) throw startupError(tr('startupCancelled'),'STARTUP_CANCELLED');
  if(startupAbort?.signal.aborted) throw startupAbort.signal.reason;
}
function startupLog(phase, fields={}) {
  fs.appendFileSync(path.join(dataDir,'controller-boot.log'),JSON.stringify({scope:'CONTROLLER_STARTUP',phase,
    elapsed_ms:Math.max(0,Date.now()-startupStartedAt),...fields})+'\n');
}
function cancelStartup() {
  startupCancelled=true;
  if(startupAbort&&!startupAbort.signal.aborted) startupAbort.abort(startupError(tr('startupCancelled'),'STARTUP_CANCELLED'));
}
function startupWait(promise, signal) {
  if(signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve,reject)=>{
    const abort=()=>{signal.removeEventListener('abort',abort);reject(signal.reason);};
    signal.addEventListener('abort',abort,{once:true});
    Promise.resolve(promise).then(value=>{signal.removeEventListener('abort',abort);resolve(value);},
      error=>{signal.removeEventListener('abort',abort);reject(error);});
  });
}
async function startupPause(milliseconds) {
  let timer;
  try { await startupWait(new Promise(resolve=>{timer=setTimeout(resolve,milliseconds);}),startupAbort.signal); }
  finally { clearTimeout(timer); }
}
function startupPage(phase) {
  const opening=phase==='PREPARING'&&!backgroundLaunch;
  const motion=startupMotion({svg:ICON_SVG,brand:DISPLAY_NAME,opening});
  const base=startupMotion({svg:ICON_SVG.replace(/id="([^"]+)"/g,'id="base-$1"').replace(/url\(#([^)]+)\)/g,'url(#base-$1)'),brand:DISPLAY_NAME,opening:false});
  return 'data:text/html;charset=utf-8,'+encodeURIComponent(`<!doctype html><html lang="${uiLanguage.resolved_locale}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>${DISPLAY_NAME}</title><style>
:root{color-scheme:light;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#303842;background:#f5f6f8}*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;background:#f5f6f8}body>.startup-frame{position:relative;min-height:100vh;display:grid;place-items:center;padding:52px 32px 32px;background:#f5f6f8;overflow:hidden;border-radius:0}
header{position:absolute;inset:0 0 auto;height:36px;-webkit-app-region:drag}main{display:flex;flex-direction:column;align-items:center;width:100%;max-width:352px}
h1{font-size:24px;font-weight:500;line-height:32px;letter-spacing:.02em;margin:12px 0 0;overflow-wrap:anywhere}
${motion.css}
.startup-mark{position:relative;inline-size:96px;aspect-ratio:1;pointer-events:none}
.startup-base,.startup-fill{position:absolute;inset:0;clip-path:inset(0);opacity:1;pointer-events:none}
.startup-base{opacity:.2}
.startup-mark .eeas-startup-motion{inline-size:100%;margin:0;transition:none}
.startup-fill[data-animated="true"]{animation:cirvenStartupFill 2800ms cubic-bezier(.36,.04,.24,1) 520ms infinite;will-change:clip-path,opacity}
.startup-fill .eeas-startup-motion[data-opening="true"][data-structure="three-part"] g[data-eeas-motion-part]{animation-duration:520ms}
.startup-fill .eeas-startup-motion[data-opening="true"][data-structure="whole-mark"]>svg{animation-duration:420ms}
@keyframes cirvenStartupFill{0%,8%{clip-path:inset(100% 0 0 0);opacity:1}76%,88%{clip-path:inset(0);opacity:1}100%{clip-path:inset(0);opacity:0}}
@media(prefers-reduced-motion:reduce){.startup-fill[data-animated="true"]{animation:none;clip-path:inset(0);opacity:1;will-change:auto}}
</style></head><body><div class="startup-frame"><header aria-hidden="true"></header><main role="status" aria-busy="true" aria-label="${tr('startingTitle')}"><div class="startup-mark" aria-hidden="true"><div class="startup-base">${base.markup}</div><div class="startup-fill" data-animated="${!backgroundLaunch}">${motion.markup}</div></div><h1>${SHORT_NAME}</h1></main></div></body></html>`);
}
async function showStartupPage(phase) {
  checkStartup();
  // One document preserves the short opening and the fill loop across internal
  // startup phases. Readiness never waits for either animation to finish.
  if(startupPresentationLoaded)return;
  await startupWait(window.loadURL(startupPage(phase)),startupAbort.signal);checkStartup();
  startupPresentationLoaded=true;
}

async function request(endpoint, body, signal) {
  if (!origin || !cookie || !csrf) throw new Error(tr('controllerNotReady'));
  if(signal?.aborted)throw signal.reason??new Error('Request aborted');
  const captureAction=body!==undefined&&body?.reply!=='reject'&&(/\/(?:messages|guidance|load|child-messages)$/.test(endpoint)||/^\/api\/agent-sessions\/open$/.test(endpoint)||/\/permissions\/[^/]+\/reply$/.test(endpoint)||endpoint.startsWith('/api/desktop-host/native-messages')&&!/\/(?:cancel|revoke|reject|stop|interrupt|disconnect)(?:\/|$)/.test(endpoint));
  if(captureAction&&desktopCaptureFeed&&!desktopCaptureFeed.active&&!quitting&&!stopped&&!closePreparing){
    try{await desktopCaptureFeed.start();}catch{/* The original backend guard still decides admission. */}
  }
  const response = await fetch(origin + endpoint, {
    method: body === undefined ? 'GET' : 'POST', redirect: 'error',
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(45000)]) : AbortSignal.timeout(45000),
    headers: { Cookie: cookie, Origin: origin, 'X-EEAS-CSRF': csrf,
      'X-EEAS-Desktop-Host': desktopToken, 'Accept-Language':uiLanguage.resolved_locale, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let value;
  try { value = await response.json(); }
  catch (error) {
    if (!response.ok) {
      error.status = response.status;
      error.quick_submission_observation = require('./quick-submission-observation.cjs').httpSubmissionObservation(response.status);
    }
    throw error;
  }
  if (!response.ok) {
    const error = new Error(value?.error?.message || value?.detail || 'Desktop document action failed');
    error.status = response.status;
    error.quick_submission_observation = require('./quick-submission-observation.cjs').httpSubmissionObservation(response.status, value);
    throw error;
  }
  return value;
}

async function startIdeCompanion() {
  if(ideBridge||quitting||stopped)return;
  let candidate;
  try {
  // Only this Main holds bootstrap credentials. The extension receives a
  // separate, explicitly approved client ticket and typed API projections.
  const dispatch=createIdeApiAdapter({request,nativeDeliveryExports:nativeDeliveryExportsHost,
    notifyEditorCaptures:(rows)=>candidate?.notifyEditorCaptures(rows),
    recordEditorCapture:(value)=>candidate?.recordEditorCapture(value),
    waitEditorCapture:(reference,options)=>candidate?.waitEditorCapture(reference,options),
    captureCursor:(reference)=>candidate?.captureCursor(reference)??0,
    openArtifact:({kind,target,...body},{scope,signal,authorize}={})=>kind==='workspace-file'
      ?target==='editor'?request('/api/desktop-host/native-open/file',body,signal).then(value=>{authorize?.();return value;})
        :resolveAndOpenDocument('/api/desktop-host/native-open/file',body,{keepHidden:true,signal,authorize,
            ...(scope?.conversation_ref?{linkToDiscussion:false}:{associationScope:{projectId:body.project_id,sessionId:scope?.session_ref?.id,conversationId:scope?.conversation_id}})})
      :resolveAndOpenDocument('/api/desktop-host/native-open/report',body,{keepHidden:true})});
  candidate=new IdeCompanionBridge({dataDir,version:app.getVersion?.()??'0.1.0',dispatch,
    requestPairing:async({client_label,signal})=>{
      if(signal.aborted||quitting||stopped||closePreparing)return {approved:false};
      const options={type:'question',title:tr('pairTitle'),message:tr('pairMessage',{client:client_label}),
        detail:tr('pairDetail'),
        buttons:[tr('pairAllow'),tr('pairDeny')],defaultId:1,cancelId:1,noLink:true,signal};
      const result=window&&!window.isDestroyed()&&window.isVisible()
        ?await dialog.showMessageBox(window,options):await dialog.showMessageBox(options);
      return {approved:!signal.aborted&&!quitting&&!stopped&&!closePreparing&&result.response===0};
    },reveal:()=>{
      if(quitting||stopped||closePreparing||!backgroundPresence?.restore()){
        const error=new Error(tr('closingReview'));
        error.public=true;error.code='DESKTOP_UNAVAILABLE';throw error;
      }
      return {status:'OPEN_REQUESTED'};
    },onError:orbFailure});
  ideBridge=candidate;
    await candidate.start();
    desktopCaptureFeed=new DesktopEditorCaptureFeed({
      openStream:signal=>fetch(origin+'/api/desktop-host/editor-capture-events',{redirect:'error',signal,
        headers:{Cookie:cookie,Origin:origin,'X-EEAS-CSRF':csrf,'X-EEAS-Desktop-Host':desktopToken,Accept:'application/x-ndjson'}}),
      notify:rows=>candidate.notifyEditorCaptures(rows),
      isClosing:()=>quitting||stopped||closePreparing,onError:orbFailure});
    await desktopCaptureFeed.start();
    if(quitting||stopped||closePreparing){await candidate.close();return;}
    startupLog('IDE_COMPANION_READY',{protocol:'eeas.ide-companion.v1'});
  }catch(error){
    await candidate?.close().catch(orbFailure);
    startupLog('IDE_COMPANION_UNAVAILABLE',{error_type:error?.name??'Error'});
    orbFailure(error);
  }
}

async function startController() {
  // A stable origin preserves renderer project/file/provider/draft state on
  // normal restarts. An occupied saved port is rejected before any HTTP access.
  checkStartup();
  const port = await startupWait(controllerPort(dataDir),startupAbort.signal);checkStartup();
  origin = localOrigin(`http://127.0.0.1:${port}`);
  const portable = process.platform === 'win32' ? path.join(root,'runtime','python','python.exe') : path.join(root,'runtime','python','bin','python3');
  const development = process.platform === 'win32' ? path.join(root,'.venv','Scripts','python.exe') : path.join(root,'.venv','bin','python');
  const python = process.env.EEAS_PYTHON || (fs.existsSync(portable) ? portable : development);
  if (!fs.existsSync(python)) throw new Error(tr('pythonMissing'));
  const temp = path.join(dataDir,'tmp'); fs.mkdirSync(temp,{recursive:true});
  const home = path.join(dataDir,'home');
  const env = { ...process.env, EEAS_HOST:'127.0.0.1', EEAS_PORT:String(port), EEAS_WORKER:'0',
    EEAS_NATIVE_RUNTIME:'opencode-http',
    EEAS_DATA_DIR:dataDir, EEAS_DESKTOP_HOST_TOKEN:desktopToken, EEAS_DESKTOP_HOST_PID:String(process.pid),
    PYTHONUTF8:'1', PYTHONIOENCODING:'utf-8', PYTHONDONTWRITEBYTECODE:'1',
    HOME:home, USERPROFILE:home, APPDATA:path.join(dataDir,'appdata'), LOCALAPPDATA:path.join(dataDir,'local-appdata'),
    TEMP:temp, TMP:temp, TMPDIR:temp, XDG_CACHE_HOME:path.join(dataDir,'cache'),
    XDG_CONFIG_HOME:path.join(dataDir,'config'), XDG_DATA_HOME:home };
  const profileDrive = process.platform === 'win32' ? path.win32.parse(home).root.replace(/[\\/]+$/,'') : '';
  if(profileDrive){env.HOMEDRIVE=profileDrive;env.HOMEPATH=home.slice(profileDrive.length);}
  else{delete env.HOMEDRIVE;delete env.HOMEPATH;}
  for(const key of Object.keys(env))if(key.toUpperCase()==='ELECTRON_RUN_AS_NODE')delete env[key];
  for(const key of ['EEAS_OPENCODE_PROFILE','EEAS_OPENCODE_API_KEY','ANTHROPIC_API_KEY','OPENAI_API_KEY',
    'CLAUDE_CODE_OAUTH_TOKEN','CHATGPT_ACCESS_TOKEN']) delete env[key];
  const packagedSite = path.join(root,'runtime','site-packages');
  if (fs.existsSync(packagedSite)) env.PYTHONPATH=packagedSite;
  const log = fs.openSync(path.join(dataDir,'controller-boot.log'),'a');
  const packageBootstrap = path.join(root,'scripts','native_package_bootstrap.py');
  const packageManifest = path.join(root,'PACKAGE_MANIFEST.json');
  const controllerArgs = fs.existsSync(packageManifest)
    ? ['-B',packageBootstrap,'--api'] : ['-B','-m','apps.api','serve'];
  if (fs.existsSync(packageManifest) && !fs.existsSync(packageBootstrap)) {
    fs.closeSync(log);
    throw new Error(tr('launcherMissing'));
  }
  checkStartup();controllerSpawnedAt=Date.now();
  try { controller = spawn(python,controllerArgs,{cwd:root,env,windowsHide:true,stdio:['ignore',log,log]}); }
  finally { fs.closeSync(log); }
  let attempts=0,ready=false;
  const deadline=controllerSpawnedAt+STARTUP_BUDGET_MS;
  const fail=(message,code)=>{if(!startupAbort.signal.aborted)startupAbort.abort(startupError(message,code));};
  const onError=()=>fail(tr('serviceStartFailed'),'STARTUP_PROCESS_ERROR');
  const onExit=()=>fail(tr('serviceStartExited'),'STARTUP_PROCESS_EXIT');
  controller.on('error',onError);controller.once('exit',onExit);
  const budgetTimer=setTimeout(()=>fail(tr('serviceTimeout'),'STARTUP_TIMEOUT'),STARTUP_BUDGET_MS);
  try {
    startupLog('SPAWNED',{pid:controller.pid,budget_ms:STARTUP_BUDGET_MS});
    await showStartupPage('WAITING');
    startupLog('WAITING_FOR_API',{budget_ms:STARTUP_BUDGET_MS});
    while(Date.now()<deadline) {
      checkStartup();
      if(controller.exitCode!==null||controller.signalCode!==null)onExit();
      checkStartup();attempts++;
      const probe=new AbortController();
      const relay=()=>probe.abort(startupAbort.signal.reason);
      startupAbort.signal.addEventListener('abort',relay,{once:true});
      const probeTimer=setTimeout(()=>probe.abort(startupError('Bootstrap probe timed out.','STARTUP_PROBE_TIMEOUT')),
        Math.min(1000,Math.max(1,deadline-Date.now())));
      try {
        const result=await startupWait(fetch(origin+'/api/bootstrap',{headers:{'X-EEAS-Bootstrap':'1'},
          redirect:'error',signal:probe.signal}).then(async response=>response.ok
            ? {value:await response.json(),cookie:response.headers.get('set-cookie')?.split(';')[0]} : null),probe.signal);
        checkStartup();
        if(result?.cookie&&typeof result.value?.csrf_token==='string'&&result.value.csrf_token) {
          cookie=result.cookie;csrf=result.value.csrf_token;ready=true;
          startupLog('API_READY',{pid:controller.pid,readiness_wait_ms:Date.now()-controllerSpawnedAt,attempts});
          return;
        }
      } catch(error) {
        checkStartup(); // Only readonly GET readiness is polled. Never restart or replay a POST.
      } finally {
        clearTimeout(probeTimer);startupAbort.signal.removeEventListener('abort',relay);
      }
      await startupPause(Math.min(150,Math.max(1,deadline-Date.now())));
    }
    fail(tr('serviceTimeout'),'STARTUP_TIMEOUT');checkStartup();
  } finally {
    clearTimeout(budgetTimer);controller.removeListener('exit',onExit);
    // Retain an error handler after readiness so an owned child error is never
    // an unhandled EventEmitter event; no restart/model replay is introduced.
    if(!ready)startupLog(startupCancelled?'CANCELLED':'NOT_READY',{
      readiness_wait_ms:Date.now()-controllerSpawnedAt,attempts,reason:startupAbort.signal.reason?.code||'STARTUP_ERROR'});
  }
}

function admitted(event) {
  if (!trustedSender(event,window,origin)) throw new Error(tr('owningPageRequired'));
}

function ownWindowState(state){
  return {...state,maximized:!!window&&!window.isDestroyed()&&window.isMaximized?.()===true,
    window_corners:windowCorners?.get()};
}

function registerIpc() {
  ipcMain.handle('eeas:task-activity-get',async event=>{admitted(event);const state=taskActivity?await taskActivity.refresh():{snapshot:null,readStatus:'NOT_READY'};return {...state,snapshot:supervisedSnapshot(state.snapshot)};});
  const supervisor=event=>{admitted(event);return coordinatorStore();};
  ipcMain.handle('eeas:supervisor-conversation-get',event=>supervisor(event).snapshot());
  ipcMain.handle('eeas:supervisor-history-read',(event,body)=>{const store=supervisor(event);exactKeys(body,['cursor','limit']);return store.readHistoryPage(body);});
  ipcMain.handle('eeas:supervisor-operation-read',(event,body)=>{const store=supervisor(event);exactKeys(body,['operation_id','request_sha256']);return store.readOperation(body);});
  ipcMain.handle('eeas:supervisor-draft-save',(event,body)=>{const store=supervisor(event);exactKeys(body,['text','expectedRevision']);return store.saveDraft(body);});
  ipcMain.handle('eeas:supervisor-ask',(event,body)=>{const store=supervisor(event);if(closePreparing)throw new Error(tr('settingsClosing'));return store.ask(checkedSupervisorAsk(body));});
  ipcMain.handle('eeas:supervisor-inspect',(event,operationId)=>supervisor(event).inspect(operationId));
  ipcMain.handle('eeas:supervisor-local-save',(event,body)=>{const store=supervisor(event);exactKeys(body,['question','answer']);return store.persistLocal(body);});
  ipcMain.handle('eeas:supervised-task-detail',async(event,key)=>{
    admitted(event);return readSupervisorTaskDetail({store:supervisorConversation,key,snapshot:taskActivity?.get().snapshot??await request('/api/supervisor'),request,label:tr('supervisorQuestion')});
  });
  ipcMain.handle('eeas:native-conversation-get',async(event,body)=>{
    admitted(event);exactKeys(body,['projectId','conversationId','operationId']);
    const store=coordinatorStore(),snapshot=store.snapshot();
    if(body.projectId===null&&body.conversationId===snapshot.conversation_id){
      const selected=store.lookupOperation(body.operationId);
      if(!selected)throw new Error('SUPERVISOR_QUESTION_REFERENCE_CHANGED');
      return {kind:'SUPERVISOR_DISCUSSION',conversation_id:snapshot.conversation_id,
        selection:store.displayForOperation(selected.operation_id,selected.request_sha256)};
    }
    return readNativeConversation(request,body);
  });
  ipcMain.handle('eeas:native-conversation-directory',async(event,body)=>{admitted(event);return readNativeConversationDirectory(request,body);});
  ipcMain.handle('eeas:task-visible',async(event,key)=>{
    admitted(event);
    if(key===null){visibleTaskKey=null;return {status:'CLEARED'};}
    const identity=value=>JSON.stringify([value?.kind,value?.project_id??null,value?.object_id,value?.operation_id??null,value?.generation]);
    const found=taskActivity?.get().snapshot?.tasks.find(task=>identity(task.key)===identity(key));
    if(!found)throw new Error(tr('visibleTaskUnknown'));visibleTaskKey={...found.key};return {status:'OBSERVED'};
  });
  const openSubscriptionLogin = createSubscriptionLoginOpener({ request,
    openExternal: target => shell.openExternal(target),
    isClosing: () => quitting || stopped || !window || window.isDestroyed() });
  ipcMain.handle('eeas:subscription-login-open', async (event, attemptId) => {
    admitted(event); return openSubscriptionLogin(attemptId);
  });
  ipcMain.handle('eeas:editor-capture-channel-ensure',async event=>{
    admitted(event);if(closePreparing||quitting||stopped)return {connected:false,read_only:true};
    if(!desktopCaptureFeed)return {connected:false,read_only:true};
    try{return await desktopCaptureFeed.start();}catch{return {connected:false,read_only:true};}
  });
  for(const method of ['get','prepare','approve','start','revoke']) {
    ipcMain.handle('eeas:native-append-'+method,async(event,body)=>{admitted(event);return nativeAppend[method](body);});
  }
  ipcMain.handle('eeas:collaboration-get',async event=>{admitted(event);return ownWindowState(await companion.get());});
  ipcMain.handle('eeas:context-capture',async(event,args)=>{admitted(event);return companion.capture(args);});
  ipcMain.handle('eeas:conversation-scope-get',async(event,identity)=>{admitted(event);return request('/api/conversations/'+encodeURIComponent(conversationIdentity(identity))+'/scope');});
  ipcMain.handle('eeas:conversation-history-get',async(event,body)=>{admitted(event);return request(conversationHistoryPath(body));});
  ipcMain.handle('eeas:input-preview',async(event,body)=>{admitted(event);return inputPreview(body,{request,imageReader});});
  for(const [channel,method]of ['save','inspect','cancel','recover','list'].map((name,index)=>['eeas:delivery-export-'+name,NATIVE_EXPORT_METHODS[index]])){
    ipcMain.handle(channel,async(event,body)=>{admitted(event);exactKeys(body,['scope','payload']);
      try{return {status:'OK',result:await nativeDeliveryExportsHost.dispatch(method,body,{client_id:'desktop_'+event.sender.id,
        currentGrants:()=>{admitted(event);return {methods:[...NATIVE_EXPORT_METHODS],project_ids:[body.scope?.project_id]};}})};}
      catch(e){return {status:'ERROR',error:{code:e?.public&&/^[A-Z0-9_]{1,80}$/.test(e.code??'')?e.code:'NATIVE_EXPORT_UNKNOWN'}};}
    });
  }
  ipcMain.handle('eeas:collaboration-mode',async(event,mode)=>{admitted(event);return ownWindowState(await companion.setMode(mode));});
  ipcMain.handle('eeas:collaboration-fullscreen',async(event,enabled)=>{admitted(event);return ownWindowState(await companion.setFullscreen(enabled));});
  ipcMain.handle('eeas:companion-pinned',async(event,enabled)=>{admitted(event);return setChatPinned(enabled);});
  ipcMain.handle('eeas:ui-language-get',event=>{admitted(event);return {...uiLanguage};});
  ipcMain.handle('eeas:ui-language-set',(event,preference)=>{admitted(event);return setUILanguage(preference);});
  ipcMain.handle('eeas:assistant-orb-get', event => {
    admitted(event); return assistantOrb?.get() ?? { enabled: false, visible: false };
  });
  ipcMain.handle('eeas:workflow-orb-scope', async(event,body)=>{
    admitted(event);
    if(quitting||stopped)return;
    workflowOrbScope=validateWorkflowOrbScope(body);
    await assistantOrb?.setWorkflowActive(workflowOrbScope!==null);
  });
  ipcMain.handle('eeas:assistant-orb-set', async (event, enabled) => {
    admitted(event);
    if (quitting || stopped) throw new Error(tr('settingsClosing'));
    if (typeof enabled !== 'boolean' || !assistantOrb) throw new Error(tr('orbUnavailable'));
    saveOrbPreference(enabled);
    return assistantOrb.setEnabled(enabled);
  });
  ipcMain.handle('eeas:quick-scope', async(event,body)=>{admitted(event);const before=quickDigest(quickAssociationScope(quickHost.scope));const result=quickHost.setScope(body);if(before!==quickDigest(quickAssociationScope(result)))documentOpenEpoch++;await quickSurface?.syncDiscussion(result);publishSupervisorConversation();return result;});
  ipcMain.handle('eeas:file-context-get',event=>{admitted(event);return quickHost.fileContextState();});
  ipcMain.handle('eeas:file-context-clear',(event,body)=>{
    admitted(event);exactKeys(body,['scope','expectedRef']);quickHost.assertFileScope(body.scope);documentOpenEpoch++;
    return quickHost.clearFileContext(body);
  });
  ipcMain.handle('eeas:file-context-select',async(event,body)=>{
    admitted(event);exactKeys(body,['scope','fileLinkRef']);const scope=quickHost.assertFileScope(body.scope),reference=quickFileLinkRef(body.fileLinkRef);
    if(reference.project_id!==scope.projectId)throw new Error(tr('documentUnresolved'));
    const candidate=quickHost.fileContextState().legacy_candidates.find(item=>quickDigest(item.fileLinkRef)===quickDigest(reference));
    if(!candidate)throw new Error(tr('documentUnresolved'));
    const epoch=++documentOpenEpoch;
    const observation=await request('/api/desktop-host/file-contexts/observe',{link_ref:reference});
    quickHost.assertFileScope(scope);
    if(epoch!==documentOpenEpoch||quickDigest(observation.link_ref)!==quickDigest(reference))throw new Error(tr('openSuperseded'));
    quickHost.setFileContext({file_link_ref:reference,filename:candidate.filename},{scope});return quickHost.fileContextState();
  });
  ipcMain.handle('eeas:quick-drafts-get', event=>{admitted(event);return quickHost.list();});
  ipcMain.handle('eeas:quick-draft-ack', (event,ref)=>{admitted(event);return quickHost.acknowledge(ref);});
  ipcMain.handle('eeas:quick-draft-submit', (event,body)=>{
    admitted(event);exactKeys(body,['ref',...['text'].filter(key=>Object.hasOwn(body??{},key))]);
    return quickHost.submitDraft(body);
  });
  ipcMain.handle('eeas:quick-draft-open', async(event,ref)=>{
    admitted(event);const generation=quickHost.scope.generation;
    if(closePreparing)throw new Error(tr('draftUnsaved'));
    return quickSurface.openAsk({draftRef:ref,isCurrent:()=>!quitting&&!closePreparing&&!stopped&&quickHost.scope.generation===generation});
  });
  ipcMain.handle('eeas:native-reply-link-open',async(event,body)=>{
    admitted(event);exactKeys(body,['operationId','text_sha256','url']);
    if(typeof body.operationId!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(body.operationId)||typeof body.url!=='string'||body.url.length>4096||/[\u0000-\u0020\u007f]/.test(body.url))throw new Error('Invalid reply link');
    const target=new URL(body.url);if(!['http:','https:','mailto:'].includes(target.protocol)||target.username||target.password)throw new Error('Invalid reply link');
    const scope=quickHost.scope,generation=scope.generation;
    const reply=await request('/api/desktop-host/native-messages/'+body.operationId);
    if(quitting||closePreparing||stopped||quickHost.scope.generation!==generation||reply.operation_id!==body.operationId||reply.project_id!==scope.projectId||reply.conversation_id!==scope.conversationId||typeof reply.text!=='string'||quickDigest(reply.text)!==body.text_sha256||!replyLinkTargets(reply.text).includes(target.href))throw new Error('Reply changed');
    await shell.openExternal(target.href);return {status:'OPENED'};
  });
  ipcMain.handle('eeas:close-flush-complete',(event,body)=>{
    admitted(event);exactKeys(body,['id','status']);
    if(!closeFlush||body.id!==closeFlush.id||!['SAVED','FAILED'].includes(body.status))throw new Error(tr('flushReference'));
    const pending=closeFlush;closeFlush=undefined;clearTimeout(pending.timer);
    body.status==='SAVED'?pending.resolve():pending.reject(new Error(tr('draftUnsaved')));
    return {status:'OBSERVED'};
  });
  ipcMain.handle('eeas:native-message-submit',async(event,args)=>{
    admitted(event);exactKeys(args,['operationId','rawText',...['contextRef','providerProfileRef','fileSourceRef','fileLinkRef','conversationId','projectId','nativeReview','inputAttachments','conversationRef','contextSelectionId'].filter(key=>Object.hasOwn(args??{},key))]);
    return companion.submitMessage(args);
  });
  ipcMain.handle('eeas:native-review-catalog',async(event,body)=>{admitted(event);exactKeys(body,['scope',...['selection','kind'].filter(key=>Object.hasOwn(body??{},key))]);if(body.kind!==undefined&&body.kind!=='CAPABILITY_TASK')throw new Error('Invalid capability purpose');if(!quickHost)throw new Error('原生协作尚未就绪');return quickHost.reviewCatalog(body);});
  ipcMain.handle('eeas:native-review-control',async(event,body)=>{admitted(event);exactKeys(body,['parentOperationId','kind','body',...['inspect'].filter(key=>Object.hasOwn(body??{},key))]);if(body.inspect!==undefined&&typeof body.inspect!=='boolean')throw new Error('Exact inspection flag required');if(!quickHost)throw new Error('原生协作尚未就绪');return quickHost.reviewControl(body);});
  for(const method of ['inspect','cancel'])ipcMain.handle('eeas:native-message-'+method,async(event,id)=>{
    admitted(event);if(typeof id!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(id))throw new Error(tr('operationRequired'));
    try { const result=await request('/api/desktop-host/native-messages/'+id+(method==='cancel'?'/cancel':''),method==='cancel'?{}:undefined);return quickHost?quickHost.decorateReviewControls(result):result; }
    catch(error) {
      if(method==='inspect'&&error.status===404) return {operation_id:id,status:'NO_RECORD',automatic_retry:false,
        execution_outcome:'NOT_PROVEN',automatic_input_authorized:false,
        error:{code:'NOT_FOUND',message:tr('operationMissing')}};
      throw error;
    }
  });
  for(const action of ['prepare','restart','operation'])ipcMain.handle('eeas:native-context-recovery-'+action,async(event,body)=>{
    admitted(event);exactKeys(body,action==='prepare'?['parentOperationId','expectedNativeRef']:action==='restart'?['parentOperationId','request']:['parentOperationId','operationId','requestSha256']);
    const validId=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value),validHash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
    if(!validId(body.parentOperationId))throw Error('Exact native parent required');const base='/api/desktop-host/native-messages/'+encodeURIComponent(body.parentOperationId)+'/context-recovery';
    if(action==='prepare'){const ref=body.expectedNativeRef;exactKeys(ref,['id','revision','sha256']);if(!validId(ref.id)||!Number.isSafeInteger(ref.revision)||ref.revision<1||!validHash(ref.sha256))throw Error('Exact private native reference required');return request(base+'/prepare',{expected_native_ref:ref});}
    if(action==='restart'){const input=body.request;exactKeys(input,['operation_id','expected_native_ref','preparation_sha256']);const ref=input.expected_native_ref;exactKeys(ref,['id','revision','sha256']);if(!validId(input.operation_id)||!validHash(input.preparation_sha256)||!validId(ref.id)||!Number.isSafeInteger(ref.revision)||ref.revision<1||!validHash(ref.sha256))throw Error('Exact private native recovery required');return request(base,input);}
    if(!validId(body.operationId)||!validHash(body.requestSha256))throw Error('Exact original recovery key required');let result;try{result=await request(base+'/'+encodeURIComponent(body.operationId));}catch(error){if(error.status===404)return {kind:'NATIVE_CONTEXT_RECOVERY_NOT_FOUND',parent_operation_id:body.parentOperationId,operation_id:body.operationId,request_sha256:body.requestSha256,read_only:true,model_calls:0,native_posts:0,original_execution_outcome:'NOT_PROVEN'};throw error;}if(result.parent_operation_id!==body.parentOperationId||result.operation_id!==body.operationId||result.request_sha256!==body.requestSha256)throw Error('Original private recovery differs');return result;
  });
  ipcMain.handle('eeas:control-get',async event=>{admitted(event);return request('/api/desktop-host/control/get');});
  ipcMain.handle('eeas:control-delegate',async(event,args)=>{admitted(event);return request('/api/desktop-host/control/delegate',delegateInput(args));});
  ipcMain.handle('eeas:control-takeover',async(event,args)=>{admitted(event);return request('/api/desktop-host/control/takeover',takeoverInput(args));});
  ipcMain.handle('eeas:choose-folder',async event=>{
    admitted(event);
    const chosen=await dialog.showOpenDialog(window,{title:tr('chooseFolder'),properties:['openDirectory']});
    if(!chosen.canceled)lastChosenFolder=chosen.filePaths[0];
    return chosen.canceled ? null : chosen.filePaths[0];
  });
  ipcMain.handle('eeas:choose-files',async event=>{
    admitted(event);
    const chosen=await dialog.showOpenDialog(window,{title:tr('chooseFiles'),properties:['openFile','multiSelections'],
      filters:[{name:tr('materialFilter'),extensions:['docx','dotx','pptx','xlsx','vsdx','xmind','pdf','md','txt','png','jpg','jpeg']},{name:tr('allFiles'),extensions:['*']}]});
    return chosen.canceled ? [] : chosen.filePaths;
  });
  ipcMain.handle('eeas:native-capabilities',async event=>{admitted(event);return request('/api/desktop-host/capabilities');});
  ipcMain.handle('eeas:agent-capabilities',async event=>{admitted(event);return request('/api/desktop-host/capabilities/get');});
  ipcMain.handle('eeas:agent-tools-read',async(event,scope)=>{
    admitted(event);if(scope!==undefined&&scope!==null)exactKeys(scope,['project_id','session_ref','generation']);
    return request('/api/desktop-host/capabilities/tools'+(scope?'?scope='+encodeURIComponent(JSON.stringify(scope)):''));
  });
  ipcMain.handle('eeas:agent-mcp-read',async(event,id,ref)=>{
    admitted(event);exactKeys(ref,['id','revision','sha256']);
    if(typeof id!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(id)||ref.id!==id||!Number.isSafeInteger(ref.revision)||ref.revision<1||typeof ref.sha256!=='string'||! /^[a-f0-9]{64}$/.test(ref.sha256))throw new Error(tr('capabilityRequired'));
    return request('/api/desktop-host/capabilities/mcp/'+id+'?'+new URLSearchParams({revision:String(ref.revision),sha256:ref.sha256}));
  });
  ipcMain.handle('eeas:agent-capability-operation',async(event,id)=>{
    admitted(event);if(typeof id!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(id))throw new Error(tr('capabilityRequired'));
    return request('/api/desktop-host/capabilities/operations/'+id);
  });
  ipcMain.handle('eeas:agent-skill-read',async(event,id)=>{
    admitted(event);if(typeof id!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(id))throw new Error(tr('skillRequired'));
    return request('/api/desktop-host/capabilities/skills/'+id);
  });
  async function saveCapability(route,body){
    try{return await request('/api/desktop-host/capabilities/'+route,body);}
    catch(error){if([400,403,404,409,422].includes(error?.status))return {schema:'eeas.capability-rejection.v1',confirmed_rejected:true,status:error.status,code:'HTTP_REJECTED',message:error.message};throw error;}
  }
  for(const [channel,route] of [['agent-skill-import','skills/import'],['agent-mcp-configure','mcp/configure']]){
    ipcMain.handle('eeas:'+channel,async(event,body)=>{admitted(event);return saveCapability(route,body);});
  }
  for(const [channel,kind,method] of [['agent-skill-enabled','skills','enabled'],['agent-mcp-enabled','mcp','enabled'],['agent-mcp-probe','mcp','probe']]){
    ipcMain.handle('eeas:'+channel,async(event,id,body)=>{
      admitted(event);if(typeof id!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(id))throw new Error(tr('capabilityRequired'));
      return method==='enabled'?saveCapability(kind+'/'+id+'/'+method,body):request('/api/desktop-host/capabilities/'+kind+'/'+id+'/'+method,body);
    });
  }
  ipcMain.handle('eeas:open-document',async(event,args)=>{
    admitted(event);exactKeys(args,['projectId','fileSourceRef',...['deliveryOperationId','scope'].filter(key=>Object.hasOwn(args??{},key))]);
    if(args.scope)quickHost.assertFileScope(args.scope);
    return resolveAndOpenDocument('/api/desktop-host/native-open/file',{project_id:args.projectId,file_source_ref:args.fileSourceRef,...args.deliveryOperationId?{delivery_operation_id:args.deliveryOperationId}:{}},{associationScope:args.scope});
  });
  ipcMain.handle('eeas:open-linked-file',async(event,args)=>{
    admitted(event);exactKeys(args,['fileLinkRef','scope']);const scope=quickHost.assertFileScope(args.scope),reference=quickFileLinkRef(args.fileLinkRef);
    if(scope.projectId!==reference.project_id)throw new Error(tr('documentUnresolved'));
    return resolveAndOpenDocument('/api/desktop-host/native-open/link',{link_ref:reference,project_id:reference.project_id},{associationScope:scope});
  });
  ipcMain.handle('eeas:open-context-file',async(event,args)=>{
    admitted(event);exactKeys(args,['source','scope']);const scope=quickHost.assertFileScope(args.scope);
    // The original backend resolves an indexed, identity-pinned file. Opening
    // a historical reference never retargets this discussion or its file input.
    return resolveAndOpenDocument('/api/desktop-host/native-open/context-file',
      {source:args.source,project_id:args.source?.project_id},{associationScope:scope,linkToDiscussion:false});
  });
  ipcMain.handle('eeas:open-report-artifact',async(event,args)=>{
    admitted(event);exactKeys(args,['projectId','artifactId','sha256','downloadUrl',...['scope'].filter(key=>Object.hasOwn(args??{},key))]);
    if(args.scope)quickHost.assertFileScope(args.scope);
    return resolveAndOpenDocument('/api/desktop-host/native-open/report',{
      project_id:args.projectId,artifact_id:args.artifactId,sha256:args.sha256,download_url:args.downloadUrl},{associationScope:args.scope});
  });
}

async function resolveAndOpenDocument(route,body,{keepHidden=false,associationScope,linkToDiscussion=true,signal,authorize}={}){
  // Freeze the user's presentation choice before resolving a file over IPC.
  // A late response must not undo a newer mode, pin, or fullscreen decision.
  const scope=quickAssociationScope(associationScope??quickHost?.scope??{});
  const presentation={openEpoch:++documentOpenEpoch,expectedEpoch:companion.getPresentationEpoch(),
    expectedFileScope:linkToDiscussion&&scope.projectId===body.project_id&&scope.conversationId?scope:undefined,
    expectedNavigationScope:associationScope?scope:undefined,
    keepHidden:keepHidden||window.isMinimized()||!window.isVisible()};
  const requestBody=route.endsWith('/link')?{link_ref:body.link_ref}:route.endsWith('/context-file')?{source:body.source}:body;
  const resolved=await request(route,requestBody,signal);
  authorize?.();
  return openResolved(resolved,presentation);
}

function assertDocumentOpen(openEpoch,expectedEpoch,expectedFileScope){
  if(quitting||closePreparing||stopped||!window||window.isDestroyed())throw new Error(tr('openStopped'));
  if(openEpoch!==undefined&&openEpoch!==documentOpenEpoch)throw new Error(tr('openSuperseded'));
  if(expectedEpoch!==undefined&&companion.getPresentationEpoch()!==expectedEpoch)
    throw new Error(tr('presentationChanged'));
  if(expectedFileScope&&quickDigest(expectedFileScope)!==quickDigest(quickAssociationScope(quickHost?.scope??{})))throw new Error(tr('presentationChanged'));
}

async function openResolved(value,{openEpoch,expectedEpoch,expectedFileScope,expectedNavigationScope,keepHidden=false}={}){
  const assertCurrent=()=>{
    assertDocumentOpen(openEpoch,expectedEpoch,expectedFileScope);
    if(expectedNavigationScope&&quickDigest(expectedNavigationScope)!==quickDigest(quickAssociationScope(quickHost?.scope??{})))throw new Error(tr('presentationChanged'));
  };
  assertCurrent();
  if(value.status!=='RESOLVED'||typeof value.native_path!=='string'||!path.isAbsolute(value.native_path))throw new Error(tr('documentUnresolved'));
  if(/\.(exe|com|bat|cmd|ps1|vbs|js|msi|lnk|url|scr)$/i.test(value.native_path))throw new Error(tr('documentsOnly'));
  // Shell acknowledgement does not establish that Word loaded the document.
  // Count UTF-16 code units, including a Windows extended-path prefix: Office
  // does not gain long-path support from that prefix or a short-name alias.
  if(process.platform==='win32'&&/\.(docx?|docm|dotx?|dotm|rtf)$/i.test(value.native_path)&&value.native_path.length>=260)
    throw new Error(tr('wordPathTooLong'));
  const preserveHidden=keepHidden||window.isMinimized()||!window.isVisible();
  const failure=await shell.openPath(value.native_path);
  if(failure)throw new Error(failure);
  const {native_path,...result}=value;
  const canPresent=()=>!quitting&&!closePreparing&&!stopped&&window&&!window.isDestroyed()&&window.isVisible()&&!window.isMinimized();
  let presentationStatus=preserveHidden||!canPresent()?'PRESERVED_HIDDEN':'SUPERSEDED',presentationError;
  if(!preserveHidden&&canPresent()){
    try{
      const current=assertCurrent;
      current();
      const state=await companion.prepareDocumentOpen({expectedEpoch,current});
      current();
      window.webContents.send('eeas:collaboration-state',ownWindowState(state));
      presentationStatus=state.mode==='companion'&&canPresent()?'COMPANION_VISIBLE_WITHOUT_FOCUS':'PRESERVED_HIDDEN';
    }catch(error){
      // The OS request already happened. Preserve that fact and never replay it.
      presentationStatus='UNCONFIRMED';presentationError={code:error.code??'PRESENTATION_NOT_CONFIRMED',message:error.message};
    }
  }
  let fileContextStatus='NOT_AVAILABLE';
  // OS opening was already requested. Do not replay it or let its late ack
  // replace the context chosen by a subsequent explicit open.
  const current=openEpoch===undefined||openEpoch===documentOpenEpoch;
  if(!current)fileContextStatus='SUPERSEDED';
  else if(!quitting&&!closePreparing&&!stopped&&value.file_link_ref&&expectedFileScope&&quickHost?.setFileContext){
    try{quickHost.setFileContext({file_link_ref:value.file_link_ref,filename:value.filename},{scope:expectedFileScope});fileContextStatus='SAVED';}
    catch{fileContextStatus='LOCAL_SAVE_UNCONFIRMED';}
  }
  return {...result,status:'OPEN_REQUESTED',file_context_status:fileContextStatus,file_context_scope:expectedFileScope??null,
    companion_presentation_status:presentationStatus,...presentationError?{companion_presentation_error:presentationError}:{},
    native_window_attached:false,desktop_geometry_mutated:false};
}

async function imageReader(route,image){
  const response=await fetch(origin+route,{redirect:'error',signal:AbortSignal.timeout(15000),
    headers:{Cookie:cookie,Origin:origin,'X-EEAS-Desktop-Host':desktopToken}});
  if(!response.ok||response.headers.get('content-type')?.split(';')[0]!=='image/png')throw new Error(tr('previewUnavailable'));
  const raw=Buffer.from(await response.arrayBuffer());
  if(raw.length>16*1024*1024||raw.length!==image.byte_length||createHash('sha256').update(raw).digest('hex')!==image.sha256)throw new Error(tr('imageBytesChanged'));
  if(raw.length<24||raw.readUInt32BE(16)!==image.width||raw.readUInt32BE(20)!==image.height)throw new Error(tr('imageDimensionsChanged'));
  return 'data:image/png;base64,'+raw.toString('base64');
}

function controllerExitedCleanly(child) {
  return child?.exitCode===0&&child.signalCode===null;
}
async function waitForControllerExit(child) {
  if(controllerExitedCleanly(child))return;
  if(child.exitCode!==null||child.signalCode!==null)throw new Error(tr('exitAbnormal'));
  await new Promise((resolve,reject)=>{
    let timer;
    const finish=error=>{clearTimeout(timer);child.removeListener('exit',exit);child.removeListener('error',failed);
      error?reject(error):resolve();};
    const exit=(code,signal)=>finish(code===0&&signal===null?undefined:
      new Error(tr('exitAbnormal')));
    const failed=()=>finish(new Error(tr('exitUnknown')));
    child.once('exit',exit);child.once('error',failed);
    timer=setTimeout(()=>finish(new Error(tr('exitWait'))),45000);
    // The child may have exited between the first check and listener setup.
    if(child.exitCode!==null||child.signalCode!==null)exit(child.exitCode,child.signalCode);
  });
}
async function stopController() {
  await desktopCaptureFeed?.close();
  if (stopped) return;
  if(controllerShutdown) {
    // A POST with an unknown response must never be replayed. Only this exact
    // retained ChildProcess can later prove its own successful termination.
    if(controller!==controllerShutdown.child||!controllerExitedCleanly(controllerShutdown.child))
      throw new Error(tr('previousExitUnknown'));
    stopped=true;return;
  }
  globalShortcut.unregisterAll();
  if(nativeAppend){nativeAppend.takeoverEpoch++;nativeAppend.activate.cancelPending?.();}
  if(!cookie&&!csrf&&!companion) {
    // Before bootstrap has admitted any business owner, cancellation may stop
    // only the exact child freshly spawned by this Main. Never look up a PID.
    if(controller&&controller.exitCode===null&&controller.signalCode===null)controller.kill('SIGTERM');
    stopped=true;return;
  }
  if(!cookie||!csrf||!controller)throw new Error(tr('exitContextMissing'));
  if (companion) {
    const result=await companion.close();
    if(result?.status!=='CLOSED') throw new Error(tr('ownersNotClosed'));
  }
  if (cookie && csrf) {
    const result=await request('/api/desktop-host/close',{});
    if(result.status!=='CLOSED') throw new Error(tr('nativeWindowNotDetached'));
  }
  controllerShutdown={child:controller}; // Persist in Main before the one POST.
  const result=await request('/api/shutdown',{});
  if(result?.status!=='stopping'||result.owner_shutdown?.status!=='CLOSED')
    throw new Error(tr('executionNotClosed'));
  await waitForControllerExit(controllerShutdown.child);
  stopped=true;
}

async function quitSafely() {
  if(quitting||closePreparing)return;closePreparing=true;
  try {
    nativeDeliveryExportsHost.abortPending();await nativeDeliveryExportsHost.drainWrites();
    // Flush actual drafts while the API and host are still accepting writes.
    // Startup cancellation has no mounted product renderer to flush.
    if(quickSurface)await quickSurface.hide({save:true});
    if(origin&&window&&!window.isDestroyed()&&window.webContents.getURL().startsWith(origin+'/')) {
      await new Promise((resolve,reject)=>{
        const id=randomBytes(16).toString('hex');
        const timer=setTimeout(()=>{if(closeFlush?.id===id)closeFlush=undefined;reject(new Error(tr('flushTimeout')));},12000);
        closeFlush={id,resolve,reject,timer};window.webContents.send('eeas:flush-drafts',{id});
      });
    }
    quitting=true;cancelStartup();
    // Stop accepting IDE work before closing the existing business owners.
    // Disconnecting this transport never sends a task cancel or shutdown.
    await desktopCaptureFeed?.close();await ideBridge?.close();
    await taskActivity?.close();await coordinatorTaskObserver?.close();await stopController();await supervisorConversation?.close();quickHost?.close();quickSurface?.dispose();assistantOrb?.dispose();backgroundPresence?.dispose();windowCorners?.dispose();if(window&&!window.isDestroyed())window.destroy();app.quit();
  }
  catch(error) {
    quitting=false;
    if(assistantOrb&&window&&!window.isDestroyed())void assistantOrb.setHostMinimized(window.isMinimized()||!window.isVisible()).catch(orbFailure);
    dialog.showErrorBox(tr('closeIncomplete'),error.message+(ideBridge?.closed?'\n'+tr('idePaused'):''));
  } finally {closePreparing=false;}
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  const recoverOwnWindow=()=>{if(!quitting){if(backgroundPresence)backgroundPresence.restore();else revealOwnWindow(window,screen);}};
  const openApplicationWindow=async()=>{
    if(quitting||closePreparing||stopped||!window||window.isDestroyed())return;
    let state;
    if(companion){
      if(window.isFullScreen()){
        const epoch=companion.getPresentationEpoch();await companion.setFullscreen(false);
        if(companion.getPresentationEpoch()!==epoch+1)return;
      }
      state=await companion.setMode('workbench');
      if(state.presentation_epoch!==companion.getPresentationEpoch())return;
    }
    if(quitting||closePreparing||stopped||window.isDestroyed())return;
    window.maximize();recoverOwnWindow();
    if(state)window.webContents.send('eeas:collaboration-state',ownWindowState(state));
  };
  app.on('second-instance',(_event,argv)=>{if(!Array.isArray(argv)||!argv.includes('--eeas-background'))return openApplicationWindow().catch(orbFailure);});
  app.on('activate',recoverOwnWindow);
  startupPromise=app.whenReady().then(async()=>{
    startupAbort=new AbortController();startupStartedAt=Date.now();uiLanguage=languageState();checkStartup();
    session.defaultSession.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
    session.defaultSession.on('will-download',(_event,item)=>{
      item.setSaveDialogOptions({defaultPath:path.join(lastChosenFolder||app.getPath('downloads'),path.basename(item.getFilename()))});
    });
    window=new BrowserWindow({width:1180,height:880,minWidth:780,minHeight:480,title:DISPLAY_NAME,icon:nativeImage.createFromBuffer(brandPNG('app256')),
      backgroundColor:'#f5f6f8',show:false,frame:true,autoHideMenuBar:true,roundedCorners:false,
      ...(process.platform==='win32'?{titleBarStyle:'hidden',titleBarOverlay:{color:'#f5f6f8',symbolColor:'#303842',height:36}}:{}),
      webPreferences:{preload:path.join(__dirname,'preload.cjs'),
        nodeIntegration:false,contextIsolation:true,sandbox:true,webviewTag:false}});
    windowCorners=new OwnedWindowCorners({window,screen,rectangular:true,platform:process.platform,systemVersion:process.getSystemVersion?.(),onError:orbFailure,
      isClosing:()=>quitting||closePreparing||stopped});
    // Keep the native Windows resize/caption controls while the product draws
    // its own continuous title band. No default Electron File/Edit menu row.
    window.removeMenu?.();
    backgroundPresence=new BackgroundPresence({Tray,Menu,nativeImage,getWindow:()=>window,getOrb:()=>assistantOrb,locale:uiLanguage.resolved_locale,
      reveal:owned=>revealOwnWindow(owned,screen),onExit:quitSafely,isClosing:()=>quitting||stopped,onError:orbFailure});
    // Position only our normal window in the selected display's existing DIP
    // work area. No desktop reservation or foreign-window geometry is changed.
    const workArea=screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
    window.setMinimumSize(Math.max(1,Math.min(780,Math.floor(workArea.width))),Math.max(1,Math.min(480,Math.floor(workArea.height))));
    const ownWidth=Math.min(1180,workArea.width),ownHeight=Math.min(880,workArea.height);
    window.setBounds({x:workArea.x+Math.max(0,workArea.width-ownWidth-16),
      y:workArea.y+Math.max(0,Math.floor((workArea.height-ownHeight)/2)),width:ownWidth,height:ownHeight},false);
    window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    // The normal close gesture only hides this window; the retained tray owns
    // explicit exit. destroy() happens only after qualified owner shutdown.
    window.webContents.on('will-navigate',(event,url)=>{try{if(new URL(url).origin!==origin)event.preventDefault();}catch{event.preventDefault();}});
    // The collaboration UI must stay visible in ordinary screenshots, remote
    // desktops and OCR. Official account credentials remain in their browser.
    if(process.platform==='win32')window.setContentProtection(false);
    startupLog('PREPARING');
    await showStartupPage('PREPARING');
    if(!backgroundLaunch&&backgroundPresence.canAutoReveal()){window.maximize();window.showInactive();}
    await startController();checkStartup();
    if(fs.existsSync(path.join(dataDir,'restore-provenance.json'))){
      try{
        await startupWait(recoverRendererStorage({dataDir,origin,BrowserWindow,ses:session.defaultSession,signal:startupAbort.signal}),startupAbort.signal);checkStartup();
      }catch(error){
        const failure=new Error(uiLanguage.resolved_locale.startsWith('en')?'Saved page drafts could not be safely restored. Your original data is retained; check this restored copy before continuing.':'恢复后的页面草稿尚未能安全接续，原数据已保留。请先核查恢复副本。');failure.cause=error;throw failure;
      }
    }
    await showStartupPage('CONNECTING');checkStartup();
    if (process.platform === 'win32') {
      quickJournal=new QuickJournal({dataDir});
      quickSurface=new QuickSurface({BrowserWindow,screen,ipcMain,locale:uiLanguage.resolved_locale,isClosing:()=>quitting||stopped,
        onActivationState:async options=>{
          const scope=quickDigest(quickHost.scope);
          const settle=options?.phase==='begin'&&options?.trigger==='CTRL_SHIFT_SPACE';
          const state=await request('/api/desktop-host/context/activation-state'+(settle?'?settle_trigger=true':''),
            undefined,AbortSignal.timeout(activationReadTimeout(options)));
          return scope===quickDigest(quickHost.scope)?{...state,host_scope_sha256:scope}:{status:'UNAVAILABLE'};
        },
        pinned:companionPinnedPreference(),onSetPinned:setChatPinned,
        onEmptyCapture:()=>quickHost.emptyCapture(),onCapture:body=>quickHost.capture(body),onDraft:body=>quickHost.save(body),onContinuationDraft:body=>quickHost.saveContinuation(body),
        onPrepareSend:body=>quickHost.preflightSend(body),onSend:body=>quickHost.send(body),onInspect:id=>quickHost.inspect(id),onLoadDraft:body=>quickHost.load(body),
        onLoadReadonlyResult:(id,draftId)=>quickHost.loadReadonlyResult(id,draftId),
        onRetainSubmitted:body=>quickHost.retainSubmitted(body),
        onReviewCatalog:body=>quickHost.reviewCatalog(body),onReviewControl:body=>quickHost.reviewControl(body),
        onClearFileContext:capture=>quickHost.clearDraftFileContext(capture),onCancel:id=>quickHost.cancel(id),
        onOpenTask:key=>openSupervisedTask(key,{surface:'quick'}),onExpandActivity:()=>openSupervisedTask(null),onOpenSupervisor:openSupervisor,
        onGetSupervisorConversation:()=>coordinatorStore().snapshot(),
        onReadSupervisorHistory:body=>{exactKeys(body,['cursor','limit']);return coordinatorStore().readHistoryPage(body);},
        onReadSupervisorOperation:body=>{exactKeys(body,['operation_id','request_sha256']);return coordinatorStore().readOperation(body);},
        onSaveSupervisorDraft:body=>{exactKeys(body,['text','expectedRevision']);return coordinatorStore().saveDraft(body);},
        onAskSupervisor:body=>{if(closePreparing)throw new Error(tr('settingsClosing'));return coordinatorStore().ask(checkedSupervisorAsk(body));},
        onInspectSupervisor:operationId=>coordinatorStore().inspect(operationId),
        onSaveSupervisorLocalAnswer:body=>{if(closePreparing)throw new Error(tr('settingsClosing'));exactKeys(body,['question','answer']);return coordinatorStore().persistLocal(body);},

        onRefreshActivity:async()=>supervisedSnapshot(taskActivity?(await taskActivity.refresh()).snapshot:await request('/api/supervisor')),
        onExpand:async(mode,origin)=>{const target=origin?quickHost.replyTarget(origin):undefined,generation=quickHost.generation;
          const current=()=>!quitting&&!stopped&&generation===quickHost.generation;
          const hidden=await quickSurface.hide({save:true,isCurrent:current});
          if(hidden?.status==='STALE'||hidden?.status==='BUSY'||!current())return {status:'STALE'};
          const epoch=quickSurface.epoch;
          return orbAction(mode==='workbench'?'workbench':'show-agent',{...(target?{replyTarget:target}:{}),isCurrent:()=>current()&&epoch===quickSurface.epoch});},
        onOpenArtifact:artifact=>openQuickArtifact({artifact,quickHost,resolveAndOpenDocument,translate:tr}),
        onOpenExternal:target=>shell.openExternal(target),
        onError:orbFailure,onState:syncQuickIndicator});
      await startupWait(quickSurface.ready,startupAbort.signal);checkStartup();
      assistantOrb = new AssistantOrb({ BrowserWindow, screen, ipcMain, locale:uiLanguage.resolved_locale, onAction: routeOwnedOrbAction, onError: orbFailure, watchOutside:watchOrbOutside,
        isClosing: () => quitting || stopped });
      await startupWait(assistantOrb.ready, startupAbort.signal);checkStartup();
      await assistantOrb.setEnabled(orbEnabledPreference());checkStartup();
      await assistantOrb.setHostMinimized(window.isMinimized()||!window.isVisible());checkStartup();
    }
    companion=new NativeCompanion({window,screen,initialMode:'workbench',pinnedPreference:companionPinnedPreference(),onPinnedChange:saveCompanionPinned,isClosing:()=>quitting||closePreparing||stopped,
      ownedWindows:assistantOrb?{assistantOrb:assistantOrb.window,...quickSurface.windows}:{},request,imageReader,onContext:context=>{
      if(!quitting&&!window.isDestroyed())window.webContents.send('eeas:native-context',context);
    }});
    if(quickJournal)quickHost=new QuickHost({journal:quickJournal,getCompanion:()=>companion,request,
      onChanged:quickDraftsChanged,isClosing:()=>quitting||stopped,loadSnapshot:loadQuickSnapshot,
      onResult:result=>{
        if(!['PROCESSING','REQUESTING','RUNNING','QUEUED','CANCEL_REQUESTED'].includes(result.status))quickUnread=true;
        quickSurface.publishResult(result);syncQuickIndicator();
      }});
    const publishWindowState=()=>{
      void companion.get().then(state=>{if(!quitting&&!window.isDestroyed())window.webContents.send('eeas:collaboration-state',ownWindowState(state));})
        .catch(()=>{/* A failed own-state read cannot become a fullscreen success receipt. */});
    };
    window.on('enter-full-screen',publishWindowState);
    window.on('leave-full-screen',publishWindowState);
    window.on('maximize',publishWindowState);
    window.on('unmaximize',publishWindowState);
    await startupWait(companion.initialize(),startupAbort.signal);checkStartup();
    // Native geometry is readable only after this exact Main HWND is bound.
    // Keep the system rectangle when the optional corner adapter is unavailable.
    if(windowCorners.start)await startupWait(windowCorners.start(),startupAbort.signal);checkStartup();
    nativeAppend=new NativeAppendHost({window,dialog,request,isClosing:()=>quitting,
      activate:createNativeAppendActivation({root,dataDir})});
    if(process.platform==='win32') {
      // Remember the actual external page before our first window takes focus.
      // No screenshot/UIA/model or input action is performed during startup.
      try { await startupWait(companion.capture({intent:tr('initialCapture'),need:{metadata:true,accessibility:false,visual:false,ocr:false}}),startupAbort.signal); }
      catch(error) { fs.appendFileSync(path.join(dataDir,'controller-boot.log'),`Initial readonly context unavailable: ${error.name}\n`); }
    }
    try{supervisorConversation=new SupervisorConversation({dataDir,request,onChange:value=>{
      if(!quitting&&window&&!window.isDestroyed())window.webContents.send('eeas:supervisor-conversation-changed',value);
      publishSupervisorConversation(value);
    }});coordinatorTaskObserver=new CoordinatorTaskObserver({store:supervisorConversation,onError:orbFailure});publishSupervisorConversation();}catch(error){orbFailure(error);}
    checkStartup();registerIpc();await startupWait(window.loadURL(origin+'/'),startupAbort.signal);checkStartup();
    await startIdeCompanion();checkStartup();
    if(!backgroundLaunch&&backgroundPresence.canAutoReveal())revealOwnWindow(window,screen);
    startupLog('APP_READY',{readiness_wait_ms:Date.now()-controllerSpawnedAt,background:backgroundLaunch});
    if(typeof Notification==='function') {
      taskNotifications=createTaskNotifications({Notification,locale:uiLanguage.resolved_locale,journalPath:path.join(dataDir,'task-notifications.json'),
        isActive:()=>!!window&&!window.isDestroyed()&&window.isFocused(),getVisibleTask:()=>visibleTaskKey,
        onOpenTask:selection=>openSupervisedTask(selection.key),onError:orbFailure});
      taskActivity=new TaskActivityHost({request,watchRequest:(endpoint,signal)=>request(endpoint,undefined,signal),notifications:taskNotifications,isClosing:()=>quitting||stopped,
        onError:orbFailure,onSnapshot:(value,{initial})=>{
          try{coordinatorTaskObserver?.observe(value);}catch(error){orbFailure(error);}
          const unread=value.alerts.filter(alert=>!alert.acknowledged);
          const errors=unread.filter(alert=>['EXECUTION_ERROR','TIMED_OUT'].includes(alert.category));
          const waiting=unread.filter(alert=>['WAITING_USER','NEEDS_CHECK','OBSERVATION_UNAVAILABLE'].includes(alert.category));
          const completed=unread.filter(alert=>alert.category==='EXECUTION_RETURNED');
          const fresh=unread.filter(alert=>!taskAlertWindow.has(alert.id)&&['EXECUTION_ERROR','TIMED_OUT','WAITING_USER','NEEDS_CHECK','OBSERVATION_UNAVAILABLE','EXECUTION_RETURNED'].includes(alert.category));
          taskAlertWindow=new Set(value.alerts.map(alert=>alert.id));
          const running=value.tasks.filter(task=>['RUNNING','CANCEL_REQUESTED'].includes(task.classification)).length;
          const attention=value.tasks.filter(task=>['WAITING_USER','NEEDS_CHECK','EXECUTION_ERROR','TIMED_OUT'].includes(task.classification)||task.observation_status!=='CURRENT').length;
          assistantOrb?.setTaskActivity({running:value.settings.enabled?running:0,attention:value.settings.enabled?attention:0,unread:value.settings.enabled?unread.length:0,
            urgency:!value.settings.enabled?'none':errors.length?'error':waiting.length?'waiting':completed.length?'complete':'none',
            ...value.settings.enabled&&!initial&&fresh.length?{notice:fresh.length===1?tr('taskUpdateOne'):tr('taskUpdateMany',{count:fresh.length}),
              eventId:createHash('sha256').update(JSON.stringify(fresh.map(alert=>alert.alert_ref).sort((a,b)=>a.id.localeCompare(b.id)))).digest('hex')}:{}});
          if(!quitting&&window&&!window.isDestroyed())window.webContents.send('eeas:task-activity',supervisedSnapshot(value));
          quickSurface?.updateActivity?.(supervisedSnapshot(value));syncQuickIndicator();
        }});
      taskActivity.start();
    }
    void quickHost?.restoreObservations().catch(orbFailure);
    globalShortcut.register('CommandOrControl+Shift+Space',()=>{
      if(shortcutBusy||quitting)return;shortcutBusy=true;
      void orbAction('ask',{trigger:'CTRL_SHIFT_SPACE'}).catch(orbFailure).finally(()=>{shortcutBusy=false;});
    });
  }).catch(async error=>{if(!startupCancelled)dialog.showErrorBox(tr('startupFailed'),error.message);await quitSafely();});
  app.on('window-all-closed',()=>app.quit());
  app.on('before-quit',event=>{if(!stopped){event.preventDefault();void quitSafely();}});
}
