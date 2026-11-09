"""Seed demo roles (and optionally sample items) in Firestore / the local db.

    python -m scripts.seed_roles                  # roles + one demo subscriber
    python -m scripts.seed_roles --sample-items   # also next week's SAMPLE items, with sample posters

Three demo accounts: a club rep, Student Affairs, and a regular student (no role,
used on stage to show an approval being refused).
Sample items are made up and titled "[SAMPLE] ..." so they are never mistaken for real
posters. Replace them with items extracted from real APU posters for the demo issue.
"""
import io
import sys
import textwrap
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFont

from backend import checks, config, store
from backend.issue_builder import _hm, _nice_date

POSTER_COLOURS = {"event": ((18, 32, 74), (255, 210, 90)), "announcement": ((8, 80, 65), (159, 225, 203)),
                  "achievement": ((27, 62, 143), (255, 210, 90))}


def _font(size: int, bold: bool = False):
    for path in (("arialbd.ttf" if bold else "arial.ttf"), ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sample_poster(item: dict) -> bytes:
    """A plain made-up poster for a sample item, labelled SAMPLE so it is never mistaken for a real one."""
    d, kind = item["data"], item["type"]
    bg, accent = POSTER_COLOURS[kind]
    img = Image.new("RGB", (800, 1000), bg)
    draw = ImageDraw.Draw(img)
    title = d.get("title", "").replace("[SAMPLE] ", "")
    top = d.get("organiser") or "Asia Pacific University"
    draw.text((60, 70), (top + (" presents" if kind == "event" else "")).upper(), font=_font(30, True), fill=accent)
    y = 150
    if kind == "achievement":
        draw.text((60, y), "Congratulations", font=_font(64, True), fill=accent); y += 110
    for line in textwrap.wrap(title, 16)[:4]:
        draw.text((60, y), line, font=_font(76, True), fill="white"); y += 92
    y += 40
    if kind == "event":
        lines = [_nice_date(d.get("date")).upper(),
                 (_hm(d.get("start_time")) + (" - " + _hm(d.get("end_time")) if d.get("end_time") else "")).upper(),
                 (d.get("venue") or "").upper(), (d.get("fee") or "").upper()]
    elif kind == "announcement":
        lines = textwrap.wrap(d.get("summary") or "", 30)[:3] + ([f"CLOSES {_nice_date(d.get('deadline')).upper()}"] if d.get("deadline") else [])
    else:
        lines = [d.get("result", "").upper(), d.get("team", ""), d.get("competition", "")]
    for line in [ln for ln in lines if ln]:
        draw.text((60, y), line, font=_font(40, True), fill=accent if kind != "announcement" else "white"); y += 62
    draw.text((60, 920), "SAMPLE POSTER - NOT A REAL EVENT", font=_font(26, True), fill=(255, 140, 140))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def attach_sample_poster(item: dict) -> None:
    """Locally, store it where real uploads go (local_data/posters) and show it in the newsletter."""
    if not config.LOCAL_MODE:
        return
    folder = config.LOCAL_DATA_DIR / "posters"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item['id']}.png").write_bytes(sample_poster(item))
    item["poster_uri"], item["show_poster"], item["poster_wanted"] = f"local:posters/{item['id']}.png", True, True

ROLES = {
    "clubrep.demo@mail.apu.edu.my": {"role": "club_rep", "club_id": "cloud-ai-society"},
    "studentaffairs.demo@apu.edu.my": {"role": "student_affairs"},
    # "student.demo@mail.apu.edu.my" deliberately has NO role
}
CLUB_REP, SA = list(ROLES)


