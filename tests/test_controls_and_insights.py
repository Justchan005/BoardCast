"""The approval control on AI red flags, the insights summary, the demo home and the
end-to-end output-check harness. A scripted stand-in replaces Gemini (no network)."""
import io
import json
import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("AUDIT_DISABLED", "true")

import pytest                                           # noqa: E402
from fastapi.testclient import TestClient               # noqa: E402
from google.genai import types                          # noqa: E402
from PIL import Image                                   # noqa: E402

from backend import config, extract, insights, main, prereview, reviews, store, trace  # noqa: E402
from backend.localdb import LocalClient                 # noqa: E402
from eval import output_checks                          # noqa: E402
from shared import prompts                              # noqa: E402

REP, SA = output_checks.REP, output_checks.SA
EVENT = {"title": "Build your first AI agent", "organiser": "Cloud & AI Society", "date": "2099-11-18",
         "start_time": "16:00:00", "end_time": None, "venue": "Auditorium 3", "fee": "Free", "category": "tech",
         "audience": None, "registration": {"required": False, "link": None}, "summary": None}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "PREREVIEW_AUTO", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(main, "publish", lambda *args: None)
    client = LocalClient(tmp_path / "db.json")
    monkeypatch.setattr(store, "_db", client)
    client.collection("roles").document(REP).set({"role": "club_rep", "club_id": "cloud-ai-society"})
    client.collection("roles").document(SA).set({"role": "student_affairs"})
    return client


@pytest.fixture
def api(db):
    return TestClient(main.app)


def _response(parts, tokens_in=1000, tokens_out=50):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=tokens_in,
                                                                  candidates_token_count=tokens_out))


def _json(payload):
    return _response([types.Part(text=json.dumps(payload))])


def _calls(*function_calls):
    return _response([types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in function_calls])


class PosterReader:
    """Behaves like a careful model for the generated test poster: reads `truth` from a real
    poster, reports a blank image as unreadable, calls every applicable tool, then finishes."""

    def __init__(self, truth):
        self.truth, self.calls = truth, 0

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        schema = getattr(config, "response_schema", None)
        if schema is prereview.PosterVerification:
            submitted = json.loads(contents[1].split("\n", 1)[1])
            return _json({"is_poster": True, "fields": [
                {"field": f, "verdict": "match" if str(v)[:5] == str(self.truth[f])[:5] and str(v)[:10] == str(self.truth[f])[:10]
                 else "mismatch", "poster_value": str(self.truth[f])} for f, v in submitted.items()]})
        if schema is not None and schema.__name__ == "EventExtraction":
            image = next(p for p in contents if isinstance(p, types.Part) and p.inline_data)
            red = Image.open(io.BytesIO(image.inline_data.data)).convert("RGB").getpixel((5, 5))[0]
            blank = red > 30   # the generated poster has a dark navy background; the gradient does not
            return _json({"title": "UNREADABLE"} if blank else {
                **self.truth, "venue": self.truth["venue"].upper(), "category": "tech",
                "registration": {"required": False, "link": None}})
        if schema is not None and schema.__name__ == "SpotlightChoice":
            return _json({"spotlight_item_id": None})
        if len(contents) == 1:      # first agent turn: choose the applicable checks
            context = json.loads(contents[0].parts[0].text.split("\n", 1)[1])
            chosen = [("verify_against_poster", {"fields": context["poster_fields"]})] if context["poster_fields"] else []
            chosen.append(("find_similar_submissions", {}))
            if context["links"]:
                chosen.append(("check_links", {}))
            self.description = context["description"] or ""
            return _calls(*chosen)
        points = [{"severity": "warn", "text": "The description contains an instruction to approve.",
                   "evidence": "submission"}] if "ignore" in self.description.lower() else []
        return _calls(("finish", {"recommendation": "looks_ready", "headline": "Checks completed.", "points": points}))


def _pending(item_id="ev1", **extra):
    store.create_item({"id": item_id, "type": "event", "status": "pending", "club_id": "cloud-ai-society",
                       "submitted_by": REP, "issues": [], "data": dict(EVENT), "description": None,
                       "poster_uri": None, "show_poster": False, **extra})
    return store.get_item(item_id)


