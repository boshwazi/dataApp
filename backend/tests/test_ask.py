import pytest

from test_chat import ScriptedAI
from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)


def published_project(tmp_path, *answers):
    ai = ScriptedAI(*answers)
    p = Project(tmp_path, ai)
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    ok(p.api.post(f"{p.base}/publish"))
    return p, ai


def ask(p, question, history=()):
    return p.api.post(f"{p.base}/ask", json={"question": question, "history": list(history)})


def test_answers_come_from_engine_results_with_how_they_were_calculated(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Revenue"], "group_by": SEGMENT, "sort": "desc", "limit": 1,
                      "show_as": "bar", "title": "Top segment by revenue"}]},
        {"answer": "Retail is the top segment with RM 50 of revenue.", "follow_ups": ["How did Retail do last year?"]},
    )
    body = ok(ask(p, "Which segment sells the most?"))
    result = body["results"][0]
    assert result["rows"] == [["Retail", 50.0]] and result["truncated"] is True
    assert result["how"] == {
        "measures": [{"name": "Revenue", "definition": "sum of [qty] * [price] in sales"}],
        "split_by": ["customers[segment]"],
        "filters": [],
        "tables": ["customers", "sales"],
    }
    assert body["check"] == {"verified": True, "unmatched": []}
    assert body["follow_ups"] == ["How did Retail do last year?"] and body["dataset_version"] == 1

    plan_prompt = ai.calls[0]["messages"][-1]["content"]
    assert "Revenue by segment" in plan_prompt and "sum of [qty] * [price]" in plan_prompt
    answer_prompt = ai.calls[1]["messages"][-1]["content"]
    assert '"Retail", 50.0' in answer_prompt  # the answer is written from the results


def test_numbers_not_in_the_results_are_flagged(tmp_path):
    p, _ = published_project(
        tmp_path,
        {"queries": [{"measures": ["Revenue"]}]},
        {"answer": "Revenue is RM 90."},
    )
    body = ok(ask(p, "What's total revenue?"))
    assert body["results"][0]["rows"] == [[64.0]]
    assert body["check"] == {"verified": False, "unmatched": [90.0]}


def test_bad_queries_are_reported_not_guessed(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Profit"]}], "cannot_answer": ""},
        {"answer": "There is no profit measure in this data."},
    )
    body = ok(ask(p, "What's our profit?"))
    assert "no measure called 'Profit'" in body["results"][0]["error"]
    assert "no measure called 'Profit'" in ai.calls[1]["messages"][-1]["content"]


def test_filters_and_conversation_history(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Orders"], "filters": [{"table": "Date", "column": "Year", "operator": "equals", "value": 2025}]}]},
        {"answer": "There were 2 orders in 2025."},
    )
    body = ok(ask(p, "And in 2025?", [{"role": "user", "content": "How many orders?"}, {"role": "assistant", "content": "5."}]))
    assert body["results"][0]["rows"] == [[2]] and body["results"][0]["how"]["filters"] == ["Date[Year] equals 2025"]
    assert "How many orders?" in ai.calls[0]["messages"][-1]["content"]


@pytest.mark.parametrize("question, status", [("", 400), ("x" * 1001, 400)])
def test_question_limits(tmp_path, question, status):
    p, _ = published_project(tmp_path)
    assert ask(p, question).status_code == status


def test_ask_needs_a_key(tmp_path):
    p = Project(tmp_path)
    assert ask(p, "Hi?").status_code == 400


def test_ask_answers_for_the_slicer_selection(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Revenue"]}]},
        {"answer": "Revenue is RM 50 for Retail."},
    )
    page = ok(p.api.get(p.base, params={"side": "published"}))["pages"][0]["id"]
    body = ok(p.api.post(f"{p.base}/ask", json={"question": "What's revenue?", "page_id": page, "filters": SLICED}))
    assert body["results"][0]["rows"] == [[50.0]] and body["scope"] == ["customers[segment] is Retail"]
    assert body["results"][0]["how"]["filters"] == ["customers[segment] is Retail (page filter)"]
    assert "filtered to customers[segment] is Retail" in ai.calls[0]["messages"][-1]["content"]


SLICED = [{"table": "customers", "column": "segment", "operator": "in", "values": ["Retail"]}]
