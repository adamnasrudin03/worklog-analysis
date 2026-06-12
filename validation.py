"""Validasi CSV dan peringatan data work log."""

from __future__ import annotations

from collections import defaultdict

from worklog_lib import WorkLogEntry, sum_hours

REQUIRED_CSV_COLUMNS = (
    "Name",
    "Issue",
    "Ticket",
    "Time Spent",
    "Grouping Activity",
    "Activity",
    "Date",
    "SP",
)

MAX_HOURS_PER_DAY_WARN = 12.0


def validate_csv_header(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("File CSV kosong atau tidak punya header.")
    missing = [col for col in REQUIRED_CSV_COLUMNS if col not in fieldnames]
    if missing:
        raise ValueError(
            f"Kolom CSV tidak lengkap. Kurang: {', '.join(missing)}. "
            f"Dibutuhkan: {', '.join(REQUIRED_CSV_COLUMNS)}"
        )


def collect_data_warnings(entries: list[WorkLogEntry]) -> list[str]:
    warnings: list[str] = []

    by_day: dict[str, list[WorkLogEntry]] = defaultdict(list)
    for entry in entries:
        by_day[entry.day].append(entry)

    for day, day_entries in sorted(by_day.items()):
        total = sum_hours(day_entries)
        if total > MAX_HOURS_PER_DAY_WARN:
            warnings.append(
                f"{day}: total {total:.2f} jam (> {MAX_HOURS_PER_DAY_WARN:g}j) — "
                "cek double log"
            )

    sp_by_issue: dict[str, set[float]] = defaultdict(set)
    for entry in entries:
        if entry.story_points > 0:
            sp_by_issue[entry.issue_key].add(entry.story_points)
    for key, values in sorted(sp_by_issue.items()):
        if len(values) > 1:
            sp_list = ", ".join(f"{v:g}" for v in sorted(values))
            warnings.append(f"{key}: SP tidak konsisten ({sp_list})")

    return warnings
