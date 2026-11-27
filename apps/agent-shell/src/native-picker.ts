import type { ApiClient } from './api';
import type { SourceRef } from './contracts';
import { nativeCollaborationBridge } from './native-collaboration.ts';
import {AuthoredUIError} from './authored-ui-copy.ts';

export type NativeFilePicker = {
  openFilePicker: (options?: { multiple?: boolean; title?: string; extensions?: string[] }) => Promise<{ token?: string; files: { path: string; name: string; size?: number }[] } | null>;
  releasePickedFiles?: (token: string) => Promise<void>;
};
export type NativeDirectoryPicker = { openDirectoryPicker: (options?: { multiple?: boolean; title?: string; defaultPath?: string }) => Promise<string | string[] | null> };

export function nativeDirectoryPicker(host: unknown): NativeDirectoryPicker | undefined {
  const api = (host as { api?: Partial<NativeDirectoryPicker> } | undefined)?.api;
  if(api && typeof api.openDirectoryPicker === 'function')return api as NativeDirectoryPicker;
  const desktop=(host as {eeasDesktop?:{version?:number;chooseFolder?:()=>Promise<string|null>}}|undefined)?.eeasDesktop;
  return (desktop?.version===1||nativeCollaborationBridge(host)?.version===2)&&typeof desktop?.chooseFolder==='function'?{openDirectoryPicker:()=>desktop.chooseFolder!()}:undefined;
}

export function nativeFilePicker(host: unknown): NativeFilePicker | undefined {
  const api = (host as { api?: Partial<NativeFilePicker> } | undefined)?.api;
  if(api && typeof api.openFilePicker === 'function' && typeof api.releasePickedFiles === 'function')return api as NativeFilePicker;
  const desktop=(host as {eeasDesktop?:{version?:number;chooseFiles?:()=>Promise<string[]>}}|undefined)?.eeasDesktop;
  if((desktop?.version===1||nativeCollaborationBridge(host)?.version===2)&&typeof desktop?.chooseFiles==='function')return{openFilePicker:async()=>{const paths=await desktop.chooseFiles!();return paths.length?{files:paths.map(path=>({path,name:path.split(/[\\/]/).at(-1)??path}))}:null;}};
  return undefined;
}

export function splitTemplatePath(path: string): { root: string; filename: string } {
  return splitPickedPath(path,['docx','dotx']);
}
export function splitPickedPath(path: string,extensions:string[]): { root: string; filename: string } {
  if (!path.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(path) && !/^\\\\[^\\]+\\[^\\]+\\/.test(path)) throw new Error('文件选择器必须返回本机绝对路径');
  const separator = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  const filename = path.slice(separator + 1);
  if (!extensions.some(extension=>filename.toLowerCase().endsWith('.'+extension.toLowerCase()))) throw new AuthoredUIError('请选择 {formats} 文件',{formats:extensions.join(' / ').toUpperCase()});
  const root = separator === 0 ? '/' : separator === 2 && path[1] === ':' ? path.slice(0, 3) : path.slice(0, separator);
  return { root, filename };
}

export async function pickTemplateSource(projectId: string, client: Pick<ApiClient, 'bindWorkspace' | 'file'>, picker: NativeFilePicker): Promise<{ name: string; sourceRef: SourceRef } | undefined> {
  return pickLocalSource(projectId,client,picker,['docx','dotx'],'选择 Word 报告模板');
}
export async function pickLocalSource(projectId:string,client:Pick<ApiClient,'bindWorkspace'|'file'>,picker:NativeFilePicker,extensions:string[],title:string):Promise<{name:string;sourceRef:SourceRef}|undefined>{
  const picked = await picker.openFilePicker({ multiple: false, title, extensions });
  if (!picked) return;
  try {
    if (picked.files.length !== 1) throw new Error('请选择一份 Word 模板');
    const file = picked.files[0];
    const path = splitPickedPath(file.path,extensions);
    const binding = await client.bindWorkspace(projectId, path.root, false);
    const content = await client.file(projectId, binding.id, path.filename);
    if (content.source_ref.project_id !== projectId || content.source_ref.workspace_id !== binding.id || !content.current) throw new Error('模板来源已变化，请重新选择');
    return { name: file.name || path.filename, sourceRef: content.source_ref };
  } finally {
    if(picked.token&&picker.releasePickedFiles)await picker.releasePickedFiles(picked.token);
  }
}
