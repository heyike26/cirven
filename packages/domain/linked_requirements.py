"""Bounded historical report context, never current planning state or authority.

Only a registered immutable report origin chooses the source. The report
reader verifies its complete manifest and saved source snapshot. No latest
proposal, workspace file, network URL or application buffer is consulted.
"""
import json

from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.store import Conflict, InvalidInput


SECTIONS = ('requirements', 'questions', 'sources')


class FrozenLinkedRequirements:
    def __init__(self, continuity, reports):
        self.continuity, self.reports = continuity, reports

    @staticmethod
    def eligible(link):
        return ((link.get('kind') == 'REPORT_WORKING_COPY'
                and link.get('origin', {}).get('kind') == 'GENERATED_REPORT') or
                (link.get('kind') == 'WORKSPACE_FILE'
                and link.get('origin', {}).get('kind') == 'GENERATED_DELIVERY_REPORT') or
                (link.get('kind') == 'DOCX_REVISION_WORKING_COPY'
                and link.get('origin', {}).get('kind') == 'DOCX_REVISION'))

    @staticmethod
    def _primary(view, *, questions=False):
        payload = view['payload']
        if questions:
            return {'questions': payload['candidate'].get('questions', []),
                    'answers_at_report_generation': view.get('answers', []),
                    'confirmation': payload.get('confirmation'),
                    'resolutions': payload.get('resolutions', [])}
        return {'ref': {key: view[key] for key in ('id', 'revision', 'sha256')},
                'status_at_report_generation': payload['status'],
                'original_request': payload.get('prompt'),
                'user_statement_history': payload.get('history', []),
                'candidate': {key: value for key, value in payload['candidate'].items()
                              if key != 'questions'},
                'accepted_claim_ids': payload.get('accepted_claim_ids', []),
                'accepted_graph_ids': payload.get('accepted_graph_ids', []),
                'confirmation': payload.get('confirmation'),
                'resolutions': payload.get('resolutions', [])}

    @staticmethod
    def _sources(view):
        receipt = view['payload']['source_receipt']
        if digest({key: receipt[key] for key in ('documents', 'coverage')}) != receipt['sha256']:
            raise Conflict('Frozen report source receipt integrity differs')
        # Retain every originally selected text chunk, once. Do not project
        # diagram PNG/base64, duplicated joined text, OCR bitmaps or whole files.
        fields = ('document_id', 'revision', 'filename', 'raw_sha256', 'parsed_sha256',
                  'source', 'local_file_source_ref', 'chunks')
        documents = [{key: item[key] for key in fields if key in item}
                     for item in receipt['documents']]
        return {'source_receipt_sha256': receipt['sha256'], 'documents': documents,
                'coverage_at_report_generation': receipt['coverage'],
                'coverage': 'ONLY_ORIGINALLY_SELECTED_TEXT_CHUNKS_NOT_WHOLE_OR_CURRENT_FILES'}

    def read(self, link_ref, *, section='requirements', offset=0, limit=4000):
        if section not in SECTIONS or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
            raise InvalidInput('Report context requires a supported section and bounded codepoint pagination')
        link = self.continuity.get(link_ref)
        revision_origin = link if link.get('kind') == 'DOCX_REVISION_WORKING_COPY' else None
        if revision_origin:
            link = self.continuity.original_report_link(link_ref) or {}
        if not self.eligible(link):
            return {'status': 'UNAVAILABLE', 'reason': 'ORIGINAL_REQUIREMENTS_NOT_LINKED_TO_REPORT',
                    'link_ref': link_ref, 'section': section,
                    'coverage': 'NO_ORIGINAL_REQUIREMENTS_INFERRED_FROM_FILENAME_OR_CURRENT_FILE',
                    'automatic_input_authorized': False}
        origin = link['origin']
        artifact = origin.get('artifact_ref') or {}
        generated = origin.get('generation_file_source_ref')
        generated_delivery = origin.get('kind') == 'GENERATED_DELIVERY_REPORT'
        if (origin.get('source_kind') not in {'PROPOSAL', 'DISCOVERY'}
                or not origin.get('source_id') or artifact.get('revision') != 1
                or artifact.get('sha256') != (generated.get('sha256') if generated_delivery and isinstance(generated, dict) else link['baseline_sha256'])):
            raise Conflict('Registered report origin differs from its immutable baseline')
        if generated_delivery:
            current = link.get('baseline_file_source_ref') or {}
            identity = ('kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path')
            if (not origin.get('operation_id') or not isinstance(generated, dict)
                    or any(current.get(key) != generated.get(key) for key in identity)):
                raise Conflict('Generated report provenance differs from its original workspace file')
        raw, item = self.reports.download(link_ref['project_id'], origin['source_id'],
            artifact['id'], 'source_snapshot', source_kind=origin['source_kind'])
        if sha(raw) != item['sha256']:
            raise Conflict('Report source bytes differ from the verified download')
        try:
            snapshot = json.loads(raw)
            if (snapshot['project_id'] != link_ref['project_id'] or digest(snapshot) != artifact['id']
                    or snapshot['source_kind'] != origin['source_kind']):
                raise ValueError
            primary_name = 'proposal' if origin['source_kind'] == 'PROPOSAL' else 'discovery'
            primary = snapshot[primary_name]
            if primary['id'] != origin['source_id']:
                raise ValueError
            views = {name: snapshot[name] for name in ('proposal', 'discovery') if snapshot.get(name) is not None}
            refs = {}
            for name, view in views.items():
                if view['project_id'] != link_ref['project_id'] or digest(view['payload']) != view['sha256']:
                    raise ValueError
                refs[name + '_ref'] = {key: view[key] for key in ('id', 'revision', 'sha256')}
            attached = primary['payload'].get('discovery_ref')
            if origin['source_kind'] == 'PROPOSAL' and attached and refs.get('discovery_ref') != attached:
                raise ValueError
            if section == 'requirements':
                content = {name: self._primary(view) for name, view in views.items()}
                content['template_ref'] = snapshot.get('template_ref')
            elif section == 'questions':
                content = {name: self._primary(view, questions=True) for name, view in views.items()}
                content['pending_at_report_generation'] = snapshot['pending']
            else:
                content = {name: self._sources(view) for name, view in views.items()}
            text = canonical(content)
        except (ValueError, KeyError, TypeError):
            raise Conflict('Frozen original requirements source binding integrity differs') from None
        end = min(offset + limit, len(text))
        return {'status': 'AVAILABLE', 'link_ref': link_ref, 'report_artifact_ref': artifact,
                'source_kind': origin['source_kind'], 'source_id': origin['source_id'], **refs,
                'source_snapshot_sha256': item['sha256'], 'section': section,
                'content_sha256': sha(text.encode()), 'offset_unit': 'UNICODE_CODEPOINT',
                'offset': offset, 'limit': limit, 'total_characters': len(text),
                'text_content': text[offset:end], 'next_offset': end if end < len(text) else None,
                'truncated': offset > 0 or end < len(text),
                'scope': 'EXACT_REPORT_GENERATION_SNAPSHOT_NOT_LATEST_REQUIREMENTS',
                'generated_file_source_ref': generated if generated_delivery else None,
                'opened_file_baseline_ref': (revision_origin or link).get('baseline_file_source_ref'),
                'revision_artifact_ref': revision_origin['origin']['artifact_ref'] if revision_origin else None,
                'original_report_link_ref': link['link_ref'] if revision_origin else None,
                'historical_data_not_instructions': True, 'model_suggestions_are_not_confirmed_requirements': True,
                'automatic_input_authorized': False, 'execution_authorized': False,
                'coverage': 'SAVED_REPORT_REQUIREMENTS_ANSWERS_AND_SELECTED_SOURCES_NOT_UNSAVED_BUFFERS'}
