import test from 'node:test';
import assert from 'node:assert/strict';
import {fitPaneWidths,projectPaneWidths,PANE_MIN,WORKBENCH_MIN} from '../src/panes.ts';

test('wide customized panes fit a smaller workbench without clipping either sidebar',()=>{
 const wide=fitPaneWidths(440,640,1920);assert.deepEqual(wide,{files:440,agent:640});
 const narrow=fitPaneWidths(wide.files,wide.agent,950);
 assert.ok(narrow.files>=PANE_MIN.files);assert.ok(narrow.agent>=PANE_MIN.agent);
 assert.ok(950-narrow.files-narrow.agent-12>=WORKBENCH_MIN);
 assert.deepEqual(fitPaneWidths(narrow.files,narrow.agent,950),narrow);
});
test('restored and dragged panes keep the central workspace available at the desktop breakpoint',()=>{
 for(const viewport of [921,950,1024,1280])for(const [files,agent] of [[5000,5000],[440,640],[236,360],[-50,NaN]]){
  const fit=fitPaneWidths(files,agent,viewport);
  assert.ok(Number.isFinite(fit.files)&&Number.isFinite(fit.agent));
  assert.ok(fit.files>=220&&fit.agent>=360);
  assert.ok(viewport-fit.files-fit.agent-12>=WORKBENCH_MIN);
 }
});
test('normal widths and hidden compact panes retain their minimum readable sizes',()=>{
 assert.deepEqual(fitPaneWidths(236,360,1280),{files:236,agent:360});
 const small=fitPaneWidths(440,640,430);assert.deepEqual(small,{files:220,agent:360});
});

test('reading gives the opened content space without rewriting the conversation width or a deliberate reading width',()=>{
 const intent={files:236,agent:680},before={...intent};
 const reading=projectPaneWidths(intent,1440,'reading');assert.equal(reading.agent,420);
 assert.ok(1213-projectPaneWidths(intent,1213,'reading').files-projectPaneWidths(intent,1213,'reading').agent-12>=540);
 assert.deepEqual(projectPaneWidths(intent,1440),{files:236,agent:680});
 assert.equal(projectPaneWidths(intent,1440,'reading',500).agent,500);assert.deepEqual(intent,before);
 const narrow=projectPaneWidths(intent,1101,'reading',900);assert.ok(1101-narrow.files-narrow.agent-12>=WORKBENCH_MIN);
});
