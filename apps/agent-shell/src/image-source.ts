export type ImageMime='image/png'|'image/jpeg';
export function imageMime(bytes:Uint8Array):ImageMime{
 if([137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value))return'image/png';
 if(bytes.length>=4&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return'image/jpeg';
 throw new Error('图片没有真实 PNG / JPEG 字节签名');
}
export async function boundImage(bytes:Uint8Array,path:string,expectedSha:string,declaredMime?:string){
 const mime=imageMime(bytes),extension=path.split('.').at(-1)?.toLowerCase();if((mime==='image/png'&&extension!=='png')||(mime==='image/jpeg'&&!['jpg','jpeg'].includes(extension??''))||(declaredMime!==undefined&&declaredMime!==mime))throw new Error('图片格式与扩展名或 MIME 不符');
 const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(value=>value.toString(16).padStart(2,'0')).join('');if(hash!==expectedSha)throw new Error('图片字节与来源 SHA 不符');return{mime,sha256:hash};
}
