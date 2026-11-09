"""Run: pytest -q   (no cloud access needed)"""
import pytest

from backend import checks

EVENT = {
    "title": "Build your first AI agent", "organiser": "Cloud & AI Society",
    "date": "2099-11-11", "start_time": "16:00", "end_time": "18:00",
    "venue": "Auditorium 3", "fee": "Free",
    "registration": {"required": True, "link": "https://forms.gle/abc"},
}


def test_complete_event_has_no_issues():
    assert checks.run_all("event", EVENT) == []


def test_missing_fee_is_flagged():
    assert "Missing: fee" in checks.run_all("event", {**EVENT, "fee": None})


def test_registration_can_be_required_without_a_separate_url():
    assert checks.run_all("event", {**EVENT, "registration": {"required": True, "link": None}}) == []
    assert checks.run_all("event", {**EVENT, "registration": {"required": True}}) == []


def test_optional_registration_url_is_still_checked_when_supplied():
    data = {**EVENT, "registration": {"required": False, "link": "http://forms.gle/x"}}
    assert any("https" in issue for issue in checks.run_all("event", data))


def test_empty_registration_url_is_treated_as_absent():
    from shared.schemas import Registration
    assert Registration(required=True, link="   ").link is None
    assert Registration(required=True, link=" https://forms.gle/x ").link == "https://forms.gle/x"


def test_description_url_digits_do_not_count_as_new_event_facts():
    description = "Join us: https://forms.gle/form98765?group=4321. Bring a laptop."
    assert checks.description_problems(description, EVENT) == []
    assert any("numbers" in p for p in checks.description_problems(description + " Fee RM999.", EVENT))


def test_description_urls_keep_existing_link_checks():
    assert any("https" in p for p in checks.description_problems("Join: http://forms.gle/x", EVENT))
    assert any("allowed list" in p for p in checks.description_problems("Join: https://unknown.example/x", EVENT))
    assert any("not a valid" in p for p in checks.description_problems("Join: https://[invalid/x", EVENT))


def test_missing_organiser_is_flagged():
    assert "Missing: organiser" in checks.run_all("event", {**EVENT, "organiser": None})


def test_http_and_unknown_domains_are_flagged():
    assert checks.link_problem("http://forms.gle/x").startswith("Link must start with https")
    assert "needs admin review" in checks.link_problem("https://free-prizes.example/x")
    assert checks.link_problem("https://docs.google.com/forms/d/1") is None


def test_lookalike_domain_is_flagged():
    assert "needs admin review" in checks.link_problem("https://forms.gle.evil.example/x")


def test_missing_venue_is_still_flagged_for_students():
    assert "Missing: venue" in checks.run_all("event", {**EVENT, "venue": None})


def test_past_event_is_flagged():
    assert "Date is already in the past" in checks.run_all("event", {**EVENT, "date": "2020-01-01"})


def test_achievement_needs_consent():
    a = {"title": "Win", "team": "Team A", "competition": "X", "result": "1st place", "consent_confirmed": False}
    assert "Winners' consent to be featured not confirmed" in checks.run_all("achievement", a)


def test_extracted_times_drop_gemini_time_zone_offset():
    from shared.schemas import EventExtraction
    e = EventExtraction.model_validate({"title": "x", "start_time": "15:00:00-08:00", "end_time": "17:00"})
    assert e.model_dump(mode="json")["start_time"] == "15:00:00"
    assert e.start_time.tzinfo is None


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,hello", "http://instagram.com/p/example",
    "https://instagram.com.evil.example/p/example", "https://example.com/p/example",
    "https://user:password@instagram.com/p/example", "https://@instagram.com/p/example",
    "https://instagram.com\\@evil.example/p/example", "https://insta\ngram.com/p/example",
    "https://instagram.com:wrong/p/example", "https://instagram.com:99999/p/example",
    "https://instagram.com:/p/example", "https:///instagram.com/p/example",
])
def test_unsafe_achievement_source_blocks_review(url):
    data = {"title": "Win", "team": "Team A", "competition": "Campus contest",
            "result": "Winner", "consent_confirmed": True, "instagram_url": url}
    assert checks.instagram_link_problem(url)
    assert checks.run_all("achievement", data)


@pytest.mark.parametrize("url", [None, "", "https://instagram.com/p/example/",
                                 "https://www.instagram.com/p/example/?img_index=1",
                                 "https://WWW.INSTAGRAM.COM/p/example/"])
def test_valid_optional_instagram_sources_are_accepted(url):
    assert checks.instagram_link_problem(url) is None


@pytest.mark.parametrize("url", ["https://user@forms.gle/a", "https://@docs.google.com/a",
                                 "https://forms.gle\\@evil.example/a", "https://forms.gle:bad/a",
                                 "https://forms.gle:99999/a", "https://forms.gle:/a",
                                 "https://forms.\tgle/a", "https://forms.gle/a b"])
def test_generic_links_reject_ambiguous_addresses(url):
    assert checks.link_problem(url)


@pytest.mark.parametrize("url", ["https://forms.gle/a?question=one%20two",
                                 "https://docs.google.com/forms/d/a#section",
                                 "https://events.apu.edu.my/open-day"])
def test_existing_allowed_link_patterns_remain_supported(url):
    assert checks.link_problem(url) is None
