"""Confirmed prompt bundles in granted folders; never overwrite prior files."""
import json
import os
from copy import deepcopy
from pathlib import PurePosixPath

from packages.domain.intake import canonical, digest
from packages.domain.local_materials import LocalMaterials
from packages.domain.local_workspaces import FileEditInput, sha
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.planning_coverage import require_clear as require_claim_coverage


class PlanningExports:
    def __init__(self,proposals,workspaces):
        self.proposals,self.workspaces=proposals,workspaces
        from packages.domain.harness_manual import HarnessManual
        self.manual=proposals.manual_changes or HarnessManual(proposals,workspaces)

    def _replay(self,operation,request_hash,binding):
        ws=self.workspaces;claim=operation/'request.json';completed=operation/'result.json'
        if claim.exists():
            saved=ws._json(claim)
            if saved['request_sha256']!=request_hash or digest(saved['request'])!=request_hash:
                raise Conflict('IDEMPOTENCY_CONFLICT: export binds different input')
        elif completed.exists():raise Conflict('Export result has no original request')
        if not completed.exists():return None
        result=ws._json(completed);checks=[]
        for file in result['files']:
            try:raw=ws._raw(binding,file['path']);current=sha(raw)==file['sha256']
            except (NotFound,InvalidInput,Conflict):current=False
            checks.append({'path':file['path'],'current':current})
        return {**result,'replayed':True,'file_checks':checks}

    def _directories(self,binding,directory):
        native=self.workspaces._native_handles()
        if native is not None:
            native.create_directories(binding,directory,private_check=self.workspaces._private)
            return
        if os.name!='posix':
            raise InvalidInput('Secure selected-folder directory creation is unavailable on this platform')
        parts=PurePosixPath(directory).parts
        for i,part in enumerate(parts):
            parent='/'.join(parts[:i])
            with self.workspaces._parent(binding,parent,directory=True) as (fd,_):
                try:os.mkdir(part,mode=0o700,dir_fd=fd); os.fsync(fd)
                except FileExistsError:pass
            # Reopening rejects a preexisting link or non-directory.
            with self.workspaces._parent(binding,'/'.join(parts[:i+1]),directory=True):pass

    def export(self,pid,ident,body,*,read_budget=None):
        budget=read_budget if read_budget is not None else self.manual.read_budget()
        with self.manual.proof_scope(pid,body.workspace_ref.model_dump(),budget):
            return self._export(pid,ident,body,read_budget=budget)

    def _export(self,pid,ident,body,*,read_budget=None):
        ws=self.workspaces; ref=body.workspace_ref
        binding=ws.get(pid,ref.id,ref.revision)
        if binding['binding_sha256']!=ref.binding_sha256:
            raise Conflict('Export workspace binding changed')
        grant=body.directory+'/'
        if not any(scope=='*' or (scope.endswith('/') and grant.startswith(scope)) for scope in binding['allowed_write_paths']):
            raise InvalidInput('Grant the selected harness output directory before export')
        operation=ws._data(pid,ref.id,'planning-exports',digest(body.operation_id))
        request={'proposal_id':ident,**body.model_dump(exclude={'operation_id'})}; request_hash=digest(request)
        # Old completed operations keep their exact hash/result, even if later
        # human edits require review for a *new* export. Do not create a lock.
        replay=self._replay(operation,request_hash,binding)
        if replay is not None:return replay
        coverage_view=self.proposals.get(pid,ident,body.expected_revision)
        require_claim_coverage(coverage_view)
        if coverage_view.get('coverage') is not None and (coverage_view['payload']['status']!='CONFIRMED' or
                coverage_view['current_revision']!=body.expected_revision or coverage_view['sha256']!=body.proposal_sha256 or
                coverage_view['review_status']!='CURRENT'):
            raise Conflict('Harness export needs exact current confirmed accepted-item coverage')
        read_budget=read_budget if read_budget is not None else self.manual.read_budget()
        target={'id':ident,'revision':body.expected_revision,'sha256':body.proposal_sha256}
        if not (operation/'request.json').exists():
            self.manual.require_clear(pid,ident,ref.model_dump(),target=target,read_budget=read_budget)
        with LocalMaterials(ws)._lock(operation/'lock'):
            replay=self._replay(operation,request_hash,binding)
            if replay is not None:return replay
            require_claim_coverage(self.proposals.get(pid,ident,body.expected_revision))
            own_pending={'id':operation.name,'request_sha256':request_hash}if(operation/'request.json').exists()else None
            self.manual.require_clear(pid,ident,ref.model_dump(),target=target,read_budget=read_budget,_active_export=own_pending)
            claim=operation/'request.json'
            if claim.exists():
                if ws._json(claim)['request_sha256']!=request_hash:
                    raise Conflict('IDEMPOTENCY_CONFLICT: export binds different input')
            else:self.manual.publish_export(claim,{'request_sha256':request_hash,'request':request})
            active_export={'id':operation.name,'request_sha256':request_hash}
            completed=operation/'result.json'
            if completed.exists():
                result=ws._json(completed)
                checks=[]
                for file in result['files']:
                    try:raw=ws._raw(binding,file['path']); current=sha(raw)==file['sha256']
                    except (NotFound,InvalidInput,Conflict):current=False
                    checks.append({'path':file['path'],'current':current})
                return {**result,'replayed':True,'file_checks':checks}
            proposal=self.proposals.get(pid,ident,body.expected_revision)
            if (proposal['current_revision']!=body.expected_revision or proposal['sha256']!=body.proposal_sha256 or
                    proposal['payload']['status']!='CONFIRMED' or proposal['review_status']!='CURRENT'):
                raise Conflict('Export needs the exact current confirmed task plan and unchanged sources')
            if proposal['payload']['workspace_ref'] is not None and proposal['payload']['workspace_ref']!=ref.model_dump():
                raise Conflict('Export must use the project workspace bound to this plan')
            harness=self.proposals.harness(pid,ident,body.expected_revision)
            pending=self.proposals.pending(pid,ident,body.expected_revision)
            # Identity and hash define a fresh stable version directory; edits
            # create a separate directory rather than replacing old prompts.
            directory=body.directory+'/'+ident+'/v'+str(proposal['revision'])+'-'+proposal['sha256'][:16]
            self._directories(binding,directory)
            files=[{'filename':p['filename'],'content':p['content'],'sha256':p['sha256']} for p in harness['prompts']]
            files += [{'filename':p['filename'],'content':p['content'],'sha256':p['sha256']} for p in harness.get('context_files',[])]
            index=['# '+proposal['payload']['candidate']['title'],'','计划：'+canonical(harness['proposal_ref']),'',
                   *[f'- [{p["node_id"]}]({p["filename"]})' for p in harness['prompts']],
                   '', '先检查前置产物、来源和权限。此目录保存计划，不自动启动执行。','']
            network={k:v for k,v in harness.items() if k not in {'prompts','context_files','requirement_coverage'}}
            if harness.get('coverage_sha256'):
                network['requirement_coverage_ref']={'filename':'accepted-items.json','snapshot_sha256':harness['coverage_sha256']}
            if len(canonical(network).encode())>900000:
                parts=[]; group=[]; size=0
                for node in network.pop('nodes'):
                    count=len(canonical(node).encode())
                    if group and size+count>700000:
                        parts.append(group); group=[]; size=0
                    group.append(node); size+=count
                if group:parts.append(group)
                network['node_files']=[]
                for i,part in enumerate(parts,1):
                    name=f'task-network-nodes-{i:04d}.json'
                    network['node_files'].append(name); files.append({'filename':name,'content':canonical(part)+'\n'})
            sources=[{k:v for k,v in d.items() if k not in {'text_or_selected_chunks','chunks'}}
                     for d in proposal['payload']['source_receipt']['documents']]
            files += [{'filename':'INDEX.md','content':'\n'.join(index)},
                {'filename':'task-network.json','content':canonical(network)+'\n'},
                {'filename':'sources.json','content':canonical({'documents':sources,'coverage':proposal['payload']['source_receipt']['coverage']})+'\n'},
                {'filename':'pending-confirmation.json','content':canonical(pending)+'\n'},
                {'filename':'pending-confirmation.md','content':self.proposals.pending_markdown(pid,ident,body.expected_revision)}]
            # Manifest is the final completion marker, never written before
            # every prompt and pending artifact has been checked.
            manifest={'schema_version':'eeas.project-prompt-bundle.v1','proposal_ref':harness['proposal_ref'],
                'source_receipt_sha256':harness['source_receipt_sha256'],'execution_authorized':False,
                'files':[{'filename':f['filename'],'sha256':sha(f['content'].encode()),'byte_length':len(f['content'].encode())} for f in files]}
            if harness.get('manual_inheritance'):manifest['manual_inheritance']=harness['manual_inheritance']
            if harness.get('coverage_sha256'):manifest['coverage_sha256']=harness['coverage_sha256']
            files.append({'filename':'manifest.json','content':canonical(manifest)+'\n'})
            self.manual.require_clear(pid,ident,ref.model_dump(),target=target,read_budget=read_budget,_active_export=active_export)
            results=[]
            for file in files:
                if not self.proposals._current(pid,proposal['payload']):
                    raise Conflict('Source changed during export; partial output remains recorded and uncommitted')
                path=directory+'/'+file['filename']; expected=sha(file['content'].encode())
                try:existing=ws._raw(binding,path,missing=True)
                except NotFound:existing=None
                if existing is not None:
                    if sha(existing)!=expected:
                        raise Conflict('Existing export bytes differ; old/user files were not overwritten')
                    outcome='EXISTING_IDENTICAL'
                else:
                    ws.edit(pid,ref.id,FileEditInput(operation_id='prompt-'+digest([body.operation_id,path])[:60],
                        path=path,expected_revision=0,content=file['content']))
                    outcome='WRITTEN'
                raw=ws._raw(binding,path)
                if sha(raw)!=expected:
                    raise Conflict('Export byte check failed; manifest is not qualified')
                results.append({'path':path,'sha256':expected,'byte_length':len(raw),'outcome':outcome})
            result={'status':'EXPORTED_CONFIRMED_PLAN','proposal_ref':harness['proposal_ref'],
                    'workspace_ref':ref.model_dump(),'version_directory':directory,'files':results,
                    'execution_started':False,'replayed':False}
            if harness.get('coverage_sha256'):result['coverage_sha256']=harness['coverage_sha256']
            self.manual.require_clear(pid,ident,ref.model_dump(),target=target,read_budget=read_budget,_active_export=active_export)
            if read_budget['charged_bytes'] or read_budget['metadata']['charged_bytes']:result['manual_read_receipt']=deepcopy(read_budget)
            self.manual.publish_export(completed,result)
            return result
