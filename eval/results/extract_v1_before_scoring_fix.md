# Extract prompt v1

Run 2026-11-08 01:57 · 14 posters · model `gemini-3.5-flash` · avg 8.3s per poster

| Field | Correct | Accuracy |
| --- | --- | --- |
| title | 11/12 | 92% |
| organiser | 9/12 | 75% |
| date | 11/11 | 100% |
| start_time | 10/10 | 100% |
| end_time | 9/9 | 100% |
| venue | 10/10 | 100% |
| fee | 10/10 | 100% |
| category | 8/10 | 80% |
| registration.link | 10/10 | 100% |
| deadline | 3/3 | 100% |
| action_link | 3/3 | 100% |
| team | 1/1 | 100% |
| competition | 1/1 | 100% |
| result | 1/1 | 100% |
| overall | 97/103 | 94% |

## Misses

| Poster | Field | Expected | Got |
| --- | --- | --- | --- |
| gdgoc-recruiting.png | organiser | ['Google Developer Groups On Campus Asia Pacific University', 'Google Developer Groups On Campus'] | Google Developer Groups On Campus - Asia Pacific University |
| gdgoc-deploy-scale-monitor.png | organiser | ['Google Developer Groups On Campus Asia Pacific University', 'Google Developer Groups On Campus'] | Google Developer Groups On Campus - Asia Pacific University |
| apac-forensic-game.png | title | ['Forensic Game 2026: The Perfect Alibi', 'Can You Solve the Murder Mystery?'] | Can You Solve The Murder Mystery? Forensic Game 2026: The Perfect Alibi |
| apac-forensic-game.png | category | social | other |
| apac-zurich-panel.png | category | career | tech |
| apubcc-ai-blockchain-bootcamp.png | organiser | ['APU Blockchain & Cryptocurrency Club', 'APUBCC', 'APU Blockchain & Cryptocurrency Club (APUBCC)', 'APU Blockchain and Cryptocurrency Club'] | APUBCC & Superteam MY |
