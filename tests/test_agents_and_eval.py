"""Editor grounding, prompt loading, signed links and eval scoring. Run: pytest -q"""
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("AUDIT_DISABLED", "true")

from backend import editor, tokens                    # noqa: E402
from eval.scoring import score, summarise             # noqa: E402
from shared import prompts                            # noqa: E402
from shared.schemas import EditorPlan                 # noqa: E402

ITEMS = [
    {"id": "e1", "type": "event", "data": {"title": "CV clinic", "organiser": "Career Centre", "venue": "B-04-02",
                                            "date": "2026-11-12", "start_time": "14:00", "fee": "Free"}},
    {"id": "e2", "type": "event", "data": {"title": "Photo walk", "venue": "Main lobby", "date": "2026-11-13", "fee": "RM5"}},
    {"id": "w1", "type": "achievement", "data": {"title": "Win", "team": "Team A", "competition": "X", "result": "1st place"}},
]


# ---------- editor agent output is validated ----------

@pytest.mark.parametrize("claim", [
    "Guaranteed internships at Google for every attendee",
    "CV clinic on 13 Nov for RM12",  # both numbers exist, attached to different facts
    "Photo walk at the airport",     # invented words need no numbers
])
def test_unverified_editor_prose_never_publishes(claim):
    raw = EditorPlan(spotlight_item_id="e1", headline=claim, teaser=claim, subject_line=claim)
    plan, replaced = editor.validate(raw, ITEMS)
    assert plan == editor.fallback_plan(ITEMS, "e1")
    assert set(replaced) == {"headline", "teaser", "subject_line"}


def test_headline_teaser_and_subject_are_checked():
    raw = EditorPlan(spotlight_item_id="ghost",
                     headline="CV clinic, plus 1 more event coming up",       # 1 = number of other events: allowed
                     teaser="Photo walk on Friday, only RM3!",                 # 3 is in no item: replaced
                     subject_line="Campus Weekly: CV clinic")
    plan, replaced = editor.validate(raw, ITEMS)
    assert plan.spotlight_item_id == "e1"                     # unknown spotlight -> earliest event
    assert plan.headline == "CV clinic, plus 1 more event coming up"
    assert plan.teaser == "Also coming up: Photo walk (Fri 13 Nov)." and "teaser" in replaced
    assert plan.subject_line == "Campus Weekly: CV clinic" and "spotlight_item_id" in replaced


def test_plain_version_without_gemini():
    plan = editor.fallback_plan(ITEMS)
    assert plan.spotlight_item_id == "e1"
    assert plan.headline == "CV clinic, plus 1 more event coming up"
    assert plan.subject_line == "Campus Weekly: CV clinic"
    assert editor.fallback_plan([]).subject_line == "Campus Weekly: a quiet week on campus"


def test_editor_can_select_spotlight_but_cannot_supply_copy(monkeypatch):
    generate = Mock(return_value=SimpleNamespace(parsed=editor.SpotlightChoice(spotlight_item_id="e2")))
    monkeypatch.setattr(editor, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    plan, report = editor.plan_issue(ITEMS)
    assert plan == editor.fallback_plan(ITEMS, "e2")
    assert report["copy_source"] == "approved_fields"
    assert generate.call_args.kwargs["config"].response_schema is editor.SpotlightChoice


def test_editor_failure_uses_source_copy(monkeypatch):
    generate = Mock(side_effect=RuntimeError("model unavailable"))
    monkeypatch.setattr(editor, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    plan, report = editor.plan_issue(ITEMS)
    assert plan == editor.fallback_plan(ITEMS)
    assert report["source"] == "fallback"


def test_editor_teaser_keeps_complete_source_pairs_and_length():
    items = [ITEMS[0], {"id": "long", "type": "event", "data": {
        "title": " ".join(["Long"] * 50), "date": "2026-11-13"}}, ITEMS[1]]
    plan = editor.fallback_plan(items)
    assert len(plan.teaser.split()) <= editor.TEASER_MAX_WORDS
    assert plan.teaser == "Also coming up: Photo walk (Fri 13 Nov)."
    assert "quiet week" not in editor.fallback_plan([ITEMS[2]]).subject_line


# ---------- prompts are versioned files ----------

def test_all_prompts_load_with_versions():
    for name in ("coordinator", "intake", "review", "extract", "editor"):
        p = prompts.load(name)
        assert p.version.startswith("v") and len(p.text) > 100 or name == "coordinator"


def test_extract_prompt_renders_year():
    assert "{year}" not in prompts.load("extract").render(year=2026)


# ---------- signed links ----------

def test_signed_token_round_trip_and_tamper():
    t = tokens.sign("tp012345@mail.apu.edu.my")
    assert tokens.verify(t) == "tp012345@mail.apu.edu.my"
    assert tokens.verify(t, "verify") is None                     # wrong purpose
    assert tokens.verify(t[:-2] + ("AA" if t[-2:] != "AA" else "BB")) is None


def test_code_hash_matches():
    code, h, _ = tokens.new_code()
    assert len(code) == 6 and tokens.hash_code(code) == h


# ---------- eval scoring ----------

def test_scoring_normalises_formatting():
    expected = {"title": "Build your first AI agent", "start_time": "16:00", "fee": "Free",
                "registration": {"link": "https://forms.gle/abc"}}
    got = {"title": "build your first AI agent ", "start_time": "16:00:00", "fee": "FREE",
           "registration": {"link": "forms.gle/abc/"}}
    assert all(score(expected, got).values())


def test_scoring_counts_a_guess_as_wrong():
    assert score({"date": None}, {"date": "2026-11-09"}) == {"date": False}


def test_summarise_overall():
    s = summarise([{"title": True, "date": False}, {"title": True}])
    assert s["title"] == (2, 2) and s["overall"] == (2, 3)


def test_scoring_accepts_any_answer_listed_up_front():
    expected = {"venue": ["Audi 4, Level 3", "Auditorium 4, Level 3"], "category": "tech"}
    assert score(expected, {"venue": "Auditorium 4, Level 3", "category": "tech"}) == {"venue": True, "category": True}
    assert score(expected, {"venue": "Auditorium 4", "category": "tech"})["venue"] is False


def test_announcement_and_achievement_fields_are_scored():
    from eval.scoring import FIELDS_BY_TYPE
    got = {"title": "Membership Launch", "action_link": "https://x.example/a"}
    assert score({"title": "Membership launch", "action_link": "https://x.example/a/"}, got,
                 FIELDS_BY_TYPE["announcement"]) == {"title": True, "action_link": True}
    assert "result" in FIELDS_BY_TYPE["achievement"]


def test_qr_link_rules():
    from backend.qr_rules import apply_qr_links
    assert apply_qr_links("event", {"registration": {"required": False, "link": None}}, ["https://l.example"])["registration"] \
        == {"required": True, "link": "https://l.example"}
    assert apply_qr_links("announcement", {"action_link": None}, ["https://l.example"])["action_link"] == "https://l.example"
    assert apply_qr_links("announcement", {"action_link": "https://printed.example"}, ["https://l.example"])["action_link"] \
        == "https://printed.example"                                   # a printed link wins on announcements
    assert "action_link" not in apply_qr_links("achievement", {}, ["https://l.example"])


def test_separators_are_formatting_but_room_codes_are_not():
    from eval.scoring import norm
    assert norm("organiser", "GDG On Campus - Asia Pacific University") == norm("organiser", "GDG On Campus • Asia Pacific University")
    assert norm("venue", "D-6-04") != norm("venue", "D 6 04")
