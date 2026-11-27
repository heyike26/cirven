import asyncio
from typing import Annotated
from pydantic import Field
from packages.domain.questions import RequestModel
from packages.domain.workbench_feedback import NativeBitmap,LocalBitmap
from packages.domain.workbench_observations import WorkbenchObservations
from packages.adapters.offline_ocr import OfflineOCR


class OCRInput(RequestModel):
    bitmap_ref: Annotated[NativeBitmap|LocalBitmap,Field(discriminator='kind')]


def register_workbench_observation_routes(app,store,data_dir,source_root):
    ocr=OfflineOCR(source_root/'toolchain/ocr/MODELS.json',app.state.desktop_host.bitmap)
    service=WorkbenchObservations(store,data_dir,ocr,app.state.desktop_host.bitmap)
    app.state.offline_ocr=ocr;app.state.workbench_observations=service
    app.state.workbench_feedback.observation_authority=service
    app.state.discovery.observation_authority=service
    app.state.planning_proposals.observation_authority=service

    @app.get('/api/workbench/ocr/capabilities')
    def capabilities():return ocr.capabilities()

    @app.post('/api/projects/{pid}/workbench/ocr')
    async def recognize(pid:str,body:OCRInput):
        return await asyncio.to_thread(service.recognize,pid,body.bitmap_ref.model_dump())

    @app.get('/api/projects/{pid}/workbench/ocr/{ident}')
    def get(pid:str,ident:str):return service.get(pid,ident)
