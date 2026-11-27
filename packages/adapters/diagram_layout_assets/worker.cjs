'use strict';
// Fixed local layout computation. No HTTP, dynamic script, file path or options
// from the caller are accepted. Runtime is the already bundled Electron Node.
const crypto=require('node:crypto');
const ELK=require('./elk.bundled.js');
const MAX_INPUT=4*1024*1024,MAX_OUTPUT=8*1024*1024;
const ID=/^[A-Za-z0-9_-]{1,100}$/;
const fail=()=>{process.stderr.write('DIAGRAM_LAYOUT_FAILED\n');process.exitCode=2;};
function check(v){if(!v)throw Error('LAYOUT_INPUT');}
const options=Object.freeze({
 'elk.algorithm':'layered','elk.direction':'DOWN','elk.edgeRouting':'ORTHOGONAL',
 'elk.spacing.nodeNode':'28','elk.layered.spacing.nodeNodeBetweenLayers':'32',
 'elk.spacing.edgeNode':'12','elk.spacing.edgeLabel':'6','elk.spacing.labelNode':'8',
 'elk.spacing.labelLabel':'8','elk.padding':'[top=24,left=24,bottom=24,right=24]',
 'elk.randomSeed':'1','elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES',
 'elk.layered.mergeEdges':'false'
});
let size=0,parts=[];
process.stdin.on('data',part=>{size+=part.length;if(size>MAX_INPUT){parts=[];process.stdin.destroy();fail();}else parts.push(part);});
process.stdin.on('error',fail);
process.stdin.on('end',async()=>{
 try{
  check(size>0&&size<=MAX_INPUT);const raw=Buffer.concat(parts),value=JSON.parse(raw.toString('utf8'));parts=[];
  check(value&&Object.keys(value).sort().join(',')==='edges,nodes,schema_version'&&value.schema_version===1);
  check(Array.isArray(value.nodes)&&value.nodes.length>0&&value.nodes.length<=2000&&Array.isArray(value.edges)&&value.edges.length<=8000);
  const ids=new Set(),edgeIds=new Set();
  const number=v=>typeof v==='number'&&Number.isFinite(v)&&v>0&&v<=10000;
  const nodes=value.nodes.map(n=>{check(n&&Object.keys(n).sort().join(',')==='height,id,width'&&ID.test(n.id)&&!ids.has(n.id)&&number(n.width)&&number(n.height));ids.add(n.id);return{id:n.id,width:n.width,height:n.height};});
  const edges=value.edges.map(e=>{check(e&&Object.keys(e).sort().join(',')==='height,id,source,target,width'&&ID.test(e.id)&&!edgeIds.has(e.id)&&!ids.has(e.id)&&ids.has(e.source)&&ids.has(e.target)&&number(e.width)&&number(e.height));edgeIds.add(e.id);return{id:e.id,sources:[e.source],targets:[e.target],labels:[{id:'$label:'+e.id,width:e.width,height:e.height,text:e.id}]};});
  const elk=new ELK({algorithms:['layered']});
  const out=await elk.layout({id:'$root',layoutOptions:options,children:nodes,edges});
  const result={schema_version:1,input_sha256:crypto.createHash('sha256').update(raw).digest('hex'),engine:'elkjs@0.12.0',direction:'DOWN',width:out.width,height:out.height,
   nodes:out.children.map(n=>({id:n.id,x:n.x,y:n.y,width:n.width,height:n.height})),
   edges:out.edges.map(e=>({id:e.id,source:e.sources[0],target:e.targets[0],labels:e.labels.map(l=>({id:l.id,x:l.x,y:l.y,width:l.width,height:l.height})),sections:(e.sections||[]).map(s=>({start:s.startPoint,bends:s.bendPoints||[],end:s.endPoint}))}))};
  const encoded=JSON.stringify(result);check(Buffer.byteLength(encoded)<=MAX_OUTPUT);process.stdout.write(encoded);
 }catch{fail();}
});
