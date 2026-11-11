# Evidence

Files here are generated from real runs. Nothing in this folder is written by hand.

| File | How to produce it | What it shows |
| --- | --- | --- |
| `output_checks.md`, `output_checks.json` | `python -m eval.output_checks` (needs a Gemini credential in `.env`) | Normal, difficult, missing-data and failure cases: expected, actual, gaps |
| `run-log-normal.md`, `run-log-difficult.md`, `run-log-missing-data.md`, `run-log-failure.md` | Written by the same command | One input linked to every model call, tool call, code check and human decision |
| Any other `run-log-<id>.md` | **Download run log (.md)** on `/admin/trace/<id>` | The trace of a submission you made by hand |

If this folder holds only this file, the checks have not been run on this copy yet.
