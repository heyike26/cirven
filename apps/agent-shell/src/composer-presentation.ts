export type ComposerView={folded:boolean;focused:boolean};
/** Own presentation only. Draft text, annotations and source refs remain owned by App. */
export class ComposerPresentation{
 private current:ComposerView={folded:false,focused:false};private notify:(value:ComposerView)=>void;
 constructor(notify:(value:ComposerView)=>void){this.notify=notify;}
 snapshot(){return{...this.current};}
 fold(){this.current={folded:true,focused:false};this.notify(this.snapshot());}
 compose(){this.current={folded:false,focused:false};this.notify(this.snapshot());}
 focus(focused:boolean){this.current={...this.current,folded:focused?false:this.current.folded,focused};this.notify(this.snapshot());}
}
