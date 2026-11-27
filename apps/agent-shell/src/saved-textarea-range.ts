/** Textareas normalize line endings. Return offsets in the unchanged saved text. */
export function savedTextareaRange(source:string,buffer:string,start:number,end:number) {
  if(buffer!==source.replace(/\r\n?/g,'\n')||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>buffer.length)throw new Error('所选内容尚未与原文对应');
  let dom=0,begin=-1,finish=-1;
  for(let index=0;index<=source.length;) {
    if(dom===start)begin=index;
    if(dom===end){finish=index;break;}
    if(index===source.length)break;
    index+=source[index]==='\r'&&source[index+1]==='\n'?2:1;dom++;
  }
  if(begin<0||finish<0)throw new Error('所选内容尚未与原文对应');
  return {start:begin,end:finish};
}
