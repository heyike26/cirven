'use strict';

// Only explicit conversation/workbench intents may reveal Main.
function createAssistantOrbActions({ getCompanion, getWindow, getQuickSurface, getAnchor, getTaskActivity, reveal, isClosing, logFailure = () => {} }) {
  let busy = false, intentEpoch = 0;
  return async function act(action,options={}) {
    if (!['ask', 'show-agent', 'workbench', 'annotate','tasks'].includes(action)) throw new Error('Unknown assistant action');
    if (busy || isClosing()) return { status: 'BUSY' };
    if(options.replyTarget&&action!=='show-agent')throw new Error('Reply navigation needs explicit conversation intent');
    const current=()=>!isClosing()&&(typeof options.isCurrent!=='function'||options.isCurrent());
    if(!current())return {status:'STALE'};
    const epoch = ++intentEpoch;
    busy = true;
    try {
      if (action === 'ask' || action === 'annotate' || action==='tasks') {
        const surface = getQuickSurface?.();
        if (!surface) return { status: 'NOT_READY' };
        // Release the launcher while capture waits. QuickSurface guards late
        // frames by epoch; a second explicit action can cancel/reopen it.
        const anchor = getAnchor?.();
        const isCurrent = () => epoch === intentEpoch && !isClosing() && getQuickSurface?.() === surface;
        const pending = action==='tasks' ? Promise.resolve(getTaskActivity?.()).then(value=>isCurrent()?surface.openActivity(value,{anchor,isCurrent}):{status:'STALE'}) :
          action === 'annotate' ? surface.beginAnnotation({anchor}) : surface.openAsk({anchor,
            ...(options.trigger==='CTRL_SHIFT_SPACE'?{trigger:options.trigger}:{})});
        Promise.resolve(pending).catch(logFailure);
        return { status: 'OPENING_IN_PLACE' };
      }
      const companion = getCompanion();
      if (!companion) return { status: 'NOT_READY' };
      const mode=action === 'workbench' ? 'workbench' : 'companion';
      const state = await companion.setMode(mode, mode==='companion'?{anchor:getAnchor?.()}:undefined);
      if (isClosing()) return { status: 'CLOSING' };
      if(!current())return {status:'STALE'};
      const window = getWindow(); reveal(window);
      window.webContents.send('eeas:collaboration-state', state);
      if (action === 'show-agent') {
        if(options.replyTarget)window.webContents.send('eeas:quick-question',options.replyTarget);
        else window.webContents.send('eeas:quick-question');
      }
      return { status: 'SHOWN' };
    } finally { busy = false; }
  };
}

module.exports = { createAssistantOrbActions };
