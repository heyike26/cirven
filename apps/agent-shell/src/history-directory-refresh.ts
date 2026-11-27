import type {SupervisorSnapshot} from './global-supervisor';

/** Dirty hints only: the existing directory reader still owns data and errors. */
export class HistoryDirectoryRefresh {
  private closed=false;
  private visible=false;
  private visibilityKnown=false;
  private busy=false;
  private dirty=false;
  private queued=false;
  private reading=false;
  private taskKeys?:string;
  private readonly read:()=>Promise<unknown>;
  private readonly schedule:(run:()=>void)=>void;

  constructor(read:()=>Promise<unknown>,schedule:(run:()=>void)=>void=run=>globalThis.queueMicrotask(run)){this.read=read;this.schedule=schedule;}

  tasks(snapshot:Pick<SupervisorSnapshot,'tasks'>){
    if(this.closed)return;
    // Snapshot validation bounds this set. Ignore phase, timestamps and order.
    const keys=JSON.stringify([...new Set(snapshot.tasks.map(({key})=>JSON.stringify([
      key.kind,key.project_id??null,key.object_id,key.operation_id??null,key.generation,
    ])))].sort());
    if(this.taskKeys!==undefined&&keys!==this.taskKeys)this.invalidate();
    this.taskKeys=keys;
  }

  setVisible(value:boolean){
    if(this.closed)return;
    const reappeared=this.visibilityKnown&&!this.visible&&value;
    this.visibilityKnown=true;this.visible=value;
    if(reappeared)this.dirty=true;
    this.drain();
  }

  setBusy(value:boolean){if(!this.closed){this.busy=value;this.drain();}}
  invalidate(){if(!this.closed){this.dirty=true;this.drain();}}

  private drain(){
    if(this.closed||!this.visible||this.busy||this.reading||this.queued||!this.dirty)return;
    this.queued=true;
    this.schedule(()=>{
      this.queued=false;
      if(this.closed||!this.visible||this.busy||this.reading||!this.dirty)return;
      this.dirty=false;this.reading=true;
      void Promise.resolve().then(()=>{if(!this.closed)return this.read();}).catch(()=>{
        // The reader presents its own failure; no automatic retry on rejection.
      }).finally(()=>{this.reading=false;this.drain();});
    });
  }

  dispose(){this.closed=true;this.dirty=false;this.taskKeys=undefined;}
}
