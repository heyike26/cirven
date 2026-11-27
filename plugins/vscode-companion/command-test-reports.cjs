'use strict';
const tools=require('./session-tool.cjs'),wire=require('./test-report-wire.cjs');
const METHODS=Object.freeze(['session.tool.reports','session.tool.report']);
class CommandTestReports{
 constructor(host,scopeOf,safeCode){this.host=host;this.scopeOf=scopeOf;this.safeCode=safeCode;this.sequence=0;this.reset();}
 reset(){this.sequence++;this.owner=null;this.catalog=null;this.detail=null;this.loading=null;this.error=null;}
 supported(){return METHODS.every(method=>this.host.client?.methods?.includes(method));}
 scope(){return this.host.selected?.session_ref?this.scopeOf(this.host.selected):null;}
 valid(token){const scope=this.scope();return scope&&this.supported()&&tools.find(this.host.selected,scope,token);}
 snapshot(){if(this.owner&&!this.valid(this.owner))this.reset();return {supported:this.supported(),owner:this.owner,loading:this.loading,error:this.error,catalog:this.catalog?{sources:this.catalog.sources.map(source=>({...source,token:wire.sourceToken(this.catalog,source)})),command_status:this.catalog.command?.status??null,command_exit_code:this.catalog.command?.exit_code??null}:null,detail:this.detail};}
 async sources(token){const reference=this.valid(token);wire.check(reference);const h=this.host,scope=this.scope(),epoch=h.epoch,sequence=++this.sequence;
  this.owner=token;this.catalog=null;this.detail=null;this.error=null;this.loading='SOURCES';h.emit();
  const current=()=>h.epoch===epoch&&this.sequence===sequence&&this.owner===token&&this.valid(token);
  try{const catalog=wire.validateSources(await h.read('session.tool.reports',scope,{tool_ref:reference}),scope,reference);if(current())this.catalog=catalog;}
  catch(error){if(current())this.error=this.safeCode(error);}
  finally{if(current()){this.loading=null;h.emit();}}
 }
 async page(token,offset=0,outcome='ALL'){
  const h=this.host,catalog=this.catalog,owner=this.owner,scope=this.scope(),source=catalog?.sources.find(row=>wire.sourceToken(catalog,row)===token);
  wire.check(this.valid(owner)&&source&&wire.count(offset)&&wire.FILTERS.includes(outcome));
  const epoch=h.epoch,sequence=++this.sequence,previous=this.detail?.source_token===token?this.detail.report.report_ref:undefined;
  this.loading=token;this.error=null;h.emit();const current=()=>h.epoch===epoch&&sequence===this.sequence&&this.owner===owner&&this.catalog===catalog&&this.valid(owner);
  try{const options={offset,outcome,expectedRef:previous},payload={tool_ref:catalog.tool_ref,source_ref:{catalog_sha256:catalog.catalog_sha256,path:source.path,sha256:source.sha256},offset,outcome,...(previous?{report_ref:previous}:{})};
   const result=await h.read('session.tool.report',scope,payload);
   wire.check(result&&wire.same(result.scope,scope)&&wire.same(result.tool_ref,catalog.tool_ref)&&result.catalog_sha256===catalog.catalog_sha256);
   const report=wire.validateReport(result.report,catalog,source,options);if(current())this.detail={source_token:token,report};
  }catch(error){if(current())this.error=this.safeCode(error);}
  finally{if(current()){this.loading=null;h.emit();}}
 }
}
module.exports={CommandTestReports,METHODS};
