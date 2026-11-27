import test from 'node:test';
import assert from 'node:assert/strict';
import {catalogSelection,modelProfileConnectionHint,sortModelCatalog,sortModelProfiles} from '../src/model-catalog.ts';

test('newer numeric generations precede older generations, with variants next to their base model',()=>{
  const models=['gpt-5.4','gpt-6.1-sol-ultrafast','gpt-5.10','gpt-6.1-sol-fast','gpt-6-astra','gpt-6.1-sol','gpt-5.9'].map(id=>Object.freeze({id,name:id}));
  const before=[...models];Object.freeze(models);
  assert.deepEqual(sortModelCatalog(models).map(x=>x.id),['gpt-6.1-sol','gpt-6.1-sol-fast','gpt-6.1-sol-ultrafast','gpt-6-astra','gpt-5.10','gpt-5.9','gpt-5.4']);
  assert.deepEqual(models,before);assert.equal(sortModelCatalog(models)[0],models[5]);
});
test('saved profile ordering preserves exact objects, same-model ties and unknown custom identifiers',()=>{
  const values=[{model_id:'gpt-5.4',profile_ref:{id:'old'}},{model_id:'gpt-6.1-sol',profile_ref:{id:'new-a'}},{model_id:'gpt-6.1-sol',profile_ref:{id:'new-b'}},{model_id:'公司内网模型',profile_ref:{id:'custom'}}];
  const result=sortModelProfiles(values);assert.equal(result.length,4);assert.ok(result.indexOf(values[1])<result.indexOf(values[0]));assert.ok(result.indexOf(values[1])<result.indexOf(values[2]));
  assert.deepEqual(values.map(v=>v.profile_ref.id),['old','new-a','new-b','custom']);
});
test('refresh keeps explicit choice and saved selection, and does not switch a removed selection to another model',()=>{
  const list=[{id:'gpt-5.4'},{id:'gpt-6.1-sol'}];
  assert.equal(catalogSelection(list,'gpt-5.4','gpt-6.1-sol'),'gpt-5.4');
  assert.equal(catalogSelection(list,'','gpt-5.4',true),'gpt-5.4');
  assert.equal(catalogSelection(list,'','gpt-5.4'),'');
  assert.equal(catalogSelection(list,'removed-model','gpt-6.1-sol'),'');
  const lost=catalogSelection(list,'removed-model','gpt-6.1-sol');
  assert.equal(catalogSelection(list,lost,'gpt-6.1-sol'),'');
  assert.equal(catalogSelection(list,''),'');assert.equal(catalogSelection([],'gpt-6.1-sol','gpt-6.1-sol'),'');
});

test('same-model choices retain exact connection IDs and distinguish provider hosts without changing source objects',()=>{
  const profiles=Object.freeze([
    Object.freeze({model_id:'same-model',provider_url:'https://models.example.com:8443/v1',profile_ref:Object.freeze({id:'research'})}),
    Object.freeze({model_id:'same-model',provider_url:'http://localhost:9301/v1',profile_ref:Object.freeze({id:'local'})}),
  ]),before=JSON.stringify(profiles);
  assert.equal(modelProfileConnectionHint(profiles[0],profiles),'research · models.example.com:8443');
  assert.equal(modelProfileConnectionHint(profiles[1],profiles),'local · localhost:9301');
  assert.equal(JSON.stringify(profiles),before);
  assert.deepEqual(sortModelProfiles(profiles),[profiles[0],profiles[1]]);
});
test('unique model names keep the compact one-line choice and exact IDs are not normalized',()=>{
  const profiles=[{model_id:'model-v1',provider_url:'https://example.com/v1',profile_ref:{id:'work'}},{model_id:'model-v2',provider_url:'https://example.com/v1',profile_ref:{id:'other'}}];
  assert.equal(modelProfileConnectionHint(profiles[0],profiles),undefined);
  assert.equal(modelProfileConnectionHint(profiles[1],profiles),undefined);
});
test('connection hints strip URL credentials, path, query and fragment and tolerate invalid legacy URLs',()=>{
  const profiles=[{model_id:'same',provider_url:'https://user:private-key@example.com:443/secret-path?token=private#fragment',profile_ref:{id:'saved'}},{model_id:'same',provider_url:'invalid legacy endpoint',profile_ref:{id:'legacy'}}];
  assert.equal(modelProfileConnectionHint(profiles[0],profiles),'saved · example.com');
  assert.equal(modelProfileConnectionHint(profiles[1],profiles),'legacy');
  assert.doesNotMatch(modelProfileConnectionHint(profiles[0],profiles),/user|private|secret|token|fragment/);
});
