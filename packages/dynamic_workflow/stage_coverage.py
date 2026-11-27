"""Project the requirements coverage supported by a workflow's stored records."""
from collections import Counter, defaultdict
from datetime import datetime

from packages.domain.store import NotFound
from packages.dynamic_workflow.stage_assertions import (
    DevelopmentEvidence, MANUAL_METHOD, action_receipts, case_receipt_issues, passing_check,
)


def _rows(stage):
    return (stage.get("artifact") or {}).get("items", [])


def _start(stage):
    # A failed or interrupted attempt may leave the previous display artifact.
    # Only link from the successful operation that owns the current artifact.
    if stage["status"] not in ("review", "approved") or stage.get("error"):
        return None
    operation = stage.get("operation_id")
    if not operation:
        return None
    events = [entry for entry in stage["log"] if entry.get("operation_id") == operation]
    manual = next((entry for entry in events if entry["event"] == "manual_results_started"), None)
    if manual:
        return manual
    if not any(entry["event"] == "finished" and entry.get("status") == "review"
               and not entry.get("error") for entry in events):
        return None
    return next((entry for entry in events if entry["event"] == "started"), None)


def _instant(value):
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.utcoffset() is not None:
                return stamp
        except ValueError:
            pass
    return None


class StageTrace:
    """Join output to the approved versions consumed at the start of its operation."""

    def __init__(self, run, archives, digest, visible=None):
        self.nodes = {node["id"]: node for node in run["definition"]["nodes"]}
        self.live = {stage["node_id"]: stage for stage in run["stages"]}
        self.archived = {stage["stage_session_id"] for stage in archives}
        self.versions = defaultdict(list)
        self.retired = {}
        self.sha = {}
        self.input_cache = {}
        self.dependent_cache = {}
        self.reference_cache = {}
        self.row_counts = {}
        self.case_results = {}
        self.test_evidence = {}
        self.development_results = {}
        self.development_evidence = {}
        self.visible = visible or (lambda session: True)
        self.uncertain = False
        for stage in (*archives, *run["stages"]):
            session = stage.get("stage_session_id")
            for entry in stage["log"]:
                if entry["event"] in ("revised", "rework") and (previous := entry.get("previous_session_id")):
                    self.retired[previous] = _instant(entry.get("at"))
            if session:
                self.versions[stage["node_id"]].append(stage)
                if stage["status"] == "approved" and stage.get("approval_sha256") and stage.get("artifact"):
                    self.sha[session] = digest(stage["artifact"])

    def target(self, stage, row):
        node = self.nodes[stage["node_id"]]
        archived = stage["stage_session_id"] in self.archived
        version = 1 + sum(entry["event"] in ("rework", "revised") for entry in stage["log"])
        return {"node_id": node["id"], "stage_name": node["name"], "version": version, "archived": archived,
                "stage_session_id": stage["stage_session_id"], "operation_id": stage.get("operation_id"),
                "row_id": row["id"], "title": row["title"]}

    def _resolve(self, start, node_id):
        sha = start.get("inputs", {}).get(node_id)
        if not sha:
            return None
        candidates = [stage for stage in self.versions[node_id] if self.sha.get(stage["stage_session_id"]) == sha]
        if "input_versions" in start:
            pin = start["input_versions"].get(node_id)
            return next((s for s in candidates if pin == {"stage_session_id": s["stage_session_id"],
                         "approval_sha256": s["approval_sha256"]}), None)
        # Legacy records identify content. Use a recorded approval/retirement
        # interval when identical content exists in multiple stage sessions.
        at = _instant(start.get("at"))
        possible = []
        for stage in candidates:
            session = stage["stage_session_id"]
            approved = next((_instant(entry.get("at")) for entry in reversed(stage["log"])
                             if entry["event"] == "approve"), None)
            retired = self.retired.get(session)
            if at is not None and ((approved is not None and approved > at) or
                                   (retired is not None and retired <= at)):
                continue
            possible.append(stage)
        return possible[0] if len(possible) == 1 else None

    def inputs(self, stage):
        session = stage["stage_session_id"]
        if session not in self.input_cache:
            start = _start(stage)
            self.input_cache[session] = {key: self._resolve(start, key)
                for key in self.nodes[stage["node_id"]]["inputs"]} if start else {}
        return self.input_cache[session]

    def dependents(self, source, kind, input_kind, historical):
        key = (source["stage_session_id"], kind, input_kind, historical)
        if key not in self.dependent_cache:
            prior = self.uncertain
            self.uncertain = False
            links = list(self._dependents(source, kind, input_kind, historical))
            self.dependent_cache[key] = links, self.uncertain
            self.uncertain = prior
        links, uncertain = self.dependent_cache[key]
        self.uncertain |= uncertain
        return links

    def _dependents(self, source, kind, input_kind, historical):
        source_id, session = source["node_id"], source["stage_session_id"]
        if session not in self.sha:
            return
        for node in self.nodes.values():
            if node["kind"] != kind or source_id not in node["inputs"]:
                continue
            candidates = self.versions[node["id"]] if historical else [self.live[node["id"]]]
            for stage in candidates:
                if not stage.get("stage_session_id") or not self.visible(stage["stage_session_id"]):
                    continue
                inputs = self.inputs(stage)
                peer_ids = [key for key in node["inputs"] if self.nodes[key]["kind"] == input_kind]
                chosen = inputs.get(source_id)
                if chosen is None or any(inputs.get(key) is None for key in peer_ids):
                    start = _start(stage)
                    if start and start.get("inputs", {}).get(source_id) == self.sha[session]:
                        self.uncertain = True
                    continue
                if chosen["stage_session_id"] != session:
                    continue
                ids = Counter(row["id"] for key in peer_ids for row in _rows(inputs[key]))
                yield stage, ids

    def consuming_nodes(self, sources, kind):
        return {node["id"] for node in self.nodes.values()
                if node["kind"] == kind and sources.intersection(node["inputs"])}

    def count(self, stage, ident):
        session = stage["stage_session_id"]
        if session not in self.row_counts:
            self.row_counts[session] = Counter(row["id"] for row in _rows(stage))
        return self.row_counts[session][ident]

    def references(self, stage, field, ident):
        key = (stage["stage_session_id"], field)
        if key not in self.reference_cache:
            refs = defaultdict(list)
            for row in _rows(stage):
                value = row.get(field, [])
                for name in ([value] if isinstance(value, str) else value):
                    refs[name].append(row)
            self.reference_cache[key] = refs
        return self.reference_cache[key].get(ident, ())

    def test_records(self, stage, result):
        session = stage["stage_session_id"]
        if session not in self.test_evidence:
            by_case, by_id = defaultdict(list), {}
            for record in stage["verified_evidence"]:
                by_case[record.get("case_id")].append(record)
                by_id[record["id"]] = record
            self.test_evidence[session] = by_case, by_id
        by_case, by_id = self.test_evidence[session]
        case = result["case_id"]
        # Keep the full history for this case. Other references still need to
        # be recognized by ID, but cannot contribute a passing case check.
        other = [by_id[ref] for ref in result.get("evidence_refs", [])
                 if ref in by_id and by_id[ref].get("case_id") != case]
        return [*by_case.get(case, ()), *other]

    def implementation_records(self, stage):
        session = stage["stage_session_id"]
        if session not in self.development_evidence:
            upstream = [record for value in self.inputs(stage).values() if value
                        for record in value.get("verified_evidence", [])]
            self.development_evidence[session] = DevelopmentEvidence(stage.get("verified_evidence", []), upstream)
        return self.development_evidence[session]


