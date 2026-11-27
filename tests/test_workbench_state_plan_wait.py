"""Real App/HTTP/SQLite persistence for the Discovery wait preference.

This is presentation state only: it does not dispatch/retry a planning operation.
"""
from test_workbench_state import client_at, counts


def check(value, message="plan wait persistence acceptance failed"):
    if not value:
        raise AssertionError(message)


def project(client):
    response = client.post("/api/projects", json={"name": "等待预算持久化原创夹具", "description": ""})
    check(response.status_code == 201, response.text)
    pid = response.json()["id"]
    return pid, f"/api/projects/{pid}/workbench-state", {
        "project_id": pid, "kind": "discovery", "object_id": "original-requirements",
        "session_id": "original-discussion", "revision": 4, "sha256": "a" * 64,
    }


def body(key, *, operation="save-wait", revision=0, fields=None):
    return {"key": key, "operation_id": operation, "expected_revision": revision,
            "fields": fields if fields is not None else {
                "planWaitSeconds": 600, "answers": {"Q01": "仍保留的人工答复"},
                "decisions": {"Q02": {"assumption": "待用户审阅的假设", "reason": "尚未提交"}},
                "accepted": ["R01"], "graphsAccepted": ["G01"], "reason": "原始批注保留",
            },
            "view": {"graphs": {"G01": {"zoom": 1.2, "pan": {"x": -32, "y": 48},
                      "positions": {"N01": {"x": 72, "y": 88}}, "selected": ["N01"],
                      "search": "原始节点", "mode": "mindmap"}}, "scroll": {"x": 0, "y": 5522}},
            "dirty": True}


def test_wait_budgets_coexist_with_existing_fields_and_survive_real_store_restart(tmp_path):
    directory = tmp_path / "data"
    saved = []
    with client_at(directory) as client:
        pid, base, key = project(client)
        for index, budget in enumerate([120, 300, 600]):
            value = body(key, operation=f"save-wait-{budget}", revision=index)
            value["fields"]["planWaitSeconds"] = budget
            response = client.post(base + "/save", json=value)
            check(response.status_code == 200, response.text)
            receipt = response.json()
            check(receipt["fields"] == value["fields"] and receipt["view"] == value["view"])
            check(receipt["state_revision"] == index + 1 and receipt["dirty"] is True)
            # Exact operation inspection observes the same committed snapshot.
            inspect = client.get(base + f"/operations/save-wait-{budget}")
            check(inspect.status_code == 200 and inspect.json() == {**receipt, "replayed": True})
            saved.append((value, receipt))
        # An old CAS cannot change the frozen current preference or user draft.
        stale = body(key, operation="stale-budget-tab", revision=0)
        check(client.post(base + "/save", json=stale).status_code == 409)
        check(counts(client.app.state.store)["agent_executions"] == 0)
        check(counts(client.app.state.store)["task_runs"] == 0)

    with client_at(directory) as restarted:
        loaded = restarted.post(base + "/load", json={"key": key})
        check(loaded.status_code == 200, loaded.text)
        state = loaded.json()
        check(state["fields"] == saved[-1][0]["fields"] and state["view"] == saved[-1][0]["view"])
        check(state["fields"]["planWaitSeconds"] == 600 and state["state_revision"] == 3)
        for value, receipt in saved:
            historical = restarted.get(base + "/operations/" + value["operation_id"])
            check(historical.status_code == 200 and historical.json() == {**receipt, "replayed": True})
        replay = restarted.post(base + "/save", json=saved[0][0])
        check(replay.status_code == 200 and replay.json() == {**saved[0][1], "replayed": True})
        current = restarted.post(base + "/load", json={"key": key}).json()
        check(current == state, "replaying the old receipt must not replace the current budget")
        with restarted.app.state.store.connect() as db:
            check(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
            check(db.execute("SELECT count(*) FROM workbench_state_versions").fetchone()[0] == 3)
        check(counts(restarted.app.state.store)["agent_executions"] == 0)


def test_wait_preference_rejects_non_integer_or_unknown_values_without_widening_whitelist(tmp_path):
    with client_at(tmp_path / "data") as client:
        pid, base, key = project(client)
        before = counts(client.app.state.store)
        for index, invalid in enumerate([True, False, 120.0, 300.0, 600.0, 0, 121, 599, 601, -1, "600", None, [], {}]):
            value = body(key, operation=f"invalid-wait-{index}", fields={"planWaitSeconds": invalid})
            response = client.post(base + "/save", json=value)
            check(response.status_code == 422, f"accepted invalid wait {invalid!r}: {response.text}")
            check(counts(client.app.state.store) == before)
        for kind in ["proposal", "workspace", "report"]:
            other = body({**key, "kind": kind}, operation="wrong-kind-" + kind, fields={"planWaitSeconds": 600})
            check(client.post(base + "/save", json=other).status_code == 422)
        extra = body(key, operation="unrelated-field", fields={"planWaitSeconds": 600, "unapproved": "never accepted"})
        check(client.post(base + "/save", json=extra).status_code == 422)
        # Old saved Discovery states remain accepted without the new preference.
        legacy = body(key, operation="legacy-discovery", fields={"answers": {"Q01": "原有字段"}, "accepted": ["R01"]})
        response = client.post(base + "/save", json=legacy)
        check(response.status_code == 200 and response.json()["fields"] == legacy["fields"])
