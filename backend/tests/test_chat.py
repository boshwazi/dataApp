import json
from types import SimpleNamespace

from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)


class ScriptedAI:
    """Answers each chat turn with the next scripted {reply, actions}."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer), tool_calls=None))])


def project_with(tmp_path, *answers):
    ai = ScriptedAI(*answers)
    p = Project(tmp_path, ai)
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    return p, ai


def say(p, stage, text, mode="full"):
    return ok(p.api.post(f"{p.dataset}/chat/{stage}", json={"message": text, "mode": mode}))


def steps(p, table="sales"):
    query = next(q for q in p.api.get(f"{p.dataset}/transform").json()["queries"] if q["name"] == table)
    return query["steps"]


def last_change(body):
    return body["messages"][-1]["change"]


# Transform


def test_full_mode_applies_valid_actions_as_one_undoable_change(tmp_path):
    p, ai = project_with(tmp_path, {
        "reply": "I'll trim regions and remove the bad one.",
        "actions": [
            {"action": "add_step", "params": {"table": "sales", "op": "change_case", "params": {"columns": ["customer"], "case": "upper"}},
             "reason": "consistent ids"},
            {"action": "add_step", "params": {"table": "sales", "op": "trim", "params": {"columns": ["nope"]}}},
        ],
    })
    before = len(steps(p))
    body = say(p, "transform", "Make customer ids upper case")
    change = last_change(body)
    assert body["applied"] is True and change["status"] == "applied"
    assert [a["ok"] for a in change["actions"]] == [True, False]
    assert change["actions"][0]["label"] == "sales: Make customer upper case"
    assert "does not exist" in change["actions"][1]["error"]
    new = steps(p)[before:]
    assert [(s["op"], s["status"]) for s in new] == [("change_case", "AI")]

    log = p.api.get(f"{p.dataset}/activity").json()["entries"][0]
    assert (log["stage"], log["actor"], log["via"]) == ("transform", "AI", "chat")

    ok(p.api.post(f"{p.dataset}/transform/undo"))
    assert len(steps(p)) == before

    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "Current state of this stage" in prompt and '"table": "sales"' in prompt
    assert "Earlier stages (read-only)" in prompt and "Get Data" in prompt


def test_review_mode_checks_on_a_copy_and_waits_for_approval(tmp_path):
    action = {"action": "add_step", "params": {"table": "sales", "op": "change_case", "params": {"columns": ["customer"], "case": "lower"}}}
    p, _ = project_with(tmp_path, {"reply": "Here's the plan.", "actions": [action]}, {"reply": "Again.", "actions": [action]})
    before = len(steps(p))

    body = say(p, "transform", "Lower-case the ids", mode="review")
    change = last_change(body)
    assert change["status"] == "pending" and change["actions"][0]["ok"] is True
    assert len(steps(p)) == before  # nothing applied yet

    body = ok(p.api.post(f"{p.dataset}/chat/transform/changes/{change['id']}/approve"))
    assert last_change(body)["status"] == "applied" and body["applied"] is True
    assert len(steps(p)) == before + 1
    assert p.api.post(f"{p.dataset}/chat/transform/changes/{change['id']}/approve").status_code == 400

    second = last_change(say(p, "transform", "Again", mode="review"))
    body = ok(p.api.post(f"{p.dataset}/chat/transform/changes/{second['id']}/reject"))
    assert last_change(body)["status"] == "rejected" and len(steps(p)) == before + 1


def test_questions_change_nothing_and_history_is_sent(tmp_path):
    p, ai = project_with(tmp_path, {"reply": "Sales has 5 rows.", "actions": []},
                         {"reply": "Yes, 5 columns too.", "actions": []})
    body = say(p, "transform", "How many rows does sales have?")
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"] and body["applied"] is False
    say(p, "transform", "And columns?")
    assert "How many rows does sales have?" in ai.calls[1]["messages"][-1]["content"]


# Get Data


def test_removing_data_always_waits_for_approval(tmp_path):
    p, _ = project_with(tmp_path, {"reply": "Removing customers.", "actions": [{"action": "remove_table", "params": {"table": "customers"}}]})
    change = last_change(say(p, "get_data", "Remove the customers table"))  # full mode
    assert change["status"] == "pending"
    assert len(p.api.get(p.dataset).json()["sources"]) == 2
    ok(p.api.post(f"{p.dataset}/chat/get_data/changes/{change['id']}/approve"))
    assert [s["table"] for s in p.api.get(p.dataset).json()["sources"]] == ["sales"]


def test_get_data_rename(tmp_path):
    p, _ = project_with(tmp_path, {"reply": "Renamed.", "actions": [{"action": "rename_table", "params": {"table": "sales", "name": "Orders"}}]})
    change = last_change(say(p, "get_data", "Call sales Orders"))
    assert change["actions"][0] == {**change["actions"][0], "ok": True, "label": "Rename sales to orders"}
    assert "orders" in [s["table"] for s in p.api.get(p.dataset).json()["sources"]]


# Model


def test_model_chat_adds_changes_and_refuses(tmp_path):
    p, _ = project_with(tmp_path, {
        "reply": "Adding margin and fixing format.",
        "actions": [
            {"action": "add_measure", "params": {"name": "Units", "kind": "aggregate", "table": "sales", "aggregation": "sum",
                                                  "expression": "[qty]", "format": {"type": "integer"}}},
            {"action": "change_measure", "params": {"name": "Revenue", "format": {"type": "currency", "decimals": 2, "currency": "RM"}}},
            {"action": "add_measure", "params": {"name": "Bad", "kind": "aggregate", "table": "sales", "aggregation": "sum",
                                                  "expression": "[customer]"}},
            {"action": "remove_relationship", "params": {"from_table": "sales", "from_column": "customer",
                                                          "to_table": "customers", "to_column": "customer"}},
        ],
    })
    change = last_change(say(p, "model", "Add units, show revenue with cents, drop the customer link"))
    assert [a["ok"] for a in change["actions"]] == [True, True, False, True]
    state = p.api.get(f"{p.dataset}/model").json()
    measures = {m["name"]: m for m in state["measures"]}
    assert measures["Units"]["value"] == 10 and measures["Units"]["status"] == "AI"
    assert measures["Revenue"]["format"]["decimals"] == 2
    assert [(r["from_name"], r["to_name"]) for r in state["relationships"]] == [("sales", "Date")]


# Report (dashboard)


def test_dashboard_chat_replaces_a_visual(tmp_path):
    p, _ = project_with(tmp_path, {
        "reply": "Switching to a bar chart.",
        "actions": [{"action": "replace_visual", "params": {"visual": "Revenue share", "type": "bar", "title": "Revenue by segment"}}],
    })
    page = p.page()
    ok(p.visual(page, type="donut", title="Revenue share", measures=["Revenue"], group_by=SEGMENT))
    body = ok(p.api.post(f"{p.base}/chat", json={"message": "Swap the donut for a bar chart"}))
    assert last_change(body)["actions"][0]["label"] == "Replace 'Revenue share' with a bar 'Revenue by segment'"
    visual = p.visuals()[0]
    assert (visual["type"], visual["title"], visual["status"]) == ("bar", "Revenue by segment", "AI")
    log = p.api.get(f"{p.base}/activity").json()["entries"][0]
    assert log["via"] == "chat"


def test_dashboard_review_mode(tmp_path):
    p, _ = project_with(tmp_path, {"reply": "Adding a page.", "actions": [{"action": "add_page", "params": {"title": "Customers"}}]})
    body = ok(p.api.post(f"{p.base}/chat", json={"message": "Add a customers page", "mode": "review"}))
    change = last_change(body)
    assert change["status"] == "pending" and ok(p.api.get(p.base))["pages"] == []
    ok(p.api.post(f"{p.base}/chat/changes/{change['id']}/approve"))
    assert [page["title"] for page in ok(p.api.get(p.base))["pages"]] == ["Customers"]


# Stale markers and the activity log


def test_stage_markers_follow_upstream_changes(tmp_path):
    p, _ = project_with(tmp_path)
    # The setup changed Transform, then Model, so nothing is out of date yet.
    assert p.api.get(p.dataset).json()["stale"] == {"get_data": None, "transform": None, "model": None}

    p.api.post(f"{p.dataset}/sources/upload", files=[("files", ("extra.csv", b"a\n1\n"))])
    assert p.api.get(p.dataset).json()["stale"] == {"get_data": None, "transform": "Get Data", "model": "Get Data"}

    ok(p.api.post(f"{p.dataset}/stages/transform/reviewed"))
    assert p.api.get(p.dataset).json()["stale"] == {"get_data": None, "transform": None, "model": "Transform"}
    summaries = [e["summary"] for e in p.api.get(f"{p.dataset}/activity").json()["entries"]]
    assert summaries[:2] == ["Marked Transform as reviewed", "Added extra"]


def test_dashboards_are_marked_when_their_dataset_is_republished(tmp_path):
    p, _ = project_with(tmp_path)
    page = p.page()
    ok(p.visual(page, type="kpi", title="Orders", measures=["Orders"]))
    assert ok(p.api.get(p.base))["dataset_updated_to"] is None
    ok(p.api.post(f"{p.dataset}/model/measures", json={"name": "Rows", "kind": "aggregate", "table": "sales", "aggregation": "count"}))
    p.publish()
    assert ok(p.api.get(p.base))["dataset_updated_to"] == 2
    ok(p.api.post(f"{p.base}/reviewed"))
    assert ok(p.api.get(p.base))["dataset_updated_to"] is None


def test_chat_needs_a_key_and_a_message(tmp_path):
    p = Project(tmp_path)
    assert p.api.post(f"{p.dataset}/chat/model", json={"message": "hi"}).status_code == 400
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    assert p.api.post(f"{p.dataset}/chat/model", json={"message": "  "}).status_code == 400
    assert p.api.post(f"{p.dataset}/chat/nope", json={"message": "hi"}).status_code == 404


# The Report chat answers from the data


def test_report_chat_sees_each_visuals_numbers_and_checks_its_reply(tmp_path):
    p, ai = project_with(
        tmp_path,
        {"reply": "In **Revenue by segment**, Retail leads with RM 50 against Online's RM 14.", "actions": []},
        {"reply": "Retail made RM 70.", "actions": [], "used_visuals": ["Revenue by segment"]},
    )
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    body = ok(p.api.post(f"{p.base}/chat", json={"message": "Explain the revenue by segment chart"}))
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert '"rows": [["Retail", 50.0], ["Online", 14.0]]' in prompt  # the chart's numbers, not just its layout
    reply = body["messages"][-1]
    assert reply["check"] == {"verified": True, "unmatched": []} and "answer" not in reply
    # "How this was calculated": the visual it read, its split and its measure's definition.
    assert reply["how"]["visuals"] == [{"title": "Revenue by segment", "page": "Sales overview", "type": "bar",
                                        "split_by": ["customers[segment]"]}]
    assert reply["how"]["measures"][0]["name"] == "Revenue" and "qty" in reply["how"]["measures"][0]["definition"]

    body = ok(p.api.post(f"{p.base}/chat", json={"message": "How much did Retail make?"}))
    assert body["messages"][-1]["check"]["verified"] is False and body["messages"][-1]["check"]["unmatched"] == [70]
    assert body["messages"][-1]["how"]["visuals"][0]["title"] == "Revenue by segment"  # named by the AI, not the text


def test_report_chat_queries_the_model_when_the_answer_isnt_on_the_dashboard(tmp_path):
    p, ai = project_with(
        tmp_path,
        {"reply": "Let me check the data.", "actions": [], "data_question": "How many orders were there in 2025?"},
        {"queries": [{"measures": ["Orders"], "filters": [{"table": "Date", "column": "Year", "operator": "equals", "value": 2025}]}]},
        {"answer": "There were 2 orders in 2025.", "follow_ups": ["How many in 2024?"]},
    )
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    body = ok(p.api.post(f"{p.base}/chat", json={"message": "How many orders did we get this year (2025)?"}))
    reply = body["messages"][-1]
    assert reply["content"] == "There were 2 orders in 2025."
    answer = reply["answer"]
    assert answer["question"] == "How many orders were there in 2025?"
    assert answer["results"][0]["rows"] == [[2]] and answer["results"][0]["how"]["filters"] == ["Date[Year] equals 2025"]
    assert answer["check"]["verified"] is True and answer["follow_ups"] == ["How many in 2024?"]
    assert len(ai.calls) == 3  # the chat turn, then Ask's plan and answer
    # Nothing on the dashboard changed.
    assert [v["title"] for v in p.visuals()] == ["Revenue by segment"]


# Looking things up before answering (M12b)


def test_the_transform_chat_looks_at_values_before_answering(tmp_path):
    p, ai = project_with(
        tmp_path,
        {"reply": "", "actions": [], "look": [{"tool": "column_values", "table": "sales", "column": "CUSTOMER"},
                                              {"tool": "sample_rows", "table": "nope"}]},
        {"reply": "C1 has the most orders (3 of 5).", "actions": []},
    )
    thread = say(p, "transform", "Which customer appears most?")["messages"]
    second = ai.calls[1]["messages"][-1]["content"]
    assert '"values": [["C1", 3]' in second and "no table called 'nope'" in second
    reply = thread[-1]
    assert reply["content"].startswith("C1") and reply["looked"][0] == {"tool": "column_values", "table": "sales", "column": "customer"}
    assert "look" in ai.calls[0]["messages"][0]["content"]


def test_lookups_stop_after_two_rounds(tmp_path):
    again = {"reply": "", "actions": [], "look": [{"tool": "sample_rows", "table": "sales"}]}
    p, ai = project_with(tmp_path, again, again, {"reply": "Done looking.", "actions": [], "look": [{"tool": "sample_rows", "table": "sales"}]})
    thread = say(p, "model", "Show me some rows")["messages"]
    assert len(ai.calls) == 3 and thread[-1]["content"] == "Done looking."
    assert "no more lookups" in ai.calls[2]["messages"][-1]["content"]