def _unique_rows(trace, stage, rows, response):
    for row in rows:
        if trace.count(stage, row["id"]) != 1:
            response["ambiguous"] = True
            continue
        yield row


def _implementation_trace(trace, source, ident, historical, response, groups):
    def linked(stage_kind, field, parent, parent_kind, parent_id, match):
        for stage, ids in trace.dependents(parent, stage_kind, parent_kind, historical):
            rows = (row for row in trace.references(stage, field, parent_id) if match(row))
            for row in _unique_rows(trace, stage, rows, response):
                if ids[parent_id] != 1:
                    response["ambiguous"] = True
                    continue
                yield stage, row

    def status(stage):
        return "approved" if stage["status"] == "approved" else "review"

    output = {kind: [] for kind in groups}
    decisions = set()
    for stage, row in linked("alternatives","requirement_ids",source,"requirements",ident,
                             lambda row: row.get("level")==3):
        decisions.add((stage["stage_session_id"], row["id"]))
        output["alternatives"].append({**trace.target(stage,row), "status":status(stage)})

    for stage, row in linked("design","requirement_ids",source,"requirements",ident,lambda _: True):
        inputs = [value for key, value in trace.inputs(stage).items()
                  if trace.nodes[key]["kind"] == "alternatives"]
        # A design could name a genuine decision from a different version or OR.
        # Check the exact input versions of this design before drawing the edge.
        if any(value is None for value in inputs):
            trace.uncertain = True
            continue
        basis = []
        for ref in row.get("decision_ids", []):
            versions = [value for value in inputs if trace.count(value, ref)]
            if sum(trace.count(value, ref) for value in versions) > 1:
                response["ambiguous"] = True
            elif versions and (versions[0]["stage_session_id"], ref) in decisions:
                basis.append(ref)
        if not basis:
            continue
        output["design"].append({**trace.target(stage,row), "status":status(stage), "basis_ids":basis})
        for dev, report in linked("development","design_ids",stage,"design",row["id"],lambda _: True):
            key = (dev["stage_session_id"], report["id"], stage["stage_session_id"], row["id"])
            if key not in trace.development_results:
                evidence = trace.implementation_records(dev)
                issue = evidence.completion_issue(row["id"],set(report.get("evidence_refs",[])),design=row)
                if not issue and row.get("build"):
                    issue = evidence.build_issue(row["id"],row["build"])
                trace.development_results[key] = report.get("status")=="done" and not issue
            verified = trace.development_results[key]
            output["development"].append({**trace.target(dev,report), "basis_ids":[row["id"]],
                "status":"registered" if verified and dev["status"]=="approved"
                         else "registered_review" if verified else "unverified"})
    return output


