import {createMemo,createSignal,For,onCleanup,Show} from 'solid-js';
import {stageApi,type DeliveryBundleReference,type StageRun,type RunStage} from './stage-workflows';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';

type Receipt = {
 schema:string;run_id:string;delivery_approval_sha256:string;
 files:ReceiptFile[];approvals:unknown[];test_results:{checks:{source:string}[]}[];
};
type ReceiptFile={path:string;bytes:number;sha256:string;members?:ReceiptFile[]};
type Verification={status:'match'|'mismatch'|'error';text:string};

const chunkBytes=1024**2;
const filePage=32;
function validReceiptFiles(value:unknown):value is ReceiptFile[]{
 if(!Array.isArray(value))return false;
 const pending=[{items:value,depth:0}];
 let remaining=16384;
 while(pending.length){
  const {items,depth}=pending.pop()!;
  remaining-=items.length;
  if(remaining<0||depth>8)return false;
  for(const file of items){
   if(!file||typeof file.path!=='string'||!file.path||!Number.isSafeInteger(file.bytes)||file.bytes<0||
      typeof file.sha256!=='string'||!/^[a-f0-9]{64}$/.test(file.sha256))return false;
   if(file.members!==undefined){
    if(!Array.isArray(file.members))return false;
    pending.push({items:file.members,depth:depth+1});
   }
  }
 }
 return true;
}
function receiptEntries(files:ReceiptFile[], container=''){
 const rows:{file:ReceiptFile;container:string;search:string}[]=[];
 for(const file of files){
  rows.push({file,container,search:(container+' '+file.path+' '+file.sha256).toLowerCase()});
  if(file.members)rows.push(...receiptEntries(file.members,container?container+' › '+file.path:file.path));
 }
 return rows;
}
function matchingReference(ref:DeliveryBundleReference,run:string,approval:string){
 return ref?.run_id===run&&ref.approval_sha256===approval&&ref.chunk_bytes===chunkBytes&&
  Number.isSafeInteger(ref.bytes)&&ref.bytes>0&&Array.isArray(ref.sha256_chunks)&&
  ref.sha256_chunks.length===Math.ceil(ref.bytes/chunkBytes)&&ref.sha256_chunks.every(sha=>typeof sha==='string'&&/^[a-f0-9]{64}$/.test(sha));
}
async function matchesFile(file:File,reference:DeliveryBundleReference,signal:AbortSignal){
 if(file.size!==reference.bytes)return false;
 for(const [index,expected] of reference.sha256_chunks.entries()){
  if(signal.aborted)return false;
  const offset=index*chunkBytes;
  const bytes=await file.slice(offset,offset+chunkBytes).arrayBuffer();
  const hash=new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
  if(Array.from(hash,byte=>byte.toString(16).padStart(2,'0')).join('')!==expected)return false;
 }
 return !signal.aborted;
}

