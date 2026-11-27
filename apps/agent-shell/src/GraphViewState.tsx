import {useDraftSession} from './ObjectDraft';
import {graphView,type GraphView} from './graph-view';
export function useGraphView(id:string){const owner=useDraftSession();return {
 ready:()=>{owner?.version();return !owner?.session||owner.session.status!=='loading';},
 read:()=>{owner?.version();return graphView((owner?.session?.view.graphs as Record<string,unknown>|undefined)?.[id]);},
 write:(value:GraphView)=>{const session=owner?.session;if(!session)return;session.set('view','graphs',{...(session.view.graphs as object??{}),[id]:value});}
};}
