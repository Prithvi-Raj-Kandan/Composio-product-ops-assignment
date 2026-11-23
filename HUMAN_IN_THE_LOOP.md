# Where a human has to act

The agent answers `unknown` rather than guessing. It flags `needs_human` when a verification loop ends without a supported claim. A person still has to do the steps below, and no hand-edited cell is ever presented as agent output.

| # | Who | When | What |
|---|---|---|---|
| 1 | Anyone replicating | Before a live run | Set `COMPOSIO_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `TAVILY_API_KEY` in the shell. To use the GitHub Action, add the same four as repository secrets. |
| 2 | Anyone replicating, once | Before the first run | Run `python -m opsresearch connect-tavily`. It creates a Composio auth config and connected account for the Tavily toolkit from your Tavily key, so search goes through Composio. Tavily's free plan is 1,000 credits a month. A full two-pass run can use most of it, and search results are cached in `cache/_search/` so a rerun is free. |
| 3 | Owner (done) and replicators | Before pass 1 | Confirm the official site for ambiguous entities in `data/domain_overrides.csv`. The owner confirmed 6 (iPayX, fanbasis → Commas, Paygent → GoPaygent on NMI, Waterfall.io, Higgsfield, YouTube Transcript → TranscriptAPI). Each affected record shows `provenance.source = human_override`, and the page tags it "human override". |
| 4 | Owner, once at build time (shipped) | Before accuracy is quoted | Hand-label the 20-app stratified gold sample from official docs (Google Sheet, then `python -m opsresearch import-gold`). The labels ship in `data/gold/gold_labels.csv`. Replicators reuse them and don't label again, unless the schema changes. |
| 5 | Owner / reviewer | After pass 2 | Read the rows flagged `needs_human` and their run logs in `data/runs/`. These are the rows the loops could not settle. |
| 6 | Owner | While reading results | Judge edge cases the rubric can't see, such as a trial that needs a card or a sandbox that still needs sales. |
| 7 | Owner | Before submit | Approve the merges and the GitHub Pages publish. The agent does not publish on its own. |

## The gold set is not a replicator's job

`data/gold/gold_sample.csv` is the frozen 20-app stratified sample (seed `20261123`): two apps per category, one dev and one test, with archetype tags. Dev was used to look at errors. Test was held out and scored once. `eval` reports null accuracy for unlabelled cells; it never invents a score.