def _case_result(case, result, stage, evidence_issues, records):
    status = result["status"]
    if status == "passed":
        if case["id"] in stage.get("test_input_issues", {}):
            return "unverified"
        reported = {"items": [result]}
        cases = {"cases": {"items": [case]}}
        if not (case_receipt_issues(reported, cases, records) or evidence_issues("testing", reported, records)):
            return "passed"
        return "unverified"
    return {"failed": "reported_failed", "blocked": "blocked"}.get(status, "not_executed")


def _criterion_scenarios(trace, requirement_id, implementation, response):
    scenarios = defaultdict(list)
    for target in implementation.get("design", []):
        stage = next(s for s in trace.versions[target["node_id"]]
                     if s["stage_session_id"] == target["stage_session_id"])
        row = next(r for r in _rows(stage) if r["id"] == target["row_id"])
        inputs = [plan for key, plan in trace.inputs(stage).items() if trace.nodes[key]["kind"] == "cases"]
        if any(plan is None for plan in inputs):
            trace.uncertain = True
            continue
        for scenario in _unique_scenarios(row, requirement_id, response):
            case_id = scenario["case_id"]
            plans = [plan for plan in inputs if trace.count(plan, case_id)]
            if sum(trace.count(plan, case_id) for plan in plans) != 1:
                response["ambiguous"] |= bool(plans)
                continue
            key = (plans[0]["stage_session_id"], case_id, scenario["action_id"], scenario["criterion"])
            scenarios[key].append({**target, "scenario_id": scenario["id"],
                                   "path_kind": scenario["path_kind"], "condition": scenario["condition"]})
    return scenarios


