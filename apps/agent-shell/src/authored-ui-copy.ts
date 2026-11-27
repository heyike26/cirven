/** Product-authored copy only. Raw Error.message stays compatible with existing
 * receipts and comparisons; translation happens at an explicit UI boundary.
 * This module has no locale state, model calls, storage or rendering dependency. */
export type AuthoredUICopy={message_key:string;params?:Record<string,string|number|AuthoredUICopy>};
export type UIErrorCopy=string|AuthoredUICopy;
export type UITranslator=(message:string,params?:Record<string,string|number>)=>string;
export function authoredCopy(message_key:string,params:AuthoredUICopy['params']={}):AuthoredUICopy{return {message_key,params};}
export function authoredText(value:UIErrorCopy,translate:UITranslator=(message,params={})=>message.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g,(marker,key)=>Object.hasOwn(params,key)?String(params[key]):marker)):string{
  if(typeof value==='string')return translate(value);
  return translate(value.message_key,Object.fromEntries(Object.entries(value.params??{}).map(([key,item])=>[key,typeof item==='object'?authoredText(item,translate):item])));
}
export class AuthoredUIError extends Error{
  readonly ui_copy:AuthoredUICopy;
  constructor(message_key:string,params:AuthoredUICopy['params']={}){const copy=authoredCopy(message_key,params);super(authoredText(copy));this.ui_copy=copy;}
}
export function authoredErrorCopy(value:unknown):UIErrorCopy{
  if(value instanceof AuthoredUIError)return value.ui_copy;
  if(value instanceof Error&&value.message.startsWith('SOURCE_DRIFT:'))return authoredCopy('文件已发生变化；请先核对最新内容，再继续操作。');
  if(value instanceof Error&&value.message.startsWith('EXPERIENCE_'))return authoredCopy('经验草稿或原操作记录尚需核查；当前输入和已确认结果保留，没有自动重发。');
  if(value instanceof Error&&value.message.startsWith('ORIGINAL_INDEX_')){
    const labels:Record<string,string>={ORIGINAL_INDEX_CAPACITY:'本机原输入索引已满；旧记录和草稿保留，尚未提交。',ORIGINAL_INDEX_UNAVAILABLE:'本机原输入索引暂不可读；原内容保留，没有按空列表覆盖。',ORIGINAL_INDEX_LOCK_UNAVAILABLE:'本机原输入的保存锁不可用；草稿保留，尚未提交。',ORIGINAL_INDEX_CONFLICT:'另一窗口更新了原输入索引；原记录和草稿保留，尚未提交。',ORIGINAL_INDEX_ORIGINAL_ONLY:'此原编号已保留，请核查原结果；没有再次发送。',ORIGINAL_INDEX_SAVE_UNCONFIRMED:'原输入索引的保存尚未核实；草稿保留，尚未提交。'};
    return authoredCopy(labels[value.message]??'原输入的范围或回执需要核对；原记录与草稿保留，没有重新发送。');
  }
  if(value instanceof Error&&value.message.startsWith('ORIGINAL_WORK_INPUT_'))return authoredCopy('原工作选择需要核对；当前草稿和原请求保留，没有自动发送。');
  if(value instanceof Error&&value.message.startsWith('NATIVE_FEEDBACK_DRAFT_'))return authoredCopy('反馈引用的本机保存或版本需要核对；当前引文、原请求和已确认结果保留。');
  if(value instanceof Error&&value.message.startsWith('NATIVE_REWORK_'))return authoredCopy('返工原输入索引或保存依据需要核查；原记录与当前输入保留，没有自动重发。');
  if(value instanceof Error&&value.message.startsWith('NATIVE_PREVIEW_'))return authoredCopy('预览的本机输入、来源或原回执尚需核查；当前输入和完整原请求保留，未自动重发。');
  if(value instanceof Error&&value.message.startsWith('NATIVE_CONFLICT_'))return authoredCopy('冲突处理的来源、输入或原回执需要核查；当前输入与原请求保留，未自动重发。');
  if(value instanceof Error&&value.message.startsWith('WORK_SOURCE_'))return authoredCopy('只读来源的选择或原结果尚未保存，请在对应要求中核查；当前输入保留。');
  if(value instanceof Error&&value.message.startsWith('WORK_RETEST_'))return authoredCopy('复测的本机选择或原记录尚未保存，请核查原编号；当前输入保留。');
  if(value instanceof Error&&value.message.startsWith('WORK_CASE_'))return authoredCopy('用例对应的本机记录尚未保存，请核查原选择；当前输入保留。');
  if(value instanceof Error&&value.message.startsWith('WORK_REQUIREMENT_'))return authoredCopy('关联选择尚未保存，请在对应要求中核对；原输入保留。');
  if(value instanceof Error&&value.message.startsWith('CONFIGURATION_DRAFT_'))return authoredCopy('配置草稿尚未保存，请在连接设置中核对；当前填写保留。');
  if(value instanceof Error&&['ORIGINAL_OWNER_SELECTION_REQUIRED','CONFIGURATION_ADOPTION_REQUIRED'].includes(value.message))return authoredCopy('请核查已有连接选择，当前输入与原连接保持不变。');
  if(value instanceof Error&&/^SOURCE_CONTEXT_(CHANGED|UNAVAILABLE|LEGACY|CAPACITY):/.test(value.message))return authoredCopy('所选来源的当前授权需要核对，本次请求已阻止；原聊天与草稿保留。');
  if(value instanceof Error){
    const state=/^EDITOR_CAPTURE_([A-Z_]{1,40}):/.exec(value.message)?.[1];
    if(state){
      const labels:Record<string,string>={DIRTY:'编辑窗口有未保存内容，请先保存或保留；原输入仍在。',
        WAITING:'编辑窗口尚未回应本次核对，原输入已保留。',
        CHANNEL_REQUIRED:'编辑窗口连接暂不可用，请重新连接原桌面 App；原内容保留。',
        CAPACITY:'窗口核对容量已满，请先处理原待核查记录。',
        UNAVAILABLE:'暂时无法确认编辑窗口状态，原输入已保留。',
        OWNER_ENDED:'原编辑窗口已经结束，请核查其保存状态保护。',
        STALE:'编辑窗口的核对依据已过期，原输入已保留。',
        EXPIRED:'本次窗口核对已过期，原输入已保留。',
        RESTARTED:'原服务已重启，请重新连接后核查编辑窗口。',
        MEMBERSHIP_CHANGED:'需要核对的编辑窗口已变化，原输入已保留。',
        TARGET_CHANGED:'原任务或权限已变化，请核查后继续。'};
      return authoredCopy(labels[state]??'暂时无法确认编辑窗口状态，原输入已保留。');
    }
  }
  if(value instanceof Error&&value.message.startsWith("Error invoking remote method 'eeas:")){
    const wrapped=value.message.length<=4096?/^Error invoking remote method 'eeas:[a-z0-9:_-]{1,100}': (?:Error: )?([\s\S]*)$/.exec(value.message):null;
    if(wrapped?.[1]==='请先配置模型连接')return authoredCopy('尚未选择模型。');
    return authoredCopy('暂时无法确认结果。');
  }
  return value instanceof Error?value.message:String(value);
}
