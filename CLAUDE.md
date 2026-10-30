# slim-quality — Claude context

> **Read first.** Jamie is the **designer**; Claude writes the code and explains it. Raise any design
> change as a new decision (with a recommendation) before building it. **Short replies; Jamie is
> token-constrained.** Decisions live in slim-data-oss `docs/DECISIONS.md` (D-109, D-110, D-113, D-114); who decided
> what in slim-lims-oss `docs/DEV_DIARY.md`. Working defaults and the OSS guard: slim-lims-oss CLAUDE.md.

## What this is

SLIM's quality module. Part 1 (roadmap phase 3): QC charts, trend and forecast tools, change control
over the profile and limits, events, notes, signed QC reviews. Part 2 (phase 5), started: vertical-audit
records (D-113) and nonconformities with impact assessment, actions and effectiveness (D-114); still to
come: PT, uncertainty from QC, complaints, management review pack, the §7.11 pack. Part 3 (parked):
warnings on the review screen before release. A library: the host (slim-lims-oss) adapts slim-data's QC
history to `QcSource` and composes the vertical audit from each module's read API.

## Rules for the build

- `domain/` and `monitors/` import only stdlib + pydantic (`tests/test_architecture.py`).
- Values are `Dec`; tools are `name@version`, never edited once released (a change is a new version).
- Thresholds, windows and which tools run are profile **data** (`profiles/starter.json` until one is
  approved); never `if demo:`. Demo numbers are invented.
- Append-only tables; repositories flush, the host commits. Portable schema (SQLite + Postgres).
- Every push: ruff, full tests, 95 % branch coverage on domain + monitors.
