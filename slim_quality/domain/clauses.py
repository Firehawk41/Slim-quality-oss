"""ISO/IEC 17025:2017's clauses as the quality pages name them (D-135, D-136): what an internal audit covers
and what the readiness view has a row for. The standard's structure, not lab policy: a constant."""

CLAUSES: dict[str, str] = {
    "4.1": "Impartiality",
    "4.2": "Confidentiality",
    "5": "Structural requirements",
    "6.2": "Personnel",
    "6.3": "Facilities and environmental conditions",
    "6.4": "Equipment",
    "6.5": "Metrological traceability",
    "6.6": "Externally provided products and services",
    "7.1": "Review of requests, tenders and contracts",
    "7.2": "Selection, verification and validation of methods",
    "7.3": "Sampling",
    "7.4": "Handling of test items",
    "7.5": "Technical records",
    "7.6": "Evaluation of measurement uncertainty",
    "7.7": "Ensuring the validity of results",
    "7.8": "Reporting of results",
    "7.9": "Complaints",
    "7.10": "Nonconforming work",
    "7.11": "Control of data and information management",
    "8.2": "Management system documentation",
    "8.3": "Control of management system documents",
    "8.4": "Control of records",
    "8.5": "Actions to address risks and opportunities",
    "8.6": "Improvement",
    "8.7": "Corrective actions",
    "8.8": "Internal audits",
    "8.9": "Management reviews",
}

# A vertical audit (D-113) follows one report back through its request, its items, its records and QC to
# the report itself: it counts as an audit of these clauses.
VERTICAL_AUDIT_CLAUSES = ("7.1", "7.4", "7.5", "7.7", "7.8")


def check_clause(clause: str) -> str:
    if clause not in CLAUSES:
        raise ValueError(f"no clause {clause!r} in ISO/IEC 17025")
    return clause


def clause_label(clause: str) -> str:
    return f"{clause} {CLAUSES[clause]}"


def clause_order(clause: str) -> tuple[int, ...]:
    return tuple(int(part) for part in clause.split("."))
