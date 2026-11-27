"""Private authenticated Main metadata stream; no generic callback or command."""
import anyio
from fastapi import Header, HTTPException, Request
from fastapi.responses import StreamingResponse


class CaptureFeedResponse(StreamingResponse):
    def __init__(self, lease):
        self.lease=lease
        super().__init__(lease.lines(),media_type='application/x-ndjson',headers={
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Accel-Buffering':'no'})

    async def __call__(self,scope,receive,send):
        try:
            # Explicit disconnect observation also for ASGI 2.4: idle metadata
            # streams must release their own lease without waiting for a frame.
            async with anyio.create_task_group() as tasks:
                async def stream():
                    await self.stream_response(send)
                    tasks.cancel_scope.cancel()
                tasks.start_soon(stream)
                await self.listen_for_disconnect(receive)
                tasks.cancel_scope.cancel()
        finally:self.lease.close()


def register_editor_capture_feed(app, sessions):
    @app.get('/api/desktop-host/editor-capture-events')
    async def events(request:Request,x_eeas_desktop_host:str|None=Header(None)):
        runtime=getattr(app.state,'native_collaboration_runtime',None)
        if runtime is None:raise HTTPException(503,detail='EDITOR_CAPTURE_CHANNEL_REQUIRED')
        runtime.authority.check_host(x_eeas_desktop_host)
        if request.query_params:raise HTTPException(422,detail='EDITOR_CAPTURE_FEED_PARAMETERS_NOT_ACCEPTED')
        return CaptureFeedResponse(sessions.editor_captures.broker.reserve())
