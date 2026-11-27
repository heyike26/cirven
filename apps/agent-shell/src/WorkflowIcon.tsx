import {Icon,type IconProps} from '@opencode-ai/ui/icon';
import type {StageKind} from './stage-workflows';

const names:Record<string,IconProps['name']> = {
  settings:'settings-gear', sliders:'sliders', pen:'pencil-line', activity:'status',
  code:'code', alert:'warning', arrow:'arrow-right', check:'check', file:'open-file',
  link:'link', list:'bullet-list', chevron:'chevron-down', play:'terminal-active',
  download:'download', layers:'file-tree', terminal:'terminal', close:'close', message:'comment',
  folder:'folder', branch:'branch', clipboard:'checklist', box:'archive', target:'task',
  plus:'plus', server:'server', back:'arrow-left', next:'chevron-right', history:'reset',
  shield:'shield', menu:'menu', edit:'edit', grid:'dot-grid', eye:'eye', search:'magnifying-glass'
};
export const stageGlyph:Record<StageKind,string> = {
  init:'target', requirements:'list', alternatives:'branch', cases:'clipboard', design:'layers',
  development:'code', testing:'activity', delivery:'box', custom:'file'
};
export function WorkflowIcon(p:{name:string;size?:number}){
  return <span class="wf-icon" aria-hidden="true" style={{'--wf-icon-size':(p.size??18)+'px'}}>
    <Icon name={names[p.name]??'file-tree'}/>
  </span>;
}
