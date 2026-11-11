"""Distinct built-in layouts keep approved content complete in email and web."""
from collections import Counter

import pytest

from backend import issue_builder
from shared.schemas import EditorPlan
from tests.test_newspaper_layout import Articles, achievement, announcement, edition, event, render, render_settings


@pytest.mark.parametrize("template", ["spotlight", "bulletin"])
@pytest.mark.parametrize("channel", ["email", "web"])
@pytest.mark.parametrize("items", [[], [event("one")], [achievement("win")], [announcement("notice")],
    [event("one", poster=True), event("two"), achievement("win"), announcement("notice")]])
def test_layouts_keep_every_story_once(template, channel, items):
    issue = {**edition(*items), "newsletter_template": template}
    html = render(channel, issue)
    articles = Articles(html)
    assert Counter(articles.ids) == Counter(item["id"] for item in items)
    assert f'data-newsletter-template="{template}"' in html
    assert "Undefined" not in html and "None" not in html
    if not items:
        assert "There are no new submissions in this edition." in html


@pytest.mark.parametrize("template", ["spotlight", "bulletin"])
@pytest.mark.parametrize("channel", ["email", "web"])
def test_layouts_preserve_facts_actions_and_poster_permissions(template, channel):
    issue = {**edition(event("visible", poster=True), event("hidden", poster=True, show_poster=False),
                       event("absent", show_poster=True), achievement("win"), announcement("notice")),
             "newsletter_template": template}
    html = render(channel, issue)
    articles = Articles(html)
    assert len(articles.images) == 1 and "/p/visible.jpg" in articles.images[0]
    for fact in ("Computing Club", "Hall A", "Free", "Bring a laptop and meet the club.",
                 "Campus Robotics", "Robotics Cup", "Finalist", "Help welcome new students."):
        assert fact in html
    for route in ("/r/visible", "/cal/visible.ics", "/r/notice"):
        assert any(route in link for link in articles.links)
    assert "https://www.instagram.com/p/campuswin/" in articles.links
    if channel == "email":
        assert "https://boardcast.test/unsubscribe?token=test" in articles.links
        assert "https://boardcast.test/preferences?token=test" in articles.links
        assert any("?s=" in link for link in articles.links)
    else:
        assert not any("?s=" in link for link in articles.links)


@pytest.mark.parametrize("template", ["spotlight", "bulletin"])
@pytest.mark.parametrize("channel", ["email", "web"])
def test_layouts_remain_distinct_with_custom_appearance_and_placement(template, channel):
    issue = {**edition(event("first"), event("lead"), announcement("notice")), "newsletter_template": template,
        "newsletter_design": {"lead_id": "lead", "story_order": ["notice", "first", "missing", "notice"],
            "columns": 1, "accent_color": "#AB1234", "headline_font": "georgia",
            "header_rows": [{"cells": [["intro", "date", "name"]]}],
            "story_rows": [{"cells": [["title", "details", "picture"]]}]}}
    html = render(channel, issue)
    assert Articles(html).ids == ["lead", "notice", "first"]
    assert f'data-newsletter-template="{template}"' in html
    assert "#AB1234" in html and "Georgia" in html
    if template == "spotlight":
        assert ("pv-lead" if channel == "web" else "More from campus") in html
    else:
        assert ("pv-bulletin" if channel == "web" else "pv-email-row") in html


@pytest.mark.parametrize("template", ["spotlight", "bulletin"])
@pytest.mark.parametrize("channel", ["email", "web"])
def test_layouts_escape_untrusted_copy(template, channel):
    item = event("unsafe", poster=True)
    item["data"]["title"] = '<script>alert("title")</script>'
    item["description"] = '<img src=x onerror=alert(1)> Details: https://forms.gle/campus.'
    win = achievement("win")
    win["data"]["instagram_url"] = "javascript:alert(1)"
    html = render(channel, {**edition(item, win), "newsletter_template": template})
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "&lt;img" in html
    assert not any(link.startswith("javascript:") for link in Articles(html).links)
    assert "https://forms.gle/campus" in Articles(html).links


def test_builtin_variant_defaults_are_independent():
    assert issue_builder.default_newsletter_design("spotlight")["columns"] == 2
    assert issue_builder.default_newsletter_design("bulletin")["columns"] == 1
    assert issue_builder.newsletter_design(None, "spotlight") is None
    assert issue_builder.newsletter_design(None, "bulletin") is None


@pytest.mark.parametrize("accent", ["#777777", "#FFFFFF", "#000000", "#FFFF00", "#1F4FD8", "#AA2277", "#33CC66"])
def test_accent_surfaces_choose_accessible_foreground(accent):
    design = issue_builder.default_newsletter_design("spotlight")
    design["accent_color"] = accent
    style = issue_builder.variant_style(design)
    def luminance(color):
        channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(channels, (.2126, .7152, .0722)))
    a, b = sorted((luminance(accent), luminance(style["accent_ink"])))
    assert (b + .05) / (a + .05) >= 4.5
    assert design["accent_color"] == accent


def test_spotlight_preserves_single_column_at_tablet_width():
    issue = {**edition(event("one"), event("two")), "newsletter_template": "spotlight",
             "newsletter_design": {"columns": 1}}
    html = render("web", issue)
    assert "--pv-columns:1;--pv-tablet-columns:1" in html
