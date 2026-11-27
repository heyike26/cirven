export type PanelPresentation={filesHidden:boolean;agentHidden:boolean;workbenchHidden:boolean;fullscreen:boolean;fullscreenAgent:boolean;fullscreenFiles?:boolean};
/** Fullscreen projects panel visibility without mutating saved widths, artifact, or drafts. */
export function visiblePanels(value:PanelPresentation){return value.fullscreen?{files:!!value.fullscreenFiles,workbench:true,agent:value.fullscreenAgent}:{files:!value.filesHidden,workbench:!value.workbenchHidden,agent:!value.agentHidden};}