def _unique_scenarios(row, requirement_id, response):
    scenarios = row.get("behavior_scenarios", [])
    counts = Counter(s["id"] for s in scenarios)
    for scenario in scenarios:
        if scenario["requirement_id"] == requirement_id:
            if counts[scenario["id"]] == 1:
                yield scenario
            else:
                response["ambiguous"] = True


def _action_proof(record, stage, case, action, result, target):
    verdict = "missing"
    if record:
        if record["status"] in ("failed", "blocked"):
            verdict = record["status"]
        elif record["id"] not in result.get("evidence_refs", []) or not passing_check(record):
            verdict = "unconfirmed"
        elif any(issue.startswith(f"{case['id']} / {action['id']} ")
                 for issue in stage.get("test_input_issues", {}).get(case["id"], [])):
            verdict = "stale"
        else:
            verdict = "verified"
    return {**target, "proof": verdict, **({"receipt_id": record["id"],
            "at": record.get("recorded_at") or record.get("at")} if record else {})}


def _criteria(trace, row, requirement, cases, response):
    criteria = [{"index": index, "text": text, "actions": []}
                for index, text in enumerate(row.get("acceptance", []), 1)]
    if not criteria:
        return criteria
    scenarios = _criterion_scenarios(trace, row["id"], requirement.get("implementation", {}), response)
    for plan, case, case_target, results in cases:
        actions = case.get("actions", [])
        counts = Counter(action["id"] for action in actions)
        refs = {(r["criterion"], r["action_id"]) for r in case.get("acceptance_refs", [])
                if r["requirement_id"] == row["id"]}
        for action in actions:
            if counts[action["id"]] != 1:
                response["ambiguous"] = True
                continue
            for criterion in criteria:
                index = criterion["index"]
                key = (plan["stage_session_id"], case["id"], action["id"], index)
                design = scenarios.get(key, [])
                explicit = (index, action["id"]) in refs
                if not explicit and (refs or len(criteria) != 1 or (len(actions) != 1 and
                        not any(link["status"] == "approved" for link in design))):
                    continue
                proofs = [_action_proof(receipts.get(action["id"]), stage, case, action, result, target)
                          for stage, result, target, receipts in results]
                criterion["actions"].append({"id": action["id"], "method": action["method"],
                    "binding": "explicit" if explicit else "single" if len(actions) == 1 else "design",
                    "case": case_target, "scenarios": design, "results": proofs})
    return criteria


def requirement_coverage(run, source, selector, ident, *, historical, digest, evidence_issues,
                         archives=(), visible=None):
    trace = StageTrace(run, archives, digest, visible)
    by_source = selector == "ur"
    rows = _rows(source)
    originals = source.get("source_urs", {})
    follow_up = next((item for item in source.get("follow_ups", []) if item.get("source_id") == ident), None)
    if by_source:
        if ident not in originals and follow_up is None:
            raise NotFound("此版本中找不到该原始需求")
        if follow_up and follow_up.get("withdrawn_at"):
            rows = []
        else:
            rows = [row for row in rows if ident in row.get("source_ur", [])]
        origin_ids = [ident]
    else:
        rows = [row for row in rows if row["id"] == ident]
        if not rows:
            raise NotFound("此版本中找不到该包需求")
        origin_ids = list(dict.fromkeys(key for row in rows for key in row.get("source_ur", [])))

    sources = [{"id": key, "text": follow_up["text"] if by_source and follow_up else originals.get(key)}
               for key in origin_ids]
    response = {"historical": historical, "sources": sources,
                "withdrawn": bool(by_source and follow_up and follow_up.get("withdrawn_at")),
                "ambiguous": False, "requirements": []}
    _requirement_rows(trace, source, rows, historical, evidence_issues, response, include_criteria=True)
    return {**response, "version_uncertain": trace.uncertain}


