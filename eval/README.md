# Prompt accuracy eval

Shows judges how well the extract prompt reads **real APU posters**, and how each prompt change improved it.

## Set up (once)
1. Photograph 15–20 real posters from the lift lobbies. Save them in `eval/posters/` (jpg or png).
2. Copy `expected.example.jsonl` to `expected.jsonl`. Add one line per poster with the **correct** answers, typed by hand from the poster. Have a second teammate check them.
   - Only include fields the poster actually shows. Write `null` when the poster deliberately lacks it (e.g. no date) so a guess counts as wrong.
   - Times in 24-hour `HH:MM`, dates `YYYY-MM-DD`.

## Run
```bash
python -m eval.run_eval            # scores the version in prompts/extract.md
```
Writes `eval/results/extract_<version>.md` (per-field accuracy + every miss) and the raw outputs.

## Improve
1. Read the misses. Look for a pattern (e.g. "4pm" read as 04:00, venue missing the block letter).
2. Add a rule or an example to `prompts/extract.md` that fixes the pattern. Bump `version` and write the `changelog`.
3. Run again. Keep every results file. Never edit `expected.jsonl` to make a score go up.

Put the before/after table in the deck: e.g. "v1 78% → v3 95% overall; start time 70% → 100%".
