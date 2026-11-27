"""Actual official MCP stdio subprocess; it must not claim controller recovery."""
import asyncio
from pathlib import Path
from packages.planning.client import call_discovery_tool
from packages.domain.intake import digest
from test_discovery_workbench import create,edited
from test_project_intake import project

ROOT=Path(__file__).resolve().parents[1]


def test_official_stdio_discovery_client_reads_exact_project_without_confirmation_or_recovery(client):
    pid,_,_,view=create(client);view=edited(client,pid,view)
    with client.app.state.store.connect() as c:
        c.execute('INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)',
            (pid,'owned-active',view['id'],'ADVANCE',digest({'real':'owner'}),'PROCESSING',None,None))
    data=client.app.state.settings.data_dir
    result=asyncio.run(call_discovery_tool(ROOT,data,pid,'discovery_graph',{'session_id':view['id'],'graph_id':'architecture'}))
    assert not result.is_error
    assert view['id'] in str(result) and 'architecture' in str(result)
    with client.app.state.store.connect() as c:
        assert c.execute("SELECT status FROM discovery_operations WHERE operation_id='owned-active'").fetchone()[0]=='PROCESSING'
        assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0
    other=project(client)
    denied=asyncio.run(call_discovery_tool(ROOT,data,other,'discovery_graph',{'session_id':view['id'],'graph_id':'architecture'}))
    assert denied.is_error
