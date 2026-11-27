import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import { createSignal, Show } from 'solid-js';
import { Button } from '@opencode-ai/ui/button';
import type { ArtifactEnvelope, FileContent, RendererContext } from './contracts';
import { nativeCollaborationBridge } from './native-collaboration';

/** Ordinary documents stay in their associated application. No HWND parenting,
 * resize observer, desktop workarea reservation or layout IPC is used here. */
export default function NativeDocument(props: { file: FileContent; artifact: ArtifactEnvelope; context: RendererContext; autoOpen?: boolean }) {
  const bridge = nativeCollaborationBridge(globalThis);
  const [pending,setPending] = createSignal(''), [error,setError] = createSignal<UIErrorCopy>(''), [receipt,setReceipt] = createSignal<unknown>();
  async function perform(label: string, operation: () => Promise<unknown>) {
    if (pending()) return;
    setPending(label); setError('');
    try { await operation(); } catch(error) { setError(authoredErrorCopy(error)); }
    finally { setPending(''); }
  }
  async function open() {
    if (!bridge?.openDocument) throw new Error('原生文件打开接口尚未连接');
    if (!props.file.current) throw new Error('原件已变化，请先读取最新版本');
    setReceipt(await bridge.openDocument(props.artifact.scope.projectId,props.file.source_ref));
  }
  async function download() {
    const bytes = props.file.encoding === 'base64' ? Uint8Array.from(atob(props.file.content),character=>character.charCodeAt(0)) : new TextEncoder().encode(props.file.content);
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(value=>value.toString(16).padStart(2,'0')).join('');
    if (hash !== props.file.source_ref.sha256) throw new Error('文件字节与来源版本不符，请重新读取');
    const url = URL.createObjectURL(new Blob([bytes]));
    const link = document.createElement('a'); link.href=url;link.download=props.file.source_ref.path.split(/[\\/]/).at(-1)??'document';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section class="as-native-document"><h3>{props.file.source_ref.path.split(/[\\/]/).at(-1)}</h3>
    <div class="as-proposal-toolbar"><Show when={bridge?.openDocument}><Button size="small" variant="primary" disabled={!!pending()||!props.file.current} onClick={()=>void perform('打开原软件',open)}>{uiText("在原软件打开")}</Button></Show><Button size="small" variant="secondary" disabled={!!pending()} onClick={()=>void perform('下载原文件',download)}>{uiText("下载原文件")}</Button><Button size="small" variant="ghost" disabled={!!pending()} onClick={()=>void perform('读取最新原件',()=>props.context.dispatch('workspace.file.refresh',props.artifact,{}))}>{uiText("读取最新原件")}</Button></div>
    <p class="as-muted">{bridge?uiText("在系统关联的软件中阅读和编辑；从原生页面唤起轻量协作后可反馈。"):uiText("可下载此实际文件，在电脑上的软件中阅读和编辑。")}</p>
    <Show when={!props.file.current}><p class="as-inline-warning">{uiText("磁盘原件已变化，当前文件是历史版本。请读取最新原件后继续。")}</p></Show>
    <details><summary>{uiText("文件来源与版本")}</summary><pre class="as-json">{JSON.stringify(props.file.source_ref,null,2)}</pre></details>
    <Show when={receipt()}><details open><summary>{uiText("原生打开回执")}</summary><pre class="as-json">{JSON.stringify(receipt(),null,2)}</pre></details></Show>
    <Show when={error()}><p role="alert" class="as-inline-error">{authoredText(error(),uiText)}</p></Show>
  </section>;
}
