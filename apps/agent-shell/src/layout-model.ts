import {visiblePanels,type PanelPresentation} from './presentation';

export type WorkspaceLayoutState=PanelPresentation&{singlePane:boolean;mobileView:'agent'|'workbench';companion:boolean};
export function mobileWorkspace(state:Pick<WorkspaceLayoutState,'singlePane'|'fullscreen'|'companion'>):boolean {
  return state.singlePane&&!state.fullscreen&&!state.companion;
}
export function workspacePanels(state:WorkspaceLayoutState) {
  const panels=visiblePanels(state);
  return mobileWorkspace(state)?{...panels,agent:state.mobileView==='agent',workbench:state.mobileView==='workbench'}:panels;
}
