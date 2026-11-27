import type {AgentSession,OriginalAgent} from './agent-sessions';

type ModeStorage=Pick<Storage,'getItem'|'setItem'>;
const valid=(value:unknown):value is OriginalAgent=>value==='plan'||value==='build'||value==='workflow';
const key=(id:string)=>{if(!id)throw Error('CONVERSATION_MODE_UNAVAILABLE');return 'eeas-conversation-agent-mode-v1:'+id;};

/** A preference belongs to the stable discussion, including imported original sessions.
 * The last actual user message is only a migration fallback, never an authorization. */
export function originalConversationMode(session:AgentSession|undefined,fallback:OriginalAgent):OriginalAgent{
 const user=session?.messages.filter(message=>message.info.role==='user').at(-1);
 if(valid(user?.info.agent))return user.info.agent;
 return valid(session?.work_mode_message?.agent)?session.work_mode_message.agent:fallback;
}
export function readConversationAgentMode(storage:ModeStorage,id:string,fallback:OriginalAgent):OriginalAgent{
 const saved=storage.getItem(key(id));
 if(saved===null)return fallback;
 if(!valid(saved))throw Error('CONVERSATION_MODE_UNAVAILABLE');
 return saved;
}
export function saveConversationAgentMode(storage:ModeStorage,id:string,mode:OriginalAgent):void{
 if(!valid(mode))throw Error('CONVERSATION_MODE_UNAVAILABLE');
 const name=key(id);storage.setItem(name,mode);
 if(storage.getItem(name)!==mode)throw Error('CONVERSATION_MODE_UNAVAILABLE');
}
