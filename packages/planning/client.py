"""Application-native stdio client using the original MCP SDK."""
import sys
from pathlib import Path

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


async def _call(source_root,data_dir,project_id,tool,arguments,module):
    arguments_tail=['--data-dir',str(data_dir),'--project-id',project_id,'--source-root',str(source_root)]
    if (Path(source_root)/'PACKAGE_MANIFEST.json').is_file():
        from scripts.native_package_bootstrap import python_module_command
        command=python_module_command(source_root,module,arguments_tail)
    else:
        command=[sys.executable,'-B','-m',module,*arguments_tail]
    parameters=StdioServerParameters(command=command[0],args=command[1:],
        cwd=str(source_root))
    async with stdio_client(parameters) as (reader,writer):
        async with ClientSession(reader,writer) as client:
            await client.initialize()
            listed=await client.list_tools()
            if tool not in {t.name for t in listed.tools}:raise ValueError('Tool is not in the project planning toolkit')
            return await client.call_tool(tool,arguments)


async def call_planning_tool(source_root,data_dir,project_id,tool,arguments):
    return await _call(source_root,data_dir,project_id,tool,arguments,'packages.planning.mcp_server')


async def call_discovery_tool(source_root,data_dir,project_id,tool,arguments):
    return await _call(source_root,data_dir,project_id,tool,arguments,'packages.planning.discovery_mcp_server')