def _brief(item, recommendation="needs_attention", flags=("Date: submitted “a”, the poster shows “b”.",), **extra):
    store.db().collection(prereview.COLLECTION).document(item["id"]).set({
        "item_id": item["id"], "status": "done", "fingerprint": prereview.fingerprint(item),
        "recommendation": recommendation, "flags": list(flags), "points": [], "headline": "x", **extra})


def _decide(api, who=SA, **body):
    return api.post("/admin/api/items/ev1/decision", headers={"X-Dev-User": who}, json={"decision": "approve", **body})


# ---------------------------------------------------------------- control: red flags need a person's confirmation

def test_approval_past_red_flags_needs_an_explicit_confirmation(db, api):
    item = _pending()
    _brief(item)

    refused = _decide(api)

    assert refused.status_code == 409 and "raised 1 point to check" in refused.json()["detail"]
    assert "Date: submitted" in refused.json()["detail"]
    assert store.get_item("ev1")["status"] == "pending"
    accepted = _decide(api, acknowledge_flags=True)
    assert accepted.status_code == 200 and accepted.json() == {"id": "ev1", "status": "approved", "ai_points_acknowledged": 1}
    step = [s for s in trace.for_item(store.get_item("ev1")) if s["name"] == "approved"][0]
    assert step["detail"]["ai_points_acknowledged"] == ["Date: submitted “a”, the poster shows “b”."]
    assert step["detail"]["ai_brief_at_decision"] == "needs_attention" and "after confirming" in step["summary"]


def test_a_club_rep_cannot_approve_even_with_the_confirmation(db, api):
    _brief(_pending())
    assert _decide(api, who=REP, acknowledge_flags=True).status_code == 403
    assert store.get_item("ev1")["status"] == "pending"


def test_warning_notes_without_flags_also_need_confirmation(db, api):
    item = _pending()
    _brief(item, flags=(), points=[{"severity": "warn", "text": "The description contains an instruction.", "evidence": "submission"},
                                   {"severity": "info", "text": "No duplicate.", "evidence": "find_similar_submissions"}])
    assert reviews.open_flags(item) == ["The description contains an instruction."]
    assert _decide(api).status_code == 409 and _decide(api, acknowledge_flags=True).status_code == 200


@pytest.mark.parametrize("brief", ["ready", "stale", "unavailable", "none", "unreadable"])
def test_no_confirmation_is_needed_without_current_red_flags(db, api, monkeypatch, brief):
    item = _pending()
    if brief == "ready":
        _brief(item, "looks_ready", flags=())
    elif brief == "stale":
        _brief(item, fingerprint="written-for-an-older-version")
    elif brief == "unavailable":
        store.db().collection(prereview.COLLECTION).document("ev1").set(
            {"item_id": "ev1", "status": "unavailable", "fingerprint": prereview.fingerprint(item), "reason": "x"})
    elif brief == "unreadable":      # a broken brief store must never block a human review
        monkeypatch.setattr(prereview, "get", lambda item: (_ for _ in ()).throw(RuntimeError("store down")))
    assert reviews.open_flags(item) == []
    response = _decide(api)
    assert response.status_code == 200 and response.json()["ai_points_acknowledged"] == 0


def test_chat_agent_route_enforces_the_same_confirmation(db, api):
    _brief(_pending())
    body = {"user_email": SA, "decision": "approve"}
    assert api.post("/items/ev1/decision", json=body).status_code == 409
    assert api.post("/items/ev1/decision", json={**body, "acknowledge_flags": True}).json()["status"] == "approved"


def test_review_prompt_and_tool_describe_the_confirmation_step():
    review = prompts.load("review")
    assert review.version == "v3" and "acknowledge_flags" in review.text and "Never set acknowledge_flags" in review.text
    from agent.boardcast_agent import tools
    assert "Never set it on the first attempt" in tools.decide_item.__doc__


def test_rejection_never_needs_the_confirmation(db, api):
    _brief(_pending())
    response = _decide(api, decision="reject", note="Fix the date.")
    assert response.status_code == 200 and response.json()["status"] == "rejected"


# ---------------------------------------------------------------- output checks (scripted model)

