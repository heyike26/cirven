import type { ArtifactEnvelope, RendererContext, SourceRef } from './contracts';
import { deliveryComplete, deliveryFiles, deliverySource, type DeliveryAsset, type DeliveryFile, type DeliveryOperation } from './planning-delivery.ts';

export type ChatDeliveryFile = { file: DeliveryFile; source: SourceRef; filename: string; label?:string; diagramNumber?:number; kind: 'WORD' | 'DIAGRAM' | 'HARNESS' | 'PENDING' | 'FILE' };
const sourceKey = (source: SourceRef) => JSON.stringify([source.project_id, source.workspace_id, source.binding_revision, source.binding_sha256, source.path, source.revision, source.sha256]);
/** Asset-store paths and workspace copies are different objects. Link roles only
 * through a unique, exact copied report directory and its full workspace scope.
 * This is display metadata: unknown/legacy rows and all open authority survive. */
function deliveryDisplayRoles(value: DeliveryOperation, all: ChatDeliveryFile[]) {
  const roles=new Map<string,{internal:boolean;pending?:boolean;diagramNumber?:number}>();
  if(!deliveryComplete(value)||!Array.isArray(value.artifacts))return roles;
  const report=value.report as typeof value.report & {source_kind?:string;proposal_ref?:{id:string;revision:number;sha256:string}},basis=value.status==='STAGE_REPORT'?value.session_ref:value.proposal_ref;
  const reportBasis=value.status==='STAGE_REPORT'?report?.session_ref:report?.proposal_ref;
  if(!basis||!reportBasis||(['id','revision','sha256'] as const).some(k=>basis[k]!==reportBasis[k])||report.source_kind!==(value.status==='STAGE_REPORT'?'DISCOVERY':'PROPOSAL'))return roles;
  const descriptor=(asset:DeliveryAsset,expected:unknown)=>{
    const item=expected as Partial<DeliveryAsset>|undefined;
    return !!item&&(['filename','sha256','byte_length','download_url'] as const).every(k=>asset[k]===item[k]);
  };
  const safeName=(name:string)=>typeof name==='string'&&!!name&&!/[\\/\r\n]/.test(name)&&name!=='.'&&name!=='..';
  const raw=[...value.project_files,...value.harness?.files??[]];
  const exactSource=(item:ChatDeliveryFile)=>raw.filter(f=>f.path===item.file.path).every(file=>
    file.sha256===item.source.sha256&&[file.file_source_ref,file.source_ref].every(source=>source===undefined||deliverySource(source)&&sourceKey(source)===sourceKey(item.source)));
  const reportAssets=value.artifacts.filter(asset=>asset.kind==='docx'&&descriptor(asset,report.artifact)&&safeName(asset.filename));
  if(reportAssets.length!==1)return roles;
  const docx=reportAssets[0],tag=report.artifact.id?.slice(0,16);
  const copiedName=(asset:DeliveryAsset)=>value.status==='STAGE_REPORT'?asset.filename:
    ({docx:`taskbook-${tag}.docx`,pending_markdown:`report-pending-${tag}.md`,pending_json:`report-pending-${tag}.json`,source_snapshot:`report-source-${tag}.json`} as Record<string,string>)[asset.kind]??asset.filename;
  const anchors=all.filter(item=>{
    const segment=value.status==='STAGE_REPORT'?'discovery/sessions':'planning/proposals';
    const url=`/api/projects/${encodeURIComponent(item.source.project_id)}/${segment}/${encodeURIComponent(basis.id)}/reports/${report.artifact.id}/download`;
    if(!exactSource(item)||item.filename!==copiedName(docx)||item.source.sha256!==docx.sha256||item.file.byte_length!==docx.byte_length||docx.download_url!==url)return false;
    if(value.status==='STAGE_REPORT')return item.source.path.endsWith(`/stages/${basis.id}/r${basis.revision}-${tag}/${docx.filename}`);
    const workspace=value.harness?.workspace_ref;
    return !!workspace&&item.source.path===`${value.version_directory}/${copiedName(docx)}`&&item.source.workspace_id===workspace.id&&item.source.binding_revision===workspace.revision&&item.source.binding_sha256===workspace.binding_sha256;
  });
  if(anchors.length!==1)return roles;
  const anchor=anchors[0],directory=anchor.source.path.slice(0,anchor.source.path.lastIndexOf('/'));
  const sameScope=(source:SourceRef)=>(['project_id','workspace_id','binding_revision','binding_sha256'] as const).every(key=>source[key]===anchor.source[key]);
  for(const asset of value.artifacts){
    if(!safeName(asset.filename)||value.artifacts.filter(a=>a.filename===asset.filename&&a.sha256===asset.sha256&&a.byte_length===asset.byte_length).length!==1)continue;
    let internal=false,pending=false,diagramNumber:number|undefined;
    const reportKey=({docx:'artifact',pending_markdown:'pending',pending_json:'pending_json',source_snapshot:'source_snapshot'} as Record<string,'artifact'|'pending'|'pending_json'|'source_snapshot'>)[asset.kind];
    if(reportKey){
      if(!descriptor(asset,report[reportKey]))continue;
      internal=asset.kind==='pending_json'||asset.kind==='source_snapshot';pending=asset.kind==='pending_markdown';
    }else{
      const format=({diagram_svg:'svg',diagram_png:'png',diagram_json:'json'} as Record<string,'svg'|'png'|'json'>)[asset.kind];
      if(!format)continue;
      const diagramBasis=value.status==='STAGE_REPORT'?value.session_ref:value.discovery_ref;
      const matching=value.diagrams?.map((diagram,index)=>({diagram,index})).filter(({diagram})=>{
        const ref=diagram.session_ref,file=diagram.files[format];
        return typeof diagram.graph_id==='string'&&!!diagramBasis&&!!ref&&(['id','revision','sha256'] as const).every(k=>ref[k]===diagramBasis[k])&&!!file&&
          asset.filename===`diagram-${diagram.graph_id}.${format}`&&asset.sha256===file.sha256&&asset.byte_length===file.byte_length&&
          asset.download_url===`/api/projects/${encodeURIComponent(anchor.source.project_id)}/discovery/sessions/${encodeURIComponent(diagramBasis.id)}/diagrams/${encodeURIComponent(diagram.graph_id)}?revision=${diagramBasis.revision}&format=${format}`;
      });
      if(matching?.length!==1)continue;
      internal=format==='json';diagramNumber=matching[0].index+1;
    }
    const matches=all.filter(item=>exactSource(item)&&sameScope(item.source)&&item.source.path===`${directory}/${copiedName(asset)}`&&item.filename===copiedName(asset)&&item.source.sha256===asset.sha256&&item.file.byte_length===asset.byte_length);
    if(matches.length!==1)continue;
    const key=sourceKey(matches[0].source);
    // Conflicting duplicate roles are not evidence that a user file is internal.
    if(roles.has(key)){roles.delete(key);continue;}
    roles.set(key,{internal,pending,diagramNumber});
  }
  return roles;
}

