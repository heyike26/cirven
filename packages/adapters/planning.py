from typing import Protocol
from packages.domain.requirements import RequirementBundle


class PlanningAdapter(Protocol):
    async def extract(self, document: dict, context: dict | None = None) -> RequirementBundle: ...


class MockPlanner:
    """Deterministic original-fixture parser, explicitly not model extraction."""
    async def extract(self, document: dict, context: dict | None = None) -> RequirementBundle:
        requirements, acceptance, assumptions, questions = [], [], [], []
        ranges = sorted((part["char_start"], part["char_end"]) for part in context["chunks"]) if context else None
        offset = 0
        for number, physical_line in enumerate(document["content"].splitlines(keepends=True), 1):
            start, end = offset, offset + len(physical_line)
            offset = end
            if ranges is not None:
                covered = start
                for lo, hi in ranges:
                    if lo <= covered < hi:
                        covered = hi
                if covered < end:
                    continue  # Never turn a fragment in the middle of a line into a new REQ.
            line = physical_line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
            prefix, separator, text = line.partition(":")
            if not separator or not text.strip():
                continue
            prefix, text = prefix.strip().upper(), text.strip()
            if prefix == "REQ":
                requirements.append({"id": f"R{len(requirements)+1}", "text": text,
                    "source_refs": [{"document_id": document["id"], "revision": document["revision"],
                                     "sha256": document["sha256"], "line_start": number, "line_end": number}]})
            elif prefix == "ACCEPT":
                acceptance.append({"id": f"A{len(acceptance)+1}", "text": text})
            elif prefix == "ASSUME":
                assumptions.append(text)
            elif prefix == "QUESTION":
                questions.append(text)
        if not requirements or not acceptance:
            raise ValueError("Mock extraction needs REQ: and ACCEPT: lines; use manual input for other documents")
        for item in acceptance:
            item["requirement_ids"] = [r["id"] for r in requirements]
        return RequirementBundle(schema_version="eeas.requirements.v1", title=document["filename"],
                                 requirements=requirements, acceptance=acceptance,
                                 assumptions=assumptions, questions=questions, confirmed=False, origin="mock")
