from packages.domain.workbench_texts import WorkbenchTexts


def register_workbench_text_routes(app):
    service=WorkbenchTexts(app.state.planning_reports,app.state.discovery)
    app.state.workbench_texts=service;app.state.workbench_feedback.text_authority=service

    @app.get('/api/projects/{pid}/workbench/reports/{source_id}/{artifact_id}/text')
    def report_text(pid:str,source_id:str,artifact_id:str,source_kind:str='DISCOVERY'):
        return service.report(pid,source_id,artifact_id,source_kind)
