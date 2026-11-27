const MAX_FILES=3;
const MAX_BYTES=1024*1024;
export type ManualDraftFile={id:string;file:File};
export type ManualFileUpload={id:string;name:string;content_base64:string};
export type ManualFileReceipt={id:string;name:string;bytes:number;sha256:string};

function openDrafts():Promise<IDBDatabase>{
 return new Promise((resolve,reject)=>{
  const request=indexedDB.open('cirven-workflow-manual-draft-files',1);
  request.onupgradeneeded=()=>request.result.createObjectStore('files');
  request.onerror=()=>reject(request.error);
  request.onsuccess=()=>resolve(request.result);
 });
}

export async function manualDraftFiles(key:string,update?:(files:ManualDraftFile[])=>ManualDraftFile[]):Promise<ManualDraftFile[]>{
 const db=await openDrafts();
 try{return await new Promise((resolve,reject)=>{
  const transaction=db.transaction('files',update?'readwrite':'readonly',update?{durability:'strict'}:undefined);
  const store=transaction.objectStore('files'),request=store.get(key);
  let result:ManualDraftFile[];
  request.onsuccess=()=>{
   try{
    const current:ManualDraftFile[]=request.result??[];
    result=update?update(current):current;
    if(update)result.length?store.put(result,key):store.delete(key);
   }catch(error){transaction.abort();reject(error);}
  };
  transaction.oncomplete=()=>resolve(result);
  transaction.onabort=()=>reject(transaction.error);
 });}finally{db.close();}
}

export function mergeManualFiles(current:ManualDraftFile[],picked:ManualDraftFile[]):ManualDraftFile[]{
 const items=[...current,...picked];
 if(items.length>MAX_FILES||items.reduce((total,item)=>total+item.file.size,0)>MAX_BYTES)throw Error('limit');
 return items;
}

export async function prepareManualFiles(current:ManualDraftFile[],picked:File[]):Promise<ManualDraftFile[]>{
 if(picked.some(file=>!file.size||[...file.name].length>255||
  !file.name.trim()||file.name==='.'||file.name==='..'||/[\\/:\u0000-\u001f\u007f]/u.test(file.name)))throw Error('invalid');
 const draft=picked.map(file=>({id:crypto.randomUUID().replaceAll('-',''),file}));
 mergeManualFiles(current,draft);
 return Promise.all(draft.map(async ({id,file})=>({
  id,file:new File([await file.arrayBuffer()],file.name,{type:file.type,lastModified:file.lastModified})
 })));
}

export function encodeManualFile({id,file}:ManualDraftFile):Promise<ManualFileUpload>{
 return new Promise((resolve,reject)=>{
  const reader=new FileReader();
  reader.onerror=()=>reject(reader.error);
  reader.onload=()=>resolve({id,name:file.name,content_base64:String(reader.result).split(',',2)[1]});
  reader.readAsDataURL(file);
 });
}

export function manualFileSize(bytes:number):string{
 return bytes<1024?bytes+' B':new Intl.NumberFormat(undefined,{maximumFractionDigits:1}).format(bytes/1024)+' KiB';
}
