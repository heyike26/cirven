"""Original multi-file picker, partial rejection, stale callback and revision flows."""
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect,sync_playwright
from test_web import launch_browser


def keep(name,value):
 folder=os.environ.get('EEAS_EVIDENCE_DIR')
 if folder:(Path(folder)/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


@pytest.fixture
def desk(live_server):
 with httpx.Client(base_url=live_server.origin,trust_env=False) as api:
  bootstrap=api.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()
  api.headers.update({'Origin':live_server.origin,'X-EEAS-CSRF':bootstrap['csrf_token']})
  project=api.post('/api/projects',json={'name':'原创多份资料','description':'共同梳理前保存全部原件'}).json()
  with sync_playwright() as playwright:
   browser=launch_browser(playwright);page=browser.new_page(viewport={'width':1100,'height':780})
   errors=[];requests=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda q:requests.append(q.url))
   page.goto(live_server.origin+'/?view=library&project='+project['id'])
   expect(page.locator('#library-upload')).to_be_visible()
   yield {'api':api,'pid':project['id'],'page':page,'origin':live_server.origin}
   assert not errors,errors
   assert all(url.startswith(live_server.origin+'/') for url in requests),requests
   browser.close()


def rows(desk):
 response=desk['api'].get('/api/projects/'+desk['pid']+'/documents')
 assert response.status_code==200,response.text
 return response.json()['documents']


def select(desk,files):
 p=desk['page'];p.locator('#library-upload').click()
 p.locator('#document-file').set_input_files(files)
 p.locator('#document-source').fill('原创多文件夹具，不是公司资料')
 p.locator('#document-form button[type=submit]').click()


def test_actual_picker_saves_every_file_and_exact_source_bytes(desk):
 originals=[('会议纪要 & 范围.txt','目标：读串口数据。注意：波特率需要共同确认。\r\n'),('设计说明.md','# 已有设计\n验收：原始行可重放；格式由用户模板决定。\n')]
 keep('multi-picker-inputs.json',[{'filename':name,'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()} for name,text in originals])
 select(desk,[{'name':name,'mimeType':'text/plain','buffer':text.encode()} for name,text in originals])
 p=desk['page'];expect(p.locator('#upload-batch-summary')).to_contain_text('2 份已保存')
 expect(p.locator('#upload-file-list li[data-state=SAVED]')).to_have_count(2)
 assert p.locator('#upload-drawer').is_visible()
 found=rows(desk);assert len(found)==2
 for name,text in originals:
  doc=next(x for x in found if x['filename']==name)
  assert doc['sha256']==hashlib.sha256(text.encode()).hexdigest()
  result=desk['api'].get(f"/api/projects/{desk['pid']}/documents/{doc['id']}/download",params={'revision':1})
  assert result.content==text.encode()
 keep('multi-picker-saved.json',found)
 if os.environ.get('EEAS_EVIDENCE_DIR'):p.screenshot(path=os.environ['EEAS_EVIDENCE_DIR']+'/multi-picker.png')
 p.locator('#upload-view-library').click()
 expect(p.locator('#upload-drawer')).to_be_hidden()
 expect(p.locator('#documents li')).to_have_count(2)
 assert desk['api'].get(f"/api/projects/{desk['pid']}/tasks").json()['tasks']==[]


def test_partial_server_rejection_retries_only_unsaved_file(desk):
 raw=b'original accepted notes\r\n';bad=b'original deliberately invalid PNG, no image'
 select(desk,[{'name':'accepted.txt','mimeType':'text/plain','buffer':raw},{'name':'rejected.png','mimeType':'image/png','buffer':bad}])
 p=desk['page'];expect(p.locator('#upload-batch-summary')).to_contain_text('1 份已保存')
 expect(p.locator('#upload-file-list li[data-state=FAILED]')).to_have_count(1)
 expect(p.locator('#upload-error')).to_be_focused()
 found=rows(desk);assert len(found)==1 and found[0]['sha256']==hashlib.sha256(raw).hexdigest()
 snapshot=json.dumps(found,sort_keys=True)
 p.locator('#document-form button[type=submit]').click()
 expect(p.locator('#upload-file-list li[data-state=FAILED]')).to_have_count(1)
 expect(p.locator('#document-form button[type=submit]')).to_be_enabled()
 assert json.dumps(rows(desk),sort_keys=True)==snapshot
 keep('multi-partial-saved.json',found)


def test_new_selection_during_real_response_stops_remaining_files(desk):
 p=desk['page'];calls=[]
 def complete_after_new_selection(route):
  if route.request.method!='POST':return route.continue_()
  response=route.fetch();calls.append(response.status)
  p.evaluate("document.querySelector('#document-new').click()")
  route.fulfill(response=response)
 p.route('**/api/projects/*/documents',complete_after_new_selection)
 select(desk,[{'name':'first.txt','mimeType':'text/plain','buffer':b'original first'}, {'name':'must-not-send.txt','mimeType':'text/plain','buffer':b'original second'}])
 expect(p.locator('#upload-file-state')).to_have_text('尚未选择文件')
 expect(p.locator('#upload-batch-summary')).to_be_hidden()
 p.wait_for_function("window.EEASUploadBatch && !document.querySelector('#document-form [type=submit]').disabled")
 found=rows(desk);assert calls==[201] and len(found)==1 and found[0]['filename']=='first.txt'
 keep('multi-stale-response.json',{'statuses':calls,'documents':found})


def test_revision_rejects_two_dropped_files_then_retains_exact_old_bytes(desk):
 api,p=desk['api'],desk['page'];raw=b'original old revision\r\n'
 response=api.post(f"/api/projects/{desk['pid']}/documents",files={'file':('old.txt',raw,'text/plain')},data={'source':'original revision fixture'})
 assert response.status_code==201,response.text
 doc=response.json();p.reload();p.locator('#documents').get_by_role('button',name='上传新修订').click()
 assert not p.locator('#document-file').evaluate('(el)=>el.multiple')
 p.evaluate("""() => {let d=new DataTransfer();d.items.add(new File(['one'],'one.txt'));d.items.add(new File(['two'],'two.txt'));let e=new Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(e,'dataTransfer',{value:d});document.querySelector('#upload-dropzone').dispatchEvent(e)}""")
 p.locator('#document-form button[type=submit]').click()
 expect(p.locator('#upload-error')).to_contain_text('修订只能选择一份')
 assert rows(desk)[0]['revision']==1
 p.locator('#document-file').set_input_files({'name':'new.txt','mimeType':'text/plain','buffer':b'original new revision'})
 p.locator('#document-form button[type=submit]').click()
 expect(p.locator('#upload-drawer')).to_be_hidden()
 assert rows(desk)[0]['revision']==2
 assert api.get(f"/api/projects/{desk['pid']}/documents/{doc['id']}/download",params={'revision':1}).content==raw
 keep('multi-revision.json',rows(desk))
