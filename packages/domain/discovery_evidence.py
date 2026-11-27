"""Host-resolved exact fact citations; never semantic approval or native edits.

Only missing graph anchors are copied from every explicitly named SOURCE_FACT
claim after the existing exact-source validator succeeds. The original native
candidate stays immutable; the caller still runs complete candidate validation.
"""
from copy import deepcopy
from types import SimpleNamespace

from packages.domain.discovery_schema import DiscoveryClaim
from packages.domain.intake import canonical, digest
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.store import Conflict, InvalidInput

MAX_BYTES = 2 * 1024 * 1024


def resolve_graph_evidence(raw_candidate, source_receipt):
    """Return a separate candidate and hash-bound host derivation receipt.

    Missing means an absent field or actual empty list. Direct anchors and
    non-factual graph elements remain unchanged. Unknown/mixed/ambiguous bases,
    malformed anchors and capacity overflow fail, without dropping evidence.
    """
    if not isinstance(raw_candidate, dict) or len(canonical(raw_candidate).encode()) > MAX_BYTES:
        raise InvalidInput('Discovery evidence requires a candidate object within 2 MiB')
    if (not isinstance(source_receipt, dict) or not {'documents', 'coverage', 'sha256'} <= source_receipt.keys()
            or digest({k: source_receipt[k] for k in ('documents', 'coverage')}) != source_receipt['sha256']):
        raise Conflict('Discovery evidence source receipt integrity differs')
    claims = raw_candidate.get('claims', [])
    if not isinstance(claims, list):
        raise InvalidInput('Discovery claims must be an explicit bounded list')
    by_id = {}
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get('id'), str):
            raise InvalidInput('Discovery evidence requires explicit claim identities')
        if claim['id'] in by_id:
            raise InvalidInput('Ambiguous duplicate claim identities cannot supply diagram evidence')
        by_id[claim['id']] = claim
    result = deepcopy(raw_candidate)
    changes, validated = [], {}
    graphs = result.get('graphs', [])
    if not isinstance(graphs, list):
        raise InvalidInput('Discovery graphs must be an explicit bounded list')
    for graph in graphs:
        if not isinstance(graph, dict):
            raise InvalidInput('Discovery graph must be an object')
        for collection, kind in (('nodes', 'NODE'), ('edges', 'EDGE')):
            elements = graph.get(collection, [])
            if not isinstance(elements, list):
                raise InvalidInput('Discovery graph elements must be a list')
            for element in elements:
                if not isinstance(element, dict):
                    raise InvalidInput('Discovery graph element must be an object')
                anchors = element.get('source_anchors', [])
                if element.get('origin') != 'SOURCE_FACT' or not isinstance(anchors, list) or anchors:
                    continue
                ids = element.get('claim_ids')
                if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(i, str) for i in ids):
                    raise InvalidInput('Unanchored fact diagram element needs explicit fact claim references')
                unique_ids = list(dict.fromkeys(ids))
                if any(i not in by_id or by_id[i].get('origin') != 'SOURCE_FACT' for i in unique_ids):
                    raise InvalidInput('Diagram fact evidence cannot be inherited from unknown or non-factual claims')
                inherited, seen, source_claims = [], set(), []
                for ident in unique_ids:
                    if ident not in validated:
                        fact = DiscoveryClaim.model_validate(by_id[ident])
                        PlanningProposals._validate_sources(SimpleNamespace(nodes=[fact], questions=[]), source_receipt)
                        validated[ident] = fact
                    for anchor in validated[ident].source_anchors:
                        item = anchor.model_dump(); key = canonical(item)
                        if key not in seen:
                            seen.add(key); inherited.append(item)
                            if len(inherited) > 32:
                                raise InvalidInput('Inherited diagram evidence exceeds 32 anchors; nothing was dropped')
                    source_claims.append({'claim_id': ident, 'claim_sha256': digest(by_id[ident]),
                        'source_anchors_sha256': digest(by_id[ident]['source_anchors'])})
                if not inherited:
                    raise InvalidInput('Fact claims must supply actual selected source anchors')
                before = digest(element)
                element['source_anchors'] = inherited
                changes.append({'graph_id': graph.get('id'), 'element_kind': kind, 'element_id': element.get('id'),
                    'raw_element_sha256': before, 'resolved_element_sha256': digest(element),
                    'source_claims': source_claims, 'inherited_source_anchors_sha256': digest(inherited),
                    'inherited_anchor_count': len(inherited)})
    receipt = {'schema_version': 'eeas.discovery-graph-evidence.v1',
        'status': 'RESOLVED' if changes else 'NOT_REQUIRED',
        'rule': 'ALL_REFERENCED_SOURCE_FACT_CLAIMS_WITH_EXACT_VALIDATED_ANCHORS',
        'raw_candidate_sha256': digest(raw_candidate), 'resolved_candidate_sha256': digest(result),
        'source_receipt_sha256': source_receipt['sha256'], 'elements': changes,
        'raw_native_result_modified': False, 'semantic_verification': 'HUMAN_REVIEW_REQUIRED',
        'requirements_confirmed': False, 'execution_authorized': False}
    if len(canonical(receipt).encode()) > MAX_BYTES:
        raise InvalidInput('Diagram evidence receipt exceeds 2 MiB; original result is retained')
    return result, receipt
