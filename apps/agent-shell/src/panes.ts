import {defaultConversationWidth} from './chat-workbench-layout';

export const PANE_MIN={files:220,agent:360} as const;
export type PaneWidthIntent={files:number;agent?:number};
export function savedPaneWidth(value:string|null,kind:'files'|'agent'){
 if(value===null||value.trim()==='')return undefined;
 const width=Number(value);
 return Number.isFinite(width)&&width>0?paneWidth(width,kind,Infinity):undefined;
}
export const READING_AGENT_WIDTH_KEY='eeas-agent-reading-width-v1';
export function projectPaneWidths(intent:PaneWidthIntent,viewport:number,presentation:'chat'|'reading'='chat',readingAgent?:number){
 const agent=presentation==='reading'?readingAgent??420:intent.agent??defaultConversationWidth(viewport,intent.files);
 return fitPaneWidths(intent.files,agent,viewport);
}
export function paneWidth(value:number,kind:'files'|'agent',viewport:number){const min=PANE_MIN[kind];const max=Math.max(min,Math.min(kind==='files'?440:960,viewport*(kind==='files'?.35:.72)));return Math.min(max,Math.max(min,Number.isFinite(value)?value:kind==='files'?236:600));}

export const WORKBENCH_MIN=320;
export function fitPaneWidths(files:number,agent:number,viewport:number){
 const widths={files:paneWidth(files,'files',viewport),agent:paneWidth(agent,'agent',viewport)};
 const minimum=PANE_MIN.files+PANE_MIN.agent;
 const available=Math.max(minimum,viewport-WORKBENCH_MIN-12);
 if(widths.files+widths.agent<=available)return widths;
 const extra=widths.files+widths.agent-minimum,remaining=available-minimum;
 const left=Math.floor((widths.files-PANE_MIN.files)*remaining/extra);
 return {files:PANE_MIN.files+left,agent:PANE_MIN.agent+Math.floor(remaining-left)};
}
