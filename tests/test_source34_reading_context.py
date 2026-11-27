"""Reading-context regressions; these do not assert native pagination.

Copy into R/tests beside test_source34_report_semantics.py. No private fixtures,
fixed workspace paths, internal renderer helpers, or native/model calls are used.
Prepared without executing tests; Root owns the unified validation run.
"""
from copy import deepcopy
import hashlib
from io import BytesIO

from docx import Document

from packages.domain.discovery_schema import DiscoveryCandidate
from packages.domain.local_workspaces import canonical
from packages.domain.planning_docx import render_docx
from packages.domain.planning_schema import PlanningCandidate, PlanningQuestion
from packages.domain.questions import QuestionAnchor
from test_source34_report_semantics import body, check, source


def _render_without_mutating(snapshot):
    before = deepcopy(snapshot)
    before_bytes = canonical(snapshot).encode("utf-8")
    raw, _ = render_docx(snapshot)
    check(snapshot == before, "Navigation must not rewrite business/source/state data")
    check(canonical(snapshot).encode("utf-8") == before_bytes,
          "The complete source snapshot must remain byte-canonical equivalent")
    return Document(BytesIO(raw))


def _set_nodes(snapshot, nodes):
    candidate = deepcopy(snapshot["proposal"]["payload"]["candidate"])
    candidate["nodes"] = nodes
    snapshot["proposal"]["payload"]["candidate"] = PlanningCandidate.model_validate(candidate).model_dump()
    snapshot["proposal"]["flow"]["nodes"] = [
        {"id": node["id"], "planning_status": "ACCEPTED_PLAN"} for node in nodes
    ]


def _section(document, title):
    paragraphs = document.paragraphs
    matches = [i for i, p in enumerate(paragraphs) if p.text == title]
    check(len(matches) == 1, "The complete primary heading must appear exactly once")
    start = matches[0]
    end = next((i for i in range(start + 1, len(paragraphs))
                if paragraphs[i].style.name in {"Heading 1", "Heading 2"}), len(paragraphs))
    return paragraphs[start + 1:end]


def _anchor(document_id, original):
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    return QuestionAnchor.model_validate({
        "document_id": document_id, "revision": 1, "raw_sha256": digest,
        "chunk_index": 0, "chunk_sha256": digest, "char_start": 0,
        "char_end": len(original), "quote_sha256": digest,
    }).model_dump()


def _sources(snapshot, records):
    receipt = snapshot["proposal"]["payload"]["source_receipt"]
    receipt["documents"] = [
        {"document_id": a["document_id"], "revision": 1, "filename": name,
         "source": "local-workspace:fixture", "raw_sha256": a["raw_sha256"]}
        for a, name in records
    ]
    receipt["coverage"] = [
        {"document_id": a["document_id"], "text_coverage": "FULL_PARSED_TEXT",
         "omitted_chunk_indexes": [], "warnings": []} for a, _ in records
    ]
    receipt["sha256"] = hashlib.sha256(canonical({
        key: value for key, value in receipt.items() if key != "sha256"
    }).encode("utf-8")).hexdigest()


def test_colliding_long_titles_have_distinct_bounded_navigation_and_exact_main_headings():
    snapshot = source()
    prefix = "跨页导航保留人工输入及工单来源与未决接受状态记录"
    check(len(prefix) == 24, "Fixture deliberately collides at the navigation title budget")
    titles = [prefix + "\n  现场复核任务甲：保留原文",
              prefix + "\t 现场复核任务乙：不覆盖人工补充"]
    base = snapshot["proposal"]["payload"]["candidate"]["nodes"][0]
    nodes = []
    for index, title in enumerate(titles, 1):
        node = deepcopy(base)
        node.update(id=f"T{index:02d}", title=title,
                    description=f'业务原文{index} {{"keep":"人工措辞不可改写"}}。',
                    inputs=[f"原输入{index}"], outputs=[f"交付原稿{index}"],
                    acceptance=[f"验收原句{index}"], capabilities=[f"已有能力{index}"],
                    risks=[f"原风险{index}"], depends_on=[] if index == 1 else ["T01"],
                    parent_id=None if index == 1 else "T01")
        nodes.append(node)
    _set_nodes(snapshot, nodes)
    document = _render_without_mutating(snapshot)
    for index, title in enumerate(titles, 1):
        section = _section(document, f"2.{index}  {title}")
        for label in ("输入", "交付物", "验收条件", "所需能力", "节点风险"):
            navigation = [p.text for p in section if p.text.startswith(label + " · ")]
            check(len(navigation) == 1, "Every continuation entry needs a local task context")
            check(f"2.{index}" in navigation[0] and prefix in navigation[0],
                  "Identical shortened titles remain distinguishable by their full document number")
            check("…" in navigation[0] and len(navigation[0]) <= len(label) + len(" · 2.1 ") + 25,
                  "Navigation stays short and marks omission; the full original belongs in the main heading")
        dependency = next(p.text for p in section if p.text.startswith("前置依赖"))
        check(f"2.{index}" in dependency and prefix in dependency,
              "The page-8 dependency entry must identify its current task")
        if index == 2:
            parent = next(p.text for p in section if p.text.startswith("所属节点"))
            check("2.2" in parent and prefix in parent)
        check(nodes[index - 1]["description"] in body(document), "Original business prose remains readable")


