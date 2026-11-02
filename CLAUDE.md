# slim-quality — Claude context

> **Read first.** Jamie is the **designer**; Claude writes the code and explains it. Raise any design
> change as a new decision (with a recommendation) before building it. **Short replies; Jamie is
> token-constrained.** Decisions live in slim-data-oss `docs/DECISIONS.md` (D-109, D-110, D-113, D-114, D-125…D-130); who decided
> what in slim-lims-oss `docs/DEV_DIARY.md`. Working defaults and the OSS guard: slim-lims-oss CLAUDE.md.

## What this is

SLIM's quality module. Part 1 (roadmap phase 3): QC charts, trend and forecast tools, change control
over the profile and limits, events, notes, signed QC reviews. Part 2 (phase 5), started: vertical-audit
records (D-113) and nonconformities with impact assessment, actions and effectiveness (D-114); batch 5a:
the equipment register with its use check (D-125, D-126), reference lots that slim-prep reads through the
host (D-127), PT rounds and z-scores charted by the same tools on fixed limits (D-128, D-129), complaints
whose outcome nobody involved approves (D-130). Still to come: uncertainty from QC, competence, trends
(5b); risks, internal audits, readiness, management review, the §7.11 pack (5c). Part 3 (parked):
warnings on the review screen before release. A library: the host (slim-lims-oss) adapts slim-data's QC
history to `QcSource` and composes the vertical audit from each module's read API.

## Rules for the build

- `domain/` and `monitors/` import only stdlib + pydantic (`tests/test_architecture.py`).
- Values are `Dec`; tools are `name@version`, never edited once released (a change is a new version).
- Thresholds, windows and which tools run are profile **data** (`profiles/starter.json` until one is
  approved); never `if demo:`. Demo numbers are invented.
- Append-only tables; repositories flush, the host commits. Portable schema (SQLite + Postgres).
- Every push: ruff, full tests, 95 % branch coverage on domain + monitors.

## File map

```
slim_quality/
├── domain/      # pure: base (Frozen, Dec, NotAllowed), enums, series, qc (to_series), limits, profile
│                #   (tools, measures, equipment and PT settings), flags, changes, records, audits,
│                #   nonconformity; equipment (register, state, check_use), materials (ReferenceLot),
│                #   pt (rounds, z, classes, z_series), complaints (states, the §7.9.6 refusal)
├── monitors/    # pure: westgard, ewma, cusum, exceedance, projection, shared; runner (evaluate)
├── persistence/ # oss_guard, db, migrate, migrations/ (0001…0003), tables, append_only, store
├── service/     # quality (charts: QC + PT z, change control, records, audits, NCs), register (equipment,
│                #   lots), pt, complaints, svg, verify
├── profiles/    # starter.json
└── demo/        # history.py: the invented QC history
```
