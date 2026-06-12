"""Core logic untuk analisis work log."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from activity_reference import (
    activity_category,
    activity_category_label,
    activity_description,
    canonical_activity_kind,
    lookup_activity_info,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DEMO_CSV = DATA_DIR / "templates" / "worklog-export.example.csv"
DEFAULT_HTML_REPORT = PROJECT_ROOT / "laporan.html"
WORK_SUMMARY_VISIBLE = 6
HOLIDAY_COUNTRY = "ID"

ISSUE_KEY_RE = re.compile(r"/browse/([A-Z]+-\d+)")
ACTIVITY_CLEAN_RE = re.compile(r"[\[\]*]")
ACTIVITY_KIND_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)", re.DOTALL)
WORKLOG_TICKET_RE = re.compile(r"^worklog\b", re.I)
WORK_SUMMARY_HOURS_SUFFIX_RE = re.compile(r"\s+\([\d.]+j\)$")


@dataclass(frozen=True)
class WorkLogEntry:
    name: str
    issue_url: str
    issue_key: str
    ticket: str
    time_spent: float
    grouping_activity: str
    activity: str
    activity_normalized: str
    date: datetime
    story_points: float

    @property
    def day(self) -> str:
        return self.date.strftime("%Y-%m-%d")

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m")

    @property
    def iso_week(self) -> str:
        y, w, _ = self.date.isocalendar()
        return f"{y}-W{w:02d}"

    @property
    def layer(self) -> str:
        return detect_ticket_layer(self.ticket)


@dataclass(frozen=True)
class TicketWorkSummary:
    issue_key: str
    total_hours: float
    lines: list[str]
    lines_with_hours: list[str]


@dataclass
class DaySummary:
    day: str
    total_hours: float
    entry_count: int
    by_grouping: dict[str, float]
    by_layer: dict[str, float]
    done_count: int
    target_hours: float
    work_summary_tickets: list[TicketWorkSummary]
    work_summary_lines: list[str]
    work_summary_plain_visible: list[str]
    work_summary_plain_hidden: list[str]

    @property
    def target_met(self) -> bool:
        return self.total_hours >= self.target_hours

    @property
    def gap_hours(self) -> float:
        return self.total_hours - self.target_hours


@dataclass
class TicketSummary:
    issue_key: str
    ticket: str
    layer: str
    story_points: float
    total_hours: float
    lines: list[WorkLogEntry]
    is_done: bool


@dataclass
class SpTicketMetric:
    issue_key: str
    story_points: float
    hours: float

    @property
    def hours_per_sp(self) -> float | None:
        if self.story_points <= 0:
            return None
        return self.hours / self.story_points


@dataclass
class WeeklySpVelocity:
    week: str
    sp_done: float
    hours: float
    done_count: int


@dataclass
class SpLayerMetric:
    layer: str
    hours: float
    story_points: float

    @property
    def hours_per_sp(self) -> float | None:
        if self.story_points <= 0:
            return None
        return self.hours / self.story_points


@dataclass
class PeriodComparison:
    current_label: str
    previous_label: str
    current_hours: float
    previous_hours: float
    current_days: int
    previous_days: int
    current_sp_done: float
    previous_sp_done: float

    @property
    def hours_delta(self) -> float:
        return self.current_hours - self.previous_hours

    @property
    def hours_pct(self) -> float | None:
        if self.previous_hours <= 0:
            return None
        return (self.hours_delta / self.previous_hours) * 100


@dataclass
class PersonSummary:
    name: str
    total_hours: float
    entry_count: int
    days_active: int
    done_count: int


@dataclass
class SpMetrics:
    total_sp: float
    hours_on_sp_tickets: float
    hours_per_sp: float | None
    per_ticket: list[SpTicketMetric]
    weekly_velocity: list[WeeklySpVelocity]
    by_layer: list[SpLayerMetric]


@dataclass
class ReportData:
    period_label: str
    target_hours: float
    workdays_only: bool
    entries: list[WorkLogEntry]
    daily_summaries: list[DaySummary]
    ticket_summaries: list[TicketSummary]
    entries_by_day: dict[str, list[WorkLogEntry]]
    by_grouping: dict[str, float]
    by_activity: dict[str, float]
    by_activity_category: dict[str, float]
    by_layer: dict[str, float]
    by_issue: dict[str, float]
    issue_meta: dict[str, WorkLogEntry]
    done_keys: set[str]
    sp_touched: dict[str, float]
    sp_metrics: SpMetrics
    zero_time_entries: list[WorkLogEntry]
    warnings: list[str] = field(default_factory=list)
    period_comparison: PeriodComparison | None = None
    person_summaries: list[PersonSummary] = field(default_factory=list)

    @property
    def total_hours(self) -> float:
        return sum(e.time_spent for e in self.entries)

    @property
    def names(self) -> list[str]:
        return sorted({e.name for e in self.entries})

    @property
    def days(self) -> list[str]:
        return sorted({e.day for e in self.entries})

    @property
    def target_daily_summaries(self) -> list[DaySummary]:
        if not self.workdays_only:
            return self.daily_summaries
        holidays = load_holidays(*years_from_days([d.day for d in self.daily_summaries]))
        return [d for d in self.daily_summaries if is_workday(d.day, holidays)]

    @property
    def avg_hours_per_day(self) -> float:
        summaries = self.target_daily_summaries
        if not summaries:
            return 0.0
        total = sum(d.total_hours for d in summaries)
        return total / len(summaries)

    @property
    def days_target_met(self) -> int:
        return sum(1 for d in self.target_daily_summaries if d.target_met)

    @property
    def target_day_count(self) -> int:
        return len(self.target_daily_summaries)


def normalize_activity(raw: str) -> str:
    return ACTIVITY_CLEAN_RE.sub("", raw).strip().upper()


def parse_float(value: str, default: float = 0.0) -> float:
    value = value.strip()
    if not value:
        return default
    return float(value)


def parse_date(value: str) -> datetime:
    # Format umum: 2026-06-11T14:18:52.142+0700 atau ...+07:00 atau ...Z
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    else:
        m = re.match(r"^(.+)([+-])(\d{2})(\d{2})$", value)
        if m:
            base, sign, hh, mm = m.groups()
            value = f"{base}{sign}{hh}:{mm}"
    return datetime.fromisoformat(value)


def extract_issue_key(issue_url: str) -> str:
    match = ISSUE_KEY_RE.search(issue_url)
    return match.group(1) if match else issue_url


def detect_ticket_layer(ticket: str) -> str:
    ticket_upper = ticket.upper()
    if ticket_upper.startswith("[FE]"):
        return "FE"
    if ticket_upper.startswith("[BE]"):
        return "BE"
    if "[GENESIS]" in ticket_upper or ticket_upper.startswith("[GENESIS]"):
        return "Genesis"
    return "Lainnya"


def load_entries(path: Path) -> list[WorkLogEntry]:
    from validation import validate_csv_header

    entries: list[WorkLogEntry] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        validate_csv_header(reader.fieldnames)
        for row in reader:
            entries.append(
                WorkLogEntry(
                    name=row["Name"].strip(),
                    issue_url=row["Issue"].strip(),
                    issue_key=extract_issue_key(row["Issue"]),
                    ticket=row["Ticket"].strip(),
                    time_spent=parse_float(row["Time Spent"]),
                    grouping_activity=row["Grouping Activity"].strip(),
                    activity=row["Activity"].strip(),
                    activity_normalized=normalize_activity(row["Activity"]),
                    date=parse_date(row["Date"].strip()),
                    story_points=parse_float(row["SP"]),
                )
            )
    return entries


def entry_dedup_key(entry: WorkLogEntry) -> tuple:
    return (
        entry.name,
        entry.issue_key,
        entry.date.isoformat(),
        entry.time_spent,
        entry.activity_normalized,
        entry.grouping_activity,
    )


@lru_cache(maxsize=4)
def _id_holiday_calendar(min_year: int, max_year: int):
    import holidays

    years = range(min_year, max_year + 1)
    return holidays.country_holidays(HOLIDAY_COUNTRY, years=years)


def years_from_days(days: list[str]) -> tuple[int, int]:
    if not days:
        y = date.today().year
        return y - 1, y + 1
    years = [int(d[:4]) for d in days]
    return min(years), max(years)


def years_from_entries(entries: list[WorkLogEntry]) -> tuple[int, int]:
    return years_from_days([e.day for e in entries])


def load_holidays(min_year: int | None = None, max_year: int | None = None) -> set[str]:
    """Libur nasional Indonesia via library `holidays` (gratis, offline)."""
    if min_year is None or max_year is None:
        y = date.today().year
        min_year, max_year = y - 1, y + 1
    cal = _id_holiday_calendar(min_year, max_year)
    return {d.isoformat() for d in cal}


def is_public_holiday(day: str, holiday_dates: set[str] | None = None) -> bool:
    if holiday_dates is not None:
        return day in holiday_dates
    d = datetime.strptime(day, "%Y-%m-%d").date()
    y = d.year
    cal = _id_holiday_calendar(y, y)
    return d in cal


def is_workday(day: str, holidays: set[str] | None = None) -> bool:
    if datetime.strptime(day, "%Y-%m-%d").weekday() >= 5:
        return False
    return not is_public_holiday(day, holidays)


def today_str() -> str:
    return date.today().isoformat()


def yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def name_matches(entry_name: str, filter_name: str) -> bool:
    return filter_name.strip().lower() in entry_name.strip().lower()


def load_all_csv(paths: list[Path]) -> tuple[list[WorkLogEntry], int]:
    entries: list[WorkLogEntry] = []
    seen_paths: set[Path] = set()
    seen_entries: set[tuple] = set()
    skipped = 0
    for path in paths:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        for entry in load_entries(path):
            key = entry_dedup_key(entry)
            if key in seen_entries:
                skipped += 1
                continue
            seen_entries.add(key)
            entries.append(entry)
    entries.sort(key=lambda e: e.date)
    return entries, skipped


def sum_hours(entries: list[WorkLogEntry]) -> float:
    return sum(e.time_spent for e in entries)


def group_sum(entries: list[WorkLogEntry], key_fn) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for e in entries:
        totals[key_fn(e)] += e.time_spent
    return dict(sorted(totals.items(), key=lambda x: (-x[1], x[0])))


def group_sum_activity_category(entries: list[WorkLogEntry]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for entry in entries:
        label = activity_category_label(activity_kind_from_entry(entry))
        totals[label] += entry.time_spent
    return dict(sorted(totals.items(), key=lambda x: (-x[1], x[0])))


def unique_issues(entries: list[WorkLogEntry]) -> dict[str, WorkLogEntry]:
    issues: dict[str, WorkLogEntry] = {}
    for e in entries:
        issues[e.issue_key] = e
    return issues


def filter_entries(
    entries: list[WorkLogEntry],
    *,
    day: str | None = None,
    week: str | None = None,
    month: str | None = None,
    name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[WorkLogEntry]:
    result = entries
    if day:
        result = [e for e in result if e.day == day]
    if week:
        result = [e for e in result if e.iso_week == week]
    if month:
        result = [e for e in result if e.month == month]
    if name:
        result = [e for e in result if name_matches(e.name, name)]
    if date_from:
        result = [e for e in result if e.day >= date_from]
    if date_to:
        result = [e for e in result if e.day <= date_to]
    return result


def list_available_names(entries: list[WorkLogEntry]) -> list[str]:
    return sorted({e.name for e in entries})


def list_available_dates(entries: list[WorkLogEntry]) -> list[str]:
    return sorted({e.day for e in entries})


def list_available_weeks(entries: list[WorkLogEntry]) -> list[str]:
    return sorted({e.iso_week for e in entries})


def list_available_months(entries: list[WorkLogEntry]) -> list[str]:
    return sorted({e.month for e in entries})


def group_entries_by_day(entries: list[WorkLogEntry]) -> dict[str, list[WorkLogEntry]]:
    grouped: dict[str, list[WorkLogEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.day].append(entry)
    return {day: sorted(rows, key=lambda e: e.date) for day, rows in sorted(grouped.items())}


def build_ticket_summaries(
    entries: list[WorkLogEntry], done_keys: set[str]
) -> list[TicketSummary]:
    by_issue: dict[str, list[WorkLogEntry]] = defaultdict(list)
    for entry in entries:
        by_issue[entry.issue_key].append(entry)

    summaries: list[TicketSummary] = []
    for key, lines in by_issue.items():
        lines.sort(key=lambda e: e.date)
        meta = lines[-1]
        summaries.append(
            TicketSummary(
                issue_key=key,
                ticket=meta.ticket,
                layer=meta.layer,
                story_points=meta.story_points,
                total_hours=sum(e.time_spent for e in lines),
                lines=lines,
                is_done=key in done_keys,
            )
        )
    summaries.sort(key=lambda s: (-s.total_hours, s.issue_key))
    return summaries


def shorten_label(label: str, max_len: int = 18) -> str:
    if len(label) <= max_len:
        return label
    return label[: max_len - 1] + "…"


def is_worklog_ticket(ticket: str) -> bool:
    return bool(WORKLOG_TICKET_RE.match(ticket.strip()))


def activity_kind_from_raw(activity: str) -> str:
    match = ACTIVITY_KIND_RE.match(activity.strip())
    if match:
        return canonical_activity_kind(match.group(1))
    return canonical_activity_kind(normalize_activity(activity))


def activity_category_from_entry(entry: WorkLogEntry) -> str:
    return activity_category(activity_kind_from_entry(entry))


def format_grouping_title_case(label: str) -> str:
    return " & ".join(part.strip().title() for part in label.split(" & "))


def activity_kind_from_entry(entry: WorkLogEntry) -> str:
    return activity_kind_from_raw(entry.activity)


format_activity_kind_title_case = format_grouping_title_case


def format_activity_display(kind: str) -> str:
    """Label activity untuk tampilan (title case + tooltip-ready)."""
    return format_activity_kind_title_case(canonical_activity_kind(kind))


def format_worklog_summary_line(
    entry: WorkLogEntry,
    *,
    include_hours: bool = False,
    max_detail_len: int = 120,
) -> str:
    """Ringkas entri work log: `[jenis aktivitas] detail` dari field Activity."""
    activity = entry.activity.strip()
    ticket = entry.ticket.strip()
    match = ACTIVITY_KIND_RE.match(activity)
    if match:
        kind = format_activity_display(match.group(1))
        detail = match.group(2).strip()
        if not detail and not is_worklog_ticket(ticket):
            detail = ticket
        if detail and len(detail) > max_detail_len:
            detail = detail[: max_detail_len - 3] + "..."
        line = f"[{kind}] {detail}" if detail else f"[{kind}]"
    elif is_worklog_ticket(ticket):
        line = activity or "-"
    elif activity:
        short_ticket = ticket if len(ticket) <= max_detail_len else ticket[: max_detail_len - 3] + "..."
        line = f"{short_ticket} — {activity}"
    else:
        short_ticket = ticket if len(ticket) <= max_detail_len else ticket[: max_detail_len - 3] + "..."
        line = short_ticket or "-"

    if include_hours:
        return f"{line} ({entry.time_spent:.2f}j)"
    return line


def build_day_work_tickets(day_entries: list[WorkLogEntry]) -> list[TicketWorkSummary]:
    by_issue: dict[str, list[WorkLogEntry]] = defaultdict(list)
    for entry in sorted(day_entries, key=lambda e: e.date):
        by_issue[entry.issue_key].append(entry)

    tickets: list[TicketWorkSummary] = []
    for key in sorted(
        by_issue.keys(),
        key=lambda issue_key: (-sum(e.time_spent for e in by_issue[issue_key]), issue_key),
    ):
        rows = by_issue[key]
        lines = [format_worklog_summary_line(entry, include_hours=False) for entry in rows]
        lines_with_hours = [
            format_worklog_summary_line(entry, include_hours=True) for entry in rows
        ]
        tickets.append(
            TicketWorkSummary(
                issue_key=key,
                total_hours=sum(entry.time_spent for entry in rows),
                lines=lines,
                lines_with_hours=lines_with_hours,
            )
        )
    return tickets


def format_ticket_block_plain(ticket: TicketWorkSummary, *, include_hours: bool = False) -> str:
    inner = "; ".join(ticket.lines_with_hours if include_hours else ticket.lines)
    if include_hours:
        return f"{ticket.issue_key} ({ticket.total_hours:.2f}j): {inner}"
    return f"{ticket.issue_key}: {inner}" if inner else ticket.issue_key


def build_day_work_summary(
    day_entries: list[WorkLogEntry],
    max_items: int = WORK_SUMMARY_VISIBLE,
    *,
    include_hours: bool = True,
) -> tuple[list[str], list[str]]:
    tickets = build_day_work_tickets(day_entries)
    all_lines = [format_ticket_block_plain(t, include_hours=include_hours) for t in tickets]
    return all_lines[:max_items], all_lines[max_items:]


def build_day_summary(
    entries: list[WorkLogEntry], day: str, target_hours: float
) -> DaySummary:
    day_entries = [e for e in entries if e.day == day]
    issue_meta = unique_issues(day_entries)
    by_layer: dict[str, float] = defaultdict(float)
    for key, meta in issue_meta.items():
        by_layer[meta.layer] += sum(
            e.time_spent for e in day_entries if e.issue_key == key
        )
    done_keys = {
        e.issue_key for e in day_entries if e.activity_normalized == "DONE"
    }
    visible_h, hidden_h = build_day_work_summary(day_entries, include_hours=True)
    visible_p, hidden_p = build_day_work_summary(day_entries, include_hours=False)
    work_tickets = build_day_work_tickets(day_entries)
    return DaySummary(
        day=day,
        total_hours=sum_hours(day_entries),
        entry_count=len(day_entries),
        by_grouping=group_sum(day_entries, lambda e: e.grouping_activity),
        by_layer=dict(sorted(by_layer.items(), key=lambda x: -x[1])),
        done_count=len(done_keys),
        target_hours=target_hours,
        work_summary_tickets=work_tickets,
        work_summary_lines=visible_h,
        work_summary_plain_visible=visible_p,
        work_summary_plain_hidden=hidden_p,
    )


def build_sp_metrics(
    entries: list[WorkLogEntry], done_keys: set[str]
) -> SpMetrics:
    per_ticket: list[SpTicketMetric] = []
    for key, sp in sorted(
        {
            e.issue_key: e.story_points
            for e in entries
            if e.story_points > 0
        }.items()
    ):
        hours = sum(e.time_spent for e in entries if e.issue_key == key)
        per_ticket.append(SpTicketMetric(key, sp, hours))
    per_ticket.sort(key=lambda m: (-m.story_points, m.issue_key))

    total_sp = sum(m.story_points for m in per_ticket)
    hours_on_sp = sum(m.hours for m in per_ticket)
    hours_per_sp = hours_on_sp / total_sp if total_sp > 0 else None

    week_data: dict[str, dict[str, float | int | set[str]]] = {}
    for entry in entries:
        if entry.issue_key not in done_keys:
            continue
        sp = entry.story_points
        if sp <= 0:
            continue
        bucket = week_data.setdefault(
            entry.iso_week,
            {"sp": 0.0, "hours": 0.0, "keys": set()},
        )
        bucket["hours"] = float(bucket["hours"]) + entry.time_spent
        keys = bucket["keys"]
        assert isinstance(keys, set)
        if entry.issue_key not in keys:
            keys.add(entry.issue_key)
            bucket["sp"] = float(bucket["sp"]) + sp

    weekly_velocity = [
        WeeklySpVelocity(
            week=week,
            sp_done=float(data["sp"]),
            hours=float(data["hours"]),
            done_count=len(data["keys"]),
        )
        for week, data in sorted(week_data.items())
    ]

    layer_sp: dict[str, float] = defaultdict(float)
    layer_hours: dict[str, float] = defaultdict(float)
    seen_layer_issue: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        layer_hours[entry.layer] += entry.time_spent
        if entry.story_points > 0 and entry.issue_key not in seen_layer_issue[entry.layer]:
            seen_layer_issue[entry.layer].add(entry.issue_key)
            layer_sp[entry.layer] += entry.story_points
    by_layer = [
        SpLayerMetric(layer=layer, hours=layer_hours[layer], story_points=layer_sp[layer])
        for layer in sorted(layer_hours.keys(), key=lambda k: -layer_hours[k])
    ]

    return SpMetrics(
        total_sp=total_sp,
        hours_on_sp_tickets=hours_on_sp,
        hours_per_sp=hours_per_sp,
        per_ticket=per_ticket,
        weekly_velocity=weekly_velocity,
        by_layer=by_layer,
    )


def build_person_summaries(entries: list[WorkLogEntry]) -> list[PersonSummary]:
    by_name: dict[str, list[WorkLogEntry]] = defaultdict(list)
    for entry in entries:
        by_name[entry.name].append(entry)
    summaries: list[PersonSummary] = []
    for name, rows in by_name.items():
        done_keys = {
            e.issue_key for e in rows if e.activity_normalized == "DONE"
        }
        summaries.append(
            PersonSummary(
                name=name,
                total_hours=sum_hours(rows),
                entry_count=len(rows),
                days_active=len({e.day for e in rows}),
                done_count=len(done_keys),
            )
        )
    summaries.sort(key=lambda s: (-s.total_hours, s.name))
    return summaries


def _previous_iso_week(week: str) -> str | None:
    y, w = int(week[:4]), int(week.split("-W")[1])
    d = date.fromisocalendar(y, w, 1) - timedelta(days=7)
    py, pw, _ = d.isocalendar()
    return f"{py}-W{pw:02d}"


def _previous_month(month: str) -> str | None:
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def build_period_comparison(
    all_entries: list[WorkLogEntry],
    filtered: list[WorkLogEntry],
    *,
    week: str | None = None,
    month: str | None = None,
) -> PeriodComparison | None:
    if week:
        prev = _previous_iso_week(week)
        if not prev:
            return None
        current = filter_entries(all_entries, week=week)
        previous = filter_entries(all_entries, week=prev)
        current_label = f"Minggu {week}"
        previous_label = f"Minggu {prev}"
    elif month:
        prev = _previous_month(month)
        if not prev:
            return None
        current = filtered
        previous = filter_entries(all_entries, month=prev)
        current_label = f"Bulan {month}"
        previous_label = f"Bulan {prev}"
    else:
        return None

    if not current and not previous:
        return None

    done_current = {
        e.issue_key for e in current if e.activity_normalized == "DONE"
    }
    done_previous = {
        e.issue_key for e in previous if e.activity_normalized == "DONE"
    }
    sp_current = sum(
        e.story_points
        for e in unique_issues(current).values()
        if e.issue_key in done_current and e.story_points > 0
    )
    sp_previous = sum(
        e.story_points
        for e in unique_issues(previous).values()
        if e.issue_key in done_previous and e.story_points > 0
    )

    return PeriodComparison(
        current_label=current_label,
        previous_label=previous_label,
        current_hours=sum_hours(current),
        previous_hours=sum_hours(previous),
        current_days=len({e.day for e in current}),
        previous_days=len({e.day for e in previous}),
        current_sp_done=sp_current,
        previous_sp_done=sp_previous,
    )


def build_report(
    entries: list[WorkLogEntry],
    period_label: str,
    target_hours: float = 8.0,
    *,
    workdays_only: bool = False,
    warnings: list[str] | None = None,
    period_comparison: PeriodComparison | None = None,
) -> ReportData:
    issue_meta = unique_issues(entries)
    by_layer: dict[str, float] = defaultdict(float)
    for key, meta in issue_meta.items():
        by_layer[meta.layer] += sum(
            e.time_spent for e in entries if e.issue_key == key
        )

    sp_touched = {
        key: meta.story_points
        for key, meta in issue_meta.items()
        if meta.story_points > 0
    }

    days = list_available_dates(entries)
    daily_summaries = [
        build_day_summary(entries, d, target_hours) for d in days
    ]
    done_keys = {
        e.issue_key for e in entries if e.activity_normalized == "DONE"
    }

    return ReportData(
        period_label=period_label,
        target_hours=target_hours,
        workdays_only=workdays_only,
        entries=entries,
        daily_summaries=daily_summaries,
        ticket_summaries=build_ticket_summaries(entries, done_keys),
        entries_by_day=group_entries_by_day(entries),
        by_grouping=group_sum(entries, lambda e: e.grouping_activity),
        by_activity=group_sum(entries, activity_kind_from_entry),
        by_activity_category=group_sum_activity_category(entries),
        by_layer=dict(sorted(by_layer.items(), key=lambda x: -x[1])),
        by_issue=group_sum(entries, lambda e: e.issue_key),
        issue_meta=issue_meta,
        done_keys=done_keys,
        sp_touched=sp_touched,
        sp_metrics=build_sp_metrics(entries, done_keys),
        zero_time_entries=[e for e in entries if e.time_spent == 0],
        warnings=warnings or [],
        period_comparison=period_comparison,
        person_summaries=build_person_summaries(entries),
    )


def format_hours_per_sp(hours_per_sp: float | None) -> str:
    if hours_per_sp is None:
        return "-"
    return f"{hours_per_sp:.2f} jam/SP"


def target_summary_label(report: ReportData) -> str:
    if not report.target_daily_summaries:
        return "tidak ada data harian"
    suffix = " (hari kerja)" if report.workdays_only else ""
    return (
        f"{report.days_target_met}/{report.target_day_count} hari tercapai{suffix}"
    )


def format_hours(hours: float) -> str:
    whole = int(hours)
    minutes = round((hours - whole) * 60)
    if minutes == 60:
        whole += 1
        minutes = 0
    if whole and minutes:
        return f"{hours:.2f} jam ({whole}j {minutes}m)"
    if whole:
        return f"{hours:.2f} jam ({whole}j)"
    return f"{hours:.2f} jam ({minutes}m)"


def format_gap(hours: float) -> str:
    sign = "+" if hours >= 0 else ""
    return f"{sign}{hours:.2f} jam"


def format_time_spent(hours: float) -> str:
    if hours == 0:
        return "0"
    return f"{hours:.2f}"


def format_grouping_breakdown(
    by_grouping: dict[str, float], *, include_hours: bool = True
) -> str:
    if not by_grouping:
        return "-"
    if include_hours:
        return " | ".join(
            f"{shorten_label(k)} {v:.2f}j" for k, v in by_grouping.items()
        )
    return " | ".join(shorten_label(k) for k in by_grouping)


EXCEL_FIELDS = ("summary", "hours", "full")


def format_grouping_title_case(label: str) -> str:
    return " & ".join(part.strip().title() for part in label.split(" & "))


def format_day_grouping_field(day: DaySummary) -> str:
    if not day.by_grouping:
        return "-"
    labels = sorted(format_grouping_title_case(label) for label in day.by_grouping.keys())
    return "; ".join(labels)


def format_day_excel_field(day: DaySummary, field: str = "summary") -> str:
    all_plain = day.work_summary_plain_visible + day.work_summary_plain_hidden
    if field == "hours":
        return format_hours(day.total_hours)
    if field == "full":
        lines = [
            f"Grouping: {format_grouping_breakdown(day.by_grouping, include_hours=False)}",
            f"Layer: {format_grouping_breakdown(day.by_layer, include_hours=False)}",
        ]
        if all_plain:
            lines.append("Kerja:")
            lines.extend(f"• {line}" for line in all_plain)
        return "\n".join(lines)
    if all_plain:
        return "; ".join(all_plain)
    return "-"


def excel_escape_cell(value: str) -> str:
    if any(ch in value for ch in ("\t", "\n", '"')):
        return '"' + value.replace('"', '""') + '"'
    return value


def render_excel_tsv(
    report: ReportData, field: str = "summary", *, with_date: bool = True
) -> str:
    rows: list[str] = []
    for day in report.daily_summaries:
        cell = format_day_excel_field(day, field)
        if with_date:
            grouping = format_day_grouping_field(day)
            rows.append(
                f"{day.day}\t{excel_escape_cell(grouping)}\t{excel_escape_cell(cell)}"
            )
        else:
            rows.append(excel_escape_cell(cell))
    return "\n".join(rows) + ("\n" if rows else "")


def period_label_for(
    day: str | None,
    week: str | None,
    month: str | None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    if day:
        return day
    if week:
        return f"Minggu {week}"
    if month:
        return f"Bulan {month}"
    if date_from and date_to:
        return f"{date_from} s/d {date_to}"
    if date_from:
        return f"dari {date_from}"
    if date_to:
        return f"sampai {date_to}"
    return "semua tanggal"
