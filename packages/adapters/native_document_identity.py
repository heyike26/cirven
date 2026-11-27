"""Bounded own reader process; never starts or manipulates an Office instance."""
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import threading

from packages.domain.tasks import digest


class NativeDocumentIdentity:
    def __init__(self, *, runner=subprocess.run, platform=os.name, executable=sys.executable):
        self.runner, self.platform, self.executable = runner, platform, executable
        self.allowance = threading.BoundedSemaphore(1)

    def read(self, window):
        unavailable = {'status': 'UNAVAILABLE', 'reason': 'NATIVE_DOCUMENT_IDENTITY_NOT_SUPPORTED'}
        if self.platform != 'nt' or window.get('window_class') != 'OpusApp':
            return unavailable
        if (type(window.get('window_id')) is not int or not 0 < window['window_id'] < 2**64
                or type(window.get('pid')) is not int or not 0 < window['pid'] < 2**32):
            return unavailable
        if not self.allowance.acquire(blocking=False):
            return {**unavailable, 'reason': 'NATIVE_DOCUMENT_IDENTITY_BUSY'}
        try:
            worker = Path(__file__).with_name('word_window_identity_worker.py')
            result = self.runner([self.executable, '-I', '-B', str(worker), str(window['window_id']), str(window['pid'])],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=2.5, check=False, creationflags=0x08000000)
            if result.returncode != 0 or len(result.stdout) > 24000 or result.stderr:
                return unavailable
            value = json.loads(result.stdout)
            if (value.get('status') != 'OBSERVED' or value.get('source') != 'WORD_WINDOW_NATIVEOM_PROPERTYGET'
                    or value.get('root_window_id') != window['window_id'] or value.get('process_id') != window['pid']
                    or type(value.get('saved')) is not bool or value.get('content_equals_saved_bytes') is not False
                    or not isinstance(value.get('native_path'), str) or len(value['native_path']) > 4096):
                return unavailable
            path = PureWindowsPath(value['native_path'])
            if not path.is_absolute() or len(path.drive) != 2 or path.drive[1:] != ':' or any(ord(char) < 32 for char in value['native_path']):
                return unavailable
            proof = {'source': value['source'], 'native_path': value['native_path'], 'saved': value['saved'],
                'window_lifetime_sha256': window['identity_sha256'], 'content_equals_saved_bytes': False}
            return {'status': 'OBSERVED', **proof, 'identity_sha256': digest(proof)}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return unavailable
        finally:
            self.allowance.release()


def public_document_identity(value):
    if value.get('status') != 'OBSERVED':
        return {'status': 'UNAVAILABLE'}
    return {key: value[key] for key in ('status', 'source', 'saved', 'window_lifetime_sha256',
        'content_equals_saved_bytes', 'identity_sha256')}
