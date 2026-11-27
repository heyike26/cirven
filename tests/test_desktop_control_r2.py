"""Negative invalidated-authority records remain explicitly stoppable.

These records never create an Agent lease, a native session or a GUI window.
"""
import copy
import pytest
from test_desktop_control import control,takeover
from packages.domain.local_workspaces import digest


def test_default_user_no_desktop_execution_does_not_show_takeover(tmp_path):
    value=control(tmp_path)
    assert value.get()['owner']=='USER' and value.get()['can_takeover'] is False
    taken=takeover(value)
    assert taken['owner']=='USER' and taken['can_takeover'] is False and taken['cancellation']['status']=='NOT_NEEDED'


def test_expired_unverified_record_is_user_but_cleanup_remains_available(tmp_path):
    value=control(tmp_path)
    ref={'project_id':'unverified-negative-record','execution_id':'no-native-session-created','request_sha256':'b'*64,'task_ref':None}
    # An expired authority fixture is never admitted: no current native layout,
    # no qualified input route, no existing execution authority.
    value.state['owner']='AGENT'
    value.state['lease']={'generation':value.generation,'expires_unix_ms':0,
        'execution_ref':ref,'execution_scope':{'surface_ref':{'id':'f'*64,'revision':1,'sha256':'a'*64},'input_epoch':1,'task_ref':None}}
    value._save('NEGATIVE_EXPIRED_UNVERIFIED_AUTHORITY_RECORD')
    state=value.get()
    assert state['owner']=='USER' and state['lease_ref'] is None and not state['queued_actions_enabled']
    assert state['can_takeover'] and not state['capabilities']['delegation_ready']
    first=takeover(value,'take-invalidated')
    assert first['owner']=='USER' and first['can_takeover'] and first['cancellation']['status']=='FAILED'
    assert first['cancellation']['confirmed'] is False and first['model_calls']==0
    retry=takeover(value,'retry-invalidated')
    assert retry['control_epoch']==first['control_epoch']+1 and retry['cancellation']['status']=='FAILED'
    assert retry['owner']=='USER' and not retry['queued_actions_enabled']


@pytest.mark.parametrize('status',['REQUESTED','UNKNOWN_OUTCOME','FAILED'])
def test_pending_cancellation_safe_flag_does_not_claim_execution_was_cancelled(tmp_path,status):
    value=control(tmp_path)
    ref={'project_id':'negative-history','execution_id':'unknown-owned-outcome','request_sha256':'c'*64,'task_ref':None}
    value.state['revoked_execution_ref']=ref
    value.state['cancellation']={'status':status,'confirmed':False,'execution_ref':copy.deepcopy(ref),
        'queued_inputs':{'status':'UNKNOWN_OUTCOME','discarded_count':None}}
    value._save('NEGATIVE_CANCELLATION_OUTCOME_RECORD_'+status)
    state=value.get()
    assert state['owner']=='USER' and state['execution_ref'] is None and state['can_takeover'] is True
    assert state['cancellation']['status']==status and not state['cancellation']['confirmed']
    assert not state['queued_actions_enabled'] and not state['capabilities']['delegation_ready']
