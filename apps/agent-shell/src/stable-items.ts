import {createMemo,type Accessor} from 'solid-js';
/** Stable render identities with reactive current values. The editable source
 * stays ordinary JSON; only the read-only projection uses proxies, so changing a
 * character does not replace its focused input's whole For/Show subtree. */
export function stableItems<T extends object>(read:Accessor<T[]>,id:(value:T)=>string):Accessor<T[]>{
 const values=createMemo(()=>new Map(read().map(value=>[id(value),value]))),cache=new Map<string,T>();
 return createMemo(()=>[...values().keys()].map(key=>{let proxy=cache.get(key);if(!proxy){proxy=new Proxy({} as T,{get:(_target,field)=>Reflect.get(values().get(key)??{},field),has:(_target,field)=>field in (values().get(key)??{}),ownKeys:()=>Reflect.ownKeys(values().get(key)??{}),getOwnPropertyDescriptor:(_target,field)=>({configurable:true,enumerable:true,value:Reflect.get(values().get(key)??{},field)})});cache.set(key,proxy);}return proxy;}));
}
