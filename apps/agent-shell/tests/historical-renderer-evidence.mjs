import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdirSync,mkdtempSync,readFileSync,existsSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {isAbsolute,join,dirname,resolve} from 'node:path';

// The original counterexamples depend on frozen source bytes, independent of the volume hosting them.
const pins={
  "layout": {
    "apps/agent-shell/src/App.tsx": "98ec40e1538fadc52d606a27358f793080d9d329160feb163a0914cadb7c1be5"
  },
  "pane": {
    "apps/agent-shell/src/App.tsx": "df300c167d98addce248786b8e055c016770b880d478fbf7c4fbe4a4077291eb",
    "apps/agent-shell/src/panes.ts": "4a3e28f3ca2395f0eb894b69875614c8f9b609dc1081a60ee3cf96a377a359a1"
  },
  "manual": {
    "apps/agent-shell/src/ManualChangesReview.tsx": "59ff846ea9636ba72a8da61f9662bd6008b30f1855af5c027c94bab205708ade"
  },
  "chat": {
    "apps/agent-shell/src/NativeContextPanel.tsx": "1ba8ab77af677c7c78faa09be71822a8a779c94cf2317e9254f7cf695c54b87d",
    "apps/agent-shell/src/NativePageConversation.tsx": "b2a44ef9df9b7bd1d15ab148ae54ae88ac2b340ccfa6cbdb00385210c1147ef7"
  }
};
export function baseline(name,variable){
  const directory=process.env[variable];
  assert.ok(directory&&isAbsolute(directory),'Supply the original source baseline in '+variable);
  for(const [relative,sha256] of Object.entries(pins[name])){
    const actual=createHash('sha256').update(readFileSync(join(directory,relative))).digest('hex');
    assert.equal(actual,sha256,'Historical baseline changed: '+relative);
  }
  return directory;
}
function internalDirectory(location){
  assert.ok(isAbsolute(location),'Choose an absolute internal evidence directory');
  let ancestor=resolve(location);
  while(!existsSync(ancestor))ancestor=dirname(ancestor);
  const actual=realpathSync(ancestor);
  assert.ok(!/^(?:d:|\/mnt\/d(?:\/|$))/i.test(actual)&&!/^(?:d:|\/mnt\/d(?:\/|$))/i.test(resolve(location)),
    'Choose an internal evidence directory outside D:');
  mkdirSync(location,{recursive:true});
  return location;
}
export function evidenceDirectory(name){
  const root=process.env.EEAS_HISTORICAL_RUN_ROOT;
  if(root)return mkdtempSync(join(internalDirectory(root),name+'-'));
  const selected=process.env.EEAS_EVIDENCE_DIR;
  if(selected)return internalDirectory(selected);
  return mkdtempSync(join(internalDirectory(tmpdir()),name+'-'));
}
