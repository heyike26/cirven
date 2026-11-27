import {createResource,Show} from 'solid-js';
import {t as uiText} from './ui-language';
import {authoredErrorCopy,authoredText} from './authored-ui-copy';
import type {ArtifactEnvelope,FileContent,RendererContext} from './contracts';
import BitmapFeedbackEditor from './BitmapFeedbackEditor';
import {boundImage} from './image-source';
export default function BitmapFile(props:{file:FileContent;artifact:ArtifactEnvelope;context:RendererContext}){
 const [image]=createResource(()=>props.file,async file=>{const bytes=Uint8Array.from(atob(file.content),character=>character.charCodeAt(0));const {mime}=await boundImage(bytes,file.source_ref.path,file.source_ref.sha256);const decoded=await createImageBitmap(new Blob([bytes],{type:mime}));try{if(decoded.width<1||decoded.height<1)throw new Error('图片无法解码成有效画面');}finally{decoded.close();}return`data:${mime};base64,${file.content}`;});
 return <section><h3>{props.file.source_ref.path}</h3><Show when={image()}>{url=><BitmapFeedbackEditor projectId={props.artifact.scope.projectId} url={url()} bitmap={{kind:'LOCAL_FILE',file_source_ref:props.file.source_ref,sha256:props.file.source_ref.sha256}} onSubmit={input=>props.context.dispatch('bitmap.file.feedback',props.artifact,input)}/>}</Show><Show when={image.error}><p role="alert" class="as-inline-error">{authoredText(authoredErrorCopy(image.error),uiText)}</p></Show></section>;
}
