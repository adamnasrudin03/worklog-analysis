"""Referensi jenis activity work log (dari standar tim)."""

from __future__ import annotations

import re
from dataclasses import dataclass

GROOMING_N_RE = re.compile(r"^GROOMING-\d+$", re.I)

# Alias typo / variasi penulisan → kunci kanonik
ACTIVITY_ALIASES: dict[str, str] = {
    "REVISI": "REVISIT",
    "MEETING": "MEETING",
    "ON LEAVE": "ON LEAVE",
}


@dataclass(frozen=True)
class ActivityInfo:
    key: str
    description: str
    category: str
    category_label: str
    productive: bool | None = None  # None = netral


def _info(
    key: str,
    description: str,
    category: str,
    category_label: str,
    *,
    productive: bool | None = None,
) -> ActivityInfo:
    return ActivityInfo(key, description, category, category_label, productive)


# Katalog activity — gabungan standar SA/FE/BE + umum di worklog
ACTIVITY_CATALOG: dict[str, ActivityInfo] = {
    "GROOMING": _info(
        "GROOMING",
        "Grooming session (iterasi grooming)",
        "collaboration",
        "Kolaborasi",
        productive=True,
    ),
    "READ PRD": _info(
        "READ PRD",
        "Reading Product Requirement Document",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "CHECK EXISTING FEATURE": _info(
        "CHECK EXISTING FEATURE",
        "Reviewing existing features",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "CHECK EXISTING CODE": _info(
        "CHECK EXISTING CODE",
        "Reviewing existing codebase",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "ANALYSIS DB": _info(
        "ANALYSIS DB",
        "Database analysis",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "ANALYSIS API": _info(
        "ANALYSIS API",
        "API analysis",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "ANALYSIS UI & UX": _info(
        "ANALYSIS UI & UX",
        "UI/UX analysis",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "ANALYSIS TECHNICAL": _info(
        "ANALYSIS TECHNICAL",
        "Technical analysis and investigation",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "MATRIX": _info(
        "MATRIX",
        "Create matrix for epic",
        "analysis",
        "Analisis",
        productive=True,
    ),
    "DETAILING": _info(
        "DETAILING",
        "Detailing requirements or tasks (BE/FE)",
        "detailing",
        "Detailing",
        productive=True,
    ),
    "CODING": _info(
        "CODING",
        "Implementasi / coding task",
        "development",
        "Development",
        productive=True,
    ),
    "PAIRING": _info(
        "PAIRING",
        "Collaborative programming sessions",
        "development",
        "Development",
        productive=True,
    ),
    "DISCUSSION": _info(
        "DISCUSSION",
        "Team discussions",
        "collaboration",
        "Kolaborasi",
        productive=True,
    ),
    "MEETING": _info(
        "MEETING",
        "Meeting",
        "collaboration",
        "Kolaborasi",
        productive=True,
    ),
    "CODE REVIEW": _info(
        "CODE REVIEW",
        "Reviewing code changes",
        "review",
        "Review",
        productive=True,
    ),
    "REVIEW": _info(
        "REVIEW",
        "Review pekerjaan / perubahan",
        "review",
        "Review",
        productive=True,
    ),
    "REVISIT": _info(
        "REVISIT",
        "Revisit ticket",
        "review",
        "Review",
        productive=True,
    ),
    "BLOCKED": _info(
        "BLOCKED",
        "Blocked by an issue",
        "blocked",
        "Blocked",
        productive=False,
    ),
    "PROJECT SA": _info(
        "PROJECT SA",
        "SA project",
        "support",
        "Support",
        productive=True,
    ),
    "ISSUE": _info(
        "ISSUE",
        "Resolve issue",
        "support",
        "Support",
        productive=True,
    ),
    "DONE": _info(
        "DONE",
        "Task completed",
        "done",
        "Selesai",
        productive=None,
    ),
    "ON LEAVE": _info(
        "ON LEAVE",
        "On leave / sick leave / libur nasional",
        "admin",
        "Non-produktif",
        productive=False,
    ),
    "OTHERS": _info(
        "OTHERS",
        "Interview, workshop, LP ultah, farewell, dll. (non-produktif)",
        "admin",
        "Non-produktif",
        productive=False,
    ),
}

CATEGORY_ORDER = (
    "analysis",
    "detailing",
    "development",
    "collaboration",
    "review",
    "support",
    "blocked",
    "done",
    "admin",
    "other",
)


def canonical_activity_kind(raw_kind: str) -> str:
    """Normalisasi label activity ke kunci kanonik (uppercase)."""
    kind = raw_kind.strip().replace("*", "").upper()
    if GROOMING_N_RE.match(kind):
        return "GROOMING"
    return ACTIVITY_ALIASES.get(kind, kind)


def lookup_activity_info(kind: str) -> ActivityInfo | None:
    return ACTIVITY_CATALOG.get(canonical_activity_kind(kind))


def activity_description(kind: str) -> str | None:
    info = lookup_activity_info(kind)
    return info.description if info else None


def activity_category(kind: str) -> str:
    info = lookup_activity_info(kind)
    return info.category if info else "other"


def activity_category_label(kind: str) -> str:
    info = lookup_activity_info(kind)
    return info.category_label if info else "Lainnya"


def activity_catalog_for_export() -> dict[str, dict[str, str]]:
    """Metadata activity untuk HTML/JS (key = kanonik uppercase)."""
    out: dict[str, dict[str, str]] = {}
    for key, info in ACTIVITY_CATALOG.items():
        title = " & ".join(part.strip().title() for part in key.split(" & "))
        out[key] = {
            "title": title,
            "description": info.description,
            "category": info.category,
            "categoryLabel": info.category_label,
        }
    return out
