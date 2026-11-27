'use strict';

// Electron window-local DIP geometry. Physical mouse coordinates must be
// converted by the native host before calling containsScreenPoint.
const rect=value=>value&&['x','y','width','height'].every(key=>Number.isSafeInteger(value[key]))&&value.width>0&&value.height>0;
const inside=(point,box)=>point.x>=box.x&&point.y>=box.y&&point.x<box.x+box.width&&point.y<box.y+box.height;
function union(values){
  const x=Math.min(...values.map(r=>r.x)),y=Math.min(...values.map(r=>r.y));
  return {x,y,width:Math.max(...values.map(r=>r.x+r.width))-x,height:Math.max(...values.map(r=>r.y+r.height))-y};
}
function distance(point,a,b){
  const dx=b.x-a.x,dy=b.y-a.y,length=dx*dx+dy*dy;
  const t=length?Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/length)):0;
  return Math.hypot(point.x-a.x-t*dx,point.y-a.y-t*dy);
}
function stableOrbGeometry({bubble,area,arc,notice}){
  if(!rect(bubble)||!rect(area)||bubble.width!==56||bubble.height!==56)throw Error('Invalid bounded orb geometry');
  const candidates=[bubble,...arc?[arc.bounds]:[],...notice?[notice.bounds]:[]];
  if(candidates.some(box=>!rect(box)||box.x<area.x||box.y<area.y||box.x+box.width>area.x+area.width||box.y+box.height>area.y+area.height))throw Error('Orb geometry exceeds its work area');
  const bounds=union(candidates),local=box=>({x:box.x-bounds.x,y:box.y-bounds.y,width:box.width,height:box.height});
  const center={x:bubble.x+28,y:bubble.y+28},orb=local(bubble);
  const actions=(arc?.offsets??[]).map(offset=>({x:center.x+offset.dx,y:center.y+offset.dy}));
  const circle=(cx,cy,radius)=>{
    const result=[],top=Math.max(bounds.y,Math.floor(cy-radius)),bottom=Math.min(bounds.y+bounds.height,Math.ceil(cy+radius));
    for(let y=top;y<bottom;y+=2){
      const height=Math.min(2,bottom-y),dy=Math.max(Math.abs(y-cy),Math.abs(y+height-cy));
      const span=Math.sqrt(Math.max(0,radius*radius-dy*dy));
      const x=Math.max(bounds.x,Math.floor(cx-span)),right=Math.min(bounds.x+bounds.width,Math.ceil(cx+span));
      if(right>x)result.push(local({x,y,width:right-x,height}));
    }
    return result;
  };
  // Keep a small shadow allowance. Disconnected menu regions leave the large
  // empty envelope click-through; the center region never changes on toggles.
  const centerShape=circle(center.x,center.y,28),expanded=[...centerShape,...actions.flatMap(point=>circle(point.x,point.y,31))];
  const noticeRects=notice?[...centerShape,local({x:notice.bounds.x+notice.panel.x,y:notice.bounds.y+notice.panel.y,width:notice.panel.width,height:notice.panel.height})]:centerShape;
  const unique=values=>[...new Map(values.filter(rect).map(value=>[JSON.stringify(value),value])).values()];
  const shape={collapsed:centerShape,expanded:unique(expanded),notice:unique(noticeRects)};
  const contains=(point,{expanded=false,noticeVisible=false,corridor=false}={})=>{
    if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.y))return false;
    if(Math.hypot(point.x-center.x,point.y-center.y)<=28)return true;
    if(noticeVisible&&notice&&inside(point,{x:notice.bounds.x+notice.panel.x,y:notice.bounds.y+notice.panel.y,width:notice.panel.width,height:notice.panel.height}))return true;
    if(!expanded)return false;
    if(actions.some(target=>Math.hypot(point.x-target.x,point.y-target.y)<=31))return true;
    return corridor&&(actions.some(target=>distance(point,center,target)<=16)||actions.some((target,index)=>index&&distance(point,actions[index-1],target)<=20));
  };
  return {bounds,orb,center:{x:center.x-bounds.x,y:center.y-bounds.y},shape,contains};
}
module.exports={stableOrbGeometry};