def test_supplement_claim_basis_and_acceptance_identify_the_local_category_and_claim():
    snapshot = source()
    claims = [
        {"id": "T04", "category": "REQUIREMENT", "text": '附录原文 {"keep":"不声称验证已开展"}。',
         "origin": "MODEL_SUGGESTION", "acceptance": ["原验收：六标题与末端均可定位。"]},
        {"id": "C04", "category": "CONSTRAINT", "text": "附录人工约束仍待执行授权。",
         "origin": "USER_STATEMENT", "acceptance": ["原验收：人工稿不被覆盖。"]},
    ]
    candidate = DiscoveryCandidate.model_validate({
        "title": "附录业务要求", "summary": "产生时原文仍完整留在来源快照。",
        "phase": "DEFINE", "claims": claims,
    }).model_dump()
    snapshot["discovery"] = {"revision": 2, "payload": {"status": "DRAFT", "candidate": candidate}}
    document = _render_without_mutating(snapshot)
    for context, claim, basis in (("需求 · T04", claims[0], "模型建议"),
                                  ("约束 · C04", claims[1], "用户陈述")):
        section = _section(document, context)
        basis_rows = [p.text for p in section if p.text.startswith("依据类型")]
        acceptance_rows = [p.text for p in section if p.text.startswith("验收条件")]
        check(len(basis_rows) == len(acceptance_rows) == 1)
        check(context in basis_rows[0] and basis in basis_rows[0],
              "A basis line at the top of a continuation page must identify its claim")
        check(context in acceptance_rows[0], "The appendix T04 acceptance entry needs its own identity")
        check(claim["text"] in body(document) and claim["acceptance"][0] in body(document))


def test_full_questions_and_local_pending_material_answer_context_survive_both_report_kinds():
    for kind in ("PROPOSAL", "DISCOVERY"):
        snapshot = source()
        questions = []
        for index in (1, 2):
            spec = PlanningQuestion.model_validate({
                "id": f"Q0{index}", "category": "DECISION", "blocking": False,
                "text": "请选择交接正文语言并保留原始人工补充及未决状态\n"
                        + f"完整问题{index}：不能只留下被截断的导航题名。",
                "reason": f"原始理由{index}，未答不自动接受。", "affects_node_ids": ["T01"],
                "requested_materials": [f"请求原文{index}：提供获准语言选择。"],
            }).model_dump()
            questions.append({**spec,
                "status": "ANSWERED_PENDING_ACCEPTANCE" if index == 1 else "AWAITING_ANSWER",
                "answers": [{"text": "人工答复原文：暂保中文稿，最终选择待明确接受。"}] if index == 1 else [],
                "resolution": None})
        snapshot["proposal"]["payload"]["candidate"]["questions"] = [
            {k: v for k, v in q.items() if k not in {"status", "answers", "resolution"}} for q in questions
        ]
        snapshot["pending"].update(questions=questions, pending_count=2)
        if kind == "DISCOVERY":
            primary = snapshot.pop("proposal")
            discovery_questions = []
            for q in questions:
                q["affects_claim_ids"] = ["R01"]
                q.pop("affects_node_ids")
                discovery_questions.append({k: v for k, v in q.items()
                                            if k not in {"status", "answers", "resolution"}})
            candidate = DiscoveryCandidate.model_validate({
                "title": primary["payload"]["candidate"]["title"], "phase": "DEFINE",
                "summary": primary["payload"]["candidate"]["summary"],
                "claims": [{"id": "R01", "category": "REQUIREMENT", "text": "语言未决不冒充接受。"}],
                "questions": discovery_questions,
            }).model_dump()
            snapshot.update(source_kind="DISCOVERY", discovery={
                "id": primary["id"], "revision": primary["revision"], "sha256": primary["sha256"],
                "answers": [], "readiness": {"review_status": "CURRENT"},
                "payload": {"status": "CONFIRMED", "candidate": candidate,
                            "source_receipt": primary["payload"]["source_receipt"], "resolutions": []}})
            snapshot["pending"]["session_ref"] = snapshot["pending"].pop("proposal_ref")
        document = _render_without_mutating(snapshot)
        paragraphs = document.paragraphs
        for index, q in enumerate(questions, 1):
            heading = next(p.text for p in paragraphs
                           if p.style.name == "Heading 2" and p.text.startswith(f"3.{index}  "))
            section = _section(document, heading)
            check(q["text"] in body(document), "Question navigation must not replace the full original question")
            status = next(p.text for p in section if p.text.startswith("状态"))
            check(f"3.{index}" in status and ("待接受" if index == 1 else "待回答") in status)
            requests = [p.text for p in section if p.text.startswith("请求")]
            check(any(f"3.{index}" in p for p in requests), "Requested material needs the current question number")
            check(q["requested_materials"][0] in body(document))
            if q["answers"]:
                answers = [p.text for p in section if p.text.startswith("已有回答")]
                check(any(f"3.{index}" in p for p in answers), "Recorded answers need the current question number")
                check(q["answers"][0]["text"] in body(document))
                check("已接受回答：" + q["answers"][0]["text"] not in body(document))


