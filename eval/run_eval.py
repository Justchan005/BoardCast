"""Measure how accurately the extract prompt reads real APU posters.

    python -m eval.run_eval                 # uses the version in prompts/extract.md
    python -m eval.run_eval --limit 5       # quick check

Inputs:  eval/posters/*.jpg|png  +  eval/expected.jsonl
Outputs: eval/results/extract_<version>.md     per-field accuracy + every miss
         eval/outputs/extract_<version>.jsonl  raw model output (for the deck / debugging)

Workflow: run -> read the misses -> change prompts/extract.md -> bump its version -> run again.
Keep every results file; the before/after table is evidence for judging criteria 4 and 5.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("AUDIT_DISABLED", "true")  # no Firestore needed for eval

from backend import extract                    # noqa: E402
from backend.qr_rules import apply_qr_links     # noqa: E402
from shared import prompts                     # noqa: E402
from shared.schemas import ItemType            # noqa: E402

from .scoring import ALL_FIELDS, FIELDS_BY_TYPE, get, score, summarise  # noqa: E402

HERE = Path(__file__).resolve().parent


def qr_links(image: bytes) -> list[str]:
    try:
        from backend import qr
        return qr.decode_links(image)
    except Exception:          # zbar not installed: score the model alone
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", default=str(HERE / "expected.jsonl"))
    ap.add_argument("--posters", default=str(HERE / "posters"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rescore", action="store_true",
                    help="score the saved outputs of this prompt version again (after a scoring fix); no Gemini calls")
    args = ap.parse_args()

    version = prompts.load("extract").version
    cases = [json.loads(l) for l in Path(args.expected).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        cases = cases[: args.limit]

    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "outputs").mkdir(exist_ok=True)
    rows, misses, outputs, latencies = [], [], [], []
    saved = {}
    if args.rescore:
        saved_path = HERE / "outputs" / f"extract_{version}.jsonl"
        saved = {o["file"]: o for o in map(json.loads, saved_path.read_text(encoding="utf-8").splitlines()) if o}

    for case in cases:
        path = Path(args.posters) / case["file"]
        image = path.read_bytes()
        if args.rescore:                                        # same model answers, scored again
            got, links = saved[case["file"]]["got"], saved[case["file"]]["qr"]
        else:
            t0 = time.time()
            try:
                got = extract.extract(ItemType(case.get("type", "event")), image=image,
                                      mime_type=mimetypes.guess_type(path.name)[0] or "image/jpeg").model_dump(mode="json")
            except Exception as e:
                got = {"_error": str(e)}
            latencies.append(time.time() - t0)
            links = qr_links(image)
            apply_qr_links(case.get("type", "event"), got, links)   # same rule as the API
        result = score(case["expected"], got, FIELDS_BY_TYPE[case.get("type", "event")])
        rows.append(result)
        outputs.append({"file": case["file"], "got": got, "qr": links})
        for f, ok in result.items():
            if not ok:
                misses.append((case["file"], f, get(case["expected"], f), get(got, f)))
        print(f"{case['file']}: {sum(result.values())}/{len(result)} fields correct")

    summary = summarise(rows)
    if not args.rescore:  # a re-score never overwrites what the model actually answered
        with open(HERE / "outputs" / f"extract_{version}.jsonl", "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(o, default=str) + "\n" for o in outputs)

    lines = [f"# Extract prompt {version}", "",
             f"Run {datetime.now():%Y-%m-%d %H:%M} · {len(cases)} posters · model `{extract.config.GEMINI_MODEL}` · "
             + (f"avg {sum(latencies) / max(len(latencies), 1):.1f}s per poster" if latencies else
              "re-scored from the saved outputs of this version (no new Gemini calls) after a scoring fix"), "",
             "| Field | Correct | Accuracy |", "| --- | --- | --- |"]
    for f in ALL_FIELDS + ["overall"]:
        if f in summary:
            c, t = summary[f]
            lines.append(f"| {f} | {c}/{t} | {100 * c / t:.0f}% |")
    lines += ["", "## Misses", "", "| Poster | Field | Expected | Got |", "| --- | --- | --- | --- |"]
    lines += [f"| {p} | {f} | {e} | {g} |" for p, f, e, g in misses] or ["| none | | | |"]
    out = HERE / "results" / f"extract_{version}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    c, t = summary["overall"]
    print(f"\nOverall {c}/{t} = {100 * c / max(t, 1):.0f}%  ->  {out}")


if __name__ == "__main__":
    main()