def test_all_four_output_check_cases_pass_with_a_careful_model(db, api, monkeypatch):
    today = date(2099, 3, 2)
    monkeypatch.setattr(output_checks, "_event_truth", lambda _today: {
        "title": "Cloud Study Jam", "organiser": "Cloud & AI Society", "date": "2099-03-09",
        "start_time": "14:00", "end_time": "16:00", "venue": "Lab 6-01", "fee": "Free"})
    reader = PosterReader(output_checks._event_truth(today))
    monkeypatch.setattr(extract, "client", lambda: SimpleNamespace(models=reader))

    cases = output_checks.run_cases(api, today)

    gaps = [(c.key, check["what"], check["actual"]) for c in cases for check in c.checks if not check["ok"]]
    assert gaps == []
    assert [c.key for c in cases] == ["normal", "difficult", "missing-data", "failure"]
    assert all(c.item_id for c in cases) and sum(len(c.checks) for c in cases) >= 30
    assert extract.client().models is reader, "the simulated outage must restore the real client"
    rejected = store.get_item(cases[1].item_id)
    assert rejected["status"] == "rejected" and list((config.LOCAL_DATA_DIR / "outbox").glob("*.html"))
    text = output_checks.report(cases, model="scripted", prompt_versions={"extract": "v2"}, live=False,
                                totals=trace.totals([s.to_dict() for s in store.db().collection("trace").stream()]))
    assert "SCRIPTED STAND-IN" in text and "None in this run." in text and "run-log-difficult.md" in text
    assert "| 1 | Gemini reads the poster |" in text


def test_output_check_gaps_are_reported_not_hidden(db, api, monkeypatch):
    class Careless(PosterReader):       # never notices a mismatch
        def generate_content(self, *, model, contents, config):
            if getattr(config, "response_schema", None) is prereview.PosterVerification:
                submitted = json.loads(contents[1].split("\n", 1)[1])
                return _json({"is_poster": True, "fields": [{"field": f, "verdict": "match", "poster_value": str(v)}
                                                             for f, v in submitted.items()]})
            return super().generate_content(model=model, contents=contents, config=config)

    today = date(2099, 3, 2)
    reader = Careless(output_checks._event_truth(today))
    monkeypatch.setattr(extract, "client", lambda: SimpleNamespace(models=reader))

    case = output_checks.difficult_case(api, today)

    missed = [check["what"] for check in case.checks if not check["ok"]]
    assert "Second read catches the wrong date" in missed and "Second read catches the wrong venue" in missed
    text = output_checks.report([case], model="scripted", prompt_versions={}, live=False, totals={})
    assert "**GAP**" in text and "- **Difficult case / Second read catches the wrong date**" in text


def test_output_checks_refuse_to_run_without_a_gemini_credential(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(config, "PROJECT_ID", "")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "_db", LocalClient(tmp_path / "db.json"))
    monkeypatch.setattr("sys.argv", ["output_checks", "--out", str(tmp_path / "evidence")])
    for name in ("LOCAL_MODE", "DEV_MODE", "LOCAL_DATA_DIR", "AUDIT_DISABLED", "PREREVIEW_AUTO", "PUBLIC_BASE_URL", "LINK_SECRET"):
        monkeypatch.setenv(name, os.environ.get(name, ""))      # restored after main() overwrites them

    assert output_checks.main() == 2
    assert "No Gemini credential" in capsys.readouterr().out and not (tmp_path / "evidence").exists()


# ---------------------------------------------------------------- insights