export default function WorkflowDeliveryReceipt(p:{run:StageRun;stage:RunStage;t:(zh:string,en:string)=>string}){
 const [open,setOpen]=createSignal(false),[busy,setBusy]=createSignal(false);
 const [failure,setFailure]=createSignal('');
 const [ready,setReady]=createSignal<Receipt>();
 const [bundling,setBundling]=createSignal(false),[actionError,setActionError]=createSignal('');
 const [checking,setChecking]=createSignal(false),[verification,setVerification]=createSignal<Verification>();
 const [retryFile,setRetryFile]=createSignal<File>();
 const [showFiles,setShowFiles]=createSignal(false),[query,setQuery]=createSignal(''),[visibleLimit,setVisibleLimit]=createSignal(filePage);
 let receiptArea:HTMLDivElement|undefined,fileTrigger:HTMLButtonElement|undefined,fileSearch:HTMLInputElement|undefined;
 let fileList:HTMLDivElement|undefined, overviewScroll=0;
 let controller:AbortController|undefined,bundleRequest:AbortController|undefined,checkRequest:AbortController|undefined;
 let picker:HTMLInputElement|undefined,verifyEntry:HTMLButtonElement|undefined;
 const t=p.t;
 const rows=createMemo(()=>showFiles()?receiptEntries(ready()?.files??[]):[]);
 const filtered=createMemo(()=>{
  const term=query().trim().toLowerCase();
  return term?rows().filter(entry=>entry.search.includes(term)):rows();
 });
 const sizeFormat=createMemo(()=>new Intl.NumberFormat(t('zh-CN','en')));
 onCleanup(()=>{controller?.abort();bundleRequest?.abort();checkRequest?.abort();});

 async function load(download=false){
  const sha=p.stage.approval_sha256,run=p.run,stage=p.stage;
  if(!sha||busy()||bundleRequest||checkRequest||stage.status!=='approved')return;
  const request=new AbortController();
  controller=request;
  const stillOpen=()=>!request.signal.aborted&&open()&&p.run.project_id===run.project_id&&
   p.run.id===run.id&&p.stage.stage_session_id===stage.stage_session_id&&p.stage.approval_sha256===sha;
  setBusy(true);
  if(download){setActionError('');setVerification(undefined);}
  else setFailure('');
  try{
   const bytes=await stageApi.deliveryReceipt(run,stage,sha,request.signal);
   if(!stillOpen())return;
   const receipt:Receipt=JSON.parse(new TextDecoder().decode(bytes));
   if(receipt.schema!=='cirven.workflow.delivery-receipt.v1'||receipt.run_id!==run.id||
      receipt.delivery_approval_sha256!==sha||!validReceiptFiles(receipt.files)||
      !Array.isArray(receipt.approvals)||!Array.isArray(receipt.test_results)||
      receipt.test_results.some(result=>!Array.isArray(result.checks)))
    throw Error(t('返回的凭证与这一版交付不一致。','The receipt belongs to a different delivery.'));
   setReady(receipt);
   if(download)saveDownload(new Blob([bytes],{type:'application/json'}),sha,'json');
  }catch(error){
   if(stillOpen()){
    const message=error instanceof Error?error.message:t('暂时无法核对这版交付。','Could not verify this delivery.');
    if(download)setActionError(message);
    else setFailure(message);
   }
  }finally{
   if(controller===request){controller=undefined;setBusy(false);}
  }
 }
 function close(){
  controller?.abort();
  bundleRequest?.abort();
  checkRequest?.abort();
  controller=undefined;
  bundleRequest=undefined;
  checkRequest=undefined;
  setOpen(false);setReady(undefined);setFailure('');setBusy(false);setBundling(false);setActionError('');
  setChecking(false);setRetryFile(undefined);setVerification(undefined);
  setShowFiles(false);setQuery('');setVisibleLimit(filePage);
 }
 function openFiles(){
  const body=receiptArea?.closest('.wf-dialog-body');
  overviewScroll=body?.scrollTop??0;
  setShowFiles(true);
  queueMicrotask(()=>{if(open()&&showFiles())fileSearch?.focus({preventScroll:true});});
 }
 function backToReceipt(){
  setShowFiles(false);setQuery('');setVisibleLimit(filePage);
  queueMicrotask(()=>{
   if(!open()||showFiles())return;
   const body=receiptArea?.closest('.wf-dialog-body');
   if(body)body.scrollTop=overviewScroll;
   fileTrigger?.focus({preventScroll:true});
  });
 }
 function moreFiles(){
  const start=visibleLimit();
  setVisibleLimit(start+filePage);
  queueMicrotask(()=>fileList?.querySelectorAll<HTMLElement>('summary')[start]?.focus());
 }
 function saveDownload(payload:Blob,approval:string,extension:'json'|'zip'){
  const url=URL.createObjectURL(payload);
  try{
   const link=document.createElement('a');
   link.href=url;
   link.download='cirven-delivery-'+approval.slice(0,12)+'.'+extension;
   link.click();
  }finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
 }
 async function saveBundle(){
  const run=p.run,stage=p.stage,approval=stage.approval_sha256;
  if(!approval||ready()?.delivery_approval_sha256!==approval||busy()||bundleRequest||checkRequest)return;
  const request=new AbortController();
  bundleRequest=request;
  setBundling(true);setActionError('');setVerification(undefined);setRetryFile(undefined);
  try{
   const response=await stageApi.deliveryBundle(run,stage,approval,request.signal);
   if(response.headers.get('content-type')!=='application/zip'||response.headers.get('x-cirven-delivery-approval')!==approval){
    await response.body?.cancel();
    throw Error(t('返回的交付包与此审核版本不一致。','The bundle belongs to a different approval.'));
   }
   const blob=await response.blob();
   if(!request.signal.aborted&&p.stage.approval_sha256===approval)saveDownload(blob,approval,'zip');
  }catch(error){
   if(!request.signal.aborted)setActionError(error instanceof Error?error.message:t('暂时无法下载交付包。','Could not download the delivery bundle.'));
  }finally{
   if(bundleRequest===request){bundleRequest=undefined;setBundling(false);}
  }
 }
 async function checkBundle(file:File){
  const run=p.run,stage=p.stage,approval=stage.approval_sha256;
  if(!approval||ready()?.delivery_approval_sha256!==approval||busy()||bundleRequest||checkRequest)return;
  const request=new AbortController();
  checkRequest=request;
  setChecking(true);setActionError('');setVerification(undefined);setRetryFile(undefined);
  try{
   const ref=await stageApi.deliveryBundleReference(run,stage,approval,request.signal);
   if(request.signal.aborted||p.stage.approval_sha256!==approval)return;
   if(!matchingReference(ref,run.id,approval))
    throw Error(t('核验依据与此审核版本不一致。','The reference belongs to a different approval.'));
   const match=await matchesFile(file,ref,request.signal);
   if(request.signal.aborted||p.stage.approval_sha256!==approval)return;
   setVerification(match?{status:'match',text:t('核验通过。所选 ZIP 与本版交付完全一致。','Verified. This ZIP matches the approved bundle byte for byte.')}
    :{status:'mismatch',text:t('所选 ZIP 与本版交付不一致。请核对版本。','This ZIP differs from the approved bundle. Check its version.')});
  }catch(error){
   if(!request.signal.aborted&&p.stage.approval_sha256===approval){
    setRetryFile(file);
    setVerification({status:'error',text:error instanceof Error?error.message:t('暂时无法核验交付包。','Could not verify this delivery bundle.')});
   }
  }finally{
   if(checkRequest===request){checkRequest=undefined;setChecking(false);}
  }
 }
 return <>
  <button type="button" class="wf-receipt-entry" aria-haspopup="dialog" onClick={event=>{
   event.currentTarget.focus({preventScroll:true});
   setOpen(true);void load();
  }}><WorkflowIcon name="check" size={16}/>{t('交付凭证','Delivery receipt')}</button>
  <WorkflowDialog open={open()} title={t('交付凭证','Delivery receipt')} size="compact" locale={t('zh-CN','en')} onClose={close}>
   <div class="wf-delivery-receipt" ref={receiptArea} data-show-files={showFiles()}>
    <Show when={showFiles()} fallback={<>
    <div>
     <p>{t('交付包含这一版获批文件和对应凭证。','The bundle contains the files and receipt for this approval.')}</p>
     <Show when={ready()} fallback={<p class="wf-receipt-feedback" role={failure()?'alert':'status'}>
      {failure()||t('正在核对已审核的文件和测试依据…','Verifying the approved files and test records…')}
     </p>}>{receipt=><>
      <dl>
       <div><dt>{t('已核对的成果物','Verified files')}</dt><dd>{receipt().files.length}</dd></div>
       <div><dt>{t('关联的审核版本','Linked approvals')}</dt><dd>{receipt().approvals.length}</dd></div>
       <div><dt>{t('通过的测试用例','Passed test cases')}</dt><dd>{receipt().test_results.length}</dd></div>
      </dl>
      <div class="wf-delivery-verify">
       <Show when={receipt().files.length}><button ref={fileTrigger} type="button" class="wf-link" onClick={openFiles}>
        <WorkflowIcon name="list" size={15}/>{t('查看交付文件','View delivery files')}
       </button></Show>
       <input type="file" accept=".zip,application/zip" ref={picker} hidden aria-label={t('选择交付 ZIP','Choose a delivery ZIP')}
        onChange={event=>{
         const file=event.currentTarget.files?.[0];event.currentTarget.value='';
         if(file)void checkBundle(file);
        }}/>
       <button ref={verifyEntry} type="button" class="wf-link" aria-disabled={busy()||checking()||bundling()} onClick={()=>{
        if(!busy()&&!checking()&&!bundling())picker?.click();
       }}><WorkflowIcon name="check" size={15}/>{t('核验本机 ZIP','Verify a local ZIP')}</button>
       <Show when={retryFile()}>{file=><button type="button" class="wf-link" aria-disabled={busy()||checking()||bundling()} onClick={()=>{
        verifyEntry?.focus({preventScroll:true});void checkBundle(file());
       }}>
        {t('重试核验','Retry verification')}</button>}</Show>
      </div>
     </>}</Show>
    </div>
    <div>
    <p class="wf-delivery-bundle-status" data-verification={verification()?.status}
     role={actionError()||verification()&&verification()?.status!=='match'?'alert':'status'}>
     {actionError()||verification()?.text||(busy()&&ready()?t('正在重新核对凭证…','Checking the receipt again…'):
      checking()?t('正在核验所选 ZIP…','Verifying the selected ZIP…'):
      bundling()?t('正在校验并打包本版成果…','Verifying and packing this delivery…'):'')}
    </p>
    <div class="wf-delivery-receipt-actions">
     <button type="button" classList={{'wf-primary':!ready()}} aria-disabled={busy()||bundling()||checking()} onClick={()=>void load(!!ready())}>
      <WorkflowIcon name={ready()?'download':'check'} size={16}/>
      {ready()?t('下载凭证','Download receipt'):busy()?t('正在核对','Verifying'):t('重新核对','Verify again')}
     </button>
     <Show when={ready()}>
      <button type="button" class="wf-primary" aria-disabled={busy()||bundling()||checking()} onClick={()=>void saveBundle()}>
       <WorkflowIcon name="download" size={16}/>{t('下载交付包','Download bundle')}
      </button>
     </Show>
    </div>
    </div>
    </>}>
     <div class="wf-delivery-file-header">
      <button type="button" class="wf-link" onClick={backToReceipt}><WorkflowIcon name="back" size={15}/>{t('返回凭证','Back to receipt')}</button>
      <h3>{t('交付文件','Delivery files')}</h3>
     </div>
     <input ref={fileSearch} type="search" aria-label={t('按路径或摘要查找','Find by path or digest')}
      placeholder={t('按路径或摘要查找','Find by path or digest')} value={query()} onInput={event=>{
       setQuery(event.currentTarget.value);setVisibleLimit(filePage);
       if(fileList)fileList.scrollTop=0;
      }}/>
     <div ref={fileList} class="wf-delivery-file-list">
      <For each={filtered().slice(0,visibleLimit())} fallback={<p role="status">{t('没有匹配的文件。','No matching files.')}</p>}>
       {entry=><details>
        <summary><WorkflowIcon name={entry.file.members?'box':'file'} size={16}/>
         <span class="wf-delivery-file-path">{entry.file.path}
          <Show when={entry.container}><small>{t('包内','Inside')} {entry.container}</small></Show>
         </span>
         <small class="wf-delivery-file-size">{sizeFormat().format(entry.file.bytes)} B</small>
         <WorkflowIcon name="chevron" size={13}/>
        </summary>
        <div class="wf-delivery-file-digest"><small>SHA-256</small><code>{entry.file.sha256}</code></div>
       </details>}
      </For>
      <Show when={visibleLimit()<filtered().length}>
       <button type="button" class="wf-link wf-delivery-file-more" onClick={moreFiles}>{t('显示更多','Show more')}</button>
      </Show>
     </div>
    </Show>
   </div>
  </WorkflowDialog>
 </>;
}
