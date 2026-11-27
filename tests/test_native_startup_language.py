"""Actual local preference/catalog reads; no native GUI qualification."""
import json
from pathlib import Path

import pytest

from scripts.native_package_bootstrap import StartupCopy


def copy_fixture(tmp_path, preference):
    root = tmp_path / 'Cirven 本地包'
    catalog = Path(__file__).resolve().parents[1] / 'packages/i18n/bootstrap.json'
    for name, value in [('packages/branding/product.json', {'display_name':'Cirven Agent'}),
        ('packages/i18n/bootstrap.json', json.loads(catalog.read_text())),
        ('user-data/desktop-preferences.json', {'schema':'eeas.desktop-preferences.v1',
            'ui_language':preference, 'assistant_orb_enabled':False})]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return root


@pytest.mark.parametrize('preference,phrase', [('en','Checking installation files'), ('zh-CN','正在校验安装文件')])
def test_before_verification_ui_uses_persisted_language_and_original_local_brand(tmp_path, preference, phrase):
    root = copy_fixture(tmp_path, preference)
    before = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    copy = StartupCopy(root)
    assert copy.locale == preference and copy.brand == 'Cirven Agent'
    assert phrase in copy('verifyBody')
    assert copy('verifyCount', completed=2, total=11).startswith('2 / 11')
    original_path = r'D:\论文 {not_a_key}\原始日志.log'
    message = copy('abnormalExit', code=23, path=original_path)
    assert original_path in message and '23' in message
    assert before == {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_restart_rereads_language_without_touching_orb_or_other_user_files(tmp_path):
    root = copy_fixture(tmp_path, 'en')
    assert StartupCopy(root).locale == 'en'
    target = root / 'user-data/desktop-preferences.json'
    value = json.loads(target.read_text());value['ui_language'] = 'zh-CN';target.write_text(json.dumps(value))
    assert StartupCopy(root).locale == 'zh-CN'
    assert json.loads(target.read_text())['assistant_orb_enabled'] is False


@pytest.mark.parametrize('value', [[], {}, 9, None, 'unsupported'])
def test_invalid_language_and_missing_catalog_keep_recovery_available(tmp_path, monkeypatch, value):
    root = copy_fixture(tmp_path, value)
    monkeypatch.setattr('locale.getlocale', lambda:('en_US','UTF-8'))
    copy = StartupCopy(root)
    assert copy.locale == 'en'
    (root / 'packages/i18n/bootstrap.json').write_text('not a catalog')
    copy = StartupCopy(root)
    assert copy('failedTitle') == 'Cirven Agent · Startup failed'


def test_oversized_and_linked_copy_never_loads_outside_content(tmp_path):
    root = copy_fixture(tmp_path, 'en')
    target = root / 'packages/i18n/bootstrap.json'
    target.write_text('x' * 131073)
    assert StartupCopy(root)('cancel') == 'Cancel startup'
    outside = tmp_path / 'outside.json';outside.write_text('{"display_name":"Outside Agent"}')
    brand = root / 'packages/branding/product.json';brand.unlink();brand.symlink_to(outside)
    assert StartupCopy(root).brand == 'Agent'
