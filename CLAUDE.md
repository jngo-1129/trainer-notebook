# Umamusume Advisor

Local, advisory-only helper for Umamusume: Pretty Derby (Global). **Account safety overrides everything** — see SAFETY.md (create it from the project prompt if missing). Never touch the game process, network, files, input, or account credentials.

## Workflow
- New feature → plan first (plan mode), then build.
- After every feature or dependency change → run the `safety-auditor` agent. Don't call a feature done until it PASSes.
- After changing the screenshot importer → run the `ocr-tester` agent.
- Game data/meta/banner updates or fact checks → use the `game-data-researcher` agent (public sites only, no logins).
- After a feature → `/code-review`. After anything touching files, screenshots, or network → `/security-review`.
- Probability/simulation math → verify with the `data:statistical-analysis` skill; charts → `dataviz`.
