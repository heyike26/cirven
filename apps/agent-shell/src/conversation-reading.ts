import {protectLayoutReading,readingIntentChanged} from './chat-workbench-layout.ts';
export type LatestMessagePosition={visible:boolean;top:number};
export type ConversationReadingScope={projectId:string|null;kind:'conversation'|'discovery';id:string;revision:number;sha256?:string};
type Bookmark={key:string;bottom:boolean;anchor?:string;block?:number;offset:number;top:number};
type FollowIntent={conversationId:string;operationId:string;current:()=>boolean;followCurrent?:()=>boolean;following:boolean;target?:HTMLElement;resolveTarget?:()=>HTMLElement|undefined;entry?:boolean;pending?:Bookmark;settled?:()=>boolean;rendered?:boolean};
const BOOKMARK_KEY='eeas-conversation-reading-v1';
function scopeKey(scope:ConversationReadingScope){
 if((scope.projectId!==null&&(typeof scope.projectId!=='string'||scope.projectId.length>256))||!['conversation','discovery'].includes(scope.kind)||typeof scope.id!=='string'||!scope.id||scope.id.length>400||!Number.isSafeInteger(scope.revision)||scope.revision<1||scope.sha256!==undefined&&!/^[a-f0-9]{64}$/.test(scope.sha256))return;
 return JSON.stringify([scope.projectId,scope.kind,scope.id,scope.revision,scope.sha256??null]);
}
function validBookmark(value:unknown):value is Bookmark{
 const b=value as Bookmark;return !!b&&typeof b.key==='string'&&b.key.length<=1000&&typeof b.bottom==='boolean'&&Number.isFinite(b.offset)&&Math.abs(b.offset)<=1e7&&Number.isFinite(b.top)&&b.top>=0&&b.top<=1e7&&(b.anchor===undefined||typeof b.anchor==='string'&&b.anchor.length<=800)&&(b.block===undefined||Number.isSafeInteger(b.block)&&b.block>=0&&b.block<1024);
}
/** One current chat; UI bookmarks never carry a draft, body, permission or request. */
export class ConversationReading {
 private options:{container:()=>HTMLElement|undefined;changed:(value:LatestMessagePosition)=>void;storage?:Pick<Storage,'getItem'|'setItem'>};
 private intent?:FollowIntent;
 private node?:HTMLElement;
 private frame?:number;
 private resize?:ResizeObserver;
 private mutation?:MutationObserver;
 private releaseProtection?:()=>void;
 private children=new Set<Element>();
 private lastTop=0;
 private touchY?:number;
 private revision=0;
 private entryRevision=0;
 private key?:string;
 private enteredKey?:string;
 private bookmarks=new Map<string,Bookmark>();
 private saveTimer?:ReturnType<typeof setTimeout>;
 private interactionRoot?:HTMLElement;
 private published:LatestMessagePosition={visible:false,top:0};
 constructor(options:{container:()=>HTMLElement|undefined;changed:(value:LatestMessagePosition)=>void;storage?:Pick<Storage,'getItem'|'setItem'>}){
  this.options=options;
  try{const raw=options.storage?.getItem(BOOKMARK_KEY);if(raw&&raw.length<=65536){const value=JSON.parse(raw);if(value?.version===1&&Array.isArray(value.entries)&&value.entries.length<=32)for(const b of value.entries)if(validBookmark(b))this.bookmarks.set(b.key,b);}}catch{/* Optional UI state must never fail a business save. */}
 }
 stamp(){return this.revision;}
 entryStamp(){return this.entryRevision;}
 owns(scope:ConversationReadingScope){return this.valid()&&this.intent?.conversationId===(scope.kind==='conversation'?scope.id:JSON.stringify(['discovery',scope.projectId,scope.id]));}
 following(conversationId:string){return this.valid()&&this.intent?.conversationId===conversationId&&!!this.intent.following;}
 enter(scope:ConversationReadingScope,current:()=>boolean,settled:()=>boolean=()=>true,restore=true){
  const key=scopeKey(scope);if(!key||!current())return;
  if(this.key===key&&this.enteredKey===key&&this.valid()&&this.node===this.options.container())return;
  this.clear();this.key=key;this.enteredKey=key;const saved=restore?this.bookmarks.get(key):undefined;
  this.intent={conversationId:scope.kind==='conversation'?scope.id:JSON.stringify(['discovery',scope.projectId,scope.id]),operationId:'',current,following:restore&&(saved?.bottom??true),entry:restore,pending:saved&&!saved.bottom?saved:undefined,settled,resolveTarget:()=>this.node};this.revision++;
  const node=this.options.container();if(node)this.attach(node);this.update();
 }
 follow(conversationId:string,operationId:string,current:()=>boolean,resolveTarget?:()=>HTMLElement|undefined,owner?:()=>boolean){
  this.entryRevision++;
  const prior=this.intent?.conversationId===conversationId?this.intent:undefined,key=prior?this.key:undefined;
  const trackingCurrent=owner??prior?.current??current;
  this.clear();this.key=key;this.intent={conversationId,operationId,current:trackingCurrent,followCurrent:current,following:true,resolveTarget,rendered:prior?.rendered};this.revision++;
  const node=this.options.container();if(node)this.attach(node);this.update();
 }
 sync(conversationId:string|undefined,scope?:ConversationReadingScope){
  if(this.intent&&(this.intent.conversationId!==conversationId||!this.intent.current())){this.clear();return;}
  const key=scope&&scopeKey(scope);
  if(this.intent&&this.key&&key&&key!==this.key){this.persist();this.key=key;this.intent.pending=undefined;this.intent.entry=false;this.intent.rendered=false;this.changedIntent();this.update();}
 }
 pause(conversationId?:string){this.entryRevision++;if(this.intent&&(!conversationId||this.intent.conversationId===conversationId)){this.intent.following=false;this.intent.entry=false;this.intent.pending=undefined;this.changedIntent();this.remember();this.update();}else this.revision++;}
 interact(){if(this.intent?.entry)this.pause();else{this.entryRevision++;this.revision++;}}
 latest(){if(this.valid()&&this.intent){this.entryRevision++;this.intent.followCurrent=undefined;this.intent.following=true;this.intent.entry=false;this.intent.pending=undefined;this.changedIntent();this.update();}}
 update(){if(this.frame===undefined&&this.intent)this.frame=requestAnimationFrame(()=>{this.frame=undefined;this.render();});}
 flush(){if(this.intent?.current())this.remember();if(this.saveTimer!==undefined)clearTimeout(this.saveTimer);this.saveTimer=undefined;this.persist();}
 clear(){
  this.flush();this.revision++;this.intent=undefined;this.key=undefined;this.enteredKey=undefined;
  if(this.node)readingIntentChanged(this.node);this.releaseProtection?.();this.releaseProtection=undefined;
  if(this.frame!==undefined)cancelAnimationFrame(this.frame);this.frame=undefined;
  this.resize?.disconnect();this.mutation?.disconnect();this.resize=undefined;this.mutation=undefined;this.children.clear();
  if(this.node){for(const [name,listener]of this.listeners)this.node.removeEventListener(name,listener);}
  if(this.interactionRoot)for(const name of ['input','compositionstart','pointerdown'])this.interactionRoot.removeEventListener(name,this.takeOver,true);
  this.interactionRoot=undefined;this.node=undefined;this.touchY=undefined;this.publish({visible:false,top:0});
 }
 private valid(){if(this.intent&&!this.intent.current()){this.clear();return false;}if(this.intent?.followCurrent&&!this.intent.followCurrent()){this.intent.followCurrent=undefined;this.intent.following=false;this.intent.target=this.node;this.intent.resolveTarget=()=>this.node;this.changedIntent();}return !!this.intent;}
 private changedIntent(){this.revision++;if(this.node)readingIntentChanged(this.node);}
 private nearBottom(){return !!this.node&&this.node.scrollHeight-this.node.clientHeight-this.node.scrollTop<8;}
 private anchors(){return [...this.node?.querySelectorAll<HTMLElement>('[data-reading-anchor]')??[]].filter(row=>!!row.dataset.readingAnchor&&row.dataset.readingAnchor.length<=800&&row.getClientRects().length>0);}
 private block(row:HTMLElement,index?:number){return index===undefined?row:row.querySelectorAll<HTMLElement>('p,h1,h2,h3,h4,li,pre,table')[index]??row;}
 private remember(){
  const node=this.node,intent=this.intent;if(!this.key||!node?.isConnected||!node.clientHeight||!intent?.rendered||intent.pending)return;
  const top=node.getBoundingClientRect().top,anchors=this.anchors(),visible=anchors.filter(a=>a.getBoundingClientRect().bottom>top+4),row=visible.filter(a=>a.getBoundingClientRect().top<=top+4).at(-1)??visible[0];
  const blocks=row?[...row.querySelectorAll<HTMLElement>('p,h1,h2,h3,h4,li,pre,table')].slice(0,1024):[],index=blocks.findIndex(a=>a.getClientRects().length>0&&a.getBoundingClientRect().bottom>top+4);
  const anchor=row?.dataset.readingAnchor,unique=anchor&&anchors.filter(a=>a.dataset.readingAnchor===anchor).length===1;
  const b:Bookmark={key:this.key,bottom:!!intent.following&&!!this.nearBottom(),offset:row?(this.block(row,index>=0?index:undefined).getBoundingClientRect().top-top):0,top:node.scrollTop,...unique?{anchor,...index>=0?{block:index}:{}}:{}};
  if(!validBookmark(b)||!b.bottom&&!b.anchor)return;
  if(JSON.stringify(this.bookmarks.get(this.key))===JSON.stringify(b))return;
  this.bookmarks.delete(this.key);this.bookmarks.set(this.key,b);while(this.bookmarks.size>32)this.bookmarks.delete(this.bookmarks.keys().next().value!);
  if(this.saveTimer===undefined)this.saveTimer=setTimeout(()=>{this.saveTimer=undefined;this.persist();},150);
 }
 private persist(){try{this.options.storage?.setItem(BOOKMARK_KEY,JSON.stringify({version:1,entries:[...this.bookmarks.values()]}));}catch{/* A reading bookmark cannot clear or reject a draft. */}}
 private scroll=()=>{
  if(!this.valid()||!this.node||!this.intent)return;
  const top=this.node.scrollTop;if(this.intent.pending){this.lastTop=top;this.update();return;}
  if(this.nearBottom()){if(!this.intent.following){this.intent.following=true;this.changedIntent();}}
  else if(top<this.lastTop-1)this.pause();
  else if(!this.intent.following&&Math.abs(top-this.lastTop)>1)this.changedIntent();
  this.lastTop=top;this.remember();this.update();
 };
 private takeOver=()=>{if(this.intent?.entry)this.pause();};
 private wheel=(event:Event)=>{const delta=(event as WheelEvent).deltaY;if(delta<0||this.intent?.pending&&delta!==0)this.pause();};
 private keydown=(event:Event)=>{
  const e=event as KeyboardEvent,target=e.target as HTMLElement|null;
  if(target?.closest('input,textarea,[contenteditable="true"]'))return;
  if(['PageUp','Home','ArrowUp'].includes(e.key)||e.key===' '&&e.shiftKey)this.pause();
 };
 private touchStart=(event:Event)=>{this.touchY=(event as TouchEvent).touches[0]?.clientY;};
 private touchMove=(event:Event)=>{const y=(event as TouchEvent).touches[0]?.clientY;if(y!==undefined&&this.touchY!==undefined&&y>this.touchY+2)this.pause();this.touchY=y;};
 private listeners:[string,EventListener][]=[['scroll',this.scroll],['wheel',this.wheel],['keydown',this.keydown],['touchstart',this.touchStart],['touchmove',this.touchMove]];
 private attach(node:HTMLElement){
  this.node=node;this.lastTop=node.scrollTop;
  readingIntentChanged(node);this.releaseProtection=protectLayoutReading(node,()=>!!this.intent&&(this.intent.following||!!this.intent.pending||!this.intent.current()));
  for(const [name,listener]of this.listeners)node.addEventListener(name,listener,{passive:true});
  this.interactionRoot=node.parentElement??undefined;
  if(this.interactionRoot)for(const name of ['input','compositionstart','pointerdown'])this.interactionRoot.addEventListener(name,this.takeOver,{capture:true,passive:true});
  this.resize=new ResizeObserver(()=>this.update());this.resize.observe(node);this.observeChildren();
  this.mutation=new MutationObserver(()=>{this.observeChildren();this.update();});this.mutation.observe(node,{childList:true,characterData:true,subtree:true});
 }
 private observeChildren(){
  if(!this.node||!this.resize)return;const next=new Set([...this.node.children].slice(0,64));
  for(const child of this.children)if(!next.has(child))this.resize.unobserve(child);
  for(const child of next)if(!this.children.has(child))this.resize.observe(child);
  this.children=next;
 }
 private render(){
  if(!this.valid()||!this.intent)return;
  const node=this.node;if(!node?.isConnected){this.clear();return;}
  if(!node.clientHeight){this.publish({visible:false,top:0});return;}
  const intent=this.intent;
  const visibleTarget=(target:HTMLElement|undefined)=>target?.isConnected&&node.contains(target)&&target.getClientRects().length>0&&!target.closest('[inert]');
  if(!visibleTarget(intent.target)){
   const target=intent.resolveTarget?.()??(!intent.resolveTarget?[...node.querySelectorAll<HTMLElement>('[data-operation-id]')].find(row=>row.dataset.operationId===intent.operationId&&visibleTarget(row)):undefined);
   intent.target=visibleTarget(target)?target:undefined;
  }
  if(!intent.target){this.publish({visible:false,top:0});return;}
  if(intent.pending){const saved=intent.pending,rows=this.anchors().filter(row=>row.dataset.readingAnchor===saved.anchor);
   if(rows.length===1){node.scrollTop+=this.block(rows[0],saved.block).getBoundingClientRect().top-node.getBoundingClientRect().top-saved.offset;intent.pending=undefined;intent.entry=false;}
   else if(!intent.settled?.())return;
   else{intent.pending=undefined;intent.following=true;}
  }
  if(intent.following)node.scrollTop=node.scrollHeight;
  this.lastTop=node.scrollTop;intent.rendered=true;this.remember();
  const parent=node.parentElement,top=parent?node.getBoundingClientRect().bottom-parent.getBoundingClientRect().top-52:0;
  this.publish({visible:!this.nearBottom()&&node.clientHeight>=64,top});
 }
 private publish(value:LatestMessagePosition){if(this.published.visible===value.visible&&Math.abs(this.published.top-value.top)<.25)return;this.published=value;this.options.changed(value);}
}