def _next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def sample_items(monday: date) -> list[dict]:
    d = lambda n: (monday + timedelta(days=n)).isoformat()  # noqa: E731
    ev = lambda **k: {"category": "other", "audience": None, "end_time": None, "summary": None,  # noqa: E731
                      "registration": {"required": False, "link": None}, **k}
    return [
        {"id": "sample-ev-1", "type": "event", "status": "approved", "club_id": "cloud-ai-society",
         "submitted_by": CLUB_REP, "approved_by": SA,
         "data": ev(title="[SAMPLE] Build your first AI agent", organiser="Cloud & AI Society", date=d(2),
                    start_time="16:00", end_time="18:00", venue="Auditorium 3", fee="Free", category="tech",
                    audience="All students",
                    registration={"required": True, "link": "https://forms.gle/sample-not-real"},
                    summary="Hands-on workshop building a simple AI agent.")},
        {"id": "sample-ev-2", "type": "event", "status": "approved", "club_id": "sports-club",
         "submitted_by": CLUB_REP, "approved_by": SA,
         "data": ev(title="[SAMPLE] Futsal friendly match", organiser="APU Sports Club", date=d(0),
                    start_time="17:00", venue="Sports court", fee="Free", category="sports",
                    audience="All welcome", summary="Friendly match between club members and newcomers. All welcome.")},
        {"id": "sample-ev-3", "type": "event", "status": "approved", "club_id": "career-centre",
         "submitted_by": SA, "approved_by": SA,
         "data": ev(title="[SAMPLE] CV clinic with recruiters", organiser="Career Centre", date=d(3),
                    start_time="11:00", end_time="13:00", venue="B-04-02", fee="RM5", category="career",
                    registration={"required": True, "link": "https://apu.edu.my/sample-not-real"},
                    summary="Bring your CV for one-to-one feedback from recruiters.")},
        {"id": "sample-ev-6", "type": "event", "status": "approved", "club_id": "volunteer-club",
         "submitted_by": SA, "approved_by": SA,
         "data": ev(title="[SAMPLE] Charity fun run", organiser="APU Volunteer Club", date=d(24),
                    start_time="07:00", end_time="10:00", venue="APU main entrance", fee="RM15", category="volunteer",
                    registration={"required": True, "link": "https://forms.gle/sample-run-not-real"},
                    summary="A 5km fun run around campus. All proceeds go to a local children's home.")},
        {"id": "sample-ev-4", "type": "event", "status": "pending", "club_id": "cloud-ai-society",
         "submitted_by": CLUB_REP,
         "data": ev(title="[SAMPLE] Cloud study jam", organiser="Cloud & AI Society", date=d(4),
                    start_time="14:00", end_time="16:00", venue="Lab 6-01", fee="Free", category="tech")},
        {"id": "sample-ev-5", "type": "event", "status": "draft", "club_id": "cloud-ai-society",
         "submitted_by": CLUB_REP,
         "data": ev(title="[SAMPLE] Hack night", organiser="Cloud & AI Society", date=d(4),
                    start_time="19:00", venue="Lab 6-01", fee=None, category="tech")},
        {"id": "sample-ach-1", "type": "achievement", "status": "approved", "submitted_by": SA, "approved_by": SA,
         "data": {"title": "[SAMPLE] APU team wins regional hackathon", "team": "Team Sample",
                  "competition": "Sample Regional Hackathon", "result": "1st place", "date": d(-5),
                  "instagram_url": "https://www.instagram.com/p/sample-not-real/", "consent_confirmed": True}},
        {"id": "sample-ann-1", "type": "announcement", "status": "approved", "submitted_by": SA, "approved_by": SA,
         "data": {"title": "[SAMPLE] Student ambassador applications open", "organiser": "Student Affairs",
                  "summary": "Apply to represent APU at open days.", "deadline": f"{d(10)}T23:59:00",
                  "action_link": "https://forms.office.com/sample-not-real", "audience": "All students"}},
    ]


if __name__ == "__main__":
    for email, record in ROLES.items():
        store.db().collection("roles").document(email).set(record)
        print("role set:", email, record)
    store.upsert_subscriber({"email": "student.demo@mail.apu.edu.my", "active": True})
    print("demo subscriber added")

    if "--sample-items" in sys.argv:
        monday = _next_monday(date.today())
        for item in sample_items(monday):
            item["issues"] = checks.run_all(item["type"], item["data"])
            if item["id"] != "sample-ann-1":       # left as typed-in-chat (no poster) to show the plain notice
                attach_sample_poster(item)
            store.create_item(item)
            print(f"sample {item['type']} ({item['status']}): {item['data']['title']}")
        print("sample items added for the week of", monday)
