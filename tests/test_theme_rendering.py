"""Render reusable placeholder arrangements without losing approved copy or links."""
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from itertools import permutations

import pytest

from backend import config, issue_builder, tokens
from shared.schemas import EditorPlan


@pytest.fixture(autouse=True)
def local_render_settings(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://boardcast.test")
    monkeypatch.setattr(tokens, "_SECRET", b"theme-render-test-only")


def event(item_id, poster=True):
    return {"id": item_id, "type": "event", "status": "approved", "show_poster": poster,
            "poster_uri": f"local:posters/{item_id}.jpg", "description": f"UNIQUE_COPY_{item_id}", "data": {
                "title": f"Workshop {item_id}", "organiser": "Computing Club", "date": "2099-11-11",
                "start_time": "16:00", "end_time": "18:00", "venue": "Hall A", "fee": "Free",
                "registration": {"required": True, "link": "https://forms.gle/workshop"}}}


def edition(*items, **design):
    return {"events": list(items), "achievements": [], "announcements": [],
            "week_start": date(2099, 11, 9), "week_end": date(2099, 11, 15),
            "newsletter_template": "custom", "newsletter_design": design}


def render(channel, issue):
    if channel == "email":
        return issue_builder.render_email(issue, EditorPlan(), {"email": "reader@mail.apu.edu.my"},
            issue_id="theme-test", unsubscribe_url="https://boardcast.test/unsubscribe?token=test",
            prefs_url="https://boardcast.test/preferences?token=test")
    return issue_builder.render_magazine(issue, EditorPlan(), "theme-test")


class ThemeMarkup(HTMLParser):
    """Capture output grouping independently of the Jinja implementation."""
    def __init__(self, html):
        super().__init__()
        self.depth = 0
        self.story = None
        self.header_depth = None
        self.story_depth = None
        self.ids, self.header_blocks, self.links, self.images = [], [], [], []
        self.story_blocks, self.story_rows = {}, {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        void = tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
        if not void:
            self.depth += 1
        if attrs.get("data-theme-section") == "header":
            self.header_depth = self.depth
        if "data-story-id" in attrs:
            self.story = attrs["data-story-id"]
            self.story_depth = self.depth
            self.ids.append(self.story)
            self.story_blocks[self.story] = []
            self.story_rows[self.story] = []
        if "data-theme-block" in attrs:
            if self.story:
                self.story_blocks[self.story].append(attrs["data-theme-block"])
            elif self.header_depth is not None:
                self.header_blocks.append(attrs["data-theme-block"])
        if "data-theme-row" in attrs and self.story:
            self.story_rows[self.story].append(int(attrs["data-cells"]))
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag == "img":
            self.images.append(attrs.get("src", ""))

    def handle_endtag(self, tag):
        if self.depth == self.story_depth:
            self.story = self.story_depth = None
        if self.depth == self.header_depth:
            self.header_depth = None
        self.depth -= 1


@pytest.mark.parametrize("channel", ["web", "email"])
@pytest.mark.parametrize("order", list(permutations(["title", "picture", "details"])))
def test_all_story_placeholder_orders_render_with_copy_once(channel, order):
    issue = edition(event("one"), event("two"), story_rows=[{"cells": [[block]]} for block in order],
                    header_rows=[{"cells": [["intro"], ["name", "date"]]}])
    html = render(channel, issue)
    markup = ThemeMarkup(html)
    assert markup.header_blocks == ["intro", "name", "date"]
    assert markup.ids == ["one", "two"]
    assert all(blocks == list(order) for blocks in markup.story_blocks.values())
    assert all(rows == [1, 1, 1] for rows in markup.story_rows.values())
    assert html.count("UNIQUE_COPY_one") == html.count("UNIQUE_COPY_two") == 1


@pytest.mark.parametrize("channel", ["web", "email"])
@pytest.mark.parametrize("columns", [1, 2, 3])
def test_split_rows_preserve_column_placement_and_all_actions(channel, columns):
    items = [event(item_id) for item_id in ["a", "b", "c", "d"]]
    issue = edition(*items, columns=columns, lead_id="d", story_order=["c", "b", "a"],
                    column_map={"a": 1, "b": 2, "c": 3},
                    story_rows=[{"cells": [["title", "details"], ["picture"]]}])
    html = render(channel, issue)
    markup = ThemeMarkup(html)
    assert markup.ids[0] == "d"
    assert Counter(markup.ids) == Counter(["a", "b", "c", "d"])
    assert all(blocks == ["title", "details", "picture"] for blocks in markup.story_blocks.values())
    assert all(rows == [2] for rows in markup.story_rows.values())
    assert len(markup.images) == 4
    for item_id in ["a", "b", "c", "d"]:
        assert html.count(f"UNIQUE_COPY_{item_id}") == 1
        register = next(link for link in markup.links if f"/r/{item_id}" in link)
        calendar = next(link for link in markup.links if f"/cal/{item_id}.ics" in link)
        assert ("?s=" in register) == (channel == "email")
        assert ("?s=" in calendar) == (channel == "email")
    if channel == "email":
        assert 'data-columns="3"' not in html
        assert "https://boardcast.test/unsubscribe?token=test" in markup.links
        assert "https://boardcast.test/preferences?token=test" in markup.links
    else:
        assert f'data-columns="{columns}"' in html


@pytest.mark.parametrize("channel", ["web", "email"])
def test_hidden_poster_collapses_its_cell_without_dropping_copy(channel):
    html = render(channel, edition(event("hidden", poster=False)))
    markup = ThemeMarkup(html)
    assert markup.images == []
    assert markup.story_blocks["hidden"] == ["title", "details"]
    assert markup.story_rows["hidden"] == [1]
    assert "UNIQUE_COPY_hidden" in html


@pytest.mark.parametrize("channel", ["web", "email"])
def test_empty_theme_is_renderable_and_does_not_invent_content(channel):
    html = render(channel, edition())
    markup = ThemeMarkup(html)
    assert markup.ids == []
    assert markup.header_blocks == ["name", "date", "intro"]
    assert "There are no new submissions in this edition." in html
    assert "MORE FROM CAMPUS" not in html


@pytest.mark.parametrize("channel", ["web", "email"])
def test_theme_escapes_content_and_uses_selected_font_and_colours(channel):
    item = event("safe")
    item["data"]["title"] = 'Club <script>alert("x")</script>'
    item["description"] = '<img src=x onerror=alert(1)> https://forms.gle/workshop'
    html = render(channel, edition(item, headline_font="times", body_font="verdana",
                    accent_color="#672D91", text_color="#19242B", paper_color="#FFF5E8"))
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x" in html
    assert "Times New Roman" in html and "Verdana" in html
    assert "#672D91" in html and "#19242B" in html and "#FFF5E8" in html
    assert "https://fonts.googleapis.com" not in html
