import WorkflowStudio,{type WorkflowArrival,type WorkflowViewContext} from './WorkflowStudio';
import WorkflowModeSwitch from './WorkflowModeSwitch';
import {stageApi,stageModeKey,type Scope as WorkflowScope,type StageDestination,type StageRun,type StageIndex} from './stage-workflows';
import {flushResourceAllowanceDrafts} from './resource-allowance-journal';
import {flushExperienceDrafts} from './task-experience-journal';
import {flushNativeFeedbackDrafts} from './native-feedback-drafts';
import {flushNativePreviewDrafts} from './native-preview-editor';
import {makeOriginalWorkChoice,originalWorkChoiceForRef,originalWorkChoiceMatches,sameOriginalWorkChoice,originalWorkStatusText,type OriginalWorkChoice,type OriginalWorkInputSource} from './original-work-input';
import {flushWorkMaintenanceDrafts} from './work-maintenance-editor';
import {flushNativeExportJournals} from './native-export-journal';
import {flushWorkDeliveryDrafts} from './work-delivery-editor';
import {flushNativeConflictDrafts} from './native-conflict-editor';
import {flushWorkSourceDrafts} from './work-source-editor';
import {flushWorkRetestDrafts} from './work-retest-editor';
import RetainedConfigurationRecords from './RetainedConfigurationRecords';
import {flushConfigurationDrafts} from './connection-configuration-draft';
import {flushWorkRequirementDrafts} from './work-requirement-editor';
import {flushWorkCaseDrafts} from './work-case-editor';
import {ConfigurationAdoptionClient,type AdoptionView} from './configuration-adoption';
import {OriginalOwnerSelectionClient} from './original-owner-selection';
import {configurationOwner} from './conversation-configuration';
import ContextDialog from './ContextDialog';
import type {ContextSelection,ContextSource,ContextFileTarget} from './context-graph';
import {originalConversationMode,readConversationAgentMode,saveConversationAgentMode} from './conversation-agent-mode';
import DraftRecoveryDialog from './DraftRecoveryDialog';
import {ConversationCreationClient,type CreationReceipt} from './conversation-creation';
import {OriginalConnectionClient,OriginalSessionResumption,originalOwnerCanResume,originalOwnerNeedsRecovery} from './original-connection';
import OriginalOwnerRecovery from './OriginalOwnerRecovery';
import ContextRecovery from './ContextRecovery';
import NativeContextRecovery from './NativeContextRecovery';
import {NativeContextRecoveryClient} from './native-context-recovery';
import {ContextRecoveryClient} from './context-recovery';
import ConversationManager,{type HistorySelectionResult} from './ConversationManager';
import ProviderDialog from './ProviderDialog';
import ConversationOrganizationDialog,{type OrganizationDialogTarget} from './ConversationOrganizationDialog';
import {ConversationOrganizationClient,type OrganizationReceipt,type OrganizationIntent} from './conversation-organization';
import type {ManualReview,ManualCaptureRequest} from './manual-harness';
import {loadSupervisedTaskNavigation,type SupervisedTaskTarget} from './supervised-task-navigation';
import {DiscussionDraftLease,DiscussionDraftMigrationConflict,migrateConversationDraft,discussionDraftKey,seedConversationDraft,readDraftCopies,chooseDraft,removeDraftCopy,type DiscussionDraftKey,type DiscussionDraft,type DraftCopy} from './discussion-drafts';
import ConversationTranscript from './ConversationTranscript';
import {ConversationReading,type LatestMessagePosition,type ConversationReadingScope} from './conversation-reading';
import {nativeInputAttachments,nativeCapturedInput} from './native-collaboration';
import {ConversationThreadClient,threadDraftSources,threadOriginalOwner,verifyThreadOriginalOwner,type ThreadScope,type ThreadMessage,type ThreadHistory} from './conversation-thread';
import {historyDiscoverySource,threadDiscoveryDocument} from './discovery-history-context';
import ConversationSidebar from './ConversationSidebar';
import {parseHistoryPage,mergeHistoryItems,labelHistoryItems,saveConversationLabel,type HistoryItem} from './conversation-history';
import {HistoryDirectoryRefresh} from './history-directory-refresh';
import {selectedSupervisorOperation,type SupervisorOperationSelection} from './supervisor-task-navigation';
import NativeReviewActions from './NativeReviewActions';
import {ordinaryControlScope,ordinaryControlSummaries,dispatchOrdinaryGuidance,controlNotice} from './native-message-guidance';
import AssistantReply from './AssistantReply';
import OriginalLiveMessage from './OriginalLiveMessage';
import {showOriginalLiveMessage} from './original-live-projection';
import {originalFileInput,originalFileChoice,sameOriginalFileInput,sameOriginalFileTarget,type OriginalFileInput} from './original-file-input';
import OriginalPendingControls from './OriginalPendingControls';
import WorkflowProgress from './WorkflowProgress';
import OriginalTaskControls from './OriginalTaskControls';
import {flushOriginalControlDrafts,type ControlAttempt} from './original-pending-controls';
import {assertQuestionRecoveryCurrent,questionRecoveryText,appendQuestionRecovery,type QuestionDraftRecovery} from './original-question-recovery';
import OriginalSessionFiles from './OriginalSessionFiles';
import OriginalCommandResults from './OriginalCommandResults';
import type {OriginalCommandDisplayCoverage} from './original-command-observations';
import {displayedCommandKeys,sameCommandDisplayScope} from './original-command-display';
import {stableItems} from './stable-items';
import {readReviewDraft,saveReviewDraft,reviewInput,sameReview,needsReviewProcess,needsReviewRemote,consentedReviewInput,pageReviewInput,resetReviewConsent,type NativeReviewInput,type NativePageCatalog,type NativePageSelection,type ReviewControl} from './native-review';
import {authoredCopy,authoredErrorCopy,authoredText,type AuthoredUICopy,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText,uiLocale} from './ui-language';
import { batch, createEffect, createMemo, createResource, createSignal, For, lazy, onCleanup, onMount, Show, untrack } from 'solid-js';
import { Dynamic } from 'solid-js/web';
import { Button } from '@opencode-ai/ui/button';
import { Icon } from '@opencode-ai/ui/icon';
import productBrand from './brand';
import brandIcon from '../../../packages/branding/icon.svg';
import brandSignature from './assets/cirven-signature.svg';
import { api, ApiError, operationId } from './api';
import {FolderConnection,type FolderConnectionIntent} from './project-folder';
import type { ArtifactEnvelope, ClaimCoverageDraft, FileContent, Harness, HarnessState, Material, PlanningCandidate, PlanningOperation, PlanningProposal, ProviderProfile, Project, ReportArtifact, SourceRef, TaskBook, TreeEntry, Workspace } from './contracts';
import { ModuleRegistry, NavigationOwner } from './registry';
import { GenericRenderer, registerBuiltins } from './renderers';
import { trapFocus } from './focus';
import { nativeDirectoryPicker, nativeFilePicker } from './native-picker';
import { companionFilesApi, companionRoute, directFileSupported, exactFileSource, readCompanionFileDraft, sameSelectedFile, saveCompanionFileDraft, selectedWriteAllowed, type CompanionFile, type CompanionFileDraft } from './companion-files';
import { nativeFeedbackApi, uncheckedFileFeedback, recheckNativeFileFeedbacks,savedTextareaSelection,readFeedbackDisplays,saveFeedbackDisplay,verifiedFeedbackDisplay,type FeedbackSelection, type NativeFileFeedback } from './native-feedback-api';
import {workbenchApi} from './workbench-api';
import {WRITING_PANEL_BASIS} from './writing-experience';
import type {CaptureInput,CompetitionCapture,FeedbackView,RevisionRef,WordResult,WritingProfile,WritingRule,WritingSource,WritingImportRequest} from './workbench-contracts';
import PaneSeparator from './PaneSeparator';
import {railKey} from './activity-rail';
import LocalExplorer from './LocalExplorer';
import InteractionPanels,{InteractionRestoreMenuItems} from './InteractionPanels';
import {QuickReplyNavigation} from './quick-reply-navigation';
import {startupProject,userProjects} from './startup-project';
import ComposerAddMenu from './ComposerAddMenu';
import NativeAttachmentEditor from './NativeAttachmentEditor';
import {NativeInputPreviews} from './native-input-previews';
import NativeMessageArtifacts from './NativeMessageArtifacts';
import NativeMessageFeedback from './NativeMessageFeedback';
import {nativeReportArtifact,openNativeReportArtifact} from './native-artifacts';
import DesktopTitlebar from './DesktopTitlebar';
import SupervisorCommandCenter from './SupervisorCommandCenter';
import {SupervisorConversationClient,supervisorDraftFailureKind,type SupervisorConversationClientView} from './supervisor-conversation-client';
import {centerText} from './supervisor-command-center-copy';
import type {CenterReadStatus} from './supervisor-command-center';
import './supervisor-titlebar.css';
import WorkspaceToolbar from './WorkspaceToolbar';
import StudioPopover from './StudioPopover';
import UILanguageControl from './UILanguageControl';
import InspectorDocuments from './InspectorDocuments';
import WorkbenchReview from './WorkbenchReview';
import {reviewObjects,type ReviewObject} from './workbench-review';
import {ObjectDraftBoundary} from './ObjectDraft';
import {objectDraft,workspaceStateKey,keyIdentity,workbenchStateApi,artifactStateKey,flushWorkbenchDrafts,type WorkbenchKey,type WorkbenchState} from './workbench-state';
import {QuickInbox,conversationIdFor,sameQuickScope,quickIntentSummary,quickRef,type QuickScope,type QuickRecord,type QuickBridge} from './quick-inbox';
import QuickInboxPanel from './QuickInboxPanel';
import NativeCapabilitiesPanel from './NativeCapabilitiesPanel';
import TaskMethodPicker from './TaskMethodPicker';
import {preserveLayoutReading,workspaceView,WORKSPACE_VIEW_KEY,type WorkspaceView} from './chat-workbench-layout';
import type {MCPToolSelectionDraft} from './native-tools';
import DeliveryArtifactCard from './DeliveryArtifactCard';
import AgentSessionPanel from './AgentSessionPanel';
import {flushDynamicWorkflowDrafts} from './dynamic-workflow';
import GlobalSupervisorPanel from './GlobalSupervisorPanel';
import './task-activity-shell.css';
import './conversation-navigation.css';
import {sameSupervisedTask} from './task-activity';
import {globalSupervisor,supervisorSnapshot,supervisorCounts,supervisorTaskStatus,type SupervisorSnapshot,type SupervisorTask} from './global-supervisor';
import NativeReworkPanel,{NativeFeedbackComposer,type FeedbackComposerAction} from './NativeReworkPanel';
import NativeAppendPanel from './NativeAppendPanel';
import NativeActionProposals from './NativeActionProposals';
import CompanionFileContext from './CompanionFileContext';
import NativePageConversation,{type ComposerAttachment} from './NativePageConversation';
import {BrandLoader} from './BrandLogo';
import TaskStatus from './TaskStatus';
import LiveOperationStatus from './LiveOperationStatus';
import DiscoveryOperationRecovery from './DiscoveryOperationRecovery';
import {operationStatus} from './operation-outcome';
import {discoveryInput,sameDiscoveryInput,discoverySendGate,readDiscoveryPending,saveDiscoveryPending,clearDiscoveryPending,type DiscoveryInput} from './discovery-pending';
import {modelProfileConnectionHint,sortModelProfiles} from './model-catalog';
import {DiscussionFileContext,sameDiscussionScope,sameFileLink,associationScope,fileConversationBasis} from './discussion-file-context';
import type {NativeRef,FileLinkRef,QuickFileContextState,FileAssociationScope} from './native-collaboration';
import { ComposerPresentation, type ComposerView } from './composer-presentation';
import { PinnedSourceObserver, type PinnedObservation } from './pinned-source-observer';
import {nativePreviewTargets,sameNativePreview,readNativePreview,type NativePreviewTarget} from './native-preview-continuity';
import { nativeReworkApi, reworkPreviewQuestion, ReworkPreviewRecovery, type ReworkRecord } from './native-rework';
import {originalMessagesNeedAttention,agentSessionsApi, originalSessionReady, originalSessionCanResumeForSend, originalReadableParts, originalGuidanceText, dispatchOriginalMessage, restoreOriginalOperations, AgentSessionOutcomeError, type AgentSession, type AgentSessionOperation, type OriginalOperationReview, type OriginalAgent} from './agent-sessions';
import { planningDeliveryApi, deliveryComplete, resumableExport, saveDeliveryHistory, discardPreclaimDelivery, type DeliveryReceipt, type DeliveryFile, type DeliveryOperation, type StageOptions, type ExportOptions } from './planning-delivery';
import DeliveryHistoryFeed from './DeliveryHistoryFeed';
import { DeliveryHistoryRecovery, deliveryTarget, loadedDeliveryTargets, selectedDeliverySession, deliveryFeedItem, upsertDeliveryFeed, type DeliveryTarget, type DeliveryFeedItem } from './delivery-recovery';
import {ProjectObjectCatalog,type ProjectCatalogStatus} from './project-object-catalog';
import { NativeCollaborationController, confirmedCollaborationFullscreen, NativeMessageClient, nativeMessageInput, dispatchNativeMessage, NativeRequestIndexError, nativeInspectionFailure, readNativeHistory, saveNativeHistory, restoreNativeHistory, nativeCollaborationBridge, nativeInitialMode, currentNativeReference, nativeContextLabel, nativeReference, sameNativeReference, nativeMessagePending, assertNativeMessage, readNativeDraft, saveNativeDraft, readNativeText, saveNativeText, type NativeFeedbackDisplayIdentity, type NativeMode, type NativeCollaborationState, type NativeContextSnapshot, type NativeDraftAttachment, type NativeMessage, type NativeMessageArtifact, type NativeInputAttachment, type NativeRegion } from './native-collaboration';
import { feedbackReworkApi } from './feedback-rework-api';
import {InteractionState,panelKey} from './interaction-state';
import {presentationFor,type PresentationCause} from './interaction-policy';
import {directApi,type DirectAction} from './direct-actions';
import {projectPaneWidths,savedPaneWidth,READING_AGENT_WIDTH_KEY,type PaneWidthIntent} from './panes';
import {mobileWorkspace,workspacePanels,type WorkspaceLayoutState} from './layout-model';
import type {Annotation,AnnotationSubmission} from './annotations';
import type {TextAnnotation} from './text-feedback';
import type {BitmapSubmission} from './BitmapFeedbackEditor';
import type {NativeFrame} from './native-document';
import {appendFeedbackDraft,ComposerDraftPersistence,hydrateDraftRefs,readComposerDraft,type ComposerDraft,type DraftRestore} from './composer-draft';
import {SelectedSourceWatcher,selectedSources,sourceIdentity,syncSelectedSource,type SourceCheck,type SourceSync} from './source-watch';
import { discoveryApi,isPlanWaitSeconds } from './discovery-api';
import {readDiscoveryWait,saveDiscoveryWait} from './discovery-wait-budget';
import type { DiscoveryCandidate, DiscoveryOperation, DiscoveryPlan, DiscoverySession, EntryMode, Resolution } from './discovery-contracts';

const NativeApp = lazy(() => import('./native'));
const sourceHash = __EEAS_UI_SOURCE_SHA__;
type UICopy=UIErrorCopy;
const uiCopyText=(value:UICopy)=>authoredText(value,uiText);
type FeedItem = { id: string; title: string; body: string; status: string; artifact?: ArtifactEnvelope; deliveryTarget?: DeliveryTarget;deliveryInspection?:DeliveryFeedItem['deliveryInspection'];deliveryHistory?:DeliveryFeedItem['deliveryHistory'];title_copy?:AuthoredUICopy;body_copy?:AuthoredUICopy };

/** Product-authored receipt status only; user messages and raw diagnostics are never filtered. */
export function DeliveryFeedFeedback(props:{item:FeedItem;onInspect:(item:FeedItem)=>Promise<unknown>}){
  const [reading,setReading]=createSignal(false);let disposed=false,trigger:HTMLButtonElement|undefined;onCleanup(()=>{disposed=true;});
  async function inspect(){
    if(reading())return;const clicked=trigger,focused=clicked===document.activeElement,card=clicked?.closest<HTMLElement>('.as-feed-item'),id=props.item.id;
    setReading(true);try{const result=await props.onInspect(props.item) as DeliveryOperation|undefined;
      if(result&&deliveryComplete(result)&&focused)requestAnimationFrame(()=>{
        if(disposed||!card?.isConnected||props.item.id!==id||props.item.deliveryInspection||!clicked||clicked.isConnected||document.activeElement!==document.body)return;
        (card.querySelector<HTMLElement>('.as-chat-delivery button:not(:disabled),.as-chat-delivery a[href]')??card.querySelector<HTMLElement>('.as-feed-title>strong'))?.focus({preventScroll:true});
      });
    }catch{/* The original GET records its scoped unavailable status. */}finally{if(!disposed)setReading(false);}
  }
  return <><p>{props.item.deliveryTarget?authoredText(props.item.body_copy??props.item.body,uiText):props.item.body}</p><Show when={props.item.deliveryInspection}>{info=><>
    <button ref={trigger} type="button" class="as-text-action" aria-disabled={reading()} aria-busy={reading()} onClick={()=>void inspect()}>{uiText(reading()?'核查中…':'核查原操作')}</button>
    <details><summary>{uiText('查看核查详情')}</summary><p>{uiText('未自动重发或重新导出。')}</p><p style={{'overflow-wrap':'anywhere'}}>{uiText('原操作编号：')}{info().operationId}</p><Show when={info().diagnostic}><pre class="as-json" style={{'max-height':'min(240px, 35vh)','overflow':'auto'}}>{info().diagnostic}</pre></Show></details>
  </>}</Show></>;
}

type SupervisedDetail = {key:SupervisorTask['key'];task:SupervisorTask;input_text:string;answer_text:string;artifacts:NativeMessageArtifact[];navigation?:{status:string;reason_code?:string|null};session_ref?:RevisionRef;object_ref?:RevisionRef;continuation?:{kind:'supervisor';operation_id:string;request_sha256:string};text_coverage?:string;text_truncated?:boolean;answer_status?:'AVAILABLE'|'PARTIAL'|'UNAVAILABLE'|'NOT_RETURNED';answer_message?:string};
type DiscussionDestination=SupervisedTaskTarget|{kind:'native';input:string;message:NativeMessage;conversation:{messages:NativeMessage[]};navigation:{project_id:string|null;conversation_id:string;workspace_ref:null}}|{kind:'original';session:AgentSession;navigation:{project_id:string;session_ref:NativeRef;workspace_ref:Pick<Workspace,'id'|'revision'|'binding_sha256'>|null}}|{kind:'discovery';session:DiscoverySession;navigation:{project_id:string;workspace_ref:DiscoverySession['payload']['workspace_ref']}};
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

export default function App() {
  const nativeBridge = nativeCollaborationBridge(globalThis);
  const nativeClient = nativeBridge ? new NativeMessageClient(nativeBridge) : undefined;
  const [windowMode,setWindowMode] = createSignal<NativeMode>(nativeInitialMode(globalThis,window.location.search));
  const [collaborationState,setCollaborationState] = createSignal<NativeCollaborationState>();
  const [companionPinBusy,setCompanionPinBusy] = createSignal(false);
  async function toggleCompanionPin(){
    if(companionPinBusy()||windowMode()!=='companion'||!nativeController)return;
    setCompanionPinBusy(true);
    try{await nativeController.pin(!(collaborationState()?.pinned_preference??collaborationState()?.pinned));}
    catch(reason){setError(authoredErrorCopy(reason));}
    finally{setCompanionPinBusy(false);}
  }
  const [currentNativeContext,setCurrentNativeContext] = createSignal<NativeContextSnapshot>();
  const [sessionManagementOpen,setSessionManagementOpen] = createSignal(false);
  const [nativeContextBusy,setNativeContextBusy] = createSignal(false);
  const [nativeDraftAttachments,setNativeDraftAttachments] = createSignal<NativeDraftAttachment[]>([]);
  const [nativeMessages,setNativeMessages] = createSignal<{input:string;message:NativeMessage;feedback_preview?:true;feedback_display_identity?:NativeFeedbackDisplayIdentity}[]>([]);
  const [feedbackDisplayVersion,setFeedbackDisplayVersion]=createSignal(0);const feedbackDisplays=createMemo(()=>{feedbackDisplayVersion();try{return readFeedbackDisplays(localStorage);}catch{return[];}});
  const nativeMessageRows=stableItems(nativeMessages,row=>JSON.stringify([row.message.project_id??null,row.message.conversation_id??null,row.message.operation_id]));
  const [nativePending,setNativePending] = createSignal<string>();
  const [historyReloadToken,setHistoryReloadToken]=createSignal(0);
  const [selectedDiscussion,setSelectedDiscussion]=createSignal<{key:DiscussionDraftKey;conversationId:string}>();
  const [supervisorReturn,setSupervisorReturn]=createSignal<SupervisedDetail>();
  let discussionDraftLease:DiscussionDraftLease|undefined;
  const threadClient=new ConversationThreadClient(api);
  const [connectionReady,setConnectionReady]=createSignal(false);
  const [threadAccess,setThreadAccess]=createSignal<'ready'|'checking'|'deleted'|'unavailable'>('ready'),[draftOnlyTitle,setDraftOnlyTitle]=createSignal('');
  const [threadScope,setThreadScope]=createSignal<ThreadScope>(),[threadMessages,setThreadMessages]=createSignal<ThreadMessage[]>([]),[threadCursor,setThreadCursor]=createSignal<string|null>(null),[threadLoading,setThreadLoading]=createSignal(false),[threadError,setThreadError]=createSignal('');
  let threadReadGeneration=0,threadReadAbort:AbortController|undefined,composerInteracted=false,threadStartupRestored=false;
  const [threadResultSelection,setThreadResultSelection]=createSignal<{input:string;message:NativeMessage}>();let threadResultDialog:HTMLDialogElement|undefined,threadResultGeneration=0;
  createEffect(()=>{if(threadResultSelection())threadResultDialog?.showModal();else if(threadResultDialog?.open)threadResultDialog.close();});
  function clearThread(){setThreadAccess('ready');setDraftOnlyTitle('');threadReadGeneration++;threadReadAbort?.abort();threadResultGeneration++;setThreadResultSelection(undefined);setThreadScope(undefined);setThreadMessages([]);setThreadCursor(null);setThreadLoading(false);setThreadError('');}
  function threadHasNative(message:NativeMessage){return threadMessages().some(row=>row.source.kind==='native'&&row.operation_id===message.operation_id&&row.source.project_id===(message.project_id??null)&&row.source.conversation_id===message.conversation_id);}
  function threadHasOriginal(id:string){const value=originalSession();return threadMessages().some(row=>row.source.kind==='original'&&row.native_message_id===id&&row.source.project_id===value?.project_id&&row.source.session_id===value?.session_ref.id);}
  async function refreshThread(more=false){
    const previous=threadScope();if(!previous||threadSendingBlocked())return;if(more)conversationReading.pause(previous.conversation_ref.id);
    threadReadAbort?.abort();const abort=new AbortController();threadReadAbort=abort;const generation=++threadReadGeneration,id=previous.conversation_ref.id;setThreadLoading(true);setThreadError('');
    const current=()=>!studioDisposed&&generation===threadReadGeneration&&threadScope()?.conversation_ref.id===id;
    try{const scope=more?previous:await threadClient.scope(id,abort.signal),page=await threadClient.history(scope,more?threadCursor():null,abort.signal);if(!current())return;const readingRevision=conversationReading.stamp(),restore=preserveLayoutReading(shell,undefined,()=>current()&&conversationReading.stamp()===readingRevision&&!conversationReading.following(id));setThreadScope(scope);if(scope.metadata_source&&scope.title){const title=scope.title;setHistoryItems(items=>items.map(item=>item.id===id&&item.title!==title?{...item,title}:item));setHistorySelectedItem(item=>item?.id===id?{...item,title}:item);}setThreadCursor(page.nextCursor);setThreadMessages(old=>{const byId=new Map(old.map(row=>[row.id,row]));return (more?[...page.messages,...old]:page.messages).filter((row,index,list)=>list.findIndex(item=>item.id===row.id)===index).map(row=>{const prior=byId.get(row.id);return prior&&sameReview(prior,row)?prior:row;});});restore();conversationReading.update();}
    catch(reason){if(current()&&!abort.signal.aborted)setThreadError(uiLocale()==='en'?'This conversation could not be read. Your draft is kept.':'会话暂时无法读取，草稿保留。');}
    finally{if(current())setThreadLoading(false);}
  }
  async function threadUserText(row:ThreadMessage){
    const unavailable=uiLocale()==='en'?'The original feedback cannot be verified yet.':'这条反馈的原文暂时无法核对。';
    if(!row.native_feedback_ref)return row.text;
    const display=feedbackDisplays().find(item=>item.operation_id===row.operation_id&&item.project_id===row.source.project_id&&item.conversation_id===row.source.conversation_id&&sameReview(item.feedback_ref,row.native_feedback_ref)&&sameReview(item.source_ref,row.file_source_ref));
    return display&&row.operation_id?(await verifiedFeedbackDisplay(display,row.text,{project_id:row.source.project_id??undefined,conversation_id:row.source.conversation_id,operation_id:row.operation_id})??unavailable):unavailable;
  }
  function threadInput(item:NativeInputAttachment){void openInputEditor({context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,...(item.kind==='SCREENSHOT'?{preview_sha256:item.image_sha256}:{})},true);}
  async function threadResult(row:ThreadMessage){
    const id=threadScope()?.conversation_ref.id,generation=++threadResultGeneration;if(!id||row.source.kind!=='native'||!row.operation_id)return;if(!nativeBridge?.inspectNativeMessage){setError(uiLocale()==='en'?'The desktop result reader is not connected. The saved result is kept.':'原生结果接口尚未连接，原记录保留。');return;}
    try{const message=await nativeBridge.inspectNativeMessage(row.operation_id);if(threadScope()?.conversation_ref.id!==id||generation!==threadResultGeneration)return;if(message.operation_id!==row.operation_id||message.project_id!==row.source.project_id||message.conversation_id!==row.source.conversation_id||message.request_sha256!==row.request_sha256)throw new Error('HISTORY_REFERENCE_CHANGED');const input=threadMessages().find(item=>item.role==='user'&&item.operation_id===row.operation_id&&item.source.alias_id===row.source.alias_id)?.text??'';setThreadResultSelection({input,message});}
    catch(reason){setError(authoredErrorCopy(reason));}
  }
  function threadLiveText(row:ThreadMessage){
    const value=originalSession();if(row.source.kind!=='original'||row.role!=='assistant'||row.source.project_id!==value?.project_id||row.source.session_id!==value?.session_ref.id||!row.native_message_id)return row.text;
    const live=value.messages.find(message=>message.info.id===row.native_message_id);return live?live.parts.filter(part=>part.type==='text'&&typeof part.text==='string').map(part=>part.text).join('\n'):row.text;
  }
  function selectedThreadId(){return threadScope()?.conversation_ref.id??(selectedDiscussion()?.key.kind==='conversation'?selectedDiscussion()!.key.id:undefined);}
  function savedThreadRow(scope:ThreadScope,id:string):HistoryItem{
    const known=historyItems().find(item=>item.id===id);
    if(scope.metadata_source){const root=scope.sources.find(source=>source.id===id);if(root?.identity.kind!=='native'||!root.identity.conversation_id)throw Error('CONVERSATION_THREAD_UNAVAILABLE');return {id,kind:'native',source:scope.metadata_source,projectId:scope.project_id,projectName:projects().find(item=>item.id===scope.project_id)?.name,title:scope.title||known?.title||(uiLocale()==='en'?'Untitled chat':'未命名对话'),updatedAt:known?.updatedAt??null,readOnly:false,organization:{revision:scope.conversation_ref.revision,archived:scope.archived},conversationRef:scope.conversation_ref,conversationOrigin:{kind:'native',project_id:root.identity.project_id,conversation_id:root.identity.conversation_id}};}
    const origin=scope.sources.find(source=>source.original_source?.kind==='original'&&source.identity.project_id===scope.project_id)??scope.sources.find(source=>source.original_source!==null);if(!origin?.original_source)throw new Error('CONVERSATION_THREAD_UNAVAILABLE');
    return known??{id,kind:origin.original_source.kind,source:origin.original_source,projectId:scope.project_id,title:uiLocale()==='en'?'Saved conversation':'已存会话',updatedAt:null,readOnly:true,conversationRef:scope.conversation_ref};
  }
  async function openSavedThread(id:string,current:()=>boolean,signal?:AbortSignal,minimumRevision=0,destination?:StageDestination){
    const scope=await threadClient.scope(id,signal);if(!current())return;if(scope.conversation_ref.revision<minimumRevision)throw Error('ORGANIZATION_REFRESH_REQUIRED');
    if(destination&&(scope.project_id!==destination.project||scope.conversation_ref.id!==id))throw Error('WORKFLOW_DESTINATION_UNAVAILABLE');
    const page=await threadClient.history(scope,null,signal);if(!current())return;
    const row=savedThreadRow(scope,id);
    if(row.source.kind==='discovery'&&historyDiscoverySource(row,scope,new DiscussionDraftLease(localStorage,{kind:'conversation',projectId:null,id:row.id}).read(),page)){
      // An exact relationship can point beyond the currently loaded history
      // page. Its verified scope/source supplies this one navigation row.
      if(!historyItems().some(item=>item.id===row.id&&sameReview(item.source,row.source)))setHistoryItems(items=>[row,...items.filter(item=>item.id!==row.id)].slice(0,2000));
      return openLegacyHistoryConversation(row);
    }
    return await installThread(row,scope,page,current,signal,[],undefined,undefined,destination)===true&&current()?'OPENED':'DEFERRED';
  }
  async function installDraftOnly(id:string,access:'deleted'|'unavailable',current:()=>boolean,title?:string){
    const key:DiscussionDraftKey={kind:'conversation',projectId:null,id};if(localStorage.getItem(discussionDraftKey(key))===null)throw Error('DISCUSSION_DRAFT_UNAVAILABLE');
    const lease=new DiscussionDraftLease(localStorage,key),draft=lease.read(),label=title??(organizationClient.pending?.conversationId===id?organizationClient.pending.title:undefined)??historyItems().find(item=>item.id===id)?.title??(uiLocale()==='en'?'Saved conversation':'已存会话');saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();if(!current())return;saveComposerDraft();clearThread();draftPersistence.pause();nativeDraftOwner=undefined;stopObserver();
    batch(()=>{setSelectedDiscussion(undefined);setActiveSession(undefined);selectOriginalSession(undefined);setActiveOperation(undefined);setProject(undefined);setWorkspace(undefined);setWorkspaces([]);clearProjectFeed();setNativeMessages([]);setDraftAttachments([]);setFeedbackRecords([]);setInspector(undefined);setDirectFile(undefined);setDirectResults([]);discussionDraftLease=lease;setSelectedDiscussion({key,conversationId:''});setDraftOnlyTitle(label);setSupervisorReturn(undefined);setHistoryActiveId(id);setHistorySelectedItem(undefined);setPrompt(draft.text);setNativeDraftAttachments(draft.attachments);setToolMode('direct');setThreadAccess(access);setError('');});showAgentChat();presentWorkspace('chat');if(compact())setSidebarOpen(false);
  }
  async function restoreSelectedThread(){
    let id:string|undefined,deleted=false,storedTitle:string|undefined;try{const raw=localStorage.getItem('eeas-current-thread-v1');if(!raw){restoreFreeDraft();return;}if(raw.length>1024)throw new Error('CONVERSATION_THREAD_UNAVAILABLE');const value=JSON.parse(raw);if(value.version!==1||typeof value.id!=='string'||!/^(native|original|discovery):[a-f0-9]{64}$/.test(value.id))throw new Error('CONVERSATION_THREAD_UNAVAILABLE');id=value.id;if(value.deleted===true){if(typeof value.title!=='string'||value.title.length>500)throw Error('CONVERSATION_THREAD_UNAVAILABLE');deleted=true;storedTitle=value.title;}}catch{setError(uiLocale()==='en'?'The saved conversation selection could not be read. Your drafts are kept.':'已保存的会话选择暂时无法读取，原草稿保留。');return;}
    if(!id||composerInteracted)return;
    const owner=navigation.begin(),request=++activityRequest,current=()=>!studioDisposed&&owner.current()&&request===activityRequest&&!composerInteracted;
    try{if(deleted)await installDraftOnly(id,'deleted',current,storedTitle);else await openSavedThread(id,current,owner.signal);}
    catch(reason){if(current()){try{await installDraftOnly(id,'unavailable',current);}catch{if(current())setError(uiLocale()==='en'?'The last conversation is unavailable. Your saved drafts are kept.':'上次会话暂时无法打开，已保存草稿保留。');}}}
  }
  createEffect(()=>{if(!connectionReady()||!historyLoaded()||threadStartupRestored)return;threadStartupRestored=true;void restoreSelectedThread();});
  onCleanup(()=>{threadReadGeneration++;threadResultGeneration++;threadReadAbort?.abort();});
  const [historyItems,setHistoryItems]=createSignal<HistoryItem[]>([]),[historyLoaded,setHistoryLoaded]=createSignal(false),[historyLoading,setHistoryLoading]=createSignal(false),[historyLoadingMore,setHistoryLoadingMore]=createSignal(false),[historyError,setHistoryError]=createSignal(''),[historyPartial,setHistoryPartial]=createSignal(false),[historyCursor,setHistoryCursor]=createSignal<string|null>(null),[historyActiveId,setHistoryActiveId]=createSignal<string>(),[historySelectedItem,setHistorySelectedItem]=createSignal<HistoryItem>(),[leftTab,setLeftTab]=createSignal<'conversations'|'files'>('conversations');
  const [organizationTarget,setOrganizationTarget]=createSignal<OrganizationDialogTarget>();
  function readContextChoices():Record<string,ContextSelection>{try{const value=JSON.parse(localStorage.getItem('eeas.context-choices.v1')??'{}');if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length>64)return {};const valid:Record<string,ContextSelection>={};for(const [key,entry]of Object.entries(value)){const item=entry as ContextSelection;if(item&&/^[a-f0-9]{64}$/.test(item.selection_id)&&key===JSON.stringify([item.project_id,item.conversation_id])&&Number.isSafeInteger(item.characters)&&item.characters>=0&&item.characters<=16000)valid[key]=item;}return valid;}catch{return {};}}
  const [contextOpen,setContextOpen]=createSignal(false),[contextChoices,setContextChoices]=createSignal<Record<string,ContextSelection>>(readContextChoices()),[contextFileTarget,setContextFileTarget]=createSignal<{target:ContextFileTarget;label:string}>();let contextReturn:HTMLElement|undefined;
  function persistContextChoices(value:Record<string,ContextSelection>){const entries=Object.entries(value);if(entries.length>64)throw Error('Context choice capacity reached');const compact=Object.fromEntries(entries.map(([key,item])=>[key,{...item,selected:[],omitted:[]}]));localStorage.setItem('eeas.context-choices.v1',JSON.stringify(compact));return value;}
  function clearContextChoice(){const next={...contextChoices()};delete next[contextChoiceKey()];setContextChoices(next);try{persistContextChoices(next);}catch{setError(uiLocale()==='en'?'The current context choice was removed, but could not be saved locally. Check it after reopening.':'当前引用已取消，但本机保存失败，重开后请核对。');}}


  const contextChoiceKey=()=>JSON.stringify([project()?.id??null,threadScope()?.conversation_ref.id??null]);
  const contextChoice=()=>contextChoices()[contextChoiceKey()];
  function showContext(target:HTMLElement){contextReturn=target;setContextFileTarget(undefined);setContextOpen(true);}
  function showFileContext(source:SourceRef,target:HTMLElement){contextReturn=target;setContextFileTarget({target:{kind:'WORKSPACE_FILE',source_ref:source},label:source.path});setContextOpen(true);}
  function showSelectedFileContext(target:HTMLElement){const linked=linkedFile(),selected=directFile();contextReturn=target;if(linked)setContextFileTarget({target:{kind:'LINKED_FILE',link_ref:linked.fileLinkRef},label:linked.filename??(uiLocale()==='en'?'Linked file':'关联文件')});else if(selected)setContextFileTarget({target:{kind:'WORKSPACE_FILE',source_ref:selected.file.source_ref},label:selected.file.source_ref.path});else return;setContextOpen(true);}
  async function openContextOriginal(source:ContextSource){
    if(!nativeBridge?.openContextFile)throw Error('DESKTOP_CONTEXT_OPEN_UNAVAILABLE');
    const current=navigation.capture(),scope=associationScope(quickScope());
    const result=await nativeBridge.openContextFile(source,scope) as {status?:string;context_source_ref?:ContextSource;context_file_key?:string};
    if(result.status!=='OPEN_REQUESTED'||result.context_file_key!==source.file_identity_sha256||!sameReview(result.context_source_ref,source))throw Error('ORIGINAL_FILE_OPEN_UNCONFIRMED');
    if(current()&&sameDiscussionScope(scope,quickScope())){setContextOpen(false);setNotice(uiLocale()==='en'?'The system received the original-file open request.':'系统已接收原文件打开请求。');}
  }
  async function openContextDiscussion(reference:{id:string;revision:number}){
    if(busy())throw Error('CURRENT_INPUT_BUSY');
    const owner=navigation.begin(),request=++activityRequest,current=()=>!studioDisposed&&owner.current()&&request===activityRequest;
    const outcome=await openSavedThread(reference.id,current,owner.signal,reference.revision);
    if(outcome!=='OPENED'||threadScope()?.conversation_ref.id!==reference.id&&historyActiveId()!==reference.id)throw Error('CONVERSATION_OPEN_UNCONFIRMED');
    setContextOpen(false);
  }
  function selectContext(value:ContextSelection){const expectedProject=project()?.id??null,expectedConversation=threadScope()?.conversation_ref.id??null;if(value.project_id!==expectedProject||value.conversation_id!==expectedConversation)return;setContextChoices(old=>persistContextChoices({...old,[contextChoiceKey()]:value}));}
  const [conversationManagerOpen,setConversationManagerOpen]=createSignal(false),[organizationVersion,setOrganizationVersion]=createSignal(0),[organizationRefresh,setOrganizationRefresh]=createSignal(0),[organizationNotice,setOrganizationNotice]=createSignal<{title:string;receipt:OrganizationReceipt;item?:HistoryItem}>();
  let conversationManagerReturn:HTMLElement|undefined;
  const organizationClient=new ConversationOrganizationClient(api,localStorage,()=>setOrganizationVersion(value=>value+1));
  const [creationVersion,setCreationVersion]=createSignal(0),[creationNotice,setCreationNotice]=createSignal('');
  const creationClient=new ConversationCreationClient(api,localStorage,()=>setCreationVersion(v=>v+1));
  const [connectionVersion,setConnectionVersion]=createSignal(0),[connectionNoticeState,setConnectionNoticeState]=createSignal<{scope:string;text:string}>();
  function connectionNoticeScope(){return JSON.stringify([selectedThreadId()??null,project()?.id??null,originalSession()?.session_ref.id??null]);}
  function connectionNotice(){const value=connectionNoticeState();return value?.scope===connectionNoticeScope()?value.text:'';}
  function setConnectionNotice(text:string){setConnectionNoticeState({scope:connectionNoticeScope(),text});}
  const configurationAdoption=new ConfigurationAdoptionClient(agentSessionsApi,localStorage,()=>setConnectionVersion(v=>v+1));
  const [configurationAdoptionView,setConfigurationAdoptionView]=createSignal<{basis:string;view:AdoptionView}>(),[configurationAdoptionPreparing,setConfigurationAdoptionPreparing]=createSignal(false);
  const ownerSelection=new OriginalOwnerSelectionClient(agentSessionsApi,localStorage,()=>setConnectionVersion(v=>v+1));
  const originalConnection=new OriginalConnectionClient(agentSessionsApi,localStorage,()=>setConnectionVersion(v=>v+1));
  const originalResumption=new OriginalSessionResumption(agentSessionsApi,localStorage,()=>setConnectionVersion(v=>v+1));
  const [sourceRecoveryVersion,setSourceRecoveryVersion]=createSignal(0),[sourceRecoveryError,setSourceRecoveryError]=createSignal<{scope:string;text:string;ready:boolean;retryStop?:boolean}>();
  const sourceRecoveryClient=new ContextRecoveryClient(api,localStorage,()=>setSourceRecoveryVersion(v=>v+1));
  const [nativeRecoveryVersion,setNativeRecoveryVersion]=createSignal(0);
  const nativeRecoveryClient=new NativeContextRecoveryClient(nativeBridge??{},localStorage,()=>setNativeRecoveryVersion(v=>v+1));
  if(typeof window!=='undefined'){const changed=(event:StorageEvent)=>{if(event.key?.startsWith('cirven-native-context-recovery-v1:'))setNativeRecoveryVersion(v=>v+1);};window.addEventListener('storage',changed);onCleanup(()=>window.removeEventListener('storage',changed));}
  if(typeof window!=='undefined'){const changed=(event:StorageEvent)=>{if(event.key?.startsWith('eeas-source-context-recovery-v1:'))setSourceRecoveryVersion(v=>v+1);};window.addEventListener('storage',changed);onCleanup(()=>window.removeEventListener('storage',changed));}
  const [ownerRecoveryError,setOwnerRecoveryError]=createSignal<{scope:string;text:string}>();
  const [draftRecovery,setDraftRecovery]=createSignal<{scope:DiscussionDraftKey;lease:DiscussionDraftLease;local?:DiscussionDraft;restoring?:boolean;saved:DiscussionDraft;after?:(draft:DiscussionDraft)=>Promise<void>;resolveMigration?:(choice:'local'|'saved')=>Promise<void>}>(),[draftRecoveryError,setDraftRecoveryError]=createSignal(''),[draftRecoveryBusy,setDraftRecoveryBusy]=createSignal(false),[draftVersion,setDraftVersion]=createSignal(0);
  function originalWorkInputChoice():OriginalWorkChoice|undefined{draftVersion();const selected=selectedDiscussion();if(!selected||!discussionDraftLease||discussionDraftLease.key!==discussionDraftKey(selected.key))return undefined;return discussionDraftLease.read().original_work_choice;}
  function currentDiscussionDraft():DiscussionDraft{const choice=originalWorkInputChoice();return {text:prompt(),attachments:nativeDraftAttachments(),...(choice?{original_work_choice:choice}:{})};}
  function restoreOriginalWorkInput(session:AgentSession,reference:unknown){const choice=originalWorkChoiceForRef(session,reference);if(!discussionDraftLease){if(choice)throw Error('DISCUSSION_DRAFT_UNAVAILABLE');return;}const next=discussionDraftLease.read();if(choice)next.original_work_choice=choice;else delete next.original_work_choice;discussionDraftLease.save(next);setDraftVersion(v=>v+1);}
  let workInputSelectionRequest=0;
  function clearOriginalWorkInput(){workInputSelectionRequest++;try{saveComposerDraft();const lease=discussionDraftLease;if(!lease)return;const next=lease.read();delete next.original_work_choice;lease.save(next);setDraftVersion(v=>v+1);focusComposer();}catch(reason){setError(authoredErrorCopy(reason));}}
  async function chooseOriginalWorkInput(source:OriginalWorkInputSource,sourceCurrent:()=>boolean){
    const request=++workInputSelectionRequest,session=originalSession();if(!session||!sourceCurrent())return;
    const choice=await makeOriginalWorkChoice(session,source);if(request!==workInputSelectionRequest||!sourceCurrent()||!originalWorkChoiceMatches(choice,originalSession()))return;
    if(!await useOriginalSession(session,originalAgent())||request!==workInputSelectionRequest)return;
    if(!originalWorkChoiceMatches(choice,originalSession())||!selectedDiscussion()||!discussionDraftLease)throw Error('ORIGINAL_WORK_INPUT_CHANGED');
    saveComposerDraft();const lease=discussionDraftLease,before=lease.read(),next={...before,original_work_choice:choice};
    if(before.original_work_choice&&(before.text||before.attachments.length)&&!sameReview([before.original_work_choice.owner,before.original_work_choice.reference],[choice.owner,choice.reference]))chooseDraft(localStorage,lease,next,'local');else lease.save(next);
    setDraftVersion(v=>v+1);chooseTool('original',false);showAgentChat();focusComposer();
  }
  function savedCopies(){draftVersion();selectedDiscussion();const lease=discussionDraftLease;if(!lease)return [];try{return readDraftCopies(localStorage,lease);}catch{return [];}}
  function recoverDraft(scope:DiscussionDraftKey,local:DiscussionDraft,after?:(draft:DiscussionDraft)=>Promise<void>){const lease=new DiscussionDraftLease(localStorage,scope);setDraftRecoveryError('');setDraftRecovery({scope,lease,local:structuredClone(local),saved:lease.read(),after});}
  async function selectDraftChoice(choice:'local'|'saved'){
    const target=draftRecovery();if(!target?.local||draftRecoveryBusy())return;setDraftRecoveryBusy(true);setDraftRecoveryError('');
    try{
      if(target.resolveMigration){
        await target.resolveMigration(choice);
        setDraftVersion(v=>v+1);
        if(draftRecovery()===target)setDraftRecovery(undefined);
        return;
      }
      const draft=chooseDraft(localStorage,target.lease,target.local,choice);setDraftVersion(v=>v+1);
      if(target.after){await target.after(draft);if(draftRecovery()===target)setDraftRecovery(undefined);}
      else if(sameReview(selectedDiscussion()?.key,target.scope)){discussionDraftLease=target.lease;batch(()=>{setPrompt(draft.text);setNativeDraftAttachments(draft.attachments);});setDraftRecovery(undefined);}
    }catch{setDraftRecoveryError(uiLocale()==='en'?'The draft could not be saved, or another window changed it. Both inputs are kept. Free space or reopen this recovery view before choosing again.':'草稿暂时无法保存，或另一窗口又修改了它。两份输入均保留；请检查空间或重新打开恢复窗口后再选择。');}finally{setDraftRecoveryBusy(false);}
  }
  function showDraftCopies(){try{saveComposerDraft();}catch{return;}const selected=selectedDiscussion();if(!selected||!discussionDraftLease)return;setDraftRecoveryError('');setDraftRecovery({scope:selected.key,lease:discussionDraftLease,saved:discussionDraftLease.read()});}
  function restoreDraftCopy(draft:DiscussionDraft){const target=draftRecovery();if(!target||target.resolveMigration)return;try{saveComposerDraft();}catch{return;}recoverDraft(target.scope,draft,target.after);setDraftRecovery(value=>value?{...value,restoring:true}:value);}
  function deleteDraftCopy(id:string){const target=draftRecovery();if(!target)return;try{removeDraftCopy(localStorage,target.lease,id);setDraftVersion(v=>v+1);setDraftRecovery({...target});}catch{setDraftRecoveryError(uiLocale()==='en'?'The copies could not be changed. All saved inputs are kept.':'副本暂时无法修改，已保存的输入均保留。');}}
  function draftRecoveryCopies():DraftCopy[]{draftVersion();const target=draftRecovery();if(!target)return [];try{return readDraftCopies(localStorage,target.lease);}catch{return [];}}

  const organizationBlocked=()=>{organizationVersion();return organizationClient.busy||!!organizationClient.pending||organizationClient.error==='ORGANIZATION_STORAGE_UNAVAILABLE';};
  function showConversationManager(returnTarget?:HTMLElement){conversationManagerReturn=returnTarget;organizationClient.reload();setConversationManagerOpen(true);setSidebarOpen(false);setHistoryPopoverOpen(false);}
  function restoreFreeDraft(){
    const raw=localStorage.getItem('eeas-current-free-draft-v1');if(!raw)return;
    if(raw.length>1024)throw Error('DISCUSSION_DRAFT_UNAVAILABLE');const record=JSON.parse(raw);
    if(record.version!==1||record.scope?.kind!=='native'||record.conversationId!==record.scope.id)throw Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
    if(record.scope.projectId!==(project()?.id??null))return;const lease=new DiscussionDraftLease(localStorage,record.scope);if(lease.key!==discussionDraftKey(record.scope))throw Error('DISCUSSION_DRAFT_CONFLICT');
    const draft=lease.read();draftPersistence.pause();nativeDraftOwner=undefined;discussionDraftLease=lease;batch(()=>{setSelectedDiscussion({key:record.scope,conversationId:record.conversationId});setPrompt(draft.text);setNativeDraftAttachments(draft.attachments);});
  }
  function prepareFreeDraft():DiscussionDraftKey|undefined{
    if(threadScope()||selectedDiscussion()?.key.kind==='conversation'||activeSession()||originalSession()||hasConversationContent()||directFile()||linkedFile()||draftAttachments().length)return undefined;
    const selected=selectedDiscussion();if(selected&&selected.key.kind!=='native')return undefined;
    const cid=conversationId();if(!cid)throw Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
    const key:DiscussionDraftKey=selected?.key??{kind:'native',projectId:project()?.id??null,id:cid};
    const lease=selected?discussionDraftLease:new DiscussionDraftLease(localStorage,key);if(!lease||lease.key!==discussionDraftKey(key))throw Error('DISCUSSION_DRAFT_CONFLICT');
    const value=currentDiscussionDraft(),saved=lease.read();
    if(!selected&&(saved.text||saved.attachments.length)&&!sameReview(saved,value)){recoverDraft(key,value);throw Error('DISCUSSION_DRAFT_CONFLICT');}
    lease.save(value);const pointer=JSON.stringify({version:1,scope:key,conversationId:cid});localStorage.setItem('eeas-current-free-draft-v1',pointer);if(localStorage.getItem('eeas-current-free-draft-v1')!==pointer)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
    draftPersistence.pause();nativeDraftOwner=undefined;discussionDraftLease=lease;setSelectedDiscussion({key,conversationId:cid});return key;
  }
  function newChatBlocked(){creationVersion();return creationClient.busy||!!creationClient.pending||creationClient.error==='CREATION_STORAGE_UNAVAILABLE';}
  function creationAffectsCurrent(){creationVersion();const p=creationClient.pending;return !!p&&(!!p.seedOwner&&sameReview(p.seedOwner,selectedDiscussion()?.key)||p.receipt?.conversation_ref.id===selectedThreadId());}
  async function newConversation(projectId:string|null){
    if(newChatBlocked()){setCreationNotice(uiLocale()==='en'?'Check the pending new chat before creating another.':'请先核查待确认的新会话，再继续新建。');return;}
    const owner=navigation.begin(),request=++activityRequest,current=()=>!studioDisposed&&owner.current()&&request===activityRequest;
    try{saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();if(!current())return;saveComposerDraft();const seedOwner=prepareFreeDraft();
      setCreationNotice('');setHistoryPopoverOpen(false);if(compact())setSidebarOpen(false);
      const receipt=await creationClient.create(projectId,seedOwner);void refreshHistory(false,true);if(current())await adoptCreatedConversation(receipt,undefined,current);
    }catch{if(current())setCreationNotice(uiLocale()==='en'?'Your input is kept. Check the new-chat result before trying again.':'当前输入已保留，请核查新会话结果后再继续。');}
  }
  async function adoptCreatedConversation(receipt:CreationReceipt,choice?:{chosen:DiscussionDraft;before:DiscussionDraft},owned?:()=>boolean):Promise<HistorySelectionResult>{
    const p=creationClient.pending;if(!p||p.request.operation_id!==receipt.operation_id)throw Error('CREATION_CHECK_REQUIRED');
    const owner=owned?undefined:navigation.begin(),request=owned?activityRequest:++activityRequest,current=owned??(()=>!studioDisposed&&!!owner?.current()&&request===activityRequest);
    const scope=await threadClient.scope(receipt.conversation_ref.id);if(!current())return 'DEFERRED';
    const page=await threadClient.history(scope);if(!current())return 'DEFERRED';
    const marker='eeas-conversation-created-adoption-v1:'+receipt.operation_id;
    let transferred=false;try{const saved=JSON.parse(localStorage.getItem(marker)??'null'),pointer=JSON.parse(localStorage.getItem('eeas-current-thread-v1')??'null');transferred=saved?.version===1&&saved.id===scope.conversation_ref.id||pointer?.id===scope.conversation_ref.id&&pointer.creation_operation_id===receipt.operation_id;}catch{throw Error('CREATION_STORAGE_UNAVAILABLE');}
    if(!transferred&&p.seedOwner&&scope.project_id!==p.request.project_id&&!choice)throw Error('CREATION_TARGET_CHANGED');
    const seed=transferred?undefined:choice?()=>{if(!sameReview(choice.before,currentDiscussionDraft()))throw Error('DISCUSSION_DRAFT_CONFLICT');return choice.chosen;}:p.seedOwner?()=>new DiscussionDraftLease(localStorage,p.seedOwner!).read():undefined;
    const installed=await installThread(savedThreadRow(scope,scope.conversation_ref.id),scope,page,current,undefined,[],{operationId:receipt.operation_id,seed});
    if(!installed||!current())return 'DEFERRED';
    try{await creationClient.acknowledge(receipt.operation_id,scope.conversation_ref.id);localStorage.removeItem(marker);if(!current())return 'DEFERRED';localStorage.removeItem('eeas-current-free-draft-v1');setCreationNotice('');}
    catch{if(!current())return 'DEFERRED';setCreationNotice(uiLocale()==='en'?'The new chat is open. Its recovery record still needs to be cleared.':'新会话已打开，恢复记录仍需核查清理。');}
    if(!current())return 'DEFERRED';
    void refreshHistory(false,true);focusComposer();return 'OPENED';
  }
  async function inspectCreation(retry=false){try{const receipt=retry?await creationClient.retry():await creationClient.inspect();await adoptCreatedConversation(receipt);}catch(error){setCreationNotice(error instanceof Error&&error.message==='CREATION_TARGET_CHANGED'?(uiLocale()==='en'?'This new chat was moved to another project. Your current input is kept; open it from the left after checking its project.':'新会话已被移到另一项目，当前输入保留；请核对左侧归属后再打开。'):(uiLocale()==='en'?'The new chat could not be opened yet. Your current input is kept.':'新会话暂时无法打开，当前输入保留。'));}}
  async function leaveCreatedChat(){const p=creationClient.pending;if(p?.phase!=='CONFIRMED'||!p.receipt)return;try{saveComposerDraft();await creationClient.acknowledge(p.request.operation_id,p.receipt.conversation_ref.id);setCreationNotice('');void refreshHistory(false,true);}catch{setCreationNotice(uiLocale()==='en'?'Keep your input here; the new-chat recovery record still needs checking.':'当前输入保留，新会话恢复记录仍需核查。');}}
  function creationStatus(){creationVersion();const p=creationClient.pending,en=uiLocale()==='en',error=creationClient.error;if(!p&&!error&&!creationNotice())return undefined;
    return <div class="as-conversation-organization-status" role={error?'alert':'status'} data-chat-creation><span>{creationNotice()||(creationClient.busy?(en?'Creating a new chat…':'正在新建会话…'):p?.phase==='CONFIRMED'?(en?'Your new chat is ready.':'新会话已创建。'):p?(en?'Check the new-chat result. Your draft is kept.':'新会话结果待核查，草稿保留。'):(en?'New-chat recovery could not be read. Your input is kept.':'暂未读到新会话恢复记录，当前输入保留。'))}</span><Show when={p}><button type="button" disabled={creationClient.busy} onClick={()=>void inspectCreation()}>{p?.phase==='CONFIRMED'?(en?'Open new chat':'打开新会话'):(en?'Check result':'核查结果')}</button><Show when={p?.phase==='NOT_FOUND'}><button type="button" disabled={creationClient.busy} onClick={()=>void inspectCreation(true)}>{en?'Retry new chat':'重试新建'}</button></Show><Show when={p?.phase==='CONFIRMED'}><button type="button" disabled={creationClient.busy} onClick={()=>void leaveCreatedChat()}>{en?'Stay in this chat':'留在当前会话'}</button></Show></Show><Show when={!p}><button type="button" onClick={()=>{creationClient.reload();setCreationNotice('');}}>{en?'Check again':'重新核查'}</button></Show></div>;
  }
  const refreshConversationJournals=(event:StorageEvent)=>{if(event.key==='eeas-conversation-creation-v1')creationClient.reload();if(event.key?.startsWith('eeas-original-connection-v1:')||event.key?.startsWith('eeas-original-resume-v1:'))setConnectionVersion(v=>v+1);if(event.key?.startsWith('eeas-discussion-draft-copies-v1:'))setDraftVersion(v=>v+1);};
  window.addEventListener('storage',refreshConversationJournals);onCleanup(()=>window.removeEventListener('storage',refreshConversationJournals));
  function threadOrganizationPending(){organizationVersion();return !!selectedThreadId()&&organizationClient.pending?.conversationId===selectedThreadId();}
  function threadSendingBlocked(){return creationAffectsCurrent()||connectionAffectsCurrent()||!!selectedThreadId()&&(!threadScope()||threadAccess()!=='ready'||threadOrganizationPending());}
  function threadWriteAllowed(){
    if(connectionAffectsCurrent()){setConnectionNotice(uiLocale()==='en'?'Check the connection result before sending. Your input is kept.':'请先核查连接结果再发送，当前输入保留。');return false;}
    if(creationAffectsCurrent()){setCreationNotice(uiLocale()==='en'?'Check the new-chat result first. Your input is kept.':'请先核查新会话结果，当前输入保留。');return false;}
    if(!threadSendingBlocked())return true;
    setError(uiLocale()==='en'?threadAccess()==='deleted'?'This chat was deleted. Your draft is kept; select another chat to continue.':'The conversation change needs to be checked first. Your draft is kept.':threadAccess()==='deleted'?'此会话已删除，草稿保留；请选择其他会话继续。':'请先核对会话整理结果，当前草稿保留。');return false;
  }
  async function syncOrganizedThread(receipt?:OrganizationReceipt){
    const id=selectedThreadId();if(!id||receipt&&receipt.conversation_id!==id||threadAccess()==='deleted'&&receipt?.action!=='delete')return;
    const owned=navigation.capture(),generation=++threadReadGeneration;threadReadAbort?.abort();const abort=new AbortController();threadReadAbort=abort;
    const current=()=>!studioDisposed&&!abort.signal.aborted&&owned()&&generation===threadReadGeneration&&selectedThreadId()===id;
    if(receipt?.action==='delete'){
      setThreadAccess('deleted');threadResultGeneration++;setThreadResultSelection(undefined);
      try{const pointer=JSON.stringify({version:1,id,deleted:true,title:(historySelectedItem()?.title??(draftOnlyTitle()||undefined))??organizationNotice()?.title??''});localStorage.setItem('eeas-current-thread-v1',pointer);if(localStorage.getItem('eeas-current-thread-v1')!==pointer)throw Error('UNCONFIRMED');}catch{setError(uiLocale()==='en'?'The chat was deleted. Keep a copy of this draft before closing; its recovery marker could not be saved.':'会话已删除，但草稿恢复标记未保存；关闭前请复制当前草稿。');}
      return;
    }
    setThreadAccess('checking');let timer:ReturnType<typeof setTimeout>|undefined;
    try{
      await Promise.race([(async()=>{
        if(!threadScope()){await openSavedThread(id,current,abort.signal,receipt?.organization.revision);return;}
        const scope=await threadClient.scope(id,abort.signal);if(!current())return;
        if(receipt&&scope.conversation_ref.revision<receipt.organization.revision)throw Error('ORGANIZATION_REFRESH_REQUIRED');
        const list=scope.project_id===null?projects():userProjects(await api.projects(abort.signal));if(!current())return;
        const selected=scope.project_id===null?undefined:list.find(item=>item.id===scope.project_id);if(scope.project_id!==null&&!selected)throw Error('HISTORY_PROJECT_UNAVAILABLE');
        const changed=(project()?.id??null)!==scope.project_id,spaces=changed&&scope.project_id!==null?await api.workspaces(scope.project_id,abort.signal):changed?[]:workspaces();if(!current())return;
        saveComposerDraft();if(scope.project_id===null)localStorage.removeItem('eeas-shell-project');else{localStorage.setItem('eeas-shell-project',scope.project_id);if(localStorage.getItem('eeas-shell-project')!==scope.project_id)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');}
        const restore=preserveLayoutReading(shell);
        batch(()=>{setThreadScope(scope);setSelectedDiscussion(value=>value?{...value,conversationId:scope.next_message.conversation_id}:value);setProject(selected);if(changed){setWorkspaces(spaces);setWorkspace(undefined);setDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);setSelection([]);setMaterials([]);}setHistorySelectedItem(value=>value?.id===id?{...value,projectId:scope.project_id,projectName:selected?.name,organization:{revision:scope.conversation_ref.revision,archived:scope.archived},conversationRef:scope.conversation_ref}:value);setThreadAccess('ready');});restore();
      })(),new Promise<never>((_resolve,reject)=>{timer=setTimeout(()=>reject(Error('ORGANIZATION_REFRESH_REQUIRED')),15000);})]);
    }catch{if(current())setThreadAccess('unavailable');}
    finally{if(timer!==undefined)clearTimeout(timer);abort.abort();}
  }
  function requestOrganization(item:HistoryItem,action:'move'|'delete',returnTarget:HTMLElement){if(organizationBlocked())return;setOrganizationTarget({item,action,returnTarget});}
  async function organized(receipt:OrganizationReceipt,item?:HistoryItem,title?:string){
    if(receipt.action==='archive'||receipt.action==='delete')setHistoryItems(rows=>rows.filter(row=>row.id!==receipt.conversation_id||(row.organization?.revision??0)>receipt.organization.revision));
    else setHistoryItems(rows=>rows.map(row=>row.id===receipt.conversation_id&&(row.organization?.revision??0)<=receipt.organization.revision?{...row,projectId:receipt.project_id,projectName:projects().find(project=>project.id===receipt.project_id)?.name,organization:{...receipt.organization},conversationRef:{id:receipt.conversation_id,revision:receipt.organization.revision},updatedAt:receipt.updated_at}:row));
    if(compact()&&threadScope()?.conversation_ref.id===receipt.conversation_id&&!conversationManagerOpen())setSidebarOpen(false);
    setOrganizationNotice({title:item?.title??title??organizationNotice()?.title??(uiLocale()==='en'?'Chat':'会话'),receipt,item});setOrganizationVersion(value=>value+1);setOrganizationRefresh(value=>value+1);if(organizationTarget()?.item.id===receipt.conversation_id&&organizationTarget()?.action===receipt.action)setOrganizationTarget(undefined);
    await Promise.allSettled([syncOrganizedThread(receipt),refreshHistory()]);
    requestAnimationFrame(()=>{if(!conversationManagerOpen()&&!organizationTarget()&&document.activeElement===document.body)focusComposer();});
  }
  async function organizeConversation(item:HistoryItem,intent:OrganizationIntent|'archive'|'restore'){
    saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();saveComposerDraft();
    const active=threadScope()?.conversation_ref.id===item.id;if(active)setThreadAccess('checking');
    try{const receipt=await organizationClient.change(item,intent);await organized(receipt,item);}
    catch(reason){setOrganizationVersion(value=>value+1);if(active&&!organizationClient.pending)await syncOrganizedThread();throw reason;}
  }
  async function inspectOrganization(){try{const title=organizationClient.pending?.title,receipt=await organizationClient.inspect();await organized(receipt,undefined,title);}catch{setOrganizationVersion(value=>value+1);}}
  async function retryOrganization(){try{const title=organizationClient.pending?.title,receipt=await organizationClient.retry();await organized(receipt,undefined,title);}catch{setOrganizationVersion(value=>value+1);}}
  const organizationStatus=()=>{organizationVersion();const pending=organizationClient.pending,error=organizationClient.error,notice=organizationNotice(),en=uiLocale()==='en';if(!pending&&!error&&!notice)return undefined;if(!pending&&!error&&notice?.receipt.action==='delete'&&threadAccess()==='deleted'&&notice.receipt.conversation_id===(threadScope()?.conversation_ref.id??selectedDiscussion()?.key.id)&&!conversationManagerOpen())return undefined;const copy=error==='ORGANIZATION_RECOVERY_SAVE_FAILED'?(en?'The change is confirmed. Its local recovery record could not be cleared; check it before another change.':'整理已确认，但本机恢复记录暂未清理，请先核查再继续。'):error==='ORGANIZATION_STORAGE_UNAVAILABLE'?(en?'Chat organization recovery could not be read. Existing records are retained.':'暂未读到会话整理恢复记录，原记录保留。'):error==='ORGANIZATION_REFRESH_REQUIRED'?(en?'Chat organization changed. Refresh before trying again.':'会话状态已变化，请刷新后再操作。'):error==='ORGANIZATION_REJECTED'?(en?'The change was not accepted. Your chat is retained.':'本次整理未被接受，会话保留。'):error==='ORGANIZATION_NOT_FOUND'?(en?'No committed result was found. You can retry the original operation.':'尚未查到原操作结果，可重试原操作。'):error?(en?'Check the original operation before making another change.':'请先核查原操作，再继续整理。'):pending?(en?'Checking chat organization; your discussion and draft are retained.':'会话整理结果待核查，当前讨论和草稿保留。'):notice?(en?({archive:'Archived: ',restore:'Restored: ',move:'Moved: ',delete:'Deleted: '}[notice.receipt.action]):({archive:'已归档：',restore:'已恢复：',move:'已移动：',delete:'已删除：'}[notice.receipt.action]))+notice.title:'';return <div class="as-conversation-organization-status" role={error?'alert':'status'}><span>{copy}</span><Show when={pending}><button type="button" disabled={organizationClient.busy} onClick={()=>void inspectOrganization()}>{en?'Check result':'核查结果'}</button><Show when={pending?.phase==='NOT_FOUND'}><button type="button" disabled={organizationClient.busy} onClick={()=>void retryOrganization()}>{en?'Retry original operation':'重试原操作'}</button></Show></Show><Show when={!pending&&notice?.item&&notice.receipt.action==='archive'&&!error}><button type="button" disabled={organizationBlocked()} onClick={()=>{const value=organizationNotice();if(value?.item)void organizeConversation({...value.item,organization:value.receipt.organization},'restore').catch(()=>{});}}>{en?'Undo archive':'撤销归档'}</button></Show><Show when={!pending&&error}><button type="button" onClick={()=>{organizationClient.reload();void refreshHistory();void syncOrganizedThread();}}>{en?'Refresh':'刷新'}</button></Show><Show when={!pending&&!error}><button type="button" aria-label={en?'Dismiss chat status':'关闭会话提示'} onClick={()=>setOrganizationNotice(undefined)}>×</button></Show></div>;};
  onMount(()=>{const handler=(event:StorageEvent)=>{if(event.key==='eeas-conversation-organization-v1')organizationClient.reload();};window.addEventListener('storage',handler);onCleanup(()=>window.removeEventListener('storage',handler));});
  const [historyPreview,setHistoryPreview]=createSignal<{title:string;input:string;answer:string}>();let historyDialog:HTMLDialogElement|undefined;
  const [historyPopoverOpen,setHistoryPopoverOpen]=createSignal(false);
  let historyDirectoryRequest=0,historyDirectoryAbort:AbortController|undefined,historySourceUnavailable=false;const historyCursors=new Set<string>();
  const historyDirectoryRefresh=new HistoryDirectoryRefresh(()=>refreshHistory(false,true));
  async function refreshHistory(more=false,retain=false){
    if(more&&(historyLoading()||historyLoadingMore()||!historyCursor()))return;
    const cursor=more?historyCursor():null,request=++historyDirectoryRequest;
    historyDirectoryAbort?.abort();const controller=new AbortController();historyDirectoryAbort=controller;
    historyDirectoryRefresh.setBusy(true);
    if(more)setHistoryLoadingMore(true);else{setHistoryLoading(true);setHistoryLoadingMore(false);}setHistoryError('');
    try{
      if(cursor&&historyCursors.has(cursor))throw new Error('HISTORY_CURSOR_REPEATED');
      const page=parseHistoryPage(await api.request('/api/conversations?limit=50'+(cursor?'&cursor='+encodeURIComponent(cursor):''),'GET',undefined,controller.signal),uiLocale());
      if(studioDisposed||request!==historyDirectoryRequest)return;
      const items=labelHistoryItems(page.items,localStorage);
      setHistoryItems(previous=>mergeHistoryItems(more||retain?previous:[],items));
      historySourceUnavailable=(more||retain?historySourceUnavailable:false)||page.sourceUnavailable;
      if(!more)historyCursors.clear();if(cursor)historyCursors.add(cursor);
      setHistoryCursor(retain&&historyCursor()?historyCursor():page.nextCursor);setHistoryPartial(page.partial||historySourceUnavailable);setHistoryLoaded(true);
      if(!more)setDeliveryRefresh(value=>value+1);
    }catch(reason){if(!controller.signal.aborted&&request===historyDirectoryRequest)setHistoryError(uiLocale()==='en'?'Could not refresh chat history. Your chats are retained.':'暂未刷新历史会话，原记录保留。');}
    finally{if(request===historyDirectoryRequest){setHistoryLoading(false);setHistoryLoadingMore(false);historyDirectoryRefresh.setBusy(false);}}
  }
  function finishHistorySelection(current:()=>boolean,keepSupervisor=false){
    if(!current())return false;
    setSupervisorReturn(undefined);setActivityLoading(false);setActivityDetail(undefined);setActivityOpen(false);setSupervisorOpen(keepSupervisor);
    return true;
  }
  async function openHistoryConversation(row:HistoryItem):Promise<HistorySelectionResult>{
    if(busy())return 'DEFERRED';
    if(creationClient.pending?.receipt?.conversation_ref.id===row.id){
      const owner=navigation.begin(),request=++activityRequest,current=()=>!studioDisposed&&owner.current()&&request===activityRequest;
      try{return await adoptCreatedConversation(creationClient.pending.receipt,undefined,current);}
      catch{if(!current())return 'DEFERRED';setCreationNotice(uiLocale()==='en'?'The new chat could not be opened. Your current input is kept.':'新会话暂时无法打开，当前输入保留。');return 'FAILED';}
    }
    const selected=historyItems().find(item=>item.id===row.id);if(!selected||!sameReview(selected.source,row.source)){setError(uiLocale()==='en'?'This conversation could not be opened. Your current draft is kept.':'会话暂时无法打开，当前草稿保留。');void refreshHistory(false,true);return 'FAILED';}
    const owner=navigation.begin(),request=++activityRequest,current=()=>!studioDisposed&&owner.current()&&request===activityRequest;
    const workflowProject=stagePresentation()&&row.source.kind==='conversation'&&row.projectId===workflowScope()?.project_id
      ?row.projectId:undefined;
    try{
      let nativeSeed:NativeMessage[]=[];
      if(row.source.kind==='native'&&nativeBridge?.getNativeConversation){
        const value=await nativeBridge.getNativeConversation({projectId:row.source.project_id,conversationId:row.source.conversation_id,operationId:row.source.last_operation_id}) as {kind?:string;messages?:NativeMessage[]};
        if(!current())return 'DEFERRED';if(value.kind==='SUPERVISOR_DISCUSSION')return await openLegacyHistoryConversation(row);nativeSeed=value.messages??[];
      }
      let scope:ThreadScope,recovered=false;
      try{scope=await threadClient.select(row,owner.signal);}
      catch(reason){
        if(!current())return 'DEFERRED';
        const source=row.source;
        if(!workflowProject||source.kind!=='conversation'||!(reason instanceof ApiError)||reason.code!=='REVISION_CONFLICT')throw reason;
        scope=await threadClient.scope(row.id,owner.signal);
        if(!current())return 'DEFERRED';
        if(scope.project_id!==workflowProject||!scope.metadata_source||
          scope.conversation_ref.revision<=source.conversation_ref.revision||scope.archived!==(row.organization?.archived??false))throw reason;
        row=savedThreadRow(scope,row.id);
        recovered=true;
      }
      if(!current())return 'DEFERRED';
      if(workflowProject&&scope.project_id!==workflowProject)throw Error('WORKFLOW_DESTINATION_UNAVAILABLE');
      const discoveryDraft=row.source.kind==='discovery'?new DiscussionDraftLease(localStorage,{kind:'conversation',projectId:null,id:row.id}).read():undefined;
      const page=await threadClient.history(scope,null,owner.signal);if(!current())return 'DEFERRED';
      if(discoveryDraft&&historyDiscoverySource(row,scope,discoveryDraft,page))return await openLegacyHistoryConversation(row);
      const opened=await installThread(row,scope,page,current,owner.signal,nativeSeed,undefined,undefined,recovered?'workflow-sidebar':undefined);
      if(!opened||!current())return 'DEFERRED';
      if(recovered)void refreshHistory(false,true);
      return 'OPENED';
    }catch(reason){if(current()){setError(uiLocale()==='en'?'This conversation could not be opened. Your current draft is kept.':'会话暂时无法打开，当前草稿保留。');void refreshHistory(false,true);return 'FAILED';}return 'DEFERRED';}
  }
  async function openThreadDiscoveryDocument(row:ThreadMessage){
    const scope=threadScope(),target=scope&&threadDiscoveryDocument(scope,row);if(!scope||!target)return;
    const owner=navigation.begin(),request=++activityRequest,owned=()=>!studioDisposed&&owner.current()&&request===activityRequest;
    const current=()=>owned()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id;
    try{
      const ref=target.objectRef;
      const value=await api.request<DiscoverySession>(`${api.projectPath(target.projectId)}/discovery/sessions/${encodeURIComponent(ref.id)}?revision=${ref.revision}`,'GET',undefined,owner.signal);
      if(!current())return;
      if(value.project_id!==target.projectId||value.id!==ref.id||value.revision!==ref.revision||value.sha256!==ref.sha256)throw Error('HISTORY_REFERENCE_CHANGED');
      if(scope.archived||scope.project_id!==target.projectId||value.current_revision!==value.revision||value.source_current===false){
        saveComposerDraft();
        const candidate=value.payload.candidate;
        setHistoryPreview({title:uiLocale()==='en'?`Requirements record · Revision ${value.revision}`:`需求记录 · 版本 ${value.revision}`,input:'',answer:[candidate.summary,...candidate.claims.map(claim=>claim.text),...candidate.questions.map(question=>question.text)].join('\n\n')});return;
      }
      await installDiscussionTarget({kind:'discovery',session:value,navigation:{project_id:target.projectId,workspace_ref:value.payload.workspace_ref??null}},current,owner.signal,'chat');
      if(owned()){showAgentChat();presentWorkspace('chat');}
    }catch(reason){if(current())setError(uiLocale()==='en'?'This requirements version could not be opened. Your draft is kept.':'此需求版本暂时无法打开，当前草稿保留。');}
  }
  async function installThread(row:HistoryItem,scope:ThreadScope,page:ThreadHistory,navigationCurrent:()=>boolean,signal?:AbortSignal,nativeSeed:NativeMessage[]=[],creation?:{operationId:string;seed?:()=>DiscussionDraft},migration?:{conflict:DiscussionDraftMigrationConflict;choice:'local'|'saved';active:()=>boolean},destination?:StageDestination|'workflow-sidebar'){
    const current=()=>navigationCurrent()&&(!migration||migration.active());
    const pid=scope.project_id,projectCatalog=project()?.id===pid&&projects().some(item=>item.id===pid)?allProjects():await api.projects(signal);if(!current())return;
    const selected=pid===null?undefined:userProjects(projectCatalog).find(item=>item.id===pid);if(pid!==null&&!selected)throw new Error('HISTORY_PROJECT_UNAVAILABLE');
    const list=pid===null?[]:await api.workspaces(pid,signal);if(!current())return;
    const publicSource=threadOriginalOwner(scope,row.source.project_id);
    let original:AgentSession|undefined;
    if(publicSource?.original_source?.kind==='original'){
      original=await agentSessionsApi.get(publicSource.original_source.project_id,publicSource.original_source.session_ref);if(!current())return;
      verifyThreadOriginalOwner(scope,publicSource,original);
    }
    if(!current())return;
    const workspaceRef=scope.metadata_source?(publicSource?.identity.project_id===pid?publicSource.original_source?.workspace_ref??null:null):row.source.project_id===pid?row.source.workspace_ref:null;
    const bound=workspaceRef?list.find(item=>item.id===workspaceRef.id&&item.revision===workspaceRef.revision&&item.binding_sha256===workspaceRef.binding_sha256):(creation||!scope.metadata_source)&&project()?.id===pid?list.find(item=>item.id===workspace()?.id&&item.revision===workspace()?.revision&&item.binding_sha256===workspace()?.binding_sha256):undefined;
    if(workspaceRef&&!bound)throw new Error('HISTORY_WORKSPACE_CHANGED');
    saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();if(!current())return;saveComposerDraft();
    const keys=threadDraftSources(scope),previous=selectedDiscussion();
    let liveKey=keys.find(key=>previous&&previous.key.kind!=='conversation'?sameReview(key,previous.key):!previous&&!activeSession()&&(key.kind==='native'&&key.projectId===(project()?.id??null)&&key.id===conversationId()||key.kind==='original'&&key.id===originalSession()?.session_ref.id&&key.projectId===originalSession()?.project_id));
    if(liveKey&&!previous&&!composerInteracted&&localStorage.getItem('eeas-discussion-draft-link-v1:'+discussionDraftKey(liveKey))!==null)liveKey=undefined;
    const id=scope.conversation_ref.id;
    const workflowTarget=pid?{project_id:pid,conversation_id:id}:undefined;
    const workflowKey=workflowTarget?stageModeKey(workflowTarget):undefined;
    const sameThread=threadScope()?.project_id===pid&&threadScope()?.conversation_ref.id===id;
    const workflowMode=workflowKey?localStorage.getItem(workflowKey):null;
    const nextWorkflow=(!sameThread||destination)&&workflowTarget&&workflowMode!=='daily'
      ?await stageApi.list(workflowTarget,signal).catch(reason=>{
        if(workflowMode==='workflow'&&threadScope())throw reason;
        return undefined;
      }):undefined;
    if(!current())return;
    if(destination==='workflow-sidebar'){
      if(!isWorkflowStage(nextWorkflow,id))throw Error('WORKFLOW_DESTINATION_UNAVAILABLE');
    }else if(destination){
      const selectedRun=nextWorkflow?.runs.find(run=>run.id===destination.run&&run.project_id===destination.project&&
        run.conversation_id===destination.owner);
      const historical=nextWorkflow?.historical_stage;
      const archivedMatch=historical?.stage_session_id===id&&historical.node_id===destination.node;
      const currentMatch=!historical&&selectedRun?.stages.some(stage=>stage.stage_session_id===id&&stage.node_id===destination.node);
      if(!selectedRun||!(archivedMatch||!destination.historical&&currentMatch))throw Error('WORKFLOW_DESTINATION_UNAVAILABLE');
    }
    const showWorkflow=workflowMode==='workflow'||!workflowMode&&isWorkflowStage(nextWorkflow,id);
    migration?.conflict.resolveDraft(migration.choice);
    let lease:DiscussionDraftLease;
    try{
      lease=migrateConversationDraft(localStorage,id,keys,!migration&&liveKey?{scope:liveKey,value:currentDiscussionDraft()}:undefined);
    }catch(error){
      if(!(error instanceof DiscussionDraftMigrationConflict))throw error;
      const conflict=error,reference={...scope.conversation_ref},archived=scope.archived;
      async function resolveMigration(choice:'local'|'saved'){
        const active=()=>navigationCurrent()&&draftRecovery()?.resolveMigration===resolveMigration;
        if(!active())return;
        const fresh=await threadClient.scope(id,signal);
        if(!active())return;
        if(!sameReview(fresh.conversation_ref,reference)||fresh.project_id!==pid
          ||fresh.archived!==archived||!sameReview(threadDraftSources(fresh),keys))throw Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
        const refreshed=await threadClient.history(fresh,null,signal);
        if(!active())return;
        await installThread(row,fresh,refreshed,navigationCurrent,signal,nativeSeed,creation,{conflict,choice,active},destination);
      }
      setDraftRecoveryError('');
      setDraftRecovery({scope:{kind:'conversation',projectId:null,id},lease:error.lease,local:error.local,saved:error.saved,resolveMigration});
      return false;
    }
    if(creation?.seed){const seed=seedConversationDraft(localStorage,scope.conversation_ref.id,creation.seed());if(seed.conflict){const receipt=creationClient.pending?.receipt;if(!receipt)throw Error('CREATION_CHECK_REQUIRED');const before=structuredClone(currentDiscussionDraft());recoverDraft({kind:'conversation',projectId:null,id:scope.conversation_ref.id},seed.local,async chosen=>{await adoptCreatedConversation(receipt,{chosen,before});});return false;}lease=seed.lease;}
    const draft=lease.read(),savedMode=readConversationAgentMode(localStorage,scope.conversation_ref.id,originalConversationMode(original,scope.metadata_source?'build':'plan'));
    const pointer=JSON.stringify({version:1,id:scope.conversation_ref.id,...(creation?{creation_operation_id:creation.operationId}:{})});localStorage.setItem('eeas-current-thread-v1',pointer);if(localStorage.getItem('eeas-current-thread-v1')!==pointer)throw new Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
    if(creation){const key='eeas-conversation-created-adoption-v1:'+creation.operationId,value=JSON.stringify({version:1,id:scope.conversation_ref.id});localStorage.setItem(key,value);if(localStorage.getItem(key)!==value)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');}
    if(pid===null)localStorage.removeItem('eeas-shell-project');else localStorage.setItem('eeas-shell-project',pid);
    if(pid!==null){const workspaceKey=`eeas-shell-workspace:${pid}`;if(bound)localStorage.setItem(workspaceKey,bound.id);else localStorage.removeItem(workspaceKey);if(localStorage.getItem(workspaceKey)!==(bound?.id??null))throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');}
    draftPersistence.pause();nativeDraftOwner=undefined;stopObserver();threadReadAbort?.abort();threadReadGeneration++;threadResultGeneration++;setThreadResultSelection(undefined);
    batch(()=>{
      setDraftReady(false);setSelectedDiscussion(undefined);discussionDraftLease=undefined;setActiveOperation(undefined);
      if(!sameThread){
        setPreparedWorkflow(workflowKey&&nextWorkflow?{key:workflowKey,index:nextWorkflow}:undefined);
        setStagePresentation(showWorkflow);
      }
      setProjects(projectCatalog);setProject(selected);projectObjects.activate(pid??undefined);setWorkspaces(list);setWorkspace(bound);selectOriginalSession(undefined);setActiveSession(undefined);setFeedbackRecords([]);setDraftAttachments([]);setMaterials([]);setSelection([]);clearProjectFeed();setDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);setDirectResults([]);setNativeFileFeedbacks([]);setDirectFeedbackRefs({});setInspector(undefined);setNativeOpen(false);setSkillDrawer(false);setSessionManagementOpen(false);
      setNativeMessages(nativeSeed.filter(message=>nativeMessagePending(message)||message.status==='UNKNOWN_OUTCOME').map(message=>({input:nativeDiscussionInput(message),message})));
      discussionDraftLease=lease;setSelectedDiscussion({key:{kind:'conversation',projectId:null,id:scope.conversation_ref.id},conversationId:scope.next_message.conversation_id});
      setThreadAccess('ready');setThreadScope(scope);setThreadMessages(page.messages);setThreadCursor(page.nextCursor);setThreadLoading(false);setThreadError('');setToolMode(row.source.kind==='native'?'direct':'original');setOriginalAgent(savedMode);if(original)selectOriginalSession(original);
      setHistoryActiveId(row.id);setHistorySelectedItem({...row,projectId:pid,conversationRef:scope.conversation_ref});setHistoryReloadToken(value=>value+1);setPrompt(draft.text);setNativeDraftAttachments(draft.attachments);setError('');setNotice('');
    });
    if(!finishHistorySelection(current))return false;
    enterConversationReading(conversationReadingState(),current);
    showAgentChat();presentWorkspace('chat');setHistoryPopoverOpen(false);if(compact())setSidebarOpen(false);if(pid!==null){loadSourceContext(pid);void refreshNative(pid);void refreshObjects(pid);if(!scope.archived)void restoreFileFeedbackHistory(pid,()=>navigationCurrent()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id&&!threadScope()?.archived);}
    return true;
  }
  async function openLegacyHistoryConversation(row:HistoryItem):Promise<HistorySelectionResult>{
    const selected=historyItems().find(item=>item.id===row.id);if(!selected||!sameReview(selected.source,row.source))throw new Error('HISTORY_REFERENCE_CHANGED');
    if(selected.source.kind==='conversation')return openHistoryConversation(selected);
    const source=selected.source,owner=navigation.begin(),request=++activityRequest;
    const current=()=>!studioDisposed&&owner.current()&&request===activityRequest;
    try{
      let destination:DiscussionDestination;
      if(source.kind==='native'){
        if(!nativeBridge?.inspectNativeMessage||!nativeBridge.getNativeConversation)throw new Error('HISTORY_NATIVE_READER_UNAVAILABLE');
        const message=await nativeBridge.inspectNativeMessage(source.last_operation_id);if(!current())return 'DEFERRED';
        if(message.operation_id!==source.last_operation_id||message.project_id!==source.project_id||message.request_project_id!==source.request_project_id||message.conversation_id!==source.conversation_id||message.request_sha256!==source.request_sha256)throw new Error('HISTORY_REFERENCE_CHANGED');
        const response=await nativeBridge.getNativeConversation({projectId:source.project_id,conversationId:source.conversation_id,operationId:source.last_operation_id}) as {kind:string;conversation_id:string;selection?:{operation_id:string;request_sha256:string;question:string;text:string;status:SupervisorOperationSelection['status']}};if(!current())return 'DEFERRED';
        if(response.kind==='SUPERVISOR_DISCUSSION'){
          const selected=response.selection;if(response.conversation_id!==source.conversation_id||!selected||selected.operation_id!==source.last_operation_id||selected.request_sha256!==source.request_sha256||typeof selected.question!=='string'||typeof selected.text!=='string')throw new Error('HISTORY_REFERENCE_CHANGED');
          saveComposerDraft();await supervisorClient?.getSnapshot();if(!finishHistorySelection(current,true))return 'DEFERRED';
          setSupervisorSelection({...selected,answer_text:selected.text});setHistoryActiveId(row.id);return 'OPENED';
        }
        const conversation=response as unknown as {project_id:string|null;conversation_id:string;messages:NativeMessage[]};
        const exact=conversation.messages?.find(item=>item.operation_id===source.last_operation_id);
        if(conversation.project_id!==source.project_id||conversation.conversation_id!==source.conversation_id||!exact||exact.request_sha256!==source.request_sha256)throw new Error('HISTORY_REFERENCE_CHANGED');
        if(source.project_id!==source.request_project_id){if(!finishHistorySelection(current))return 'DEFERRED';setHistoryPreview({title:selected.title,input:nativeDiscussionInput(exact),answer:nativeReplyText(exact)??''});return 'OPENED';}
        destination={kind:'native',input:nativeDiscussionInput(exact),message,conversation,navigation:{project_id:source.project_id,conversation_id:source.conversation_id,workspace_ref:null}};
      }else if(source.kind==='original'){
        const session=await agentSessionsApi.get(source.project_id,source.session_ref);if(!current())return 'DEFERRED';
        if(session.generation!==source.generation||!sameReview(session.workspace_ref??null,source.workspace_ref)||!sameReview(session.provider_profile_ref??null,source.provider_profile_ref))throw new Error('HISTORY_REFERENCE_CHANGED');
        destination={kind:'original',session,navigation:{project_id:source.project_id,session_ref:source.session_ref,workspace_ref:source.workspace_ref}};
      }else{
        const session=await api.request<DiscoverySession>(`${api.projectPath(source.project_id)}/discovery/sessions/${encodeURIComponent(source.object_ref.id)}?revision=${source.object_ref.revision}`,'GET',undefined,owner.signal);if(!current())return 'DEFERRED';
        if(session.project_id!==source.project_id||session.id!==source.object_ref.id||session.revision!==source.object_ref.revision||session.sha256!==source.object_ref.sha256||!sameReview(session.payload.workspace_ref??null,source.workspace_ref))throw new Error('HISTORY_REFERENCE_CHANGED');
        if(session.current_revision!==session.revision||session.source_current===false){if(!finishHistorySelection(current))return 'DEFERRED';setHistoryPreview({title:selected.title,input:'',answer:session.payload.candidate.summary});return 'OPENED';}
        destination={kind:'discovery',session,navigation:{project_id:source.project_id,workspace_ref:source.workspace_ref}};
      }
      await installDiscussionTarget(destination,current,owner.signal,'chat');if(!finishHistorySelection(current))return 'DEFERRED';
      setHistoryActiveId(selected.id);setHistorySelectedItem(selected);showAgentChat();setSidebarOpen(false);return 'OPENED';
    }catch(reason){if(current()){setHistoryError(uiLocale()==='en'?'Could not open this version. Refresh history and try again.':'暂未打开这段讨论，请刷新历史后再试。');throw reason;}}
    return 'DEFERRED';
  }
  onMount(()=>{void refreshHistory();onCleanup(()=>{historyDirectoryRefresh.dispose();historyDirectoryRequest++;historyDirectoryAbort?.abort();});});
  createEffect(()=>{if(historyPreview())historyDialog?.showModal();else if(historyDialog?.open)historyDialog.close();});
  createEffect(()=>{if(windowMode()!=='companion')setHistoryPopoverOpen(false);});
  function toggleHistory(){if(windowMode()==='companion'){setHistoryPopoverOpen(value=>!value);return;}setLeftTab('conversations');if(fullscreen()){setFullscreenFiles(value=>!value);setFullscreenAgent(false);}else if(compact()){setFilesHidden(false);setSidebarOpen(value=>!value);}else setFilesHidden(value=>!value);}
  const [discussionFileState,setDiscussionFileState]=createSignal<QuickFileContextState>(),[discussionFileError,setDiscussionFileError]=createSignal('');
  const [nativeMenu,setNativeMenu] = createSignal(false);
  const [viewMenu,setViewMenu] = createSignal(false);
  const [activityOpen,setActivityOpen]=createSignal(false),[activitySnapshot,setActivitySnapshot]=createSignal<SupervisorSnapshot>();
  const [activityReadStatus,setActivityReadStatus]=createSignal<CenterReadStatus>('INITIAL');
  const [supervisorOpen,setSupervisorOpen]=createSignal(false),[supervisorView,setSupervisorView]=createSignal<SupervisorConversationClientView>();
  const [supervisorSelection,setSupervisorSelection]=createSignal<SupervisorOperationSelection>();
  let supervisorClient:SupervisorConversationClient|undefined,supervisorDiscussionEpoch=0;
  const supervisorProfile=()=>providers().find(item=>item.profile_ref.id===providerId())?.profile_ref;
  const supervisorError=()=>{const value=supervisorView();if(!value?.error||value.error.operation_id)return '';const kind=supervisorDraftFailureKind(value.error.code);return centerText(uiLocale(),value.draftConflict?'draftConflict':value.error.code==='SUPERVISOR_STORAGE_FULL'?'storageFull':kind==='UNKNOWN'?'draftSaveUnknown':kind==='FAILED'||value.draftBlocked?'draftUnavailable':'readUnavailable');};
  const supervisorCenter=()=> <SupervisorCommandCenter snapshot={activitySnapshot()} readStatus={activityReadStatus()} visible={!document.hidden}
    open={supervisorOpen()} onOpenChange={value=>{supervisorDiscussionEpoch++;setSupervisorOpen(value);if(!value)supervisorClient?.resetSearch();}} draft={supervisorView()?.draft} onDraftChange={text=>supervisorClient?.edit(text)}
    operations={supervisorView()?.snapshot?.operations} messages={supervisorView()?.snapshot?.messages} history={supervisorView()?.history} onReadEarlier={()=>supervisorClient?.readEarlier()} onResetHistory={()=>supervisorClient?.resetHistory()}
    search={supervisorView()?.search} searchBusy={supervisorView()?.searchBusy} onSearchEarlier={query=>supervisorClient?.searchEarlier(query)} onResetSearch={()=>supervisorClient?.resetSearch()}
    onSelectDiscussion={async reference=>{const client=supervisorClient,epoch=++supervisorDiscussionEpoch;if(!client)return;const selected=await client.readDiscussion(reference);if(!studioDisposed&&supervisorOpen()&&epoch===supervisorDiscussionEpoch&&supervisorClient===client)setSupervisorSelection(selected);}}
    providerProfileRef={supervisorProfile()} canAsk={!!supervisorProfile()&&!!nativeBridge?.askSupervisor}
    askAdmission={text=>{supervisorView();const profile=supervisorProfile();return supervisorClient&&profile?supervisorClient.askAdmission(text,profile):{allowed:true};}}
    selectedOperation={supervisorSelection()} onDismissSelected={()=>{supervisorDiscussionEpoch++;setSupervisorSelection(undefined);}}
    onConfigure={()=>void openProviders()} onAsk={async question=>{const profile=supervisorProfile();if(!supervisorClient||!profile)return {status:'REJECTED'};try{return await supervisorClient.ask({operationId:operationId(),text:question.text,providerProfileRef:profile,...question.followupRef?{followupRef:question.followupRef}:{}});}catch{return {status:'REJECTED'};}}}
    onInspect={id=>supervisorClient?supervisorClient.inspect(id):Promise.resolve({status:'REJECTED'})}
    onLocalAnswer={(question,answer)=>supervisorClient?.persistLocal({question,answer})}
    error={supervisorError()} draftConflict={supervisorView()?.draftConflict} onRefresh={()=>supervisorClient?.getSnapshot()}
    draftBlocked={supervisorView()?.draftBlocked} draftRecovery={(supervisorView(),supervisorClient?.draftSaveRecovery())}
    onCheckDraftSave={()=>supervisorClient?.inspectDraftSave().catch(()=>{})} onRetryDraftSave={()=>supervisorClient?.retryDraftSave().catch(()=>{})}
    onResolveDraftConflict={choice=>{if(!supervisorClient)return;supervisorClient.resolveDraftConflict(choice);if(choice==='keep-local')void supervisorClient.flush().catch(()=>{});}}
    onOpenTask={openActivityTask} onOpenAll={()=>{activityRequest++;setActivityLoading(false);setActivityDetail(undefined);setActivityOpen(true);}}/>;
  onMount(()=>{
    const bridge=nativeBridge;
    const release=bridge?.onSupervisorOpen?.(()=>{setNativeMenu(false);setActivityOpen(false);setSupervisorOpen(true);});
    const configure=bridge?.onSupervisorConfigure?.(()=>{setSupervisorOpen(false);setNativeMenu(false);setActivityOpen(false);void openProviders();});
    if(bridge?.getSupervisorConversation&&bridge.saveSupervisorDraft&&bridge.askSupervisor&&bridge.inspectSupervisor&&bridge.saveSupervisorLocalAnswer){
      supervisorClient=new SupervisorConversationClient({getSnapshot:()=>bridge.getSupervisorConversation!(),saveDraft:body=>bridge.saveSupervisorDraft!(body),
        ask:body=>bridge.askSupervisor!(body),inspect:id=>bridge.inspectSupervisor!(id),persistLocal:body=>bridge.saveSupervisorLocalAnswer!(body),onChange:bridge.onSupervisorConversation,
        ...(bridge.getSupervisorHistoryPage?{readHistoryPage:body=>bridge.getSupervisorHistoryPage!(body)}:{}),
        ...(bridge.getSupervisorOperation?{readOperation:body=>bridge.getSupervisorOperation!(body)}:{})},setSupervisorView);
      void supervisorClient.start();
    }
    onCleanup(()=>{release?.();configure?.();void supervisorClient?.close().catch(()=>{});});
  });

  const [activityDetail,setActivityDetail]=createSignal<SupervisedDetail>(),[activityError,setActivityError]=createSignal<UIErrorCopy>(''),[activityLoading,setActivityLoading]=createSignal(false);
  let activityRequest=0;
  function showTaskActivity(){activityRequest++;setActivityLoading(false);setNativeMenu(false);setActivityDetail(undefined);setActivityOpen(value=>!value);}
  function backTaskActivity(){activityRequest++;setActivityLoading(false);setActivityDetail(undefined);}
  function acceptTaskActivity(raw:unknown){try{const snapshot=supervisorSnapshot(raw);setActivitySnapshot(snapshot);historyDirectoryRefresh.tasks(snapshot);setActivityReadStatus('READY');if(authoredText(activityError()).startsWith('任务动态暂未'))setActivityError('');}catch{setActivityError('任务动态暂未核对成功，保留上次观察。');}}
  async function openActivityTask(task:SupervisorTask){
    supervisorDiscussionEpoch++;
    const request=++activityRequest;setActivityOpen(true);setActivityLoading(true);setActivityError('');setActivityDetail(undefined);
    const query=new URLSearchParams({kind:task.key.kind,object_id:task.key.object_id,generation:String(task.key.generation),
      ...task.key.project_id?{project_id:task.key.project_id}:{},...task.key.operation_id?{operation_id:task.key.operation_id}:{}});
    try{
      const value=nativeBridge?.getSupervisedTaskDetail?await nativeBridge.getSupervisedTaskDetail(task.key) as SupervisedDetail:await api.request<SupervisedDetail>('/api/supervisor/task-detail?'+query,'GET');
      if(!sameSupervisedTask(value.key,task.key)||!sameSupervisedTask(value.task.key,task.key))throw new Error('原任务引用不同');
      if(value.continuation&&nativeBridge?.getSupervisedTaskDetail){
        const view=await supervisorClient?.getSnapshot();
        const operation=value.continuation?await supervisorClient?.readOperation(value.continuation):undefined;
        const selected=selectedSupervisorOperation(value,view?.snapshot,operation);
        if(!studioDisposed&&request===activityRequest){setSupervisorSelection(selected);setActivityOpen(false);setActivityDetail(undefined);setSupervisorOpen(true);}
      }else if(!studioDisposed&&request===activityRequest)setActivityDetail(value);
    }catch(error){if(!studioDisposed&&request===activityRequest)setActivityError(authoredCopy('原任务暂未打开，未重发：{detail}',{detail:authoredErrorCopy(error)}));}
    finally{if(!studioDisposed&&request===activityRequest)setActivityLoading(false);}
  }
  async function enterSupervisedTask(detail:SupervisedDetail){
    if(busy())return;
    const request=++activityRequest,owner=navigation.begin();
    const current=()=>!studioDisposed&&request===activityRequest&&owner.current()&&activityOpen();
    setActivityLoading(true);setActivityError('');
    try{
      const target=await loadSupervisedTaskNavigation(detail,{
        get:(path,signal)=>api.request(path,'GET',undefined,signal),
        inspectNativeMessage:nativeBridge?.inspectNativeMessage,
        getNativeConversation:nativeBridge?.getNativeConversation,
      },{signal:owner.signal,current,expectedTask:{key:detail.key,source_ref:detail.task.source_ref}});
      if(!target||!current())return;
      if(target.readOnly){setActivityError(authoredCopy('这是历史记录，已保留原问题和回答；当前未切换执行上下文。'));return;}
      await installDiscussionTarget(target,current,owner.signal);
      if(!current())return;
      setSupervisorReturn(detail);setActivityLoading(false);setActivityOpen(false);setActivityDetail(undefined);setSupervisorOpen(false);showAgentChat();
    }catch(reason){if(current())setActivityError(authoredErrorCopy(reason));}
    finally{if(current())setActivityLoading(false);}
  }
  async function installDiscussionTarget(target:DiscussionDestination,current:()=>boolean,signal?:AbortSignal,presentation:'result'|'chat'='result'){
    const pid=target.navigation.project_id;
    const selected=pid===null?undefined:project()?.id===pid?project():(await api.projects(signal)).find(item=>item.id===pid);
    if(!current())return;
    if(pid!==null&&!selected)throw new Error('原项目暂不可用，当前讨论保持不变');
    const list=pid===null?[]:await api.workspaces(pid,signal);if(!current())return;
    const workspaceRef=target.navigation.workspace_ref;
    const bound=workspaceRef?list.find(item=>item.id===workspaceRef.id&&item.revision===workspaceRef.revision&&item.binding_sha256===workspaceRef.binding_sha256):undefined;
    if(workspaceRef&&!bound)throw new Error('原工作目录的绑定已变化，未切换到其他目录');
    const key:DiscussionDraftKey|undefined=target.kind==='native'?{kind:'native',projectId:pid,id:target.navigation.conversation_id}:target.kind==='original'?{kind:'original',projectId:pid,id:target.navigation.session_ref.id}:undefined;
    saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();if(!current())return;
    // Check current input again after asynchronous saves; never carry it into another discussion.
    saveComposerDraft();
    const lease=key?new DiscussionDraftLease(localStorage,key):undefined,draft=lease?.read();
    const cid=target.kind==='native'?target.navigation.conversation_id:target.kind==='original'?conversationIdFor(localStorage,fileConversationBasis({projectId:pid??undefined},target.navigation.session_ref)):undefined;
    if(target.kind==='native'){
      const prior=readNativeHistory(localStorage,cid);
      saveNativeHistory(localStorage,[...target.conversation.messages.filter(message=>message.operation_id!==target.message.operation_id),target.message].slice(-30).map(message=>({input:prior.find(item=>item.operation_id===message.operation_id)?.input??nativeDiscussionInput(message,message.operation_id===target.message.operation_id?target.input:''),operation_id:message.operation_id})),cid);
    }
    const mode=target.kind==='native'?'direct':target.kind==='original'?'original':'discovery';
    localStorage.setItem('eeas-agent-tool-mode',mode);
    if(localStorage.getItem('eeas-agent-tool-mode')!==mode)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
    if(target.kind==='discovery'){
      const sessionKey=`eeas-discovery-session:${target.session.project_id}`;localStorage.setItem(sessionKey,target.session.id);
      if(localStorage.getItem(sessionKey)!==target.session.id)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
    }
    if(pid===null)localStorage.removeItem('eeas-shell-project');else localStorage.setItem('eeas-shell-project',pid);
    if(pid!==null){const workspaceKey=`eeas-shell-workspace:${pid}`;if(bound)localStorage.setItem(workspaceKey,bound.id);else localStorage.removeItem(workspaceKey);if(localStorage.getItem(workspaceKey)!==(bound?.id??null))throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');}
    localStorage.removeItem('eeas-current-thread-v1');clearThread();
    draftPersistence.pause();nativeDraftOwner=undefined;stopObserver();setActiveOperation(undefined);
    batch(()=>{
      setDraftReady(false);setSelectedDiscussion(undefined);discussionDraftLease=undefined;
      setProject(selected);projectObjects.activate(pid??undefined);setWorkspaces(list);setWorkspace(bound);selectOriginalSession(undefined);setActiveSession(undefined);
      setFeedbackRecords([]);setDraftAttachments([]);setMaterials([]);setSelection([]);clearProjectFeed();
      setDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);setDirectResults([]);setNativeFileFeedbacks([]);setDirectFeedbackRefs({});
      setInspector(undefined);setNativeOpen(false);setSkillDrawer(false);setSessionManagementOpen(false);setError('');setNotice('');
      if(target.kind==='native'||target.kind==='original'){
        discussionDraftLease=lease;setToolMode(target.kind==='native'?'direct':'original');
        if(target.kind==='original')selectOriginalSession(target.session);
        setSelectedDiscussion({key:key!,conversationId:cid!});setHistoryReloadToken(value=>value+1);setPrompt(draft!.text);setNativeDraftAttachments(draft!.attachments);
      }else{
        setToolMode('discovery');setNativeDraftAttachments([]);
        if(target.kind==='discovery'){
          activateSession(target.session);
          try{
            const pending=readDiscoveryPending(localStorage,target.session.project_id);
            if(pending?.sessionId===target.session.id&&(!pending.basisRevision||pending.basisRevision===target.session.revision))observeDiscovery(target.session.project_id,target.session.id,pending.id);
          }catch{setDiscoveryError({projectId:target.session.project_id,sessionId:target.session.id,message:'本机处理恢复记录暂不可用，原记录和输入保留。'});}
          if(presentation==='result')inspect(envelope('discovery',target.session.id,target.session,target.session.revision,target.session.sha256));
        }
        else{restoreComposerDraft();openProjectArtifact('proposal',target.proposal);}
      }
    });
    if(pid!==null){loadSourceContext(pid);void refreshNative(pid);void refreshObjects(pid);}
  }
  function nativeDiscussionInput(value:NativeMessage,fallback=''){const input=(value as NativeMessage&{input?:{raw_text?:string};native_feedback_ref?:NativeRef});return input.native_feedback_ref?(uiLocale()==='en'?'File feedback':'文件修改意见'):input.input?.raw_text??fallback;}
  async function returnToSupervisor(){
    try{saveComposerDraft();await supervisorClient?.getSnapshot();setSupervisorOpen(true);setActivityOpen(false);setNativeMenu(false);}
    catch(reason){setError(authoredErrorCopy(reason));}
  }
  onMount(()=>{
    let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;
    const receive=(raw:unknown)=>{if(!stopped)acceptTaskActivity(raw);};
    const release=nativeBridge?.onTaskActivity?.(receive);
    const open=nativeBridge?.onSupervisedTask?.(key=>{
      const request=++activityRequest;setActivityOpen(true);setActivityDetail(undefined);setActivityLoading(!!key);
      if(key)void globalSupervisor.refresh().then(value=>{if(stopped||request!==activityRequest||!activityOpen())return;acceptTaskActivity(value);const target=value.tasks.find(task=>sameSupervisedTask(key,task.key));if(target)void openActivityTask(target);else{setActivityLoading(false);setActivityError('原任务引用已变化，保留任务列表。');}}).catch(()=>{if(!stopped&&request===activityRequest&&activityOpen()){setActivityLoading(false);setActivityError('暂未读取到原任务，未重试执行。');}});
    });
    const read=async()=>{
      try{const native=await nativeBridge?.getTaskActivity?.();if(stopped)return;if(native?.snapshot)receive(native.snapshot);else receive(await globalSupervisor.refresh());}
      catch{if(!stopped){setActivityReadStatus('OBSERVATION_UNAVAILABLE');setActivityError('暂时无法读取任务动态，保留原任务。');}}
      finally{if(!stopped&&!nativeBridge?.onTaskActivity)timer=setTimeout(()=>void read(),30000);}
    };
    void read();onCleanup(()=>{stopped=true;if(timer!==undefined)clearTimeout(timer);release?.();open?.();});
  });
  createEffect(()=>{const key=activityOpen()?activityDetail()?.key??null:null;void nativeBridge?.setVisibleTask?.(key).catch(()=>{});});
  const [composerView,setComposerView]=createSignal<ComposerView>({folded:false,focused:false});const composerPresentation=new ComposerPresentation(setComposerView);
  const [composerMenu,setComposerMenu]=createSignal<'model'|'options'>(),[composerMenuKeyboard,setComposerMenuKeyboard]=createSignal(false);
  const [agentDrawer,setAgentDrawer]=createSignal<'original'|'native'|'resources'>('original');
  const [orbEnabled,setOrbEnabled]=createSignal(true),[orbSettingBusy,setOrbSettingBusy]=createSignal(false),[orbSettingError,setOrbSettingError]=createSignal('');
  let modelMenuButton:HTMLButtonElement|undefined,optionsMenuButton:HTMLButtonElement|undefined,composerMenuFrame:number|undefined,studioDisposed=false;
  let skillDrawerReturn:HTMLElement|undefined;
  function closeComposerMenu(restoreFocus=false){const previous=composerMenu();setComposerMenu(undefined);if(composerMenuFrame!==undefined){cancelAnimationFrame(composerMenuFrame);composerMenuFrame=undefined;}if(restoreFocus)(previous==='model'?modelMenuButton:optionsMenuButton)?.focus({preventScroll:true});}
  function toggleComposerMenu(next:'model'|'options',keyboard=false){const open=composerMenu()!==next;closeComposerMenu();if(!open)return;setComposerMenuKeyboard(keyboard);setComposerMenu(next);composerMenuFrame=requestAnimationFrame(()=>{composerMenuFrame=undefined;if(composerMenu()!==next)return;const panel=document.querySelector<HTMLElement>(next==='model'?'#as-composer-model-options':'#as-composer-action-options');if(next!=='model')panel?.querySelector<HTMLElement>('select,input:not(:disabled),button:not(:disabled)')?.focus({preventScroll:true});});}
  onMount(()=>{if(nativeBridge?.getAssistantOrbState)void nativeBridge.getAssistantOrbState().then(value=>{if(!studioDisposed)setOrbEnabled(value.enabled);}).catch(()=>{if(!studioDisposed)setOrbSettingError('暂时无法读取悬浮球设置，可重新选择。');});});
  onCleanup(()=>{studioDisposed=true;if(composerMenuFrame!==undefined)cancelAnimationFrame(composerMenuFrame);});
  async function changeOrbSetting(enabled:boolean){if(orbSettingBusy()||!nativeBridge?.setAssistantOrbEnabled)return;setOrbSettingBusy(true);setOrbSettingError('');try{const result=await nativeBridge.setAssistantOrbEnabled(enabled);if(!studioDisposed)setOrbEnabled(result.enabled);}catch{if(!studioDisposed)setOrbSettingError('暂时无法更改悬浮球设置，请稍后重试。');}finally{if(!studioDisposed)setOrbSettingBusy(false);}}

  const [pinnedObservation,setPinnedObservation]=createSignal<PinnedObservation>();
  const [nativeMaximized,setNativeMaximized]=createSignal(false);
  let nativeTimer:ReturnType<typeof setTimeout>|undefined, nativeDisposed=false, nativeDraftOwner:string|undefined;
  const nativeController = nativeBridge ? new NativeCollaborationController(nativeBridge,value=>{setCollaborationState(value);setWindowMode(value.mode);const maximized=Reflect.get(value,'maximized');if(typeof maximized==='boolean')setNativeMaximized(maximized);if(typeof value.fullscreen==='boolean'&&(fullscreenPending===undefined||!value.fullscreen))syncFullscreenPanels(value.fullscreen);},setCurrentNativeContext,target=>{if(target){void quickReplyNavigation.open(target);return;}quickReplyNavigation.cancel();showAgentChat();setNativeMenu(false);focusComposer();}) : undefined;
  const [allProjects, setProjects] = createSignal<Project[]>([]); const projects=createMemo(()=>userProjects(allProjects())); const [project, setProject] = createSignal<Project>(); const [workspaces, setWorkspaces] = createSignal<Workspace[]>([]); const [workspace, setWorkspace] = createSignal<Workspace>();
  const [selection, setSelection] = createSignal<string[]>([]); const [materials, setMaterials] = createSignal<Material[]>([]);
  const [sourceChecks,setSourceChecks]=createSignal<SourceCheck[]>([]);const [sourceSyncs,setSourceSyncs]=createSignal<SourceSync[]>([]);const [sourceWatchError,setSourceWatchError]=createSignal('');const [invalidFeedback,setInvalidFeedback]=createSignal<Record<string,string>>({});
  const [books, setBooks] = createSignal<TaskBook[]>([]); const [harnesses, setHarnesses] = createSignal<Harness[]>([]); const [reports, setReports] = createSignal<ReportArtifact[]>([]); const [feed, setFeed] = createSignal<FeedItem[]>([]);
  const feedById=createMemo(()=>new Map(feed().map(item=>[item.id,item])));
  const [fullscreen,setFullscreen]=createSignal(false);const [fullscreenAgent,setFullscreenAgent]=createSignal(false);const [fullscreenFiles,setFullscreenFiles]=createSignal(false);
  const initialView=workspaceView(localStorage.getItem(WORKSPACE_VIEW_KEY));
  const [paneIntent,setPaneIntent]=createSignal<PaneWidthIntent>({files:savedPaneWidth(localStorage.getItem('eeas-file-width'),'files')??236,agent:savedPaneWidth(localStorage.getItem('eeas-agent-width-v2'),'agent')});
  const [readingAgentWidth,setReadingAgentWidth]=createSignal(savedPaneWidth(localStorage.getItem(READING_AGENT_WIDTH_KEY),'agent'));
  const initialPanes=projectPaneWidths(paneIntent(),window.innerWidth);
  const [fileWidth,setFileWidth]=createSignal(initialPanes.files),[agentWidth,setAgentWidth]=createSignal(initialPanes.agent);
  const resizePanes=()=>{const next=projectPaneWidths(paneIntent(),window.innerWidth,readingWorkspace()?'reading':'chat',readingAgentWidth());setFileWidth(next.files);setAgentWidth(next.agent);return next;};
  let fileWidthEdit=0;
  function changePaneWidth(kind:'files'|'agent',value:number){if(fullscreen()||windowMode()==='companion'||singlePane())return;const width=savedPaneWidth(String(value),kind);if(width===undefined)return;if(kind==='agent'&&readingWorkspace()){setReadingAgentWidth(width);resizePanes();localStorage.setItem(READING_AGENT_WIDTH_KEY,String(width));return;}if(kind==='files')fileWidthEdit++;setPaneIntent(current=>({...current,[kind]:width}));resizePanes();localStorage.setItem(kind==='files'?'eeas-file-width':'eeas-agent-width-v2',String(width));}
  createEffect(()=>{readingWorkspace();readingAgentWidth();if(!fullscreen()&&windowMode()==='workbench')untrack(resizePanes);});
  const [filesHidden,setFilesHidden]=createSignal(false),[agentHidden,setAgentHidden]=createSignal(initialView==='workbench'),[workbenchHidden,setWorkbenchHidden]=createSignal(initialView==='chat');
  const [mobileView,setMobileView]=createSignal<'workbench'|'agent'>(initialView==='workbench'?'workbench':'agent');
  const [singlePane,setSinglePane]=createSignal(window.innerWidth<=1100);
  const [pageReview,setPageReview]=createSignal<NativeReviewInput|null>(null),[reviewCatalog,setReviewCatalog]=createSignal<NativePageCatalog>(),[reviewHintSelection,setReviewHintSelection]=createSignal<NativePageSelection|null>(),[reviewSelecting,setReviewSelecting]=createSignal(false),[reviewContinue,setReviewContinue]=createSignal<string>(),[reviewUnknown,setReviewUnknown]=createSignal<string>(),[reviewRetryContext,setReviewRetryContext]=createSignal<NativeRef>();
  const [reviewCatalogError,setReviewCatalogError]=createSignal<{copy:UIErrorCopy;scope:QuickScope;selection:NativePageSelection|null}>();
  const [ordinaryNewQuestion,setOrdinaryNewQuestion]=createSignal(false),[ordinaryControlSending,setOrdinaryControlSending]=createSignal<string>();
  let reviewReadEpoch=0,reviewEditEpoch=0;const nativeObserved=new Map<string,string>();
  function currentReviewCatalogError(){const saved=reviewCatalogError();return saved&&sameReview(saved.scope,{...quickScope(),...(linkedFile()?{fileLinkRef:linkedFile()!.fileLinkRef}:{})})&&sameReview(saved.selection,pageReview()?.selection??null)?saved.copy:undefined;}
  function openReviewModelConnections(){if(composerMenu()!=='model')toggleComposerMenu('model');}
  async function readPageReview(open=false){
    const cid=conversationId(),epoch=++reviewReadEpoch,selection=pageReview()?.selection??null;
    const scope={...quickScope(),...(linkedFile()?{fileLinkRef:linkedFile()!.fileLinkRef}:{})};
    const current=()=>!studioDisposed&&epoch===reviewReadEpoch&&conversationId()===cid&&sameReview(scope,{...quickScope(),...(linkedFile()?{fileLinkRef:linkedFile()!.fileLinkRef}:{})})&&sameReview(selection,pageReview()?.selection??null);
    const fail=(copy:UIErrorCopy)=>{if(current())setReviewCatalogError({copy,scope,selection});};
    setReviewCatalogError(undefined);
    if(!nativeBridge?.getNativeReviewCatalog){fail('技能与工具目录暂不可用');return;}
    if(!scope.providerProfileRef){fail(authoredCopy('尚未选择模型。'));return;}
    try{const catalog=await nativeBridge.getNativeReviewCatalog({scope,selection,kind:'CAPABILITY_TASK'});
      if(!current())return;
      setReviewCatalog(catalog);setReviewHintSelection(selection);if(open){setReviewSelecting(true);setCapabilitiesOpen(true);}
    }catch(e){fail(authoredErrorCopy(e));}
  }
  async function openPageReview(){return readPageReview(true);}
  function changePageReview(selection:NativePageSelection|null){
    reviewReadEpoch++;reviewEditEpoch++;const value=pageReviewInput(selection);
    setPageReview(value);setReviewContinue(undefined);setReviewRetryContext(undefined);setReviewHintSelection(undefined);
    const cid=conversationId();try{if(cid)saveReviewDraft(localStorage,cid,value);}catch(e){setError(authoredErrorCopy(e));}
    if(value)void readPageReview();
  }
  function pageReviewLabel(){const input=pageReview(),catalog=reviewCatalog();if(!input)return '';if(input.version===2)return [catalog?.skills.find(row=>sameReview(row.config_ref,input.selection.skill_ref))?.title??(input.selection.skill_ref?uiText('技能'):''),input.selection.mcp_tool_selection.length?uiText('已选择 {count} 个工具',{count:input.selection.mcp_tool_selection.length}):''].filter(Boolean).join(' · ');const legacy=catalog?.schema==='eeas.native-capability-task-capabilities.v2'?catalog.legacy_review_catalog:catalog,selected=input.selection,tool=selected.tool_selection;return [legacy?.skills.find(row=>sameReview(row.config_ref,selected.skill_ref))?.title??selected.skill_ref?.id,(tool?.kind==='BUILTIN_READONLY'?uiText(legacy?.tools.find(row=>sameReview(row.selection,tool))?.title??tool.tool_ref.id):legacy?.tools.find(row=>sameReview(row.selection,tool))?.title??tool?.mcp_tool_selection.items[0]?.name)].filter(Boolean).join(' · ');}
  function reviewProviderForOperation(key:string){return readNativeHistory(localStorage,conversationId()).find(row=>row.operation_id===key)?.request?.providerProfileRef??quickRecords().find(row=>row.operationId===key&&sameQuickScope(row.scope,quickScope()))?.scope.providerProfileRef;}
  function ordinaryGuidanceParent(){
    const selected=reviewContinue();if(selected)return nativeMessages().find(row=>row.message.operation_id===selected&&ordinaryControlScope(row.message))?.message;
    if(pageReview()||ordinaryNewQuestion())return;
    const pending=nativeMessages().filter(row=>nativeMessagePending(row.message));
    return pending.length===1&&ordinaryControlScope(pending[0].message)?pending[0].message:undefined;
  }
  function ordinaryGuidanceAvailable(){const scope=ordinaryControlScope(ordinaryGuidanceParent());return !!scope&&(scope.can_guide||scope.can_status);}
  async function sendOrdinaryGuidance(parent:NativeMessage,rawText:string){
    const cid=conversationId();if(!cid||ordinaryControlSending())return;if(nativeDraftAttachments().length||pageReview()||!sameReview(reviewProviderForOperation(parent.operation_id),quickScope().providerProfileRef)){setError('此条暂不能补充，请核查原操作或新建问题');return;}
    setOrdinaryControlSending(parent.operation_id);try{
      const {result,submittedText}=await dispatchOrdinaryGuidance(localStorage,parent,cid,rawText,operationId(),controlPageReview);
      if(conversationId()!==cid)return;
      if(['ACCEPTED','OBSERVED','NEXT_TURN_ASSOCIATED','COHORT_RETURNED','RETURNED','COMPLETED'].includes(result.status)){if(prompt()===submittedText)setPrompt('');setNotice(controlNotice(result));setError('');}
      else setError(['FAILED_NOT_DISPATCHED','FAILED','REJECTED'].includes(result.status)?'此条补充未执行，内容已保留':'结果尚未确认，请核查原操作');
      const current=await nativeClient!.inspect(parent.operation_id);if(conversationId()!==cid)return;nativeRecord('',current);persistNativeHistory();if(nativeMessagePending(current))observeNativeMessage(current.operation_id,nativeMessages().find(row=>row.message.operation_id===current.operation_id)?.input??'');
    }catch(e){if(conversationId()===cid)setError(authoredErrorCopy(e));}finally{if(ordinaryControlSending()===parent.operation_id)setOrdinaryControlSending(undefined);}
  }
  function beginPageGuidance(message:NativeMessage){
    const ordinary=ordinaryControlScope(message);
    if(ordinary){if(!(ordinary.can_guide||ordinary.can_status)||prompt().trim()||nativeDraftAttachments().length||pageReview()){setError('此条暂不能补充，请核查原操作或新建问题');return;}setOrdinaryNewQuestion(false);setReviewContinue(message.operation_id);focusComposer();return;}
    const review=message.review as {version?:2;selection:NativePageSelection;can_guide:boolean;task_root_operation_id?:string}|undefined;
    if(!review?.can_guide||prompt().trim()||nativeDraftAttachments().length||!sameReview(reviewProviderForOperation(message.operation_id),quickScope().providerProfileRef)){setError('此条暂不能补充，请核查原操作或新建问题');return;}
    try{const value=pageReviewInput(review.selection,review.version===2?review.task_root_operation_id??null:null);reviewEditEpoch++;setPageReview(value);setReviewContinue(message.operation_id);setReviewRetryContext(undefined);focusComposer();}catch(e){setError(authoredErrorCopy(e));}
  }
  function continueCapabilityTask(message:NativeMessage){
    const review=message.review as {version?:number;selection?:NativePageSelection;task_root_operation_id?:string}|undefined;
    if(message.status!=='RETURNED'||review?.version!==2||!review.selection||!review.task_root_operation_id||!/^[A-Za-z0-9_-]{1,100}$/.test(review.task_root_operation_id)||reviewUnknown()||!sameReview(reviewProviderForOperation(message.operation_id),quickScope().providerProfileRef)){setError('此条暂不能补充，请核查原操作或新建问题');return;}
    if(prompt().trim()||nativeDraftAttachments().length||pageReview()&&!sameReview(pageReview()!.selection,review.selection)){setError('请先发送或清空当前补充');return;}
    try{reviewEditEpoch++;setPageReview(pageReviewInput(review.selection,review.task_root_operation_id));setReviewContinue(undefined);setReviewRetryContext(undefined);setCurrentNativeContext(undefined);setOrdinaryNewQuestion(true);void readPageReview();focusComposer();}catch(error){setError(authoredErrorCopy(error));}
  }
  async function controlPageReview(request:ReviewControl){if(!nativeBridge?.controlNativeReview)throw new Error('原生协作尚未就绪');return nativeBridge.controlNativeReview(request);}
  function newPageQuestion(){
    if(ordinaryControlSending())return;
    const continuation=pageReview();if(continuation?.version===2&&continuation.task_root_operation_id&&!reviewContinue()&&!reviewUnknown()){setPageReview({...resetReviewConsent(continuation),task_root_operation_id:null} as import('./native-review').NativeCapabilityTaskInput);setOrdinaryNewQuestion(true);void readPageReview();return;}
    if(prompt().trim()||nativeDraftAttachments().length){setError('请先发送或清空当前补充');return;}
    setOrdinaryNewQuestion(true);reviewEditEpoch++;setReviewUnknown(undefined);setReviewRetryContext(undefined);try{if(conversationId())localStorage.removeItem('eeas-native-review-unresolved:'+conversationId());}catch(e){setError(authoredErrorCopy(e));}
    setReviewContinue(undefined);setPrompt('');setNativeDraftAttachments([]);setCurrentNativeContext(undefined);
    if(pageReview()){const value=resetReviewConsent(pageReview()!);setPageReview(value.version===2?{...value,task_root_operation_id:null}:value);void readPageReview();}
  }
  const [capabilitiesOpen,setCapabilitiesOpen]=createSignal(false),[capabilitiesTab,setCapabilitiesTab]=createSignal<'tools'|'mcp'|'skills'>();
  const [toolSelection,setToolSelection]=createSignal<MCPToolSelectionDraft>();
  const [interactionVersion,setInteractionVersion]=createSignal(0);const interactions=new InteractionState(()=>setInteractionVersion(value=>value+1));const [agentToolVersion,setAgentToolVersion]=createSignal(0);const agentTools=new InteractionState(()=>setAgentToolVersion(value=>value+1));const [skillDrawer,setSkillDrawer]=createSignal(false);const [toolMode,setToolMode]=createSignal(localStorage.getItem('eeas-agent-tool-mode')==='discovery'?'discovery':'direct');const [directWrite,setDirectWrite]=createSignal(false);const [directResults,setDirectResults]=createSignal<DirectAction[]>([]);const [directInstructions,setDirectInstructions]=createSignal<Record<string,string>>({});
  const [directFile,setDirectFile]=createSignal<CompanionFile>();let nativeFeedbackQuoteEditEpoch=0;const [nativeSelectionCheck,setNativeSelectionCheck]=createSignal<{source:SourceRef;lease:string;token:number}>();let nativeSelectionToken=0;const [nativeFeedbackAction,setNativeFeedbackAction]=createSignal<FeedbackComposerAction>();const [nativeFeedbackSelection,setNativeFeedbackSelection]=createSignal<{basis:string;source:SourceRef;quote:string;selection:FeedbackSelection}>();const [directFileRequired,setDirectFileRequired]=createSignal(false);const [nativeFileFeedbacks,setNativeFileFeedbacks]=createSignal<NativeFileFeedback[]>([]);const [nativeReworkRecords,setNativeReworkRecords]=createSignal<ReworkRecord[]>([]);const [nativeReworkError,setNativeReworkError]=createSignal('');const [directFeedbackRefs,setDirectFeedbackRefs]=createSignal<Record<string,RevisionRef>>({});type OpenedFeedbackFile={source:SourceRef;scope:FileAssociationScope;generation:number;lease:string;link?:FileLinkRef;observedLink?:FileLinkRef;superseded?:boolean};const [openedFeedbackFile,setOpenedFeedbackFile]=createSignal<OpenedFeedbackFile>();const [suspendedFeedbackGeneration,setSuspendedFeedbackGeneration]=createSignal<number>();let openingFeedbackFile:OpenedFeedbackFile|undefined;let directFileGeneration=0;let feedbackRefreshGeneration=0;let directFileScope:FileAssociationScope|undefined;const [previousDirectFile,setPreviousDirectFile]=createSignal<CompanionFileDraft>();
  const [originalSession,setOriginalSession]=createSignal<AgentSession>(),[originalAgent,setOriginalAgent]=createSignal<OriginalAgent>('plan'),[originalOperations,setOriginalOperations]=createSignal<AgentSessionOperation[]>([]),[originalRecovery,setOriginalRecovery]=createSignal<OriginalOperationReview[]>([]);let originalGeneration=0;let originalTimer:ReturnType<typeof setTimeout>|undefined;
  const [commandDisplayCoverage,setCommandDisplayCoverage]=createSignal<OriginalCommandDisplayCoverage>();
  const commandContextKey=()=>JSON.stringify(selectedDiscussion()?.key??[project()?.id,conversationId()]);
  function observeCommandDisplay(value:OriginalCommandDisplayCoverage){
    if(value.entries.length){if(displayedCommandKeys(value,originalSession(),commandContextKey()).size)setCommandDisplayCoverage(value);}
    else{const previous=commandDisplayCoverage();if(previous&&sameCommandDisplayScope(previous,value))setCommandDisplayCoverage(undefined);}
  }
  const originalMessageRows=stableItems(()=>originalSession()?.messages??[],message=>JSON.stringify([originalSession()?.project_id,originalSession()?.session_ref,originalSession()?.generation,message.info.id,message.info.role]));
  const [feedbackRecords,setFeedbackRecords]=createSignal<FeedbackView[]>([]);const [draftAttachments,setDraftAttachments]=createSignal<FeedbackView[]>([]);const [draftReady,setDraftReady]=createSignal(false);const draftPersistence=new ComposerDraftPersistence(localStorage);
  const [discoverySessions,setDiscoverySessions]=createSignal<DiscoverySession[]>([]);const [activeSession,setActiveSession]=createSignal<DiscoverySession>();const [entryMode,setEntryMode]=createSignal<EntryMode>('PROBLEM');
  const [discoveryWaitSeconds,setDiscoveryWaitSeconds]=createSignal(readDiscoveryWait(localStorage));
  const [discoveryWaitError,setDiscoveryWaitError]=createSignal<UIErrorCopy>('');
  const [proposals,setProposals]=createSignal<PlanningProposal[]>([]);const [providers,setProviders]=createSignal<ProviderProfile[]>([]);const [providersReady,setProvidersReady]=createSignal(false);const [providerDialogArtifact,setProviderDialogArtifact]=createSignal<ArtifactEnvelope>();const [providerDialogOpen,setProviderDialogOpen]=createSignal(false);const [providerDialogReturn,setProviderDialogReturn]=createSignal<HTMLElement>();const [providerId,setProviderId]=createSignal('');const [planningMode,setPlanningMode]=createSignal<'opencode'|'source_outline'>('opencode');const [activeOperation,setActiveOperation]=createSignal<{id:string;projectId:string;workspaceId?:string;sessionId?:string;kind?:'planning'|'discovery'|'direct';status:string;execution?:PlanningOperation['native_execution'];events?:unknown[];basisRevision?:number;retainedInput?:DiscoveryInput;discoveryPurpose?:string;historicalInspection?:boolean;recoverySelection?:boolean}|undefined>();
  const selectedModelTitle=()=>{const profile=providers().find(item=>item.profile_ref.id===providerId());return profile?[profile.model_id,modelProfileConnectionHint(profile,providers())].filter(Boolean).join(' · '):uiText("选择模型连接");};
  const [discoveryError,setDiscoveryError]=createSignal<{projectId:string;sessionId:string;message:string}>();
  const [busy, setBusy] = createSignal(''); const [error, setError] = createSignal<UIErrorCopy>(''); const [notice, setNotice] = createSignal<UICopy>(''); const [inspector, setInspector] = createSignal<ArtifactEnvelope>(); const [contextTab, setContextTab] = createSignal('tasks');const [contextTray,setContextTray]=createSignal(false);const [compact,setCompact]=createSignal(window.matchMedia('(max-width:920px)').matches);
  const [folderDialog, setFolderDialog] = createSignal(false); const [folderLoading,setFolderLoading]=createSignal(false); const [folderTarget,setFolderTarget]=createSignal<'NEW'|'CURRENT'>('NEW'); const [folderProject,setFolderProject]=createSignal<Project>(); const [folderConnection,setFolderConnection]=createSignal<FolderConnectionIntent>(); let folderConnections:FolderConnection|undefined; let folderEpoch=0; const [folderPath, setFolderPath] = createSignal(''); const [projectName, setProjectName] = createSignal(''); const [writeScope, setWriteScope] = createSignal(false); const [prompt, setPrompt] = createSignal(readNativeText(localStorage,undefined)); const [unboundRecoveryText,setUnboundRecoveryText]=createSignal<string>(); const [sidebarOpen, setSidebarOpen] = createSignal(false); const [nativeOpen, setNativeOpen] = createSignal(false); const [nativeConfig, setNativeConfig] = createSignal<{ status: string; server_url?: string; reason?: string }>();
  const navigation = new NavigationOwner(); const registry = new ModuleRegistry(); registerBuiltins(registry,sourceHash);
  let fullscreenPending:boolean|undefined;let fullscreenRequest=0;let shell:HTMLDivElement|undefined;let sidebar:HTMLElement|undefined;let fullscreenScroll:{node:HTMLElement;top:number;left:number}[]=[];
  const [latestMessage,setLatestMessage]=createSignal<LatestMessagePosition>({visible:false,top:0});
  const conversationReading=new ConversationReading({container:()=>shell?.querySelector<HTMLElement>('.as-agent-content > .as-conversation')??undefined,changed:setLatestMessage,storage:localStorage});
  function conversationReadingScope(){const thread=threadScope();if(thread)return thread.conversation_ref.id;const value=activeSession();return value&&project()?.id===value.project_id?JSON.stringify(['discovery',value.project_id,value.id]):undefined;}
  function conversationReadingState():ConversationReadingScope|undefined{
    const thread=threadScope();if(thread)return {projectId:thread.project_id,kind:'conversation',id:thread.conversation_ref.id,revision:thread.conversation_ref.revision};
    const value=activeSession();return value&&project()?.id===value.project_id?{projectId:value.project_id,kind:'discovery',id:value.id,revision:value.revision,sha256:value.sha256}:undefined;
  }
  let readingEntryToken=0,readingEntryScope:string|undefined;
  function readingScopeIdentity(value:ConversationReadingScope|undefined){return value&&JSON.stringify([value.projectId,value.kind,value.id]);}
  function readingScopeCurrent(scope:ConversationReadingScope|undefined){const identity=readingScopeIdentity(scope);return()=>!studioDisposed&&!!identity&&readingScopeIdentity(conversationReadingState())===identity;}
  function enterConversationReading(scope:ConversationReadingScope|undefined,current:()=>boolean=navigation.capture()){
    if(!scope||!current())return;const stamp=conversationReading.entryStamp(),entry=++readingEntryToken;readingEntryScope=readingScopeIdentity(scope);
    queueMicrotask(()=>{const active=conversationReadingState();if(studioDisposed||entry!==readingEntryToken||!active||readingScopeIdentity(active)!==readingScopeIdentity(scope))return;
      const stableCurrent=readingScopeCurrent(active);
      const settled=()=>active.kind!=='discovery'||readingDeliveryReady()===JSON.stringify([active.projectId,active.id,active.revision,active.sha256]);
      if(stamp!==conversationReading.entryStamp()||!sameReview(scope,active)){if(!conversationReading.owns(active))conversationReading.enter(active,stableCurrent,settled,false);return;}
      conversationReading.enter(active,stableCurrent,settled);
    });
  }
  createEffect(()=>{const readingScope=conversationReadingState(),identity=readingScopeIdentity(readingScope);if(identity!==readingEntryScope){readingEntryScope=identity;readingEntryToken++;}conversationReading.sync(conversationReadingScope(),readingScope);});
  onCleanup(()=>conversationReading.clear());
  function readingWorkspace(){interactionVersion();return !!inspector()||interactions.current().some(item=>item.visible&&['proposal','discovery','taskbook','harness','local-file','report'].includes(item.artifact.type));}
  function layoutState():WorkspaceLayoutState{return {filesHidden:filesHidden(),agentHidden:agentHidden(),workbenchHidden:workbenchHidden(),fullscreen:fullscreen(),fullscreenAgent:fullscreenAgent(),fullscreenFiles:fullscreenFiles(),singlePane:singlePane(),mobileView:mobileView(),companion:windowMode()==='companion'};}
  function presentWorkspace(view:WorkspaceView){const restore=preserveLayoutReading(shell);if(mobileWorkspace(layoutState())){setMobileView(view==='chat'?'agent':'workbench');restore();return;}batch(()=>{setWorkbenchHidden(view==='chat');setAgentHidden(view==='workbench');setMobileView(view==='chat'?'agent':'workbench');});localStorage.setItem(WORKSPACE_VIEW_KEY,view);restore();}
  function toggleAgent(){if(fullscreen()){setFullscreenAgent(value=>!value);return;}if(mobileWorkspace(layoutState())){presentWorkspace(panelVisibility().agent?'workbench':'chat');return;}setAgentHidden(value=>!value);}
  createEffect(()=>{if(windowMode()==='workbench'&&!fullscreen())localStorage.setItem(WORKSPACE_VIEW_KEY,workbenchHidden()?'chat':agentHidden()?'workbench':'split');});
  function openCapabilityManager(tab?:'tools'|'mcp'|'skills'){setCapabilitiesTab(tab);setCapabilitiesOpen(true);setNativeMenu(false);setSkillDrawer(false);}
  function globalNavigationMenu(sidebarMenu:boolean){return <StudioPopover open={nativeMenu()} placement={sidebarMenu?'top-start':'bottom-end'} onOpenChange={setNativeMenu} onCloseAutoFocus={event=>{if(focusRestoredAgentTool()){event.preventDefault();return;}if(capabilitiesOpen()){event.preventDefault();return;}if(skillDrawer()){event.preventDefault();shell?.querySelector<HTMLElement>('#as-agent-skills > header button')?.focus({preventScroll:true});}}} label={uiText("协作与设置")} class="as-companion-menu" trigger={<button type="button" class={sidebarMenu?'as-agent-menu-trigger as-sidebar-tools':'as-agent-menu-trigger'} aria-label={sidebarMenu?(uiLocale()==='en'?'Tools and settings':'工具与设置'):uiText("协作菜单")} aria-expanded={nativeMenu()} aria-controls="as-companion-menu" onClick={()=>setNativeMenu(value=>!value)}><Icon name={sidebarMenu?'settings-gear':'menu'}/><Show when={sidebarMenu}><span>{uiLocale()==='en'?'Tools and settings':'工具与设置'}</span></Show></button>}><nav onKeyDown={event=>railKey(event,event.currentTarget)} class="as-companion-menu-body" id="as-companion-menu" aria-label={uiText("协作入口")}><InteractionRestoreMenuItems state={agentTools} version={agentToolVersion()} registry={registry} onRestore={restoredAgentTool}/><button type="button" aria-haspopup="dialog" onClick={()=>{shell?.querySelector<HTMLButtonElement>('.as-agent-menu-trigger')?.focus({preventScroll:true});openCapabilityManager();}}><Icon name="providers"/>{uiText("管理工具与 MCP")}</button><button type="button" disabled={!project()} onClick={()=>{setNativeMenu(false);void openCompetition();}}><Icon name="folder"/>{uiText("竞品资料")}</button><button type="button" disabled={!project()} onClick={()=>{setNativeMenu(false);void openExperiences();}}><Icon name="folder"/>{uiLocale()==='en'?'Templates and experience':'模板与经验'}</button><details class="as-menu-settings"><summary><Icon name="settings-gear"/>{uiText("设置")}</summary><UILanguageControl/><Show when={nativeBridge?.setAssistantOrbEnabled}><label><input type="checkbox" checked={orbEnabled()} disabled={orbSettingBusy()} onChange={event=>void changeOrbSetting(event.currentTarget.checked)}/><span>{uiText("后台显示悬浮球")}</span></label><button type="button" onClick={showNativeActions}>{uiLocale()==='en'?'Diagnostics':'诊断'}</button><Show when={orbSettingError()}><p role="alert">{uiText(orbSettingError())}</p></Show></Show></details></nav></StudioPopover>;}

  let reviewScopeIdentity:string|undefined;createEffect(()=>{const scope=JSON.stringify(quickScope());if(scope===reviewScopeIdentity)return;reviewScopeIdentity=scope;untrack(()=>{reviewReadEpoch++;reviewEditEpoch++;setReviewHintSelection(undefined);setReviewContinue(undefined);setPageReview(value=>value?resetReviewConsent(value):value);});});
  function chooseSessionTools(selection:MCPToolSelectionDraft){setToolSelection(selection);setCapabilitiesOpen(false);showOriginalSessionManager();}
  function panelVisibility(){const state=layoutState(),value=workspacePanels(state);return !nativeBridge&&workbenchHidden()&&!fullscreen()&&!mobileWorkspace(state)?{...value,agent:true}:value;}
  function historyDirectoryVisible(){return !document.hidden&&(windowMode()==='companion'?historyPopoverOpen():leftTab()==='conversations'&&panelVisibility().files&&(!compact()||sidebarOpen()||fullscreenFiles()));}
  createEffect(()=>historyDirectoryRefresh.setVisible(historyDirectoryVisible()));
  onMount(()=>{
    const focused=()=>historyDirectoryRefresh.invalidate(),visibility=()=>historyDirectoryRefresh.setVisible(historyDirectoryVisible());
    window.addEventListener('focus',focused);document.addEventListener('visibilitychange',visibility);
    onCleanup(()=>{window.removeEventListener('focus',focused);document.removeEventListener('visibilitychange',visibility);});
  });
  function syncFullscreenPanels(enabled:boolean){if(fullscreen()===enabled)return;if(enabled){if(!fullscreenScroll.length)fullscreenScroll=[...shell?.querySelectorAll<HTMLElement>('.as-inspector,.as-conversation')??[]].map(node=>({node,top:node.scrollTop,left:node.scrollLeft}));setFullscreen(true);setFullscreenAgent(false);setFullscreenFiles(false);setMobileView('workbench');return;}setFullscreen(false);setFullscreenAgent(false);setFullscreenFiles(false);const scroll=fullscreenScroll;fullscreenScroll=[];requestAnimationFrame(()=>{for(const value of scroll)if(value.node.isConnected){value.node.scrollTop=value.top;value.node.scrollLeft=value.left;}});}
  async function enterFullscreen(){const request=++fullscreenRequest;fullscreenPending=true;if(!fullscreen())fullscreenScroll=[...shell?.querySelectorAll<HTMLElement>('.as-inspector,.as-conversation')??[]].map(node=>({node,top:node.scrollTop,left:node.scrollLeft}));try{if(nativeBridge){await confirmedCollaborationFullscreen(nativeBridge,true);}else{if(!shell)throw new Error('工作台尚未就绪');await shell.requestFullscreen();if(document.fullscreenElement!==shell)throw new Error('浏览器尚未确认全屏');}if(request===fullscreenRequest)syncFullscreenPanels(true);}catch(error){if(request===fullscreenRequest){if(!fullscreen())fullscreenScroll=[];setError(authoredErrorCopy(error));}}finally{if(request===fullscreenRequest)fullscreenPending=undefined;}}
  async function leaveFullscreen(){const request=++fullscreenRequest;fullscreenPending=false;try{if(nativeBridge)await confirmedCollaborationFullscreen(nativeBridge,false);else if(document.fullscreenElement===shell)await document.exitFullscreen();if(request!==fullscreenRequest)return false;syncFullscreenPanels(false);return !fullscreen();}catch(error){if(request===fullscreenRequest)setError(authoredErrorCopy(error));return false;}finally{if(request===fullscreenRequest)fullscreenPending=undefined;}}
  let operationTimer:ReturnType<typeof setTimeout>|undefined;let operationObserver:AbortController|undefined;
  const scope = () => ({ projectId: project()?.id ?? '', workspaceId: workspace()?.id,sessionId:activeSession()?.id });
  const nativeAvailable = () => nativeConfig()?.status === 'READY' && !!nativeConfig()?.server_url;
  const activeRenderer = createMemo(() => inspector() ? registry.renderer(inspector()!)?.component ?? GenericRenderer : GenericRenderer);
  function inspect(value:ArtifactEnvelope|undefined,cause:PresentationCause='EXPLICIT_OPEN'){if(!value){setInspector(undefined);return;}if(value.type==='local-file'&&cause==='EXPLICIT_OPEN')void adoptDirectSource((value.data as FileContent).source_ref);const choice=presentationFor(value,cause);if(choice.destination==='DIALOG'){setProviderDialogReturn(document.activeElement instanceof HTMLElement?document.activeElement:undefined);setProviderDialogArtifact({...value,presentation:{...value.presentation,entryId:value.presentation?.entryId??operationId()}});setProviderDialogOpen(true);return;}if(choice.destination==='CHAT'){return;}if(cause==='EXPLICIT_OPEN'&&value.type==='report'&&nativeBridge?.openReportArtifact){const report=value.data as ReportArtifact;void action('打开实际产物',async()=>{const receipt=await nativeBridge.openReportArtifact!(value.scope.projectId,report.id,report.sha256,typeof report.download_url==='string'?report.download_url:api.reportDownload(value.scope.projectId,report.id),{scope:associationScope(quickScope())});const status=(receipt as {status?:string})?.status;if(status!=='OPEN_REQUESTED')throw new Error((receipt as {reason?:string})?.reason??'系统尚未确认产物打开请求');setNotice('系统已接收产物打开请求，可在原软件中继续处理');});return;}if(cause==='EXPLICIT_OPEN'&&windowMode()==='companion'&&(choice.destination==='WORKSPACE'||choice.mode==='FULL_WORKSPACE')){const current=navigation.capture(),pid=project()?.id;void changeNativeMode('workbench').then(confirmed=>{if(confirmed&&current()&&project()?.id===pid)inspect(value,cause);});return;}if(choice.destination==='AGENT_TOOL'){agentTools.request(value,choice.mode,choice.reason,true);setSkillDrawer(false);if(cause==='EXPLICIT_OPEN'&&compact())setSidebarOpen(false);setAgentHidden(false);setMobileView('agent');return;}navigation.begin();if(choice.destination==='WORKSPACE')setInspector(value);else interactions.request(value,choice.mode,choice.reason,cause==='EXPLICIT_OPEN');if(cause==='EXPLICIT_OPEN'){if(compact())setSidebarOpen(false);presentWorkspace('split');}const key=artifactStateKey(value);if(key&&workspaceStateOwner)workspaceStateOwner.set('view','active_key',key);}
  const interactionBlocking=createMemo(()=>{interactionVersion();return interactions.blocking();});const interactionPopover=createMemo(()=>{interactionVersion();return interactions.current().some(item=>item.visible&&item.request.mode==='MODELESS_POPOVER');});
  const context = { open: (value:ArtifactEnvelope)=>inspect(value), dispatch: (id: string,target: ArtifactEnvelope,input: unknown) => registry.dispatch(id,target,input,scope()) };
  function envelope(type: string,id: string,data: unknown,revision=1,hash?: string): ArtifactEnvelope { return { artifactId: id,type,schemaVersion:1,revision,scope:scope(),availability:'IMPLEMENTED',contentHash:hash,data }; }
  const pinnedObserver=new PinnedSourceObserver(value=>setPinnedObservation(value));createEffect(()=>{const ref=nativeFeedbackAvailable()?directFile()?.file.source_ref:undefined;pinnedObserver.update(ref?.project_id===project()?.id?ref:undefined);});onCleanup(()=>pinnedObserver.stop());
  function focusComposer(){closeComposerMenu();composerPresentation.compose();requestAnimationFrame(()=>shell?.querySelector<HTMLTextAreaElement>('.as-composer textarea')?.focus({preventScroll:true}));}
  const sourceWatcher=new SelectedSourceWatcher((pid,wid,refs,signal)=>api.sourceCheck(pid,wid,refs,signal),(pid,checks)=>{if(project()?.id===pid){const previous=sourceChecks(),changed=checks.filter(value=>{const old=previous.find(item=>sourceIdentity(item.source_ref)===sourceIdentity(value.source_ref));return old?old.status!==value.status||old.live_sha256!==value.live_sha256:value.status!=='CURRENT';});setSourceChecks(checks);setSourceWatchError('');if(changed.some(check=>exactFileSource(check.source_ref,directFile()?.file.source_ref)))void pinnedObserver.refresh();if(changed.some(check=>nativeFileFeedbacks().some(value=>sameSelectedFile(value.payload.baseline_file_source_ref,check.source_ref))))void refreshNativeFileFeedbacks(pid);}},(pid,reason)=>{if(project()?.id===pid)setSourceWatchError(message(reason));});
  createEffect(()=>{const pid=project()?.id??'',opened=inspector()?.type==='local-file'?(inspector()!.data as FileContent).source_ref:undefined;const refs=selectedSources(pid,opened,materials()),chosen=nativeFeedbackAvailable()?directFile()?.file.source_ref:undefined;if(chosen?.project_id===pid&&!refs.some(item=>sourceIdentity(item)===sourceIdentity(chosen)))refs.push(chosen);sourceWatcher.update(pid,refs);});onCleanup(()=>sourceWatcher.stop());
  function loadSourceContext(pid:string){setSourceChecks([]);setSourceWatchError('');try{const stored=JSON.parse(localStorage.getItem(`eeas-source-context:${pid}`)??'null');setSourceSyncs(Array.isArray(stored?.syncs)?stored.syncs.filter((item:SourceSync)=>item.current?.project_id===pid&&item.previous?.project_id===pid).slice(-12):[]);setInvalidFeedback(stored?.invalid&&typeof stored.invalid==='object'?stored.invalid:{});}catch{setSourceSyncs([]);setInvalidFeedback({});}}
  async function synchronizeSource(ref:SourceRef,canPublish:()=>boolean=()=>true){const owned=navigation.capture(),pid=ref.project_id,session=activeSession();if(materials().filter(item=>item.document_ref).length>=19&&!materials().some(item=>item.file_source_ref?.workspace_id===ref.workspace_id&&item.file_source_ref.path===ref.path))throw new Error('资料范围已达19份，请先明确本轮需要保留的资料');const result=await syncSelectedSource(api,ref);if(!canPublish())return result;if(!owned()||project()?.id!==pid)throw new Error('文件已导入新版本；工作区已切换，可返回原项目查看。');setMaterials(list=>[...list.filter(item=>!(item.file_source_ref?.workspace_id===ref.workspace_id&&item.file_source_ref.path===ref.path)),result.material]);setSourceSyncs(list=>[...list,result].slice(-12));const invalid={...invalidFeedback()};if(session&&activeSession()?.id===session.id){for(const feedback of feedbackRecords())invalid[feedback.feedback_ref.id]='关联资料已同步新版本，请在当前理解下重新定位批注';}setInvalidFeedback(invalid);localStorage.setItem(`eeas-source-context:${pid}`,JSON.stringify({syncs:sourceSyncs(),invalid}));if(fullscreen())setFullscreenAgent(true);else setAgentHidden(false);setMobileView('agent');setNotice('资料上下文已同步；工作台中的编辑内容保持原样，请核对新旧版本');return result;}
  function addFeed(title: string,body: string,status: string,artifact?: ArtifactEnvelope) { setFeed(value => [...value,{ id:operationId(),title,body,status,artifact }]); }
  const [readingDeliveryReady,setReadingDeliveryReady]=createSignal<string>();
  let readingDirectoryTurn=0;
  const deliveryRecovery=new DeliveryHistoryRecovery(planningDeliveryApi,localStorage,recordDeliveryFeed,(target,state,operationIds)=>{if(target.kind==='STAGE'){if(state==='PENDING')readingDirectoryTurn++;setReadingDeliveryReady(state==='COMPLETE'?JSON.stringify([target.projectId,target.basis.id,target.basis.revision,target.basis.sha256]):undefined);}setDeliveryDirectories(previous=>({...previous,[JSON.stringify([target.projectId,target.kind,target.basis.id])]:{state,operationIds:operationIds??[]}}));});
  const [deliveryDirectories,setDeliveryDirectories]=createSignal<Record<string,import('./delivery-recovery').DeliveryDirectoryView>>({});
  const [deliveryRefresh,setDeliveryRefresh]=createSignal(0);
  createEffect(()=>{deliveryDirectories();readingDeliveryReady();conversationReading.update();});
  function clearProjectFeed(){deliveryRecovery.resetView();readingDirectoryTurn++;setReadingDeliveryReady(undefined);setDeliveryDirectories({});setFeed([]);}
  async function inspectDeliveryFeedback(item:FeedItem){const target=item.deliveryTarget,inspection=item.deliveryInspection,current=navigation.capture(),pid=project()?.id,selected=activeSession(),view=selected?{id:selected.id,revision:selected.revision,sha256:selected.sha256}:undefined;if(!target||!inspection||pid!==target.projectId)return;return deliveryRecovery.read(target,inspection.operationId,()=>current()&&project()?.id===pid&&(view?activeSession()?.project_id===pid&&sameNativeReference(view,activeSession()):!activeSession()));}
  let observedDeliveryRefresh=0;
  createEffect(()=>{const refresh=deliveryRefresh(),force=refresh!==observedDeliveryRefresh;observedDeliveryRefresh=refresh;const pid=project()?.id??'',discoveryMode=toolMode()==='discovery',saved=pid&&discoveryMode?localStorage.getItem(`eeas-discovery-session:${pid}`):null,selected=selectedDeliverySession(pid,discoveryMode,activeSession(),saved,discoverySessions()),targets=pid&&!(discoveryMode&&!selected&&inspector()?.type!=='proposal')?loadedDeliveryTargets(pid,selected,proposals(),inspector()):[],current=navigation.capture();setFeed(list=>list.filter(item=>!item.deliveryTarget||item.deliveryTarget.projectId===pid));const restoration=deliveryRecovery.restore(targets,{refresh:force}),readingTurn=readingDirectoryTurn;void restoration.catch(()=>{if(!studioDisposed&&readingTurn===readingDirectoryTurn&&project()?.id===pid&&selected&&(!activeSession()||activeSession()?.project_id===pid&&sameNativeReference(selected,activeSession())))setReadingDeliveryReady(JSON.stringify([selected.project_id,selected.id,selected.revision,selected.sha256]));if(current()&&project()?.id===pid&&selected&&activeSession()?.project_id===pid&&sameNativeReference(selected,activeSession()))setError(uiLocale()==='en'?'Could not refresh delivery files. Saved receipts are retained; refresh the original discussion.':'交付文件暂未同步，已保存回执保留；请刷新原讨论。');});});
  onCleanup(()=>deliveryRecovery.stop());

  async function action(label: string,operation: () => Promise<unknown>) { if (busy()) return; setBusy(label); setError(''); setNotice(''); try { await operation(); } catch(e) { setError(authoredErrorCopy(e)); } finally { setBusy(''); } }
  const [projectCatalogState,setProjectCatalogState]=createSignal<ProjectCatalogStatus>({phase:'idle',failed:[]});
  const projectObjects=new ProjectObjectCatalog({
    books:(pid,signal)=>api.taskbooks(pid,signal),harnesses:(pid,signal)=>api.harnesses(pid,signal),
    reports:(pid,signal)=>api.reports(pid,signal),proposals:(pid,signal)=>api.proposals(pid,signal),
    sessions:(pid,signal)=>discoveryApi.list(pid,signal),
  },pid=>project()?.id===pid,()=>batch(()=>{setBooks([]);setHarnesses([]);setReports([]);setProposals([]);setDiscoverySessions([]);}),
  patch=>batch(()=>{if(patch.books)setBooks(patch.books);if(patch.harnesses)setHarnesses(patch.harnesses);if(patch.reports)setReports(patch.reports);if(patch.proposals)setProposals(patch.proposals);if(patch.sessions)setDiscoverySessions(patch.sessions);}),setProjectCatalogState,()=>untrack(()=>({books:books(),harnesses:harnesses(),reports:reports(),proposals:proposals(),sessions:discoverySessions()})));
  createEffect(()=>projectObjects.activate(project()?.id));
  onCleanup(()=>projectObjects.stop());
  async function refreshObjects(id:string,signal?:AbortSignal){
    if(signal?.aborted||project()?.id!==id)return;
    // Directory reads belong to the selected project, not the open document's navigation lease.
    await projectObjects.load(id);
  }
  function stopObserver(){if(operationTimer)clearTimeout(operationTimer);operationTimer=undefined;operationObserver?.abort();operationObserver=undefined;}
  function observeOperation(projectId:string,workspaceId:string,id:string){stopObserver();const controller=new AbortController();operationObserver=controller;setActiveOperation({id,projectId,workspaceId,status:'REQUESTING'});localStorage.setItem(`eeas-shell-operation:${projectId}`,JSON.stringify({id,workspaceId}));async function tick(){if(controller.signal.aborted)return;try{const result=await api.planningOperation(projectId,id,controller.signal);if(controller.signal.aborted)return;if('payload' in result){if(project()?.id===projectId&&workspace()?.id===workspaceId){setProposals(list=>[result,...list.filter(item=>item.id!==result.id)]);setNotice('拆解提案已返回，可继续检查、答复与编辑');}localStorage.removeItem(`eeas-shell-operation:${projectId}`);setActiveOperation(undefined);stopObserver();return;}setActiveOperation({id,projectId,workspaceId,status:result.status,execution:result.native_execution});if(result.status==='FAILED'||result.status==='CANCELLED'||result.status==='INTERRUPTED'){if(project()?.id===projectId)setError(result.message??'规划没有完成，请查看实际执行回执');localStorage.removeItem(`eeas-shell-operation:${projectId}`);stopObserver();return;}}catch(e){if(controller.signal.aborted)return;if(!(e instanceof ApiError&&e.status===404))setError(authoredErrorCopy(e));}operationTimer=setTimeout(()=>void tick(),1500);}void tick();}
  function sessionMaterials(value:DiscoverySession){const documents=(value.payload.source_receipt as {documents?:{document_id:string;filename?:string;local_file_source_ref?:SourceRef}[]})?.documents??[];return value.payload.materials.map(ref=>{const doc=documents.find(item=>item.document_id===ref.document_id);return{path:doc?.filename??ref.document_id,status:'SAVED',document_ref:ref,...doc?.local_file_source_ref?{file_source_ref:doc.local_file_source_ref}:{}} as Material;});}
  function saveComposerDraft(){if(selectedDiscussion()){if(!discussionDraftLease)throw new Error('DISCUSSION_DRAFT_UNAVAILABLE');try{discussionDraftLease.save(currentDiscussionDraft());}catch(error){if(error instanceof Error&&error.message==='DISCUSSION_DRAFT_CONFLICT'){const selected=selectedDiscussion()!,fresh=new DiscussionDraftLease(localStorage,selected.key);if(fresh.key===discussionDraftLease.key)recoverDraft(selected.key,currentDiscussionDraft());}throw error;}return;}const p=project();if(!p){const text=prompt();saveNativeText(localStorage,undefined,text);if(readNativeText(localStorage,undefined)!==text)throw new Error('未归属文字未能保存，当前工作保持不变');return;}if(!draftReady())return;draftPersistence.save(p.id,activeSession()?.id,prompt(),draftAttachments().map(item=>item.feedback_ref));}
  function restoreComposerDraft(value?:DiscoverySession,carry?:ComposerDraft){const pid=value?.project_id??project()?.id;if(!pid)return;setDraftReady(false);const lease=draftPersistence.begin(pid,value?.id,carry);batch(()=>{setPrompt(lease.draft.text);setDraftAttachments([]);draftPersistence.initialize(lease);setDraftReady(true);});if(value)void refreshFeedback(value,lease);else draftPersistence.hydrated(lease);}
  async function refreshFeedback(value:DiscoverySession,restore?:DraftRestore){try{const records=await workbenchApi.feedbackList(value.project_id,value.id);if(project()?.id===value.project_id&&activeSession()?.id===value.id){setFeedbackRecords(records);if(restore&&draftPersistence.current(restore)){const actual=hydrateDraftRefs(restore.draft.feedback_refs,records),current=draftAttachments();for(const item of current)if(!actual.some(saved=>saved.feedback_ref.id===item.feedback_ref.id))actual.push(item);draftPersistence.hydrated(restore);batch(()=>{setDraftAttachments(actual);setDraftReady(true);});}}}catch(e){if(project()?.id===value.project_id&&activeSession()?.id===value.id&&(!restore||draftPersistence.current(restore)))setError(authoredErrorCopy(e));}}
  createEffect(()=>{prompt();draftAttachments();nativeDraftAttachments();selectedDiscussion();if(selectedDiscussion()||!project()||draftReady())try{saveComposerDraft();}catch(reason){setError(authoredErrorCopy(reason));}});
  function activateSession(value:DiscoverySession,carry?:ComposerDraft){const target=carry??readComposerDraft(localStorage,value.project_id,value.id);setDraftReady(false);batch(()=>{setActiveSession(value);setMaterials(sessionMaterials(value));restoreComposerDraft(value,target);});enterConversationReading({projectId:value.project_id,kind:'discovery',id:value.id,revision:value.revision,sha256:value.sha256});}
  function startNewDiscovery(){chooseTool('discovery');saveComposerDraft();setDraftReady(false);draftPersistence.pause();navigation.begin();const pid=project()?.id;if(pid)localStorage.removeItem(`eeas-discovery-session:${pid}`);batch(()=>{setActiveSession(undefined);setFeedbackRecords([]);clearProjectFeed();restoreComposerDraft(undefined,{text:'',feedback_refs:[]});setSidebarOpen(false);});}
  function rememberSession(value:DiscoverySession,open=true){if(project()?.id!==value.project_id)return;setDiscoverySessions(list=>[value,...list.filter(item=>item.id!==value.id)]);if(activeSession()?.id===value.id){setActiveSession(value);setMaterials(sessionMaterials(value));localStorage.setItem(`eeas-discovery-session:${value.project_id}`,value.id);}void refreshFeedback(value);if(open)inspect(envelope('discovery',value.id,value,value.revision,value.sha256),'TOOL_RESULT');}
  async function openDiscovery(value:DiscoverySession){
    const owner=navigation.begin();
    try{
      saveComposerDraft();const latest=await discoveryApi.get(value.project_id,value.id,owner.signal);if(!owner.current())return;
      if(latest.project_id!==value.project_id||latest.id!==value.id)throw new Error('原版本依据不一致，草稿保留且未套用到新版本');
      await installDiscussionTarget({kind:'discovery',session:latest,navigation:{project_id:latest.project_id,workspace_ref:latest.payload.workspace_ref??null}},owner.current,owner.signal,'chat');
      if(!owner.current())return;
      if(windowMode()==='companion'&&!await changeNativeMode('workbench'))return;
      if(!owner.current())return;inspect(envelope('discovery',latest.id,latest,latest.revision,latest.sha256));setSidebarOpen(false);
    }catch(e){if(owner.current())setError(authoredErrorCopy(e));}
  }

  function restoreDiscoveryOperation(projectId:string,sessionId:string,operation:{operation_id:string;session_id:string;kind?:string;basis_revision?:number|null},explicit=false){
    if(project()?.id!==projectId||activeSession()?.id!==sessionId||operation.session_id!==sessionId||activeOperation())return;
    if(!explicit&&operation.basis_revision!==activeSession()?.revision)return;
    observeDiscovery(projectId,sessionId,operation.operation_id,{basisRevision:operation.basis_revision??undefined,discoveryPurpose:operation.kind,recoverySelection:explicit,historicalInspection:explicit&&operation.basis_revision!==activeSession()?.revision});
  }
  function releaseDiscoveryOperation(projectId:string,sessionId:string,key:string,completed=false){
    const owner=activeOperation();
    if(owner?.kind!=='discovery'||owner.id!==key||owner.projectId!==projectId||owner.sessionId!==sessionId)return;
    stopObserver();setActiveOperation(undefined);
    try{clearDiscoveryPending(localStorage,projectId,key);}
    catch{if(project()?.id===projectId&&activeSession()?.id===sessionId)setDiscoveryError({projectId,sessionId,message:completed?'结果已保存，本机恢复标记暂未清理。可以继续查看结果。':'较早的处理记录仍保留在本机，当前版本保持不变。'});}
  }
  function presentDiscoveryResult(value:DiscoverySession,open=false){
    try{rememberSession(value,open);}
    catch{if(project()?.id===value.project_id&&activeSession()?.id===value.id)setDiscoveryError({projectId:value.project_id,sessionId:value.id,message:'结果已保存，本机显示暂未同步。请重新打开本次工作查看。'});}
  }
  function applyDiscoveryObservation(projectId:string,sessionId:string,key:string,value:DiscoveryOperation){
    const owner=activeOperation();
    if(project()?.id!==projectId||owner?.id!==key||owner.projectId!==projectId||owner.sessionId!==sessionId||value.operation_id!==key||value.session_id!==sessionId)return false;
    const basis=value.basis_revision??owner.basisRevision;
    if(activeSession()?.id!==sessionId||!owner.historicalInspection&&basis!==activeSession()?.revision){
      releaseDiscoveryOperation(projectId,sessionId,key);return false;
    }
    const status=operationStatus({status:value.status,execution:value.native_execution});
    setActiveOperation({...owner,basisRevision:basis,discoveryPurpose:value.kind,status,execution:value.native_execution,events:value.events});
    if(status==='COMPLETED'){
      batch(()=>{
        releaseDiscoveryOperation(projectId,sessionId,key,true);
        if(value.result&&!owner.historicalInspection){const result='proposal' in value.result?value.result.session:value.result;presentDiscoveryResult(result);setNotice('工作结果已保存，可以继续检查和完善');}
      });
      return false;
    }
    if(['FAILED','CANCELLED','UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED'].includes(status)){
      // Preserve this exact key for read-only checking after reload/restart.
      stopObserver();return false;
    }
    return true;
  }
  async function inspectDiscoveryOperation(projectId:string,sessionId:string,key:string){
    const value=await discoveryApi.operation(projectId,key);
    if(activeOperation()?.id===key)setDiscoveryError(undefined);applyDiscoveryObservation(projectId,sessionId,key,value);return value;
  }
  function observeDiscovery(projectId:string,sessionId:string,key:string,context:{basisRevision?:number;retainedInput?:DiscoveryInput;discoveryPurpose?:string;historicalInspection?:boolean;recoverySelection?:boolean}={}){
    const saved=readDiscoveryPending(localStorage,projectId),previous=saved?.id===key&&saved.sessionId===sessionId?saved:undefined;
    const retainedInput=context.retainedInput??previous?.retainedInput??discoveryInput(prompt(),materials().flatMap(item=>item.document_ref?[item.document_ref]:[]),draftAttachments().map(item=>item.feedback_ref));
    const basisRevision=context.basisRevision??previous?.basisRevision;
    saveDiscoveryPending(localStorage,projectId,{id:key,sessionId,...(basisRevision!==undefined?{basisRevision}:{}),retainedInput});
    stopObserver();const controller=new AbortController();operationObserver=controller;
    setDiscoveryError(undefined);
    setActiveOperation({id:key,projectId,sessionId,kind:'discovery',status:'REQUESTING',basisRevision,retainedInput,discoveryPurpose:context.discoveryPurpose,historicalInspection:context.historicalInspection,recoverySelection:context.recoverySelection});
    async function tick(){
      if(controller.signal.aborted)return;
      try{const value=await discoveryApi.operation(projectId,key,controller.signal);if(controller.signal.aborted)return;if(!applyDiscoveryObservation(projectId,sessionId,key,value))return;}
      catch(e){if(controller.signal.aborted)return;if(!(e instanceof ApiError&&e.status===404)){stopObserver();setActiveOperation(operation=>operation?.id===key?{...operation,status:'UNKNOWN_OUTCOME'}:operation);setDiscoveryError({projectId,sessionId,message:'暂时无法核查本次处理。原记录与输入保留，请稍后再核查。'});return;}}
      operationTimer=setTimeout(()=>void tick(),1500);
    }
    void tick();
  }
  function followDiscoveryReading(value:DiscoverySession,key:string,current:()=>boolean){
    const id=conversationReadingScope();
    if(!id||project()?.id!==value.project_id||activeSession()?.id!==value.id)return;
    conversationReading.follow(id,key,()=>current()&&conversationReadingScope()===id&&project()?.id===value.project_id&&activeSession()?.id===value.id,
      ()=>[...shell?.querySelectorAll<HTMLElement>('.as-persistent-transcript')??[]].find(node=>node.dataset.projectId===value.project_id&&node.dataset.discoverySessionId===value.id),readingScopeCurrent(conversationReadingState()));
  }
  async function advanceDiscovery(value:DiscoverySession,messageText:string,feedbackRefs:RevisionRef[]=[],retainedInput?:DiscoveryInput){
    if(!workspace())throw new Error('先关联工作目录，再继续梳理。');
    const current=navigation.capture(),key=operationId(),refs=materials().flatMap(item=>item.document_ref?[item.document_ref]:[]);
    const input=retainedInput??discoveryInput(messageText,refs,feedbackRefs);
    if(discoverySendGate(activeOperation(),value.project_id,value.id,input)==='blocked')return;
    try{observeDiscovery(value.project_id,value.id,key,{basisRevision:value.revision,discoveryPurpose:'ADVANCE',retainedInput:input});}
    catch{setDiscoveryError({projectId:value.project_id,sessionId:value.id,message:'无法保存本机处理恢复记录。尚未发送，原输入保留。'});return;}
    followDiscoveryReading(value,key,current);
    try{const result=await discoveryApi.advance(value,messageText,{profile:providers().find(item=>item.profile_ref.id===providerId())?.profile_ref,workspace:workspace(),materials:refs.length?refs:undefined,feedbackRefs,timeoutSeconds:discoveryWaitSeconds()},key);
      if(!('payload' in result)){setNotice('操作正在处理，继续读取本次执行回执。');return;}
      if(project()?.id!==value.project_id||activeSession()?.id!==value.id)return;
      if(activeOperation()&&activeOperation()?.id!==key)return;
      if(activeSession()?.revision!==value.revision)return activeSession()?.revision===result.revision&&activeSession()?.sha256===result.sha256?result:undefined;
      batch(()=>{releaseDiscoveryOperation(value.project_id,value.id,key,true);presentDiscoveryResult(result,current());});
      try{await refreshObjects(value.project_id);}catch{setNotice('结果已保存，列表暂未刷新。');}return result;
    }catch(e){if(activeOperation()?.id===key){
        try{const observed=await inspectDiscoveryOperation(value.project_id,value.id,key);if(observed.status==='COMPLETED'&&observed.result&&project()?.id===value.project_id&&activeSession()?.id===value.id){const result='proposal' in observed.result?observed.result.session:observed.result;if(activeSession()?.revision===value.revision||activeSession()?.revision===result.revision&&activeSession()?.sha256===result.sha256)return result;}}
        catch{if(activeOperation()?.id===key){stopObserver();setActiveOperation(operation=>operation?{...operation,status:'UNKNOWN_OUTCOME'}:undefined);setDiscoveryError({projectId:value.project_id,sessionId:value.id,message:'暂时无法核查本次处理。原记录与输入保留，请稍后再核查。'});}}
      }return;}
  }
  async function refreshNative(id:string){try{const value=await api.request<{status:string;server_url?:string;reason?:string}>(`/api/agent-shell/native?project_id=${encodeURIComponent(id)}`);if(project()?.id===id)setNativeConfig(value);}catch(e){if(project()?.id===id)setNativeConfig({status:'UNAVAILABLE',reason:e instanceof ApiError&&e.status===404?'原生会话代理尚未启用':message(e)});}}
  async function selectProject(value:Project,options?:{restore?:boolean}){
    const unboundText=!project()&&!selectedDiscussion()?prompt():undefined;saveComposerDraft();if(!options?.restore)localStorage.removeItem('eeas-current-thread-v1');clearThread();const owner=navigation.begin();
    batch(()=>{
      setDraftReady(false);draftPersistence.pause();setDraftAttachments([]);setSelectedDiscussion(undefined);discussionDraftLease=undefined;setSupervisorReturn(undefined);setHistoryActiveId(undefined);
      stopObserver();setActiveOperation(undefined);setProject(value);projectObjects.activate(value.id);selectOriginalSession(undefined);void restoreOriginalSession(value.id);
      feedbackRefreshGeneration++;loadSourceContext(value.id);setDirectResults([]);setDirectFile(undefined);setDirectWrite(false);setNativeFileFeedbacks([]);setDirectFeedbackRefs({});
      void restoreDirectHistory(value.id,owner.current);void restoreSelectedDirectFile(value.id);setActiveSession(undefined);setFeedbackRecords([]);setNativeOpen(false);
      setNativeConfig({status:'UNAVAILABLE',reason:'正在检查当前项目的会话连接'});void refreshNative(value.id);
      setWorkspace(undefined);setInspector(undefined);setSelection([]);setMaterials([]);clearProjectFeed();setWorkspaces([]);setError('');
      const targetText=windowMode()==='companion'?readNativeText(localStorage,value.id):readComposerDraft(localStorage,value.id).text;
      const targetHasAttachments=windowMode()==='companion'?readNativeDraft(localStorage,value.id).length>0:readComposerDraft(localStorage,value.id).feedback_refs.length>0;
      const carry=unboundText&&!targetText&&!targetHasAttachments?{text:unboundText,feedback_refs:[]}:undefined;
      if(carry&&windowMode()==='companion')saveNativeText(localStorage,value.id,carry.text);
      restoreComposerDraft(undefined,carry);restoreNativeDraft(value.id);setUnboundRecoveryText(unboundText&&unboundText!==prompt()?unboundText:undefined);localStorage.setItem('eeas-shell-project',value.id);
    });
    try{
      const list=await api.workspaces(value.id,owner.signal);if(!owner.current())return;setWorkspaces(list);
      // The selected discussion owns its directory. A project preference may belong to an older discussion.
      const savedId=options?.restore&&toolMode()==='discovery'&&!localStorage.getItem('eeas-current-thread-v1')?localStorage.getItem(`eeas-discovery-session:${value.id}`):null;
      if(savedId){
        const saved=await discoveryApi.get(value.id,savedId,owner.signal);
        if(!owner.current()||composerInteracted)return;
        if(saved.project_id!==value.id||saved.id!==savedId)throw new Error('Saved requirements session does not match this project');
        const ref=saved.payload.workspace_ref,bound=ref?list.find(item=>item.id===ref.id&&item.revision===ref.revision&&item.binding_sha256===ref.binding_sha256):undefined;
        if(ref&&!bound){activateSession(saved);setError(authoredCopy('原工作目录的绑定已变化，未切换到其他目录'));return;}
        if(bound)await selectWorkspace(bound,owner,saved);
        else{activateSession(saved);await refreshObjects(value.id,owner.signal);}
        if(!owner.current()||activeSession()?.id!==saved.id)return;
        try{const pending=readDiscoveryPending(localStorage,value.id);if(pending&&pending.sessionId===saved.id&&(!pending.basisRevision||pending.basisRevision===saved.revision))observeDiscovery(value.id,pending.sessionId,pending.id);}
        catch{setDiscoveryError({projectId:value.id,sessionId:saved.id,message:'本机处理恢复记录暂不可用，原记录和输入保留。'});}
      }else if(list.length)await selectWorkspace(list.find(item=>item.id===localStorage.getItem(`eeas-shell-workspace:${value.id}`))??list[0],owner);
      else await refreshObjects(value.id,owner.signal);
    }
    catch(reason){if(owner.current())setError(authoredErrorCopy(reason));}
  }

  async function selectWorkspace(value:Workspace,parentOwner?:ReturnType<NavigationOwner['begin']>,restoredSession?:DiscoverySession){
    const owner=parentOwner??navigation.begin();if(!owner.current())return;
    const key=`eeas-shell-workspace:${value.project_id}`;localStorage.setItem(key,value.id);if(localStorage.getItem(key)!==value.id)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
    batch(()=>{stopObserver();setActiveOperation(undefined);setWorkspace(value);setSelection([]);setMaterials([]);setInspector(undefined);setSidebarOpen(false);if(restoredSession)activateSession(restoredSession);});
    try{await refreshObjects(value.project_id,owner.signal);if(!owner.current()||restoredSession)return;
      const raw=localStorage.getItem(`eeas-shell-operation:${value.project_id}`);if(raw){try{const op=JSON.parse(raw);if(op.workspaceId===value.id&&typeof op.id==='string')observeOperation(value.project_id,value.id,op.id);}catch{localStorage.removeItem(`eeas-shell-operation:${value.project_id}`);}}
    }catch(e){if(owner.current())setError(authoredErrorCopy(e));}
  }

  async function openFile(entry: TreeEntry) { const p=project(),w=workspace();if(!p||!w)return;const owner=navigation.begin();try{const value=await api.file(p.id,w.id,entry.path,owner.signal);if(!owner.current()||workspace()?.id!==w.id)return;inspect(envelope('local-file',entry.path,value,value.source_ref.revision,value.source_ref.sha256));setSidebarOpen(false);}catch(e){if(owner.current())setError(authoredErrorCopy(e));} }
  function sameEditorFile(a:SourceRef|undefined,b:SourceRef|undefined){return !!a&&!!b&&sameSelectedFile(a,b)&&a.binding_revision===b.binding_revision&&a.binding_sha256===b.binding_sha256;}
  async function reloadEditor(target:ArtifactEnvelope,input:{canPresent:()=>boolean;expected?:SourceRef}){
    const file=target.data as FileContent,source=file.source_ref,current=navigation.capture(),discussion=associationScope(quickScope());
    const owned=()=>current()&&sameDiscussionScope(discussion,quickScope())&&panelVisibility().workbench&&project()?.id===source.project_id&&workspace()?.id===source.workspace_id&&workspace()?.revision===source.binding_revision&&workspace()?.binding_sha256===source.binding_sha256&&inspector()?.type==='local-file'&&exactFileSource((inspector()!.data as FileContent).source_ref,source);
    if(!owned()||!input.canPresent())return {file,presented:false};
    const latest=await api.file(source.project_id,source.workspace_id,source.path);
    if(!latest.current||!sameEditorFile(latest.source_ref,source))throw new Error('原版本依据不一致，草稿保留且未套用到新版本');
    if(!owned()||!input.canPresent()||input.expected&&!exactFileSource(latest.source_ref,input.expected))return {file:latest,presented:false};
    if(exactFileSource(latest.source_ref,source))return {file:latest,presented:false};
    const focused=document.activeElement,returnFocus=focused instanceof HTMLElement&&focused.matches('[data-file-reload]');
    const value={...target,revision:latest.source_ref.revision,contentHash:latest.source_ref.sha256,data:latest};
    setInspector(value);
    const chosen=directFile();if(exactFileSource(chosen?.file.source_ref,source)){setDirectFile({...chosen!,file:latest});try{persistSelectedDirectFile();}catch(error){setError(authoredErrorCopy(error));}}
    const key=artifactStateKey(value);if(key&&workspaceStateOwner)workspaceStateOwner.set('view','active_key',key);
    if(returnFocus)queueMicrotask(()=>{if(current()&&exactFileSource((inspector()?.data as FileContent|undefined)?.source_ref,latest.source_ref)&&(document.activeElement===focused||document.activeElement===document.body))shell?.querySelector<HTMLButtonElement>('.as-cached-document:not([hidden]) [data-file-reload]')?.focus({preventScroll:true});});
    return {file:latest,presented:true};
  }
  function observeEditorApply(source:SourceRef){
    const target=inspector(),file=target?.type==='local-file'?target.data as FileContent:undefined,key=target&&artifactStateKey(target),session=key?objectDraft(key):undefined,fields=session?.fields,editor=shell?.querySelector<HTMLTextAreaElement>('.as-cached-document:not([hidden]) .as-file-editor'),epoch=editor?.dataset.editorEpoch;
    const inputUntouched=()=>!!editor&&editor.isConnected&&shell?.querySelector('.as-cached-document:not([hidden]) .as-file-editor')===editor&&editor.dataset.editorEpoch===epoch&&editor.dataset.editorComposing!=='true';
    const safe=!!file&&sameEditorFile(file.source_ref,source)&&panelVisibility().workbench&&!!session&&!session.dirty&&!['loading','error','conflict','unknown'].includes(session.status)&&inputUntouched(),current=navigation.capture(),discussion=associationScope(quickScope());
    return (record:ReworkRecord)=>{
      if(record.kind!=='APPLY'||record.status!=='APPLIED'||record.payload.project_id!==source.project_id||project()?.id!==source.project_id||!sameDiscussionScope(discussion,quickScope()))return;
      const outcome=record.payload.engine_receipt?.payload.outcomes.find(value=>['APPLIED','UNCHANGED'].includes(value.status)&&sameEditorFile(value.source_ref,source));if(!outcome?.source_ref)return;
      setNotice(authoredCopy('{path} · {label}{revision} · {sha} · {detail}',{path:outcome.source_ref.path,label:authoredCopy('实际更新版本 v'),revision:outcome.source_ref.revision,sha:outcome.source_ref.sha256.slice(0,12),detail:authoredCopy('当前显示历史版本，草稿保留。')}));
      if(safe&&target&&session)void reloadEditor(target,{expected:outcome.source_ref,canPresent:()=>current()&&session.fields===fields&&!session.dirty&&!['loading','error','conflict','unknown'].includes(session.status)&&inputUntouched()}).then(value=>{if(value.presented)setNotice('');}).catch(error=>{if(current()&&project()?.id===source.project_id&&sameDiscussionScope(discussion,quickScope())&&panelVisibility().workbench&&inspector()?.type==='local-file'&&exactFileSource((inspector()!.data as FileContent).source_ref,file!.source_ref))setError(authoredErrorCopy(error));});
    };
  }
  function openProjectArtifact(type:'proposal'|'taskbook',value:PlanningProposal|TaskBook){if(value.project_id!==project()?.id)return;setSkillDrawer(false);inspect(envelope(type,value.id,value,value.revision,value.sha256));}
  async function openHarness(harness: Harness) { if(harness.project_id!==project()?.id)return;const owner=navigation.begin();try{const state=await api.harnessState(harness.project_id,harness.id,harness.revision,owner.signal);if(owner.current()&&project()?.id===harness.project_id){if(state.revision!==undefined&&state.revision!==harness.revision||state.sha256!==undefined&&state.sha256!==harness.sha256)throw new Error('任务链状态不属于所选原版本');setSkillDrawer(false);inspect(envelope('harness',harness.id,{harness,state},harness.revision,harness.sha256));}}catch(e){if(owner.current())setError(authoredErrorCopy(e));} }
  registry.registerAction({ actionId:'workspace.file.save',moduleId:'eeas.local-file',label:'保存文件',availability:'IMPLEMENTED',scope:'workspace',requiresWrite:true },async(target,input)=>{
    const current=navigation.capture(),pid=target.scope.projectId,wid=target.scope.workspaceId!,file=target.data as FileContent,text=prompt(),sessionId=activeSession()?.id,discussion=associationScope(quickScope());
    const result=await api.edit(pid,wid,file,String(input));
    return {...result,syncContext:async(canPublish:()=>boolean)=>{const valid=()=>current()&&sameDiscussionScope(discussion,quickScope())&&panelVisibility().workbench&&inspector()?.type==='local-file'&&sameEditorFile((inspector()!.data as FileContent).source_ref,file.source_ref)&&project()?.id===pid&&workspace()?.id===wid&&workspace()?.revision===file.source_ref.binding_revision&&workspace()?.binding_sha256===file.source_ref.binding_sha256&&prompt()===text&&activeSession()?.id===sessionId&&canPublish();if(valid())try{await synchronizeSource(file.source_ref,valid);}catch(error){if(valid())setError(authoredCopy('{saved}{version}{revision} · {context}: {detail}',{saved:authoredCopy('已保存 · '),version:authoredCopy('文件版本 '),revision:result.source_ref.revision,context:authoredCopy('同步原件'),detail:authoredErrorCopy(error)}));throw error;}}};
  });
  registry.registerAction({actionId:'workspace.file.reload',moduleId:'eeas.local-file',label:'读取当前版本',availability:'IMPLEMENTED',scope:'workspace',requiresWrite:false},async(target,input)=>reloadEditor(target,input as {canPresent:()=>boolean;expected?:SourceRef}));
  registry.registerAction({ actionId:'taskbook.save',moduleId:'eeas.taskbook',label:'保存任务书',availability:'IMPLEMENTED',scope:'project',requiresWrite:true },async(target,input)=>{const current=navigation.capture(),result=await api.saveBook(target.data as TaskBook,input as TaskBook['payload']['sections']);if(current()&&project()?.id===target.scope.projectId){await refreshObjects(target.scope.projectId);if(current()){inspect(envelope('taskbook',result.id,result,result.revision,result.sha256));setNotice(authoredCopy('已保存任务书版本 {revision}',{revision:result.revision}));}}return result;});
  async function updateProposal(original:PlanningProposal,operation:()=>Promise<unknown>){const current=navigation.capture();await operation();const result=await api.proposal(original.project_id,original.id);if(current()&&project()?.id===original.project_id){inspect(envelope('proposal',result.id,result,result.revision,result.sha256));await refreshObjects(original.project_id);}return result;}
  registry.registerAction({actionId:'workspace.file.refresh',moduleId:'eeas.local-file',label:'同步原件',availability:'IMPLEMENTED',scope:'workspace',requiresWrite:false},async(target)=>{const file=target.data as FileContent;return synchronizeSource(file.source_ref);});
  registry.registerAction({actionId:'native.capture.draft',moduleId:'eeas.local-file',label:'加入实际窗口截图',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target,input)=>{const frame=input as NativeFrame;if(frame.frame_ref.observed_file_source_ref.project_id!==target.scope.projectId)throw new Error('截图文件不属于当前项目');const ref={document_id:frame.image.document_id,revision:frame.image.revision,sha256:frame.image.sha256};if(!materials().some(item=>item.document_ref?.document_id===ref.document_id))setMaterials(list=>[...list,{path:`原软件截图 · ${frame.frame_ref.frame_revision}`,status:'SAVED',document_ref:ref}]);setPrompt(value=>value+(value?'\n\n':'')+'已加入原软件实际截图。文字尚未定位，请先确认可处理的内容和需要补充的说明。');if(fullscreen())setFullscreenAgent(true);else setAgentHidden(false);setMobileView('agent');return ref;});
  registry.registerAction({actionId:'native.capture.feedback',moduleId:'eeas.local-file',label:'保存画面批注',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const owned=navigation.capture(),value=input as {frame:NativeFrame;feedback:BitmapSubmission},file=target.data as FileContent;if(!value.frame.bitmap_ref||value.frame.frame_ref.observed_file_source_ref.project_id!==target.scope.projectId)throw new Error('画面缺少同项目的权威捕获绑定');let original=activeSession();if(!original){const imported=await api.materials(target.scope.projectId,file.source_ref.workspace_id,[file.source_ref.path]);const refs=imported.materials.flatMap(item=>item.document_ref?[item.document_ref]:[]);refs.push({document_id:value.frame.image.document_id,revision:value.frame.image.revision,sha256:value.frame.image.sha256});original=await discoveryApi.create(target.scope.projectId,{title:file.source_ref.path.split('/').at(-1)??'文稿反馈',entry_mode:'REQUIREMENTS',statement:value.feedback.comment,materials:refs,workspace:workspace()});if(!owned()||project()?.id!==target.scope.projectId)throw new Error('工作区已切换，已保存的需求工作可从原项目重新打开。');activateSession(original,{text:prompt(),feedback_refs:[]});rememberSession(original,false);}return registry.dispatch('workbench.feedback',envelope('discovery',original.id,original,original.revision,original.sha256),value.feedback,scope());});
  registry.registerAction({actionId:'bitmap.file.feedback',moduleId:'eeas.local-file',label:'保存图片批注',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const owned=navigation.capture(),file=target.data as FileContent,feedback=input as BitmapSubmission;if(feedback.selection.bitmap_ref.kind!=='LOCAL_FILE'||feedback.selection.bitmap_ref.sha256!==file.source_ref.sha256)throw new Error('图片批注未绑定当前原文件');let original=activeSession();if(!original){const imported=await api.materials(target.scope.projectId,file.source_ref.workspace_id,[file.source_ref.path]);original=await discoveryApi.create(target.scope.projectId,{title:file.source_ref.path.split('/').at(-1)??'图片反馈',entry_mode:'REQUIREMENTS',statement:feedback.comment,materials:imported.materials.flatMap(item=>item.document_ref?[item.document_ref]:[]),workspace:workspace()});if(!owned()||project()?.id!==target.scope.projectId)throw new Error('工作区已切换，已保存的需求工作可从原项目重新打开。');activateSession(original,{text:prompt(),feedback_refs:[]});rememberSession(original,false);}return registry.dispatch('workbench.feedback',envelope('discovery',original.id,original,original.revision,original.sha256),feedback,scope());});
  registry.registerAction({actionId:'proposal.save',moduleId:'eeas.proposal',label:'保存提案',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const proposal=target.data as PlanningProposal;const value=input as {candidate:PlanningCandidate;reason:string;operationId?:string;manualReview?:ManualReview;requirementCoverage?:ClaimCoverageDraft};if(value.manualReview||value.requirementCoverage){if(!value.operationId)throw new Error(value.manualReview?'原人工审阅保存操作缺失':'原保存操作缺失');return api.editProposal(proposal,value.candidate,value.reason,value.operationId,value.manualReview,value.requirementCoverage);}return updateProposal(proposal,()=>api.editProposal(proposal,value.candidate,value.reason));});
  registry.registerAction({actionId:'proposal.manual.capture',moduleId:'eeas.proposal',label:'核对人工修改',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>workbenchApi.captureManual(target.scope.projectId,target.artifactId,input as ManualCaptureRequest));
  registry.registerAction({actionId:'proposal.answer',moduleId:'eeas.proposal',label:'保存答复',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const proposal=target.data as PlanningProposal;const value=input as {questionId:string;text:string};return updateProposal(proposal,()=>api.answerProposal(proposal,value.questionId,value.text));});
  registry.registerAction({actionId:'proposal.confirm',moduleId:'eeas.proposal',label:'确认计划',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const proposal=target.data as PlanningProposal;if(Array.isArray(input))return updateProposal(proposal,()=>api.confirmProposal(proposal,input));const value=input as {operationId:string;resolutions:unknown[];coverageSha256:string;confirmedExclusionIds:string[]};if(!value?.operationId)throw new Error('原确认操作缺失');return api.confirmProposal(proposal,value.resolutions,value.operationId,{coverage_sha256:value.coverageSha256,confirmed_exclusion_ids:value.confirmedExclusionIds});});
  registry.registerAction({actionId:'proposal.taskbook',moduleId:'eeas.proposal',label:'生成任务书',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target)=>{const proposal=target.data as PlanningProposal;if(proposal.project_id!==project()?.id)throw new Error('提案不属于当前项目，请重新打开核对');const owner=navigation.begin();const result=await api.request<{taskbook_id:string;revision:number;sha256:string}>(`${api.projectPath(proposal.project_id)}/planning/proposals/${encodeURIComponent(proposal.id)}/taskbook?revision=${proposal.revision}`,'POST',undefined,undefined,false);if(!owner.current()||project()?.id!==proposal.project_id)return result;if(!result||typeof result.taskbook_id!=='string'||!result.taskbook_id||!Number.isSafeInteger(result.revision)||result.revision<1||!/^[a-f0-9]{64}$/.test(result.sha256))throw new Error('任务书生成回执不完整，请从产物列表核对原结果');const book=await api.request<TaskBook>(`${api.projectPath(proposal.project_id)}/taskbooks/${encodeURIComponent(result.taskbook_id)}?revision=${result.revision}`,'GET',undefined,owner.signal);if(!owner.current()||project()?.id!==proposal.project_id)return result;if(book.project_id!==proposal.project_id||book.id!==result.taskbook_id||book.revision!==result.revision||book.sha256!==result.sha256)throw new Error('任务书内容与原生成版本不一致，请从产物列表核对');await refreshObjects(proposal.project_id,owner.signal);if(owner.current()&&project()?.id===proposal.project_id){openProjectArtifact('taskbook',book);setNotice(authoredCopy('已打开任务书版本 {revision}，可继续审阅和编辑',{revision:book.revision}));}return result;});
  for(const [actionId,path] of [['proposal.validate-network','validate-network'],['proposal.acceptance-coverage','acceptance-coverage']] as const)registry.registerAction({actionId,moduleId:'eeas.proposal',label:'检查提案',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(_target,input)=>api.request(`/api/planning/tools/${path}`,'POST',input));
  registry.registerAction({actionId:'proposal.report',moduleId:'eeas.proposal',label:'生成报告',availability:'IMPLEMENTED',scope:'workspace',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),proposal=target.data as PlanningProposal,value=input as {format:'docx';templatePath?:string;templateRef?:SourceRef};let template=value.templateRef;if(template&&template.project_id!==proposal.project_id)throw new Error('模板不属于当前项目');if(!template&&value.templatePath){template=(await api.file(target.scope.projectId,target.scope.workspaceId!,value.templatePath)).source_ref;}const result=await api.request<{artifact:ReportArtifact;pending:{download_url:string};status:string}>(`${api.projectPath(proposal.project_id)}/planning/proposals/${encodeURIComponent(proposal.id)}/reports`,'POST',{operation_id:operationId(),expected_revision:proposal.revision,format:value.format,...template?{template_ref:template}:{}});if(current()){const artifact={...result.artifact,pending_download_url:result.pending.download_url};setReports(list=>[artifact,...list.filter(item=>item.id!==artifact.id)]);addFeed('报告已生成',uiText(result.status==='DRAFT'?'草稿 · 待确认问题单独保留':'已确认计划的报告'),'SAVED',envelope('report',artifact.id,artifact,1,artifact.sha256));}return result;});
  registry.registerAction({actionId:'proposal.harness.export',moduleId:'eeas.proposal',label:'导出执行提示词',availability:'IMPLEMENTED',scope:'workspace',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),proposal=target.data as PlanningProposal,w=workspace()!,value=input as {directory:string};const result=await api.request(`${api.projectPath(proposal.project_id)}/planning/proposals/${encodeURIComponent(proposal.id)}/harness/export`,'POST',{operation_id:operationId(),expected_revision:proposal.revision,proposal_sha256:proposal.sha256,workspace_ref:{id:w.id,revision:w.revision,binding_sha256:w.binding_sha256},directory:value.directory});if(current()){inspect(envelope('harness-export',proposal.id,result));addFeed('执行文件已保存',value.directory,'SAVED');}return result;});
  registry.registerAction({actionId:'discovery.save',moduleId:'eeas.discovery',label:'保存需求文稿',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),value=input as {candidate:DiscoveryCandidate;reason:string};const result=await discoveryApi.edit(target.data as DiscoverySession,value.candidate,value.reason);rememberSession(result,current());return result;});
  registry.registerAction({actionId:'discovery.answer',moduleId:'eeas.discovery',label:'保存回答',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),value=input as {questionId:string;text:string},original=target.data as DiscoverySession;await discoveryApi.answer(original,value.questionId,value.text);const result=await discoveryApi.get(original.project_id,original.id);rememberSession(result,current());return result;});
  registry.registerAction({actionId:'discovery.confirm',moduleId:'eeas.discovery',label:'确认要求',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),value=input as {claims:string[];graphs:string[];resolutions:Resolution[]};const result=await discoveryApi.confirm(target.data as DiscoverySession,value.claims,value.graphs,value.resolutions);rememberSession(result,current());return result;});
  registry.registerAction({actionId:'discovery.advance',moduleId:'eeas.discovery',label:'继续共同梳理',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>advanceDiscovery(target.data as DiscoverySession,(input as {message:string}).message));
  registry.registerAction({actionId:'discovery.plan',moduleId:'eeas.discovery',label:'生成任务计划',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{
    const current=navigation.capture(),original=target.data as DiscoverySession,experience=(input as {experience?:RevisionRef})?.experience,timeoutSeconds=(input as {timeoutSeconds?:unknown})?.timeoutSeconds??300;
    if(!isPlanWaitSeconds(timeoutSeconds))throw new Error('请选择 2、5 或 10 分钟的等待预算。');
    const previous=activeOperation();
    if(previous?.kind==='discovery'&&previous.projectId===original.project_id&&previous.sessionId===original.id&&previous.basisRevision===original.revision&&!previous.historicalInspection&&previous.discoveryPurpose==='PLAN'&&!['COMPLETED','FAILED','CANCELLED'].includes(previous.status))return;
    const key=operationId();
    try{observeDiscovery(original.project_id,original.id,key,{basisRevision:original.revision,discoveryPurpose:'PLAN'});}
    catch{setDiscoveryError({projectId:original.project_id,sessionId:original.id,message:'无法保存本机处理恢复记录。尚未发送，原输入保留。'});return;}
    try{const result=await discoveryApi.plan(original,providers().find(value=>value.profile_ref.id===providerId())?.profile_ref,key,experience,timeoutSeconds,'ACCEPTED_CLAIMS_V1');if(!('proposal' in result))return result;
      if(project()?.id!==original.project_id||activeSession()?.id!==original.id||activeOperation()&&activeOperation()?.id!==key)return;
      if(activeSession()?.revision!==original.revision&&!(activeSession()?.revision===result.session.revision&&activeSession()?.sha256===result.session.sha256))return;
      batch(()=>{releaseDiscoveryOperation(original.project_id,original.id,key,true);presentDiscoveryResult(result.session);});if(current())try{inspect(envelope('proposal',result.proposal.id,result.proposal,result.proposal.revision,result.proposal.sha256));}catch{setDiscoveryError({projectId:original.project_id,sessionId:original.id,message:'计划已保存，查看面板暂未打开。可以从当前工作重新查看。'});}try{await refreshObjects(original.project_id);}catch{setNotice('结果已保存，列表暂未刷新。');}return result;
    }catch{if(activeOperation()?.id===key){try{const observed=await inspectDiscoveryOperation(original.project_id,original.id,key);if(observed.status==='COMPLETED'&&observed.result&&'proposal' in observed.result&&project()?.id===original.project_id&&activeSession()?.id===original.id&&(activeSession()?.revision===original.revision||activeSession()?.revision===observed.result.session.revision&&activeSession()?.sha256===observed.result.session.sha256))return observed.result;}catch{if(activeOperation()?.id===key){stopObserver();setActiveOperation(operation=>operation?{...operation,status:'UNKNOWN_OUTCOME'}:undefined);setDiscoveryError({projectId:original.project_id,sessionId:original.id,message:'暂时无法核查本次处理。原记录与输入保留，请稍后再核查。'});}}}return;}
  });
  registry.registerAction({actionId:'discovery.open-plan',moduleId:'eeas.discovery',label:'查看任务计划',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target)=>{const current=navigation.capture(),original=target.data as DiscoverySession,ref=original.payload.proposal_ref;if(!ref)throw new Error('当前版本尚未关联任务计划');const value=await api.proposal(original.project_id,ref.id);if(current())inspect(envelope('proposal',value.id,value,value.revision,value.sha256));return value;});
  registry.registerAction({actionId:'provider.save',moduleId:'eeas.providers',label:'保存模型连接',availability:'IMPLEMENTED',scope:'global',requiresWrite:true},async(_target,input)=>api.request<ProviderProfile>('/api/planning/providers','POST',input,AbortSignal.timeout(45000),false));
  registry.registerAction({actionId:'provider.inspect',moduleId:'eeas.providers',label:'回查连接保存',availability:'IMPLEMENTED',scope:'global',requiresWrite:false},async(_target,input)=>{const id=(input as {operation_id?:unknown})?.operation_id;if(typeof id!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(id))throw new Error('请核对原连接保存操作');return api.request('/api/planning/providers/operations/'+encodeURIComponent(id));});
  registry.registerAction({actionId:'provider.refresh',moduleId:'eeas.providers',label:'刷新模型连接',availability:'IMPLEMENTED',scope:'global',requiresWrite:false},async(_target,input)=>{const current=navigation.capture(),list=await api.providers();setProviders(list);setProvidersReady(true);const id=(input as {select_id?:unknown})?.select_id;if(typeof id==='string'&&list.some(profile=>profile.profile_ref.id===id)){setProviderId(id);localStorage.setItem('eeas-provider-id',id);}const owned=providerDialogArtifact();if(current()&&owned&&panelKey(owned)===panelKey(_target)&&owned.presentation?.entryId===_target.presentation?.entryId){setProviderDialogArtifact({...owned,data:list});if(typeof id==='string'&&list.some(profile=>profile.profile_ref.id===id))setProviderDialogOpen(false);}return list;});
  async function openProviders(kind?:'subscription'|'api'){const opener=document.activeElement instanceof HTMLElement?document.activeElement:undefined;closeComposerMenu();await action('读取模型连接',async()=>{const current=navigation.capture();const list=await api.providers();setProviders(list);setProvidersReady(true);if(current()){const selected=list.find(profile=>profile.profile_ref.id===providerId());inspect({...envelope('providers','providers',list),presentation:{entryId:operationId(),initialTab:kind??(selected?(selected.auth_kind==='OAUTH_SUBSCRIPTION'?'subscription':'api'):undefined)}});setProviderDialogReturn(opener);}});}
  async function openCapabilities(){openCapabilityManager();}
  registry.registerAction({actionId:'capabilities.manage',moduleId:'eeas.capabilities',label:'管理工具',availability:'IMPLEMENTED',scope:'global',requiresWrite:false},async()=>openCapabilityManager());


  async function reviseRecordedFeedback(value:FeedbackView){if(invalidFeedback()[value.feedback_ref.id])throw new Error('关联来源已更新，此旧批注需要重新定位');const original=activeSession();if(!original||value.payload.revision_target_ref.id!==original.id)throw new Error('批注不属于当前工作');if(!workspace())throw new Error('反馈已保存；关联工作目录后可让 Agent 修订');const current=navigation.capture(),key=operationId();observeDiscovery(original.project_id,original.id,key);try{const result=await workbenchApi.reviseFeedback(original.project_id,value,{profile:providers().find(item=>item.profile_ref.id===providerId())?.profile_ref,workspace:workspace()},key);if('payload' in result){if(activeOperation()?.id===key){stopObserver();setActiveOperation(undefined);localStorage.removeItem(`eeas-discovery-operation:${original.project_id}`);}rememberSession(result,current());setNotice('Agent 已基于已保存的反馈生成新版本，请核对修改内容与来源');}return result;}catch(e){if(e instanceof ApiError&&activeOperation()?.id===key){stopObserver();setActiveOperation(operation=>operation?{...operation,status:'FAILED'}:undefined);}throw new Error(`反馈已保存（${value.feedback_ref.id}），修订尚未完成：${message(e)}`);}}
  registry.registerAction({actionId:'workbench.feedback',moduleId:'eeas.discovery',label:'保存反馈',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{
    const original=target.data as DiscoverySession,current=navigation.capture(),submitted=input as AnnotationSubmission|BitmapSubmission|TextAnnotation|{tool:'TEXT_EDIT';comment:string;edited_candidate:DiscoveryCandidate;delivery?:'DRAFT'|'SEND'};const {delivery='SEND',...body}=submitted;if(delivery==='DRAFT'&&draftPersistence.hasPending(original.project_id,original.id))throw new Error('正在恢复已有草稿批注，请等待后再加入新批注。');const receipt=await workbenchApi.recordFeedback(original,body);
    if(project()?.id!==original.project_id||activeSession()?.id!==original.id)return receipt;rememberSession(receipt.session,false);setFeedbackRecords(list=>[...list.filter(item=>item.feedback_ref.id!==receipt.feedback_ref.id),receipt]);addFeed('反馈已保存',receipt.payload.comment,'RECORDED');if(!current())return receipt;
    if(delivery==='DRAFT'){const next=appendFeedbackDraft(prompt(),draftAttachments(),receipt);setPrompt(next.text);setDraftAttachments(next.feedback);setDraftReady(true);if(fullscreen())setFullscreenAgent(true);else setAgentHidden(false);setMobileView('agent');setNotice('批注已加入聊天草稿；保留原有文字，编辑完整消息后再发送。');if(body.tool==='TEXT_EDIT')rememberSession(receipt.session,true);return receipt;}
    await reviseRecordedFeedback(receipt);return receipt;
  });
  async function openCompetition(){if(!project())return;await action('读取竞品资料',async()=>{const current=navigation.capture(),pid=project()!.id;const sources=await workbenchApi.captures(pid);if(current())inspect(envelope('competition','competition',{sources,materials:materials()}));setSkillDrawer(false);});}
  async function openExperiences(target?:ArtifactEnvelope){if(!project())return;const fromDrawer=skillDrawer();const originOpen=()=>target?[...interactions.current(),...agentTools.current()].some(item=>item.visible&&panelKey(item.artifact)===panelKey(target))||!!inspector()&&panelKey(inspector()!)===panelKey(target):!fromDrawer||skillDrawer();await action('读取模板经验',async()=>{const current=navigation.capture(),pid=project()!.id;const inventory=await workbenchApi.profileSummaries(pid);if(current()&&originOpen())inspect(envelope('experience','experience',{...inventory,projects:projects().filter(item=>!!startupProject([item],item.id)),projectName:project()!.name,workspace:workspace()},1,WRITING_PANEL_BASIS));});}
  registry.registerAction({actionId:'competition.open',moduleId:'eeas.competition',label:'打开竞品资料',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async()=>openCompetition());
  registry.registerAction({actionId:'experience.open',moduleId:'eeas.experience',label:'打开写法经验',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target)=>openExperiences(target));
  registry.registerAction({actionId:'competition.capture',moduleId:'eeas.competition',label:'保存来源快照',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),pid=target.scope.projectId,result=await workbenchApi.capture(pid,input as CaptureInput);const sources=await workbenchApi.captures(pid);if(current())inspect(envelope('competition','competition',{sources,materials:materials()}));setSkillDrawer(false);return result;});
  registry.registerAction({actionId:'competition.material.add',moduleId:'eeas.competition',label:'选入梳理',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target,input)=>{const source=input as CompetitionCapture;if(source.project_id!==target.scope.projectId||!source.document_ref)throw new Error('当前来源没有可用的真实文档');if(!materials().some(item=>item.document_ref?.document_id===source.document_ref!.document_id)){if(materials().filter(item=>item.document_ref).length>=19)throw new Error('本轮最多19份资料，请先移除不需要的资料');setMaterials(list=>[...list,{path:source.title||source.url||source.id,status:source.status,document_ref:source.document_ref}]);}setNotice('已选入本轮资料；下一次共同梳理时会精确绑定来源');return source.document_ref;});
  registry.registerAction({actionId:'experience.create',moduleId:'eeas.experience',label:'观察模板经验',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const value=input as {name:string;sources:WritingSource[]};return workbenchApi.createProfile(target.scope.projectId,value.name,value.sources);});
  registry.registerAction({actionId:'experience.import',moduleId:'eeas.experience',label:'复制已审阅的写法',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>workbenchApi.importProfile(target.scope.projectId,input as WritingImportRequest));
  registry.registerAction({actionId:'experience.review',moduleId:'eeas.experience',label:'接受写法规则',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const value=input as {profile:WritingProfile;ids:string[];reason:string;edits:WritingRule[];operationId:string};if(value.profile.project_id!==target.scope.projectId)throw new Error('经验不属于当前工作');return workbenchApi.reviewProfile(value.profile,value.ids,value.reason,value.edits,value.operationId);});
  function recordDeliveryFeed(item:DeliveryFeedItem){if(project()?.id!==item.deliveryTarget.projectId)return;const result=item.artifact?.data as DeliveryOperation|undefined;if(result&&deliveryComplete(result))setReports(list=>[{...result.report.artifact,pending_download_url:result.report.pending?.download_url},...list.filter(report=>report.id!==result.report.artifact.id)]);setFeed(list=>upsertDeliveryFeed(list,item));}
  function deliveryFeed(pid:string,result:DeliveryReceipt,target:ArtifactEnvelope){if(project()?.id===pid)recordDeliveryFeed(deliveryFeedItem(deliveryTarget(target),result,false));}
  registry.registerAction({actionId:'discovery.stage',moduleId:'eeas.discovery',label:'生成阶段产物',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),original=target.data as DiscoverySession,value=input as StageOptions,key=operationId();saveDeliveryHistory(localStorage,original.project_id,'STAGE',original.id,key);const result=await planningDeliveryApi.stage(original,value,key);if(current()){deliveryFeed(original.project_id,result,target);setNotice('阶段报告、待确认事项与同源图已保存；实际执行尚未授权。');}return result;});
  registry.registerAction({actionId:'proposal.delivery.export',moduleId:'eeas.proposal',label:'生成完整交付文件',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target,input)=>{const current=navigation.capture(),original=target.data as PlanningProposal,value=input as ExportOptions,key=operationId();let prepared:string[]|false=false;let result:DeliveryOperation;try{result=await planningDeliveryApi.export(original,value,key,()=>{prepared=saveDeliveryHistory(localStorage,original.project_id,'EXPORT',original.id,key);});}catch(error){discardPreclaimDelivery(localStorage,original.project_id,original.id,key,error,prepared);throw error;}if(current()){deliveryFeed(original.project_id,result,target);await refreshObjects(original.project_id);setNotice('已确认计划的 Word、任务图、harness 与来源回执已保存；实际执行尚未授权。');}return result;});
  registry.registerAction({actionId:'delivery.export.resume',moduleId:'eeas.proposal',label:'继续原交付',availability:'IMPLEMENTED',scope:'project',requiresWrite:true},async(target)=>{
    const current=navigation.capture(),discussion=associationScope(quickScope()),artifactScope=scope(),pid=target.scope.projectId,original=target.data as DeliveryOperation;
    const resume=target.type==='planning-delivery'&&target.artifactId===original.operation_id?resumableExport(original,pid):undefined;
    const owns=()=>current()&&project()?.id===pid&&sameDiscussionScope(discussion,quickScope())&&sameDiscussionScope(artifactScope,scope());
    if(!owns()||!resume||target.scope.workspaceId!==undefined&&target.scope.workspaceId!==artifactScope.workspaceId||target.scope.sessionId!==undefined&&target.scope.sessionId!==artifactScope.sessionId)throw new Error('只能继续已核对的原失败交付；请先读取原回执。');
    let result:DeliveryReceipt;try{result=await planningDeliveryApi.resumeExport(pid,original);}catch(error){if(!owns())return;throw error;}
    if(!owns())return;
    recordDeliveryFeed(deliveryFeedItem({projectId:pid,kind:'EXPORT',basis:resume.proposal_ref},result,false));
    setNotice('原交付已继续完成；沿用原操作与已保存报告，未授权执行任务。');return result;
  });
  registry.registerAction({actionId:'delivery.operation',moduleId:'eeas.discovery',label:'读取交付回执',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target,input)=>{const current=navigation.capture(),basis=deliveryTarget(target);return deliveryRecovery.read(basis,(input as {operationId:string}).operationId,()=>current()&&project()?.id===basis.projectId);});
  registry.registerAction({actionId:'delivery.file.open',moduleId:'eeas.local-file',label:'核对并打开交付文件',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target,input)=>{const current=navigation.capture(),scope=associationScope(quickScope()),value=input as {receipt:DeliveryReceipt;file:DeliveryFile},file=await planningDeliveryApi.readFile(target.scope.projectId,value.receipt,value.file);if(!current()||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');await adoptDirectSource(file.source_ref);if(!current()||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');if(nativeBridge?.openDocument){const receipt=await openFeedbackDocument(file.source_ref,{scope,.../\.docx$/i.test(file.source_ref.path)&&file.source_ref.sha256===value.receipt.report.artifact.sha256?{deliveryOperationId:value.receipt.operation_id}:{}},current);if((receipt as {status?:string})?.status!=='OPEN_REQUESTED')throw new Error('系统未确认文件打开请求');setNotice('系统已接收实际文件的打开请求');return receipt;}inspect(envelope('local-file',file.source_ref.path,file,file.source_ref.revision,file.source_ref.sha256));return file;});
  registry.registerAction({actionId:'delivery.file.open-current',moduleId:'eeas.local-file',label:'核对并打开当前稿',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target,input)=>{const current=navigation.capture(),scope=associationScope(quickScope()),value=input as {receipt:DeliveryReceipt;file:DeliveryFile},file=await planningDeliveryApi.readCurrentFile(target.scope.projectId,value.receipt,value.file);if(!current()||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');await adoptDirectSource(file.source_ref);if(!current()||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');if(nativeBridge?.openDocument){const receipt=await openFeedbackDocument(file.source_ref,{scope,.../\.docx$/i.test(file.source_ref.path)&&(value.file.file_source_ref??value.file.source_ref)?.sha256===value.receipt.report.artifact.sha256?{deliveryOperationId:value.receipt.operation_id}:{}},current);if((receipt as {status?:string})?.status!=='OPEN_REQUESTED')throw new Error('系统未确认文件打开请求');setNotice('当前保存稿已交给原软件打开，旧交付依据与人工修改保留。');return receipt;}inspect(envelope('local-file',file.source_ref.path,file,file.source_ref.revision,file.source_ref.sha256));return file;});
  registry.registerAction({actionId:'delivery.report.open',moduleId:'eeas.report',label:'打开实际 Word',availability:'IMPLEMENTED',scope:'project',requiresWrite:false},async(target,input)=>{const value=input as {receipt?:DeliveryReceipt;report?:WordResult},report=value.receipt?.report??value.report;if(!report?.artifact?.id||!report.artifact.download_url)throw new Error('未返回实际文稿来源');if(nativeBridge?.openReportArtifact){const receipt=await nativeBridge.openReportArtifact(target.scope.projectId,report.artifact.id,report.artifact.sha256,report.artifact.download_url,{scope:associationScope(quickScope())});if((receipt as {status?:string})?.status!=='OPEN_REQUESTED')throw new Error('系统未确认文稿打开请求');setNotice('系统已接收实际 Word 的打开请求');return receipt;}inspect(envelope('report',report.artifact.id,report.artifact,1,report.artifact.sha256));return report;});
  async function appendUnboundDraft(){const text=unboundRecoveryText();if(!text)return;await action('取回原文字',async()=>{const before=prompt(),next=before?before+'\n\n'+text:text;try{setPrompt(next);saveComposerDraft();if(windowMode()==='companion')saveNativeText(localStorage,project()?.id,next);setUnboundRecoveryText(undefined);}catch(reason){setPrompt(before);throw reason;}});}
  async function pickFolder(){const picker=nativeDirectoryPicker(globalThis);if(!picker)return;await action('选择工作目录',async()=>{const path=await picker.openDirectoryPicker({multiple:false,title:'打开工作目录',defaultPath:folderPath()||undefined});if(typeof path==='string'){setFolderPath(path);if(!projectName().trim())setProjectName(path.split(/[\\/]/).filter(Boolean).at(-1)??'新工作');}});}
  function folderConnector(){return folderConnections??=new FolderConnection(api,localStorage);}
  function openFolderDialog(){const epoch=++folderEpoch;setFolderProject(project());setFolderTarget(project()?'CURRENT':'NEW');setFolderConnection(undefined);setFolderDialog(true);setFolderLoading(true);try{void folderConnector().refresh().then(value=>{if(epoch===folderEpoch&&folderDialog())setFolderConnection(value);}).catch(reason=>{if(epoch===folderEpoch&&folderDialog())setError(authoredErrorCopy(reason));}).finally(()=>{if(epoch===folderEpoch)setFolderLoading(false);});}catch(reason){setFolderLoading(false);setError(authoredErrorCopy(reason));}}
  async function applyFolderConnection(value:FolderConnectionIntent,owned:()=>boolean,epoch:number){
    if(!owned()||epoch!==folderEpoch||!folderDialog())return;
    if(value.stage!=='CONNECTED'||!value.project||!value.workspace){setNotice(value.reason??(value.stage==='PROJECT_SAVED'?'项目已保存，可继续连接原目录。':'连接结果需要核查，未重复创建。'));return;}
    if(value.current_binding_status!=='AVAILABLE'){setNotice(value.current_binding_reason??'原连接已保存，当前目录还需重新核对。');return;}
    const p=value.project,w=value.workspace;if(!projects().some(item=>item.id===p.id))setProjects(list=>[...list,p]);
    if(project()?.id!==p.id){const pending=selectProject(p),entered=navigation.capture();await pending;if(!entered())return;}
    if(epoch!==folderEpoch||!folderDialog()||project()?.id!==p.id)return;
    setWorkspaces(list=>[...list.filter(item=>item.id!==w.id),w]);const pending=selectWorkspace(w),entered=navigation.capture();await pending;
    if(entered()&&epoch===folderEpoch&&folderDialog()&&project()?.id===p.id&&workspace()?.id===w.id){setFolderDialog(false);setNotice('已连接实际工作目录');}
  }
  async function bindFolder(){
    const root=folderPath().trim(),target=folderTarget(),name=target==='CURRENT'?folderProject()?.name??'':projectName().trim(),writable=writeScope(),selected=folderProject(),owned=navigation.capture(),epoch=folderEpoch;if(!root||!name)return;
    await action('打开工作目录',async()=>{const connector=folderConnector(),intent=await connector.prepare(target,name,root,writable,selected);if(owned()&&epoch===folderEpoch&&folderDialog())setFolderConnection(intent);const result=await connector.run(intent.operation_id);
      if(!owned()||epoch!==folderEpoch||!folderDialog())return;setFolderConnection(result);
      if(folderPath().trim()!==root||folderTarget()!==target||writeScope()!==writable||target==='NEW'&&projectName().trim()!==name){setNotice('原目录连接结果已保留，后续输入未被覆盖；可核对原连接。');return;}await applyFolderConnection(result,owned,epoch);
    });
  }
  async function recoverFolderConnection(resume=false){
    const intent=folderConnection();if(!intent)return;const owned=navigation.capture(),epoch=folderEpoch;
    await action(resume?'继续原目录连接':'核对原目录连接',async()=>{const connector=folderConnector(),result=resume?await connector.run(intent.operation_id):await connector.inspect(intent.operation_id);
      if(!owned()||epoch!==folderEpoch||!folderDialog())return;setFolderConnection(result);
      if(resume)await applyFolderConnection(result,owned,epoch);else setNotice(result.stage==='UNKNOWN'?result.reason??'原连接仍待核对，未重发。':result.stage==='CONNECTED'?(result.current_binding_status==='AVAILABLE'?'原目录已核对，可打开原项目。':result.current_binding_reason??'原连接已保存，当前目录还需核对。'):'项目已保存，可继续连接原目录。');
    });
  }

  async function importMaterials() { const p=project(),w=workspace();if(!p||!w||!selection().length)return;await action('读取所选资料',async()=>{const result=await api.materials(p.id,w.id,selection());if(project()?.id!==p.id||workspace()?.id!==w.id)return;setMaterials(result.materials);const value=envelope('materials',operationId(),result);addFeed('资料已读取',uiText('{count} 份已保存来源 · {outcome}',{count:result.materials.filter(item=>item.document_ref).length,outcome:uiText(result.status==='PARTIAL'?'有文件未能解析':'原字节与解析回执已保存')}),result.status,value);setSelection([]);addFeed('所选资料', uiText('精确来源与解析回执可查看'),'SAVED',value);}); }
  function discoverySendState(){
    if(toolMode()==='original'||toolMode()==='direct'&&(linkedFile()||!draftAttachments().length)||!threadScope()&&selectedDiscussion()?.key.kind==='native'||pageReview()||nativeDraftAttachments().length||ordinaryGuidanceParent()||nativePending()&&!ordinaryNewQuestion())return 'normal';
    return discoverySendGate(activeOperation(),project()?.id,activeSession()?.id,discoveryInput(prompt(),materials().flatMap(item=>item.document_ref?[item.document_ref]:[]),draftAttachments().map(item=>item.feedback_ref)));
  }
  createEffect(()=>{
    const owner=activeOperation(),session=activeSession();
    if(owner?.kind==='discovery'&&!owner.historicalInspection&&owner.basisRevision!==undefined&&(owner.projectId!==project()?.id||owner.sessionId!==session?.id||owner.basisRevision!==session?.revision)){
      stopObserver();setActiveOperation(undefined);
    }
  });
  async function submitPrompt(){if(!threadWriteAllowed())return;
    if(!threadScope()&&selectedDiscussion()?.key.kind==='native')return hasComposerFile()&&!draftAttachments().length?submitCompanion():sendNativeMessage(prompt(),nativeDraftAttachments());
    if(await submitFeedbackPassage())return;
    if(toolMode()!=='original'&&(ordinaryGuidanceParent()||nativePending()&&!pageReview()&&!ordinaryNewQuestion()))return sendNativeMessage(prompt(),nativeDraftAttachments());
    if(pageReview()&&toolMode()!=='original')return sendNativeMessage(prompt(),nativeDraftAttachments(),pageReview()!.version===2&&!linkedFile()&&nativeFeedbackAvailable()?directFile()?.file.source_ref:undefined,pageReview()!.version===2?linkedFile()?.fileLinkRef:undefined);
    if(toolMode()==='original')return submitOriginalPrompt();
    if(toolMode()==='direct'&&hasComposerFile()&&!draftAttachments().length)return submitCompanion();
    if(nativeDraftAttachments().length)return sendNativeMessage(prompt(),nativeDraftAttachments());
    if(toolMode()==='direct'&&!draftAttachments().length)return inspector()?.type==='local-file'?submitDirect():submitCompanion();
    if(discoverySendState()==='blocked')return;
    if(draftAttachments().some(item=>invalidFeedback()[item.feedback_ref.id])){setError('关联来源已更新，草稿旧批注不能直接发送；请移除旧附件并重新定位。');return;}if(project()&&draftPersistence.hasPending(project()!.id,activeSession()?.id)){setError('正在恢复已保存批注；请等待，或重新打开当前需求工作后再发送。');return;}const userText=prompt().trim(),refs=materials().flatMap(item=>item.document_ref?[item.document_ref]:[]);if(!userText&&!(entryMode()==='TASK_FILE'&&refs.length))return;if(refs.length>19){setError('一次需求工作最多加入19份资料，请明确选择本轮范围');return;}if(!activeSession()&&entryMode()==='TASK_FILE'&&!refs.length){setError('先从工作目录加入真实任务文件；粘贴任务正文可选择已有需求入口');return;}await action(activeSession()?'继续共同梳理':'建立需求工作',async()=>{const text=userText||'基于所选任务文件梳理要求和缺口，先共同确认要求，再拆分工作。';let p=project();if(!p){p=await api.createProject(text.split('\n')[0].slice(0,60)||'新需求工作');setProjects(list=>[...list,p!]);setProject(p);localStorage.setItem('eeas-shell-project',p.id);}let value=activeSession();if(!value){const owned=navigation.capture();value=await discoveryApi.create(p.id,{title:text.split('\n')[0].slice(0,120),entry_mode:entryMode(),statement:text,materials:refs,workspace:workspace()});if(!owned()||project()?.id!==p.id)return;activateSession(value,{text:prompt(),feedback_refs:[]});rememberSession(value);}if(!workspace()){setPrompt('');setNotice('工作已保存。关联工作目录后可继续梳理。');return;}if(draftAttachments().some(item=>item.payload.revision_target_ref.id!==value!.id||item.payload.revision_target_ref.revision!==value!.revision||item.payload.revision_target_ref.sha256!==value!.sha256))throw new Error('草稿批注依据的版本已变化，请先移除旧附件并在当前版本重新批注');const submittedInput=discoveryInput(prompt(),refs,draftAttachments().map(item=>item.feedback_ref));const result=await advanceDiscovery(value,text,draftAttachments().map(item=>item.feedback_ref),submittedInput);if(project()?.id!==p.id||activeSession()?.id!==value.id)return;if(result&&sameDiscoveryInput(submittedInput,discoveryInput(prompt(),materials().flatMap(item=>item.document_ref?[item.document_ref]:[]),draftAttachments().map(item=>item.feedback_ref)))){setDraftAttachments([]);setPrompt('');}});}

  onMount(()=>{const keydown=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!event.isComposing&&!event.defaultPrevented&&fullscreen()){event.preventDefault();void leaveFullscreen();}};const fsChanged=()=>{if(nativeBridge)return;if(fullscreen()&&!document.fullscreenElement)syncFullscreenPanels(false);};window.addEventListener('keydown',keydown);document.addEventListener('fullscreenchange',fsChanged);onCleanup(()=>{window.removeEventListener('keydown',keydown);document.removeEventListener('fullscreenchange',fsChanged);});const media=window.matchMedia('(max-width:920px)');const changed=()=>{setCompact(media.matches);if(!media.matches)setSidebarOpen(false);};const resized=()=>{setSinglePane(window.innerWidth<=1100);if(!fullscreen())resizePanes();};media.addEventListener('change',changed);window.addEventListener('resize',resized);onCleanup(()=>{media.removeEventListener('change',changed);window.removeEventListener('resize',resized);});const notification=(event:Event)=>{const value=(event as CustomEvent<{title:string;description?:string}>).detail;setNotice(value.title+(value.description?` · ${value.description}`:''));};window.addEventListener('eeas-native-notification',notification);onCleanup(()=>window.removeEventListener('eeas-native-notification',notification));void action('连接本地服务',async()=>{try{const list=await api.projects();setProjects(list);const initial=startupProject(list,localStorage.getItem('eeas-shell-project'));if(!initial)void restoreOriginalSession();if(initial){await selectProject(initial,{restore:true});}try{const list=await api.providers();setProviders(list);setProvidersReady(true);const savedProvider=localStorage.getItem('eeas-provider-id');if(savedProvider&&list.some(value=>value.profile_ref.id===savedProvider))setProviderId(savedProvider);}catch(e){setError(authoredErrorCopy(e));}try{setNativeConfig(await api.request(`/api/agent-shell/native${project()?`?project_id=${encodeURIComponent(project()!.id)}`:''}`));}catch(e){setNativeConfig({status:'UNAVAILABLE',reason:e instanceof ApiError&&e.status===404?'原生会话代理尚未启用':message(e)});}}finally{setConnectionReady(true);}});});
  onCleanup(()=>{if(typeof window!=='undefined')saveComposerDraft();navigation.dispose();stopObserver();for(const item of registry.inventory())registry.unload(item.moduleId);});
  createEffect(()=>{if(!folderDialog()||!shell)return;window.dispatchEvent(new CustomEvent('eeas-native-suspend'));const nodes=[...shell.querySelectorAll<HTMLElement>('.as-sidebar,.as-main,.as-context,.as-agent')];nodes.forEach(node=>{node.inert=true;});const dialog=shell.querySelector<HTMLElement>('.as-dialog');const release=dialog?trapFocus(dialog,()=>setFolderDialog(false)):()=>{};onCleanup(()=>{nodes.forEach(node=>{node.inert=false;});release();});});
  createEffect(()=>{if(!sidebar||!shell)return;sidebar.inert=compact()&&!sidebarOpen()&&!fullscreenFiles();const content=sidebar.querySelector<HTMLElement>('.as-file-pane');if(content)content.inert=filesHidden()&&!fullscreenFiles();if(fullscreenFiles()||!compact()||!sidebarOpen())return;const blocked=[...shell.querySelectorAll<HTMLElement>('.as-main,.as-context,.as-agent')];blocked.forEach(value=>value.inert=true);const release=trapFocus(sidebar,()=>setSidebarOpen(false));onCleanup(()=>{release();blocked.forEach(value=>value.inert=false);});});

  createEffect(()=>{if(!skillDrawer()||!shell)return;const drawer=shell.querySelector<HTMLElement>('#as-agent-skills');if(!drawer)return;const behind=[...shell.querySelectorAll<HTMLElement>('.as-agent-content > .as-conversation,.as-agent-content > .as-composer,.as-agent-content > .as-native-context,.as-agent-content > .as-agent-tools-host,.as-agent-content > .as-global-activity,.as-agent-content > .as-quick-inbox')].map(node=>({node,inert:node.inert}));behind.forEach(({node})=>{node.inert=true;});const release=trapFocus(drawer,()=>setSkillDrawer(false),{restoreTarget:skillDrawerReturn,restoreIfContained:true});onCleanup(()=>{behind.forEach(({node,inert})=>{node.inert=inert;});release();});});

  createEffect(()=>{interactions.owner(scope());agentTools.owner(scope());});
  function revealAgent(){if(fullscreen())setFullscreenAgent(true);else if(!mobileWorkspace(layoutState()))setAgentHidden(false);setMobileView('agent');}
  function showAgentChat(){for(const item of agentTools.current())if(item.visible)agentTools.collapse(item.key);setSkillDrawer(false);revealAgent();}
  let agentToolReturnKey:string|undefined;
  function restoredAgentTool(key:string){agentToolReturnKey=key;setNativeMenu(false);}
  function focusRestoredAgentTool(){const key=agentToolReturnKey;agentToolReturnKey=undefined;if(!key)return false;const panel=[...shell?.querySelectorAll<HTMLElement>('.as-agent-tools-host .as-interaction-panel[data-interaction-key]')??[]].find(node=>node.dataset.interactionKey===key);const control=panel?.querySelector<HTMLElement>('[data-interaction-collapse]');if(!control||!agentTools.current().some(item=>item.key===key&&item.visible))return false;control.focus({preventScroll:true});return true;}
  function globalMenuReturnTarget(){return shell?.querySelector<HTMLElement>(windowMode()!=='companion'&&(compact()||fullscreen())?'.as-history-toggle':windowMode()!=='companion'&&!panelVisibility().files?'.as-sidebar-reopen':'.as-agent-menu-trigger')??undefined;}
  function openAgentDrawer(section:'original'|'native'|'resources',returnTarget?:HTMLElement){
    skillDrawerReturn=returnTarget??globalMenuReturnTarget()??shell?.querySelector<HTMLElement>('.as-composer-tool')??undefined;
    closeComposerMenu();setNativeMenu(false);if(compact())setSidebarOpen(false);setAgentDrawer(section);setSkillDrawer(true);revealAgent();
    requestAnimationFrame(()=>{if(skillDrawer())shell?.querySelector<HTMLElement>('#as-agent-skills > header button')?.focus({preventScroll:true});});
  }
  let sessionManagementReturn:HTMLElement|undefined,sessionManagementPreviousView:WorkspaceView='chat',sessionManagementPreviousMobile:'agent'|'workbench'='agent';
  function showOriginalPendingRequests(){closeSessionManagement();showAgentChat();queueMicrotask(()=>shell?.querySelector<HTMLElement>('.as-original-pending')?.focus({preventScroll:true}));}
  async function restoreOriginalControlRequest(attempt:ControlAttempt){
    const selected=originalSession(),scope=threadScope();if(!selected||!scope||selected.project_id!==attempt.identity.project||!sameNativeReference(selected.session_ref,attempt.identity.session)||selected.generation!==attempt.identity.generation||selected.active_operation_id||prompt().length||nativeDraftAttachments().length||draftAttachments().length)throw Error('CONTROL_RECOVERY_SCOPE_CHANGED');
    const owned=navigation.capture(),basis=currentOriginalFileInput(),workBasis=originalWorkInputChoice(),current=()=>owned()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id&&threadScope()?.conversation_ref.revision===scope.conversation_ref.revision&&originalSession()?.generation===selected.generation&&originalSession()?.session_ref.id===selected.session_ref.id&&!originalSession()?.active_operation_id&&!prompt().length&&!nativeDraftAttachments().length&&!draftAttachments().length&&sameOriginalWorkChoice(workBasis,originalWorkInputChoice());
    const original=await agentSessionsApi.operation(selected,attempt.identity.parent),request=original.request as {raw_text?:unknown;original_work_ref?:unknown;file_input?:{kind?:string;link_ref?:unknown;source_ref?:unknown};input_attachments?:import('./native-collaboration').NativeInputAttachment[]}|undefined;
    if(!current()||typeof request?.raw_text!=='string'||!request.raw_text.trim())throw Error('CONTROL_RECOVERY_SOURCE_UNAVAILABLE');
    const oldInput=!request.file_input?undefined:request.file_input.kind==='LINKED_FILE'?{kind:'LINKED_FILE',link_ref:request.file_input.link_ref}:{kind:'WORKSPACE_FILE',source_ref:request.file_input.source_ref};if(!sameOriginalFileTarget(basis,oldInput))throw Error('CONTROL_RECOVERY_SOURCE_CHANGED');
    const restored=nativeInputAttachments((request.input_attachments??[]).map(item=>({context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,...(item.kind==='SCREENSHOT'?{preview_sha256:item.image_sha256}:{})})));
    if(basis?.kind==='WORKSPACE_FILE')await refreshDirectFile();if(!current()||!sameOriginalFileTarget(basis,currentOriginalFileInput()))throw Error('CONTROL_RECOVERY_SCOPE_CHANGED');chooseTool('original',false);
    restoreOriginalWorkInput(selected,request.original_work_ref);setPrompt(request.raw_text);setNativeDraftAttachments(restored.map(item=>({context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,...(item.kind==='SCREENSHOT'?{preview_sha256:item.image_sha256}:{})})));saveComposerDraft();showOriginalPendingRequests();focusComposer();return true;
  }
  function originalOwnerRecoveryState(){
    connectionVersion();const s=originalSession();if(!s)return {attempt:undefined,unavailable:false};
    try{return {attempt:originalResumption.attempt(s)??originalResumption.previous(s),unavailable:false};}catch{return {attempt:undefined,unavailable:true};}
  }
  async function resumeOriginalOwner(inspect=false){
    const selected=originalSession(),scope=threadScope();if(!selected||!scope||threadAccess()!=='ready'||!inspect&&!ownerMayResume(selected,scope))return;
    const owned=navigation.capture(),current=()=>owned()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id&&threadScope()?.conversation_ref.revision===scope.conversation_ref.revision&&originalSession()?.generation===selected.generation&&sameNativeReference(originalSession()?.session_ref,selected.session_ref)&&originalSession()?.project_id===selected.project_id;
    setOwnerRecoveryError(undefined);
    try{saveComposerDraft();await flushOriginalControlDrafts();if(!current())return;
      const a=originalOwnerRecoveryState().attempt,base=inspect&&a?{...selected,generation:a.generation,original_session_id:a.original_session_id,active_operation_id:a.parent}:selected;
      const result=inspect?await originalResumption.inspect(base):await originalResumption.resume(selected,current);
      if(!current())return;selectOriginalSession(result);void refreshThread();return result;
    }catch{
      // A failed LOAD can already have advanced its owner generation. Refresh
      // only this exact selected scope; never create another operation here.
      if(current())try{const latest=await agentSessionsApi.get(selected.project_id,selected.session_ref);if(current()){selectOriginalSession(latest);void refreshThread();}}catch{}
      if(owned()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id&&originalSession()?.project_id===selected.project_id&&sameNativeReference(originalSession()?.session_ref,selected.session_ref))setOwnerRecoveryError(originalOwnerRecoveryState().attempt?.phase==='FAILED'?undefined:{scope:connectionNoticeScope(),text:uiLocale()==='en'?'Reconnection needs checking. Your draft is kept; no message was sent.':'恢复结果待核查，当前草稿保留，尚未发送消息。'});
    }
  }
  async function restoreExpiredOriginalRequest(){
    const selected=originalSession(),scope=threadScope(),a=originalOwnerRecoveryState().attempt;
    if(!selected||!scope||!a||a.phase!=='RETURNED'||a.generation+1!==selected.generation||a.original_session_id!==selected.original_session_id||!a.parent||!originalSessionReady(selected)||selected.active_operation_id||prompt().length||nativeDraftAttachments().length||draftAttachments().length)return;
    const owned=navigation.capture(),basis=currentOriginalFileInput(),workBasis=originalWorkInputChoice(),current=()=>owned()&&threadAccess()==='ready'&&threadScope()?.conversation_ref.id===scope.conversation_ref.id&&threadScope()?.conversation_ref.revision===scope.conversation_ref.revision&&originalSession()?.generation===selected.generation&&originalSession()?.project_id===selected.project_id&&sameNativeReference(originalSession()?.session_ref,selected.session_ref)&&!originalSession()?.active_operation_id&&!prompt().length&&!nativeDraftAttachments().length&&!draftAttachments().length&&sameOriginalWorkChoice(workBasis,originalWorkInputChoice());
    setOwnerRecoveryError(undefined);
    try{
      const original=await agentSessionsApi.operation(selected,a.parent),request=original.request as {raw_text?:unknown;original_work_ref?:unknown;file_input?:{kind?:string;link_ref?:unknown;source_ref?:unknown};input_attachments?:import('./native-collaboration').NativeInputAttachment[]}|undefined;
      if(!current()||original.generation!==(a.originGeneration??a.generation)||original.original_session_id!==a.original_session_id||original.status!=='UNKNOWN_OUTCOME'||typeof request?.raw_text!=='string'||!request.raw_text.trim())throw Error('CONTROL_RECOVERY_SOURCE_UNAVAILABLE');
      const oldInput=!request.file_input?undefined:request.file_input.kind==='LINKED_FILE'?{kind:'LINKED_FILE',link_ref:request.file_input.link_ref}:{kind:'WORKSPACE_FILE',source_ref:request.file_input.source_ref};
      if(!sameOriginalFileTarget(basis,oldInput))throw Error('CONTROL_RECOVERY_SOURCE_CHANGED');
      const restored=nativeInputAttachments((request.input_attachments??[]).map(item=>({context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,...(item.kind==='SCREENSHOT'?{preview_sha256:item.image_sha256}:{})})));
      if(basis?.kind==='WORKSPACE_FILE')await refreshDirectFile();if(!current()||!sameOriginalFileTarget(basis,currentOriginalFileInput()))throw Error('CONTROL_RECOVERY_SCOPE_CHANGED');
      chooseTool('original',false);restoreOriginalWorkInput(selected,request.original_work_ref);setPrompt(request.raw_text);setNativeDraftAttachments(restored.map(item=>({context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,...(item.kind==='SCREENSHOT'?{preview_sha256:item.image_sha256}:{})})));saveComposerDraft();originalResumption.restored(a);focusComposer();
    }catch{if(owned()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id)setOwnerRecoveryError({scope:connectionNoticeScope(),text:uiLocale()==='en'?'Your input is kept. Check the selected file and draft before restoring the request.':'当前输入保留，请先核对所选文件与草稿，再取回原要求。'});}
  }
  async function restoreQuestionAnswer(candidate:QuestionDraftRecovery){
    const selected=originalSession(),scope=threadScope();
    if(!selected||!scope||!threadWriteAllowed()||selected.active_operation_id||candidate.identity.project!==selected.project_id||!sameNativeReference(candidate.identity.session,selected.session_ref))return;
    const owned=navigation.capture(),basis={text:prompt(),attachments:structuredClone(nativeDraftAttachments())},fileBasis=currentOriginalFileInput();
    const current=()=>owned()&&threadWriteAllowed()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id&&threadScope()?.conversation_ref.revision===scope.conversation_ref.revision&&originalSession()?.generation===selected.generation&&!originalSession()?.active_operation_id&&sameNativeReference(originalSession()?.session_ref,selected.session_ref)&&sameReview(basis,currentDiscussionDraft())&&sameOriginalFileInput(fileBasis,currentOriginalFileInput());
    try{
      const original=await agentSessionsApi.operation(selected,candidate.identity.parent);
      if(!current())return;
      assertQuestionRecoveryCurrent(localStorage,candidate);
      const body=questionRecoveryText(candidate,original,selected,scope.conversation_ref.id,uiLocale()),next=appendQuestionRecovery(prompt(),body);
      setPrompt(next);saveComposerDraft();focusComposer();return scope.conversation_ref.id;
    }catch(error){
      if(owned()&&threadScope()?.conversation_ref.id===scope.conversation_ref.id)setError(error instanceof Error&&error.message==='QUESTION_DRAFT_MESSAGE_TOO_LONG'?(uiLocale()==='en'?'The current message is too long. Your answers and input are kept; shorten it before restoring.':'当前消息太长，回答和原稿均保留；请精简消息后再取回。'):(uiLocale()==='en'?'Your answers and current input are kept. Check the original request and draft before restoring.':'回答和当前输入均保留，请核对原问题与草稿后再取回。'));
    }
  }
  function originalSourceRecoveryState(){
    sourceRecoveryVersion();const s=originalSession();if(!s)return {attempt:null,unavailable:false};
    try{return {attempt:sourceRecoveryClient.pending(s),unavailable:false};}catch{return {attempt:null,unavailable:true};}
  }
  function captureNativeRecoveryCurrent(message:NativeMessage){
    const owned=navigation.capture(),thread=threadScope()?.conversation_ref.id??null,pid=project()?.id??null,cid=conversationId()??null;
    const operation=message.operation_id;
    return ()=>owned()&&(threadScope()?.conversation_ref.id??null)===thread&&(project()?.id??null)===pid&&(conversationId()??null)===cid&&message.operation_id===operation;
  }
  async function openNativeRecoveredChat(receipt:CreationReceipt,current:()=>boolean){
    if(!current())return false;
    saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();
    if(!current())return false;saveComposerDraft();
    const scope=await threadClient.scope(receipt.conversation_ref.id);if(!current())return false;
    if(scope.project_id!==receipt.project_id)throw Error('NATIVE_CONTEXT_RECOVERY_TARGET_CHANGED');
    const page=await threadClient.history(scope);if(!current())return false;
    // Open the metadata-only chat without a draft seed or an old execution owner.
    const opened=await installThread(savedThreadRow(scope,scope.conversation_ref.id),scope,page,current);
    if(opened){void refreshHistory(false,true);focusComposer();return true;}
    return false;
  }
  async function recoverOriginalSource(action:'start'|'inspect'|'continue'|'open'){
    const selected=originalSession(),before=threadScope();if(!selected||!before||!threadWriteAllowed())return;
    const owned=navigation.capture(),key=connectionNoticeScope();
    const current=()=>owned()&&threadScope()?.conversation_ref.id===before.conversation_ref.id&&threadScope()?.conversation_ref.revision===before.conversation_ref.revision&&originalSession()?.project_id===selected.project_id&&sameNativeReference(originalSession()?.session_ref,selected.session_ref)&&originalSession()?.generation===selected.generation;
    setSourceRecoveryError(undefined);
    try{
      saveComposerDraft();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();if(!current())return;saveComposerDraft();
      const receipt=action==='start'?await sourceRecoveryClient.start(selected,before.conversation_ref):action==='continue'?await sourceRecoveryClient.continueCreation(selected):await sourceRecoveryClient.inspect(selected);
      if(!current())return;
      if(receipt.project_id!==before.project_id)throw Error('SOURCE_CONTEXT_RECOVERY_CHANGED');
      const scope=await threadClient.scope(receipt.conversation_ref.id);if(!current())return;
      if(scope.project_id!==receipt.project_id)throw Error('SOURCE_CONTEXT_RECOVERY_CHANGED');
      const page=await threadClient.history(scope);if(!current())return;
      // Existing draft persistence saves the old input; no seed or old history is passed.
      const opened=await installThread(savedThreadRow(scope,scope.conversation_ref.id),scope,page,current);
      if(opened){await sourceRecoveryClient.acknowledge(selected,scope.conversation_ref.id);void refreshHistory(false,true);focusComposer();}
    }catch(reason){
      if(!current())return;const ready=reason instanceof Error&&reason.message==='SOURCE_CONTEXT_RECOVERY_READY',retryStop=reason instanceof Error&&reason.message==='SOURCE_CONTEXT_RECOVERY_STOP_NOT_RECORDED';
      setSourceRecoveryError({scope:key,ready,retryStop,text:ready||retryStop?'':uiLocale()==='en'?'Your chat and input are kept. Check the original recovery before continuing.':'原聊天与输入均保留，请核查原恢复结果后再继续。'});
    }
  }
  function originalSourceRecovery(){
    const s=originalSession(),scope=threadScope();if(!s||!scope)return undefined;
    const state=originalSourceRecoveryState();if(s.context_authority_issue?.generation!==s.generation&&!state.attempt)return undefined;
    const message=sourceRecoveryError()?.scope===connectionNoticeScope()?sourceRecoveryError():undefined;
    return <ContextRecovery session={s} attempt={state.attempt} busy={(sourceRecoveryVersion(),sourceRecoveryClient.busy(s))} unavailable={state.unavailable||!threadWriteAllowed()} ready={!!message?.ready} retryStop={!!message?.retryStop} error={message?.text??''} locale={uiLocale()} onStart={()=>void recoverOriginalSource('start')} onInspect={()=>void recoverOriginalSource('inspect')} onContinue={()=>void recoverOriginalSource('continue')} onOpen={()=>void recoverOriginalSource('open')}/>;
  }
  function ownerMayResume(value:AgentSession,scope:ThreadScope){if(connectionAffectsCurrent()||scope.current_owner?.status==='PENDING'||scope.current_owner?.status==='SELECTION_REQUIRED')return false;const owner=scope.current_owner;if(owner?.status==='CURRENT'||owner?.status==='SELECTED')return owner.original_source.project_id===value.project_id&&sameNativeReference(owner.original_source.session_ref,value.session_ref);const sources=scope.sources.filter(s=>s.original_source?.kind==='original');return sources.length===1&&sources[0].identity.project_id===value.project_id&&sameNativeReference(sources[0].original_source?.kind==='original'?sources[0].original_source.session_ref:undefined,value.session_ref);}
  function originalOwnerRecovery(){
    const s=originalSession(),state=originalOwnerRecoveryState();if(!s||!threadScope()||s.context_authority_issue?.generation===s.generation||originalSourceRecoveryState().attempt||!originalOwnerNeedsRecovery(s,state.attempt)||!state.attempt&&!ownerMayResume(s,threadScope()!))return undefined;
    return <OriginalOwnerRecovery session={s} resumeAllowed={ownerMayResume(s,threadScope()!)} attempt={state.attempt} busy={(connectionVersion(),originalResumption.busy(state.attempt?{...s,generation:state.attempt.generation}:s))} unavailable={state.unavailable} hasDraft={!!prompt().length||!!nativeDraftAttachments().length||!!draftAttachments().length} error={ownerRecoveryError()?.scope===connectionNoticeScope()?ownerRecoveryError()!.text:''} locale={uiLocale()} onResume={()=>void resumeOriginalOwner()} onInspect={()=>void resumeOriginalOwner(true)} onRestore={()=>void restoreExpiredOriginalRequest()}/>;
  }
  async function showOriginalSessionManager(returnTarget?:HTMLElement|MouseEvent){const current=navigation.capture();if(windowMode()==='companion'&&!await changeNativeMode('workbench'))return;if(!current())return;sessionManagementReturn=returnTarget instanceof HTMLElement?returnTarget:undefined;if(!sessionManagementOpen()){sessionManagementPreviousView=workbenchHidden()?'chat':agentHidden()?'workbench':'split';sessionManagementPreviousMobile=mobileView();}closeComposerMenu();setNativeMenu(false);setHistoryPopoverOpen(false);setSkillDrawer(false);setLeftTab('conversations');setSidebarOpen(false);setSessionManagementOpen(true);presentWorkspace('split');requestAnimationFrame(()=>shell?.querySelector<HTMLElement>('#as-session-management-heading')?.focus({preventScroll:true}));}
  function closeSessionManagement(){setSessionManagementOpen(false);presentWorkspace(sessionManagementPreviousView);if(mobileWorkspace(layoutState()))setMobileView(sessionManagementPreviousMobile);const target=sessionManagementReturn,rect=target?.getBoundingClientRect();if(target?.isConnected&&rect&&rect.width>0&&rect.height>0&&rect.right>0&&rect.left<innerWidth&&rect.bottom>0&&rect.top<innerHeight&&!target.closest('[hidden],[inert]')&&getComputedStyle(target).visibility!=='hidden')target.focus({preventScroll:true});else if(panelVisibility().agent)focusComposer();else requestAnimationFrame(()=>shell?.querySelector<HTMLElement>('.as-workbench .as-topbar button')?.focus({preventScroll:true}));}

  function showNativeActions(){openAgentDrawer('native');}
  function showResources(){openAgentDrawer('resources');}
  function chooseTool(value:string,openWorkbench=false){
    const selected=selectedDiscussion();
    if(threadScope()&&value!=='direct'&&value!=='original'){setError(uiLocale()==='en'?'Keep this draft here. Structured requirements can be continued from the requirements record.':'当前草稿保留在此会话；结构化需求可从对应需求记录继续。');return;}
    if(selected&&selected.key.kind!=='conversation'&&value!==(selected.key.kind==='native'?'direct':'original'))try{saveComposerDraft();}catch(reason){setError(authoredErrorCopy(reason));return;}
    batch(()=>{
      if(selected&&selected.key.kind!=='conversation'&&value!==(selected.key.kind==='native'?'direct':'original')){setSelectedDiscussion(undefined);discussionDraftLease=undefined;if(project()){restoreComposerDraft();restoreNativeDraft(project()!.id);}else{setDraftReady(false);setPrompt(readNativeText(localStorage));setNativeDraftAttachments(readNativeDraft(localStorage));nativeDraftOwner='unbound';}}
      closeComposerMenu();setToolMode(value);localStorage.setItem('eeas-agent-tool-mode',value);setSkillDrawer(false);
    });
    if(openWorkbench&&value==='discovery'&&windowMode()==='companion')void changeNativeMode('workbench');
  }

  function rememberOriginalSession(value:AgentSession){setOriginalSession(value);try{localStorage.setItem(`eeas-original-session:${project()?.id??'unbound'}`,JSON.stringify({project_id:value.project_id,session_ref:value.session_ref}));}catch{setError('原会话索引未能保存，实际会话保留');}}
  function selectOriginalSession(value?:AgentSession){const generation=++originalGeneration;if(originalTimer)clearTimeout(originalTimer);setOriginalSession(value);setOriginalOperations([]);setOriginalRecovery([]);if(value){rememberOriginalSession(value);void restoreOriginalOperations(agentSessionsApi,localStorage,value).then(records=>{if(nativeDisposed||originalSession()?.project_id!==value.project_id||!sameNativeReference(originalSession()?.session_ref,value.session_ref)||generation+1!==originalGeneration)return;setOriginalRecovery(records);setOriginalOperations(records.flatMap(record=>record.receipt?[record.receipt]:[]));}).catch(reason=>{if(!nativeDisposed&&generation+1===originalGeneration&&originalSession()?.project_id===value.project_id&&sameNativeReference(originalSession()?.session_ref,value.session_ref))setError(authoredErrorCopy(reason));});observeOriginalSession(value);}}
  async function restoreOriginalSession(pid?:string){const generation=originalGeneration;let raw;try{raw=JSON.parse(localStorage.getItem(`eeas-original-session:${pid??'unbound'}`)??'null');}catch{return;}if(!raw||typeof raw.project_id!=='string'||!nativeReference(raw.session_ref))return;try{const value=await agentSessionsApi.get(raw.project_id,raw.session_ref);if(nativeDisposed||generation!==originalGeneration||project()?.id!==pid)return;selectOriginalSession(value);setNotice('已核对保存的原会话，未重新连接或重发消息；在原会话与执行控制中可继续。');}catch(error){if(generation===originalGeneration&&project()?.id===pid)setError(authoredCopy("保存的原会话尚未核对；索引保留，未重新连接：{detail}",{detail:authoredErrorCopy(error)}));}}
  function originalHistoryRevision(value:AgentSession){return JSON.stringify(value.messages.flatMap(message=>{const info=message?.info as {id?:string;role?:string;time?:{completed?:number}}|undefined;return info?.role==='assistant'&&info.time?.completed?[[info.id,info.time.completed]]:[];}));}
  function observeOriginalSession(value:AgentSession){const generation=++originalGeneration;let historyRevision=originalHistoryRevision(value);async function poll(){if(nativeDisposed||generation!==originalGeneration)return;try{const current=await agentSessionsApi.get(value.project_id,value.session_ref);if(nativeDisposed||generation!==originalGeneration)return;const previous=originalSession();setOriginalSession(current);const revision=originalHistoryRevision(current);if(threadScope()&&(previous?.active_operation_id&&!current.active_operation_id||revision!==historyRevision)){historyRevision=revision;void refreshThread();}if(current.guidance?.length)setOriginalOperations(list=>list.map(item=>{const observed=current.guidance!.find(row=>row.operation_id===item.id);return observed?{...item,...observed}:item;}));if(current.active_operation_id){const receipt=await agentSessionsApi.operation(current,current.active_operation_id);if(generation===originalGeneration)setOriginalOperations(list=>[...list.filter(item=>item.id!==receipt.id),receipt]);}if(current.connected_owner&&(toolMode()==='original'||current.active_operation_id))originalTimer=setTimeout(()=>void poll(),1200);}catch(error){if(generation===originalGeneration){setOriginalSession(current=>current?{...current,owner_status:'OBSERVATION_UNAVAILABLE',observation_is_current:false,observation_error:{origin:'RENDERER_GET_FAILED',message:message(error)}}:current);setError(authoredCopy("原会话观察未完成：{detail}",{detail:authoredErrorCopy(error)}));}}}void poll();}
  let originalPanelSelectionRequest=0;
  createEffect(()=>{if(!sessionManagementOpen())originalPanelSelectionRequest++;});
  async function selectOriginalSessionFromPanel(value?:AgentSession,shared=false,panelCurrent:()=>boolean=()=>true){
    const request=++originalPanelSelectionRequest,scope=threadScope();
    if(!sessionManagementOpen())return;
    if(!scope){selectOriginalSession(value);return;}
    if(!value)return;
    const owned=navigation.capture(),id=scope.conversation_ref.id,revision=scope.conversation_ref.revision,generation=originalGeneration;
    const current=()=>panelCurrent()&&request===originalPanelSelectionRequest&&sessionManagementOpen()&&owned()&&threadScope()?.conversation_ref.id===id&&threadScope()?.conversation_ref.revision===revision&&originalGeneration===generation;
    try{
      if(shared){const catalogue=await ownerSelection.prepare(scope.conversation_ref,scope.project_id);if(!current())return;const result=await ownerSelection.select(catalogue,configurationOwner(value)!,current);if(!current())return;if(!result.applied)throw Error('ORIGINAL_OWNER_SELECTION_REQUIRED');}
      const latest=await threadClient.scope(id);if(!current())return;
      const selected=threadOriginalOwner(latest,value.project_id);if(!selected)throw Error('HISTORY_REFERENCE_CHANGED');
      verifyThreadOriginalOwner(latest,selected,value);
      batch(()=>{setThreadScope(latest);selectOriginalSession(value);});
    }catch(reason){if(current())setError(authoredErrorCopy(reason));}
  }
  async function prepareOriginalOwnerChoices(){const scope=threadScope();if(!scope)throw Error('ORIGINAL_OWNER_SELECTION_REQUIRED');return ownerSelection.prepare(scope.conversation_ref,scope.project_id);}
  async function chooseOriginalOwner(source:NonNullable<ReturnType<typeof configurationOwner>>,current:()=>boolean){const value=await agentSessionsApi.get(source.project_id,source.session_ref);if(!current())return;if(value.generation!==source.generation)throw Error('ORIGINAL_OWNER_SELECTION_REQUIRED');await selectOriginalSessionFromPanel(value,true,current);}
  function pendingOwnerSelection(){connectionVersion();const id=selectedThreadId();try{return {id,pending:id?ownerSelection.pending(id):null,error:false};}catch{return {id,pending:null,error:true};}}
  async function inspectOwnerSelection(retry=false){const state=pendingOwnerSelection();if(!state.id)return;const owned=navigation.capture(),id=state.id;const current=()=>owned()&&selectedThreadId()===id&&threadScope()?.conversation_ref.revision===state.pending?.request.conversation_ref.revision&&threadScope()?.project_id===state.pending?.request.project_id;try{const result=retry?await ownerSelection.retry(id,current):await ownerSelection.inspect(id);if(!owned()||selectedThreadId()!==id)return;await refreshThread();setConnectionNotice(result.applied?(uiLocale()==='en'?'Existing connection selected. Your input is kept.':'已选择已有连接，输入保持不变。'):(uiLocale()==='en'?'The original selection is saved; check the current connection.':'原选择已保存，请核对当前连接。'));}catch{if(owned())setConnectionNotice(uiLocale()==='en'?'Check the original selection before continuing. Your input is kept.':'请先核查原连接选择，当前输入保留。');}}
  async function useOriginalSession(value:AgentSession,agent:OriginalAgent){
    const currentThread=threadScope();
    if(currentThread){
      const owner=navigation.capture(),id=currentThread.conversation_ref.id;saveComposerDraft();
      try{const latest=await threadClient.scope(id);if(!owner()||threadScope()?.conversation_ref.id!==id)return false;
        const selected=threadOriginalOwner(latest,value.project_id);if(!selected)throw new Error('HISTORY_REFERENCE_CHANGED');
        verifyThreadOriginalOwner(latest,selected,value);
        if(!changeConversationAgentMode(agent))return false;batch(()=>{setThreadScope(latest);selectOriginalSession(value);setToolMode('original');});showAgentChat();if(sessionManagementOpen())closeSessionManagement();void refreshThread();return true;
      }catch(reason){if(owner())setError(authoredErrorCopy(reason));return false;}
    }
    const managementWasOpen=sessionManagementOpen(),owner=navigation.begin(),current=()=>!studioDisposed&&owner.current();
    try{await installDiscussionTarget({kind:'original',session:value,navigation:{project_id:value.project_id,session_ref:value.session_ref,workspace_ref:(value.workspace_ref??null) as Pick<Workspace,'id'|'revision'|'binding_sha256'>|null}},current,owner.signal);if(current()){if(!changeConversationAgentMode(agent))return false;showAgentChat();if(managementWasOpen)closeSessionManagement();return true;}return false;}
    catch(reason){if(current())setError(authoredErrorCopy(reason));return false;}
  }
  function changeConversationAgentMode(mode:OriginalAgent){const id=threadScope()?.conversation_ref.id;try{if(id)saveConversationAgentMode(localStorage,id,mode);}catch{setError(uiLocale()==='en'?'The work mode could not be saved. The current mode is kept.':'工作方式暂未保存，当前方式保持不变。');return false;}setOriginalAgent(mode);return true;}
  function pendingConnection(){connectionVersion();const id=selectedThreadId();if(!id)return {id:undefined,pending:null,error:false};try{return {id,pending:originalConnection.pending(id),error:false};}catch{return {id,pending:null,error:true};}}
  function pendingConfigurationAdoption(){connectionVersion();const id=selectedThreadId();try{return {id,pending:id?configurationAdoption.pending(id):null,error:false};}catch{return {id,pending:null,error:true};}}
  function configurationScopeCurrent(p:NonNullable<ReturnType<typeof originalConnection.pending>>){const scope=threadScope();if(!scope)return false;const current=p.adopted_scope?.current?p.adopted_scope:undefined;return JSON.stringify(current?.conversation_ref??p.request.conversation_ref)===JSON.stringify(scope.conversation_ref)&&(current?current.project_id:p.request.project_id)===scope.project_id;}
  function configurationAdoptionBasis(){const state=pendingConnection(),scope=threadScope();return state.pending?.configuration_request&&scope?JSON.stringify([scope.conversation_ref,scope.project_id,state.pending.request.operation_id,state.pending.checksum,state.pending.configuration_request.confirmation_sha256]):null;}
  async function prepareConfigurationAdoption(){const state=pendingConnection(),scope=threadScope(),basis=configurationAdoptionBasis(),owned=navigation.capture();if(!basis||!scope||!state.pending||configurationAdoptionPreparing())return;setConfigurationAdoptionPreparing(true);try{const view=await configurationAdoption.prepare(scope.conversation_ref,scope.project_id,state.pending);if(owned()&&configurationAdoptionBasis()===basis)setConfigurationAdoptionView({basis,view});}catch{if(owned())setConnectionNotice(uiLocale()==='en'?'The original configuration could not be checked for this location. Your input is kept.':'暂未核对当前归属下的原配置，输入保留。');}finally{setConfigurationAdoptionPreparing(false);}}
  async function confirmConfigurationAdoption(){const saved=configurationAdoptionView(),basis=configurationAdoptionBasis(),scope=threadScope(),owned=navigation.capture();if(!saved||saved.basis!==basis||!scope)return;const current=()=>owned()&&configurationAdoptionBasis()===basis;try{const result=await configurationAdoption.confirm(saved.view,current);if(!current())return;setConfigurationAdoptionView(undefined);if(result.current)await inspectOriginalConnection(true);else setConnectionNotice(uiLocale()==='en'?'The original acknowledgement is saved; check the current location again.':'原采纳记录已保存，请重新核对当前归属。');}catch{if(current())setConnectionNotice(uiLocale()==='en'?'The acknowledgement needs checking. Your input is kept.':'采纳结果待核查，当前输入保留。');}}
  async function inspectConfigurationAdoption(retry=false){const state=pendingConfigurationAdoption(),owned=navigation.capture(),scope=threadScope();if(!state.id||!scope)return;const id=state.id,current=()=>owned()&&selectedThreadId()===id&&JSON.stringify(threadScope()?.conversation_ref)===JSON.stringify(scope.conversation_ref)&&threadScope()?.project_id===scope.project_id;try{const result=retry?await configurationAdoption.retry(id,current):await configurationAdoption.inspect(id);if(!current())return;try{await originalConnection.inspect(id);}catch{}setConnectionNotice(result.current?(uiLocale()==='en'?'Acknowledgement saved. You can continue the original configuration. Your input is kept.':'采纳已保存，可继续原配置；当前输入保留。'):(uiLocale()==='en'?'The location changed again. Check the original configuration for the current location.':'归属再次变化，请核对当前归属下的原配置。'));}catch{if(current())setConnectionNotice(uiLocale()==='en'?'The original acknowledgement is still unconfirmed. Your input is kept.':'原采纳记录仍待确认，当前输入保留。');}}
  function configurationAdoptionStatus(){const state=pendingConfigurationAdoption(),original=pendingConnection().pending,en=uiLocale()==='en';if(state.pending||state.error)return <div class="as-conversation-organization-status" role="status"><span>{en?'Original configuration acknowledgement needs checking. Your input is kept.':'原配置采纳待核查，当前输入保留。'}</span><button type="button" disabled={!state.id||configurationAdoption.busy(state.id)} onClick={()=>void inspectConfigurationAdoption()}>{en?'Check acknowledgement':'核查原采纳'}</button><Show when={state.pending?.not_found}><button type="button" disabled={!state.id||configurationAdoption.busy(state.id)} onClick={()=>void inspectConfigurationAdoption(true)}>{en?'Finish original acknowledgement':'完成原采纳'}</button></Show></div>;
    if(!original?.configuration_request||original.superseded||original.phase==='CONFIRMED'||configurationScopeCurrent(original))return;const saved=configurationAdoptionView(),plan=saved?.basis===configurationAdoptionBasis()?saved.view:undefined;
    return <div class="as-conversation-organization-status as-configuration-adoption" role="status"><span>{en?'This chat location changed. Review the original configuration before continuing. Your input is kept.':'会话归属已变化，请核对原配置后继续；当前输入保留。'}</span><div><button type="button" onClick={()=>void inspectOriginalConnection()}>{en?'Check original connection':'核查原连接'}</button><button type="button" disabled={configurationAdoptionPreparing()} onClick={()=>void prepareConfigurationAdoption()}>{en?'Review original configuration':'核对原配置归属'}</button></div><Show when={plan} keyed>{view=><div class="as-configuration-adoption-summary"><p>{view.summary.original_project_label??(en?'Chats':'聊天')} → {view.summary.current_project_label??(en?'Chats':'聊天')}</p><p>{en?'Execution project':'执行项目'} · {view.summary.execution_project_label??(en?'App internal scope':'应用内部范围')}</p><p>{en?'The original model, workspace, tool permissions and limits are retained. Your draft will not be sent.':'原模型、工作目录、工具权限和额度保持不变；不会发送草稿。'}</p><details><summary>{en?'Original execution scope':'原执行范围'}</summary><p>{view.summary.allowed_effects.map(effect=>({WORKSPACE_WRITE:en?'Reviewed file writes':'审阅后文件写入',BACKGROUND_COMMAND:en?'Exact commands':'确切命令',PROCESS_START:en?'Local tool processes':'本机工具进程',REMOTE_MCP_CONNECT:en?'Selected remote tools':'所选远程工具'}[effect]??effect)).join(' · ')||(en?'Read only':'只读')}</p><For each={view.summary.command_allowlist}>{command=><pre>{command}</pre>}</For><p>{view.summary.mcp_tool_allowlist.join(' · ')}</p><p>{en?'Call limit':'调用上限'} {view.summary.max_calls} · {en?'Output per call':'单次输出上限'} {view.summary.max_output_tokens} · {view.summary.ttl_seconds}s</p></details><Show when={!view.adoption_allowed}><p>{en?'The original execution state changed. Check its original operation first.':'原执行状态已变化，请先核查原操作。'}</p></Show><button type="button" disabled={!view.adoption_allowed||!!state.id&&configurationAdoption.busy(state.id)} onClick={()=>void confirmConfigurationAdoption()}>{en?'Use original configuration and continue':'沿用原配置并继续'}</button></div>}</Show></div>;
  }
  function connectionAffectsCurrent(){const state=pendingConnection();const owner=pendingOwnerSelection(),adoption=pendingConfigurationAdoption();return state.error||!!state.pending||owner.error||!!owner.pending||adoption.error||!!adoption.pending;}
  function originalInCurrentProject(value:AgentSession,scope:ThreadScope){const registered=scope.sources.some(source=>source.original_source?.kind==='original'&&source.identity.project_id===value.project_id&&sameNativeReference(source.original_source.session_ref,value.session_ref));if(registered){const owner=scope.current_owner;return owner?.status==='CURRENT'||owner?.status==='SELECTED'?owner.original_source.project_id===value.project_id&&sameNativeReference(owner.original_source.session_ref,value.session_ref):owner?.status!=='PENDING'&&owner?.status!=='SELECTION_REQUIRED';}return scope.project_id===value.project_id||scope.project_id===null&&allProjects().some(item=>item.id===value.project_id)&&!projects().some(item=>item.id===value.project_id);}

  async function adoptOriginalConnection(id:string,value:AgentSession,current:()=>boolean){
    const next=await threadClient.scope(id);if(!current())return false;
    const selected=threadOriginalOwner(next,value.project_id);if(!selected)throw Error('CONNECTION_RECEIPT_MISMATCH');
    verifyThreadOriginalOwner(next,selected,value);
    if(next.project_id===null&&!allProjects().some(item=>item.id===value.project_id)){const list=await api.projects();if(!current())return false;setProjects(list);}
    setThreadScope(next);selectOriginalSession(value);await originalConnection.acknowledge(id,value);return current();
  }
  async function ensureOriginalForSend(scope:ThreadScope|undefined,text:string,attachments:NativeDraftAttachment[],agent:OriginalAgent){
    if(!scope)return true;
    const ownerPending=pendingOwnerSelection(),legacy=scope.sources.filter(source=>source.original_source?.kind==='original');if(ownerPending.error||ownerPending.pending||scope.current_owner?.status==='SELECTION_REQUIRED'||(!scope.current_owner||scope.current_owner.status==='LEGACY')&&legacy.length>1){saveComposerDraft();setConnectionNotice(uiLocale()==='en'?'Choose the existing connection in connection settings. Your input is kept.':'请在连接设置中选择已有连接，当前输入保留。');return false;}
    const value=originalSession(),profile=providers().find(item=>item.profile_ref.id===providerId());
    const sameConnection=value&&originalInCurrentProject(value,scope)&&(!profile||sameNativeReference(value.provider_profile_ref??undefined,profile.profile_ref));
    if(value&&sameConnection&&originalSessionCanResumeForSend(value)){
      const owned=navigation.capture(),inputBasis=structuredClone({...currentDiscussionDraft(),text,attachments});
      const current=()=>owned()&&JSON.stringify(threadScope()?.conversation_ref)===JSON.stringify(scope.conversation_ref);
      let ready=false;
      await action('恢复原会话',async()=>{
        const resumed=await resumeOriginalOwner();
        if(!resumed||!current()||!originalSessionReady(resumed))return;
        const chosen=providers().find(item=>item.profile_ref.id===providerId());
        ready=sameReview(inputBasis,currentDiscussionDraft())&&originalAgent()===agent
          &&(!chosen||sameNativeReference(resumed.provider_profile_ref??undefined,chosen.profile_ref));
        setConnectionNotice(ready?'':uiLocale()==='en'?'Connected. Check your updated draft and send when ready.':'连接已就绪，请检查更新后的草稿再发送。');
      });
      return ready&&current();
    }
    const needsConnection=!value||!value.active_operation_id&&!sameConnection;
    if(!needsConnection){if(originalSessionReady(value))return true;setConnectionNotice(uiLocale()==='en'?'This connection is not ready. Your input is kept; open connection settings to resume it.':'此连接尚未就绪，当前输入保留；可打开连接设置继续。');return false;}
    if(!profile){saveComposerDraft();await openProviders();return false;}
    saveComposerDraft();const inputBasis=structuredClone({...currentDiscussionDraft(),text,attachments}),profileRef={...profile.profile_ref},owned=navigation.capture(),id=scope.conversation_ref.id,revision=scope.conversation_ref.revision;
    const current=()=>owned()&&threadScope()?.conversation_ref.id===id&&threadScope()?.conversation_ref.revision===revision&&sameNativeReference(providers().find(item=>item.profile_ref.id===providerId())?.profile_ref,profileRef);
    let ready=false;await action('连接模型',async()=>{try{
      const result=await originalConnection.open(id,{conversationRef:{...scope.conversation_ref},provider:profileRef,projectId:scope.project_id??undefined,preserveExecutionScope:true,originalSource:configurationOwner(value),...(workspace()?.project_id===scope.project_id?{workspace:workspace()}:{}),title:historySelectedItem()?.title??(uiLocale()==='en'?'New chat':'新会话')},current);
      if(!current())return;if(!result.bound){setConnectionNotice(uiLocale()==='en'?'The connection is saved; its chat registration needs to finish.':'连接已保存，会话登记仍需完成。');return;}
      if(!await adoptOriginalConnection(id,result.session,current))return;
      if(sameReview(inputBasis,currentDiscussionDraft())&&sameNativeReference(quickScope().providerProfileRef,profileRef)&&originalAgent()===agent&&originalSessionReady(originalSession()))ready=true;
      else setConnectionNotice(uiLocale()==='en'?'Connected. Check your updated draft and send when ready.':'连接已就绪，请检查更新后的草稿再发送。');
    }catch{if(current())setConnectionNotice(uiLocale()==='en'?'The connection result needs checking. Your input is kept; no message was sent.':'连接结果待核查，当前输入保留，尚未发送消息。');}});
    return ready;
  }
  async function configureOriginalFromPanel(options:import('./agent-sessions').SessionOpenOptions,panelCurrent:()=>boolean){
    const scope=threadScope();if(!scope)throw Error('CONNECTION_SCOPE_REQUIRED');
    const owned=navigation.capture(),id=scope.conversation_ref.id,revision=scope.conversation_ref.revision;
    const current=()=>panelCurrent()&&owned()&&threadScope()?.conversation_ref.id===id&&threadScope()?.conversation_ref.revision===revision;
    saveComposerDraft();const result=await originalConnection.open(id,{...options,projectId:scope.project_id??undefined,conversationRef:{...scope.conversation_ref},originalSource:configurationOwner(originalSession()),preserveExecutionScope:false},current);
    if(!current())return;
    if(!result.bound){setConnectionNotice(uiLocale()==='en'?'The original configuration is saved and needs checking. Your draft is kept.':'原配置请求已保存，结果仍需核对；草稿保留。');return;}
    if(!await adoptOriginalConnection(id,result.session,current))return;
    setConnectionNotice('');return result.session;
  }
  async function inspectOriginalConnection(retry=false){
    const state=pendingConnection(),id=state.id;if(!id||!state.pending)return;const owned=navigation.capture(),current=()=>owned()&&selectedThreadId()===id;
    try{const result=retry?await originalConnection.retry(id):await originalConnection.inspect(id);if(!current())return;
      if(!result.bound){setConnectionNotice(uiLocale()==='en'?'The original connection is saved. Finish its chat registration before sending.':'原连接已保存，请先完成会话登记再发送。');return;}
      if(await adoptOriginalConnection(id,result.session,current))setConnectionNotice(originalSessionReady(result.session)?(uiLocale()==='en'?'Connection found. Your draft is ready for you to send.':'已找回连接，确认草稿后即可发送。'):(uiLocale()==='en'?'Connection found. Open connection settings to resume it; your draft is kept.':'已找回连接，请在连接设置中继续，草稿保留。'));
    }catch{if(current())setConnectionNotice(uiLocale()==='en'?'The connection could not be confirmed yet. Your input is kept.':'连接暂未核对完成，当前输入保留。');}
  }
  async function discardRejectedConnection(){const state=pendingConnection();if(!state.id)return;try{await originalConnection.discardRejected(state.id);setConnectionNotice(uiLocale()==='en'?'No connection was created. Check the model and tools, then send again.':'未创建连接，请检查模型与工具后重新发送。');await openProviders();}catch{setConnectionNotice(uiLocale()==='en'?'Check the original connection result first.':'请先核查原连接结果。');}}
  async function dismissConfirmedConnection(){const state=pendingConnection();if(!state.id||!state.pending?.owner)return;try{await originalConnection.acknowledge(state.id,state.pending.owner);setConnectionNotice('');}catch{setConnectionNotice(uiLocale()==='en'?'The recovery record could not be cleared. Your input is kept.':'恢复记录暂未清理，当前输入保留。');}}
  async function useCurrentConfiguration(){const state=pendingConnection(),d=state.pending?.superseded,scope=threadScope(),owned=navigation.capture();if(!state.id||!scope||!d)return;const id=state.id,basis=d.basis_sha256,current=()=>owned()&&threadScope()?.conversation_ref.id===id&&JSON.stringify(threadScope()?.conversation_ref)===JSON.stringify(d.current_scope.conversation_ref)&&threadScope()?.project_id===d.current_scope.project_id;try{saveComposerDraft();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();if(!current())return;const latest=await threadClient.scope(id);if(!current()||latest.current_owner?.status!=='CURRENT'||latest.current_owner.connection_operation_id!==d.current.operation_id||latest.current_owner.provider_generation!==d.current.provider_generation||latest.current_owner.original_source.project_id!==d.original_source.project_id||!sameNativeReference(latest.current_owner.original_source.session_ref,d.original_source.session_ref))throw Error('CONNECTION_RECEIPT_MISMATCH');const value=await agentSessionsApi.get(d.original_source.project_id,d.original_source.session_ref);if(!current()||value.generation!==d.original_source.generation||!sameNativeReference(value.provider_profile_ref??undefined,d.provider_profile_ref))return;await originalConnection.resolveSuperseded(id,basis,fresh=>current()&&fresh.basis_sha256===basis);if(!current())return;setThreadScope(latest);if(!await useOriginalSession(value,originalAgent()))throw Error('CONNECTION_RECEIPT_MISMATCH');setConnectionNotice(uiLocale()==='en'?'Current configuration retained. The original result and your draft are kept.':'已沿用当前配置；原操作结果和草稿均保留。');}catch{if(owned())setConnectionNotice(uiLocale()==='en'?'The current configuration needs checking. Your original records and input are kept.':'当前配置仍需核查，原记录与输入保留。');}}
  function connectionStatus(){const adoption=configurationAdoptionStatus();if(adoption)return adoption;const owner=pendingOwnerSelection();if(owner.pending||owner.error)return <div class="as-conversation-organization-status" role="status"><span>{uiLocale()==='en'?'Original connection choice needs checking. Your input is kept.':'原连接选择待核查，当前输入保留。'}</span><button type="button" disabled={!owner.id||ownerSelection.busy(owner.id)} onClick={()=>void inspectOwnerSelection()}>{uiLocale()==='en'?'Check original choice':'核查原选择'}</button><Show when={owner.pending?.not_found}><button type="button" disabled={!owner.id||ownerSelection.busy(owner.id)} onClick={()=>void inspectOwnerSelection(true)}>{uiLocale()==='en'?'Finish original choice':'完成原选择'}</button></Show></div>;const state=pendingConnection(),p=state.pending,en=uiLocale()==='en';if(!state.error&&!p&&!connectionNotice())return undefined;
    if(p?.superseded)return <div class="as-conversation-organization-status" role="status"><span>{en?'This chat has a newer configuration. The original outcome is kept.':'本会话已有更新的配置，原操作结果仍保留。'}</span><small>{en?'Current model':'当前模型'} · {p.superseded.provider_profile_ref.id}</small><button type="button" disabled={!state.id||originalConnection.busy(state.id)} onClick={()=>void inspectOriginalConnection()}>{en?'Check original configuration':'核查原配置'}</button><button type="button" disabled={!state.id||originalConnection.busy(state.id)} onClick={()=>void useCurrentConfiguration()}>{en?'Use current configuration; keep original record':'沿用当前配置，保留原记录'}</button></div>;
    return <div class="as-conversation-organization-status" role={state.error?'alert':'status'} data-original-connection><span>{connectionNotice()||(state.error?(en?'The connection recovery record could not be read. Your input is kept.':'暂未读到连接恢复记录，当前输入保留。'):p?.phase==='ATTEMPTED'&&!!state.id&&originalConnection.busy(state.id)?(en?'Connecting. Your input is kept.':'正在连接，当前输入已保留。'):p?.phase==='NOT_COMMITTED'?(en?'The original attempt did not create a connection.':'原尝试未创建连接。'):(en?'The connection result needs checking. Your input is kept.':'连接结果待核查，当前输入保留。'))}</span><Show when={p}><button type="button" disabled={!!state.id&&originalConnection.busy(state.id)} onClick={()=>void inspectOriginalConnection()}>{en?'Check connection':'核查连接'}</button><Show when={p?.configuration_request?configurationScopeCurrent(p)&&['FAILED_NOT_STARTED','CANDIDATE_READY','CANDIDATE_CLOSED'].includes(p.last_status??''):(p?.phase==='NOT_FOUND'||p?.phase==='FOUND_UNBOUND')}><button type="button" disabled={!!state.id&&originalConnection.busy(state.id)} onClick={()=>void inspectOriginalConnection(true)}>{p?.configuration_request?(en?'Continue original configuration':'继续原配置'):p?.phase==='FOUND_UNBOUND'?(en?'Finish connection':'完成连接'):(en?'Retry original connection':'重试原连接')}</button></Show><Show when={p?.phase==='NOT_COMMITTED'}><button type="button" onClick={()=>void discardRejectedConnection()}>{en?'Adjust connection':'调整连接配置'}</button></Show><Show when={p?.phase==='CONFIRMED'}><button type="button" onClick={()=>void dismissConfirmedConnection()}>{en?'Keep current state':'保留当前状态'}</button></Show></Show><Show when={!p&&!state.error}><button type="button" onClick={event=>void showOriginalSessionManager(event.currentTarget)}>{en?'Agent connection settings':'Agent 连接设置'}</button><button type="button" onClick={()=>setConnectionNotice('')}>{en?'Dismiss':'收起'}</button></Show></div>;
  }
  function currentOriginalFileInput():OriginalFileInput|undefined{
    return originalFileChoice(project()?.id,linkedFile()?.fileLinkRef,directFile()?.file.source_ref);
  }
  async function submitOriginalPrompt(){if(!threadWriteAllowed())return;
    const draftText=prompt(),attachments=nativeDraftAttachments(),scope=threadScope(),chosenAgent=originalAgent();
    if(!draftText.trim()&&!attachments.length)return;
    if(draftAttachments().length){setError(uiLocale()==='en'?'The document feedback is kept. Continue it from its requirements record.':'文稿批注已保留，请从对应需求记录继续。');return;}
    const contextChoiceLease=contextChoice()?.operation_id,workChoice=originalWorkInputChoice(),workStatus=!!workChoice&&originalWorkStatusText(draftText);
    if(!originalWorkChoiceMatches(workChoice,originalSession())){setError(uiLocale()==='en'?'This draft belongs to another original work connection. Select that connection or remove the work link.':'草稿关联另一原工作连接，请选回对应连接或移除此关联。');return;}
    const attachmentBasis=structuredClone(attachments);let input:import('./agent-sessions').OriginalConversationInput,inputsChecked=false;
    try{const fileInput=originalFileInput(currentOriginalFileInput());input={...(scope?{conversationRef:{...scope.conversation_ref}}:{}),inputAttachments:nativeInputAttachments(attachments),...(fileInput?{fileInput}:{}),...(contextChoice()?{contextSelectionId:contextChoice()!.selection_id}:{}),...(workChoice?{originalWorkRef:workChoice.reference}:{})};inputsChecked=true;saveComposerDraft();}
    catch{setError(inputsChecked?(uiLocale()==='en'?'Your draft could not be saved. Nothing was sent; keep your input and try again.':'草稿暂时无法保存，尚未发送；请保留当前输入后重试。'):(uiLocale()==='en'?'The attached material could not be verified. Nothing was sent; choose that material again.':'资料引用暂时无法核对，尚未发送；请重新选择该资料。'));return;}
    try{if(!workStatus&&!await ensureOriginalForSend(scope,draftText,attachments,chosenAgent))return;}catch{setError(uiLocale()==='en'?'Your input could not be saved. Nothing was sent.':'当前输入暂未保存，尚未发送。');return;}if(!sameOriginalFileInput(input.fileInput,currentOriginalFileInput())){setError(uiLocale()==='en'?'The selected file changed while connecting. Your draft is kept; send again with your current selection.':'连接期间文件选择已变化，原稿保留；请使用当前选择重新发送。');return;}if(!sameOriginalWorkChoice(workChoice,originalWorkInputChoice())||!originalWorkChoiceMatches(workChoice,originalSession())){setError(uiLocale()==='en'?'The selected work changed while connecting. Your draft is kept.':'连接期间原工作选择已变化，草稿保留。');return;}const value=originalSession();
    const inTarget=value&&(scope?scope.project_id===value.project_id||scope.project_id===null&&allProjects().some(item=>item.id===value.project_id)&&!projects().some(item=>item.id===value.project_id):!project()||value.project_id===project()!.id);
    if(!value||!workStatus&&(!originalSessionReady(value)||!value.active_operation_id&&!inTarget)){if(scope){setConnectionNotice(uiLocale()==='en'?'The connection is not ready. Your input is kept.':'连接尚未就绪，当前输入保留。');return;}try{saveComposerDraft();await showOriginalSessionManager();}catch{setError(uiLocale()==='en'?'Your draft could not be saved. Nothing was sent; keep your input and try again.':'草稿暂时无法保存，尚未发送；请保留当前输入后重试。');}return;}
    const text=draftText.trim()?draftText:uiLocale()==='en'?'Please review the attached material.':'请查看我添加的资料。';
    const owned=navigation.capture(),threadId=scope?.conversation_ref.id;
    const current=()=>owned()&&threadScope()?.conversation_ref.id===threadId&&originalSession()?.session_ref.id===value.session_ref.id&&originalSession()?.generation===value.generation;
    await action('发送原会话消息',async()=>{const key=operationId();if(scope)conversationReading.follow(scope.conversation_ref.id,key,current,undefined,readingScopeCurrent(conversationReadingState()));try{
      const result=await dispatchOriginalMessage(agentSessionsApi,localStorage,value,text,chosenAgent,key,input);if(!current())return;
      setOriginalOperations(list=>[...list.filter(item=>item.id!==key),result]);
      const accepted=result.status==='ACCEPTED'||result.status==='RETURNED',localStatus=result.role==='LOCAL_STATUS';
      if(accepted&&!localStatus&&input.contextSelectionId&&contextChoice()?.selection_id===input.contextSelectionId&&contextChoice()?.operation_id===contextChoiceLease)clearContextChoice();
      if(accepted&&prompt()===draftText&&sameReview(nativeDraftAttachments(),attachmentBasis)&&sameOriginalFileInput(input.fileInput,currentOriginalFileInput())&&sameOriginalWorkChoice(workChoice,originalWorkInputChoice())){setPrompt('');if(!localStatus)setNativeDraftAttachments([]);setConnectionNotice('');}
      else if(!accepted)setError(authoredCopy('原会话发送未获确认；原文字保留，请核查操作 {detail}',{detail:String(key)}));
      if(scope)void refreshThread();observeOriginalSession(value);
    }catch(error){if(!current())return;if(error instanceof AgentSessionOutcomeError&&error.receipt)setOriginalOperations(list=>[...list.filter(item=>item.id!==key),error.receipt!]);throw error;}});
  }

  function hasComposerFile(){return !!linkedFile()||nativeFeedbackAvailable()&&!!directFile();}
  function persistSelectedDirectFile(){const chosen=directFile();if(chosen&&project()?.id===chosen.file.source_ref.project_id)saveCompanionFileDraft(localStorage,chosen.file.source_ref.project_id,{source_ref:chosen.file.source_ref,write_requested:directWrite()});}
  async function adoptDirectSource(source:SourceRef,retainWrite=false){const pid=project()?.id,scope=associationScope(quickScope());if(pid!==source.project_id)return;const generation=++directFileGeneration,previous=directFile()?.file.source_ref,value=await companionFilesApi.load(source,false);if(project()?.id!==pid||generation!==directFileGeneration||!sameDiscussionScope(scope,quickScope()))return;if(!retainWrite&&linkedFile())await discussionFiles?.clear();if(project()?.id!==pid||generation!==directFileGeneration||!sameDiscussionScope(scope,quickScope()))return;directFileScope=scope;setDirectFile(value);setPreviousDirectFile(undefined);if(!retainWrite&&!sameSelectedFile(previous,source))setDirectWrite(false);setDirectFileRequired(false);persistSelectedDirectFile();void refreshNativeFileFeedbacks(pid);return value;}
  async function restoreSelectedDirectFile(pid:string){const stored=readCompanionFileDraft(localStorage,pid);if(project()?.id===pid)setPreviousDirectFile(stored);}
  async function selectPreviousDirectFile(){const stored=previousDirectFile();if(!stored||stored.source_ref.project_id!==project()?.id)return;await action('重新选择上次文件',async()=>{await adoptDirectSource(stored.source_ref);});}
  async function chooseDirectFile(){const picker=nativeFilePicker(globalThis);if(!picker){setDirectFileRequired(true);setError('本机文件选择器未连接；已关联目录中的实际文件仍可作为上下文。');return;}await action('选择当前文件',async()=>{const owned=navigation.capture();let p=project();if(!p){const text=prompt(),attachments=nativeDraftAttachments();p=await api.createProject('本机文件工作');setProjects(list=>[...list,p!]);setProject(p);localStorage.setItem('eeas-shell-project',p.id);nativeDraftOwner=p.id;saveNativeText(localStorage,p.id,text);saveNativeDraft(localStorage,p.id,attachments);restoreComposerDraft(undefined,{text,feedback_refs:[]});}const pid=p.id,scope=associationScope(quickScope()),generation=++directFileGeneration,value=await companionFilesApi.choose(pid,picker);if(!value||!sameDiscussionScope(scope,quickScope())||!owned()||project()?.id!==pid||generation!==directFileGeneration)return;if(linkedFile())await discussionFiles?.clear();if(!sameDiscussionScope(scope,quickScope())||!owned()||project()?.id!==pid||generation!==directFileGeneration)return;directFileScope=scope;setDirectFile(value);setPreviousDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);setWorkspaces(list=>[...list.filter(item=>item.id!==value.workspace.id),value.workspace]);persistSelectedDirectFile();setNotice(directFileSupported(value.file)?'实际文件已选中，后续消息会引用此文件版本。':'实际文件已固定为问答上下文；当前修改适配或大小范围尚未满足，可单独保存反馈。');});}
  async function changeDirectWrite(requested:boolean){if(linkedFile()||!nativeFeedbackAvailable())return;if(!requested){setDirectWrite(false);persistSelectedDirectFile();return;}const selected=directFile();if(!selected){setDirectWrite(true);setDirectFileRequired(true);return;}await action('授权所选文件写入',async()=>{const generation=++directFileGeneration,value=await companionFilesApi.authorize(selected);if(project()?.id!==value.file.source_ref.project_id||generation!==directFileGeneration||linkedFile())return;setDirectFile(value);setDirectWrite(true);setWorkspaces(list=>[...list.filter(item=>item.id!==value.workspace.id),value.workspace]);persistSelectedDirectFile();setNotice('仅所选文件的写入范围已确认；发送明确指令后才会执行。');});}
  async function refreshDirectFile(){const chosen=directFile();if(!chosen)return;await action('重新读取所选文件',async()=>{const generation=++directFileGeneration,value=await companionFilesApi.refresh(chosen);if(project()?.id!==value.file.source_ref.project_id||generation!==directFileGeneration)return;setDirectFile(value);persistSelectedDirectFile();void refreshNativeFileFeedbacks(value.file.source_ref.project_id);setNotice('已明确采用当前文件版本；原有反馈与修改回执保留。');});}
  function ownsOpenedFeedbackFile(value:OpenedFeedbackFile|undefined){const selected=directFile();return !!value&&!!selected&&value.generation===directFileGeneration&&value.lease===nativeFeedbackLease()&&sameDiscussionScope(value.scope,quickScope())&&value.scope.projectId===value.source.project_id&&value.scope.workspaceId===value.source.workspace_id&&exactFileSource(value.source,selected.file.source_ref)&&selected.file.current&&directFileSupported(selected.file);}
  function nativeFeedbackAvailable(){const linked=linkedFile(),opened=openedFeedbackFile();return !linked?suspendedFeedbackGeneration()!==directFileGeneration:ownsOpenedFeedbackFile(opened)&&!!opened?.link&&sameFileLink(opened.link,linked.fileLinkRef);}
  function showNativeFileFeedback(){
    const source=directFile()?.file.source_ref,scope=threadScope();
    if(!source||source.project_id!==project()?.id)return false;
    if(scope?.metadata_source)return scope.project_id===source.project_id&&!scope.archived&&threadAccess()==='ready';
    return toolMode()==='direct';
  }
  function currentNativeFeedbackAction(){return nativeFeedbackAvailable()?nativeFeedbackAction():undefined;}
  async function openFeedbackDocument(source:SourceRef,options:{scope:FileAssociationScope;deliveryOperationId?:string},current:()=>boolean){
    const attempt:OpenedFeedbackFile={source,scope:options.scope,generation:directFileGeneration,lease:nativeFeedbackLease()};openingFeedbackFile=attempt;setOpenedFeedbackFile(undefined);
    try{const result=await nativeBridge!.openDocument!(source.project_id,source,options),receipt=result as {status?:string;file_context_status?:string;file_context_scope?:FileAssociationScope;file_source_ref?:SourceRef;file_link_ref?:FileLinkRef};
      if(receipt&&current()&&openingFeedbackFile===attempt&&ownsOpenedFeedbackFile(attempt)&&receipt.status==='OPEN_REQUESTED'&&receipt.file_context_status==='SAVED'&&receipt.file_context_scope&&sameDiscussionScope(receipt.file_context_scope,attempt.scope)&&exactFileSource(receipt.file_source_ref,source)&&nativeReference(receipt.file_link_ref)&&receipt.file_link_ref?.project_id===source.project_id&&!attempt.superseded&&(!attempt.observedLink||sameFileLink(attempt.observedLink,receipt.file_link_ref))){setOpenedFeedbackFile({...attempt,link:receipt.file_link_ref});setSuspendedFeedbackGeneration(undefined);}
      return result;
    }finally{if(openingFeedbackFile===attempt)openingFeedbackFile=undefined;}
  }
  async function openDirectSource(source:SourceRef){await action('核对并打开实际文件',async()=>{const current=navigation.capture(),scope=associationScope(quickScope()),selected=await companionFilesApi.load(source);if(!current()||project()?.id!==source.project_id||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');await adoptDirectSource(source,true);if(!current()||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');if(nativeBridge?.openDocument){const receipt=await openFeedbackDocument(selected.file.source_ref,{scope},current);if((receipt as {status?:string})?.status!=='OPEN_REQUESTED')throw new Error('系统尚未确认文件打开请求');setNotice('系统已接收此实际文件版本的打开请求');return;}inspect(envelope('local-file',source.path,selected.file,source.revision,source.sha256));});}
  async function openOriginalDeliveredFile(file:FileContent){
    if(file.source_ref.project_id!==project()?.id)throw Error('HISTORY_REFERENCE_CHANGED');
    if(nativeBridge?.openDocument)return openDirectSource(file.source_ref);
    inspect(envelope('local-file',file.source_ref.path,file,file.source_ref.revision,file.source_ref.sha256));
  }

  async function refreshNativeFileFeedbacks(pid:string){const generation=++feedbackRefreshGeneration,records=nativeFileFeedbacks().filter(value=>value.payload.project_id===pid);if(!records.length)return;setNativeFileFeedbacks(list=>list.map(value=>value.payload.project_id===pid?uncheckedFileFeedback(value,'固定文件版本更新，正在读取实际反馈核对'):value));const actual=await recheckNativeFileFeedbacks(nativeFeedbackApi,pid,records);if(nativeDisposed||project()?.id!==pid||generation!==feedbackRefreshGeneration)return;const versions=new Map(actual.map(value=>[value.feedback_ref.id,value]));setNativeFileFeedbacks(list=>list.map(value=>versions.get(value.feedback_ref.id)??value));}
  const [nativePreviewReveal,setNativePreviewReveal]=createSignal<{projectId:string;conversationId:string;feedbackRef:RevisionRef;planRef:RevisionRef;token:number}>();let nativePreviewToken=0;
  function messagePreviewTargets(message:NativeMessage){return nativePreviewTargets(message,project()?.id,conversationId());}
  async function openNativeMessagePreview(target:NativePreviewTarget,trigger:HTMLButtonElement){
    const current=navigation.capture(),scope=associationScope(quickScope()),selectedGeneration=directFileGeneration,token=++nativePreviewToken;
    const owned=()=>!nativeDisposed&&current()&&token===nativePreviewToken&&sameDiscussionScope(scope,quickScope())&&selectedGeneration===directFileGeneration&&nativeMessages().some(row=>row.message.operation_id===target.operationId&&messagePreviewTargets(row.message).some(value=>sameNativePreview(value,target)));
    if(!owned())return;
    let ownsFocus=document.activeElement===trigger;const moved=(event:FocusEvent)=>{if(event.target!==trigger)ownsFocus=false;};document.addEventListener('focusin',moved,true);
    try{await action('查看预览',async()=>{let result;try{result=await readNativePreview(target,nativeReworkApi,nativeFeedbackApi);}catch(error){if(owned())throw error;return;}if(!owned())return;saveNativeFileFeedback(result.feedback);setNativeReworkRecords(list=>[...list.filter(old=>!result.records.some(value=>sameNativeReference(old.record_ref,value.record_ref))),...result.records]);setNativePreviewReveal({projectId:target.projectId,conversationId:target.conversationId,feedbackRef:result.feedback.feedback_ref,planRef:result.plan.record_ref,token});setReviewTab('attention');
      queueMicrotask(()=>{if(!owned()||!ownsFocus)return;const exact=JSON.stringify([target.projectId,result.feedback.feedback_ref.id,result.feedback.feedback_ref.revision,result.feedback.feedback_ref.sha256]);const panel=Array.from(shell?.querySelectorAll<HTMLElement>('[data-native-feedback-ref]')??[]).find(node=>node.dataset.nativeFeedbackRef===exact&&node.isConnected&&node.getClientRects().length>0&&!node.closest('[hidden],[inert],[aria-hidden="true"]'));if(!panel)return;panel.scrollIntoView({block:'nearest',behavior:'smooth'});panel.focus({preventScroll:true});});
    });}finally{document.removeEventListener('focusin',moved,true);}
  }
  function saveNativeFileFeedback(value:NativeFileFeedback){if(project()?.id!==value.payload.project_id)return;setNativeFileFeedbacks(list=>[...list.filter(item=>item.feedback_ref.id!==value.feedback_ref.id),value]);localStorage.setItem(`eeas-native-file-feedback:${value.payload.project_id}`,JSON.stringify(nativeFileFeedbacks().slice(-30).map(item=>item.feedback_ref)));}
  function registerNativeFeedbackAction(action:FeedbackComposerAction){setNativeFeedbackAction(action);return()=>{if(nativeFeedbackAction()===action)setNativeFeedbackAction(undefined);};}
  async function submitFeedbackPassage(){if(!nativeFeedbackAvailable())return false;const action=currentNativeFeedbackAction();const check=nativeSelectionCheck();if(check&&check.lease===nativeFeedbackLease()&&exactFileSource(check.source,directFile()?.file.source_ref)){setError('正在核对所选片段，请稍候。');return true;}if(!action?.active)return false;if(action.scopeKey!==nativeFeedbackScope()||action.leaseKey!==nativeFeedbackLease()||!exactFileSource(action.source,directFile()?.file.source_ref)){setError('文件反馈范围正在切换，原意见保留。');return true;}await action.submit();return true;}
  function nativeFeedbackScope(){return JSON.stringify(associationScope(quickScope()));}
  function nativeFeedbackLease(){return JSON.stringify([nativeFeedbackScope(),quickScopeGeneration]);}
  function editNativeFeedbackQuote(){nativeFeedbackQuoteEditEpoch++;setNativeSelectionCheck(undefined);}
  async function captureFileFeedbackSelection(event:Event){if(!nativeFeedbackAvailable())return;const target=event.target;if(!(target instanceof HTMLTextAreaElement)||!target.classList.contains('as-file-editor')||document.activeElement!==target)return;const shown=inspector(),selected=directFile();if(shown?.type!=='local-file'||!selected)return;const file=shown.data as FileContent;if(!exactFileSource(file.source_ref,selected.file.source_ref))return;const current=navigation.capture(),basis=nativeFeedbackLease(),editEpoch=nativeFeedbackQuoteEditEpoch,start=target.selectionStart,end=target.selectionEnd,buffer=target.value,token=++nativeSelectionToken;setNativeSelectionCheck(start===end?undefined:{source:file.source_ref,lease:basis,token});setNativeFeedbackSelection(undefined);const stillCurrent=()=>!nativeDisposed&&current()&&basis===nativeFeedbackLease()&&editEpoch===nativeFeedbackQuoteEditEpoch&&exactFileSource(directFile()?.file.source_ref,file.source_ref)&&target.isConnected&&target.value===buffer&&target.selectionStart===start&&target.selectionEnd===end;try{const capture=await savedTextareaSelection(file,buffer,start,end);if(stillCurrent())setNativeFeedbackSelection({basis,source:file.source_ref,...capture});}catch{if(stillCurrent()&&start!==end){const quote=buffer.slice(start,end);if(Array.from(quote).length<=4000)setNativeFeedbackSelection({basis,source:file.source_ref,quote,selection:{kind:'EXACT_QUOTE',quote}});else setError('请缩小本次反馈片段');}}finally{if(nativeSelectionCheck()?.token===token&&(start===end||nativeFeedbackSelection()||!stillCurrent()))setNativeSelectionCheck(undefined);}}

  async function recordSelectedFeedback(mode:'DRAFT'|'SUBMITTED',text=prompt()){const selected=nativeFeedbackAvailable()?directFile():undefined;if(!selected||!text.trim())return;const value=await nativeFeedbackApi.record(selected.file.source_ref,text,mode);saveNativeFileFeedback(value);return value;}
  async function saveSelectedFeedback(){await action('保存所选文件反馈',async()=>{const value=await recordSelectedFeedback('DRAFT');if(value)setNotice('文件反馈已按实际原版本保存；未授权写入，也未发送模型。');});}
  async function linkDirectFeedback(value:DirectAction){const ref=directFeedbackRefs()[value.operation_id];if(!ref||!value.before_ref||['PROCESSING','CANCEL_REQUESTED','UNKNOWN_OUTCOME'].includes(value.status))return;const feedback=nativeFileFeedbacks().find(item=>item.feedback_ref.id===ref.id)??await nativeFeedbackApi.get(value.project_id,ref);if(!exactFileSource(feedback.payload.baseline_file_source_ref,value.before_ref)||feedback.direct_action_links.some(item=>item.payload.action_receipt.operation_id===value.operation_id))return;await nativeFeedbackApi.link(feedback,value.operation_id,'direct-link-'+value.operation_id);saveNativeFileFeedback(await nativeFeedbackApi.get(value.project_id,ref));}
  async function submitCompanion(){if(!threadWriteAllowed())return;if(await submitFeedbackPassage())return;const linked=linkedFile();if(linked)return sendNativeMessage(prompt(),nativeDraftAttachments(),undefined,linked.fileLinkRef);const effectiveFile=nativeFeedbackAvailable()?directFile():undefined,route=companionRoute(effectiveFile?.file,directWrite(),!!nativeDraftAttachments().length);if(route==='NATIVE_OBSERVE')return sendNativeMessage(prompt(),nativeDraftAttachments());if(route==='NATIVE_FILE_OBSERVE'){const selected=effectiveFile!;try{const actual=await companionFilesApi.load(selected.file.source_ref);if(!exactFileSource(directFile()?.file.source_ref,actual.file.source_ref))throw new Error('固定文件目标已切换，请核对本轮来源');return sendNativeMessage(prompt(),nativeDraftAttachments(),actual.file.source_ref);}catch(error){setError(authoredErrorCopy(error));return;}}if(route==='SELECT_FILE'){setDirectFileRequired(true);return;}if(route==='STALE_FILE'){setError('所选文件已经变化，先重新读取并核对当前版本；本次没有启动修改。');return;}if(route==='MIXED_CONTEXT'){setError('草稿还包含原生对象批注，它与所选文件尚未建立精确定位。先发送原生对象反馈或移除此附件，再处理所选文件。');return;}if(route==='RECORD_ONLY'){await action('保存文件修改意见',async()=>{const value=await recordSelectedFeedback('SUBMITTED');if(value)setNotice('修改意见已绑定此文件版本并保存；当前文件的修改适配或大小范围尚未满足，未发送模型也未改文件。');});return;}return submitDirect(effectiveFile!.file);}
  function directRecord(value:DirectAction){if(project()?.id!==value.project_id)return;setDirectResults(list=>[...list.filter(item=>item.operation_id!==value.operation_id),value]);localStorage.setItem(`eeas-direct-history:${value.project_id}`,JSON.stringify(directResults().map(item=>item.operation_id).slice(-30)));}
  async function restoreFileFeedbackHistory(pid:string,current:()=>boolean){
    let refs:unknown;
    try{refs=JSON.parse(localStorage.getItem(`eeas-native-file-feedback:${pid}`)??'[]');}catch{return;}
    if(!Array.isArray(refs)||!current())return;
    const loaded=await Promise.allSettled(refs.slice(-30).filter(nativeReference).map(ref=>nativeFeedbackApi.get(pid,ref)));
    if(!current()||project()?.id!==pid)return;
    const restored=loaded.flatMap(value=>value.status==='fulfilled'?[value.value]:[]);
    if(restored.length)setNativeFileFeedbacks(list=>[...new Map([...restored,...list].map(value=>[value.feedback_ref.id,value])).values()]);
  }
  async function restoreDirectHistory(pid:string,current:()=>boolean){try{setDirectInstructions(JSON.parse(localStorage.getItem(`eeas-direct-instructions:${pid}`)??'{}'));setDirectFeedbackRefs(JSON.parse(localStorage.getItem(`eeas-direct-feedback-links:${pid}`)??'{}'));}catch{setDirectInstructions({});setDirectFeedbackRefs({});}let refs:unknown=[];try{refs=JSON.parse(localStorage.getItem(`eeas-direct-history:${pid}`)??'[]');}catch{}await restoreFileFeedbackHistory(pid,current);if(!current()||project()?.id!==pid)return;if(!Array.isArray(refs))return;const results=await Promise.allSettled(refs.slice(-30).filter(value=>typeof value==='string').map(key=>directApi.get(pid,key)));if(!current()||project()?.id!==pid)return;const actual=results.flatMap(value=>value.status==='fulfilled'?[value.value]:[]);setDirectResults(actual);for(const value of actual)void linkDirectFeedback(value).catch(error=>{if(current()&&project()?.id===pid)setError(authoredCopy("反馈与真实执行的关联尚未完成：{detail}",{detail:authoredErrorCopy(error)}));});const pending=actual.findLast(value=>['PROCESSING','CANCEL_REQUESTED'].includes(value.status));if(pending)observeDirectOperation(pid,pending.operation_id);}
  function observeDirectOperation(pid:string,key:string){stopObserver();const controller=new AbortController();operationObserver=controller;setActiveOperation({id:key,projectId:pid,kind:'direct',status:'PROCESSING'});async function poll(){if(controller.signal.aborted||project()?.id!==pid)return;try{const value=await directApi.get(pid,key,controller.signal);if(controller.signal.aborted||project()?.id!==pid)return;directRecord(value);setActiveOperation({id:key,projectId:pid,kind:'direct',status:value.status,execution:value.execution});if(!['PROCESSING','CANCEL_REQUESTED'].includes(value.status)){await linkDirectFeedback(value);if(activeOperation()?.id===key){stopObserver();setActiveOperation(undefined);}return;}}catch(error){if(!controller.signal.aborted)setError(authoredErrorCopy(error));}if(!controller.signal.aborted)operationTimer=setTimeout(()=>void poll(),1200);}void poll();}
  async function submitDirect(chosen?:FileContent){if(!threadWriteAllowed())return;const text=prompt();if(!text.trim())return;await action('处理当前文件',async()=>{let p=project();if(!p){p=await api.createProject('本机工作上下文');setProject(p);setProjects(list=>[...list,p!]);restoreComposerDraft(undefined,{text,feedback_refs:[]});}const pid=p.id,key=operationId(),file=chosen??(inspector()?.type==='local-file'?(inspector()!.data as FileContent):undefined),source=directFileSupported(file)?file!.source_ref:undefined;if(source){await companionFilesApi.load(source);const feedback=await nativeFeedbackApi.record(source,text,'SUBMITTED','direct-feedback-'+key);saveNativeFileFeedback(feedback);setDirectFeedbackRefs(list=>({...list,[key]:feedback.feedback_ref}));localStorage.setItem(`eeas-direct-feedback-links:${pid}`,JSON.stringify(directFeedbackRefs()));}setDirectInstructions(values=>({...values,[key]:text}));localStorage.setItem(`eeas-direct-instructions:${pid}`,JSON.stringify(directInstructions()));const savedText=prompt();stopObserver();const controller=new AbortController();operationObserver=controller;setActiveOperation({id:key,projectId:pid,kind:'direct',status:'REQUESTING'});localStorage.setItem(`eeas-direct-operation:${pid}`,key);let pending=true;
    async function poll(){if(!pending||controller.signal.aborted)return;try{const value=await directApi.get(pid,key,controller.signal);if(project()?.id!==pid||controller.signal.aborted)return;directRecord(value);setActiveOperation({id:key,projectId:pid,kind:'direct',status:value.status,execution:value.execution});if(!['PROCESSING','CANCEL_REQUESTED'].includes(value.status))pending=false;}catch(error){if(!controller.signal.aborted&&!(error instanceof ApiError&&error.status===404))setError(authoredErrorCopy(error));}if(pending&&!controller.signal.aborted)operationTimer=setTimeout(()=>void poll(),1200);}
    void poll();try{const result=await directApi.run(pid,text,{file:source,write:directWrite(),profile:providers().find(item=>item.profile_ref.id===providerId())},key);pending=false;if(project()?.id!==pid||controller.signal.aborted)return;directRecord(result);if(['APPLIED','NO_CHANGE'].includes(result.status)&&prompt()===savedText)setPrompt('');try{await linkDirectFeedback(result);}catch(error){setError(authoredCopy("文件动作回执已保存；反馈关联尚未完成：{detail}",{detail:authoredErrorCopy(error)}));}if(result.status==='APPLIED'&&result.after_ref){if(exactFileSource(directFile()?.file.source_ref,result.before_ref??undefined))try{await adoptDirectSource(result.after_ref,true);}catch(error){setError(authoredErrorCopy(error));}setNotice('文件已实际更新，回答中可打开更新后的文件；工作台未保存的编辑保留。');}if(result.error)setError(result.error.message);}catch(error){try{const result=await directApi.get(pid,key);if(project()?.id===pid)directRecord(result);}catch{}throw new Error('文件动作结果尚未确认；原文字与反馈保留，请核对原执行，勿重复发送：'+message(error));}finally{pending=false;if(activeOperation()?.id===key){stopObserver();setActiveOperation(undefined);localStorage.removeItem(`eeas-direct-operation:${pid}`);}}});}

  function restoreNativeDraft(pid?:string){if(selectedDiscussion())return;const attachments=readNativeDraft(localStorage,pid),text=readNativeText(localStorage,pid);nativeDraftOwner=pid??'unbound';batch(()=>{setNativeDraftAttachments(attachments);if(windowMode()==='companion')setPrompt(text);});}
  createEffect(()=>{const attachments=nativeDraftAttachments(),text=prompt(),pid=project()?.id;if(selectedDiscussion())return;if(nativeDraftOwner===(pid??'unbound')){try{saveNativeDraft(localStorage,pid,attachments);if(windowMode()==='companion')saveNativeText(localStorage,pid,text);}catch{setError('本机草稿未能保存，请保留反馈文字后重试');}}});
  let modeRequest=0;
  async function changeNativeMode(mode:NativeMode){const request=++modeRequest;try{if(windowMode()!==mode&&fullscreen()&&!(await leaveFullscreen()))return false;if(request!==modeRequest)return false;if(nativeController)await nativeController.mode(mode);else setWindowMode(mode);if(request!==modeRequest)return false;if(windowMode()!==mode)throw new Error('宿主尚未确认窗口切换，请重试');batch(()=>{setNativeMenu(false);if(mode==='workbench')setWorkbenchHidden(false);setMobileView(mode==='companion'?'agent':'workbench');});return true;}catch(error){if(request===modeRequest)setError(authoredErrorCopy(error));return false;}}
  const [inputPreviewVersion,setInputPreviewVersion]=createSignal(0);
  const inputPreviews=new NativeInputPreviews(nativeBridge,()=>setInputPreviewVersion(value=>value+1));
  const [inputEditor,setInputEditor]=createSignal<{item:NativeDraftAttachment;readOnly:boolean;returnTarget?:HTMLElement;snapshot?:NativeContextSnapshot;loading:boolean;error?:string}>();
  const [inputEditorReturn,setInputEditorReturn]=createSignal<HTMLElement>();
  let inputEditorEpoch=0;
  function closeInputEditor(){inputEditorEpoch++;setInputEditor(undefined);}
  let inputPreviewScope='';
  createEffect(()=>{const scope=JSON.stringify(associationScope(quickScope()));if(scope===inputPreviewScope)return;inputPreviewScope=scope;inputPreviews.update(scope);closeInputEditor();});
  createEffect(()=>inputPreviews.retain(nativeDraftAttachments()));
  onCleanup(()=>{inputEditorEpoch++;inputPreviews.dispose();});
  async function loadInputEditor(){const selected=inputEditor();if(!selected)return;const epoch=++inputEditorEpoch,current=navigation.capture(),cid=conversationId();setInputEditor({...selected,loading:true,error:undefined,snapshot:undefined});
    const accept=()=>epoch===inputEditorEpoch&&current()&&conversationId()===cid;
    try{const snapshot=await inputPreviews.full(selected.item,accept);if(accept())setInputEditor({...selected,loading:false,snapshot});}catch(error){if(accept())setInputEditor({...selected,loading:false,error:uiLocale()==='en'?'The saved image is unavailable. Your message and attachment are retained.':'暂时无法读取保存的原图，消息和附件均已保留。'});}
  }
  function openInputEditor(item:NativeDraftAttachment,readOnly=false){setInputEditorReturn(document.activeElement instanceof HTMLElement?document.activeElement:undefined);setInputEditor({item,readOnly,returnTarget:document.activeElement instanceof HTMLElement?document.activeElement:undefined,loading:true});void loadInputEditor();}
  function saveInputRegions(regions:NativeRegion[]){const selected=inputEditor();if(!selected||selected.readOnly)return;setNativeDraftAttachments(items=>items.map(item=>item===selected.item?{...item,regions:structuredClone(regions)}:item));setInputEditorReturn(undefined);closeInputEditor();}
  async function captureNativeContext(visual:boolean){
    if(!nativeController||nativeContextBusy()||!nativeBridge?.getNativeInputPreview)return;
    if(nativeDraftAttachments().length>=20){setError('本轮最多20个输入，请先整理当前附件。');return;}
    const current=navigation.capture(),cid=conversationId(),pid=project()?.id,accept=()=>current()&&cid===conversationId()&&pid===project()?.id;
    setNativeContextBusy(true);setError('');
    try{const snapshot=await nativeController.capture({intent:visual?'用户选择当前画面作为聊天输入':'用户选择当前页面作为聊天输入',need:{metadata:true,accessibility:!visual,visual,ocr:false},...(activeSession()?{taskRef:{id:activeSession()!.id,revision:activeSession()!.revision,sha256:activeSession()!.sha256}}:{})},accept);
      if(!accept())return;
      const item=nativeCapturedInput(snapshot,visual),ref=item.context_ref;
      if(nativeDraftAttachments().length>=20)throw new Error('本轮最多20个输入，请先整理当前附件。');
      if(!nativeDraftAttachments().some(old=>sameNativeReference(old.context_ref,ref)&&old.preview_sha256===item.preview_sha256))setNativeDraftAttachments(items=>[...items,item]);
      setReviewContinue(undefined);setReviewRetryContext(undefined);if(pageReview())reviewEditEpoch++;
      focusComposer();
    }catch(error){if(accept()){setError(authoredErrorCopy(error));focusComposer();}}finally{setNativeContextBusy(false);}
  }
  onMount(()=>{const release=nativeBridge?.onAnnotationRequested?.(()=>{void captureNativeContext(true);});if(release)onCleanup(release);});
  function nativeArtifactSource(artifact:NativeMessageArtifact){const value=artifact.file_source_ref??artifact.source_ref;return value?.kind==='local_file'&&!!value.project_id&&!!value.workspace_id&&!!value.path&&typeof value.sha256==='string'?value:undefined;}
  function nativeArtifactAvailable(artifact:NativeMessageArtifact,message?:NativeMessage){const report=nativeReportArtifact(artifact,message);return report?!!nativeBridge?.openReportArtifact&&report.projectId===project()?.id:artifact.kind!=='docx_revision'&&!!nativeArtifactSource(artifact)&&!!nativeBridge?.openDocument;}
  async function openNativeArtifact(artifact:NativeMessageArtifact,message?:NativeMessage){const report=nativeReportArtifact(artifact,message);if(report&&nativeBridge){await action('打开实际产物',async()=>{const current=navigation.capture(),scope=associationScope(quickScope());await openNativeReportArtifact(nativeBridge,artifact,scope,{message,current:()=>current()&&sameDiscussionScope(scope,quickScope())});if(!current()||!sameDiscussionScope(scope,quickScope()))return;await discussionFiles?.refresh();if(current()&&sameDiscussionScope(scope,quickScope()))setNotice('产物打开请求已交给系统关联软件');});return;}const source=artifact.kind!=='docx_revision'?nativeArtifactSource(artifact):undefined;if(!source||!nativeBridge?.openDocument)return;await action('打开实际产物',async()=>{const current=navigation.capture(),scope=associationScope(quickScope());await adoptDirectSource(source);if(!current()||project()?.id!==source.project_id||!sameDiscussionScope(scope,quickScope()))throw new Error('当前工作已切换，已核对的文件未自动打开');const receipt=await openFeedbackDocument(source,{scope},current);if((receipt as {status?:string})?.status!=='OPEN_REQUESTED')throw new Error('系统未确认产物打开请求');setNotice('产物打开请求已交给系统关联软件');});}
  function nativeLocalNotice(value:NativeMessage){if(value.local_persistence_status==='FAILED')return '回复已收到，本机保存未成功；请核查原操作，勿重复发送。';const projection=value.local_projection;return projection&&typeof projection==='object'&&'complete' in projection&&projection.complete===false?'本地显示的执行记录不完整，可核查原操作查看实际回执。':undefined;}
  function nativeRecord(input:string,value:NativeMessage,feedbackPreview=false,displayIdentity?:NativeFeedbackDisplayIdentity){if(value.conversation_id&&value.conversation_id!==conversationId())return false;let accepted=true;setNativeMessages(items=>{const previous=items.find(item=>item.message.operation_id===value.operation_id);if(previous&&['RETURNED','SUCCEEDED','COMPLETED','FAILED','CANCELLED','TIMED_OUT'].includes(previous.message.status)&&nativeMessagePending(value)){accepted=false;return items;}const identity=displayIdentity??previous?.feedback_display_identity;const privatePreview=feedbackPreview||previous?.feedback_preview||!!identity;if(identity?.purpose==='FILE_REWORK_PREVIEW')value={...value,project_id:value.project_id??identity.project_id,conversation_id:value.conversation_id??identity.conversation_id};if(privatePreview&&previous)value={...value,project_id:value.project_id??previous.message.project_id,conversation_id:value.conversation_id??previous.message.conversation_id};return [...items.filter(item=>item.message.operation_id!==value.operation_id),{input:previous?.input??input,message:value,...privatePreview?{feedback_preview:true as const}:{},...identity?{feedback_display_identity:identity}:{}}];});return accepted;}
  async function askNativeRework(feedback:NativeFileFeedback,proposalRef?:RevisionRef,mapping?:ReworkRecord){if(nativePending())throw new Error('原 Agent 正在处理当前提问，请先核查或停止原执行');if(project()?.id!==feedback.payload.project_id)throw new Error('反馈属于原项目，请切回原项目核对');const current=navigation.capture(),scope=nativeFeedbackLease(),source=await nativeReworkApi.previewSource(feedback);if(!current()||scope!==nativeFeedbackLease()||project()?.id!==source.project_id)throw new Error('当前工作已切换，尚未发送返工提问');await sendNativeMessage(reworkPreviewQuestion(feedback,proposalRef,mapping),[],source,undefined,{text:feedback.payload.comment,feedbackRef:feedback.feedback_ref});}
  const reworkRecovery=new ReworkPreviewRecovery(nativeReworkApi,nativeFeedbackApi,(feedback,records)=>{if(nativeDisposed||project()?.id!==feedback.payload.project_id)return;saveNativeFileFeedback(feedback);setNativeReworkRecords(list=>[...list.filter(item=>!records.some(value=>value.record_ref.id===item.record_ref.id)),...records]);},(pid,reason)=>{if(project()?.id===pid)setNativeReworkError(reason);});
  createEffect(()=>{const pid=project()?.id??'',previews=nativeMessages().flatMap(item=>Array.isArray(item.message.rework_previews)?item.message.rework_previews:[]);void reworkRecovery.restore(pid,previews);});
  onCleanup(()=>reworkRecovery.stop());
  function persistNativeHistory(){try{saveNativeHistory(localStorage,nativeMessages().slice(-30).map(item=>({input:item.input,operation_id:item.message.operation_id,...item.feedback_display_identity?{feedback_display_identity:item.feedback_display_identity}:{}})),conversationId());}catch{setError('会话索引未能保存，原执行仍可按操作编号核查');}}
  function NativeAssistantResult(props:{record:{input:string;message:NativeMessage;feedback_preview?:true;feedback_display_identity?:NativeFeedbackDisplayIdentity}}){
    const record={get input(){return props.record.input;},get message(){return props.record.message;},get feedback_preview(){return props.record.feedback_preview;},get feedback_display_identity(){return props.record.feedback_display_identity;}};
    return <div class="as-native-agent-message" data-status={record.message.status}><NativeMessageFeedback message={record.message} replyText={nativeReplyText(record.message)} notice={nativeLocalNotice(record.message)} busy={!!busy()} canInspect={!!nativeBridge?.inspectNativeMessage} scopeKey={JSON.stringify([project()?.id??null,conversationId()??null])} onInspect={()=>inspectNativeOperation(record.message.operation_id,record.input,true)} onOpenLink={href=>openAssistantLink(record.message,href)}/><Show when={quickBridge?.openQuickDraft&&savedQuickReply(record.message.operation_id)}>{saved=><button type="button" disabled={!!busy()} onClick={()=>void viewSavedQuickReply(saved())}>{uiText("浮窗查看")}</button>}</Show><Show when={record.message.status==='NOT_CONFIGURED'}><button type="button" class="as-connection-recovery" onClick={()=>void openProviders()}>{uiText("连接模型")}<Icon name="arrow-right"/></button></Show><Show when={nativeMessagePending(record.message)||canStopNativeReview(record.message)}><button disabled={!nativeBridge?.cancelNativeMessage} onClick={()=>void cancelNativeMessage(record.message.operation_id)}>{uiText(record.message.cancellation_status==='UNKNOWN'||(record.message.review as {stop_control?:unknown}|undefined)?.stop_control?'核查原操作':'停止当前执行')}</button></Show><Show when={record.message.owner_stop_confirmed===true}><small>{uiText("已停止")}</small></Show><NativeReviewActions message={record.message} onGuide={()=>beginPageGuidance(record.message)} onContinue={()=>continueCapabilityTask(record.message)} control={controlPageReview} refresh={()=>void inspectNativeOperation(record.message.operation_id,record.input)}/><Show when={ordinaryControlScope(record.message)?.can_guide||ordinaryControlScope(record.message)?.can_status}><button type="button" disabled={!!busy()} onClick={()=>beginPageGuidance(record.message)}>{uiText('补充当前任务')}</button></Show><Show when={ordinaryControlSummaries(record.message).length}><details><summary>{uiText('补充记录')} · {ordinaryControlSummaries(record.message).length}</summary><For each={ordinaryControlSummaries(record.message)}>{control=><div><p style={{'white-space':'pre-wrap'}}>{control.raw_text}</p><small>{uiText(controlNotice(control))}</small></div>}</For></details></Show><NativeContextRecovery message={record.message} client={nativeRecoveryClient} version={nativeRecoveryVersion()} locale={uiLocale()} captureCurrent={captureNativeRecoveryCurrent} onOpen={openNativeRecoveredChat}/><NativeActionProposals message={record.message} scope={{projectId:project()?.id??null,conversationId:conversationId()??''}} contextRef={currentNativeReference(currentNativeContext())} onConfigureModel={()=>void openProviders()}/><NativeMessageArtifacts message={record.message} busy={!!busy()} canOpen={artifact=>nativeArtifactAvailable(artifact,record.message)} onOpen={artifact=>void openNativeArtifact(artifact,record.message)} onInspect={nativeBridge?.inspectNativeMessage?()=>void inspectNativeOperation(record.message.operation_id,record.input):undefined}/><Show when={messagePreviewTargets(record.message).length}><div class="as-native-rework-reference"><For each={messagePreviewTargets(record.message)}>{(target,index)=><button type="button" disabled={!!busy()} onClick={event=>void openNativeMessagePreview(target,event.currentTarget)}>{uiText('查看预览')}{messagePreviewTargets(record.message).length>1?' '+(index()+1):''}</button>}</For></div></Show><Show when={record.message.events?.length}><details><summary>{uiText("实际工具与执行进度 · ")}{record.message.events!.length}</summary><pre class="as-json">{JSON.stringify(record.message.events!.slice(-20),null,2)}</pre></details></Show></div>;
  }
  function NativeUserInput(props:{record:{input:string;message:NativeMessage;feedback_preview?:true;feedback_display_identity?:NativeFeedbackDisplayIdentity}}){const row=()=>feedbackDisplays().find(item=>item.operation_id===props.record.message.operation_id&&item.conversation_id===(props.record.message.conversation_id??conversationId()));const [display]=createResource(()=>JSON.stringify([props.record.input,row()??null,props.record.message.project_id,props.record.message.conversation_id,props.record.message.operation_id]),async key=>{const [raw,meta,pid,cid,operation_id]=JSON.parse(key);const identity=props.record.feedback_display_identity;if(identity&&(identity.purpose!=='FILE_REWORK_PREVIEW'||identity.raw_sha256!==meta?.raw_sha256||identity.project_id!==pid||identity.conversation_id!==cid||identity.operation_id!==operation_id))return undefined;return meta?verifiedFeedbackDisplay(meta,raw,{project_id:pid,conversation_id:cid,operation_id}):undefined;});const host=()=>!!row()||!!props.record.feedback_display_identity||props.record.feedback_preview||!!props.record.message.rework_previews?.length;return <><p>{host()?(!display.loading?display():undefined)??uiText('按文件反馈准备修改预览'):props.record.input}</p><Show when={Array.isArray(props.record.message.input_attachments)&&props.record.message.input_attachments.length}><details class="as-message-inputs"><summary>{uiLocale()==='en'?`${(props.record.message.input_attachments as NativeInputAttachment[]).length} attached inputs`:`${(props.record.message.input_attachments as NativeInputAttachment[]).length}个引用`}</summary><For each={props.record.message.input_attachments as NativeInputAttachment[]}>{item=><div><Show when={item.kind==='SCREENSHOT'} fallback={<strong>{item.label}</strong>}><button type="button" onClick={()=>openInputEditor({context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,preview_sha256:item.image_sha256},true)}>{item.label}</button></Show><Show when={item.user_note}><p>{item.user_note}</p></Show></div>}</For></details></Show></>;}
  function nativeReplyText(value:NativeMessage){return ['RETURNED','SUCCEEDED','COMPLETED'].includes(value.status)&&typeof value.text==='string'&&value.text.trim()?value.text:undefined;}
  async function openAssistantLink(value:NativeMessage,url:string){
    const text=nativeReplyText(value),bridge=nativeBridge;if(!text||!bridge?.openNativeReplyLink)return;
    const cid=conversationId(),scope=quickScope();
    await action('打开链接',async()=>{
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(text)));
      if(cid!==conversationId()||!sameQuickScope(scope,quickScope())||!nativeMessages().some(row=>row.message.operation_id===value.operation_id&&nativeReplyText(row.message)===text))throw new Error('当前工作已切换，未打开原回复链接');
      const text_sha256=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
      await bridge.openNativeReplyLink!({operationId:value.operation_id,text_sha256,url});
    });
  }
  let nativePollBusy=false;
  function scheduleNativePoll(delay=1200){if(!nativeDisposed&&!nativeTimer&&!nativePollBusy&&nativeObserved.size)nativeTimer=setTimeout(()=>void pollNativeMessages(),delay);}
  async function pollNativeMessages(){
    nativeTimer=undefined;if(nativeDisposed||nativePollBusy||!nativeObserved.size)return;
    const cid=conversationId(),generation=historyGeneration,[id,text]=nativeObserved.entries().next().value!;nativeObserved.delete(id);nativePollBusy=true;
    try{const result=await nativeClient!.inspect(id);if(nativeDisposed||conversationId()!==cid||historyGeneration!==generation)return;const accepted=nativeRecord(text,result);persistNativeHistory();if(accepted!==false&&nativeMessagePending(result))nativeObserved.set(id,text);else if(reviewContinue()===id)setReviewContinue(undefined);}
    catch(e){if(!nativeDisposed&&conversationId()===cid&&historyGeneration===generation){nativeRecord(text,nativeInspectionFailure(id,e));persistNativeHistory();}}
    finally{nativePollBusy=false;if(!nativeDisposed){setNativePending(nativeObserved.keys().next().value);scheduleNativePoll();}}
  }
  function forgetNativeMessage(key:string){nativeObserved.delete(key);if(reviewContinue()===key)setReviewContinue(undefined);if(!nativeObserved.size&&nativeTimer){clearTimeout(nativeTimer);nativeTimer=undefined;}setNativePending(nativeObserved.keys().next().value);scheduleNativePoll();}
  function observeNativeMessage(key:string,input:string){if(!nativeObserved.has(key)&&nativeObserved.size>=5)return;nativeObserved.set(key,input);setNativePending(key);scheduleNativePoll(800);}


  async function sendNativeMessage(rawText:string,attachments:NativeDraftAttachment[]=[],fileSourceRef?:SourceRef,fileLinkRef?:FileLinkRef,displayInput?:{text:string;feedbackRef:NativeRef}){if(!threadWriteAllowed())return;if(originalWorkInputChoice()){setError(uiLocale()==='en'?'Continue from the original work input, or remove its work link first.':'请从原工作输入继续，或先移除原工作关联。');return;}
    const ordinary=ordinaryGuidanceParent();if(displayInput&&(ordinary||pageReview())){setError('请先回到普通任务，再准备文件修改预览。');return;}if(ordinary)return sendOrdinaryGuidance(ordinary,rawText);
    if(nativePending()&&!pageReview()&&!ordinaryNewQuestion()){setError('当前任务暂不能补充，请先核查原操作');return;}
    if(pageReview()&&reviewUnknown()){setError('结果尚未确认，请核查原操作');return;}
    const bridge=nativeBridge;if(!bridge?.submitNativeMessage||!bridge.inspectNativeMessage){setError('原生 Agent 会话接口尚未连接；草稿已保留，可在项目工作台使用已连接的文件与需求工具。');return;}
    if(!rawText.trim()&&!attachments.length)return;
    const continuation=reviewContinue(),parent=continuation?nativeMessages().find(row=>row.message.operation_id===continuation)?.message:undefined;
    if(continuation&&pageReview()){const r=parent?.review as {review_ref:NativeRef;selection:NativePageSelection;can_guide:boolean}|undefined;if(!r?.can_guide||!sameReview(r.selection,pageReview()!.selection)||!sameReview(reviewProviderForOperation(continuation),quickScope().providerProfileRef)||attachments.length){setError('此条暂不能补充，请核查原操作或新建问题');return;}const key=operationId(),cid=conversationId(),indexKey='eeas-native-review-control-index-v1',slot='guidance:'+cid+':'+continuation;let input:ReviewControl;try{const index=JSON.parse(localStorage.getItem(indexKey)??'{}') as Record<string,ReviewControl>;if(!index||Array.isArray(index)||typeof index!=='object'||JSON.stringify(index).length>262144||Object.keys(index).length>16)throw new Error('结果尚未确认，请核查原操作');const old=index[slot],retained=(parent?.local_review_controls as ReviewControl[]|undefined)?.find(row=>row.kind==='guidance'&&row.parentOperationId===continuation);if(old||retained){input={...(old??retained),inspect:true};if(input.parentOperationId!==continuation||!sameReview(input.body.expected_review_ref,r.review_ref))throw new Error('结果尚未确认，请核查原操作');}else{if(Object.keys(index).length>=16)throw new Error('结果尚未确认，请核查原操作');input={parentOperationId:continuation,kind:'guidance',body:{operation_id:key,expected_review_ref:r.review_ref,raw_text:rawText}};index[slot]=input;localStorage.setItem(indexKey,JSON.stringify(index));if(localStorage.getItem(indexKey)!==JSON.stringify(index))throw new Error('问题索引未能保存，尚未发送');}const result=await controlPageReview(input);if(conversationId()!==cid)return;if(['ACCEPTED','RETURNED','COMPLETED'].includes(result.status)){delete index[slot];localStorage.setItem(indexKey,JSON.stringify(index));if(prompt()===input.body.raw_text)setPrompt('');}else if(result.status==='FAILED_NOT_DISPATCHED'){delete index[slot];localStorage.setItem(indexKey,JSON.stringify(index));setError('此条暂不能补充，请核查原操作或新建问题');}else setError('结果尚未确认，请核查原操作');}catch(e){setError(authoredErrorCopy(e));}return;}
    const key=operationId();let request;try{request={...nativeMessageInput(key,rawText,attachments,currentNativeContext(),providers().find(item=>item.profile_ref.id===providerId())?.profile_ref,fileSourceRef,conversationId(),fileLinkRef,consentedReviewInput(pageReview(),reviewCatalog(),reviewHintSelection()),threadScope()?.conversation_ref,contextChoice()?.selection_id),...(pageReview()&&reviewRetryContext()?{contextRef:reviewRetryContext()}:{}),...(project()?{projectId:project()!.id}:{}),conversationId:conversationId()};}catch(error){setError(authoredErrorCopy(error));return;}
    const contextChoiceLease=contextChoice()?.operation_id;
    const text=request.rawText,savedText=prompt(),ownerConversation=conversationId(),ownedReviewEpoch=reviewEditEpoch;try{if(ownerConversation)saveConversationLabel(localStorage,project()?.id??null,ownerConversation,displayInput?.text??rawText);}catch{/* Optional metadata never changes send admission. */}let displayIdentity:NativeFeedbackDisplayIdentity|undefined;if(displayInput){const lease=nativeFeedbackLease();if(!fileSourceRef||!ownerConversation)throw new Error('反馈展示缺少原讨论与来源');try{const display=await saveFeedbackDisplay(localStorage,{project_id:fileSourceRef.project_id,conversation_id:ownerConversation,operation_id:key,feedback_ref:displayInput.feedbackRef,source_ref:fileSourceRef,display_input:displayInput.text},text);displayIdentity={purpose:'FILE_REWORK_PREVIEW',project_id:display.project_id,conversation_id:display.conversation_id,operation_id:display.operation_id,raw_sha256:display.raw_sha256};saveNativeHistory(localStorage,[{input:text,operation_id:key,feedback_display_identity:displayIdentity}],ownerConversation);if(!readNativeHistory(localStorage,ownerConversation).some(row=>row.operation_id===key&&sameReview(row.feedback_display_identity,displayIdentity)))throw new Error('反馈展示信息未保存，尚未发送');if(lease!==nativeFeedbackLease())return;setFeedbackDisplayVersion(v=>v+1);}catch(error){setError(authoredErrorCopy(error));return;}}
    setOrdinaryNewQuestion(false);setNativePending(key);setError('');nativeRecord(text,{operation_id:key,status:'REQUESTING',...displayInput?{project_id:fileSourceRef!.project_id,conversation_id:ownerConversation}:{}},!!displayInput,displayIdentity);
    
    try{const result=await dispatchNativeMessage(nativeClient!,localStorage,request);if(nativeDisposed||conversationId()!==ownerConversation)return;nativeRecord(text,result);persistNativeHistory();if(request.contextSelectionId&&contextChoice()?.selection_id===request.contextSelectionId&&contextChoice()?.operation_id===contextChoiceLease&&['PROCESSING','ACCEPTED','RUNNING','QUEUED','RETURNED','COMPLETED','SUCCEEDED'].includes(result.status))clearContextChoice();const sameEdit=!request.nativeReview||ownedReviewEpoch===reviewEditEpoch&&sameReview(request.nativeReview.selection,pageReview()?.selection);if(request.nativeReview&&sameEdit){setPageReview(current=>current?resetReviewConsent(current):current);if(result.status==='UNKNOWN_OUTCOME'){setReviewUnknown(key);localStorage.setItem('eeas-native-review-unresolved:'+ownerConversation,key);}else if(result.dispatch_state==='NOT_DISPATCHED'&&result.execution_started===false){setReviewHintSelection(undefined);const ref=(result.review as {context_ref?:NativeRef}|undefined)?.context_ref;if(ref&&nativeReference(ref))setReviewRetryContext(ref);}else{setReviewRetryContext(undefined);localStorage.removeItem('eeas-native-review-unresolved:'+ownerConversation);}}if(['NOT_CONFIGURED','UNAVAILABLE','FAILED','UNKNOWN_OUTCOME','NOT_DISPATCHED'].includes(result.status)||result.dispatch_state==='NOT_DISPATCHED'){setError(result.error?.message??result.reason??result.status);}else{if(sameEdit){if(rawText===savedText&&prompt()===savedText)setPrompt('');setNativeDraftAttachments(items=>items.filter(item=>!attachments.includes(item)));}}void refreshHistory(false,true);if(threadScope())void refreshThread();if(nativeMessagePending(result)){if(sameEdit&&(result.review&&sameReview(request.nativeReview?.selection,pageReview()?.selection)||ordinaryControlScope(result)))setReviewContinue(key);observeNativeMessage(key,text);}else if(!nativeObserved.size)setNativePending(undefined);
    }catch(error){if(conversationId()!==ownerConversation)return;nativeRecord(text,{operation_id:key,status:error instanceof NativeRequestIndexError?'NOT_DISPATCHED':'UNKNOWN_OUTCOME',error:{message:message(error)}});if(request.nativeReview&&ownedReviewEpoch===reviewEditEpoch&&sameReview(request.nativeReview.selection,pageReview()?.selection)&&!(error instanceof NativeRequestIndexError)){setReviewUnknown(key);localStorage.setItem('eeas-native-review-unresolved:'+ownerConversation,key);setPageReview(current=>current?resetReviewConsent(current):current);}setError(error instanceof NativeRequestIndexError?message(error):'发送结果尚未确认，草稿已保留；请核查原执行，勿重复发送。');setNativePending(undefined);if(!(error instanceof NativeRequestIndexError))persistNativeHistory();}
  }
  async function inspectNativeOperation(key:string,input:string,fromMessageFeedback=false){
    const ownerConversation=conversationId(),generation=historyGeneration,ownerProject=project()?.id;
    const originalMessage=nativeMessages().find(row=>row.message.operation_id===key)?.message;
    const originalScope={project_id:originalMessage?.project_id,conversation_id:originalMessage?.conversation_id};
    const feedbackCurrent=()=>conversationId()===ownerConversation&&historyGeneration===generation&&project()?.id===ownerProject&&nativeMessages().some(row=>row.message.operation_id===key);
    await action('核查原操作',async()=>{
      let readingMessage=false;
      try{
        const parent=nativeMessages().find(row=>row.message.operation_id===key)?.message;
        if(parent&&ownerConversation&&ordinaryControlScope(parent)){
          const raw=localStorage.getItem('eeas-native-review-control-index-v1')??'{}';
          if(raw.length>262144)throw new Error('结果尚未确认，请核查原操作');
          const index=JSON.parse(raw),slot='guidance:'+ownerConversation+':'+key;
          if(index[slot]||(parent.local_review_controls as ReviewControl[]|undefined)?.some(row=>row.kind==='guidance'))await dispatchOrdinaryGuidance(localStorage,parent,ownerConversation,'','inspect-only',controlPageReview);
        }
        readingMessage=true;
        const value=await nativeClient!.inspect(key);if(conversationId()!==ownerConversation||fromMessageFeedback&&!feedbackCurrent())return;nativeRecord(input,value);persistNativeHistory();if(nativeMessagePending(value))observeNativeMessage(key,input);else forgetNativeMessage(key);
      }catch(error){
        if(!fromMessageFeedback)throw error;
        if(!feedbackCurrent())return;
        nativeRecord(input,{...nativeInspectionFailure(key,readingMessage?error:{message:message(error)}),...originalScope});persistNativeHistory();
      }
    });
  }
  function canStopNativeReview(value:NativeMessage){return value.status==='UNKNOWN_OUTCOME'&&((value.review as {can_cancel?:boolean}|undefined)?.can_cancel===true||!!value.native_message_ref&&(value.control_capabilities as {can_cancel?:boolean}|undefined)?.can_cancel===true);}
  async function cancelNativeMessage(key:string){
    if(!nativeBridge?.cancelNativeMessage)return;
    const row=nativeMessages().find(item=>item.message.operation_id===key),ordinary=row?.message.native_message_ref?ordinaryControlScope(row.message):null,review=(row?.message.review??(ordinary?{review_ref:ordinary.ref,can_cancel:ordinary.can_cancel}:undefined)) as {review_ref?:NativeRef;can_cancel?:boolean;stop_control?:{operation_id:string;status:string}}|undefined;
    if(!review){try{const result=await nativeClient!.cancel(key);nativeRecord('',result);if(!nativeMessagePending(result))forgetNativeMessage(key);}catch(error){setError(authoredErrorCopy(error));}return;}
    const cid=conversationId(),slot='stop:'+key,indexKey='eeas-native-review-control-index-v1';let index:Record<string,unknown>;
    function releaseConfirmedStop(){const current=nativeMessages().find(item=>item.message.operation_id===key)?.message;if(conversationId()!==cid||current?.owner_stop_confirmed!==true||!sameReview(ordinary?current.native_message_ref:(current.review as {review_ref?:NativeRef}|undefined)?.review_ref,review?.review_ref))return;try{const raw=localStorage.getItem(indexKey)??'{}';if(raw.length>262144)throw new Error();const latest=JSON.parse(raw);if(!latest||Array.isArray(latest)||typeof latest!=='object'||Object.keys(latest).length>16)throw new Error();if(sameReview(latest[slot],index[slot])){delete latest[slot];localStorage.setItem(indexKey,JSON.stringify(latest));}}catch{setError('会话索引未能保存，原执行仍可按操作编号核查');}}
    try{const raw=localStorage.getItem(indexKey)??'{}';if(raw.length>262144)throw new Error('问题索引未能保存，尚未发送');index=JSON.parse(raw);if(!index||Array.isArray(index)||typeof index!=='object'||Object.keys(index).length>16||!nativeReference(review.review_ref))throw new Error('问题索引未能保存，尚未发送');
      const prior=index[slot] as {kind?:string;parentOperationId?:string;expected_review_ref?:NativeRef;expected_native_message_ref?:NativeRef}|undefined;
      if(prior){if(prior.kind!==(ordinary?'NATIVE_MESSAGE_STOP':'REVIEW_STOP')||prior.parentOperationId!==key||!sameReview(ordinary?prior.expected_native_message_ref:prior.expected_review_ref,review.review_ref))throw new Error('结果尚未确认，请核查原操作');await inspectNativeOperation(key,row!.input);releaseConfirmedStop();return;}
      if(review.stop_control){await inspectNativeOperation(key,row!.input);return;}
      if(!nativeMessagePending(row!.message)&&!canStopNativeReview(row!.message))return;
      if(Object.keys(index).length>=16)throw new Error('问题索引未能保存，尚未发送');
      index[slot]={kind:ordinary?'NATIVE_MESSAGE_STOP':'REVIEW_STOP',parentOperationId:key,...ordinary?{expected_native_message_ref:review.review_ref}:{expected_review_ref:review.review_ref}};const serialized=JSON.stringify(index);if(serialized.length>262144)throw new Error('问题索引未能保存，尚未发送');localStorage.setItem(indexKey,serialized);if(localStorage.getItem(indexKey)!==serialized)throw new Error('问题索引未能保存，尚未发送');
    }catch(error){setError(authoredErrorCopy(error));return;}
    try{const result=await nativeClient!.cancel(key);if(conversationId()!==cid)return;nativeRecord(row!.input,result);persistNativeHistory();if(!nativeMessagePending(result))forgetNativeMessage(key);releaseConfirmedStop();}
    catch(error){if(conversationId()!==cid)return;nativeRecord(row!.input,{...row!.message,cancellation_status:'UNKNOWN'});setError('停止请求结果尚未确认；请核查原操作，不会重复请求。');}
  }
  onMount(()=>{restoreNativeDraft();if(nativeController)void nativeController.start().catch(error=>setError(authoredErrorCopy(error)));});
  onCleanup(()=>{nativeDisposed=true;originalGeneration++;if(originalTimer)clearTimeout(originalTimer);nativeController?.stop();if(nativeTimer)clearTimeout(nativeTimer);});

  const [reviewTab,setReviewTab]=createSignal<'attention'|'current'>('current');
  const [draftRecords,setDraftRecords]=createSignal<WorkbenchState[]>([]),[draftReadError,setDraftReadError]=createSignal(''),[draftNextOffset,setDraftNextOffset]=createSignal<number|null>(null),[draftListBusy,setDraftListBusy]=createSignal(false);let draftListGeneration=0;
  const reviewRows=createMemo(()=>reviewObjects(project()?.id??'',{sessions:discoverySessions(),proposals:proposals(),books:books(),harnesses:harnesses(),reports:reports(),deliveries:feed().flatMap(item=>item.artifact?.type==='planning-delivery'&&item.artifact.scope.projectId===project()?.id?[item.artifact.data as DeliveryOperation]:[])}));
  const quickBridge=nativeBridge as (typeof nativeBridge & QuickBridge);
  const [quickVersion,setQuickVersion]=createSignal(0);
  const quickInbox=new QuickInbox(localStorage,quickBridge??{},()=>setQuickVersion(value=>value+1));
  const quickRecords=()=>{quickVersion();return quickInbox.records;};
  const quickError=()=>{quickVersion();return quickInbox.error_copy??quickInbox.error;};
  function savedQuickReply(operationId:string){return quickRecords().filter(record=>record.delivery==='SEND'&&record.operationId===operationId&&record.message?.operation_id===operationId&&!!nativeReplyText(record.message)&&sameQuickScope(record.scope,quickScope())).sort((a,b)=>b.revision-a.revision)[0];}
  async function viewSavedQuickReply(record:QuickRecord){await action('浮窗查看',async()=>{
    const current=savedQuickReply(record.operationId??'');
    if(!current||current.id!==record.id||current.revision!==record.revision||current.sha256!==record.sha256||!quickBridge?.openQuickDraft)throw new Error('原记录已变化，请核查原操作');
    const opened=await quickBridge.openQuickDraft(quickRef(current)) as {draft?:{readonly?:boolean;id?:string;operationId?:string};result?:{operation_id?:string}}|undefined;
    if(opened?.draft?.readonly!==true||opened.draft.id!==current.id||opened.draft.operationId!==current.operationId||opened.result?.operation_id!==current.operationId)throw new Error('浮窗暂不可用；原回复保留。');
  });}
  const conversationId=createMemo(()=>{const thread=threadScope();if(thread)return thread.next_message.conversation_id;const selected=selectedDiscussion();if(selected?.key.kind==='conversation')return undefined;if(selected&&selected.key.projectId===(project()?.id??null))return selected.conversationId;try{return conversationIdFor(localStorage,fileConversationBasis({projectId:project()?.id,sessionId:activeSession()?.id},toolMode()==='original'?originalSession()?.session_ref??null:undefined));}catch{return undefined;}});
  const displayedCommands=createMemo(()=>displayedCommandKeys(commandDisplayCoverage(),originalSession(),commandContextKey()));
  const quickScopeValue=createMemo(()=>{
    const scope=threadScope(),owner=originalSession(),profile=providers().find(item=>item.profile_ref.id===providerId()),workChoice=originalWorkInputChoice();
    const sharedOwner=scope&&owner&&scope.sources.some(source=>source.original_source?.kind==='original'&&source.original_source.project_id===owner.project_id&&sameNativeReference(source.original_source.session_ref,owner.session_ref));
    return JSON.stringify({...(project()?{projectId:project()!.id}:{}),...(workspace()?.project_id===project()?.id&&workspace()?{workspaceId:workspace()!.id}:{}),
      ...(toolMode()!=='original'&&activeSession()?{sessionId:activeSession()!.id}:{}),...(profile?{providerProfileRef:profile.profile_ref}:workChoice&&sharedOwner&&originalWorkChoiceMatches(workChoice,owner)&&owner.provider_profile_ref?{providerProfileRef:owner.provider_profile_ref}:{}),...(conversationId()?{conversationId:conversationId()}:{}),
      ...(scope?{conversationRef:scope.conversation_ref,agent:originalAgent()}:{}),...(workChoice?(sharedOwner&&originalWorkChoiceMatches(workChoice,owner)?{originalWorkChoice:workChoice}:{workInputUnavailable:true}):{}),...(contextChoice()?{contextSelectionId:contextChoice()!.selection_id,...(contextChoice()!.operation_id?{contextSelectionOperationId:contextChoice()!.operation_id}:{})}:{}),...(sharedOwner&&owner.generation>=1?{originalSource:{project_id:owner.project_id,session_ref:owner.session_ref,generation:owner.generation}}:{})});
  });
  const quickScope=():QuickScope=>({...JSON.parse(quickScopeValue()),generation:quickScopeGeneration});
  createEffect(()=>{const choice=contextChoice();if(!choice?.operation_id)return;const submitted=quickRecords().some(record=>record.delivery==='SEND'&&record.message?.role!=='LOCAL_STATUS'&&record.message?.original_work_status_query!==true&&record.scope.contextSelectionId===choice.selection_id&&record.scope.contextSelectionOperationId===choice.operation_id&&(record.scope.projectId??null)===choice.project_id&&(record.scope.conversationRef?.id??null)===choice.conversation_id&&record.operationId===record.message?.operation_id&&['PROCESSING','ACCEPTED','RUNNING','QUEUED','RETURNED','COMPLETED','SUCCEEDED'].includes(record.message?.status??''));if(submitted)clearContextChoice();});

  function hasConversationContent(){
    if(threadAccess()!=='ready'&&selectedDiscussion()?.key.kind==='conversation'||threadScope()&&(threadMessages().length||threadLoading()))return true;
    const current=quickScope();
    if(nativeMessages().some(({message})=>message.conversation_id?message.conversation_id===current.conversationId:!message.project_id||message.project_id===current.projectId))return true;
    if(quickRecords().some(record=>record.delivery==='SEND'&&sameQuickScope(record.scope,current)))return true;
    const discovery=activeSession();if(discovery&&discovery.project_id===current.projectId&&discovery.id===current.sessionId)return true;
    if(!current.sessionId&&directResults().some(result=>result.project_id===current.projectId))return true;
    const original=originalSession();if(toolMode()==='original'&&original&&(!current.projectId||original.project_id===current.projectId)&&original.session_ref.id===current.sessionId&&(original.messages.length||originalOperations().some(operation=>!operation.session_ref||sameReview(operation.session_ref,original.session_ref))||originalRecovery().some(operation=>!operation.receipt)||original.pending_questions.length||original.pending_permissions.length))return true;
    const running=activeOperation();return !!running&&running.projectId===current.projectId&&(!running.sessionId||running.sessionId===current.sessionId);
  }
  const linkedFile=()=>{const state=discussionFileState();return state?.selected&&sameDiscussionScope(state.scope,quickScope())?state.selected:undefined;};
  const discussionFiles=nativeBridge?.getQuickFileContext?new DiscussionFileContext(nativeBridge,value=>{setDiscussionFileState(value);if(value?.selected&&sameDiscussionScope(value.scope,quickScope())){const pending=openingFeedbackFile;if(pending&&sameDiscussionScope(pending.scope,value.scope)){if(pending.observedLink&&!sameFileLink(pending.observedLink,value.selected.fileLinkRef))pending.superseded=true;else pending.observedLink=value.selected.fileLinkRef;}if(directFile()&&!nativeFeedbackAvailable()){setOpenedFeedbackFile(undefined);setSuspendedFeedbackGeneration(directFileGeneration);}setDirectWrite(false);setDirectFileRequired(false);}},setDiscussionFileError):undefined;
  onMount(()=>{discussionFiles?.start();onCleanup(()=>discussionFiles?.stop());});
  async function unlinkDiscussionFile(){const scope=associationScope(quickScope()),generation=directFileGeneration,selected=directFile();await discussionFiles?.clear();if(selected&&generation===directFileGeneration&&sameDiscussionScope(scope,quickScope())&&exactFileSource(selected.file.source_ref,directFile()?.file.source_ref)){directFileGeneration++;setOpenedFeedbackFile(undefined);setDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);saveCompanionFileDraft(localStorage,selected.file.source_ref.project_id,undefined);}}
  async function openLinkedContext(){const selected=linkedFile();if(!selected||!nativeBridge?.openLinkedFile)return;const scope=associationScope(quickScope());await action('打开关联文稿',async()=>{const result=await nativeBridge.openLinkedFile!(selected.fileLinkRef,scope) as {status?:string};if(result.status!=='OPEN_REQUESTED')throw new Error('系统尚未确认文稿打开请求');});}
  let quickScopeGeneration=0,historyGeneration=0;let workspaceStateRelease:(()=>void)|undefined;let workspaceStateOwner:ReturnType<typeof objectDraft>|undefined;
  createEffect(()=>{const scope=quickScope();quickReplyNavigation.cancel();quickScopeGeneration++;if(directFileScope&&!sameDiscussionScope(directFileScope,scope)){directFileGeneration++;directFileScope=undefined;setDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);const pid=project()?.id;setPreviousDirectFile(pid?readCompanionFileDraft(localStorage,pid):undefined);}if(!conversationId()){void discussionFiles?.setScope(scope,false);if(selectedDiscussion()?.key.kind!=='conversation')setError('会话标识未能保存，快捷发送暂不可用；当前输入保留。');return;}if(!quickBridge?.setQuickScope)return;const generation=quickScopeGeneration;void discussionFiles?.setScope(scope,false);void quickBridge.setQuickScope({...scope,generation}).then(()=>{if(generation===quickScopeGeneration)return discussionFiles?.refresh();}).catch(error=>{if(generation===quickScopeGeneration)setError(authoredCopy("快捷入口的当前工作尚未同步：{detail}",{detail:authoredErrorCopy(error)}));});});
  createEffect(()=>{historyReloadToken();const cid=conversationId(),savedHistory=cid?readNativeHistory(localStorage,cid):[];const generation=++historyGeneration;nativeObserved.clear();setReviewContinue(undefined);setReviewRetryContext(undefined);const unresolved=cid?localStorage.getItem('eeas-native-review-unresolved:'+cid):null;setReviewUnknown(unresolved&&/^[A-Za-z0-9_-]{1,100}$/.test(unresolved)?unresolved:undefined);setPageReview(cid?readReviewDraft(localStorage,cid):null);setNativeMessages([]);setNativePending(undefined);if(nativeTimer){clearTimeout(nativeTimer);nativeTimer=undefined;}if(!cid)return;untrack(importSavedQuickReplies);if(!nativeClient)return;void restoreNativeHistory(nativeClient,savedHistory).then(results=>{if(nativeDisposed||historyGeneration!==generation)return;for(const item of results)nativeRecord(item.input,item.message,!!item.feedback_display_identity,item.feedback_display_identity);untrack(importSavedQuickReplies);for(const pending of nativeMessages().filter(item=>nativeMessagePending(item.message)).slice(-5))observeNativeMessage(pending.message.operation_id,pending.input);});});
  const importedOriginalQuick=new Map<string,string>();
  function importQuickRecord(record:QuickRecord){if(record.delivery!=='SEND'||!record.message||!sameQuickScope(record.scope,quickScope()))return;
    if(record.message.canonical_original===true){const stamp=JSON.stringify([record.message.status,record.message.text,record.message.original_source,record.message.canonical_controls]);if(importedOriginalQuick.get(record.operationId!)!==stamp){importedOriginalQuick.set(record.operationId!,stamp);if(importedOriginalQuick.size>400)importedOriginalQuick.delete(importedOriginalQuick.keys().next().value!);void refreshThread();}return;}
    const previous=nativeMessages().find(item=>item.message.operation_id===record.message!.operation_id);if(previous&&['RETURNED','SUCCEEDED','COMPLETED','FAILED','CANCELLED','TIMED_OUT'].includes(previous.message.status)&&nativeMessagePending(record.message))return;nativeRecord(quickIntentSummary(record),record.message);persistNativeHistory();if(nativeMessagePending(record.message)&&!nativePending())observeNativeMessage(record.message.operation_id,quickIntentSummary(record));else if(!nativeMessagePending(record.message))forgetNativeMessage(record.message.operation_id);}
  function importSavedQuickReplies(){for(const record of quickRecords())importQuickRecord(record);}
  const quickReplyNavigation=new QuickReplyNavigation({root:()=>shell,scope:quickScope,records:quickRecords,refresh:()=>quickInbox.refresh(),importRecord:async record=>{if(record.message?.canonical_original===true){await refreshThread();return;}if(!nativeMessages().some(row=>row.message.operation_id===record.operationId&&row.message.conversation_id===record.scope.conversationId))importQuickRecord(record);},present:ready=>{conversationReading.pause();const restore=preserveLayoutReading(shell,ready);showAgentChat();setNativeMenu(false);restore();},unavailable:()=>setError('原记录已变化，请核查原操作')});
  onCleanup(()=>quickReplyNavigation.dispose());
  createEffect(()=>{quickRecords();quickScope();untrack(importSavedQuickReplies);});
  function flushCurrentChatDraft(){
    saveComposerDraft();if(selectedDiscussion())return;
    const pid=project()?.id;saveNativeDraft(localStorage,pid,nativeDraftAttachments());if(windowMode()==='companion'||!pid)saveNativeText(localStorage,pid,prompt());
    if(pid){const composer=readComposerDraft(localStorage,pid,activeSession()?.id);if(draftReady()&&(composer.text!==prompt()||draftAttachments().some(value=>!composer.feedback_refs.some(ref=>ref.id===value.feedback_ref.id&&ref.sha256===value.feedback_ref.sha256))))throw new Error('聊天草稿尚未完成本机保存');}
    if(JSON.stringify(readNativeDraft(localStorage,pid))!==JSON.stringify(nativeDraftAttachments()))throw new Error('对象批注尚未完成本机保存');
    if((windowMode()==='companion'||!pid)&&readNativeText(localStorage,pid)!==prompt())throw new Error('输入草稿尚未完成本机保存');
  }
  onMount(()=>{
    void quickInbox.start();
    const release=nativeBridge?.onDraftFlushRequested?.(async()=>{
      const previousInert=shell?.inert;if(shell)shell.inert=true;
      try{conversationReading.flush();await supervisorClient?.flush();flushCurrentChatDraft();await quickInbox.flush();await flushWorkbenchDrafts();await flushDynamicWorkflowDrafts();await flushConfigurationDrafts();await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();await flushWorkSourceDrafts();await flushNativeConflictDrafts();await flushNativePreviewDrafts();await flushNativeFeedbackDrafts();flushOriginalControlDrafts();flushCurrentChatDraft();conversationReading.flush();}
      finally{if(shell)shell.inert=previousInert??false;}
    });onCleanup(()=>release?.());
  });
  onCleanup(()=>{quickInbox.stop();workspaceStateRelease?.();void workspaceStateOwner?.flush();});

  createEffect(()=>{const w=workspace();workspaceStateRelease?.();workspaceStateRelease=undefined;workspaceStateOwner=undefined;setDraftRecords([]);if(!w)return;const owner=objectDraft(workspaceStateKey(w));workspaceStateOwner=owner;let applied=false;const restoreOwner=navigation.capture(),fileEditAtRestore=fileWidthEdit;const applyPanes=()=>{if(applied||owner.status==='loading'||workspace()?.id!==w.id)return;applied=true;const panes=owner.view.panes as {files_width?:number;files_hidden?:boolean}|undefined;const width=fileWidthEdit===fileEditAtRestore&&Number.isFinite(panes?.files_width)?savedPaneWidth(String(panes!.files_width),'files'):undefined;setPaneIntent(current=>({...current,...width!==undefined?{files:width}:{}}));resizePanes();if(typeof panes?.files_hidden==='boolean')setFilesHidden(panes.files_hidden);if(owner.view.tab==='attention'||owner.view.tab==='current')setReviewTab(owner.view.tab);};workspaceStateRelease=owner.subscribe(applyPanes);untrack(applyPanes);void owner.ready.then(()=>{if(workspace()?.id===w.id){void refreshDraftRecords(w.project_id);const active=owner.view.active_key as WorkbenchKey|undefined;if(active&&restoreOwner()&&!inspector()&&!interactionBlocking()&&windowMode()==='workbench')void restoreDraftObject(active,false);}});});
  createEffect(()=>{const values={files_width:paneIntent().files,files_hidden:filesHidden()};const tab=reviewTab();if(workspaceStateOwner&&workspaceStateOwner.status!=='loading'){workspaceStateOwner.set('view','panes',values);workspaceStateOwner.set('view','tab',tab);}});
  async function refreshDraftRecords(pid=project()?.id,offset=0){if(!pid)return;const generation=++draftListGeneration;setDraftListBusy(true);try{const value=await workbenchStateApi.drafts(pid,offset);if(project()?.id===pid&&generation===draftListGeneration){setDraftRecords(previous=>offset?[...new Map([...previous,...value.drafts].map(item=>[keyIdentity(item.key),item])).values()]:value.drafts);setDraftNextOffset(value.next_offset);setDraftReadError(value.unavailable?.length?`${value.unavailable.length} 条草稿完整性待核查，原记录已保留。`:'');}}catch(error){if(project()?.id===pid&&generation===draftListGeneration)setDraftReadError('本机草稿列表暂未读到：'+message(error));}finally{if(generation===draftListGeneration)setDraftListBusy(false);}}
  function workbenchDraftLabel(draft:WorkbenchState){
    const key=draft.key;if(key.kind==='local-file')return key.object_id;
    const candidate=draft.fields.candidate as {title?:unknown}|undefined;
    if(candidate&&typeof candidate.title==='string'&&candidate.title.trim())return candidate.title;
    const known=reviewRows().find(row=>row.kind===key.kind&&row.id===key.object_id);
    return known?.title??uiText(({discovery:'需求与图',proposal:'任务与图',taskbook:'任务书',harness:'Harness',report:'交付'} as Record<string,string>)[key.kind]??'本机编辑草稿 ');
  }
  function automaticDraftCurrent(key:WorkbenchKey){
    const w=workspace();if(!w||key.project_id!==w.project_id||key.workspace_id&&key.workspace_id!==w.id||key.binding_revision!==undefined&&key.binding_revision!==w.revision||key.binding_sha256!==undefined&&key.binding_sha256!==w.binding_sha256)return false;
    if(localStorage.getItem('eeas-current-thread-v1')&&!threadScope())return false;
    return key.session_id===activeSession()?.id;
  }
  async function restoreDraftObject(key:WorkbenchKey,explicit=true){
    if(key.project_id!==project()?.id||!explicit&&!automaticDraftCurrent(key))return;const owner=explicit?navigation.begin():{current:navigation.capture(),signal:undefined},base=api.projectPath(key.project_id),id=encodeURIComponent(key.object_id),query=`?revision=${key.revision}`;
    try{if(explicit)saveComposerDraft();let data:unknown;if(key.kind==='local-file'){if(!key.workspace_id)throw new Error('原文件草稿缺少目录绑定');data=await api.request<FileContent>(`${base}/local-workspaces/${encodeURIComponent(key.workspace_id)}/file?${new URLSearchParams({path:key.object_id,revision:String(key.revision)})}`,'GET',undefined,owner.signal);}else{const route=({taskbook:'taskbooks',proposal:'planning/proposals',discovery:'discovery/sessions',harness:'harnesses',report:'report-artifacts'} as Record<string,string>)[key.kind];if(!route)throw new Error('此类草稿请从原工具恢复');data=await api.request(`${base}/${route}/${id}${query}`,'GET',undefined,owner.signal);}
      if(!owner.current()||project()?.id!==key.project_id||!explicit&&!automaticDraftCurrent(key))return;const value=data as {id?:string;revision?:number;sha256?:string;source_ref?:SourceRef};const ref=value.source_ref??value;if(ref.sha256!==key.sha256||('revision' in ref&&ref.revision!==key.revision)||key.kind!=='local-file'&&value.id!==key.object_id)throw new Error('原版本依据不一致，草稿保留且未套用到新版本');
      if(key.kind==='local-file'){const source=value.source_ref;if(!source||source.project_id!==key.project_id||source.workspace_id!==key.workspace_id||source.path!==key.object_id||key.binding_revision!==undefined&&source.binding_revision!==key.binding_revision||key.binding_sha256!==undefined&&source.binding_sha256!==key.binding_sha256)throw new Error('原版本依据不一致，草稿保留且未套用到新版本');}
      if(key.kind==='harness'){const state=await api.request<HarnessState>(`${base}/harnesses/${id}/state${query}`,'GET',undefined,owner.signal);if(!owner.current())return;data={harness:data,state};}
      if(explicit){
        const sessionId=key.kind==='discovery'?key.object_id:key.session_id;
        if(sessionId){
          const session=await discoveryApi.get(key.project_id,sessionId,owner.signal);if(!owner.current())return;
          if(session.project_id!==key.project_id||session.id!==sessionId)throw new Error('原版本依据不一致，草稿保留且未套用到新版本');
          await installDiscussionTarget({kind:'discovery',session,navigation:{project_id:key.project_id,workspace_ref:session.payload.workspace_ref??null}},owner.current,owner.signal,'chat');
          if(!owner.current())return;
        }
      }
      const artifact={...envelope(key.kind,key.object_id,data,key.revision,key.sha256),scope:{projectId:key.project_id,...key.workspace_id?{workspaceId:key.workspace_id}:{},...key.session_id?{sessionId:key.session_id}:{}}};if(explicit)inspect(artifact);else if(!inspector()&&!interactionBlocking())setInspector(artifact);
    }catch(error){if(owner.current())setError(authoredCopy("原版本草稿尚未恢复：{detail}",{detail:authoredErrorCopy(error)}));}
  }
  async function collapseWorkbench(){if(nativeController){if(!await changeNativeMode('companion'))return;}else{if(fullscreen()&&!await leaveFullscreen())return;setWorkbenchHidden(true);setMobileView('agent');}void workspaceStateOwner?.flush();}
  const workflowIdentity=createMemo(()=>{
    const thread=threadScope();
    return thread?.project_id&&thread.conversation_ref.id?JSON.stringify([thread.project_id,thread.conversation_ref.id]):'';
  });
  const workflowScope=createMemo<WorkflowScope|undefined>(()=>{
    const identity=workflowIdentity();if(!identity)return undefined;
    const [project_id,conversation_id]=JSON.parse(identity) as [string,string];return {project_id,conversation_id};
  });
  const [stagePresentation,setStagePresentation]=createSignal(false);
  const [workflowHeader,setWorkflowHeader]=createSignal<{scope:WorkflowScope;view:WorkflowViewContext}>();
  function showWorkflowView(scope:WorkflowScope,view:WorkflowViewContext|undefined){
    if(scope===workflowScope())setWorkflowHeader(view?{scope,view}:undefined);
  }
  const currentWorkflowHeader=createMemo(()=>{
    const header=workflowHeader();
    return stagePresentation()&&header?.scope===workflowScope()?header?.view:undefined;
  });
  const agentConversationTitle=()=>historyItems().find(item=>item.id===historyActiveId())?.title??
    (historySelectedItem()?.id===historyActiveId()?historySelectedItem()?.title:undefined)??
    (draftOnlyTitle()||undefined)??activeSession()?.payload.candidate.title??project()?.name??uiText("新对话");
  const agentHeaderTitle=()=>{
    const view=currentWorkflowHeader();
    if(!view)return agentConversationTitle();
    return view.readonly?view.title+(uiLocale()==='en'?' · Read-only':' · 只读'):view.title;
  };
  const agentHeaderHint=()=>{
    const title=agentHeaderTitle(),conversation=agentConversationTitle();
    if(!currentWorkflowHeader()||title===conversation)return title;
    return title+(uiLocale()==='en'?' · Conversation: ':' · 会话：')+conversation;
  };
  const [preparedWorkflow,setPreparedWorkflow]=createSignal<{key:string;index:StageIndex}>();
  function initialWorkflow(scope:WorkflowScope){const seed=preparedWorkflow();if(seed?.key===stageModeKey(scope))return seed.index;}
  function isWorkflowStage(index:StageIndex|undefined,id:string){
    return !!index?.owner_conversation_id&&index.owner_conversation_id!==id;
  }
  const [workflowArrival,setWorkflowArrival]=createSignal<WorkflowArrival>();
  let workflowArrivalTimer:ReturnType<typeof setTimeout>|undefined;
  function recordWorkflowArrival(value:WorkflowArrival|undefined){
    clearTimeout(workflowArrivalTimer);
    setWorkflowArrival(value);
  }
  function workflowArrivalShown(value:WorkflowArrival){
    if(value!==workflowArrival())return;
    clearTimeout(workflowArrivalTimer);
    workflowArrivalTimer=setTimeout(()=>{if(workflowArrival()===value)setWorkflowArrival(undefined);},3200);
  }
  onCleanup(()=>clearTimeout(workflowArrivalTimer));
  const [workflowEntry,setWorkflowEntry]=createSignal<'idle'|'pending'|'failed'>('idle');
  let workflowEntryRequest:AbortController|undefined,workflowDiscovery:AbortController|undefined;
  function clearWorkflowEntry(){
    workflowEntryRequest?.abort();
    workflowEntryRequest=undefined;
    setWorkflowEntry('idle');
  }
  function commitPresentation(enabled:boolean,scope:WorkflowScope|undefined,index?:StageIndex){
    if(!enabled)recordWorkflowArrival(undefined);
    try{if(scope)localStorage.setItem(stageModeKey(scope),enabled?'workflow':'daily');}
    catch{setError('未能保存此会话的模式选择');}
    batch(()=>{
      setPreparedWorkflow(scope&&index?{key:stageModeKey(scope),index}:undefined);
      setStagePresentation(enabled);
      setWorkflowEntry('idle');
    });
    if(enabled&&!sessionManagementOpen())presentWorkspace('chat');
  }
  async function switchPresentation(enabled:boolean){
    const scope=workflowScope();
    if(enabled&&workflowEntryRequest)return;
    workflowDiscovery?.abort();
    clearWorkflowEntry();
    if(!enabled||!scope){commitPresentation(enabled,scope);return;}
    if(stagePresentation())return;
    const request=workflowEntryRequest=new AbortController();
    const navigationCurrent=navigation.capture();
    const current=()=>workflowEntryRequest===request&&workflowScope()===scope&&navigationCurrent();
    setWorkflowEntry('pending');
    try{
      const index=await stageApi.list(scope,request.signal);
      if(current())commitPresentation(true,scope,index);
    }catch{
      if(current())setWorkflowEntry('failed');
    }finally{
      if(workflowEntryRequest===request){
        if(!navigationCurrent())setWorkflowEntry('idle');
        workflowEntryRequest=undefined;
      }
    }
  }
  createEffect(()=>{
    const scope=workflowScope();
    clearWorkflowEntry();
    onCleanup(clearWorkflowEntry);
    const key=scope?stageModeKey(scope):undefined,saved=key?localStorage.getItem(key):null;
    const prepared=scope?untrack(()=>initialWorkflow(scope)):undefined;
    setStagePresentation(saved==='workflow'||!saved&&!!scope&&isWorkflowStage(prepared,scope.conversation_id));
    if(!scope||!key||saved||prepared)return;
    // Discover an activated node from the authoritative directory, also in a new renderer profile.
    const controller=workflowDiscovery=new AbortController();
    onCleanup(()=>{controller.abort();if(workflowDiscovery===controller)workflowDiscovery=undefined;});
    void stageApi.list(scope,controller.signal).then(result=>{
      if(controller.signal.aborted||workflowScope()!==scope||localStorage.getItem(key)!==null)return;
      if(isWorkflowStage(result,scope.conversation_id)){
        batch(()=>{setPreparedWorkflow({key,index:result});setStagePresentation(true);presentWorkspace('chat');});
      }
    }).catch(()=>{});
  });
  function workflowDirectoryChanged(run:StageRun){
    for(const stage of run.stages){if(!stage.stage_session_id)continue;
      const key=stageModeKey({project_id:run.project_id,conversation_id:stage.stage_session_id});
      try{if(localStorage.getItem(key)===null)localStorage.setItem(key,'workflow');}catch{/* A later lookup can recover the canonical parent. */}
    }
    void refreshHistory(false,true);
  }
  async function openStageConversation(id:string,destination?:StageDestination){
    const scope=workflowScope();if(!scope)return;
    localStorage.setItem(stageModeKey({...scope,conversation_id:id}),'workflow');
    const owner=navigation.begin();const result=await openSavedThread(id,owner.current,owner.signal,0,destination);
    if(result!=='OPENED')throw Error(uiLocale()==='en'?'Could not open the stage conversation.':'未能打开当前步骤的会话。');
  }
  async function createWorkflowConversation(){
    const pid=project()?.id;if(!pid){openFolderDialog();return;}
    await newConversation(pid);if(workflowScope()?.project_id===pid)switchPresentation(true);
  }
  function FileFeedbackReview(){return <Show when={nativeFileFeedbacks().length}><details class="as-review-feedback" open={reviewTab()==='attention'}><summary>{uiText("文件反馈与局部返工 · ")}{nativeFileFeedbacks().length}</summary><For each={nativeFileFeedbacks().map(value=>value.feedback_ref.id)}>{feedbackId=>{const feedback=()=>nativeFileFeedbacks().find(value=>value.feedback_ref.id===feedbackId)!;return <article class="as-native-file-feedback" tabIndex={-1} data-native-feedback-ref={JSON.stringify([feedback().payload.project_id,feedback().feedback_ref.id,feedback().feedback_ref.revision,feedback().feedback_ref.sha256])}><strong>{uiText("文件反馈已保存 · v")}{feedback().payload.baseline_file_source_ref.revision}</strong><p>{feedback().payload.comment}</p><small>{feedback().payload.baseline_file_source_ref.path} · {feedback().current_file_check.status} · {feedback().mutation_status==='NOT_ADAPTED'?uiText("此文件修改尚未接入或超出大小范围"):uiText("可通过已有单文件动作处理；反馈本身未授权写入")}</small><NativeReworkPanel feedback={feedback()} reveal={nativePreviewReveal()?.projectId===project()?.id&&nativePreviewReveal()?.conversationId===conversationId()&&sameNativeReference(nativePreviewReveal()?.feedbackRef,feedback().feedback_ref)?nativePreviewReveal():undefined} records={nativeReworkRecords().filter(item=>item.payload.project_id===feedback().payload.project_id&&item.payload.native_feedback_ref.id===feedback().feedback_ref.id)} questionBusy={!!nativePending()||!!busy()} onAskPreview={askNativeRework} onOpen={openDirectSource} onBeforeApply={()=>observeEditorApply(feedback().payload.baseline_file_source_ref)} onApplied={()=>{void refreshNativeFileFeedbacks(feedback().payload.project_id);if(sameSelectedFile(directFile()?.file.source_ref,feedback().payload.baseline_file_source_ref))void pinnedObserver.refresh();}}/><details><summary>{uiText("原版本、反馈与真实动作关联")}</summary><pre class="as-json">{JSON.stringify(feedback(),null,2)}</pre></details></article>;}}</For></details></Show>;}
  function openReviewObject(value:ReviewObject){if(value.kind==='discovery')void openDiscovery(value.data as DiscoverySession);else if(value.kind==='harness')void openHarness(value.data as Harness);else if(value.kind==='report')inspect(envelope('report',value.id,value.data,value.revision,value.sha256));else openProjectArtifact(value.kind,value.data as PlanningProposal|TaskBook);}

  async function showProjectReview(){const current=navigation.capture();if(windowMode()==='companion'&&!await changeNativeMode('workbench'))return;if(!current())return;navigation.begin();setInspector(undefined);for(const item of interactions.current())if(item.visible)interactions.collapse(item.key);setSkillDrawer(false);presentWorkspace('split');workspaceStateOwner?.set('view','active_key',null);}
  function closeInspector(){inspect(undefined);setContextTray(false);}
  const composerAttachments=():ComposerAttachment[]=>[
    ...nativeDraftAttachments().map((item,index)=>{inputPreviewVersion();const preview=inputPreviews.get(item);return {id:'native:'+JSON.stringify([item.context_ref,index]),label:item.label,description:item.comment,meta:item.regions.length?(uiLocale()==='en'?`${item.regions.length} selected regions`:`${item.regions.length}处圈选`):item.preview_sha256?(uiLocale()==='en'?'Screenshot':'截图'):(uiLocale()==='en'?'Page reference':'页面引用'),image:!!item.preview_sha256,previewUrl:preview?.url,previewStatus:preview?.status,onLoadPreview:()=>inputPreviews.request(item),...(item.preview_sha256?{onPreview:()=>openInputEditor(item)}:{}),onRemove:()=>setNativeDraftAttachments(list=>list.filter(value=>value!==item))};}),
    ...draftAttachments().map(item=>({id:'feedback:'+item.feedback_ref.id,label:item.payload.tool==='TEXT_SELECTION'?uiText("选文批注"):item.payload.tool==='TEXT_EDIT'?uiText("文稿修改"):uiText("图批注"),description:item.payload.comment,meta:uiText("当前引用 · v{value0}",{value0:String(item.payload.artifact_ref.revision)}),warning:invalidFeedback()[item.feedback_ref.id]||item.payload.revision_target_ref.revision!==activeSession()?.revision?uiText("当前版本已变化，需要重新定位"):undefined,onRemove:()=>setDraftAttachments(list=>list.filter(value=>value.feedback_ref.id!==item.feedback_ref.id))})),
    ...materials().filter(item=>item.document_ref).map(item=>({id:'material:'+item.path,label:item.path.split(/[\\/]/).at(-1)??item.path,meta:item.path.split(/[\\/]/).slice(0,-1).join('/'),title:item.path,onRemove:()=>setMaterials(list=>list.filter(value=>value!==item))}))
  ];
  return <div class="as-app-frame" onInput={event=>{if(event.target instanceof HTMLTextAreaElement){composerInteracted=true;conversationReading.interact();}}} onCompositionStart={()=>conversationReading.interact()} data-fullscreen={String(fullscreen())} data-native-maximized={String(nativeMaximized())} data-native-titlebar={String(!!nativeBridge)}> <ProviderDialog artifact={providerDialogArtifact()} open={providerDialogOpen()} onOpenChange={setProviderDialogOpen} context={context} mount={shell?.parentElement??undefined} returnTarget={providerDialogReturn()} fallbackFocus={focusComposer}/><NativeAttachmentEditor open={!!inputEditor()} attachment={inputEditor()?.item} snapshot={inputEditor()?.snapshot} loading={!!inputEditor()?.loading} error={inputEditor()?.error} locale={uiLocale()} readOnly={inputEditor()?.readOnly} onClose={closeInputEditor} onSave={saveInputRegions} onRetry={()=>void loadInputEditor()} fallbackFocus={focusComposer} returnTarget={inputEditorReturn()}/><DraftRecoveryDialog open={!!draftRecovery()} restoring={draftRecovery()?.restoring} migrating={!!draftRecovery()?.resolveMigration} conflict={draftRecovery()?.local?{local:draftRecovery()!.local!,saved:draftRecovery()!.saved}:undefined} copies={draftRecoveryCopies()} scopeKey={draftRecovery()?.lease.key??''} locale={uiLocale()} busy={draftRecoveryBusy()} error={draftRecoveryError()} bridge={nativeBridge} onClose={()=>setDraftRecovery(undefined)} onChoose={choice=>void selectDraftChoice(choice)} onRestore={restoreDraftCopy} onRemove={deleteDraftCopy} fallbackFocus={focusComposer}/><ConversationOrganizationDialog target={organizationTarget()} locale={uiLocale()} projects={projects()} busy={organizationBlocked()} onClose={()=>setOrganizationTarget(undefined)} onConfirm={organizeConversation} fallbackFocus={focusComposer} status={organizationStatus}/><ContextDialog open={contextOpen()} fileTarget={contextFileTarget()} pageContextRef={nativeDraftAttachments().at(-1)?.context_ref??currentNativeReference(currentNativeContext())} canOpenFile={!!nativeBridge?.openContextFile} onOpenFile={openContextOriginal} onOpenConversation={openContextDiscussion} workspace={workspace()} projectId={project()?.id??null} conversationId={threadScope()?.conversation_ref.id} locale={uiLocale()} onClose={()=>setContextOpen(false)} choice={contextChoice()} onClear={clearContextChoice} onSelected={selectContext} returnTarget={contextReturn} fallbackFocus={focusComposer}/>
    <ConversationManager open={conversationManagerOpen()} onOpenChange={setConversationManagerOpen} locale={uiLocale()} refreshVersion={organizationRefresh()} busy={organizationBlocked()} status={organizationStatus} onOpen={openHistoryConversation} onOrganize={organizeConversation} onRequestOrganize={requestOrganization} returnTarget={conversationManagerReturn} fallbackFocus={focusComposer}/><dialog ref={historyDialog} class="as-history-preview" onCancel={()=>setHistoryPreview(undefined)} onClose={()=>setHistoryPreview(undefined)}><header><h2>{historyPreview()?.title}</h2><button type="button" aria-label={uiLocale()==='en'?'Close':'关闭'} onClick={()=>setHistoryPreview(undefined)}>×</button></header><small>{uiLocale()==='en'?'Historical record':'历史记录'}</small><Show when={historyPreview()?.input}><p>{historyPreview()?.input}</p></Show><AssistantReply text={historyPreview()?.answer??''}/></dialog><Show when={nativeBridge}><DesktopTitlebar mode={windowMode()} center={!fullscreen()?supervisorCenter():undefined} actions={windowMode()==='workbench'?<WorkspaceToolbar workspace={workspace()} workspaces={workspaces()} onOpen={()=>{setProjectName(project()?.name??'');openFolderDialog();}} onSelect={item=>void selectWorkspace(item)}/>:undefined} pinned={collaborationState()?.pinned_preference??collaborationState()?.pinned} pinBusy={companionPinBusy()} onPin={nativeBridge?.setCompanionPinned?()=>void toggleCompanionPin():undefined}/></Show><div ref={shell} onSelect={event=>void captureFileFeedbackSelection(event)} class="as-shell" data-compact={String(compact())} data-sidebar-open={String(sidebarOpen())} data-inspector-open={String(!!inspector())} data-inspector-type={inspector()?.type} data-context-open={String(contextTray())} data-layout="workbench" data-chat-layout="adaptive" data-window-mode={windowMode()} data-fullscreen={String(fullscreen())} data-fullscreen-agent={String(fullscreenAgent())} data-fullscreen-files={String(fullscreenFiles())} data-files-hidden={String(!panelVisibility().files)} data-agent-hidden={String(!panelVisibility().agent)} data-workbench-hidden={String(!panelVisibility().workbench)} data-mobile-view={mobileView()} style={{'--as-file-width':fileWidth()+'px','--as-agent-width':agentWidth()+'px'}}>
    <Show when={compact()&&sidebarOpen()}><button class="as-drawer-scrim" aria-label={uiText("关闭工作目录")} onClick={()=>setSidebarOpen(false)}/></Show><aside ref={sidebar} class="as-sidebar" id="as-workspace-sidebar" aria-label={uiText("工作目录与会话")}><Show when={filesHidden()&&!compact()&&!fullscreen()}><button type="button" class="as-sidebar-reopen" aria-label={uiText("展开工作目录")} title={uiText("展开工作目录")} onClick={()=>setFilesHidden(false)}><Icon name="menu"/></button></Show><Show when={compact()&&sidebarOpen()}><button type="button" class="as-sidebar-close" aria-label={uiText("关闭工作目录")} onClick={()=>setSidebarOpen(false)}><Icon name="close"/></button></Show><section class="as-file-pane"><ConversationSidebar onContext={showContext} onNewChat={pid=>newConversation(pid)} newChatBusy={newChatBlocked()} projectCatalog={projects()} onManage={showConversationManager} managementOpen={conversationManagerOpen()} onOrganize={(item,action)=>organizeConversation(item as HistoryItem,action)} onRequestOrganize={(item,action,target)=>requestOrganization(item as HistoryItem,action,target)} organizationBusy={organizationBlocked()} items={historyItems()} activeId={historyActiveId()} currentProjectId={project()?.id} locale={uiLocale()} loaded={historyLoaded()} loading={historyLoading()} error={historyError()} partial={historyPartial()} onRefresh={()=>refreshHistory()} onOpen={item=>openHistoryConversation(item as HistoryItem)} tab={leftTab()} onTabChange={setLeftTab} hasMore={!!historyCursor()} loadingMore={historyLoadingMore()} onLoadMore={()=>refreshHistory(true)}><Show when={!nativeBridge||fullscreen()}><WorkspaceToolbar workspace={workspace()} workspaces={workspaces()} onOpen={()=>{setProjectName(project()?.name??'');setSidebarOpen(false);openFolderDialog();}} onSelect={item=>void selectWorkspace(item)}/></Show>
      <Show when={workspace()} keyed>{value=><><LocalExplorer workspace={value} selection={selection()} onSelection={setSelection} onOpen={entry=>void openFile(entry)}/><Show when={selection().length}><Button class="as-import" variant="primary" disabled={!!busy()||activeOperation()?.status==='PROCESSING'||activeOperation()?.status==='REQUESTING'} onClick={()=>void importMaterials()}>{uiText("加入 Agent · ")}{selection().length}</Button></Show></>}</Show>
</ConversationSidebar><Show when={compact()&&sidebarOpen()&&!conversationManagerOpen()}>{organizationStatus()}</Show><Show when={windowMode()!=='companion'}>{globalNavigationMenu(true)}</Show></section>
    </aside>
<PaneSeparator kind="files" value={fileWidth()} hidden={filesHidden()} onChange={value=>changePaneWidth('files',value)}/>
    <div class="as-workbench as-main"><header class="as-topbar"><button class="as-sidebar-toggle" aria-label={sidebarOpen()?uiText("收起工作目录"):uiText("展开工作目录")} aria-expanded={sidebarOpen()} aria-controls="as-workspace-sidebar" onClick={()=>compact()?setSidebarOpen(!sidebarOpen()):setFilesHidden(!filesHidden())}><Icon name="menu"/></button><div class="as-breadcrumb"><span>{workspace()?uiText("工作目录"):uiText("本机工作面")}</span><Show when={workspace()}><span class="as-breadcrumb-divider">/</span><span title={workspace()?.root}>{workspace()?.root}</span></Show></div><div class="as-topbar-actions"><Show when={!panelVisibility().agent||mobileWorkspace(layoutState())}><button type="button" class="as-workbench-switch" aria-label={!panelVisibility().agent&&!mobileWorkspace(layoutState())?(uiLocale()==='en'?'Show chat':'展开对话'):uiText("返回对话")} onClick={()=>!panelVisibility().agent&&!mobileWorkspace(layoutState())?showAgentChat():presentWorkspace('chat')}><Icon name="prompt"/><span>{uiText("对话")}</span></button></Show><Show when={nativeController}><button aria-label={uiText("收起整个工作台")} title={uiText("收起工作台，返回原软件")} onClick={()=>void collapseWorkbench()}><Icon name="collapse"/></button></Show><button aria-label={fullscreen()?uiText("退出工作台全屏"):uiText("工作台全屏")} onClick={()=>void(fullscreen()?leaveFullscreen():enterFullscreen())}><Icon name="expand"/></button><StudioPopover open={viewMenu()} onOpenChange={setViewMenu} label={uiText("视图与布局")} class="as-companion-menu" trigger={<button type="button" aria-label={uiText("视图与布局")} aria-expanded={viewMenu()} onClick={()=>setViewMenu(value=>!value)}><Icon name="menu"/></button>}><nav class="as-companion-menu-body" aria-label={uiText("视图与布局")} onKeyDown={event=>railKey(event,event.currentTarget)}><button type="button" onClick={()=>{setViewMenu(false);setFilesHidden(!filesHidden());}}><Icon name="folder"/><span>{uiText("显示或隐藏文件")}</span></button><button type="button" onClick={()=>{setViewMenu(false);showProjectReview();}}><Icon name="bullet-list"/><span>{uiText("任务与产物")}</span></button><button type="button" disabled={!nativeAvailable()} title={nativeConfig()?.reason} onClick={()=>{setViewMenu(false);setNativeOpen(true);}}><Icon name="square-arrow-top-right"/><span>{uiText("原 Agent 会话")}</span></button></nav></StudioPopover></div></header>
      <Show when={error()}><div class="as-error-bar" role="alert"><span>{authoredText(error(),uiText)}</span><button aria-label={uiText("关闭错误提示")} onClick={()=>setError('')}>×</button></div></Show><Show when={notice()}><div class="as-notice" role="status">{uiCopyText(notice())}</div></Show>
    <section class="as-session-management" aria-labelledby="as-session-management-heading" hidden={!sessionManagementOpen()} onKeyDown={event=>{if(event.key==='Escape'&&!event.isComposing){event.preventDefault();event.stopPropagation();closeSessionManagement();}}}>
      <header><h2 id="as-session-management-heading" tabindex="-1">{uiText("Agent 连接与权限")}</h2><button type="button" onClick={closeSessionManagement}>{uiText("返回当前工作")}</button></header>
      <AgentSessionPanel active={sessionManagementOpen()} logicalProjectId={threadScope()?.project_id??null} onOwnerPrepare={prepareOriginalOwnerChoices} onOwnerChoose={chooseOriginalOwner} onConfigure={configureOriginalFromPanel} configurationPending={connectionAffectsCurrent()} agent={originalAgent()} onPending={showOriginalPendingRequests} conversationRef={threadScope()?.conversation_ref} conversationSources={threadScope()?.sources.flatMap(source=>source.original_source?.kind==='original'?[source.original_source.session_ref]:[])} toolSelection={toolSelection()} onToolSelectionUsed={()=>setToolSelection(undefined)} projectId={project()?.id} workspace={workspace()} provider={providers().find(item=>item.profile_ref.id===providerId())?.profile_ref} selected={originalSession()} onSelect={selectOriginalSessionFromPanel} onUse={useOriginalSession}/>
      <Show when={threadScope()?.conversation_ref.id}><RetainedConfigurationRecords client={originalConnection} id={threadScope()!.conversation_ref.id} version={connectionVersion()} active={sessionManagementOpen()}/></Show>
    </section>
    <div class="as-workspace-body" data-native-active="false" style={{display:sessionManagementOpen()?'none':undefined}}><Show when={!inspector()&&!interactionBlocking()}><WorkbenchReview projectName={project()?.name} objects={reviewRows()} loading={projectCatalogState().projectId===project()?.id&&projectCatalogState().phase==='loading'} readFailed={projectCatalogState().projectId===project()?.id&&['partial','failed'].includes(projectCatalogState().phase)} onRetry={()=>{const pid=project()?.id;if(pid)void projectObjects.retry(pid);}} tab={reviewTab()} onTab={value=>{setReviewTab(value);workspaceStateOwner?.set('view','tab',value);}} onFolder={()=>openFolderDialog()} onOpen={openReviewObject} feedback={undefined} attentionCount={sourceChecks().filter(item=>item.status!=='CURRENT').length+(originalSession()&&originalSession()!.project_id===project()?.id?(originalSession()!.pending_questions.length+originalSession()!.pending_permissions.length):0)+nativeMessages().filter(item=>['UNKNOWN_OUTCOME','NO_RECORD','INSPECTION_UNAVAILABLE'].includes(item.message.status)).length} attention={<><Show when={originalSession()&&originalSession()!.project_id===project()?.id&&((originalSession()?.pending_questions.length??0)+(originalSession()?.pending_permissions.length??0))>0}><section class="as-review-attention"><h3>{uiText("原 Agent 等待回应")}</h3><button onClick={showOriginalPendingRequests}>{uiText("处理 ")}{originalSession()!.pending_questions.length}{uiText(" 个问题与 ")}{originalSession()!.pending_permissions.length}{uiText(" 项权限请求")}</button></section></Show><For each={nativeMessages().filter(item=>['UNKNOWN_OUTCOME','NO_RECORD','INSPECTION_UNAVAILABLE'].includes(item.message.status))}>{item=><article class="as-review-attention"><strong>{uiText("执行结果待核查")}</strong><p>{item.input}</p><button disabled={!!busy()} onClick={()=>void inspectNativeOperation(item.message.operation_id,item.input)}>{uiText("核查原操作")}</button></article>}</For><Show when={sourceChecks().some(item=>item.status!=='CURRENT')}><section class="as-review-attention" aria-label={uiText("需要复核的来源")}><h3>{uiText("来源变化")}</h3><For each={sourceChecks().filter(item=>item.status!=='CURRENT')}>{item=><article><strong>{item.source_ref.path}</strong><p>{item.status==='MISSING'?uiText("原文件缺失"):uiText("原件已变化，请核对依据")}</p><button onClick={()=>void openDirectSource(item.source_ref)}>{uiText("查看原引用")}</button><button disabled={item.status==='MISSING'||!!busy()} onClick={()=>void action('同步来源上下文',()=>synchronizeSource(item.source_ref))}>{uiText("同步来源")}</button></article>}</For></section></Show></>} drafts={<details class="as-review-drafts" onToggle={event=>{if(event.currentTarget.open)void refreshDraftRecords();}}><summary>{uiText("本机编辑草稿 ")}<span>{draftRecords().length}</span></summary><Show when={draftReadError()}><p role="alert">{draftReadError()}</p></Show><For each={draftRecords()}>{draft=><button onClick={()=>void restoreDraftObject(draft.key)}>{workbenchDraftLabel(draft)}{uiText(" · 原版本 v")}{draft.key.revision}</button>}</For><Show when={draftNextOffset()!==null}><button disabled={draftListBusy()} onClick={()=>void refreshDraftRecords(project()?.id,draftNextOffset()!)}>{uiText("继续读取草稿")}</button></Show></details>}/></Show><div class="as-primary-document" inert={!!interactionBlocking()} data-open={String(!!inspector())}><InspectorDocuments onFileRelated={showFileContext} active={inspector()} projectId={project()?.id} registry={registry} context={context} onBack={()=>setInspector(undefined)}/></div><InteractionPanels state={interactions} version={interactionVersion()} registry={registry} context={context} fullReady={true}/></div>
    </div><PaneSeparator kind="agent" value={agentWidth()} hidden={!panelVisibility().agent} onChange={value=>changePaneWidth('agent',value)}/><aside class="as-agent"><div class="as-agent-content" data-stage-mode={stagePresentation()}><header class="as-agent-header"><Show when={windowMode()==='companion'||compact()||fullscreen()}><StudioPopover open={windowMode()==='companion'&&historyPopoverOpen()} onOpenChange={setHistoryPopoverOpen} placement="bottom-start" label={uiLocale()==='en'?'Chat history':'历史会话'} class="as-history-popover" trigger={<button type="button" class="as-history-toggle" aria-label={uiLocale()==='en'?'Chat history':'历史会话'} title={uiLocale()==='en'?'Chat history':'历史会话'} aria-expanded={windowMode()==='companion'?historyPopoverOpen():fullscreen()?fullscreenFiles():compact()?sidebarOpen():!filesHidden()} onClick={toggleHistory}><Icon name="layout-left"/></button>}><ConversationSidebar onContext={showContext} onNewChat={pid=>newConversation(pid)} newChatBusy={newChatBlocked()} projectCatalog={projects()} onManage={showConversationManager} managementOpen={conversationManagerOpen()} onOrganize={(item,action)=>organizeConversation(item as HistoryItem,action)} organizationBusy={organizationBlocked()} items={historyItems()} activeId={historyActiveId()} currentProjectId={project()?.id} locale={uiLocale()} loaded={historyLoaded()} loading={historyLoading()} error={historyError()} partial={historyPartial()} onRefresh={()=>refreshHistory()} onOpen={async item=>{await openHistoryConversation(item as HistoryItem);setHistoryPopoverOpen(false);}} hasMore={!!historyCursor()} loadingMore={historyLoadingMore()} onLoadMore={()=>refreshHistory(true)} showFiles={false}/></StudioPopover></Show><strong title={agentHeaderHint()}>{agentHeaderTitle()}</strong><WorkflowModeSwitch workflow={stagePresentation()} entry={workflowEntry()} locale={uiLocale()} onChange={switchPresentation} onCancel={clearWorkflowEntry}/><span aria-hidden="true"/><Show when={windowMode()!=='companion'}><button type="button" class="as-workbench-switch" aria-label={uiText("显示或收起工作台")} title={uiText("显示或收起工作台")} aria-pressed={panelVisibility().workbench} onClick={()=>presentWorkspace(panelVisibility().workbench?'chat':'split')}><Icon name="layout-right"/><span>{uiText("工作台")}</span></button></Show><Show when={!nativeBridge||fullscreen()}>{supervisorCenter()}</Show><Show when={nativeBridge&&windowMode()==='companion'}><button aria-label={uiText("打开项目工作台")} title={uiText("打开项目工作台")} onClick={()=>void changeNativeMode('workbench')}><Icon name="expand"/></button></Show><Show when={windowMode()==='companion'}>{globalNavigationMenu(false)}</Show><Show when={windowMode()==='workbench'&&panelVisibility().agent&&panelVisibility().workbench&&!mobileWorkspace(layoutState())}><button class="as-hide-agent" aria-label={uiText("隐藏 Agent")} onClick={toggleAgent}><Icon name="collapse"/></button></Show></header><Show when={stagePresentation()}><Show when={workflowScope()} keyed fallback={<section class="wf-studio"><div class="wf-column wf-setup"><h1>{uiLocale()==='en'?'Start a workflow':'开始工作流'}</h1><p>{uiLocale()==='en'?'Select a project conversation in the sidebar or create one here.':'从左侧选择一个项目会话，或在这里新建。'}</p><button class="wf-primary" type="button" disabled={newChatBlocked()||!!busy()} onClick={()=>void createWorkflowConversation()}>{project()?(uiLocale()==='en'?'New project conversation':'新建项目会话'):(uiLocale()==='en'?'Choose project folder':'选择项目目录')}</button></div></section>}>{scope=><WorkflowStudio arrival={workflowArrival()} onArrival={recordWorkflowArrival} onArrivalShown={workflowArrivalShown} onViewContext={showWorkflowView} scope={scope} locale={uiLocale()} archived={threadScope()?.archived??true} initial={initialWorkflow(scope)} capabilitiesOpen={capabilitiesOpen()} onManageCapabilities={openCapabilities} onDirectory={workflowDirectoryChanged} onChild={openStageConversation}/>}</Show></Show><Show when={!conversationManagerOpen()&&!(compact()&&sidebarOpen())}>{organizationStatus()}</Show>{creationStatus()}{connectionStatus()}<Show when={windowMode()!=='companion'&&!panelVisibility().workbench}><Show when={error()}><div class="as-error-bar" role="alert"><span>{authoredText(error(),uiText)}</span><button aria-label={uiText("关闭错误提示")} onClick={()=>setError('')}>×</button></div></Show><Show when={notice()}><div class="as-notice" role="status">{uiCopyText(notice())}</div></Show></Show><div class="as-global-activity" hidden={!activityOpen()} aria-label={uiText("全局任务动态面板")}><button class="as-global-activity-close" aria-label={uiText("收起任务动态")} onClick={()=>{activityRequest++;setActivityOpen(false);}}>×</button><Show when={activityError()}><p class="as-inline-error" role="alert">{authoredText(activityError(),uiText)}</p></Show><Show when={activityLoading()}><p role="status">{uiText("正在读取原任务…")}</p></Show><Show when={activityDetail()}>{detail=><article class="as-supervised-detail"><button onClick={backTaskActivity}>{uiText("返回任务动态")}</button><h3>{detail().task.label}</h3><small>{uiText(supervisorTaskStatus(detail().task,uiText))}</small><Show when={detail().input_text}><p class="as-supervised-input">{detail().input_text}</p></Show><Show when={detail().answer_text?.trim()} fallback={<p class="as-supervised-answer">{uiText("暂无可读取的回答；原执行状态和待处理项仍保留。")}</p>}><div class="as-supervised-answer"><AssistantReply text={detail().answer_text}/></div></Show><Show when={['PARTIAL','UNAVAILABLE'].includes(detail().answer_status??'')&&detail().answer_message}><p class="as-inline-warning" role="status">{detail().answer_message}</p></Show><Show when={detail().text_truncated}><small>{uiText("此处显示片段；可进入原任务查看完整内容。")}</small></Show><For each={detail().artifacts}>{artifact=><button class="as-native-artifact" onClick={()=>void openNativeArtifact(artifact)}>{String(artifact.filename??uiText('打开交付文件'))}</button>}</For><Show when={detail().navigation?.status==='AVAILABLE'}><button type="button" disabled={activityLoading()} onClick={()=>void enterSupervisedTask(detail())}>{uiLocale()==='en'?'Open conversation':'进入原讨论'}</button></Show><Show when={detail().navigation?.status==='READONLY'}><small>{uiLocale()==='en'?'Historical record':'历史记录'}</small></Show></article>}</Show><GlobalSupervisorPanel active={activityOpen()&&!activityDetail()&&!activityLoading()} managed={true} snapshot={activitySnapshot()} onSnapshot={value=>{setActivitySnapshot(value);void nativeBridge?.getTaskActivity?.().catch(()=>setActivityError('任务动态已保留，浮标状态暂未同步。'));}} onOpenTask={openActivityTask}/></div><QuickInboxPanel records={quickRecords()} scope={quickScope()} bridge={quickBridge??{}} error={quickError()} onRefresh={()=>void quickInbox.refresh()} onSent={importQuickRecord}/>      <Show when={windowMode()==='companion'&&error()}><div class="as-error-bar" role="alert"><span>{authoredText(error(),uiText)}</span><button aria-label={uiText("关闭错误提示")} onClick={()=>setError('')}><Icon name="close"/></button></div></Show><Show when={windowMode()==='companion'&&notice()}><div class="as-notice" role="status">{uiCopyText(notice())}</div></Show><Show when={latestMessage().visible}><button type="button" class="as-conversation-latest" inert={skillDrawer()} style={{top:latestMessage().top+'px'}} aria-label={uiLocale()==='en'?'Jump to latest message':'查看最新消息'} title={uiLocale()==='en'?'Jump to latest message':'查看最新消息'} onClick={event=>{const focused=document.activeElement===event.currentTarget;conversationReading.latest();if(focused)shell?.querySelector<HTMLElement>('.as-agent-content > .as-conversation')?.focus({preventScroll:true});}}><Icon name="arrow-down-to-line"/></button></Show><main class="as-conversation" tabIndex={-1}><Show when={threadAccess()!=='ready'&&!threadOrganizationPending()}><div class="as-conversation-organization-status" role="status"><span>{threadAccess()==='deleted'?(uiLocale()==='en'?'This chat was deleted. Your local draft is kept; you can copy it or select another chat.':'此会话已删除。本机草稿保留，你可以复制草稿或选择其他会话。'):uiLocale()==='en'?'Checking the conversation change. Your draft is kept.':'会话整理待核对，当前草稿保留。'}</span><Show when={threadAccess()==='unavailable'&&!organizationClient.pending}><button type="button" onClick={()=>void syncOrganizedThread()}>{uiLocale()==='en'?'Refresh conversation':'刷新会话'}</button></Show><Show when={threadAccess()==='deleted'||threadAccess()==='unavailable'}><button type="button" onClick={()=>void navigator.clipboard.writeText(prompt()).catch(()=>setError(uiLocale()==='en'?'Select and copy the text in your draft below.':'请在下方输入框中选中并复制草稿。'))}>{uiLocale()==='en'?'Copy draft':'复制草稿'}</button></Show></div></Show><Show when={threadAccess()!=='deleted'}><Show when={threadScope()}><ConversationTranscript onModelConnection={()=>void openProviders()} messages={threadMessages().filter(row=>row.source.kind!=='native'||!nativeMessages().some(record=>record.message.operation_id===row.operation_id&&(record.message.project_id??null)===row.source.project_id&&record.message.conversation_id===row.source.conversation_id))} locale={uiLocale()} more={!!threadCursor()} loading={threadLoading()} error={threadError()} displayVersion={feedbackDisplayVersion()} onMore={()=>void refreshThread(true)} onRetry={()=>void refreshThread()} onInput={threadInput} onResult={row=>void threadResult(row)} onDocument={row=>void openThreadDiscoveryDocument(row)} resolveUser={threadUserText} liveText={threadLiveText}/></Show><dialog ref={threadResultDialog} class="as-thread-result-dialog" onCancel={event=>{event.preventDefault();threadResultGeneration++;setThreadResultSelection(undefined);}} onClose={()=>{threadResultGeneration++;setThreadResultSelection(undefined);}}><header><strong>{uiLocale()==='en'?'Task result':'任务结果'}</strong><button type="button" aria-label={uiLocale()==='en'?'Close task result':'关闭任务结果'} onClick={()=>setThreadResultSelection(undefined)}>×</button></header><Show when={threadResultSelection()}>{record=><article class="as-native-message"><NativeAssistantResult record={record()}/></article>}</Show></dialog><Show when={supervisorReturn()}><button type="button" class="as-text-action" onClick={()=>void returnToSupervisor()}>{uiLocale()==='en'?'Back to supervisor':'返回总管'}</button></Show><FileFeedbackReview/><Show when={toolMode()==='original'&&originalSession()&&(!threadScope()||originalSession()!.messages.some(item=>!threadHasOriginal(item.info.id))||originalSession()!.pending_questions.length||originalSession()!.pending_permissions.length)}><section class="as-original-transcript" aria-label={uiText("真实原会话消息")}><Show when={originalMessagesNeedAttention(originalSession()!)&&!(originalOwnerCanResume(originalSession())&&originalOwnerNeedsRecovery(originalSession()!,originalOwnerRecoveryState().attempt)&&!originalSession()!.observation_error&&!originalSession()!.native_messages_error)}><p class="as-inline-warning">{uiText("原消息观察尚未更新，历史记录保留；请核对当前原会话。")}</p></Show><For each={originalMessageRows().filter(item=>showOriginalLiveMessage(item,originalSession()!,threadScope()?threadMessages():[]))}>{item=><OriginalLiveMessage message={item} locale={uiLocale()} displayedCommands={displayedCommands()}/>}</For><For each={originalRecovery().filter(item=>!item.receipt)}>{item=><article class="as-inline-warning"><strong>{uiLocale()==='en'?'Result needs checking':'结果待核查'}</strong><p>{item.input}</p><button type="button" onClick={event=>void showOriginalSessionManager(event.currentTarget)}>{uiLocale()==='en'?'Check original operation':'核查原操作'}</button></article>}</For><For each={originalOperations()}>{operation=><><Show when={originalGuidanceText(operation,uiText)}><article class="as-history-message as-guidance-message"><Show when={operation.role==='LOCAL_STATUS'}><small>{uiText("你")}</small><p>{String((operation.request as {raw_text?:string}|undefined)?.raw_text??uiText('查看进度'))}</p></Show><small>{operation.role==='LOCAL_STATUS'?uiText("任务进度"):uiText("补充指令")}</small><p role="status">{originalGuidanceText(operation,uiText)}</p></article></Show></>}</For></section></Show><Show when={originalSession()&&originalSession()!.project_id===project()?.id&&(toolMode()==='original'||threadScope()?.sources.some(source=>source.original_source?.kind==='original'&&source.original_source.project_id===originalSession()!.project_id&&sameNativeReference(source.original_source.session_ref,originalSession()!.session_ref)))}><OriginalSessionFiles session={originalSession()!} contextKey={JSON.stringify(selectedDiscussion()?.key??[project()?.id,conversationId()])} locale={uiLocale()} onOpen={openOriginalDeliveredFile}/><OriginalCommandResults session={originalSession()!} contextKey={commandContextKey()} locale={uiLocale()} onCoverage={observeCommandDisplay} onContinueWork={chooseOriginalWorkInput}/></Show><For each={nativeMessageRows()}>{record=><article class="as-native-message" data-operation-id={record.message.operation_id} data-conversation-id={record.message.conversation_id??conversationId()??''}><div class="as-native-user-message"><small>{uiText("你")}</small><NativeUserInput record={record}/></div><NativeAssistantResult record={record}/></article>}</For><For each={directResults()}>{item=><article class="as-history-message as-direct-result"><Show when={directInstructions()[item.operation_id]}><div class="as-native-user-message"><small>{uiText("你")}</small><p>{directInstructions()[item.operation_id]}</p></div></Show><small>{item.status==='APPLIED'?uiText("文件已更新"):item.status==='NEEDS_INPUT'?uiText("需要补充"):item.status==='READY_NOT_AUTHORIZED'?uiText("建议差异 · 尚未写入"):item.status}</small><p>{item.summary??item.error?.message??uiText("实际动作回执已保存")}</p><For each={item.interaction.questions}>{question=><div class="as-inline-question"><strong>{question.text}</strong><p>{question.reason}</p><For each={question.choices??[]}>{choice=><button onClick={()=>setPrompt(text=>text+(text?'\n':'')+choice)}>{choice}</button>}</For><button onClick={()=>setPrompt(text=>text+(text?'\n\n':'')+(directInstructions()[item.operation_id]??'')+`\n补充 ${question.text}：`)}>{uiText("在聊天中回答")}</button></div>}</For><Show when={['PROCESSING','CANCEL_REQUESTED'].includes(item.status)}><div class="as-companion-file-actions"><button type="button" onClick={()=>observeDirectOperation(item.project_id,item.operation_id)}>{uiText("继续读取执行状态")}</button><button type="button" onClick={()=>void directApi.cancel(item.project_id,item.operation_id).then(directRecord).catch(error=>setError(authoredErrorCopy(error)))}>{uiText("停止此文件动作")}</button></div></Show><Show when={item.diff}><details><summary>{uiText("查看实际建议或已应用差异")}</summary><pre class="as-json">{item.diff}</pre></details></Show><Show when={item.after_ref}><button type="button" class="as-native-artifact" disabled={!!busy()} onClick={()=>void openDirectSource(item.after_ref!)}><Icon name="open-file"/><span>{item.after_ref!.path}<small>{uiText("实际更新版本 v")}{item.after_ref!.revision} · {item.after_ref!.sha256.slice(0,12)}</small></span><small>{uiText("原软件打开")}</small></button></Show><Show when={directFeedbackRefs()[item.operation_id]}><p class="as-muted">{uiText("反馈 ")}{directFeedbackRefs()[item.operation_id].id.slice(0,12)}{uiText(" 已按原文件基线保存；")}{nativeFileFeedbacks().flatMap(value=>value.direct_action_links).some(link=>link.payload.action_receipt.operation_id===item.operation_id&&!!link.payload.action_receipt.after_ref)?uiText("实际修改前后版本已关联"):nativeFileFeedbacks().some(value=>value.direct_action_links.some(link=>link.payload.action_receipt.operation_id===item.operation_id))?uiText("原文件与终态动作已关联，未记录新写入版本"):uiText("实际修改版本尚未关联")}</p></Show><Show when={item.before_ref||item.after_ref}><details><summary>{uiText("来源与执行回执")}</summary><pre class="as-json">{JSON.stringify({before:item.before_ref,after:item.after_ref,current:item.current,execution:item.execution},null,2)}</pre></details></Show></article>}</For><Show when={nativeFileFeedbacks().length||sourceChecks().some(item=>item.status!=='CURRENT')||sourceSyncs().length||sourceWatchError()}><details class="as-conversation-review"><summary>{uiText("来源与返工记录")}</summary><Show when={nativeFileFeedbacks().length}><button class="as-text-action" onClick={()=>{setReviewTab('attention');showProjectReview();}}>{uiText("审阅文件反馈与局部返工 · ")}{nativeFileFeedbacks().length}</button></Show><Show when={sourceChecks().some(item=>item.status!=='CURRENT')||sourceSyncs().length||sourceWatchError()}><section class="as-source-notifications" aria-label={uiText("来源变化")}><strong>{uiText("来源变化")}</strong><For each={sourceChecks().filter(item=>item.status!=='CURRENT')}>{item=><article><p>{item.source_ref.path} · {item.status==='MISSING'?uiText("原件缺失"):uiText("原件字节已变化")}</p><small>{uiText("依据 v")}{item.source_ref.revision} {item.source_ref.sha256}<br/>{uiText("当前 ")}{item.live_sha256??uiText("文件不存在")}</small><Button size="small" variant="secondary" disabled={!!busy()||item.status==='MISSING'} onClick={()=>void action('同步来源上下文',()=>synchronizeSource(item.source_ref))}>{uiText("同步来源到 Agent")}</Button></article>}</For><For each={sourceSyncs().slice(-4)}>{item=><article><p>{item.current.path}{uiText(" · 上下文已同步")}</p><small>v{item.previous.revision} {item.previous.sha256}<br/>→ v{item.current.revision} {item.current.sha256}</small></article>}</For><Show when={sourceWatchError()}><p role="status" class="as-inline-warning">{uiText("来源观测暂未完成：")}{sourceWatchError()}</p></Show><p class="as-muted">{uiText("仅观察已选文件与关联资料。画面变化不代表原件保存；同步不会自动请求模型。")}</p></section></Show></details></Show><Show when={activeSession()}><div class="as-persistent-transcript" data-reading-anchor="discovery-body" data-project-id={activeSession()!.project_id} data-discovery-session-id={activeSession()!.id}><For each={activeSession()!.payload.history.filter(raw=>(raw as {kind:string}).kind!=='USER_FEEDBACK')}>{raw=>{const item=raw as {kind:string;text:string;created_at:string};return <article class="as-history-message"><small>{item.kind==='USER_ANSWER'?uiText("你的回答"):uiText("你")}</small><p>{item.text}</p></article>;}}</For><For each={feedbackRecords()}>{feedback=><article class="as-history-message as-feedback-message"><small>{uiText("批注已保存 · ")}{feedback.payload.tool}</small><p>{feedback.payload.comment}</p><span>{uiText("依据文稿版本 ")}{feedback.payload.artifact_ref.revision}</span><Show when={feedback.payload.revision_target_ref.revision===activeSession()!.revision}><Button size="small" variant="ghost" disabled={!!invalidFeedback()[feedback.feedback_ref.id]||!!busy()||activeOperation()?.status==='PROCESSING'||activeOperation()?.status==='REQUESTING'} onClick={()=>void action('按已保存反馈修订',()=>reviseRecordedFeedback(feedback))}>{uiText("按这条反馈修订")}</Button></Show></article>}</For><Show when={activeSession()!.revision!==1||activeSession()!.payload.execution?.mode!=='USER_INPUT'}><article class="as-history-message as-agent-understanding" data-reading-anchor="discovery-summary"><small>{uiText(activeSession()!.payload.status==='CONFIRMED'?'已确认要求 · 版本 ':'当前要求草稿 · 版本 ')}{activeSession()!.revision}</small><AssistantReply text={activeSession()!.payload.candidate.summary}/><button type="button" class="as-text-action" onClick={()=>inspect(envelope('discovery',activeSession()!.id,activeSession()!,activeSession()!.revision,activeSession()!.sha256),'EXPLICIT_OPEN')}>{uiText('查看要求')}</button></article></Show></div></Show><Show when={feed().length||!hasConversationContent()}><Show when={feed().length} fallback={<div class="as-empty"><div class="as-empty-emblem" aria-hidden="true"><img src={brandIcon} alt="" draggable={false}/><img class="as-brand-signature" src={brandSignature} alt="" draggable={false}/></div><Show when={busy()!=='连接本地服务'} fallback={<div class="as-loading-skeleton" role="status" aria-label={uiText("正在连接本机服务")}><BrandLoader/><small>{uiText("正在连接本机服务")}</small></div>}><h1>{providersReady()&&!providers().length?uiText("连接模型，开始工作"):uiText("在这里继续")}</h1><Show when={providersReady()&&!providers().length}><div class="as-connection-actions"><Button variant="primary" onClick={()=>void openProviders('subscription')}>{uiText("登录 ChatGPT 账号")}</Button><Button variant="secondary" onClick={()=>void openProviders('api')}>{uiText("配置 API / 内网模型")}</Button></div></Show><Show when={providers().length>0&&!workspace()&&!directFile()}><button class="as-empty-folder" type="button" onClick={()=>openFolderDialog()}><Icon name="folder"/>{uiText("关联工作目录")}</button></Show></Show></div>}><div class="as-transcript" aria-live="polite"><DeliveryHistoryFeed items={feed()} directories={deliveryDirectories()} render={(id,presentation)=>{const item=createMemo(()=>feedById().get(id)!),artifact=createMemo(()=>item().artifact);return <article class="as-feed-item" data-reading-anchor={"feed:"+id}><div class="as-feed-symbol"><Icon name={item().status==='PROPOSED'||item().deliveryTarget&&(!item().artifact||!deliveryComplete(item().artifact!.data as DeliveryOperation))?'brain':'check'}/></div><div><div class="as-feed-title"><strong tabIndex={item().deliveryTarget?-1:undefined}>{presentation().title??(item().deliveryTarget?authoredText(item().title_copy??item().title,uiText):uiText(item().title))}</strong><TaskStatus value={item().status}/></div><Show when={item().deliveryTarget}><p class="as-muted"><Show when={presentation().timeLabel} fallback={uiText("创建时间未提供")}><time dateTime={presentation().createdAt}>{uiText("记录创建于 {time}",{time:presentation().timeLabel!})}</time></Show><Show when={presentation().location}> · <span style={{"overflow-wrap":"anywhere"}}>{presentation().location}</span></Show></p></Show><DeliveryFeedFeedback item={item()} onInspect={inspectDeliveryFeedback}/><Show when={item().artifact?.type==='planning-delivery'}><DeliveryArtifactCard artifact={artifact()!} context={context}/></Show><Show when={item().artifact&&item().artifact!.type!=='planning-delivery'}><Button variant="ghost" size="small" onClick={()=>inspect(item().artifact)}>{item().artifact?.type==='report'&&nativeBridge?.openReportArtifact?uiText("在原软件打开"):uiText("查看详情")}</Button></Show></div></article>;}}/></div></Show></Show></Show></main>
      <Show when={unboundRecoveryText()}><div class="as-unbound-draft" role="status"><span>{uiText("之前的未归属文字已保留")}</span><button type="button" title={unboundRecoveryText()?.slice(0,160)} disabled={!!busy()} onClick={()=>void appendUnboundDraft()}>{uiText("追加到当前草稿")}</button></div></Show>
      <WorkflowProgress session={originalSession()}/>
      <OriginalPendingControls contextRecovery={originalSourceRecovery()} ownerRecovery={originalOwnerRecovery()} session={originalSession()&&(!threadScope()||threadScope()!.sources.some(source=>source.original_source?.kind==='original'&&source.original_source.project_id===originalSession()!.project_id&&sameNativeReference(source.original_source.session_ref,originalSession()!.session_ref)))?originalSession():undefined} locale={uiLocale()} onUpdated={value=>{const previous=originalSession();if(previous?.project_id!==value.project_id||!sameNativeReference(previous.session_ref,value.session_ref)||previous.generation!==value.generation)return;selectOriginalSession(value);if(threadScope())void refreshThread();}} onRecovery={restoreOriginalControlRequest} onRecoverAnswer={restoreQuestionAnswer}/><section class="as-operation-dock" aria-label={uiText('当前处理')}>
        <OriginalTaskControls onFinished={focusComposer} session={toolMode()==='original'&&originalSession()&&(!threadScope()||threadScope()!.sources.some(source=>source.original_source?.kind==='original'&&source.original_source.project_id===originalSession()!.project_id&&sameNativeReference(source.original_source.session_ref,originalSession()!.session_ref)))?originalSession():undefined} onUpdated={value=>{const previous=originalSession();if(previous?.project_id!==value.project_id||!sameNativeReference(previous.session_ref,value.session_ref)||previous.generation!==value.generation)return;selectOriginalSession(value);if(threadScope())void refreshThread();}}/>
        <Show when={activeSession()&&!activeOperation()}><DiscoveryOperationRecovery projectId={activeSession()!.project_id} sessionId={activeSession()!.id} revision={activeSession()!.revision} onSelect={(operation,explicit)=>restoreDiscoveryOperation(activeSession()!.project_id,activeSession()!.id,operation,explicit)}/></Show>
        <Show when={activeOperation()}>{op=><LiveOperationStatus compact operation={op()} purpose={op().discoveryPurpose} historical={op().historicalInspection} historicalRevision={op().basisRevision} onBack={op().recoverySelection?()=>{clearDiscoveryPending(localStorage,op().projectId,op().id);stopObserver();setActiveOperation(undefined);setDiscoveryError(undefined);}:undefined} onInspect={op().kind==='discovery'?()=>inspectDiscoveryOperation(op().projectId,op().sessionId!,op().id):undefined} onStop={()=> (op().kind==='direct'?directApi.cancel(op().projectId,op().id):op().kind==='discovery'?discoveryApi.cancel(op().projectId,op().id):api.cancelPlanning(op().projectId,op().id)).then(()=>setNotice('已请求停止本次处理'))} onError={()=>{const owner=op();if(owner.kind==='discovery'&&owner.sessionId)setDiscoveryError({projectId:owner.projectId,sessionId:owner.sessionId,message:'暂时无法核查或停止本次处理，原记录和输入保留。'});else setError('暂时无法核查或停止本次处理，原记录和输入保留。');}}/>}</Show>
        <Show when={!!discoveryError()&&discoveryError()?.projectId===project()?.id&&discoveryError()?.sessionId===activeSession()?.id}><p class="as-discovery-error" role="alert">{uiText(discoveryError()!.message)}</p></Show>
      </section><form class="as-composer" data-deleted={String(threadAccess()==='deleted')} data-draft-only={String(!threadScope()&&selectedDiscussion()?.key.kind==='conversation')} data-composer-folded={String(windowMode()==='companion'&&composerView().folded)} data-composer-focused={String(composerView().focused)} data-has-draft={String(!!prompt().length||!!nativeDraftAttachments().length||!!draftAttachments().length||!!originalWorkInputChoice())} onSubmit={e=>{e.preventDefault();closeComposerMenu();void submitPrompt();}}>
        <Show when={originalWorkInputChoice()}>{choice=><div class="as-original-work-choice"><span title={choice().label}>{uiLocale()==='en'?'Continuing: ':'继续原工作：'}{choice().label||(uiLocale()==='en'?'Original work':'原工作')}</span><button type="button" onClick={clearOriginalWorkInput}>{uiLocale()==='en'?'Remove link':'移除关联'}</button><Show when={toolMode()!=='original'&&originalWorkChoiceMatches(choice(),originalSession())}><button type="button" onClick={()=>chooseTool('original',false)}>{uiLocale()==='en'?'Use original conversation':'使用原会话'}</button></Show><Show when={!originalWorkChoiceMatches(choice(),originalSession())}><p role="status">{uiLocale()==='en'?'Choose its original connection before sending. The draft is kept.':'发送前请选回对应原连接，草稿已保留。'}</p></Show></div>}</Show>
        <NativePageConversation folded={windowMode()==='companion'&&composerView().folded} scopeKey={JSON.stringify(associationScope(quickScope()))} busy={!!busy()||!!nativePending()} mode={<><Show when={toolMode()!=='original'}><div class="as-native-review-composer" hidden={!pageReview()&&!currentReviewCatalogError()&&!reviewContinue()&&!reviewUnknown()&&!ordinaryGuidanceAvailable()&&!nativePending()}><Show when={pageReview()}><button type="button" onClick={()=>void openPageReview()}>{uiText('技能与工具')}{pageReview()?' · '+pageReviewLabel():''}</button></Show><Show when={currentReviewCatalogError()}>{copy=><div class="as-native-review-composer as-native-composer-warning" data-native-catalog-error role="status" style={{'flex-basis':'100%','min-width':'0'}}><span>{authoredText(copy(),uiText)}</span><Show when={!quickScope().providerProfileRef}><button type="button" onClick={openReviewModelConnections}>{uiText('选择模型')}</button></Show></div>}</Show><Show when={pageReview()}><button type="button" onClick={()=>changePageReview(null)} aria-label={uiText('移除选择')}>×</button><Show when={!reviewContinue()&&needsReviewProcess(pageReview(),reviewCatalog(),reviewHintSelection())}><label class="as-native-review-choice"><input type="checkbox" checked={pageReview()!.process_start_consent} onChange={event=>{setPageReview({...pageReview()!,process_start_consent:event.currentTarget.checked});}}/>{uiText('允许启动所选本地工具')}</label></Show><Show when={!reviewContinue()&&pageReview()?.version===2&&needsReviewRemote(pageReview(),reviewCatalog(),reviewHintSelection())}><label class="as-native-review-choice"><input type="checkbox" checked={pageReview()?.version===2&&(pageReview() as import('./native-review').NativeCapabilityTaskInput).remote_mcp_connect_consent} onChange={event=>{const checked=event.currentTarget.checked;setPageReview(current=>current?.version===2?{...current,remote_mcp_connect_consent:checked}:current);}}/>{uiText('允许连接所选远端工具')}</label></Show><Show when={!reviewContinue()&&pageReview()?.version===2&&(pageReview() as import('./native-review').NativeCapabilityTaskInput).task_root_operation_id}><span>{uiText('继续任务')}</span></Show></Show><Show when={reviewContinue()||reviewUnknown()||ordinaryGuidanceAvailable()||nativePending()&&!ordinaryNewQuestion()||pageReview()?.version===2&&(pageReview() as import('./native-review').NativeCapabilityTaskInput).task_root_operation_id}><Show when={!(pageReview()?.version===2&&(pageReview() as import('./native-review').NativeCapabilityTaskInput).task_root_operation_id)&&(!reviewUnknown()||nativeMessages().find(row=>row.message.operation_id===reviewUnknown())?.message.status==='UNKNOWN_OUTCOME')}><span>{uiText(reviewUnknown()?'结果尚未确认，请核查原操作':ordinaryGuidanceParent()?'补充当前任务':'补充本页')}</span></Show><button type="button" onClick={newPageQuestion}>{uiText('新问题')}</button></Show></div></Show><Show when={toolMode()==='original'&&!threadScope()?.metadata_source}><div class="as-owner-composer-mode"><label><span>{uiText("原会话 Agent")}</span><select aria-label={uiText("原会话 Agent")} value={originalAgent()} onChange={event=>{changeConversationAgentMode(event.currentTarget.value as OriginalAgent);event.currentTarget.value=originalAgent();}}><option value="plan">Plan</option><option value="build">Build</option><option value="workflow" disabled={originalSession()?.workflow_mode_supported===false||!!originalSession()?.connected_owner&&!originalSession()?.available_agent_modes?.includes('workflow')}>Workflow</option></select></label><button type="button" onClick={()=>chooseTool('direct')}>{uiText("返回普通任务")}</button></div></Show></>} context={<><Show when={directFile()||directFileRequired()||linkedFile()||discussionFileState()?.legacy_candidates.length||discussionFileError()}><CompanionFileContext onRelated={showSelectedFileContext} asInput={toolMode()==='direct'||!threadScope()&&selectedDiscussion()?.key.kind==='native'} scopeKey={JSON.stringify(associationScope(quickScope()))} onRetryContext={()=>void discussionFiles?.refresh()} selected={nativeFeedbackAvailable()?directFile():undefined} previousFilename={previousDirectFile()?.source_ref.path.split(/[\\/]/).at(-1)} onSelectPrevious={()=>void selectPreviousDirectFile()} linked={linkedFile()} legacy={discussionFileState()?.legacy_candidates} contextError={discussionFileError()} onOpenLinked={()=>void openLinkedContext()} onUnlink={()=>void action('解除文稿关联',unlinkDiscussionFile)} onSelectLegacy={reference=>void action('关联文稿至当前讨论',async()=>{await discussionFiles?.select(reference);})} observation={pinnedObservation()} required={directFileRequired()} busy={!!busy()||!!nativePending()} title={toolMode()==='original'?uiText("文件处理"):uiText("当前页协作")} onChoose={()=>void chooseDirectFile()} onOpen={()=>directFile()&&void openDirectSource(directFile()!.file.source_ref)} onReadCurrent={()=>void refreshDirectFile()} onRemove={()=>{const selected=directFile();if(!selected)return;directFileGeneration++;setDirectFile(undefined);setDirectWrite(false);setDirectFileRequired(false);saveCompanionFileDraft(localStorage,selected.file.source_ref.project_id,undefined);}}/></Show><Show when={showNativeFileFeedback()}><div style={{display:nativeFeedbackAvailable()?'contents':'none'}}><NativeFeedbackComposer source={directFile()!.file.source_ref} scopeKey={nativeFeedbackScope()} leaseKey={nativeFeedbackLease()} checking={nativeSelectionCheck()?.lease===nativeFeedbackLease()&&exactFileSource(nativeSelectionCheck()?.source,directFile()?.file.source_ref)} onQuoteEdit={editNativeFeedbackQuote} onClear={()=>{editNativeFeedbackQuote();setNativeFeedbackSelection(undefined);}} comment={prompt()} onAction={registerNativeFeedbackAction} capture={nativeFeedbackSelection()?.basis===nativeFeedbackLease()&&exactFileSource(nativeFeedbackSelection()?.source,directFile()?.file.source_ref)?nativeFeedbackSelection():undefined} busy={!!busy()||!!nativePending()||!nativeFeedbackAvailable()} onSaved={value=>{saveNativeFileFeedback(value);setReviewTab('attention');}} onPreview={value=>askNativeRework(value)}/></div></Show></>} attachments={composerAttachments()} input={<textarea aria-label={uiText("描述要完成的工作")} rows={windowMode()==='companion'?(composerView().focused||prompt().length?3:1):1} onFocus={()=>composerPresentation.focus(true)} onBlur={event=>{const next=event.relatedTarget;if(!(next instanceof Node)||!event.currentTarget.closest('form')?.contains(next))composerPresentation.focus(false);}} value={prompt()} onInput={e=>setPrompt(e.currentTarget.value)} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();void submitPrompt();}}} placeholder={uiText("发消息…")}/>} toolbar={<div class="as-composer-toolbar">
          <Show when={windowMode()==='companion'}><button type="button" class="as-composer-icon" aria-label={composerView().folded?uiText("继续输入"):uiText("收起输入，阅读记录")} title={composerView().folded?uiText("继续输入"):uiText("收起输入")} aria-expanded={!composerView().folded} aria-controls="as-composer-fields" onClick={()=>composerView().folded?focusComposer():composerPresentation.fold()}><Icon name={composerView().folded?'pencil-line':'chevron-down'}/></button></Show>
          <Show when={toolMode()!=='original'||!!threadScope()?.metadata_source}><TaskMethodPicker mode={toolMode()} locale={uiLocale()} agentMode={toolMode()==='original'&&threadScope()?.metadata_source?originalAgent():undefined} onAgentMode={changeConversationAgentMode} workflowUnavailable={originalSession()?.workflow_mode_supported===false||!!originalSession()?.connected_owner&&!originalSession()?.available_agent_modes?.includes('workflow')} activeTask={!!originalSession()?.active_operation_id} onChoose={chooseTool} onOpening={()=>{closeComposerMenu();setNativeMenu(false);}}/></Show>
          <ComposerAddMenu draftCopies={savedCopies().length*2} onDraftCopies={showDraftCopies} draftRecoveryOpen={!!draftRecovery()} locale={uiLocale()} busy={!!busy()||!!nativePending()||nativeContextBusy()} captureAvailable={!!nativeBridge?.getNativeInputPreview&&collaborationState()?.capabilities?.current_context===true} onFile={()=>void chooseDirectFile()} onPage={()=>void captureNativeContext(false)} onScreenshot={()=>void captureNativeContext(true)} onTools={()=>void openPageReview()} toolsAvailable={toolMode()!=='original'} previousFilename={previousDirectFile()?.source_ref.path.split(/[\\/]/).at(-1)} onPrevious={()=>void selectPreviousDirectFile()}/>
          <StudioPopover open={composerMenu()==='model'} onOpenChange={open=>open?toggleComposerMenu('model'):closeComposerMenu()} label={uiText("模型选项")} initialFocus={()=>document.querySelector<HTMLElement>('#as-composer-model-options [role=option][aria-selected=true]')??document.querySelector<HTMLElement>('#as-composer-model-options [role=option]')??undefined} placement="top-start" class="as-composer-floating" trigger={<button type="button" ref={modelMenuButton} class="as-composer-model-trigger as-composer-menu-trigger" data-model-placeholder={!providers().some(item=>item.profile_ref.id===providerId())} title={selectedModelTitle()} aria-label={uiText("选择模型连接")} aria-expanded={composerMenu()==='model'} aria-controls="as-composer-model-options" onClick={event=>toggleComposerMenu('model',event.detail===0)}><span>{providers().find(item=>item.profile_ref.id===providerId())?.model_id??uiText("模型")}</span><Icon name="chevron-down"/></button>}><section class="as-composer-popover" data-keyboard={String(composerMenuKeyboard())} id="as-composer-model-options" aria-label={uiText("模型选项")}><header><strong>{uiText("模型连接")}</strong><button type="button" aria-label={uiText("关闭模型选项")} onClick={()=>closeComposerMenu(true)}><Icon name="close"/></button></header><div class="as-composer-model-list" role="listbox" aria-label={uiText("选择模型连接")} onKeyDown={event=>railKey(event,event.currentTarget)}><For each={sortModelProfiles(providers())}>{item=><button type="button" role="option" aria-selected={providerId()===item.profile_ref.id} title={[item.model_id,modelProfileConnectionHint(item,providers())].filter(Boolean).join(' · ')} onClick={()=>{setProviderId(item.profile_ref.id);localStorage.setItem('eeas-provider-id',item.profile_ref.id);closeComposerMenu(true);}}><span class="as-composer-model-choice"><span>{item.model_id}</span><Show when={modelProfileConnectionHint(item,providers())}>{hint=><small>{hint()}</small>}</Show></span><Show when={providerId()===item.profile_ref.id}><Icon name="check"/></Show></button>}</For><Show when={!providers().length}><p class="as-model-list-empty">{uiText("还没有可用的模型连接。")}</p></Show><Show when={providerId()}><button type="button" role="option" aria-selected={false} onClick={()=>{setProviderId('');localStorage.setItem('eeas-provider-id','');closeComposerMenu(true);}}>{uiText("暂不选择模型")}</button></Show></div><div class="as-composer-model"><button type="button" onClick={()=>{closeComposerMenu();void openProviders();}}>{uiText("配置连接")}<Icon name="arrow-right"/></button></div></section></StudioPopover>
          <StudioPopover open={composerMenu()==='options'} onOpenChange={open=>open?toggleComposerMenu('options'):closeComposerMenu()} label={uiText("消息选项")} placement="top-start" class="as-composer-floating" trigger={<button type="button" ref={optionsMenuButton} class="as-composer-icon as-composer-menu-trigger" aria-label={uiText("消息选项")} title={uiText("消息选项")} aria-expanded={composerMenu()==='options'} aria-controls="as-composer-action-options" onClick={event=>toggleComposerMenu('options',event.detail===0)}><Icon name="settings-gear"/></button>}><section class="as-composer-popover" data-keyboard={String(composerMenuKeyboard())} id="as-composer-action-options" aria-label={uiText("消息选项")}><header><strong>{uiText("消息选项")}</strong><button type="button" aria-label={uiText("关闭消息选项")} onClick={()=>closeComposerMenu(true)}><Icon name="close"/></button></header><Show when={toolMode()==='discovery'&&!activeSession()}><div class="as-entry-modes" aria-label={uiText("工作起点")}><For each={[['PROBLEM','探索问题'],['REQUIREMENTS','已有需求'],['TASK_FILE','已有任务文件']] as const}>{([mode,label])=><button type="button" aria-pressed={entryMode()===mode} onClick={()=>setEntryMode(mode)}>{uiText(label)}</button>}</For></div></Show><div class="as-composer-permissions"><Show when={toolMode()==='discovery'}><label class="as-discovery-wait-choice"><span>{uiText("需求梳理等待上限")}</span><select aria-label={uiText("需求梳理等待上限")} value={discoveryWaitSeconds()} disabled={activeOperation()?.status==='REQUESTING'||activeOperation()?.status==='PROCESSING'} onChange={event=>{try{setDiscoveryWaitSeconds(saveDiscoveryWait(localStorage,Number(event.currentTarget.value)));setDiscoveryWaitError('');}catch(error){setDiscoveryWaitError(authoredErrorCopy(error));}finally{event.currentTarget.value=String(discoveryWaitSeconds());}}}><option value={120}>{uiText("等待 2 分钟")}</option><option value={300}>{uiText("等待 5 分钟")}</option><option value={600}>{uiText("等待 10 分钟")}</option></select><small>{uiText("仅用于新消息，已有请求的等待上限不变")}</small><Show when={discoveryWaitError()}><small role="alert">{authoredText(discoveryWaitError(),uiText)}</small></Show></label></Show><Show when={toolMode()!=='original'&&!linkedFile()&&(toolMode()==='direct'||windowMode()==='companion')}><label><input type="checkbox" checked={directWrite()} disabled={!!busy()||!!nativePending()||!nativeFeedbackAvailable()||!!directFile()&&!directFileSupported(directFile()!.file)} onChange={event=>windowMode()==='companion'||directFile()?void changeDirectWrite(event.currentTarget.checked):setDirectWrite(event.currentTarget.checked)}/>{windowMode()==='companion'?uiText("允许修改所选文件"):uiText("修改当前文件")}</label></Show><Show when={toolMode()!=='original'&&windowMode()==='companion'&&directFile()&&!linkedFile()&&nativeFeedbackAvailable()}><button type="button" disabled={!!busy()||!prompt().trim()} onClick={()=>void saveSelectedFeedback()}>{uiText("保存文件反馈")}</button></Show></div><details class="as-composer-source-details"><summary>{uiText("上下文与处理范围")}</summary><span>{windowMode()==='companion'?(linkedFile()?uiText('提问时读取此讨论关联文件的保存稿'):directFile()?(directFileSupported(directFile()!.file)?uiText("只处理明确选择的 UTF-8 文件版本"):uiText("只读问答引用固定文件；修改尚未适配")):(nativeDraftAttachments().length?uiText("引用明确冻结的原生批注"):uiText("普通提问发送前读取当前外部页面"))):materials().filter(item=>item.document_ref).length?uiText("资料来源已绑定"):uiText("可选择左侧文件加入上下文")}</span><Show when={toolMode()==='original'}><p>{uiLocale()==='en'?'Only your explicitly selected saved file, page references and screenshots are included. This grants no write permission.':'仅传入你明确选择的保存稿、页面引用和截图；关联本身不授予修改权限。'}</p></Show><small>{uiText("Ctrl+Enter 发送")}</small></details></section></StudioPopover>
          <Button type="submit" class="as-composer-send" aria-label={uiText(currentNativeFeedbackAction()?.active?currentNativeFeedbackAction()?.pending?'核查原反馈':'预览片段修改':discoverySendState()==='new-message'?'发送新消息':'发送消息')} title={uiText(currentNativeFeedbackAction()?.active?currentNativeFeedbackAction()?.pending?'核查原反馈':'预览片段修改':discoverySendState()==='blocked'?'请先核查结果，或修改内容与引用':discoverySendState()==='new-message'?'发送新消息 · Ctrl+Enter':'发送 · Ctrl+Enter')} variant="primary" icon="arrow-up" disabled={threadSendingBlocked()||nativeFeedbackAvailable()&&nativeSelectionCheck()?.lease===nativeFeedbackLease()&&exactFileSource(nativeSelectionCheck()?.source,directFile()?.file.source_ref)?true:currentNativeFeedbackAction()?.active?currentNativeFeedbackAction()!.busy:(discoverySendState()==='blocked'||(!prompt().trim()&&!nativeDraftAttachments().length&&!(entryMode()==='TASK_FILE'&&materials().some(item=>item.document_ref)))||!!ordinaryControlSending()||!!nativePending()&&!pageReview()&&!ordinaryNewQuestion()&&!ordinaryGuidanceAvailable()||!!reviewUnknown()&&!!pageReview()||!!reviewContinue()&&!ordinaryGuidanceAvailable()&&!(nativeMessages().find(row=>row.message.operation_id===reviewContinue())?.message.review as {can_guide?:boolean}|undefined)?.can_guide||!reviewContinue()&&!!needsReviewProcess(pageReview(),reviewCatalog(),reviewHintSelection())&&!pageReview()?.process_start_consent||!reviewContinue()&&pageReview()?.version===2&&needsReviewRemote(pageReview(),reviewCatalog(),reviewHintSelection())&&!(pageReview() as import('./native-review').NativeCapabilityTaskInput).remote_mcp_connect_consent||!!busy()||activeOperation()?.status==='PROCESSING'||activeOperation()?.status==='REQUESTING')}><span class="as-send-label">{busy()?uiText("处理中"):uiText(discoverySendState()==='new-message'?'发送新消息':'发送')}</span></Button>
        </div>}/>
      </form>

<div class="as-agent-tools-host"><InteractionPanels state={agentTools} version={agentToolVersion()} registry={registry} context={context} showRestores={false} onCollapse={()=>globalMenuReturnTarget()?.focus({preventScroll:true})}/></div><aside data-open={String(skillDrawer())} aria-hidden={!skillDrawer()} inert={!skillDrawer()} class="as-skills-drawer" id="as-agent-skills" aria-label={uiText(agentDrawer()==='original'?'原会话与执行控制':agentDrawer()==='native'?'诊断':'资料与模板')} onKeyDown={event=>{if(event.key==='Escape'&&!event.isComposing){event.preventDefault();event.stopPropagation();setSkillDrawer(false);}}}>
  <header><strong>{uiText(agentDrawer()==='original'?'原会话与执行控制':agentDrawer()==='native'?'诊断':'资料与模板')}</strong><button type="button" aria-label={uiText("返回对话")} onClick={()=>setSkillDrawer(false)}><Icon name="collapse"/></button></header>
  <section class="as-agent-drawer-section" hidden={agentDrawer()!=='native'} inert={agentDrawer()!=='native'}>
    <Show when={nativeBridge}><NativeAppendPanel diagnostics={true} onConfigureModel={()=>void openProviders()} active={skillDrawer()&&agentDrawer()==='native'} rawText={prompt()} contextRef={currentNativeReference(currentNativeContext())} providerProfileRef={providers().find(item=>item.profile_ref.id===providerId())?.profile_ref}/></Show>
  </section>
  <section class="as-agent-drawer-section" hidden={agentDrawer()!=='resources'} inert={agentDrawer()!=='resources'}>
    <button class="as-skill-item" onClick={()=>void openCompetition()}><strong>{uiText("竞品资料")}</strong></button>
    <button class="as-skill-item" onClick={()=>void openExperiences()}><strong>{uiText("模板与写法经验")}</strong></button>
    <button class="as-skill-item" onClick={()=>{setSkillDrawer(false);void openCapabilities();}}><strong>{uiText("能力与接口")}</strong></button>
  </section>
</aside>
    </div></aside><Show when={fullscreen()}><button class="as-fullscreen-edge as-fullscreen-edge-files" aria-label={fullscreenFiles()?uiText("收起全屏文件面板"):uiText("展开全屏文件面板")} aria-expanded={fullscreenFiles()} onClick={()=>{setFullscreenFiles(value=>!value);setFullscreenAgent(false);}}><Icon name="folder"/>{uiText("文件")}</button><button class="as-fullscreen-edge as-fullscreen-edge-agent" aria-label={fullscreenAgent()?uiText("收起全屏 Agent 面板"):uiText("展开全屏 Agent 面板")} aria-expanded={fullscreenAgent()} onClick={()=>{setFullscreenAgent(value=>!value);setFullscreenFiles(false);}}><Icon name="brain"/>{activeOperation()?uiText("Agent · 进行中"):'Agent'}</button><div class="as-fullscreen-controls"><button onClick={()=>void leaveFullscreen()}>{uiText("退出全屏 · Esc")}</button></div></Show>
    <NativeCapabilitiesPanel open={capabilitiesOpen()} onOpenChange={open=>{setCapabilitiesOpen(open);if(!open)setReviewSelecting(false);}} initialTab={capabilitiesTab()} owner={originalSession()} onChooseTools={chooseSessionTools} review={reviewSelecting()&&reviewCatalog()?{catalog:reviewCatalog()!,selection:pageReview()?.selection??null,onChange:changePageReview,onChoose:()=>{setCapabilitiesOpen(false);setReviewSelecting(false);}}:undefined} onOpenSessionSettings={()=>{setCapabilitiesOpen(false);showOriginalSessionManager();}}/>
    <Show when={folderDialog()}><div class="as-overlay" onClick={e=>{if(e.target===e.currentTarget)setFolderDialog(false);}}><section class="as-dialog" role="dialog" aria-modal="true" aria-labelledby="as-folder-title"><button class="as-dialog-close" aria-label={uiText("关闭目录选择")} onClick={()=>setFolderDialog(false)}>×</button><div class="as-dialog-symbol"><Icon name="folder"/></div><h2 id="as-folder-title">{uiText("打开工作目录")}</h2><label>{uiText("工程归属")}<select aria-label={uiText("目录所属工程")} value={folderTarget()} disabled={!!busy()||folderLoading()} onChange={e=>setFolderTarget(e.currentTarget.value as 'NEW'|'CURRENT')}><option value="NEW">{uiText("新建工程")}</option><Show when={folderProject()}><option value="CURRENT">{uiText("关联到 ")}{folderProject()?.name}</option></Show></select></label><Show when={folderTarget()==='NEW'}><label>{uiText("工程名称")}<input autofocus value={projectName()} disabled={!!busy()||folderLoading()} onInput={e=>setProjectName(e.currentTarget.value)} placeholder={uiText("输入工程名称")}/></label></Show><Show when={folderConnection()&&(folderConnection()!.stage!=='CONNECTED'||folderConnection()!.current_binding_status!=='AVAILABLE')}><section class="as-folder-recovery" role="status"><p>{folderConnection()!.request.name} · {folderConnection()!.request.path}</p><small>{folderConnection()!.current_binding_reason??folderConnection()!.reason??uiText("原目录连接记录已保留")}</small><div><Button variant="secondary" disabled={!!busy()||folderLoading()} onClick={()=>void recoverFolderConnection(false)}>{uiText("核对原连接")}</Button><Show when={folderConnection()!.stage==='PROJECT_SAVED'}><Button variant="secondary" disabled={!!busy()||folderLoading()} onClick={()=>void recoverFolderConnection(true)}>{uiText("继续连接原目录")}</Button></Show></div></section></Show><Show when={folderConnection()?.stage==='CONNECTED'}><Button variant="ghost" disabled={!!busy()||folderLoading()} onClick={()=>void recoverFolderConnection(true)}>{uiText("核对并打开原工作目录")}</Button></Show><Show when={nativeDirectoryPicker(globalThis)}><Button variant="secondary" icon="folder" disabled={!!busy()||folderLoading()} onClick={()=>void pickFolder()}>{uiText("选择或新建文件夹")}</Button></Show><label>{uiText("本机目录的绝对路径")}<input disabled={!!busy()||folderLoading()} value={folderPath()} onInput={e=>setFolderPath(e.currentTarget.value)} placeholder={uiText("例如 /home/me/work 或 D:\\\\work")}/></label><label class="as-check"><input type="checkbox" disabled={!!busy()||folderLoading()} checked={writeScope()} onChange={e=>setWriteScope(e.currentTarget.checked)}/><span>{uiText("允许在此目录内保存编辑")}</span></label><div class="as-dialog-footer"><Button variant="ghost" onClick={()=>setFolderDialog(false)}>{uiText("取消")}</Button><Button variant="primary" disabled={folderLoading()||!folderPath().trim()||(folderTarget()==='NEW'?!projectName().trim():!folderProject())||!!busy()} onClick={()=>void bindFolder()}>{busy()==='打开工作目录'?uiText("连接中…"):uiText("打开目录")}</Button></div></section></div></Show>
    <Show when={nativeOpen()&&nativeAvailable()}><div class="as-native"><button class="as-native-back" onClick={()=>setNativeOpen(false)}>{uiText("返回工作区")}</button><NativeApp serverUrl={new URL(nativeConfig()!.server_url!,window.location.origin).href}/></div></Show>
  </div></div>;
}
