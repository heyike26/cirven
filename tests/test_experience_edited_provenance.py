"""AUTHORED_NOT_RUN: ordinary capability edits retain source policy."""
from copy import deepcopy
import pytest
from packages.domain.store import Conflict
from packages.domain.experience_provenance import read
from test_experience_candidates import experience
from test_experience_activation import promotion,promote
from test_experience_source_policy import decide,decision


@pytest.mark.parametrize('enabled',[False,True])
def test_changing_timeout_cannot_sever_forgotten_source_provenance(promotion,enabled):
    s=promotion;saved=promote(s);ref=saved['observation']['current_configuration_ref'];config=deepcopy(saved['observation']['native_operation']['configuration'])
    config.update(timeout=config['timeout']+1,enabled=enabled);decide(s,decision(s,'FORGET'))
    body={'operation_id':'edit_timeout','id':'learned-tool','expected_revision':ref['revision'],'configuration':config}
    if enabled:
        with pytest.raises(Conflict):s['caps'].configure_mcp(body)
        assert s['caps']._index()['mcp']['learned-tool']==ref
    else:
        result=s['caps'].configure_mcp(body);origin=read(s['caps'],'mcp',result['config_ref'])
        assert origin['source']['candidate_ref']==s['candidate']['record_ref']
        with pytest.raises(Conflict):s['caps'].set_enabled('mcp','learned-tool',{'operation_id':'enable_after_edit','expected_revision':result['config_ref']['revision'],'enabled':True})


def test_active_manual_edit_keeps_original_source_and_later_withdrawal_still_blocks_it(promotion):
    s=promotion;saved=promote(s);ref=saved['observation']['current_configuration_ref'];config=deepcopy(saved['observation']['native_operation']['configuration']);config['timeout']+=1
    changed=s['caps'].configure_mcp({'operation_id':'edit_active_timeout','id':'learned-tool','expected_revision':ref['revision'],'configuration':config})
    assert read(s['caps'],'mcp',changed['config_ref'])['source']['candidate_ref']==s['candidate']['record_ref']
    decide(s,decision(s));assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}
