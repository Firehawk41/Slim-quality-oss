# slim-quality

The **quality** module of SLIM, a LIMS for an ISO 17025 analytical chemistry lab: QC control charts,
trend and forecast tools, and change control over the rules, for a quality manager. A library: the
SLIM host app ([slim-lims-oss](https://github.com/Firehawk41/slim-lims-oss)) feeds it QC data and draws
the screens. **OSS sandbox only:** invented demo data, never a real lab's (`persistence/oss_guard.py`).

## What it does

- **Series, generically keyed:** instrument × analyte × conditions (e.g. plasma mode) × measure × QC
  material × level. Measures: check standard and CRM recovery, method blank, internal standard
  recovery, check RSD, calibration LOD / BEC / r.
- **A toolbox, switched on and tuned in a profile** (policy data, versioned): Westgard rules (each one
  toggleable), EWMA, CUSUM, a one-sided limit with an exceedance rate (for skewed blanks), a forecast
  of when a series will leave its acceptance window, and a shared-shift check across instruments.
  Tools are `name@version`, never edited once released.
- **Limits** from a baseline (mean ± SD, or median ± 1.4826 × MAD), or entered (a certificate); set
  once and approved, never recomputed as data arrives. Until approved, provisional limits from the
  first points, drawn dotted.
- **Change control:** a quality user or manager proposes a profile or limits; a manager decides
  (a self-approval is allowed and shown). **Backtest:** what a candidate profile would have flagged.
- **Records:** events (maintenance, new lot), notes on points, the signed periodic QC review.
- **Audits and nonconformities:** vertical audits recorded (conforming or findings); nonconformities
  raised from a flag, event or audit, with an impact assessment per released result, actions, an
  effectiveness check and closure, the state following the entries (D-113, D-114).
- **Append-only** tables (database triggers) and `as_of` reads: the rules as they stood on any date.

## Layout

```
slim_quality/
├── domain/      # pure (stdlib + pydantic): series, limits, profile, changes, records, qc (records → series),
│                #   audits, nonconformity, ports
├── monitors/    # pure: base, westgard, ewma, cusum, exceedance, projection, shared, registry, runner
├── profiles/    # starter.json: the starter profile
├── persistence/ # oss_guard, db, migrate, migrations/, tables, append_only, store (repositories)
├── service/     # quality (charts, overview, backtest, change control, records, audits, NCs), svg
└── demo/        # history.py: seeded, invented QC history with a planted drift
```

Run: `pip install -e ".[dev]" && pytest` (Postgres: `TEST_DATABASE_URL=postgresql+psycopg2://...`);
`ruff check . && ruff format --check .`. Decisions: slim-data-oss `docs/DECISIONS.md` (D-109, D-110).
