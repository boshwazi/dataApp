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


# Custom instructions (M12b)


def test_instructions_reach_every_ai_prompt(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Revenue"]}]},
        {"answer": "Revenue is RM 64."},
    )
    ok(p.api.put("/api/workspaces/current/instructions", json={"instructions": "Our financial year starts in April."}))
    ok(p.api.put(f"{p.dataset}/instructions", json={"instructions": "Revenue excludes tax."}))
    ok(p.api.put(f"{p.base}/instructions", json={"instructions": "Call customers clients."}))
    assert ok(p.api.get("/api/workspaces/current/instructions"))["instructions"] == "Our financial year starts in April."
    assert ok(p.api.get(p.base))["instructions"] == "Call customers clients."
    ok(ask(p, "What's revenue?"))
    for call in ai.calls:
        system = call["messages"][0]["content"]
        assert "financial year starts in April" in system and "excludes tax" in system and "clients" in system
        assert system.index("Instructions from the people") > 100  # after the agent's own rules
    too_long = p.api.put(f"{p.dataset}/instructions", json={"instructions": "x" * 2001})
    assert too_long.status_code == 400 and "2,000" in too_long.json()["detail"]


# Relative dates and why-did-it-change (M12b)


@pytest.mark.parametrize("period, n, expected, dates", [
    ("this_year_to_date", None, 24.0, "2025-01-01 to 2025-02-02"),
    ("last_year_to_date", None, 20.0, "2024-01-01 to 2024-02-02"),
    ("last_year", None, 40.0, "2024-01-01 to 2024-12-31"),
    ("last_n_months", 4, 34.0, "2024-11-01 to 2025-02-02"),
    ("last_month", None, 20.0, "2025-01-01 to 2025-01-31"),
    ("last_quarter", None, 10.0, "2024-10-01 to 2024-12-31"),
])
def test_relative_dates_count_back_from_the_latest_data(tmp_path, period, n, expected, dates):
    p, _ = published_project(tmp_path, {"queries": [{"measures": ["Revenue"], "period": period, "n": n}]}, {"answer": "ok"})
    result = ok(ask(p, "How much?"))["results"][0]
    assert result["rows"] == [[expected]] and dates in result["period"]
    assert any("Date[Date] at least" in f for f in result["how"]["filters"])


def test_why_did_it_change_breaks_the_change_down(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Revenue"], "period": "this_year_to_date", "compare_to": "last_year_to_date",
                      "group_by": SEGMENT}]},
        {"answer": "Revenue rose RM 4, all from Online."},
    )
    result = ok(ask(p, "Why did revenue change this year?"))["results"][0]
    assert result["show_as"] == "change"
    assert result["rows"][0] == ["Total", "", 20.0, 24.0, 4.0]
    assert result["rows"][1] == ["customers[segment]", "Online", None, 4.0, 4.0]  # Retail didn't move
    assert "biggest contributors" in ai.calls[1]["messages"][0]["content"]


def test_a_breakdown_column_out_of_reach_is_skipped_not_fatal(tmp_path):
    p, ai = published_project(
        tmp_path,
        {"queries": [{"measures": ["Revenue"], "period": "this_year_to_date", "compare_to": "last_year_to_date",
                      "group_by": [{"table": "sales", "column": "customer"}, {"table": "nowhere", "column": "x"}]}]},
        {"answer": "Revenue rose RM 4."},
    )
    result = ok(ask(p, "Why did revenue change?"))["results"][0]
    assert result["error"] is None and result["rows"][0][4] == 4.0
    assert result["skipped"] and "nowhere" in result["skipped"][0]
    assert "could_not_split_by" in ai.calls[1]["messages"][-1]["content"]


def test_the_ai_writes_in_bahasa_malaysia_when_asked(tmp_path):
    """X-Language: ms adds the language rule to every AI call; numbers stay checkable."""
    from app import language, llm

    class Client:
        def __init__(self):
            self.system = None
            self.chat = type("C", (), {"completions": type("X", (), {"create": self.create})()})()

        def create(self, **kwargs):
            self.system = kwargs["messages"][0]["content"]
            raise RuntimeError("stop here")

    client = Client()
    token = language.LANGUAGE.set("ms")
    try:
        try:
            llm.ask_json(client, "m", {}, "You are an agent.", "Hi", "t", {"type": "object"})
        except Exception:  # noqa: BLE001  only the prompt matters here
            pass
    finally:
        language.LANGUAGE.reset(token)
    assert "Bahasa Malaysia" in client.system and "never in words" in client.system
    assert language.instruction() == ""  # English again outside that request
