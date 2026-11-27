"""Actual application TCP/CSRF/host-bound OAuth URL control, no real login."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import httpx

from conftest import LiveServer
from packages.adapters.opencode_profile import CLI_SHA, VERSION, binary_hash
from test_opencode_account import CLI, check, evidence


class AccountLiveServer(LiveServer):
    def start(self):
        self.output = (self.data_dir / 'actual-api.log').open('ab')
        self.process = subprocess.Popen([sys.executable] + (['-O'] if not __debug__ else []) +
            ['-m', 'apps.api', 'serve'], cwd=Path(__file__).resolve().parents[1], env=self.env,
            stdout=self.output, stderr=self.output)
        deadline = time.monotonic() + 90
        with httpx.Client(trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None: raise RuntimeError('Actual account API exited; preserved actual-api.log')
                try:
                    if client.get(self.origin + '/health', timeout=.3).status_code == 200: return self
                except httpx.HTTPError: pass
                time.sleep(.05)
        raise RuntimeError('Actual account API startup exceeded fixture bound')

    def stop(self):
        try: super().stop()
        finally:
            if getattr(self, 'output', None): self.output.close()


def test_actual_tcp_api_csrf_host_target_cancel_and_restart_are_get_only(tmp_path):
    data = tmp_path / 'api-data'; data.mkdir()
    binary = data / 'provider-settings/runtime' / VERSION / 'opencode'
    binary.parent.mkdir(parents=True)
    shutil.copyfile(CLI, binary); binary.chmod(0o700)
    check(binary_hash(binary) == CLI_SHA['posix'])
    token = secrets.token_hex(32)
    server = AccountLiveServer(data)
    server.env.update(EEAS_WORKER='0', EEAS_DESKTOP_HOST_TOKEN=token, EEAS_DESKTOP_HOST_PID=str(os.getpid()))
    summaries = []
    try:
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=60) as client:
            denied = client.get('/api/subscription/account')
            check(denied.status_code == 401)
            bootstrap = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap':'1'})
            check(bootstrap.status_code == 200)
            client.headers.update({'Origin':server.origin})
            denied = client.post('/api/subscription/login', json={'operation_id':'actual-api-login','method':'browser'})
            check(denied.status_code == 403)
            client.headers['X-EEAS-CSRF'] = bootstrap.json()['csrf_token']
            state = client.get('/api/subscription/account')
            check(state.status_code == 200 and state.json()['status'] == 'SIGNED_OUT')
            begun = client.post('/api/subscription/login', json={'operation_id':'actual-api-login','method':'browser'})
            check(begun.status_code == 200 and begun.json()['status'] == 'WAITING_USER')
            ident = begun.json()['attempt_id']
            route = '/api/desktop-host/subscription/login-target'
            check(client.get(route, params={'attempt_id':ident}).status_code == 409)
            check(client.get(route, params={'attempt_id':ident}, headers={'X-EEAS-Desktop-Host':'invalid'}).status_code == 409)
            actual = client.get(route, params={'attempt_id':ident}, headers={'X-EEAS-Desktop-Host':token})
            check(actual.status_code == 200 and actual.json()['login_url'] == begun.json()['login_url'])
            rejected = client.post('/api/subscription/attempts/'+ident+'/complete',json={'approved':True})
            check(rejected.status_code == 422)
            completing = client.post('/api/subscription/attempts/'+ident+'/complete',json={})
            check(completing.status_code == 200 and completing.json()['phase'] == 'COMPLETING')
            repeated = client.post('/api/subscription/attempts/'+ident+'/complete',json={})
            check(repeated.status_code == 200 and repeated.json()['phase'] == 'COMPLETING')
            for _ in range(3):
                reopened = client.get(route, params={'attempt_id':ident}, headers={'X-EEAS-Desktop-Host':token})
                check(reopened.status_code == 200 and reopened.json()['login_url'] == begun.json()['login_url'])
            waiting = client.get('/api/subscription/account')
            check(waiting.status_code == 200 and waiting.json()['phase'] == 'COMPLETING')
            check('login_url' not in waiting.json())
            cancel = client.post('/api/subscription/attempts/'+ident+'/cancel',json={})
            check(cancel.status_code == 200 and cancel.json()['status'] == 'SIGNED_OUT')
            check(client.get(route, params={'attempt_id':ident}, headers={'X-EEAS-Desktop-Host':token}).status_code == 409)
            invalid = client.post('/api/subscription/login',json={'operation_id':'invalid-api-fallback','method':'browser','api_key':'fixture-not-a-key'})
            check(invalid.status_code == 422)
            missing = client.get('/api/subscription/profile-operations/no-such-operation')
            check(missing.status_code == 200 and missing.json()['status'] == 'NO_RECORD')
            summaries.append({'csrf_without_token':403,'host_without_token':409,'typed_complete_extra_fields':422,
                'native_callback_phase':completing.json()['phase'],'cancel_status':cancel.json()['status'],
                'completing_reopen_get_count':3,'completing_reopen_same_original_url':True,
                'account_get_reveals_login_url':False,'cancelled_reopen_status':409,
                'api_key_field_denied':422,'profile_operation_missing':'NO_RECORD'})
            stopped = client.post('/api/shutdown',json={})
            check(stopped.status_code == 200 and stopped.json()['status'] == 'stopping')
            check(stopped.json()['owner_shutdown']['status'] == 'CLOSED')
            server.process.wait(timeout=45); check(server.process.returncode == 0)
        server.stop()
        server = AccountLiveServer(data)
        server.env.update(EEAS_WORKER='0', EEAS_DESKTOP_HOST_TOKEN=token, EEAS_DESKTOP_HOST_PID=str(os.getpid()))
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=60) as client:
            bootstrap = client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'})
            client.headers.update({'Origin':server.origin,'X-EEAS-CSRF':bootstrap.json()['csrf_token']})
            state = client.get('/api/subscription/account')
            check(state.status_code == 200 and state.json()['status'] == 'SIGNED_OUT' and state.json()['attempt_id'] == ident)
            check('login_url' not in state.json())
            check(not (data/'subscription-account/native-data/opencode/auth.json').exists())
            summaries.append({'restart_state':state.json(),'original_auth_file_created':False})
            stopped = client.post('/api/shutdown',json={})
            check(stopped.json()['status'] == 'stopping')
            server.process.wait(timeout=45); check(server.process.returncode == 0)
        evidence({'tmp':tmp_path},'actual-tcp-csrf-host-restart',{'observed':summaries,
            'real_api_processes_exited_zero':2,'browser_opened':False,'real_token_endpoint_called':False})
    finally: server.stop()