export function chatDeliveryFiles(value: DeliveryOperation) {
  const unique = new Map<string, ChatDeliveryFile>();
  for (const file of deliveryFiles(value)) {
    const source = file.file_source_ref ?? file.source_ref;
    if (!deliverySource(source) || source.path !== file.path || source.sha256 !== file.sha256) continue;
    const filename = file.path.split(/[\\/]/).at(-1) || file.path;
    unique.set(sourceKey(source), { file, source, filename, kind: /\.docx$/i.test(filename) ? 'WORD' : /\.(png|svg)$/i.test(filename) ? 'DIAGRAM' : 'FILE' });
  }
  const all = [...unique.values()];
  const complete=deliveryComplete(value)?value:undefined;
  const report=complete?.report?.artifact;
  for(const item of all){
    // A friendly Word name comes only from the exact same content, never a title guess.
    if(item.kind==='WORD'&&report?.sha256===item.source.sha256&&typeof report.filename==='string'&&report.filename.trim()&&!/[\\/\r\n]/.test(report.filename))item.label=report.filename;
  }
  const harnessSources=new Set((complete?.harness?.files??[]).flatMap(file=>{
    const source=file.file_source_ref??file.source_ref;
    return deliverySource(source)&&source.path===file.path&&source.sha256===file.sha256?[sourceKey(source)]:[];
  }));
  const harness=all.find(item=>item.filename==='INDEX.md'&&harnessSources.has(sourceKey(item.source)));
  if(harness)harness.kind='HARNESS';
  const roles=deliveryDisplayRoles(value,all);
  for(const item of all){const role=roles.get(sourceKey(item.source));if(role?.pending)item.kind='PENDING';if(role?.diagramNumber)item.diagramNumber=role.diagramNumber;}
  const userFiles=all.filter(item=>!roles.get(sourceKey(item.source))?.internal);
  const primary: ChatDeliveryFile[] = [];
  const word = userFiles.find(item => item.kind === 'WORD');
  if (word) primary.push(word);
  const diagrams = userFiles.filter(item => item.kind === 'DIAGRAM').sort((a, b) => Number(/\.svg$/i.test(a.filename)) - Number(/\.svg$/i.test(b.filename)));
  const stems = new Set<string>();
  for (const item of diagrams) {
    const stem = `${item.source.workspace_id}:${item.source.path.replace(/\.(png|svg)$/i, '')}`;
    if (stems.has(stem)) continue;
    stems.add(stem);
    primary.push(item);
    if (primary.length >= (harness?2:3)) break;
  }
  if(harness)primary.push(harness);
  if (!primary.length && userFiles.length) primary.push(userFiles[0]);
  const selected = new Set(primary.map(item => sourceKey(item.source)));
  return { all, userFiles, primary, remaining: userFiles.filter(item => !selected.has(sourceKey(item.source))),
    missingSources: deliveryFiles(value).filter(file => !deliverySource(file.file_source_ref ?? file.source_ref)).length };
}
export async function openChatDeliveryFile(context: RendererContext, artifact: ArtifactEnvelope, value: DeliveryOperation, selected: ChatDeliveryFile,current=false) {
  if (artifact.type !== 'planning-delivery' || !deliveryComplete(value) || artifact.artifactId !== value.operation_id) throw new Error('文件没有绑定这条交付回答');
  const actual = chatDeliveryFiles(value).all.find(item => sourceKey(item.source) === sourceKey(selected.source));
  if (!actual || actual.source.project_id !== artifact.scope.projectId) throw new Error('文件不属于这条回答的实际交付来源');
  // The existing action re-reads the exact file/hash/version before Main.openDocument.
  return context.dispatch(current?'delivery.file.open-current':'delivery.file.open', artifact, { receipt: value, file: actual.file });
}
