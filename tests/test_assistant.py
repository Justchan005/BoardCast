"""BoardCast Assistant: role scoping, confirmation-only actions, quick-answer routing, the Gemini loop."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import assistant, config, extract, hosted_demo, main, prereview, store, tokens, trace
from backend.localdb import LocalClient

REP = "rep.assist@mail.apu.edu.my"
OTHER = "other.assist@mail.apu.edu.my"
SA = "reviewer.assist@apu.edu.my"


def event(title, day="2099-12-03"):
    return {"title": title, "organiser": "Computing Club", "date": day, "start_time": "14:00",
            "venue": "Auditorium 2", "fee": "Free"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(config, "PROJECT_ID", "")
    monkeypatch.setattr(config, "USE_VERTEX", False)
    monkeypatch.setattr(tokens, "_SECRET", b"assistant-test-only")
    client = LocalClient(tmp_path / "assist.json")
    monkeypatch.setattr(store, "_db", client)
    for email, role in [(REP, {"role": "club_rep", "club_id": "club-a"}),
                        (OTHER, {"role": "club_rep", "club_id": "club-b"}),
                        (SA, {"role": "student_affairs"})]:
        client.collection("roles").document(email).set(role)
    monkeypatch.setattr(main, "publish", lambda *args: None)
    monkeypatch.setattr(main.mailer, "send", lambda *args, **kwargs: "test-message-no-delivery")
    return client


@pytest.fixture
def api(db):
    with TestClient(main.app) as client:
        yield client


def h(email):
    return {"X-Dev-User": email}


def submit(api, title, email=REP, day="2099-12-03"):
    r = api.post("/admin/api/submissions", headers=h(email),
                 json={"type": "event", "data": event(title, day), "confirmed": True})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def flag(item_id, *flags):
    item = store.get_item(item_id)
    store.db().collection(prereview.COLLECTION).document(item_id).set(
        {"status": "done", "fingerprint": prereview.fingerprint(item), "recommendation": "needs_attention",
         "headline": "Check the date", "flags": list(flags), "points": []})


def chat(api, email, *texts):
    messages = []
    for text in texts:
        messages.append({"role": "user", "text": text})
        r = api.post("/admin/api/assistant/chat", headers=h(email), json={"messages": messages})
        assert r.status_code == 200, r.text
        body = r.json()
        messages.append({"role": "assistant", "text": body["reply"]})
    return body


def card(body, kind):
    found = [c for c in body["cards"] if c["type"] == kind]
    assert found, f"no {kind} card in {[c['type'] for c in body['cards']]}"
    return found[0]


# ---------------------------------------------------------------- quick answers (no Gemini)

def test_how_many_to_approve_today_counts_the_queue(api):
    for title in ("Study Jam", "Prep Night", "Photo Walk"):
        submit(api, title)
    flag(submit(api, "Late Talk"), "Date: submitted 3 Dec, the poster shows 4 Dec.")
    body = chat(api, SA, "How many posters do I need to approve today?")
    assert body["mode"] == "quick"
    assert "4 submissions waiting for review" in body["reply"]
    assert "1 flagged" in body["reply"]
    tiles = {t["label"]: t["value"] for t in card(body, "stats")["tiles"]}
    assert tiles["Waiting for review"] == 4 and tiles["Arrived today"] == 4 and tiles["AI flagged"] == 1
    assert [s["tool"] for s in body["steps"]] == ["get_dashboard"]


def test_preparing_an_approval_changes_nothing(api):
    item_id = submit(api, "Hackathon Prep Night")
    body = chat(api, SA, "Approve Hackathon Prep Night")
    decision = card(body, "decision")
    assert decision["decision"] == "approve" and decision["item"]["id"] == item_id and not decision["blocked"]
    assert store.get_item(item_id)["status"] == "pending"   # only the person's button decides


def test_return_keeps_the_reason_the_reviewer_typed(api):
    submit(api, "Photography Walk")
    body = chat(api, SA, "Return the photography walk because the venue is not booked yet")
    decision = card(body, "decision")
    assert decision["decision"] == "reject"
    assert decision["reason"] == "the venue is not booked yet"


def test_bulk_approval_leaves_out_flagged_items(api):
    ready = submit(api, "Ready One")
    flagged = submit(api, "Flagged One")
    flag(flagged, "Possible duplicate of another submission.")
    bulk = card(chat(api, SA, "Approve everything that looks ready"), "bulk")
    assert [r["id"] for r in bulk["ready"]] == [ready]
    assert [r["id"] for r in bulk["excluded"]] == [flagged]
    assert "AI raised 1 point" in bulk["excluded"][0]["excluded_because"]


def test_card_approval_goes_through_the_normal_checks(api):
    item_id = submit(api, "Card Approval")
    row = card(chat(api, SA, "Approve card approval"), "decision")["item"]
    r = api.post(f"/admin/api/items/{item_id}/decision", headers=h(REP),
                 json={"decision": "approve", "expected_updated_at": row["updated_at"], "channel": "assistant"})
    assert r.status_code == 403
    r = api.post(f"/admin/api/items/{item_id}/decision", headers=h(SA),
                 json={"decision": "approve", "expected_updated_at": row["updated_at"], "channel": "assistant"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    steps = trace.for_item(store.get_item(item_id))
    assert any(s["name"] == "approved" and s["detail"]["via"] == "assistant" for s in steps)
    # A second click on the stale card is refused, not repeated.
    again = api.post(f"/admin/api/items/{item_id}/decision", headers=h(SA),
                     json={"decision": "approve", "expected_updated_at": row["updated_at"], "channel": "assistant"})
    assert again.status_code == 409


def test_flagged_card_approval_needs_the_acknowledgement(api):
    item_id = submit(api, "Flag Check")
    flag(item_id, "Venue differs from the poster.")
    decision = card(chat(api, SA, f"approve {item_id}"), "decision")
    assert decision["flags"] == ["Venue differs from the poster."]
    body = {"decision": "approve", "expected_updated_at": decision["item"]["updated_at"], "channel": "assistant"}
    assert api.post(f"/admin/api/items/{item_id}/decision", headers=h(SA), json=body).status_code == 409
    ok = api.post(f"/admin/api/items/{item_id}/decision", headers=h(SA), json={**body, "acknowledge_flags": True})
    assert ok.status_code == 200


def test_club_rep_only_sees_their_club_and_cannot_decide(api):
    submit(api, "Our Event", REP)
    submit(api, "Their Event", OTHER)
    body = chat(api, REP, "What's the status of my submissions?")
    titles = [r["title"] for r in card(body, "queue")["items"]]
    assert titles == ["Our Event"]
    body = chat(api, REP, "Approve Our Event")
    assert "Student Affairs" in body["reply"]
    assert not [c for c in body["cards"] if c["type"] in ("decision", "bulk")]
    ctx = assistant.Ctx(email=REP, role="club_rep", club_id="club-a")
    for tool in ("prepare_decision", "prepare_bulk_approval", "get_edition", "get_insights"):
        assert "error" in assistant.run_tool(ctx, tool, {"item_id": "x", "decision": "approve"})
    other_id = store.list_items(["pending"])[0]["id"] if store.list_items(["pending"])[0]["club_id"] == "club-b" \
        else store.list_items(["pending"])[1]["id"]
    assert "error" in assistant.run_tool(ctx, "get_submission", {"item_id": other_id})


def test_rejected_submission_explains_the_reason_to_the_club(api):
    item_id = submit(api, "Returned Event")
    item = store.get_item(item_id)
    api.post(f"/admin/api/items/{item_id}/decision", headers=h(SA),
             json={"decision": "reject", "note": "Add the room number.", "expected_updated_at": item["updated_at"].isoformat()})
    body = chat(api, REP, "Why was my poster returned?")
    assert "Add the room number." in body["reply"]
    assert card(body, "queue")["items"][0]["rejection_reason"] == "Add the room number."


def test_upcoming_events_respect_club_scope_and_keep_staff_overview(db):
    day = assistant.today().isoformat()
    for item_id, club_id, status, kind in (
        ("ours-approved", "club-a", "approved", "event"),
        ("ours-published", "club-a", "published", "event"),
        ("other-approved", "club-b", "approved", "event"),
        ("other-published", "club-b", "published", "event"),
        ("ours-pending", "club-a", "pending", "event"),
        ("ours-notice", "club-a", "approved", "announcement"),
    ):
        store.create_item({"id": item_id, "type": kind, "status": status,
                           "club_id": club_id, "submitted_by": REP if club_id == "club-a" else OTHER,
                           "data": event(item_id, day)})
    rep = assistant.Ctx(email=REP, role="club_rep", club_id="club-a")
    result = assistant.run_tool(rep, "list_upcoming_events", {})
    assert {row["id"] for row in result["events"]} == {"ours-approved", "ours-published"}
    assert result["total"] == 2
    assert {row["id"] for row in rep.cards[0]["items"]} == {"ours-approved", "ours-published"}
    assert all(row["submitted_by"] == REP for row in rep.cards[0]["items"])

    affairs = assistant.Ctx(email=SA, role="student_affairs", club_id=None)
    result = assistant.run_tool(affairs, "list_upcoming_events", {})
    assert {row["id"] for row in result["events"]} == {
        "ours-approved", "ours-published", "other-approved", "other-published"}
    assert result["total"] == 4


def test_how_to_questions_use_the_real_rules(api):
    body = chat(api, REP, "What do I need to submit an event?")
    assert "title, organiser, date, start_time, venue, fee" in body["reply"]


def test_context_endpoint_and_validation(api):
    submit(api, "Context Event")
    ctx = api.get("/admin/api/assistant/context", headers=h(SA)).json()
    assert ctx["role"] == "student_affairs" and ctx["snapshot"]["waiting_for_review"] == 1 and ctx["starters"]
    assert api.get("/admin/api/assistant/context", headers=h("nobody@apu.edu.my")).status_code == 403
    bad = api.post("/admin/api/assistant/chat", headers=h(SA), json={"messages": [{"role": "assistant", "text": "hi"}]})
    assert bad.status_code == 422


# ---------------------------------------------------------------- the Gemini loop

class FakeModels:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate_content(self, *, model, contents, config):
        self.calls.append(list(contents))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            part = SimpleNamespace(text=step, thought=False)
            return SimpleNamespace(function_calls=[], candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
        calls = [SimpleNamespace(name=name, args=args) for name, args in step]
        from google.genai import types
        content = types.Content(role="model", parts=[types.Part.from_function_call(name=n, args=a) for n, a in step])
        return SimpleNamespace(function_calls=calls, candidates=[SimpleNamespace(content=content)])


@pytest.fixture
def gemini(monkeypatch):
    def install(*script):
        fake = FakeModels(script)
        monkeypatch.setattr(extract, "ai_configured", lambda: True)
        monkeypatch.setattr(extract, "client", lambda: SimpleNamespace(models=fake))
        monkeypatch.setattr(assistant.time, "sleep", lambda s: None)
        return fake
    return install


def test_agent_chooses_tools_and_cards_come_from_code(api, gemini):
    item_id = submit(api, "Agent Event")
    fake = gemini([("get_dashboard", {})], [("prepare_decision", {"item_id": item_id, "decision": "approve"})],
                  "Ready to approve **Agent Event**. Confirm on the card.")
    body = chat(api, SA, "approve the only thing waiting")
    assert body["mode"] == "gemini" and body["reply"].startswith("Ready to approve")
    assert [s["tool"] for s in body["steps"]] == ["get_dashboard", "prepare_decision"]
    assert card(body, "decision")["item"]["title"] == "Agent Event"
    assert store.get_item(item_id)["status"] == "pending"
    assert len(fake.calls) == 3
    rows = [s.to_dict() for s in store.db().collection(trace.COLLECTION).stream()]
    assistant_rows = [r for r in rows if r.get("name") == "assistant"]
    assert len(assistant_rows) == 3 and all(r["prompt"].startswith("assistant@") for r in assistant_rows)


def test_agent_cannot_reach_staff_tools_as_a_club_rep(api, gemini):
    submit(api, "Rep Event")
    gemini([("prepare_bulk_approval", {})], "Only Student Affairs can approve.")
    body = chat(api, REP, "approve everything")
    assert body["steps"] == [{"tool": "prepare_bulk_approval", "label": "Not available for your role", "ok": False}]
    assert not body["cards"]


def test_gemini_failure_falls_back_to_quick_answers(api, gemini):
    submit(api, "Fallback Event")
    gemini(RuntimeError("503 UNAVAILABLE"), RuntimeError("503 UNAVAILABLE"))
    body = chat(api, SA, "How many do I need to approve today?")
    assert body["mode"] == "quick" and "Gemini is unavailable" in body["reply"]
    assert "1 submission waiting for review" in body["reply"]


def test_one_retry_recovers_a_busy_model(api, gemini):
    gemini(RuntimeError("429 RESOURCE_EXHAUSTED"), "All clear.")
    body = chat(api, SA, "anything waiting?")
    assert body["mode"] == "gemini" and body["reply"] == "All clear."


def test_action_cards_replace_duplicate_detail_cards():
    ctx = assistant.Ctx(email=SA, role="student_affairs", club_id=None)
    ctx.add_card({"type": "item", "key": "item-a", "item": {"id": "a"}})
    ctx.add_card({"type": "queue", "key": "queue-pending", "items": []})
    ctx.add_card({"type": "decision", "key": "decision-a", "item": {"id": "a"}})
    assistant.prune_cards(ctx)
    assert [c["type"] for c in ctx.cards] == ["queue", "decision"]
    ctx.add_card({"type": "queue", "key": "matches", "items": [{"id": "a"}]})   # only repeats the decided item
    assistant.prune_cards(ctx)
    assert [c["type"] for c in ctx.cards] == ["queue", "decision"]
    ctx.add_card({"type": "bulk", "key": "bulk", "ready": [], "excluded": []})
    assistant.prune_cards(ctx)
    assert [c["type"] for c in ctx.cards] == ["decision", "bulk"]


def test_hosted_demo_rate_limits_the_chat():
    assert hosted_demo._expensive("POST", "/admin/api/assistant/chat")
    assert not hosted_demo._expensive("GET", "/admin/api/assistant/context")
