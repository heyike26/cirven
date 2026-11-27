'use strict';
const {exactKeys}=require('./ipc-policy.cjs');
const {quickRef,quickRegions}=require('./quick-journal.cjs');
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
function inputAttachments(value){
 if(value===undefined)return [];
 if(!Array.isArray(value)||value.length>20)throw Error('At most twenty explicit page inputs are supported');
 const items=value.map(item=>{
  exactKeys(item,['kind','context_ref','label','regions',...['image_sha256','user_note'].filter(key=>Object.hasOwn(item??{},key))]);
  if(!['CURRENT_PAGE','SCREENSHOT'].includes(item.kind)||typeof item.label!=='string'||!item.label.trim()||Array.from(item.label).length>500)throw Error('A bounded explicit input label is required');
  const context_ref=quickRef(item.context_ref),regions=quickRegions(item.regions);
  if(regions.length>32)throw Error('At most thirty-two regions belong to one screenshot');
  if(item.kind==='SCREENSHOT'&&!hash(item.image_sha256)||item.kind==='CURRENT_PAGE'&&(regions.length||item.image_sha256!=null))throw Error('Regions and image hashes must bind an explicitly selected screenshot');
  if(item.user_note!=null&&(typeof item.user_note!=='string'||!item.user_note.trim()||Array.from(item.user_note).length>16000))throw Error('A bounded original attachment note is required');
  return {kind:item.kind,context_ref,label:item.label,regions,...item.image_sha256!=null?{image_sha256:item.image_sha256}:{},...item.user_note!=null?{user_note:item.user_note}:{}};
 });
 if(Buffer.byteLength(JSON.stringify(items),'utf8')>96*1024)throw Error('Selected input metadata exceeds the shared bound');
 return items;
}
async function inputPreview(body,{request,imageReader}){
 exactKeys(body,['contextRef','imageSha256','view']);
 const reference=quickRef(body.contextRef);
 if(!hash(body.imageSha256)||!['THUMBNAIL','FULL'].includes(body.view))throw Error('An exact screenshot preview is required');
 if(body.view==='THUMBNAIL'){
  const value=await request('/api/desktop-host/context/thumbnail',{context_ref:reference});
  if(!value||['id','revision','sha256'].some(key=>value.context_ref?.[key]!==reference[key])||value.image_sha256!==body.imageSha256||value.automatic_input_authorized!==false||typeof value.data_url!=='string'||value.data_url.length>720000||!/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(value.data_url))throw Error('Screenshot thumbnail differs from its selected source');
  return value;
 }
 const snapshot=await request('/api/desktop-host/context/frozen',{context_ref:reference});
 const image=snapshot?.preview?.image_ref;
 if(['id','revision','sha256'].some(key=>snapshot?.observation?.context_ref?.[key]!==reference[key])||snapshot.current!==false||snapshot.automatic_input_authorized!==false||image?.sha256!==body.imageSha256||typeof image.download_url!=='string'||!/^\/api\/desktop-host\/context\/frozen-images\/[A-Za-z0-9_-]{1,100}\?revision=[1-9][0-9]*&sha256=[a-f0-9]{64}$/.test(image.download_url))throw Error('Saved screenshot preview does not match its exact input');
 snapshot.preview.data_url=await imageReader(image.download_url,image);delete image.download_url;
 return snapshot;
}
module.exports={inputAttachments,inputPreview};
