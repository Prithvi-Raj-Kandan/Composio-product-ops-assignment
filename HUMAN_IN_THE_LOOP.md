# Where a human has to act

The agent answers `unknown` instead of guessing, and it flags `needs_human` when a verification loop ends without a supported claim. A person still has to do the things below. Hand-edited cells are never presented as agent output.

Time spent is for the owner to fill in. This file does not invent those numbers.

| # | Who | When | What | Time spent |
|---|---|---|---|---|
| 1 | Owner, and anyone replicating | Before a live run | Set `COMPOSIO_API_KEY` and `GEMINI_API_KEY`. `GROQ_API_KEY`, `TAVILY_API_KEY`, and `EXA_API_KEY` are optional. | owner to record |
| 2 | Owner | Before pass 2 is treated as final | Confirm the official domain for every row in `data/domain_overrides.csv`. Each of those rows is a best-guess pre-fill with status `needs human confirm`. | owner to record |
| 3 | Owner, once, at build time | Before accuracy is quoted | Label the 20 gold apps in `data/gold/gold_labels.csv`. This is a one-time build-time effort. The finished file ships in the repo. Anyone replicating the run uses that file and does not label again. Label again only if the schema changes or a new category needs an audit. | owner to record |
| 4 | Owner | While reading pass 2 | Judge gated edge cases the rubric cannot see (a free trial that requires a card, a sandbox that still needs sales). | owner to record |
| 5 | Owner | After pass 2 | Read `needs_human` on each record and the per-app log in `data/runs/`. Those are the rows the loops could not settle. | owner to record |
| 6 | Owner | Before submit | Approve the merge and publish the case study. The agent does not publish on its own in this slice. | owner to record |

## The gold set is not a replicator's job

`data/gold/gold_sample.csv` is the frozen 20-app stratified sample (seed `20261123`): two apps in each category, one dev and one test, with archetype tags. The sampler reproduces that file. It does not draw a new sample.

`data/gold/gold_labels.csv` is the sheet for the five-plus scored fields and the doc links. Until the owner fills it, `eval` reports the rows as unlabelled and does not invent an accuracy number. After it is filled and committed, a replicator runs `python -m opsresearch eval --split dev` and `--split test` against the same labels.

Dev is the split used to adjust prompts and verifiers. Test is scored at the end and is not a tuning set.
