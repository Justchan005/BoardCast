"""Newspaper article ordering, completeness, poster permissions and shared factual copy."""
from collections import Counter
from datetime import date
from html.parser import HTMLParser

import pytest

from backend import config, issue_builder, tokens
from shared.schemas import EditorPlan


@pytest.fixture(autouse=True)
def render_settings(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://boardcast.test")
    monkeypatch.setattr(tokens, "_SECRET", b"newspaper-render-test-only")


def event(item_id, *, poster=False, show_poster=None):
    return {"id": item_id, "type": "event", "status": "approved",
            "poster_uri": f"local:posters/{item_id}.jpg" if poster else None,
            "show_poster": poster if show_poster is None else show_poster,
            "description": "Bring a laptop and meet the club.", "data": {
                "title": f"Workshop {item_id}", "organiser": "Computing Club", "date": "2099-11-11",
                "start_time": "16:00", "end_time": "18:00", "venue": "Hall A", "fee": "Free",
                "registration": {"required": True, "link": "https://forms.gle/workshop"}}}


def achievement(item_id):
    return {"id": item_id, "type": "achievement", "status": "approved", "data": {
        "title": f"Campus win {item_id}", "team": "Campus Robotics", "competition": "Robotics Cup",
        "result": "Finalist", "consent_confirmed": True,
        "instagram_url": "https://www.instagram.com/p/campuswin/"}}


def announcement(item_id):
    return {"id": item_id, "type": "announcement", "status": "approved", "data": {
        "title": f"Opportunity {item_id}", "organiser": "Student Affairs", "deadline": "2099-11-30T23:59:00",
        "summary": "Help welcome new students.", "action_link": "https://forms.office.com/campus"}}


def edition(*items):
    return {"events": [item for item in items if item["type"] == "event"],
            "achievements": [item for item in items if item["type"] == "achievement"],
            "announcements": [item for item in items if item["type"] == "announcement"],
            "week_start": date(2099, 11, 9), "week_end": date(2099, 11, 15),
            "newsletter_template": "newspaper"}


class Articles(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids, self.links, self.images = [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-story-id" in values:
            self.ids.append(values["data-story-id"])
        if tag == "a":
            self.links.append(values.get("href", ""))
        if tag == "img":
            self.images.append(values.get("src", ""))


def render(channel, issue, spotlight=None):
    plan = EditorPlan(spotlight_item_id=spotlight)
    if channel == "email":
        return issue_builder.render_email(issue, plan, {"email": "reader@mail.apu.edu.my"},
            issue_id="edition-test", unsubscribe_url="https://boardcast.test/unsubscribe?token=test",
            prefs_url="https://boardcast.test/preferences?token=test")
    return issue_builder.render_magazine(issue, plan, "edition-test")


@pytest.mark.parametrize("channel", ["email", "web"])
@pytest.mark.parametrize("items,spotlight,expected", [
    ([], None, []),
    ([event("one")], None, ["one"]),
    ([achievement("win")], None, ["win"]),
    ([announcement("notice")], None, ["notice"]),
    ([event("one"), announcement("notice")], None, ["one", "notice"]),
    ([event("one"), event("two"), achievement("win"), announcement("notice")],
     None, ["one", "two", "win", "notice"]),
    ([event("one"), event("two"), achievement("win"), announcement("notice"), announcement("other")],
     "two", ["two", "one", "win", "notice", "other"]),
    ([announcement("notice"), achievement("win"), achievement("other")], None, ["win", "other", "notice"]),
])
def test_newspaper_renders_each_article_once_in_editorial_order(channel, items, spotlight, expected):
    html = render(channel, edition(*items), spotlight)
    articles = Articles(html)
    assert articles.ids == expected
    assert Counter(articles.ids) == Counter(item["id"] for item in items)
    assert "Undefined" not in html and "None" not in html


def test_newspaper_context_uses_selected_lead_without_mutating_the_issue():
    first, selected, win, notice = event("first"), event("selected"), achievement("win"), announcement("notice")
    issue = edition(first, selected, win, notice)
    context = issue_builder._context(issue, EditorPlan(spotlight_item_id="selected"))
    assert context["newspaper_lead"] is selected
    assert context["newspaper_stories"] == [first, win, notice]
    assert issue["events"] == [first, selected]
    assert context["events"] == [selected, first]  # existing Classic event ordering remains available


@pytest.mark.parametrize("channel", ["email", "web"])
def test_newspaper_respects_poster_display_permission(channel):
    html = render(channel, edition(event("visible", poster=True),
                                  event("hidden", poster=True, show_poster=False), event("absent")))
    images = Articles(html).images
    assert any("/p/visible.jpg" in url for url in images)
    assert not any("/p/hidden.jpg" in url or "/p/absent.jpg" in url for url in images)
    assert Articles(html).ids == ["visible", "hidden", "absent"]


@pytest.mark.parametrize("channel", ["email", "web"])
def test_newspaper_keeps_factual_copy_and_actions(channel):
    html = render(channel, edition(event("event"), achievement("win"), announcement("notice")))
    for fact in ("Computing Club", "Hall A", "Free", "Bring a laptop and meet the club.",
                 "Campus Robotics", "Robotics Cup", "Finalist", "Help welcome new students."):
        assert fact in html
    links = Articles(html).links
    expected_query = f"?s={tokens.sign('reader@mail.apu.edu.my', 'save')}" if channel == "email" else ""
    base = "https://boardcast.test" if channel == "email" else ""
    assert f"{base}/r/event{expected_query}" in links
    assert f"{base}/cal/event.ics{expected_query}" in links
    assert f"{base}/r/notice{expected_query}" in links
    assert "https://www.instagram.com/p/campuswin/" in links


@pytest.mark.parametrize("channel", ["email", "web"])
def test_newspaper_escapes_copy_and_omits_unsafe_instagram_links(channel):
    item, win = event("unsafe-copy"), achievement("unsafe-link")
    item["data"]["title"] = '<script>alert("title")</script>'
    item["description"] = 'Bring <img src=x onerror=alert(1)>; details https://forms.gle/campus.'
    win["data"]["instagram_url"] = "javascript:alert(document.domain)"
    html = render(channel, edition(item, win))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "&lt;img" in html
    links = Articles(html).links
    assert "https://forms.gle/campus" in links
    assert not any(link.startswith("javascript:") for link in links)