def _coverage_nodes(trace, source):
    nodes = {kind: trace.consuming_nodes({source["node_id"]}, kind)
             for kind in ("alternatives", "design", "cases")}
    nodes["development"] = trace.consuming_nodes(nodes["design"], "development")
    nodes["testing"] = trace.consuming_nodes(nodes["cases"], "testing")
    return nodes


def _requirement_rows(trace, source, rows, historical, evidence_issues, response, groups=None, *,
                      include_criteria=False):
    plans = list(trace.dependents(source, "cases", "requirements", historical))
    if groups is None:
        nodes = _coverage_nodes(trace, source)
        groups = [kind for kind in ("alternatives", "design", "development") if nodes[kind]]

    for row in rows:
        if trace.count(source, row["id"]) != 1:
            response["ambiguous"] = True
            continue
        requirement = {**trace.target(source, row), "approved": source["status"] == "approved", "cases": []}
        if groups:
            requirement["implementation"] = _implementation_trace(trace,source,row["id"],historical,response,groups)
        response["requirements"].append(requirement)
        criterion_cases = []
        for stage, sources_per_id in plans:
            cases = trace.references(stage, "requirement_ids", row["id"])
            if not cases:
                continue
            if sources_per_id[row["id"]] != 1:
                response["ambiguous"] = True
                continue
            results = trace.dependents(stage, "testing", "cases", historical)
            for case in cases:
                if trace.count(stage, case["id"]) != 1:
                    response["ambiguous"] = True
                    continue
                case_target = {**trace.target(stage, case), "approved": stage["status"] == "approved"}
                case_info = {**case_target, "results": []}
                requirement["cases"].append(case_info)
                criterion_results = []
                for result_stage, cases_per_id in results:
                    rows_for_case = trace.references(result_stage, "case_id", case["id"])
                    if not rows_for_case:
                        continue
                    if cases_per_id[case["id"]] != 1 or len(rows_for_case) != 1:
                        response["ambiguous"] = True
                        continue
                    result = rows_for_case[0]
                    if trace.count(result_stage, result["id"]) != 1:
                        response["ambiguous"] = True
                        continue
                    source_kinds = {a["method"] == MANUAL_METHOD for a in case.get("actions", [])}
                    result_key = (stage["stage_session_id"], result_stage["stage_session_id"], result["id"])
                    if result_key not in trace.case_results:
                        records = trace.test_records(result_stage, result)
                        trace.case_results[result_key] = _case_result(case, result, result_stage, evidence_issues, records)
                    result_info = {
                        **trace.target(result_stage, result),
                        "status": trace.case_results[result_key],
                        **({"basis": "mixed" if False in source_kinds else "manual"} if True in source_kinds else {}),
                    }
                    case_info["results"].append(result_info)
                    if include_criteria:
                        receipts = action_receipts(case, trace.test_records(result_stage, result))
                        criterion_results.append((result_stage, result, result_info, receipts))
                if include_criteria:
                    criterion_cases.append((stage, case, case_target, criterion_results))
        if include_criteria:
            requirement["criteria"] = _criteria(trace, row, requirement, criterion_cases, response)


# A missing or invalid slot wins over a draft or complete slot in the same phase.
_STATES = ("failed", "unverified", "unavailable", "stale", "missing", "review", "covered")
_PHASES = ("alternatives", "design", "development", "cases", "testing")


def _slot(trace, node_id, statuses):
    stage = trace.live[node_id]
    session = stage.get("stage_session_id")
    if session and not trace.visible(session):
        return "unavailable"
    if stage["status"] == "failed" or any(status in ("reported_failed", "blocked") for status in statuses):
        return "failed"
    if any(status == "unverified" for status in statuses):
        return "unverified"
    if not statuses or "not_executed" in statuses:
        if stage["status"] in ("pending", "ready", "running") and any(
                entry["event"] in ("rework", "revised") for entry in stage["log"]):
            return "stale"
        return "missing"
    return "covered" if stage["status"] == "approved" else "review"


