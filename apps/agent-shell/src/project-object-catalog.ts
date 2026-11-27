import type {TaskBook,Harness,ReportArtifact,PlanningProposal} from './contracts';
import type {DiscoverySession} from './discovery-contracts';

export type ProjectObjects={books:TaskBook[];harnesses:Harness[];reports:ReportArtifact[];proposals:PlanningProposal[];sessions:DiscoverySession[]};
export type CatalogField=keyof ProjectObjects;
export type ProjectCatalogStatus={projectId?:string;phase:'idle'|'loading'|'ready'|'partial'|'failed';failed:CatalogField[]};
export type ProjectCatalogReaders={[K in CatalogField]:(projectId:string,signal:AbortSignal)=>Promise<ProjectObjects[K]>};
const fields:CatalogField[]=['books','harnesses','reports','proposals','sessions'];

/** Project metadata has its own lifetime; changing the open document does not cancel it. */
export class ProjectObjectCatalog {
  private projectId?:string;
  private generation=0;
  private controller?:AbortController;
  private failed:CatalogField[]=[];
  private stopped=false;
  private readers:ProjectCatalogReaders;
  private owns:(projectId:string)=>boolean;
  private reset:()=>void;
  private patch:(value:Partial<ProjectObjects>)=>void;
  private status:(value:ProjectCatalogStatus)=>void;
  private snapshot?:()=>ProjectObjects;
  constructor(readers:ProjectCatalogReaders,owns:(projectId:string)=>boolean,
    reset:()=>void,patch:(value:Partial<ProjectObjects>)=>void,status:(value:ProjectCatalogStatus)=>void,snapshot?:()=>ProjectObjects){
    this.readers=readers;this.owns=owns;this.reset=reset;this.patch=patch;this.status=status;
    this.snapshot=snapshot;
  }
  activate(projectId:string|undefined){
    if(this.stopped||projectId===this.projectId)return;
    this.controller?.abort();this.controller=undefined;this.generation++;
    this.projectId=projectId;this.failed=[];this.reset();
    this.status({projectId,phase:projectId?'loading':'idle',failed:[]});
  }
  async load(projectId:string,retryFailedOnly=false):Promise<void>{
    if(this.stopped||!this.owns(projectId))return;
    this.activate(projectId);
    const requested=retryFailedOnly?[...this.failed]:[...fields];
    if(!requested.length)return;
    const generation=++this.generation;
    this.controller?.abort();const controller=new AbortController();this.controller=controller;
    const before=this.snapshot?.();
    this.status({projectId,phase:'loading',failed:[...this.failed]});
    const results=await Promise.allSettled(requested.map(async key=>this.readers[key](projectId,controller.signal)));
    if(this.stopped||controller.signal.aborted||generation!==this.generation||this.projectId!==projectId||!this.owns(projectId))return;
    const patch:Partial<ProjectObjects>={};const failed:CatalogField[]=[];const current=this.snapshot?.();
    results.forEach((result,index)=>{
      const key=requested[index];
      if(result.status==='fulfilled'){
        // A save completed after this read began. Keep that newer local category;
        // merging by title/id could undo a deletion or restore an older revision.
        if(!before||!current||current[key]===before[key])Object.assign(patch,{[key]:result.value});
      }
      else failed.push(key);
    });
    // Failed categories retain the current project's data, including newer local results.
    this.patch(patch);this.failed=failed;this.controller=undefined;
    this.status({projectId,phase:failed.length===requested.length?'failed':failed.length?'partial':'ready',failed:[...failed]});
  }
  retry(projectId:string){return this.load(projectId,true);}
  stop(){this.stopped=true;this.generation++;this.controller?.abort();this.controller=undefined;}
}
