type ModelEntry = {id:string;name?:string};
const variant = /(?:[-_ ](?:ultrafast|fast|flex|latest|preview))$/i;
const natural = new Intl.Collator('en', {numeric:true,sensitivity:'base'});

function version(id:string){
  const match=id.match(/^(.*?)(\d+(?:\.\d+)*)(.*)$/);
  return match ? {family:match[1].toLowerCase(),number:match[2].split('.').map(Number),tail:match[3]} : undefined;
}
function compare(a:ModelEntry,b:ModelEntry){
  const left=version(a.id),right=version(b.id);
  const family=natural.compare(left?.family??a.id.toLowerCase(),right?.family??b.id.toLowerCase());
  if(family)return family;
  if(left&&right){
    for(let i=0;i<Math.max(left.number.length,right.number.length);i++){
      const delta=(right.number[i]??0)-(left.number[i]??0);
      if(delta)return delta;
    }
    const group=natural.compare(left.tail.replace(variant,''),right.tail.replace(variant,''));
    if(group)return group;
    const rank=(id:string)=>/ultrafast$/i.test(id)?3:/fast$/i.test(id)?1:/flex$/i.test(id)?2:/preview$/i.test(id)?4:0;
    const delta=rank(a.id)-rank(b.id);if(delta)return delta;
  }
  return natural.compare(a.name??a.id,b.name??b.id);
}

export function sortModelCatalog<T extends ModelEntry>(items:readonly T[]):T[]{return [...items].sort(compare);}
export function sortModelProfiles<T extends {model_id:string}>(items:readonly T[]):T[]{
  return [...items].sort((a,b)=>compare({id:a.model_id},{id:b.model_id}));
}
export function catalogSelection(items:readonly ModelEntry[],current:string,saved?:string,restoreSaved=false):string{
  if(items.some(item=>item.id===current))return current;
  if(current)return '';
  if(restoreSaved&&saved&&items.some(item=>item.id===saved))return saved;
  return '';
}

/** Existing connection identity is shown only when a model name is ambiguous.
 * Keep model/profile IDs untouched; never expose URL credentials, paths or query.
 */
export function modelProfileConnectionHint(
  profile:{model_id:string;provider_url:string;profile_ref:{id:string}},
  profiles:readonly {model_id:string}[],
):string|undefined{
  if(profiles.filter(item=>item.model_id===profile.model_id).length<2)return;
  let host='';
  try{host=new URL(profile.provider_url).host;}catch{/* Legacy invalid endpoints keep their connection ID only. */}
  return host?`${profile.profile_ref.id} · ${host}`:profile.profile_ref.id;
}
