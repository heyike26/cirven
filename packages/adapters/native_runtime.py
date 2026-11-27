"""Select one native runtime for new execution, retaining historical read access."""
import os

from packages.adapters.claude_sdk import ClaudeBridge
from packages.domain.store import Conflict


class OpenCodeRuntimeSelection:
    def __init__(self, service, data_dir):
        from packages.adapters.opencode import OpenCodeBridge
        self.service = service
        self.selected = OpenCodeBridge(service, data_dir)
        self.historical_claude = ClaudeBridge(service, data_dir)

    def __getattr__(self, name):
        return getattr(self.selected, name)

    def _reader(self, project_id, execution_id):
        row = self.selected.inspect_record(project_id, execution_id)
        if row["runtime"] == "opencode-http":
            return self.selected
        if row["runtime"] == "claude-agent-sdk":
            return self.historical_claude
        raise Conflict("Execution runtime is not an installed native adapter")

    def inspect(self, project_id, execution_id):
        return self._reader(project_id, execution_id).inspect(project_id, execution_id)

    def stream_events(self, project_id, execution_id, after=0):
        return self._reader(project_id, execution_id).stream_events(project_id, execution_id, after)

    def export_result(self, project_id, execution_id):
        return self._reader(project_id, execution_id).export_result(project_id, execution_id)


def create_runtime_bridge(service, data_dir):
    runtime = os.environ.get("EEAS_NATIVE_RUNTIME", "opencode-http")
    if runtime == "claude-agent-sdk":
        return ClaudeBridge(service, data_dir)
    if runtime == "opencode-http":
        return OpenCodeRuntimeSelection(service, data_dir)
    raise ValueError("EEAS_NATIVE_RUNTIME must select one installed runtime")