def _phase_statuses(trace, requirement, configured):
    implementation = requirement.get("implementation", {})

    def direct(kind, links):
        for node_id in configured[kind]:
            yield _slot(trace, node_id, [link.get("status", "linked")
                                       for link in links if link["node_id"] == node_id])

    def downstream(kind, parents, links_for):
        occupied = set()
        for parent in parents:
            for child in trace.consuming_nodes({parent["node_id"]}, kind):
                occupied.add(child)
                yield _slot(trace, child, [link["status"] for link in links_for(parent)
                                           if link["node_id"] == child])
        for child in configured[kind] - occupied:
            yield _slot(trace, child, [])

    def development(design):
        return [link for link in implementation.get("development", [])
                if design["row_id"] in link["basis_ids"]
                and trace.inputs(trace.live[link["node_id"]]).get(design["node_id"]) is trace.live[design["node_id"]]]

    slots = {
        "alternatives": direct("alternatives", implementation.get("alternatives", [])),
        "design": direct("design", implementation.get("design", [])),
        "development": downstream("development", implementation.get("design", []), development),
        "cases": direct("cases", requirement.get("cases", [])),
        "testing": downstream("testing", requirement.get("cases", []), lambda case: case["results"]),
    }
    return {kind: min(slots[kind], key=_STATES.index) for kind in _PHASES if configured[kind]}


def _overview_row(trace, source, ident, title, selector, states):
    return {"node_id": source["node_id"], "stage_session_id": source["stage_session_id"],
            "stage_name": trace.nodes[source["node_id"]]["name"], "selector": selector,
            "row_id": ident, "title": " ".join(title.split())[:320], "states": states}


def coverage_overview(run, *, archives, digest, evidence_issues, visible):
    trace = StageTrace(run, archives, digest, visible)
    rows = []
    configured = set()
    source_counts = Counter()
    for source in trace.live.values():
        if trace.nodes[source["node_id"]]["kind"] != "requirements":
            continue
        nodes = _coverage_nodes(trace, source)
        configured.update(kind for kind in _PHASES if nodes[kind])
        session = source.get("stage_session_id")
        if not session:
            source_counts["pending"] += 1
            continue
        if not visible(session):
            source_counts["unavailable"] += 1
            continue
        if not source.get("artifact"):
            source_counts["pending"] += 1
        originals = source.get("source_urs", {}).copy()
        for item in source.get("follow_ups", []):
            if ident := item.get("source_id"):
                if item.get("withdrawn_at"):
                    originals.pop(ident, None)
                else:
                    originals[ident] = item["text"]
        for ident, title in originals.items():
            if not any(trace.count(source, row["id"]) == 1
                       for row in trace.references(source, "source_ur", ident)):
                rows.append(_overview_row(trace, source, ident, title, "ur", {"requirements": "missing"}))
        seen = set()
        groups = [kind for kind in ("alternatives", "design", "development") if nodes[kind]]
        for row in _rows(source):
            ident = row["id"]
            if ident in seen:
                continue
            seen.add(ident)
            trace.uncertain = False
            response = {"requirements": [], "ambiguous": False}
            _requirement_rows(trace, source, (row,), False, evidence_issues, response, groups)
            states = {"requirements": "covered" if source["status"] == "approved" else "review"}
            if response["ambiguous"] and not response["requirements"]:
                states["requirements"] = "unverified"
            if response["requirements"]:
                states.update(_phase_statuses(trace, response["requirements"][0], nodes))
            if response["ambiguous"] or trace.uncertain:
                states["trace"] = "unverified"
            rows.append(_overview_row(trace, source, ident, row["title"], "or", states))
    return {"revision": run["revision"], "phases": [kind for kind in _PHASES if kind in configured],
            "pending_sources": source_counts["pending"], "unavailable_sources": source_counts["unavailable"],
            "rows": rows}
