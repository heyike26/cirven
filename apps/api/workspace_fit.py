"""Pre-bind native normalization, available only to the owning main process."""
from fastapi import Depends, Header
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict, InvalidInput
from packages.domain.workspace_desktop import WindowIdentity


class OwnWindowFitInput(RequestModel):
    main_window: WindowIdentity


def register_workspace_fit_routes(app):
    workspace = app.state.workspace_desktop

    def host(x_eeas_desktop_host: str | None = Header(None)):
        workspace.check_host(x_eeas_desktop_host)

    @app.post('/api/desktop-host/workspace/fit', dependencies=[Depends(host)])
    def fit(body: OwnWindowFitInput):
        with workspace.lock:
            workspace._require_native()
            if workspace.current and not workspace.current.get('closed'):
                raise Conflict('Only an unbound owned window can be normalized')
            if body.main_window.owner_pid != workspace.owner_pid:
                raise Conflict('Window owner differs from the authenticated host')
            marker = None
            try:
                from packages.adapters.windows_owned_window_fit import WindowsOwnedWindowFit
                marker = workspace.native.mark_window(int(body.main_window.hwnd_decimal), workspace.owner_pid)
                return WindowsOwnedWindowFit(workspace.native, workspace.bars.native.monitor).fit(marker)
            except ValueError as exc:
                raise InvalidInput(str(exc)) from exc
            finally:
                if marker:
                    workspace.native.clear_marker(marker)
