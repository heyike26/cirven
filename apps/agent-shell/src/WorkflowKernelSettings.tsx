import {batch,createEffect,createSignal,For,on,onCleanup,onMount,Show} from 'solid-js';
import {api} from './api';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';

type Route={endpoint:string;model:string;credential_env:string;allow_http:boolean};
type Kernel={id:string;revision:number;route:Route;active:string|null;available:boolean;bundled:boolean;
 credential_ready:boolean;versions:{version:string;digest:string}[]};
type Release={version:string;integrity:string};
type Install={path:string;version:string;sha256:string};
type Catalog={kernels:Kernel[];company:{revision:number;required:boolean}};
type RouteDraft={base:Route;value:Route};
type Feedback={text:string;error?:string};
const emptyRoute:Route={endpoint:'',model:'',credential_env:'',allow_http:false};
const emptyInstall:Install={path:'',version:'',sha256:''};
const sameRoute=(a:Route,b:Route)=>a.endpoint===b.endpoint&&a.model===b.model&&a.credential_env===b.credential_env&&a.allow_http===b.allow_http;
const engineNames:Record<string,string>={opencode:'OpenCode','claude-code':'Claude Code'};

export default function WorkflowKernelSettings(p:{t:(zh:string,en:string)=>string;request?:{engine:string}}){
 const t=p.t;
 const [catalog,setCatalog]=createSignal<Catalog>();
 const [selected,setSelected]=createSignal('opencode');
 const [pending,setPending]=createSignal<string>();
 const [feedback,setFeedback]=createSignal<Feedback>();
 const [details,setDetails]=createSignal(false);
 const [drafts,setDrafts]=createSignal<Record<string,RouteDraft|undefined>>({});
 const [installs,setInstalls]=createSignal<Record<string,Install|undefined>>({});
 const [releases,setReleases]=createSignal<Record<string,Release|undefined>>({});
 const [tab,setTab]=createSignal<'route'|'versions'>('route');
 const [manual,setManual]=createSignal(false);
 const kernel=()=>catalog()?.kernels.find(item=>item.id===selected());
 const route=()=>drafts()[selected()]?.value??kernel()?.route??emptyRoute;
 const hasRouteDraft=()=>{const saved=kernel();return !!saved&&!sameRoute(saved.route,route());};
 const install=()=>installs()[selected()]??emptyInstall;
 const release=()=>releases()[selected()];
 const conflicted=()=>{
  const saved=kernel(),draft=drafts()[selected()];
  return !!saved&&!!draft&&!sameRoute(saved.route,draft.base);
 };
 const problem=()=>conflicted()?t('已保存的连接已变化。采用当前设置，或再次确认后使用本机草稿。','The saved connection changed. Use the saved settings or confirm that you want to keep your draft.'):feedback()?.error;
 const status=()=>pending()??(conflicted()?t('连接已更新，草稿保留','Connection changed, draft kept'):feedback()?.text??'');
 let saveAction:HTMLButtonElement|undefined,refreshAction:HTMLButtonElement|undefined;
 const cancel=new AbortController();
 onCleanup(()=>cancel.abort());

 function editRoute(patch:Partial<Route>){
  const target=kernel();
  if(!target)return;
  setDrafts(previous=>({...previous,[target.id]:{base:previous[target.id]?.base??target.route,value:{...route(),...patch}}}));
 }
 function editInstall(patch:Partial<Install>){
  setInstalls(previous=>({...previous,[selected()]:{...install(),...patch}}));
 }
 createEffect(on(()=>p.request,request=>{
  if(!request)return;
  batch(()=>{setSelected(catalog()?.company.required?'claude-code':request.engine);setTab('route');});
 }));
 async function perform(progress:string,failure:string,operation:()=>Promise<void>){
  if(pending())return;
  batch(()=>{setPending(progress);setFeedback(undefined);});
  try{await operation();}
  catch(error){
   if(!cancel.signal.aborted)setFeedback({text:failure,error:error instanceof Error?error.message:String(error)});
  }finally{
   if(!cancel.signal.aborted)setPending(undefined);
  }
 }
 function load(){
  return perform(t('正在读取内核设置…','Reading engine settings…'),t('内核设置读取失败','Could not read engine settings'),async()=>{
   const data=await api.request<Catalog>('/api/workflow-kernels','GET',undefined,cancel.signal);
   if(cancel.signal.aborted)return;
   batch(()=>{
    if(!catalog()&&data.company.required)setSelected('claude-code');
    setCatalog(data);
    setFeedback({text:t('内核设置已读取','Engine settings loaded')});
   });
  });
 }
 onMount(()=>void load());

 function change(path:string,method:string,body:object,value:string){
  const target=kernel();
  if(!target||pending())return;
  const name=engineNames[target.id];
  const submitted=drafts()[target.id];
  const installation=installs()[target.id];
  return perform(name+' · '+t('正在保存…','Saving…'),name+' · '+t('设置未保存','Settings not saved'),async()=>{
   const data=await api.request<Catalog>(`/api/workflow-kernels/${target.id}/${path}`,method,
    {...body,revision:target.revision},cancel.signal,false);
   if(cancel.signal.aborted)return;
   const saved=data.kernels.find(item=>item.id===target.id);
   batch(()=>{
    setCatalog(data);
    if(path==='route'&&saved)setDrafts(previous=>{
     const draft=previous[target.id];
     const base=saved.revision===target.revision+1?saved.route:draft?.base;
     return {...previous,[target.id]:draft&&draft!==submitted&&base?{base,value:draft.value}:undefined};
    });
    if(path==='versions'&&installation===installs()[target.id])setInstalls(previous=>({...previous,[target.id]:undefined}));
    if(path==='download')setReleases(previous=>({...previous,[target.id]:undefined}));
    setFeedback({text:name+' · '+value});
   });
  });
 }
 function lookup(){
  const id=selected(),name=engineNames[id];
  return perform(name+' · '+t('正在检查版本…','Checking release…'),name+' · '+t('官方版本读取失败','Could not read official release'),async()=>{
   const data=await api.request<Release>(`/api/workflow-kernels/${id}/release`,'GET',undefined,cancel.signal);
   if(cancel.signal.aborted)return;
   batch(()=>{
    setReleases(previous=>({...previous,[id]:data}));
    setFeedback({text:name+' · '+t('已读取官方版本','Official release read')});
   });
  });
 }
 function toggleCompany(required:boolean){
  const current=catalog()?.company;
  if(!current)return;
  return perform(t('正在保存工作流策略…','Saving workflow policy…'),t('策略未保存','Policy not saved'),async()=>{
   const data=await api.request<Catalog>('/api/workflow-kernels/company-policy','PUT',
    {revision:current.revision,required},cancel.signal,false);
   if(cancel.signal.aborted)return;
   batch(()=>{setCatalog(data);setFeedback({text:t('工作流连接策略已保存','Workflow connection policy saved')});});
  });
 }
 function resolveRoute(keepDraft:boolean){
  const current=kernel(),draft=drafts()[selected()];
  if(!current||!draft||!conflicted()||pending())return;
  batch(()=>{
   setDrafts(previous=>({...previous,[current.id]:keepDraft?{base:current.route,value:draft.value}:undefined}));
   setFeedback(undefined);
   setDetails(false);
  });
  queueMicrotask(()=>(tab()==='route'?saveAction:refreshAction)?.focus({preventScroll:true}));
 }
 function saveConnection(){
  if(conflicted()){setDetails(true);return;}
  if(!pending())void change('route','PUT',route(),t('已保存模型连接','Model connection saved'));
 }

 return <section class="workflow-kernel-settings" aria-label={t('运行内核','Workflow engines')}>
  <div class="wf-kernel-content">
   <label><span class="wf-kernel-meta">{t('内核','Engine')}<Show when={kernel()}>{item=>
    <span>{item().available?t('已安装','Installed'):t('待安装','Install required')}</span>
   }</Show></span>
    <select aria-label={t('内核','Engine')} value={selected()} disabled={!catalog()} onChange={e=>setSelected(e.currentTarget.value)}>
     <option value="opencode">OpenCode</option><option value="claude-code">Claude Code</option>
    </select>
   </label>
   <Show when={kernel()}>{current=><fieldset aria-label={current().id}>
    <div class="wf-kernel-tabs" role="group" aria-label={t('内核设置','Engine settings')}>
     <button type="button" aria-pressed={tab()==='route'} onClick={()=>setTab('route')}><WorkflowIcon name="link" size={15}/>{t('模型连接','Connection')}</button>
     <button type="button" aria-pressed={tab()==='versions'} onClick={()=>setTab('versions')}><WorkflowIcon name="history" size={15}/>{t('更新和切换版本','Update and switch versions')}</button>
    </div>
    <Show when={tab()==='route'} fallback={<div class="wf-kernel-pane">
     <div class="wf-kernel-tools">
      <button type="button" aria-disabled={!!pending()} onClick={()=>void lookup()}>{t('检查官方版本','Check official release')}</button>
      <button type="button" class="wf-primary" aria-disabled={!!pending()||!release()} onClick={()=>{
       const value=release();
       if(value)void change('download','POST',{version:value.version,integrity:value.integrity},t('已启用更新','Update activated'));
      }}>{t('下载并启用','Download and activate')}</button>
     </div>
     <small class="wf-kernel-release">{release()?.version}</small>
     <label>{t('已安装版本','Installed versions')}
      <select value={current().active??''} disabled={!current().versions.length} aria-disabled={!!pending()} onChange={event=>{
       const next=event.currentTarget.value||null;
       event.currentTarget.value=current().active??'';
       if(next!==current().active)void change('active','PUT',{sha256:next},t('已切换内核版本','Active version changed'));
      }}>
       <Show when={current().bundled||!current().active}><option value="">{current().bundled?t('随应用提供','Bundled version'):t('未安装','None installed')}</option></Show>
       <For each={current().versions}>{version=>
        <option value={version.digest} selected={current().active===version.digest}>{version.version} · {version.digest.slice(0,10)}</option>
       }</For>
      </select>
     </label>
     <div><button type="button" aria-expanded={manual()} onClick={()=>setManual(value=>!value)}><WorkflowIcon name="folder" size={15}/>{t('从本机导入版本','Import a local version')}</button></div>
     <Show when={manual()}><form onSubmit={e=>{e.preventDefault();void change('versions','POST',install(),t('已校验并启用新版本','Version verified and activated'));}}>
      <label>{t('原厂程序的绝对路径','Absolute path to vendor executable')}<input required value={install().path} onInput={e=>editInstall({path:e.currentTarget.value})}/></label>
      <label>{t('版本号','Version')}<input required value={install().version} onInput={e=>editInstall({version:e.currentTarget.value})}/></label>
      <label>{t('文件 SHA-256','File SHA-256')}<input required pattern="[0-9a-f]{64}" value={install().sha256} onInput={e=>editInstall({sha256:e.currentTarget.value})}/></label>
      <button type="submit" aria-disabled={!!pending()}>{t('校验并启用版本','Verify and activate version')}</button>
     </form></Show>
    </div>}>
     <form onSubmit={e=>{e.preventDefault();saveConnection();}}>
      <label>{selected()==='claude-code'?t('Anthropic Messages 网关地址','Anthropic Messages gateway'):t('OpenAI 兼容 API 地址','OpenAI-compatible endpoint')}
       <input type="url" required value={route().endpoint} placeholder={selected()==='claude-code'?'https://gateway.company':'https://gateway.company/v1'}
        onInput={e=>editRoute({endpoint:e.currentTarget.value})}/></label>
      <label>{t('模型路由','Model route')}<input required value={route().model} onInput={e=>editRoute({model:e.currentTarget.value})}/></label>
      <details class="wf-kernel-advanced">
       <summary>{t('凭据与网络选项','Credentials and network')}</summary>
       <div>
        <label>{t('凭据环境变量（选填）','Credential environment variable (optional)')}<input value={route().credential_env} placeholder="CIRVEN_MODEL_KEY"
         onInput={e=>editRoute({credential_env:e.currentTarget.value})}/></label>
        <small class="wf-kernel-credential"><Show when={current().route.credential_env}>
         {current().credential_ready?t('服务已读取已保存的凭据变量','The service loaded the saved credential variable'):t('服务尚未读取已保存的凭据变量','The service has not loaded the saved credential variable')}
        </Show></small>
        <label class="wf-toggle"><input type="checkbox" checked={route().allow_http} onChange={e=>editRoute({allow_http:e.currentTarget.checked})}/>{t('允许内网 HTTP','Allow intranet HTTP')}</label>
       </div>
      </details>
      <button ref={saveAction} class="wf-primary wf-save-connection" type="submit" aria-disabled={!!pending()}>{t('保存连接','Save connection')}</button>
     </form>
    </Show>
   </fieldset>}</Show>
   <Show when={catalog()}>{snapshot=><details class="wf-kernel-advanced">
    <summary>{t('公司工作流策略','Company workflow policy')}</summary>
    <label class="wf-toggle"><input type="checkbox" aria-disabled={!!pending()} checked={snapshot().company.required} onChange={event=>{
     const next=event.currentTarget.checked;
     event.currentTarget.checked=snapshot().company.required;
     if(next!==snapshot().company.required)void toggleCompany(next);
    }}/>{t('公司内网工作流仅使用 Claude Code','Require Claude Code for company workflows')}</label>
   </details>}</Show>
  </div>
  <div class="wf-kernel-feedback" data-error={!!problem()}>
   <div class="wf-kernel-feedback-text">
    <p role="status" title={status()}>{status()}</p>
    <small class="wf-kernel-draft-status">{hasRouteDraft()?t('当前内核草稿未保存','Current engine has an unsaved draft'):''}</small>
   </div>
   <button type="button" aria-haspopup="dialog" class="wf-kernel-feedback-detail" data-available={!!problem()} onClick={()=>{if(problem())setDetails(true);}}>
    {t('详情','Details')}
   </button>
   <button ref={refreshAction} type="button" aria-disabled={!!pending()} onClick={()=>void load()} title={t('刷新内核设置','Refresh engine settings')}>
    {t('刷新','Refresh')}
   </button>
  </div>
  <WorkflowDialog open={details()} title={t('内核设置详情','Engine settings details')} size="compact" childScroll locale={t('zh-CN','en')} onClose={()=>setDetails(false)}>
   <div class="wf-kernel-diagnosis">
    <div class="wf-kernel-diagnosis-scroll">
     <p>{problem()}</p>
     <Show when={conflicted()&&kernel()}>{current=><dl class="wf-kernel-conflict">
      <For each={[
       {label:t('已保存连接','Saved connection'),value:current().route},
       {label:t('当前草稿','Your draft'),value:route()}
      ]}>{item=><div><dt>{item.label}</dt><dd>{item.value.endpoint} · {item.value.model}
       <Show when={item.value.credential_env}> · {item.value.credential_env}</Show>
       <Show when={item.value.allow_http}> · {t('允许内网 HTTP','Allows intranet HTTP')}</Show>
      </dd></div>}</For>
     </dl>}</Show>
    </div>
    <Show when={conflicted()}><div class="wf-actions wf-kernel-conflict-actions">
     <button type="button" aria-disabled={!!pending()} onClick={()=>resolveRoute(false)}>{t('采用已保存连接','Use saved connection')}</button>
     <button type="button" aria-disabled={!!pending()} onClick={()=>resolveRoute(true)}>{t('保留当前草稿','Keep my draft')}</button>
    </div></Show>
   </div>
  </WorkflowDialog>
 </section>;
}