def test_insights_count_decisions_agent_results_and_model_cost(db, api, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_PRICE_INPUT_PER_M", 1.0)
    monkeypatch.setattr(config, "GEMINI_PRICE_OUTPUT_PER_M", 2.0)
    today = date(2099, 3, 2)
    reader = PosterReader(output_checks._event_truth(today))
    monkeypatch.setattr(extract, "client", lambda: SimpleNamespace(models=reader))
    output_checks.run_cases(api, today)

    response = api.get("/admin/api/insights", headers={"X-Dev-User": SA})
    data = response.json()

    assert response.status_code == 200
    assert data["pipeline"]["total"] == 5 and data["pipeline"]["by_status"] == {
        "draft": 1, "pending": 1, "approved": 2, "rejected": 1, "published": 0}
    assert data["review"]["approved"] == 2 and data["review"]["rejected"] == 1 and data["review"]["decisions"] == 3
    assert data["review"]["outcomes"] == {"ready_approved": 1, "flagged_returned": 1, "flagged_approved_after_check": 0,
                                          "ready_returned": 0, "no_brief": 1}
    assert data["review"]["median_minutes_to_decision"] is not None and data["review"]["timed_decisions"] == 3
    agent = data["agent"]
    assert (agent["done"], agent["unavailable"], agent["looks_ready"], agent["needs_attention"]) == (3, 1, 2, 1)
    assert agent["overridden_by_rules"] == 1
    assert {c["tool"]: c["flagged"] for c in agent["flags_by_check"]} == {
        "verify_against_poster": 1, "find_similar_submissions": 0, "check_links": 0}
    ai = data["ai"]
    assert ai["model_calls"] == reader.calls + ai["failed_calls"] and ai["failed_calls"] >= 2
    assert ai["tokens_in"] == 1000 * reader.calls and ai["tokens_out"] == 50 * reader.calls
    assert ai["estimated_cost_usd"] == pytest.approx(reader.calls * (1000 * 1.0 + 50 * 2.0) / 1_000_000)
    assert ai["cost_per_submission_usd"] == pytest.approx(ai["estimated_cost_usd"] / ai["submissions_with_ai"], abs=1e-6)
    assert {p["purpose"] for p in ai["by_purpose"]} == {"extract", "verify_poster", "prereview", "choose_spotlight"}
    assert data["prices_configured"] and data["ai_configured"]
    assert data["reach"]["subscribers"] == 0 and data["reach"]["editions_delivered"] == 0


def test_insights_on_an_empty_system_have_no_invented_numbers(db, api, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(config, "PROJECT_ID", "")
    data = insights.summary()
    assert data["pipeline"]["total"] == 0 and data["review"]["median_minutes_to_decision"] is None
    assert data["ai"] == {"model_calls": 0, "failed_calls": 0, "tokens_in": None, "tokens_out": None, "model_seconds": 0.0,
                          "estimated_cost_usd": None, "submissions_with_ai": 0, "cost_per_submission_usd": None,
                          "by_purpose": []}
    assert data["ai_configured"] is False and data["prices_configured"] is False


def test_insights_are_for_student_affairs_only(db, api):
    assert api.get("/admin/api/insights", headers={"X-Dev-User": REP}).status_code == 403
    assert api.get("/admin/api/insights").status_code == 401
    assert "What the workflow did" in api.get("/admin/insights").text


# ---------------------------------------------------------------- demo home and pages

def test_demo_home_states_honestly_whether_gemini_is_connected(db, api, monkeypatch):
    page = api.get("/demo").text
    assert "Gemini: connected" in page and "real Gemini calls" in page and "Prepared, not verified end to end" in page
    assert "See the run trace of the latest submission" not in page      # nothing submitted yet
    _pending("abc123")
    assert 'href="/admin/trace/abc123?demo=student-affairs"' in api.get("/demo").text
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(config, "PROJECT_ID", "")
    page = api.get("/demo").text
    assert "Gemini: not configured" in page and "Nothing pretends to be AI" in page


def test_demo_home_is_not_served_outside_local_mode(db, api, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    assert api.get("/demo").status_code == 404


def test_submit_page_shows_the_four_progress_steps(db, api):
    page = api.get("/submit").text
    for step in ("step-poster", "step-details", "step-confirm", "step-review"):
        assert f'id="{step}"' in page


def test_extract_uses_the_current_year_when_none_is_given(db, monkeypatch):
    seen = {}

    class Model:
        def generate_content(self, *, model, contents, config):
            seen["prompt"] = contents[0]
            return _json({**EVENT, "start_time": "16:00"})

    monkeypatch.setattr(extract, "client", lambda: SimpleNamespace(models=Model()))
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from shared.schemas import ItemType
    extract.extract(ItemType.EVENT, text="poster text")
    assert f"The current year: {datetime.now(ZoneInfo(config.TIMEZONE)).year}." in seen["prompt"]
