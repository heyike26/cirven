"""Exercise the kernel-management HTTP API with isolated local files and a controlled registry."""
import base64
from hashlib import sha256, sha512
from io import BytesIO
import os
import platform
import tarfile

import httpx

from packages.dynamic_workflow import kernel_settings, kernel_updates


def test_company_route_verified_update_and_rollback_over_http(client, tmp_path, monkeypatch):
    root = '/api/workflow-kernels'
    engine = 'claude-code'
    api = root + '/' + engine

    def catalog(response):
        assert response.status_code == 200, response.text
        data = response.json()
        return next(row for row in data['kernels'] if row['id'] == engine)

    assert catalog(client.get(root))['revision'] == 0
    route = dict(endpoint='https://internal.example.test', model='company-model',
                 credential_env='CIRVEN_TEST_REF', allow_http=False)
    assert catalog(client.put(api + '/route', json=dict(revision=0, **route)))['route'] == route
    original = tmp_path / 'vendor-test-file'
    original.write_bytes(b'original kernel fixture; never executed')
    first = sha256(original.read_bytes()).hexdigest()
    installed = catalog(client.post(api + '/versions', json=dict(
        revision=1, path=str(original), sha256=first, version='1.0.0')))
    assert installed['active'] == first

    raw = b'updated kernel fixture; never executed'
    archived = BytesIO()
    executable = 'claude.exe' if os.name == 'nt' else 'claude'
    with tarfile.open(fileobj=archived, mode='w:gz') as archive:
        entry = tarfile.TarInfo('package/' + executable)
        entry.size = len(raw)
        archive.addfile(entry, BytesIO(raw))
    package_data = archived.getvalue()
    integrity = 'sha512-' + base64.b64encode(sha512(package_data).digest()).decode()
    os_name = {'Linux':'linux','Darwin':'darwin','Windows':'win32'}[platform.system()]
    cpu = {'amd64':'x64','x86_64':'x64','arm64':'arm64','aarch64':'arm64'}[platform.machine().lower()]
    base = '@anthropic-ai/claude-code'
    package = f'{base}-{os_name}-{cpu}'
    url = f'https://registry.npmjs.org/{package}/-/test-release.tgz'
    version = '1.0.1'
    calls = []

    def registry(request):
        assert request.url.host == 'registry.npmjs.org'
        path = request.url.path
        calls.append(path)
        if str(request.url) == url:
            return httpx.Response(200, content=package_data)
        if path in (f'/{base}/latest', f'/{base}/{version}'):
            return httpx.Response(200, json={'name':base,'version':version,'optionalDependencies':{package:version}})
        assert path == f'/{package}/{version}'
        return httpx.Response(200, json={'name':package,'version':version,'dist':{'tarball':url,'integrity':integrity}})

    with httpx.Client(transport=httpx.MockTransport(registry)) as upstream:
        monkeypatch.setattr(kernel_updates.httpx, 'stream', upstream.stream)
        check = client.get(api + '/release')
        assert check.status_code == 200
        assert check.json() == dict(version=version, package=package, url=url, integrity=integrity)
        update = dict(revision=2, version=version, integrity=integrity)
        active = catalog(client.post(api + '/download', json=update))
        updated = sha256(raw).hexdigest()
        assert active['active'] == updated and active['revision'] == 3
        path = kernel_settings.select_binary(tmp_path, engine, updated)
        assert path.read_bytes() == raw
    assert len(calls) == 5
    rollback = catalog(client.put(api + '/active', json=dict(revision=3, sha256=first)))
    assert rollback['active'] == first and rollback['revision'] == 4
    assert {v['digest'] for v in rollback['versions']} == {first, updated}
    assert client.put(api + '/active', json=dict(revision=3, sha256=updated)).status_code == 409
    response = client.put(root + '/company-policy', json=dict(revision=0, required=True))
    assert response.status_code == 200 and response.json()['company'] == {'revision':1,'required':True}
    assert catalog(client.get(root))['active'] == first
