import { api, operationId, type ApiClient } from './api.ts';
import type { FileContent, SourceRef, Workspace } from './contracts';
import type { NativeFilePicker } from './native-picker';
import { deliverySource } from './planning-delivery.ts';

export type CompanionFile = { file:FileContent;workspace:Workspace };
export type CompanionFileDraft = { source_ref:SourceRef;write_requested:boolean };
export type CompanionRoute = 'NATIVE_OBSERVE'|'NATIVE_FILE_OBSERVE'|'DIRECT_FILE'|'SELECT_FILE'|'STALE_FILE'|'RECORD_ONLY'|'MIXED_CONTEXT';
const sourceFields=['kind','project_id','workspace_id','binding_revision','binding_sha256','path','revision','sha256'] as const;
export function exactFileSource(a:SourceRef|undefined,b:SourceRef|undefined):boolean{return !!a&&!!b&&sourceFields.every(key=>a[key]===b[key]);}
export function sameSelectedFile(a:SourceRef|undefined,b:SourceRef|undefined):boolean{return !!a&&!!b&&a.project_id===b.project_id&&a.workspace_id===b.workspace_id&&a.path===b.path;}
export function directFileSupported(file:FileContent|undefined):boolean {
  return !!file&&file.encoding==='utf-8'&&file.byte_length<=1024*1024&&typeof file.content==='string'&&!file.content.includes('\0')&&!/\.(docx?|dotx?|pdf|xlsx?|pptx?|png|jpe?g|gif|webp|bmp|zip|exe)$/i.test(file.source_ref.path);
}
export function companionRoute(file:FileContent|undefined,write:boolean,hasNativeAttachments=false):CompanionRoute {
  if(!file)return write?'SELECT_FILE':'NATIVE_OBSERVE';
  if(!file.current)return 'STALE_FILE';
  // Typed screen inputs may accompany a read-only file observation. The direct
  // write adapter still requires its own single-file basis.
  if(hasNativeAttachments)return write?'MIXED_CONTEXT':'NATIVE_FILE_OBSERVE';
  if(!directFileSupported(file))return write?'RECORD_ONLY':'NATIVE_FILE_OBSERVE';
  return 'DIRECT_FILE';
}
export function selectedFilePath(path:string):{root:string;filename:string} {
  if(!path.startsWith('/')&&!/^[A-Za-z]:[\\/]/.test(path)&&!/^\\\\[^\\]+\\[^\\]+\\/.test(path))throw new Error('文件选择器必须返回实际本机绝对路径');
  const slash=Math.max(path.lastIndexOf('/'),path.lastIndexOf('\\')),filename=path.slice(slash+1);
  if(!filename||filename==='.'||filename==='..')throw new Error('请选择一个实际文件');
  const root=slash===0?'/':slash===2&&path[1]===':'?path.slice(0,3):path.slice(0,slash);
  return {root,filename};
}
export function selectedWriteAllowed(value:CompanionFile):boolean {
  const path=value.file.source_ref.path;
  return value.workspace.allowed_write_paths.some(scope=>scope==='*'||scope===path||scope.endsWith('/')&&path.startsWith(scope));
}
export function readCompanionFileDraft(storage:Pick<Storage,'getItem'>,pid:string):CompanionFileDraft|undefined {
  try{const value=JSON.parse(storage.getItem(`eeas-companion-file:${pid}`)??'null');return deliverySource(value?.source_ref)&&value.source_ref.project_id===pid&&typeof value.write_requested==='boolean'?value:undefined;}catch{return undefined;}
}
export function saveCompanionFileDraft(storage:Pick<Storage,'setItem'|'removeItem'>,pid:string,value:CompanionFileDraft|undefined){const key=`eeas-companion-file:${pid}`;if(value)storage.setItem(key,JSON.stringify(value));else storage.removeItem(key);}
export class CompanionFilesApi {
  private client:ApiClient;
  constructor(client:ApiClient=api){this.client=client;}
  private async binding(ref:SourceRef):Promise<Workspace>{const value=await this.client.request<Workspace>(this.client.workspacePath(ref.project_id,ref.workspace_id));if(value.project_id!==ref.project_id||value.id!==ref.workspace_id||value.revision!==ref.binding_revision||value.binding_sha256!==ref.binding_sha256)throw new Error('所选文件的目录绑定已变化，原有选择保留');return value;}
  async load(ref:SourceRef,requireCurrent=true):Promise<CompanionFile> {
    if(!deliverySource(ref))throw new Error('缺少实际文件来源，先选择文件');
    const workspace=await this.binding(ref),file=await this.client.request<FileContent>(this.client.workspacePath(ref.project_id,ref.workspace_id)+'/file?'+new URLSearchParams({path:ref.path,revision:String(ref.revision)}));
    if(!exactFileSource(file.source_ref,ref))throw new Error('文件来源版本不一致，原引用未更换');
    const latest=await this.client.file(ref.project_id,ref.workspace_id,ref.path),current=file.current&&latest.current&&exactFileSource(latest.source_ref,ref);
    if(requireCurrent&&!current)throw new Error('所选文件已变化，人工内容保留；先重新读取并核对当前版本');
    return {file:{...file,current},workspace};
  }
  async choose(pid:string,picker:NativeFilePicker):Promise<CompanionFile|undefined> {
    const picked=await picker.openFilePicker({multiple:false,title:'选择当前要处理的文件'});if(!picked)return;
    try{if(picked.files.length!==1)throw new Error('本轮请选择一个明确的文件');const location=selectedFilePath(picked.files[0].path),workspace=await this.client.bindWorkspace(pid,location.root,false),file=await this.client.file(pid,workspace.id,location.filename);if(!file.current||file.source_ref.project_id!==pid||file.source_ref.workspace_id!==workspace.id)throw new Error('所选文件来源已变化，请重新选择');return {file,workspace};}finally{if(picked.token&&picker.releasePickedFiles)await picker.releasePickedFiles(picked.token);}
  }
  async authorize(value:CompanionFile):Promise<CompanionFile> {
    if(!directFileSupported(value.file))throw new Error('当前文件修改仅支持 1 MiB 以内的 UTF-8 文本；反馈可以单独保存');
    const exact=await this.load(value.file.source_ref);
    if(selectedWriteAllowed(exact))return exact;
    const workspace=await this.client.request<Workspace>(`${this.client.projectPath(exact.file.source_ref.project_id)}/local-workspaces`,'POST',{idempotency_key:'single-file-'+operationId(),root:exact.workspace.root,allowed_write_paths:[exact.file.source_ref.path]});
    const file=await this.client.file(exact.file.source_ref.project_id,workspace.id,exact.file.source_ref.path);
    if(!file.current||file.source_ref.sha256!==exact.file.source_ref.sha256||file.source_ref.path!==exact.file.source_ref.path||file.source_ref.workspace_id!==workspace.id)throw new Error('授权期间文件发生变化；没有启动修改，先核对当前文件');
    return {file,workspace};
  }
  async refresh(value:CompanionFile):Promise<CompanionFile>{const workspace=await this.binding(value.file.source_ref),file=await this.client.file(value.file.source_ref.project_id,value.file.source_ref.workspace_id,value.file.source_ref.path);if(!file.current||file.source_ref.project_id!==workspace.project_id||file.source_ref.workspace_id!==workspace.id)throw new Error('当前文件来源尚未可核实');return {file,workspace};}
}
export const companionFilesApi=new CompanionFilesApi();