def test_short_source_owner_stays_with_its_locator_and_the_chain_ends_there():
    snapshot = source()
    first = _anchor("manual_source_A", "用户工单原文A：原输入不覆盖。")
    second = _anchor("manual_source_B", "用户备注原文B：未知保持未知。")
    snapshot["proposal"]["payload"]["candidate"]["nodes"][0]["source_anchors"] = [first]
    snapshot["pending"]["questions"][0]["source_anchors"] = [second]
    _sources(snapshot, [(first, "工单原件A.md"), (second, "现场备注B.md")])
    document = _render_without_mutating(snapshot)
    paragraphs = document.paragraphs
    owners = [(i, p) for i, p in enumerate(paragraphs) if p.text.startswith("引用位置：")]
    check(len(owners) == 2)
    for (index, owner), (anchor, filename) in zip(owners, [(first, "工单原件A.md"), (second, "现场备注B.md")]):
        locator = paragraphs[index + 1]
        check(owner.paragraph_format.keep_with_next is True,
              "A short owner may not orphan from its immediately following actual locator")
        check(filename in locator.text and "片段 0" in locator.text
              and f"0–{anchor['char_end']}" in locator.text, "KeepNext must target the actual locator, not an empty spacer")
        check(locator.paragraph_format.keep_with_next is False,
              "A locator ends this short source chain; it must not lock following source groups")


def test_many_source_owners_remain_complete_without_creating_an_unbreakable_long_chain():
    snapshot = source()
    anchor = _anchor("shared_manual_source", "共享原始工单：保留人工标题及输入。")
    base = snapshot["proposal"]["payload"]["candidate"]["nodes"][0]
    nodes = []
    for index in range(8):
        node = deepcopy(base)
        node.update(id="task_" + "x" * 88 + f"{index:02d}", title=f"来源复核任务{index + 1}",
                    source_anchors=[deepcopy(anchor)])
        nodes.append(node)
    _set_nodes(snapshot, nodes)
    _sources(snapshot, [(anchor, "共享人工原件.md")])
    snapshot["pending"]["questions"][0]["affects_node_ids"] = [nodes[0]["id"]]
    document = _render_without_mutating(snapshot)
    paragraphs = document.paragraphs
    owners = [(i, p) for i, p in enumerate(paragraphs) if p.text.startswith("引用位置：")]
    check(len(owners) == 1, "One exact source anchor is grouped once without dropping owners")
    index, owner = owners[0]
    check(len(owner.text) > 600, "This valid fixture reaches the existing long-owner guard")
    check(all("任务 " + node["id"] in owner.text for node in nodes), "All source owners remain discoverable")
    check(owner.paragraph_format.keep_with_next is False and owner.paragraph_format.keep_together is False,
          "An oversized owner stays editable and breakable rather than locking a whole page")
    check(paragraphs[index + 1].paragraph_format.keep_with_next is False)
