"""Add pending demo submissions so the review queue and BoardCast Assistant have something to show.

    python -m scripts.seed_assistant_demo                      # into local_data/
    python -m scripts.seed_assistant_demo --data-dir <folder>  # into another local data folder

LOCAL_MODE only. Uses the demo posters in ../evidence (or any PNG passed with --posters). Each
submission goes through the normal submit path (code checks, run trace). When Gemini is configured,
the pre-review agent runs on each one before the script ends, so the AI briefs are ready to demo.
One submission deliberately differs from its poster (date) and reuses a poster, so the pre-review
flags it and the assistant shows how red flags must be acknowledged. Safe to run twice: titles that
are already waiting or approved are skipped.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSTERS = ROOT.parent / "evidence"
REP = "clubrep.demo@mail.apu.edu.my"

SUBMISSIONS = [
    {"poster": "BoardCast_Demo_Poster_AI_App_28Nov2026.png", "file": "demo-ai-app.png", "type": "event",
     "description": "A beginner-friendly workshop where you build a small AI app. Bring your laptop.",
     "data": {"title": "Build Your First AI App", "organiser": "Cloud & AI Society", "date": "2026-11-28",
              "start_time": "14:00", "end_time": "16:00", "venue": "Auditorium 3", "fee": "Free",
              "category": "tech", "audience": "All APU students", "registration": {"required": False},
              "summary": "Beginner-friendly workshop. Bring your laptop. No experience needed. Walk-ins welcome."}},
    {"poster": "BoardCast_Demo_Poster_Club_Recruitment.png", "file": "demo-recruitment.png", "type": "announcement",
     "description": "Meet other students and learn cloud and AI through practical projects. Beginners welcome.",
     "data": {"title": "New Members Welcome", "organiser": "Cloud & AI Society", "deadline": "2026-11-27T23:59:00",
              "summary": "Meet other students, explore cloud and AI, and learn together through practical projects. "
                         "Contact the club through Student Affairs.", "audience": "All APU students"}},
    {"poster": "BoardCast_Demo_Poster_Team_Spark_Achievement.png", "file": "demo-team-spark.png", "type": "achievement",
     "description": "Team Spark won gold for creative ideas for campus life.",
     "data": {"title": "Team Spark Wins Gold", "team": "Team Spark", "competition": "Campus Innovation Challenge 2026",
              "result": "Gold Award", "date": "2026-11-14", "consent_confirmed": True}},
    # Deliberately inconsistent: the poster says 28 November and the same poster is already submitted.
    {"poster": "BoardCast_Demo_Poster_AI_App_28Nov2026.png", "file": "demo-ai-app.png", "type": "event",
     "description": "A second session of the AI app workshop for students who missed the first one.",
     "data": {"title": "Build Your First AI App (Second Session)", "organiser": "Cloud & AI Society",
              "date": "2026-11-29", "start_time": "14:00", "end_time": "16:00", "venue": "Auditorium 3",
              "fee": "Free", "category": "tech", "registration": {"required": False}}},
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", help="Local data folder (default: local_data/)")
    parser.add_argument("--posters", default=str(DEFAULT_POSTERS), help="Folder with the demo poster PNGs")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["LOCAL_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ.setdefault("LOCAL_MODE", "true")
    os.environ["PREREVIEW_AUTO"] = "false"          # run the pre-review here, synchronously
    sys.path.insert(0, str(ROOT))

    from backend import config, extract, main as api, prereview, store
    from shared.schemas import ItemType
    if not config.LOCAL_MODE:
        print("Refusing to run: set LOCAL_MODE=true. This script only seeds a local demo.")
        return 2
    api.publish = lambda *a, **k: None
    posters = Path(args.posters)
    folder = config.LOCAL_DATA_DIR / "posters"
    folder.mkdir(parents=True, exist_ok=True)
    existing = {((i.get("data") or {}).get("title"), i.get("submitted_by"))
                for i in store.list_items(["pending", "approved", "draft"])}
    created = []
    for spec in SUBMISSIONS:
        title = spec["data"]["title"]
        if (title, REP) in existing:
            print(f"skip   {title} (already in the queue)")
            continue
        source = posters / spec["poster"]
        uri = None
        if source.is_file():
            target = folder / spec["file"]
            if not target.exists():
                shutil.copyfile(source, target)
            uri = f"local:posters/{spec['file']}"
        else:
            print(f"note   {spec['poster']} not found; {title} is submitted without a poster")
        result = api.submit_item(api.SubmitReq(user_email=REP, type=ItemType(spec["type"]), data=spec["data"],
                                               poster_uri=uri, description=spec["description"],
                                               show_poster=bool(uri)), None)
        print(f"added  {title}: {result['status']}" + (f" ({'; '.join(result['issues'])})" if result["issues"] else ""))
        if result["status"] == "pending":
            created.append(result["id"])
    if created and extract.ai_configured():
        for item_id in created:
            brief = prereview.run(item_id, trigger="seed", actor=REP)
            title = (store.get_item(item_id) or {}).get("data", {}).get("title")
            print(f"brief  {title}: {brief.get('recommendation') or brief.get('status')}"
                  + (f" ({len(brief.get('flags') or [])} flags)" if brief.get("flags") else ""))
    elif created:
        print("Gemini is not configured, so no AI briefs were made. The queue still works without them.")
    print("Done. Open /admin?demo=student-affairs and ask the assistant: How many posters do I need to approve today?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
