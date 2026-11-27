import {deliveryFiles,type DeliveryReceipt} from './planning-delivery.ts';
import {chatDeliveryFiles} from './chat-artifacts.ts';

export type DeliveryReviewNotice = {message:string;params?:Record<string,number|string>};
export type DeliveryReviewIssue = {name:string;status:'STALE'|'MISSING'|'UNKNOWN';retained:boolean};
export function deliveryReview(receipt:DeliveryReceipt) {
  const files=deliveryFiles(receipt), labels=new Map(chatDeliveryFiles(receipt).all.map(file=>[file.source.path,file.kind==='HARNESS'?'项目任务入口':file.label??file.filename]));
  const statuses=new Map<string,Set<string>>();
  for(const check of receipt.file_checks??[]) {
    const values=statuses.get(check.path)??new Set<string>();values.add(check.status);statuses.set(check.path,values);
  }
  const status=(path:string)=>{
    const values=statuses.get(path),value=values?.size===1?[...values][0]:'UNKNOWN';
    return ['CURRENT','STALE','MISSING'].includes(value)?value:'UNKNOWN';
  };
  const issues:DeliveryReviewIssue[]=[],seen=new Set<string>();
  function inspect(path:string,name:string,retained:boolean) {
    if(seen.has(path))return;seen.add(path);
    const value=status(path);
    if(value!=='CURRENT')issues.push({name,status:value as DeliveryReviewIssue['status'],retained});
  }
  for(const file of files)inspect(file.path,labels.get(file.path)??file.path.split(/[\\/]/).at(-1)??file.path,false);
  const assetNames:Record<string,string>={docx:'报告留存副本',pending_markdown:'待确认事项留存副本',pending_json:'待确认事项记录',source_snapshot:'生成来源快照'};
  for(const file of receipt.artifacts??[])inspect(file.path,assetNames[file.kind]??file.filename,true);
  for(const path of statuses.keys())if(!seen.has(path))inspect(path,'其他核对项',true);
  const project=issues.filter(issue=>!issue.retained),retained=issues.filter(issue=>issue.retained);
  const changed=project.filter(issue=>issue.status!=='UNKNOWN').length,unknown=project.length-changed;
  const notices:DeliveryReviewNotice[]=[];
  if(changed)notices.push({message:'{count} 份交付文件已变化或缺失，原交付依据保留。',params:{count:changed}});
  if(unknown)notices.push({message:'{count} 份交付文件暂未能核对。',params:{count:unknown}});
  if(retained.length)notices.push({message:files.length&&!project.length?'可打开文件版本一致；{count} 份留存记录需要核对。':'另有 {count} 份留存记录需要核对。',params:{count:retained.length}});
  if(receipt.basis_current===false||receipt.current_review_status==='SOURCE_OR_VERSION_CHANGED')notices.push({message:'要求版本或来源需要核对，当前交付依据保持不变。'});
  if(receipt.template_source_current===false||receipt.current_review_status==='TEMPLATE_SOURCE_CHANGED')notices.push({message:'模板已变化，当前交付仍保留原模板依据。'});
  if(receipt.answer_view_current===false||receipt.current_review_status==='ANSWER_VIEW_CHANGED')notices.push({message:'确认内容已变化，当前交付仍保留原来的要求。'});
  if(!notices.length&&receipt.current_review_status!=='CURRENT')notices.push({message:'这份交付需要重新核对。'});
  return {notices,issues};
}
