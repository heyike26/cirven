import test from 'node:test';
import assert from 'node:assert/strict';
import {railKey} from '../src/activity-rail.ts';
test('arrow/Home/End focus only enabled controls in the owning rail, preserving native Tab/Enter and never dispatching desktop input',()=>{
 const focused=[],buttons=[0,1,2].map(index=>({focus:()=>focused.push(index)})),rail={querySelectorAll:selector=>{assert.equal(selector,'button:not(:disabled)');return buttons;}};let prevented=0,stopped=0;const event=key=>({key,target:buttons[2],preventDefault:()=>prevented++,stopPropagation:()=>stopped++});railKey(event('ArrowDown'),rail);railKey(event('Home'),rail);railKey(event('End'),rail);assert.deepEqual(focused,[0,0,2]);assert.equal(prevented,3);assert.equal(stopped,3);railKey(event('Tab'),rail);railKey(event('Enter'),rail);assert.equal(focused.length,3);
});
