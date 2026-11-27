'use strict';
const {cssBounds,exactKeys}=require('./ipc-policy.cjs');
const PRESENTATIONS=new Set(['DESKTOP','MODELESS_POPOVER','FULL_WORKSPACE','DOCUMENT','APPLICATION']);
function layoutInput(value){
 exactKeys(value,['surface_ref','expected_input_epoch','bounds','presentation','popover_bounds']);
 const ref=value.surface_ref;
 exactKeys(ref,['id','revision','sha256']);
 if(!/^[a-f0-9]{64}$/.test(ref.id)||!Number.isSafeInteger(ref.revision)||ref.revision<1||!/^[a-f0-9]{64}$/.test(ref.sha256))throw new Error('Invalid desktop surface reference');
 if(!Number.isSafeInteger(value.expected_input_epoch)||value.expected_input_epoch<0||!PRESENTATIONS.has(value.presentation)||!Array.isArray(value.popover_bounds)||value.popover_bounds.length>16)throw new Error('Invalid desktop presentation');
 return {surface_ref:{...ref},expected_input_epoch:value.expected_input_epoch,bounds:cssBounds(value.bounds),presentation:value.presentation,popover_bounds:value.popover_bounds.map(cssBounds)};
}
function rectangle(value,content,zoom){
 const b=cssBounds(value);
 if(!Array.isArray(content)||content.length!==2||content.some(v=>!Number.isInteger(v)||v<1)||!Number.isFinite(zoom)||zoom<.1||zoom>10)throw new Error('Invalid owning window geometry');
 const left=Math.floor(b.x*zoom),top=Math.floor(b.y*zoom),right=Math.ceil((b.x+b.width)*zoom),bottom=Math.ceil((b.y+b.height)*zoom);
 if(right>content[0]||bottom>content[1]||right<=left||bottom<=top)throw new Error('Desktop surface exceeds the owning window');
 return {x:left,y:top,width:right-left,height:bottom-top};
}
function desktopShape(bounds,presentation,popovers,content,zoom=1){
 if(!PRESENTATIONS.has(presentation)||!Array.isArray(popovers)||popovers.length>16)throw new Error('Invalid desktop presentation');
 const hole=rectangle(bounds,content,zoom),[width,height]=content;
 // Shape coordinates are Electron window DIPs, not renderer CSS or desktop pixels.
 const inset={LEFT:hole.x,RIGHT:width-hole.x-hole.width,TOP:hole.y,BOTTOM:height-hole.y-hole.height};
 if(['FULL_WORKSPACE','DOCUMENT','APPLICATION'].includes(presentation))return {rectangles:[{x:0,y:0,width,height}],workarea_insets:inset,workspace_bounds:hole,input_blocked:presentation==='FULL_WORKSPACE'};
 const rectangles=[{x:0,y:0,width,height:hole.y},{x:0,y:hole.y+hole.height,width,height:height-hole.y-hole.height},{x:0,y:hole.y,width:hole.x,height:hole.height},{x:hole.x+hole.width,y:hole.y,width:width-hole.x-hole.width,height:hole.height}].filter(r=>r.width&&r.height);
 for(const raw of popovers){const r=rectangle(raw,content,zoom);const x=Math.max(r.x,hole.x),y=Math.max(r.y,hole.y),right=Math.min(r.x+r.width,hole.x+hole.width),bottom=Math.min(r.y+r.height,hole.y+hole.height);if(right>x&&bottom>y)rectangles.push({x,y,width:right-x,height:bottom-y});}
 // Electron [] restores a rectangular window. An all-desktop empty chrome
 // therefore keeps one deliberate edge pixel rather than accidentally blocking it.
 if(!rectangles.length)rectangles.push({x:0,y:0,width:1,height:1});
 return {rectangles,workarea_insets:inset,workspace_bounds:hole,input_blocked:false};
}
module.exports={layoutInput,rectangle,desktopShape};
