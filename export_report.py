"""Export laporan work log ke HTML dan PDF."""

from __future__ import annotations

import base64
import html
import io
import json
import re
from pathlib import Path

from activity_reference import activity_catalog_for_export, activity_reference_sections
from worklog_lib import (
    ReportData,
    TicketWorkSummary,
    WORK_SUMMARY_HOURS_SUFFIX_RE,
    WORK_SUMMARY_VISIBLE,
    activity_description,
    format_activity_display,
    format_day_grouping_field,
    format_day_excel_field,
    format_gap,
    format_grouping_breakdown,
    format_grouping_title_case,
    format_hours,
    format_hours_per_sp,
    format_time_spent,
    is_workday,
    load_holidays,
    render_excel_tsv,
    shorten_label,
    target_summary_label,
    years_from_entries,
    years_from_days,
)

ISSUE_KEY_IN_SUMMARY_RE = re.compile(r"^([A-Z]+-\d+)")
ACTIVITY_KIND_HTML_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)", re.DOTALL)

CHART_COLORS = [
    "#4f46e5",
    "#06b6d4",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#ec4899",
    "#64748b",
]


def _chart_png_base64(labels: list[str], values: list[float], title: str) -> str | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    if not labels:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = CHART_COLORS[: len(labels)]
    bars = ax.bar(labels, values, color=colors)
    ax.axhline(y=0, color="#e2e8f0", linewidth=0.5)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel("Jam")
    ax.tick_params(axis="x", rotation=25, labelsize=8)
    plt.tight_layout()

    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{val:.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _daily_target_chart(report: ReportData) -> str | None:
    if len(report.daily_summaries) < 1:
        return None
    labels = [d.day for d in report.daily_summaries]
    values = [d.total_hours for d in report.daily_summaries]
    return _chart_png_base64(labels, values, f"Jam Harian vs Target ({report.target_hours}j)")


def _pie_chart(labels: list[str], values: list[float], title: str) -> str | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    if not labels or sum(values) <= 0:
        return None

    fig, ax = plt.subplots(figsize=(6, 4.5))
    colors = CHART_COLORS[: len(labels)]
    ax.pie(values, labels=labels, autopct="%1.1f%%", colors=colors, startangle=90)
    ax.set_title(title, fontsize=12, fontweight="bold")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _table_rows(items: list[tuple[str, ...]]) -> str:
    if not items:
        return "<tr><td colspan='2' class='muted'>(tidak ada data)</td></tr>"
    rows: list[str] = []
    for row in items:
        cells: list[str] = []
        for i, val in enumerate(row):
            cls = ' class="num"' if i > 0 else ""
            cells.append(f"<td{cls}>{html.escape(val)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return "".join(rows)


def _breakdown_list_html(breakdown: dict[str, float]) -> str:
    if not breakdown:
        return "<span class='muted'>-</span>"
    items = "".join(
        "<li>"
        f"<span>{html.escape(shorten_label(label))}</span>"
        f"<span class='num'>{value:.2f}j</span>"
        "</li>"
        for label, value in breakdown.items()
    )
    return f"<ul class='breakdown-list'>{items}</ul>"


def _activity_table_rows_html(by_activity: dict[str, float]) -> str:
    if not by_activity:
        return "<tr><td colspan='2' class='muted'>(tidak ada data)</td></tr>"
    rows: list[str] = []
    for kind, hours in by_activity.items():
        label = format_activity_display(kind)
        desc = activity_description(kind) or ""
        title = f' title="{html.escape(desc)}"' if desc else ""
        desc_html = (
            f'<div class="activity-desc">{html.escape(desc)}</div>' if desc else ""
        )
        rows.append(
            f"<tr><td{title}><div class='activity-label'>{html.escape(label)}</div>"
            f"{desc_html}</td><td class='num'>{html.escape(format_hours(hours))}</td></tr>"
        )
    return "".join(rows)


def _activity_info_btn_html() -> str:
    return (
        '<button type="button" class="info-btn no-print" data-activity-info-btn '
        'aria-label="Info jenis activity" aria-expanded="false" '
        'title="Referensi jenis activity">'
        '<span aria-hidden="true">ⓘ</span></button>'
    )


def _activity_info_popover_html() -> str:
    return (
        '<div id="activityInfoPopover" class="activity-info-popover no-print" '
        'role="dialog" aria-label="Referensi jenis activity" hidden>'
        f"{_activity_reference_html()}"
        "</div>"
    )


def _activity_reference_html() -> str:
    blocks: list[str] = []
    for section in activity_reference_sections():
        lis = "".join(
            "<li><strong>"
            f"{html.escape(item.label)}</strong>"
            f" — {html.escape(item.description)}</li>"
            for item in section.items
        )
        blocks.append(
            f"<div class='ref-block'><h3>{html.escape(section.title)}</h3><ul>{lis}</ul></div>"
        )
    return "".join(blocks)


def _grouping_tags_html(by_grouping: dict[str, float]) -> str:
    if not by_grouping:
        return "<span class='muted'>-</span>"
    tags = "".join(
        f"<span class='tag tag-grouping'>{html.escape(format_grouping_title_case(label))}</span>"
        for label in sorted(by_grouping.keys())
    )
    return f"<div class='tag-list'>{tags}</div>"


def _layer_chips_html(by_layer: dict[str, float]) -> str:
    if not by_layer:
        return "<span class='muted'>-</span>"
    tags = "".join(
        "<span class='tag tag-layer'>"
        f"{html.escape(shorten_label(label, 14))}"
        f"<span class='tag-hours'>{value:.2f}j</span>"
        "</span>"
        for label, value in by_layer.items()
    )
    return f"<div class='tag-list'>{tags}</div>"


def _work_summary_line_html(line: str) -> str:
    display = WORK_SUMMARY_HOURS_SUFFIX_RE.sub("", line)
    match = ACTIVITY_KIND_HTML_RE.match(display)
    if match:
        kind, detail = match.group(1), match.group(2).strip()
        desc = activity_description(kind) or ""
        kind_title = f' title="{html.escape(desc)}"' if desc else ""
        detail_html = ""
        if detail:
            detail_html = (
                f"<span class='work-title' title='{html.escape(detail)}'>"
                f"{html.escape(detail)}</span>"
            )
        return (
            "<li class='work-item'>"
            f"<span class='work-key'{kind_title}>[{html.escape(kind)}]</span>"
            f"{detail_html}"
            "</li>"
        )
    if " — " in display:
        key_part, title = display.split(" — ", 1)
        match_key = ISSUE_KEY_IN_SUMMARY_RE.search(key_part)
        key = match_key.group(1) if match_key else key_part.strip()
        return (
            "<li class='work-item'>"
            f"<span class='work-key'>{html.escape(key)}</span>"
            f"<span class='work-title' title='{html.escape(title)}'>"
            f"{html.escape(title)}</span>"
            "</li>"
        )
    return f"<li class='work-item'><span class='work-title'>{html.escape(display)}</span></li>"


def _status_pill_html(status: str, status_class: str) -> str:
    pill = "off"
    if status_class == "status-ok":
        pill = "ok"
    elif status_class == "status-warn":
        pill = "warn"
    return f"<span class='status-pill {pill}'>{html.escape(status)}</span>"


def _work_summary_ticket_html(ticket: TicketWorkSummary) -> str:
    activities = "".join(_work_summary_line_html(line) for line in ticket.lines)
    return (
        "<li class='work-ticket-group'>"
        f"<span class='work-ticket-key'>{html.escape(ticket.issue_key)}</span>"
        f"<ul class='work-activity-list'>{activities}</ul>"
        "</li>"
    )


def _work_summary_html_from_tickets(
    visible: list[TicketWorkSummary],
    hidden: list[TicketWorkSummary] | None = None,
) -> str:
    if not visible and not hidden:
        return "<span class='muted'>-</span>"
    visible_items = "".join(_work_summary_ticket_html(ticket) for ticket in visible)
    block = (
        "<div class='work-summary-wrap'>"
        f"<ul class='work-summary-list work-ticket-list'>{visible_items}</ul>"
    )
    if hidden:
        hidden_items = "".join(_work_summary_ticket_html(ticket) for ticket in hidden)
        n = len(hidden)
        block += (
            f"<ul class='work-summary-list work-summary-extra-list work-ticket-list'>{hidden_items}</ul>"
            f"<details class='work-summary-more'>"
            f"<summary>"
            f"<span class='summary-more'>Lihat {n} tiket lainnya</span>"
            f"<span class='summary-less'>Sembunyikan tiket lainnya</span>"
            f"</summary></details>"
        )
    block += "</div>"
    return block


def _split_work_tickets(
    tickets: list[TicketWorkSummary],
) -> tuple[list[TicketWorkSummary], list[TicketWorkSummary]]:
    return tickets[:WORK_SUMMARY_VISIBLE], tickets[WORK_SUMMARY_VISIBLE:]


def _work_summary_html(visible: list[str], hidden: list[str] | None = None) -> str:
    """Legacy plain-line renderer (fallback)."""
    if not visible and not hidden:
        return "<span class='muted'>-</span>"
    visible_items = "".join(_work_summary_line_html(line) for line in visible)
    block = (
        "<div class='work-summary-wrap'>"
        f"<ul class='work-summary-list'>{visible_items}</ul>"
    )
    if hidden:
        hidden_items = "".join(_work_summary_line_html(line) for line in hidden)
        n = len(hidden)
        block += (
            f"<ul class='work-summary-list work-summary-extra-list'>{hidden_items}</ul>"
            f"<details class='work-summary-more'>"
            f"<summary>"
            f"<span class='summary-more'>Lihat {n} tiket lainnya</span>"
            f"<span class='summary-less'>Sembunyikan tiket lainnya</span>"
            f"</summary></details>"
        )
    block += "</div>"
    return block


def _entry_row_attrs(entry) -> str:
    search = f"{entry.issue_key} {entry.ticket}".lower()
    done = "1" if entry.activity_normalized == "DONE" else "0"
    return (
        ' class="filter-row entry-row"'
        f' data-day="{html.escape(entry.day)}"'
        f' data-grouping="{html.escape(entry.grouping_activity)}"'
        f' data-layer="{html.escape(entry.layer)}"'
        f' data-name="{html.escape(entry.name)}"'
        f' data-issue="{html.escape(entry.issue_key)}"'
        f' data-done="{done}"'
        f' data-search="{html.escape(search)}"'
        f' data-hours="{entry.time_spent}"'
    )


def _build_filter_data(report: ReportData) -> str:
    week_days: dict[str, set[str]] = {}
    month_days: dict[str, set[str]] = {}
    for entry in report.entries:
        week_days.setdefault(entry.iso_week, set()).add(entry.day)
        month_days.setdefault(entry.month, set()).add(entry.day)

    entries = [
        {
            "day": entry.day,
            "isoWeek": entry.iso_week,
            "hours": round(entry.time_spent, 4),
            "grouping": entry.grouping_activity,
            "layer": entry.layer,
            "name": entry.name,
            "activity": entry.activity_normalized,
            "activityRaw": entry.activity,
            "issueKey": entry.issue_key,
            "ticket": entry.ticket,
            "sp": entry.story_points,
            "isDone": entry.activity_normalized == "DONE",
            "time": entry.date.strftime("%H:%M"),
        }
        for entry in report.entries
    ]
    holiday_dates = sorted(load_holidays(*years_from_entries(report.entries)))
    return json.dumps(
        {
            "targetHours": report.target_hours,
            "workdaysOnly": report.workdays_only,
            "holidays": holiday_dates,
            "days": list(report.entries_by_day.keys()),
            "weeks": [
                {"id": week, "days": sorted(days)}
                for week, days in sorted(week_days.items())
            ],
            "months": [
                {"id": month, "days": sorted(days)}
                for month, days in sorted(month_days.items())
            ],
            "groupings": sorted({entry.grouping_activity for entry in report.entries}),
            "layers": sorted({entry.layer for entry in report.entries}),
            "names": sorted({entry.name for entry in report.entries}),
            "entries": entries,
            "colors": CHART_COLORS,
            "activityCatalog": activity_catalog_for_export(),
        }
    )


def _filter_chips_html(values: list[str], group: str) -> str:
    if not values:
        return "<span class='muted'>-</span>"
    chips = "".join(
        "<button type='button' class='chip' "
        f"data-filter-group='{group}' data-value='{html.escape(value)}'>"
        f"{html.escape(value)}</button>"
        for value in values
    )
    return f"<div class='chip-group' id='filter{group.title()}'>{chips}</div>"


def _period_select_options(periods: list[tuple[str, str]]) -> str:
    if not periods:
        return ""
    return "".join(
        f'<option value="{html.escape(pid)}">{html.escape(label)}</option>'
        for pid, label in periods
    )


def _filter_bar_html(report: ReportData) -> str:
    days = list(report.entries_by_day.keys())
    groupings = sorted({entry.grouping_activity for entry in report.entries})
    layers = sorted({entry.layer for entry in report.entries})
    week_days: dict[str, set[str]] = {}
    month_days: dict[str, set[str]] = {}
    for entry in report.entries:
        week_days.setdefault(entry.iso_week, set()).add(entry.day)
        month_days.setdefault(entry.month, set()).add(entry.day)
    names = sorted({entry.name for entry in report.entries})
    week_options = _period_select_options(
        [(w, f"Minggu {w} ({len(d)} hari)") for w, d in sorted(week_days.items())]
    )
    month_options = _period_select_options(
        [(m, f"Bulan {m} ({len(d)} hari)") for m, d in sorted(month_days.items())]
    )
    return f"""
    <details id="section-filter" class="card section-collapse filter-bar no-print">
      <summary>Filter Laporan<span class="filter-summary-hint" id="filterSummaryHint"></span></summary>
      <div class="filter-grid">
        <div class="filter-field">
          <label class="filter-label">Periode cepat</label>
          <div class="quick-filters">
            <select id="filterWeek" class="filter-select">
              <option value="">Semua minggu</option>{week_options}
            </select>
            <select id="filterMonth" class="filter-select">
              <option value="">Semua bulan</option>{month_options}
            </select>
          </div>
        </div>
        <div class="filter-field">
          <label class="filter-label">Tanggal</label>
          {_filter_chips_html(days, "days")}
        </div>
        <div class="filter-field">
          <label class="filter-label">Nama</label>
          {_filter_chips_html(names, "names")}
        </div>
        <div class="filter-field">
          <label class="filter-label">Grouping</label>
          {_filter_chips_html(groupings, "groupings")}
        </div>
        <div class="filter-field">
          <label class="filter-label">Layer</label>
          {_filter_chips_html(layers, "layers")}
        </div>
        <div class="filter-field">
          <label class="filter-label" for="filterSearch">Cari tiket / judul</label>
          <input type="search" id="filterSearch" class="filter-search"
                 placeholder="CORB-69, Genesis, ...">
        </div>
        <div class="filter-field filter-actions">
          <label class="filter-check">
            <input type="checkbox" id="filterDone"> Hanya entri DONE
          </label>
          <button type="button" id="filterReset" class="filter-reset">Reset filter</button>
        </div>
      </div>
      <p class="filter-meta" id="filterMeta"></p>
    </details>"""


def _report_nav_html() -> str:
    return """
    <nav class="report-nav no-print" aria-label="Navigasi laporan">
      <a href="#section-kpi">KPI</a>
      <a href="#section-activity-ref">Referensi</a>
      <a href="#section-summary">Ringkasan</a>
      <a href="#section-team">Tim</a>
      <a href="#section-charts">Chart</a>
      <a href="#section-tickets">Tiket</a>
      <a href="#section-detail">Detail</a>
      <a href="#section-done">DONE &amp; SP</a>
    </nav>"""


def _daily_compare_rows(report: ReportData) -> str:
    if not report.daily_summaries:
        return "<tr><td colspan='9' class='muted'>(tidak ada data)</td></tr>"
    rows: list[str] = []
    holidays = load_holidays(*years_from_days([d.day for d in report.daily_summaries]))
    for day in report.daily_summaries:
        eligible = (not report.workdays_only) or is_workday(day.day, holidays)
        if eligible and day.target_met:
            status = "OK"
            status_class = "status-ok"
        elif eligible:
            status = "kurang"
            status_class = "status-warn"
        else:
            status = "libur"
            status_class = "muted"
        rows.append(
            "<tr class='summary-row'>"
            f"<td class='col-date nowrap'>{html.escape(day.day)}</td>"
            f"<td class='col-grouping copy-plain' title='{html.escape(format_day_grouping_field(day))}'>"
            f"{_grouping_tags_html(day.by_grouping)}</td>"
            f"<td class='col-work work-summary'>{_work_summary_html_from_tickets(*_split_work_tickets(day.work_summary_tickets))}</td>"
            f"<td class='col-metric num'>{html.escape(format_hours(day.total_hours))}</td>"
            f"<td class='col-metric num gap-cell'>{html.escape(format_gap(day.gap_hours))}</td>"
            f"<td class='col-metric num'>{day.entry_count}</td>"
            f"<td class='col-metric num'>{day.done_count}</td>"
            f"<td class='col-layer'>{_layer_chips_html(day.by_layer)}</td>"
            f"<td class='col-status'>{_status_pill_html(status, status_class)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _ticket_summary_html(report: ReportData) -> str:
    if not report.ticket_summaries:
        return "<p class='muted'>(tidak ada data)</p>"

    blocks: list[str] = []
    for ticket in report.ticket_summaries:
        done = "DONE" if ticket.is_done else "-"
        sp = f"{ticket.story_points:g}" if ticket.story_points else "-"
        line_rows = "".join(
            f"<tr{_entry_row_attrs(line)}>"
            f"<td>{html.escape(line.date.strftime('%Y-%m-%d %H:%M'))}</td>"
            f"<td class='num'>{html.escape(format_time_spent(line.time_spent))}</td>"
            f"<td>{html.escape(line.grouping_activity)}</td>"
            f"<td>{html.escape(line.activity)}</td>"
            "</tr>"
            for line in ticket.lines
        )
        blocks.append(
            f"""<details class="ticket-block filter-block">
  <summary class="ticket-head">
    <strong>{html.escape(ticket.issue_key)}</strong>
    <span class="badge">{html.escape(ticket.layer)}</span>
    <span class="badge">{done}</span>
    <span class="muted">{html.escape(format_hours(ticket.total_hours))} · SP {html.escape(sp)}</span>
  </summary>
  <div class="ticket-title">{html.escape(ticket.ticket)}</div>
  <table class="detail-table">
    <thead><tr><th>Waktu</th><th class="num">Jam</th><th>Grouping</th><th>Activity</th></tr></thead>
    <tbody>{line_rows}</tbody>
  </table>
</details>"""
        )
    return "".join(blocks)


def _daily_detail_html(report: ReportData) -> str:
    if not report.entries_by_day:
        return "<p class='muted'>(tidak ada data)</p>"

    blocks: list[str] = []
    for day, rows in report.entries_by_day.items():
        day_hours = sum(entry.time_spent for entry in rows)
        body = "".join(
            f"<tr{_entry_row_attrs(entry)}>"
            f"<td>{html.escape(entry.date.strftime('%H:%M'))}</td>"
            f"<td><strong>{html.escape(entry.issue_key)}</strong></td>"
            f"<td>{html.escape(entry.ticket)}</td>"
            f"<td>{html.escape(entry.layer)}</td>"
            f"<td>{html.escape(entry.grouping_activity)}</td>"
            f"<td>{html.escape(entry.activity)}</td>"
            f"<td class='num'>{html.escape(format_time_spent(entry.time_spent))}</td>"
            f"<td class='num'>{html.escape(f'{entry.story_points:g}' if entry.story_points else '-')}</td>"
            "</tr>"
            for entry in rows
        )
        blocks.append(
            f"""<details class="day-block filter-block" data-day="{html.escape(day)}">
  <summary class="day-block-title">{html.escape(day)} — {html.escape(format_hours(day_hours))} ({len(rows)} entri)</summary>
  <table class="detail-table">
    <thead>
      <tr>
        <th>Waktu</th><th>Tiket</th><th>Judul</th><th>Layer</th>
        <th>Grouping</th><th>Activity</th><th class="num">Jam</th><th class="num">SP</th>
      </tr>
    </thead>
    <tbody>{body}</tbody>
  </table>
</details>"""
        )
    return "".join(blocks)


def _chart_img(b64: str | None, alt: str) -> str:
    if not b64:
        return ""
    return (
        f'<img src="data:image/png;base64,{b64}" alt="{html.escape(alt)}" '
        f'class="chart-img chart-fallback">'
    )


def _chart_js_config(report: ReportData) -> str:
    daily = {
        "labels": [d.day for d in report.daily_summaries],
        "hours": [round(d.total_hours, 2) for d in report.daily_summaries],
        "target": report.target_hours,
    }
    grouping = {
        "labels": [format_grouping_title_case(k) for k in report.by_grouping.keys()],
        "values": [round(v, 2) for v in report.by_grouping.values()],
    }
    layer = {
        "labels": list(report.by_layer.keys()),
        "values": [round(v, 2) for v in report.by_layer.values()],
    }
    activity = {
        "labels": [format_activity_display(k) for k in report.by_activity.keys()],
        "values": [round(v, 2) for v in report.by_activity.values()],
    }
    return json.dumps(
        {
            "daily": daily,
            "grouping": grouping,
            "layer": layer,
            "activity": activity,
            "colors": CHART_COLORS,
        }
    )


def _interactive_filter_script() -> str:
    return """
    const FILTER = JSON.parse(document.getElementById('filterData').textContent);
    const filterState = { days: new Set(), groupings: new Set(), layers: new Set(), names: new Set(), search: '', doneOnly: false };
    let chartInstances = {};
    let currentSummaryOnly = '';
    let dailySummariesCache = [];

    function esc(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function formatHours(h) {
      if (!h) return '0.00 jam';
      const hrs = Math.floor(h);
      const mins = Math.round((h - hrs) * 60);
      if (!mins) return h.toFixed(2) + ' jam';
      return h.toFixed(2) + ' jam (' + hrs + 'j ' + mins + 'm)';
    }

    function formatGap(gap) {
      if (gap >= 0) return '+' + gap.toFixed(2) + ' jam';
      return gap.toFixed(2) + ' jam';
    }

    function sumBy(entries, keyFn) {
      const totals = new Map();
      entries.forEach((entry) => {
        const key = keyFn(entry);
        totals.set(key, (totals.get(key) || 0) + entry.hours);
      });
      return [...totals.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    }

    function breakdownList(pairs, withHours) {
      if (!pairs.length) return '<span class="muted">-</span>';
      const items = pairs.map(([label, value]) => {
        const left = esc(label.length > 18 ? label.slice(0, 17) + '…' : label);
        const right = withHours ? '<span class="num">' + value.toFixed(2) + 'j</span>' : '';
        return '<li><span>' + left + '</span>' + right + '</li>';
      }).join('');
      return '<ul class="breakdown-list">' + items + '</ul>';
    }

    function isWorklogTicket(ticket) {
      return /^worklog\\b/i.test((ticket || '').trim());
    }

    function titleCaseLabel(label) {
      return label.split(' & ').map((part) => {
        const trimmed = part.trim();
        if (!trimmed) return trimmed;
        return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
      }).join(' & ');
    }

    function canonicalActivityKind(raw) {
      let kind = (raw || '').trim().replace(/\\*+/g, '').toUpperCase();
      if (/^GROOMING-\\d+$/i.test(kind)) kind = 'GROOMING';
      if (kind === 'REVISI') kind = 'REVISIT';
      return kind;
    }

    function formatActivityDisplay(kind) {
      const key = canonicalActivityKind(kind);
      const info = (FILTER.activityCatalog || {})[key];
      return info ? info.title : titleCaseLabel(key);
    }

    function activityDescription(kind) {
      const info = (FILTER.activityCatalog || {})[canonicalActivityKind(kind)];
      return info ? info.description : '';
    }

    function activityKindKeyFromEntry(entry) {
      const activity = (entry.activityRaw || entry.activity || '').trim();
      const match = /^\\[([^\\]]+)\\]\\s*(.*)/s.exec(activity);
      if (match) return canonicalActivityKind(match[1]);
      return canonicalActivityKind(entry.activity || '');
    }

    function sumActivityCategory(entries) {
      const totals = new Map();
      entries.forEach((entry) => {
        const key = activityKindKeyFromEntry(entry);
        const info = (FILTER.activityCatalog || {})[key];
        const label = info ? info.categoryLabel : 'Lainnya';
        totals.set(label, (totals.get(label) || 0) + entry.hours);
      });
      return [...totals.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    }

    function formatWorklogSummaryLine(entry, includeHours = false) {
      const activity = (entry.activityRaw || entry.activity || '').trim();
      const ticket = (entry.ticket || '').trim();
      const match = /^\\[([^\\]]+)\\]\\s*(.*)/s.exec(activity);
      let line;
      if (match) {
        const kind = formatActivityDisplay(match[1]);
        let detail = match[2].trim();
        if (!detail && !isWorklogTicket(ticket)) detail = ticket;
        if (detail && detail.length > 120) detail = detail.slice(0, 117) + '...';
        line = detail ? '[' + kind + '] ' + detail : '[' + kind + ']';
      } else if (isWorklogTicket(ticket)) {
        line = activity || '-';
      } else if (activity) {
        const shortTicket = ticket.length > 120 ? ticket.slice(0, 117) + '...' : ticket;
        line = shortTicket + ' — ' + activity;
      } else {
        line = ticket.length > 120 ? ticket.slice(0, 117) + '...' : (ticket || '-');
      }
      if (includeHours) return line + ' (' + entry.hours.toFixed(2) + 'j)';
      return line;
    }

    function buildWorkSummaryPlain(entries, maxTickets = 6) {
      const byIssue = new Map();
      [...entries].sort((a, b) => (a.time || '').localeCompare(b.time || '')).forEach((entry) => {
        if (!byIssue.has(entry.issueKey)) byIssue.set(entry.issueKey, []);
        byIssue.get(entry.issueKey).push(entry);
      });
      const sorted = [...byIssue.entries()].sort((a, b) => {
        const hoursA = a[1].reduce((sum, item) => sum + item.hours, 0);
        const hoursB = b[1].reduce((sum, item) => sum + item.hours, 0);
        return hoursB - hoursA || a[0].localeCompare(b[0]);
      });
      const blocks = sorted.map(([issueKey, issueEntries]) => {
        const lines = issueEntries.map((entry) => formatWorklogSummaryLine(entry, false));
        return {
          issueKey,
          lines,
          plain: issueKey + ': ' + lines.join('; '),
        };
      });
      return {
        visible: blocks.slice(0, maxTickets),
        hidden: blocks.slice(maxTickets),
      };
    }

    function groupingTagsHtml(pairs) {
      if (!pairs.length) return '<span class="muted">-</span>';
      const sorted = [...pairs].sort((a, b) => a[0].localeCompare(b[0]));
      const tags = sorted.map(([label]) => {
        return '<span class="tag tag-grouping">' + esc(titleCaseLabel(label)) + '</span>';
      }).join('');
      return '<div class="tag-list">' + tags + '</div>';
    }

    function layerTagsHtml(pairs) {
      if (!pairs.length) return '<span class="muted">-</span>';
      const tags = pairs.map(([label, value]) => {
        const short = label.length > 14 ? label.slice(0, 13) + '…' : label;
        return '<span class="tag tag-layer">' + esc(short) + '<span class="tag-hours">' + value.toFixed(2) + 'j</span></span>';
      }).join('');
      return '<div class="tag-list">' + tags + '</div>';
    }

    function workSummaryLineHtml(line) {
      const hoursSuffix = /\\s+\\([\\d.]+j\\)$/;
      const display = line.replace(hoursSuffix, '');
      const bracketMatch = /^\\[([^\\]]+)\\]\\s*(.*)/s.exec(display);
      if (bracketMatch) {
        const kind = bracketMatch[1];
        const detail = bracketMatch[2].trim();
        const desc = activityDescription(kind);
        const kindTitle = desc ? ' title="' + esc(desc) + '"' : '';
        const detailHtml = detail
          ? '<span class="work-title" title="' + esc(detail) + '">' + esc(detail) + '</span>'
          : '';
        return '<li class="work-item"><span class="work-key"' + kindTitle + '>[' + esc(kind) + ']</span>' + detailHtml + '</li>';
      }
      const sep = display.indexOf(' — ');
      if (sep > 0) {
        const keyPart = display.slice(0, sep);
        const title = display.slice(sep + 3);
        const keyMatch = keyPart.match(/([A-Z]+-\\d+)/);
        const key = keyMatch ? keyMatch[1] : keyPart.trim();
        return '<li class="work-item"><span class="work-key">' + esc(key) + '</span>'
          + '<span class="work-title" title="' + esc(title) + '">' + esc(title) + '</span></li>';
      }
      return '<li class="work-item"><span class="work-title">' + esc(display) + '</span></li>';
    }

    function workSummaryTicketHtml(block) {
      const activities = block.lines.map(workSummaryLineHtml).join('');
      return '<li class="work-ticket-group"><span class="work-ticket-key">' + esc(block.issueKey)
        + '</span><ul class="work-activity-list">' + activities + '</ul></li>';
    }

    function workSummaryHtml(summary) {
      if (!summary.visible.length && !summary.hidden.length) {
        return '<span class="muted">-</span>';
      }
      const items = summary.visible.map(workSummaryTicketHtml).join('');
      let html = '<div class="work-summary-wrap"><ul class="work-summary-list work-ticket-list">' + items + '</ul>';
      if (summary.hidden.length) {
        const hiddenItems = summary.hidden.map(workSummaryTicketHtml).join('');
        html += '<ul class="work-summary-list work-summary-extra-list work-ticket-list">' + hiddenItems + '</ul>'
          + '<details class="work-summary-more"><summary>'
          + '<span class="summary-more">Lihat ' + summary.hidden.length + ' tiket lainnya</span>'
          + '<span class="summary-less">Sembunyikan tiket lainnya</span>'
          + '</summary></details>';
      }
      html += '</div>';
      return html;
    }

    function ticketBlocksPlain(blocks) {
      return blocks.map((block) => block.plain).join('; ') || '-';
    }

    function statusPill(status, eligible, targetMet) {
      let pill = 'off';
      if (eligible) pill = targetMet ? 'ok' : 'warn';
      return '<span class="status-pill ' + pill + '">' + esc(status) + '</span>';
    }

    function isWorkday(day) {
      const d = new Date(day + 'T12:00:00');
      const wd = d.getDay();
      if (wd === 0 || wd === 6) return false;
      if (FILTER.holidays && FILTER.holidays.includes(day)) return false;
      return true;
    }

    function countsForTarget(day) {
      return !FILTER.workdaysOnly || isWorkday(day);
    }

    function buildDailySummaries(entries) {
      const byDay = new Map();
      entries.forEach((entry) => {
        if (!byDay.has(entry.day)) byDay.set(entry.day, []);
        byDay.get(entry.day).push(entry);
      });
      return [...byDay.keys()].sort().map((day) => {
        const dayEntries = byDay.get(day);
        const total = dayEntries.reduce((sum, entry) => sum + entry.hours, 0);
        const doneKeys = new Set(dayEntries.filter((entry) => entry.isDone).map((entry) => entry.issueKey));
        const eligible = countsForTarget(day);
        return {
          day,
          total,
          entryCount: dayEntries.length,
          doneCount: doneKeys.size,
          grouping: sumBy(dayEntries, (entry) => entry.grouping),
          layer: sumBy(dayEntries, (entry) => entry.layer),
          workSummary: buildWorkSummaryPlain(dayEntries),
          targetMet: eligible && total >= FILTER.targetHours,
          gap: total - FILTER.targetHours,
          eligible,
        };
      });
    }

    function tsvEscape(value) {
      if (/[\\t\\n"]/.test(value)) return '"' + value.replace(/"/g, '""') + '"';
      return value;
    }

    let lastDailySummaries = [];

    function groupingPlain(pairs) {
      const labels = pairs.map(([label]) => titleCaseLabel(label));
      return labels.sort((a, b) => a.localeCompare(b)).join('; ') || '-';
    }

    function buildGroupingCopyText(dailySummaries) {
      return dailySummaries.map((day) => groupingPlain(day.grouping)).join('\\n');
    }

    function buildExcelTsv(dailySummaries) {
      return dailySummaries.map((day) => {
        const grouping = groupingPlain(day.grouping);
        const allBlocks = day.workSummary.visible.concat(day.workSummary.hidden);
        const summary = ticketBlocksPlain(allBlocks);
        return day.day + '\\t' + tsvEscape(grouping) + '\\t' + tsvEscape(summary);
      }).join('\\n');
    }

    function syncDayChips() {
      document.querySelectorAll('.chip[data-filter-group="days"]').forEach((chip) => {
        chip.classList.toggle('active', filterState.days.has(chip.dataset.value));
      });
    }

    function syncPeriodSelects() {
      const weekEl = document.getElementById('filterWeek');
      const monthEl = document.getElementById('filterMonth');
      if (!weekEl || !monthEl) return;
      let matchedWeek = '';
      let matchedMonth = '';
      if (filterState.days.size) {
        const selected = [...filterState.days].sort();
        const week = (FILTER.weeks || []).find((item) => {
          const days = [...item.days].sort();
          return days.length === selected.length && days.every((day, idx) => day === selected[idx]);
        });
        if (week) matchedWeek = week.id;
        const month = (FILTER.months || []).find((item) => {
          const days = [...item.days].sort();
          return days.length === selected.length && days.every((day, idx) => day === selected[idx]);
        });
        if (month) matchedMonth = month.id;
      }
      weekEl.value = matchedWeek;
      monthEl.value = matchedMonth;
    }

    function setDays(days) {
      filterState.days = new Set(days);
      syncDayChips();
      syncPeriodSelects();
      applyFilters();
    }

    function matchesEntry(entry) {
      if (filterState.days.size && !filterState.days.has(entry.day)) return false;
      if (filterState.groupings.size && !filterState.groupings.has(entry.grouping)) return false;
      if (filterState.layers.size && !filterState.layers.has(entry.layer)) return false;
      if (filterState.names.size && !filterState.names.has(entry.name)) return false;
      if (filterState.doneOnly && !entry.isDone) return false;
      if (filterState.search) {
        const haystack = (entry.issueKey + ' ' + entry.ticket).toLowerCase();
        if (!haystack.includes(filterState.search)) return false;
      }
      return true;
    }

    function getFilteredEntries() {
      return FILTER.entries.filter(matchesEntry);
    }

    function rowMatchesDataset(row) {
      const entry = {
        day: row.dataset.day,
        grouping: row.dataset.grouping,
        layer: row.dataset.layer,
        name: row.dataset.name,
        isDone: row.dataset.done === '1',
        issueKey: row.dataset.issue,
        ticket: row.dataset.search || '',
      };
      if (filterState.search) {
        const haystack = (entry.issueKey + ' ' + entry.ticket).toLowerCase();
        if (!haystack.includes(filterState.search)) return false;
      }
      return matchesEntry(entry);
    }

    function getChartTheme() {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      return {
        tick: dark ? '#94a3b8' : '#64748b',
        grid: dark ? '#334155' : '#e2e8f0',
        legend: dark ? '#f1f5f9' : '#0f172a',
      };
    }

    function chartScales(includeX) {
      const t = getChartTheme();
      const scales = {
        y: { ticks: { color: t.tick }, grid: { color: t.grid }, beginAtZero: true },
      };
      if (includeX) {
        scales.x = { ticks: { color: t.tick }, grid: { color: t.grid } };
      }
      return scales;
    }

    function chartPlugins(showLegend) {
      const t = getChartTheme();
      return { legend: { display: showLegend, labels: { color: t.legend } } };
    }

    function initActivityInfoPopover() {
      const popover = document.getElementById('activityInfoPopover');
      if (!popover) return;
      let activeBtn = null;

      function closePopover() {
        if (popover.hidden) return;
        popover.hidden = true;
        if (activeBtn) {
          activeBtn.setAttribute('aria-expanded', 'false');
          activeBtn = null;
        }
      }

      function positionPopover(btn) {
        const rect = btn.getBoundingClientRect();
        const margin = 8;
        popover.hidden = false;
        const popW = popover.offsetWidth;
        const popH = popover.offsetHeight;
        let left = rect.left + rect.width / 2 - popW / 2;
        let top = rect.bottom + margin;
        left = Math.max(margin, Math.min(left, window.innerWidth - popW - margin));
        if (top + popH > window.innerHeight - margin) {
          top = Math.max(margin, rect.top - popH - margin);
        }
        popover.style.left = left + 'px';
        popover.style.top = top + 'px';
      }

      document.querySelectorAll('[data-activity-info-btn]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!popover.hidden && activeBtn === btn) {
            closePopover();
            return;
          }
          if (activeBtn && activeBtn !== btn) {
            activeBtn.setAttribute('aria-expanded', 'false');
          }
          activeBtn = btn;
          btn.setAttribute('aria-expanded', 'true');
          positionPopover(btn);
        });
      });

      document.addEventListener('click', (e) => {
        if (popover.hidden) return;
        if (popover.contains(e.target)) return;
        if (e.target.closest('[data-activity-info-btn]')) return;
        closePopover();
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePopover();
      });
      window.addEventListener('resize', () => {
        if (!popover.hidden && activeBtn) positionPopover(activeBtn);
      });
      window.addEventListener('scroll', () => {
        if (!popover.hidden && activeBtn) positionPopover(activeBtn);
      }, { passive: true });
    }

    function initThemeToggle() {
      const btn = document.getElementById('themeToggle');
      if (!btn) return;
      const syncLabel = () => {
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        btn.textContent = dark ? '☀️ Light mode' : '🌙 Dark mode';
        btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
      };
      syncLabel();
      btn.addEventListener('click', () => {
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        const next = dark ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('worklog-theme', next);
        syncLabel();
        applyFilters();
      });
    }

    function destroyChart(id) {
      if (chartInstances[id]) {
        chartInstances[id].destroy();
        delete chartInstances[id];
      }
    }

    function updateCharts(entries) {
      if (typeof Chart === 'undefined') return;
      const daily = buildDailySummaries(entries);
      const grouping = sumBy(entries, (entry) => entry.grouping);
      const layer = sumBy(entries, (entry) => entry.layer);
      const activity = sumBy(entries, activityKindKeyFromEntry);
      const activityCategory = sumActivityCategory(entries);

      destroyChart('dailyChart');
      const dailyCtx = document.getElementById('dailyChart');
      dailySummariesCache = daily;
      if (dailyCtx && daily.length) {
        const barColors = daily.map((day, idx) => {
          if (!day.eligible) return '#64748b';
          return day.targetMet ? (FILTER.colors[idx % FILTER.colors.length]) : '#ef4444';
        });
        chartInstances.dailyChart = new Chart(dailyCtx, {
          type: 'bar',
          data: {
            labels: daily.map((day) => day.day),
            datasets: [
              {
                label: 'Jam',
                data: daily.map((day) => +day.total.toFixed(2)),
                backgroundColor: barColors,
              },
              {
                type: 'line',
                label: 'Target',
                data: daily.map(() => FILTER.targetHours),
                borderColor: '#ef4444',
                borderDash: [6, 4],
                backgroundColor: 'transparent',
                pointRadius: 0,
              },
            ],
          },
          options: {
            responsive: true,
            plugins: chartPlugins(true),
            scales: chartScales(true),
            onClick: (evt, elements) => {
              if (!elements.length) return;
              const idx = elements[0].index;
              const day = dailySummariesCache[idx];
              if (!day) return;
              setDays([day.day]);
            },
          },
        });
      }

      [['groupingChart', grouping, true], ['layerChart', layer, false]].forEach(([id, pairs, useTitleCase]) => {
        destroyChart(id);
        const ctx = document.getElementById(id);
        if (!ctx || !pairs.length) return;
        chartInstances[id] = new Chart(ctx, {
          type: 'doughnut',
          data: {
            labels: pairs.map(([label]) => useTitleCase ? titleCaseLabel(label) : label),
            datasets: [{ data: pairs.map(([, value]) => +value.toFixed(2)), backgroundColor: FILTER.colors }],
          },
          options: { responsive: true, plugins: chartPlugins(true) },
        });
      });

      destroyChart('activityChart');
      const activityCtx = document.getElementById('activityChart');
      if (activityCtx && activity.length) {
        chartInstances.activityChart = new Chart(activityCtx, {
          type: 'doughnut',
          data: {
            labels: activity.map(([label]) => formatActivityDisplay(label)),
            datasets: [{ data: activity.map(([, value]) => +value.toFixed(2)), backgroundColor: FILTER.colors }],
          },
          options: { responsive: true, plugins: chartPlugins(true) },
        });
      }

      const activityBody = document.getElementById('activityTableBody');
      if (activityBody) {
        activityBody.innerHTML = activity.length
          ? activity.map(([label, value]) => {
              const display = formatActivityDisplay(label);
              const desc = activityDescription(label);
              const title = desc ? ' title="' + esc(desc) + '"' : '';
              const descHtml = desc ? '<div class="activity-desc">' + esc(desc) + '</div>' : '';
              return '<tr><td' + title + '><div class="activity-label">' + esc(display) + '</div>' + descHtml
                + '</td><td class="num">' + esc(formatHours(value)) + '</td></tr>';
            }).join('')
          : '<tr><td colspan="2" class="muted">(tidak ada data)</td></tr>';
      }

      const activityCategoryBody = document.getElementById('activityCategoryBody');
      if (activityCategoryBody) {
        activityCategoryBody.innerHTML = activityCategory.length
          ? activityCategory.map(([label, value]) => {
              return '<tr><td>' + esc(label) + '</td><td class="num">' + esc(formatHours(value)) + '</td></tr>';
            }).join('')
          : '<tr><td colspan="2" class="muted">(tidak ada data)</td></tr>';
      }
    }

    function updateKpis(entries) {
      const total = entries.reduce((sum, entry) => sum + entry.hours, 0);
      const daily = buildDailySummaries(entries);
      const eligible = daily.filter((day) => day.eligible);
      const daysMet = eligible.filter((day) => day.targetMet).length;
      const eligibleHours = eligible.reduce((sum, day) => sum + day.total, 0);
      const avg = eligible.length ? eligibleHours / eligible.length : 0;
      const suffix = FILTER.workdaysOnly ? ' (hari kerja)' : '';

      const totalEl = document.getElementById('kpiTotalHours');
      const targetEl = document.getElementById('kpiTarget');
      const avgEl = document.getElementById('kpiAvgHours');
      const spEl = document.getElementById('kpiSpEfficiency');
      if (totalEl) totalEl.textContent = formatHours(total);
      if (targetEl) {
        targetEl.innerHTML = FILTER.targetHours + ' jam<small>' + daysMet + '/' + eligible.length + ' hari tercapai' + suffix + '</small>';
      }
      if (avgEl) {
        avgEl.innerHTML = formatHours(avg) + '<small>' + entries.length + ' entri ditampilkan</small>';
      }
      if (spEl) {
        const byIssue = new Map();
        entries.forEach((entry) => {
          if (entry.sp <= 0) return;
          const current = byIssue.get(entry.issueKey) || { sp: entry.sp, hours: 0 };
          current.hours += entry.hours;
          byIssue.set(entry.issueKey, current);
        });
        const totalSp = [...byIssue.values()].reduce((sum, item) => sum + item.sp, 0);
        const hoursOnSp = [...byIssue.values()].reduce((sum, item) => sum + item.hours, 0);
        if (totalSp > 0) {
          spEl.innerHTML = (hoursOnSp / totalSp).toFixed(2) + ' jam/SP<small>' + totalSp + ' SP · ' + formatHours(hoursOnSp) + '</small>';
        } else {
          spEl.innerHTML = '-<small>tidak ada SP</small>';
        }
      }
    }

    function updateDailySummaryTable(dailySummaries) {
      const body = document.getElementById('dailySummaryBody');
      if (!body) return;
      if (!dailySummaries.length) {
        body.innerHTML = '<tr><td colspan="9" class="muted">(tidak ada data)</td></tr>';
        lastDailySummaries = [];
        currentSummaryOnly = '';
        return;
      }
      lastDailySummaries = dailySummaries;
      body.innerHTML = dailySummaries.map((day) => {
        const status = !day.eligible ? 'libur' : (day.targetMet ? 'OK' : 'kurang');
        const groupingTitle = groupingPlain(day.grouping);
        return '<tr class="summary-row">'
          + '<td class="col-date nowrap">' + esc(day.day) + '</td>'
          + '<td class="col-grouping copy-plain" title="' + esc(groupingTitle) + '">' + groupingTagsHtml(day.grouping) + '</td>'
          + '<td class="col-work work-summary">' + workSummaryHtml(day.workSummary) + '</td>'
          + '<td class="col-metric num">' + esc(formatHours(day.total)) + '</td>'
          + '<td class="col-metric num gap-cell">' + esc(formatGap(day.gap)) + '</td>'
          + '<td class="col-metric num">' + day.entryCount + '</td>'
          + '<td class="col-metric num">' + day.doneCount + '</td>'
          + '<td class="col-layer">' + layerTagsHtml(day.layer) + '</td>'
          + '<td class="col-status">' + statusPill(status, day.eligible, day.targetMet) + '</td>'
          + '</tr>';
      }).join('');
      currentSummaryOnly = dailySummaries.map((day) => {
        const allBlocks = day.workSummary.visible.concat(day.workSummary.hidden);
        return ticketBlocksPlain(allBlocks);
      }).join('\\n');
    }

    function updateTopTickets(entries) {
      const body = document.getElementById('topTicketBody');
      if (!body) return;
      const byIssue = sumBy(entries, (entry) => entry.issueKey);
      const top = byIssue.slice(0, 15);
      if (!top.length) {
        body.innerHTML = '<tr><td colspan="2" class="muted">(tidak ada data)</td></tr>';
        return;
      }
      body.innerHTML = top.map(([key, hours]) => {
        const ticket = entries.find((entry) => entry.issueKey === key)?.ticket || key;
        let title = ticket;
        if (title.length > 60) title = title.slice(0, 57) + '...';
        return '<tr><td>' + esc(key + ' — ' + title) + '</td><td class="num">' + esc(formatHours(hours)) + '</td></tr>';
      }).join('');
    }

    function updateDoneTable(entries) {
      const body = document.getElementById('doneTableBody');
      const title = document.getElementById('doneSectionTitle');
      const doneKeys = new Set(entries.filter((entry) => entry.isDone).map((entry) => entry.issueKey));
      if (title) title.textContent = 'Tiket DONE (' + doneKeys.size + ')';
      if (!body) return;
      if (!doneKeys.size) {
        body.innerHTML = '<tr><td colspan="2" class="muted">(tidak ada data)</td></tr>';
        return;
      }
      body.innerHTML = [...doneKeys].sort().map((key) => {
        const issueEntries = entries.filter((entry) => entry.issueKey === key);
        const hours = issueEntries.reduce((sum, entry) => sum + entry.hours, 0);
        const sp = issueEntries.find((entry) => entry.sp > 0)?.sp;
        const spLabel = sp ? 'SP ' + sp : 'SP -';
        return '<tr><td>' + esc(key + ' (' + spLabel + ')') + '</td><td class="num">' + esc(formatHours(hours)) + '</td></tr>';
      }).join('');
    }

    function updateSpTable(entries) {
      const body = document.getElementById('spTableBody');
      const title = document.getElementById('spSectionTitle');
      const byIssue = new Map();
      entries.forEach((entry) => {
        if (entry.sp <= 0) return;
        const current = byIssue.get(entry.issueKey) || { sp: entry.sp, hours: 0 };
        current.hours += entry.hours;
        byIssue.set(entry.issueKey, current);
      });
      const sorted = [...byIssue.entries()].sort((a, b) => b[1].sp - a[1].sp || a[0].localeCompare(b[0]));
      const totalSp = sorted.reduce((sum, [, value]) => sum + value.sp, 0);
      if (title) title.textContent = 'Story Points (' + sorted.length + ' tiket, total ' + totalSp + ' SP)';
      if (!body) return;
      if (!sorted.length) {
        body.innerHTML = '<tr><td colspan="3" class="muted">(tidak ada data)</td></tr>';
        return;
      }
      body.innerHTML = sorted.map(([key, value]) => {
        const hps = value.sp > 0 ? (value.hours / value.sp).toFixed(2) + ' jam/SP' : '-';
        return '<tr><td>' + esc(key + ' — SP ' + value.sp) + '</td><td class="num">' + esc(formatHours(value.hours)) + '</td><td class="num">' + esc(hps) + '</td></tr>';
      }).join('');
    }

    function updateSpVelocity(entries) {
      const body = document.getElementById('spVelocityBody');
      if (!body) return;
      const byWeek = new Map();
      entries.forEach((entry) => {
        if (!entry.isDone || entry.sp <= 0) return;
        const bucket = byWeek.get(entry.isoWeek) || { sp: 0, hours: 0, keys: new Set() };
        bucket.hours += entry.hours;
        if (!bucket.keys.has(entry.issueKey)) {
          bucket.keys.add(entry.issueKey);
          bucket.sp += entry.sp;
        }
        byWeek.set(entry.isoWeek, bucket);
      });
      const rows = [...byWeek.entries()].sort((a, b) => a[0].localeCompare(b[0]));
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="3" class="muted">(tidak ada data)</td></tr>';
        return;
      }
      body.innerHTML = rows.map(([week, value]) =>
        '<tr><td>' + esc(week) + '</td><td class="num">' + value.sp + ' SP</td><td class="num">' + esc(formatHours(value.hours)) + ' · ' + value.keys.size + ' tiket</td></tr>'
      ).join('');
    }

    function updateDetailSections() {
      document.querySelectorAll('.entry-row').forEach((row) => {
        row.hidden = !rowMatchesDataset(row);
      });
      document.querySelectorAll('.filter-block').forEach((block) => {
        const rows = block.querySelectorAll('.entry-row');
        const visible = [...rows].some((row) => !row.hidden);
        block.hidden = !visible;
        const title = block.querySelector('.day-block-title');
        if (title && block.dataset.day) {
          const visibleRows = [...rows].filter((row) => !row.hidden);
          const hours = visibleRows.reduce((sum, row) => sum + parseFloat(row.dataset.hours || '0'), 0);
          title.textContent = block.dataset.day + ' — ' + formatHours(hours) + ' (' + visibleRows.length + ' entri)';
        }
      });
      const ticketTitle = document.getElementById('ticketSectionTitle');
      const detailTitle = document.getElementById('detailSectionTitle');
      const visibleTickets = document.querySelectorAll('.ticket-block:not([hidden])').length;
      const visibleEntries = document.querySelectorAll('.entry-row:not([hidden])').length;
      if (ticketTitle) ticketTitle.textContent = 'Ringkasan per Tiket (' + visibleTickets + ' ditampilkan)';
      if (detailTitle) detailTitle.textContent = 'Detail Work Log (' + visibleEntries + ' entri ditampilkan)';
    }

    function updateFilterMeta(entries) {
      const meta = document.getElementById('filterMeta');
      if (!meta) return;
      const active = [];
      if (filterState.days.size) active.push(filterState.days.size + ' tanggal');
      if (filterState.groupings.size) active.push(filterState.groupings.size + ' grouping');
      if (filterState.layers.size) active.push(filterState.layers.size + ' layer');
      if (filterState.names.size) active.push(filterState.names.size + ' nama');
      if (filterState.doneOnly) active.push('DONE');
      if (filterState.search) active.push('cari: "' + filterState.search + '"');
      const suffix = active.length ? ' · Filter: ' + active.join(', ') : '';
      meta.textContent = 'Menampilkan ' + entries.length + ' dari ' + FILTER.entries.length + ' entri' + suffix;
      const hint = document.getElementById('filterSummaryHint');
      if (hint) {
        hint.textContent = ' — ' + entries.length + '/' + FILTER.entries.length + ' entri'
          + (active.length ? ' · ' + active.join(', ') : '');
      }
    }

    function updateStickyOffset() {
      const filter = document.getElementById('section-filter');
      let offset = 0;
      if (filter) {
        const rect = filter.getBoundingClientRect();
        // Hanya offset saat filter benar-benar menempel di atas viewport
        if (rect.top <= 1) {
          offset = Math.max(0, Math.ceil(rect.bottom));
        }
      }
      document.documentElement.style.setProperty('--sticky-offset', offset + 'px');
    }

    function applyFilters() {
      const entries = getFilteredEntries();
      const dailySummaries = buildDailySummaries(entries);
      updateKpis(entries);
      updateDailySummaryTable(dailySummaries);
      updateTopTickets(entries);
      updateDoneTable(entries);
      updateSpTable(entries);
      updateSpVelocity(entries);
      updateCharts(entries);
      updateDetailSections();
      updateFilterMeta(entries);
      requestAnimationFrame(updateStickyOffset);
    }

    function toggleChip(chip) {
      const group = chip.dataset.filterGroup;
      const value = chip.dataset.value;
      const set = filterState[group];
      if (set.has(value)) {
        set.delete(value);
        chip.classList.remove('active');
      } else {
        set.add(value);
        chip.classList.add('active');
      }
      if (group === 'days') {
        const weekEl = document.getElementById('filterWeek');
        const monthEl = document.getElementById('filterMonth');
        if (weekEl) weekEl.value = '';
        if (monthEl) monthEl.value = '';
      }
      applyFilters();
    }

    function resetFilters() {
      filterState.days.clear();
      filterState.groupings.clear();
      filterState.layers.clear();
      filterState.names.clear();
      filterState.search = '';
      filterState.doneOnly = false;
      document.querySelectorAll('.chip.active').forEach((chip) => chip.classList.remove('active'));
      const search = document.getElementById('filterSearch');
      const done = document.getElementById('filterDone');
      const weekEl = document.getElementById('filterWeek');
      const monthEl = document.getElementById('filterMonth');
      if (search) search.value = '';
      if (done) done.checked = false;
      if (weekEl) weekEl.value = '';
      if (monthEl) monthEl.value = '';
      applyFilters();
    }

    function initFilters() {
      document.querySelectorAll('.chip').forEach((chip) => {
        chip.addEventListener('click', () => toggleChip(chip));
      });
      const search = document.getElementById('filterSearch');
      if (search) {
        search.addEventListener('input', () => {
          filterState.search = search.value.trim().toLowerCase();
          applyFilters();
        });
      }
      const done = document.getElementById('filterDone');
      if (done) {
        done.addEventListener('change', () => {
          filterState.doneOnly = done.checked;
          applyFilters();
        });
      }
      const reset = document.getElementById('filterReset');
      if (reset) reset.addEventListener('click', resetFilters);

      const weekEl = document.getElementById('filterWeek');
      if (weekEl) {
        weekEl.addEventListener('change', () => {
          const monthEl = document.getElementById('filterMonth');
          if (monthEl) monthEl.value = '';
          if (!weekEl.value) {
            filterState.days.clear();
            syncDayChips();
            applyFilters();
            return;
          }
          const week = (FILTER.weeks || []).find((item) => item.id === weekEl.value);
          if (week) setDays(week.days);
        });
      }
      const monthEl = document.getElementById('filterMonth');
      if (monthEl) {
        monthEl.addEventListener('change', () => {
          const weekSelect = document.getElementById('filterWeek');
          if (weekSelect) weekSelect.value = '';
          if (!monthEl.value) {
            filterState.days.clear();
            syncDayChips();
            applyFilters();
            return;
          }
          const month = (FILTER.months || []).find((item) => item.id === monthEl.value);
          if (month) setDays(month.days);
        });
      }

      const copyTsvBtn = document.getElementById('copyTsvBtn');
      if (copyTsvBtn) {
        copyTsvBtn.addEventListener('click', () => {
          copyText(buildExcelTsv(lastDailySummaries), copyTsvBtn);
        });
      }
      const copySummaryBtn = document.getElementById('copySummaryBtn');
      if (copySummaryBtn) {
        copySummaryBtn.addEventListener('click', () => copyText(currentSummaryOnly, copySummaryBtn));
      }
      const copyGroupingBtn = document.getElementById('copyGroupingBtn');
      if (copyGroupingBtn) {
        copyGroupingBtn.addEventListener('click', () => {
          copyText(buildGroupingCopyText(lastDailySummaries), copyGroupingBtn);
        });
      }

      initThemeToggle();
      initActivityInfoPopover();

      const filterSection = document.getElementById('section-filter');
      if (filterSection) {
        filterSection.addEventListener('toggle', () => {
          requestAnimationFrame(updateStickyOffset);
        });
      }
      window.addEventListener('resize', updateStickyOffset);
      window.addEventListener('scroll', updateStickyOffset, { passive: true });

      applyFilters();
      updateStickyOffset();
    }

    document.addEventListener('DOMContentLoaded', initFilters);
    """


def _theme_init_script() -> str:
    return """
    (function () {
      var saved = localStorage.getItem('worklog-theme');
      var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      var theme = saved || (prefersDark ? 'dark' : 'light');
      document.documentElement.setAttribute('data-theme', theme);
    })();
    """


def _report_styles() -> str:
    return """
    :root {
      --bg: #f8fafc; --card: #fff; --text: #0f172a; --muted: #64748b;
      --border: #e2e8f0; --accent: #4f46e5; --ok: #10b981; --warn: #f59e0b;
      --surface: #fff; --surface-muted: #f1f5f9;
      --badge-bg: #eef2ff; --badge-text: #4338ca;
      --input-bg: #fff; --shadow: rgba(15, 23, 42, 0.08);
      --chart-grid: #e2e8f0; --chart-tick: #64748b; --chart-legend: #0f172a;
      --sticky-offset: 0px;
    }
    [data-theme="dark"] {
      --bg: #0f172a; --card: #1e293b; --text: #f1f5f9; --muted: #94a3b8;
      --border: #334155; --accent: #818cf8; --ok: #34d399; --warn: #fbbf24;
      --surface: #1e293b; --surface-muted: #0f172a;
      --badge-bg: #312e81; --badge-text: #c7d2fe;
      --input-bg: #0f172a; --shadow: rgba(0, 0, 0, 0.35);
      --chart-grid: #334155; --chart-tick: #94a3b8; --chart-legend: #f1f5f9;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.5;
      transition: background 0.2s ease, color 0.2s ease;
    }
    .wrap { max-width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 24px 16px 48px; }
    .page-header {
      display: flex; flex-wrap: wrap; align-items: flex-start;
      justify-content: space-between; gap: 16px; margin-bottom: 8px;
    }
    .page-header h1 { font-size: 1.6rem; margin: 0 0 4px; }
    .subtitle { color: var(--muted); margin: 0 0 24px; }
    .page-header .subtitle { margin-bottom: 0; }
    .theme-toggle {
      border: 1px solid var(--border); background: var(--surface);
      color: var(--text); border-radius: 999px; padding: 8px 14px;
      font-size: 0.85rem; cursor: pointer; white-space: nowrap;
      transition: border-color 0.15s, color 0.15s;
    }
    .theme-toggle:hover { border-color: var(--accent); color: var(--accent); }
    .grid { display: grid; gap: 16px; }
    .grid-2 { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
    .grid-3 { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 16px 18px;
      transition: background 0.2s ease, border-color 0.2s ease;
    }
    .card h2 { font-size: 1rem; margin: 0 0 12px; }
    .card-heading {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
    }
    .card-heading h2 { margin: 0; flex: 1 1 auto; }
    .title-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }
    .title-row h1 { margin: 0; }
    .info-btn {
      border: 1px solid var(--border);
      background: var(--surface-muted);
      color: var(--muted);
      border-radius: 999px;
      width: 28px;
      height: 28px;
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      line-height: 1;
      padding: 0;
      flex-shrink: 0;
      transition: border-color 0.15s, color 0.15s, background 0.15s;
    }
    .info-btn:hover,
    .info-btn[aria-expanded="true"] {
      border-color: var(--accent);
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 10%, var(--surface-muted));
    }
    .activity-info-popover {
      position: fixed;
      z-index: 1000;
      width: min(520px, calc(100vw - 32px));
      max-height: min(70vh, 640px);
      overflow-y: auto;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
      box-shadow: 0 16px 48px var(--shadow);
    }
    .activity-info-popover[hidden] { display: none; }
    .activity-info-popover .activity-ref-grid {
      grid-template-columns: 1fr;
      gap: 14px;
    }
    .summary-with-info {
      display: flex;
      align-items: center;
      gap: 8px;
      list-style: none;
    }
    .summary-with-info::-webkit-details-marker { display: none; }
    .summary-with-info::before {
      content: '▸ ';
      color: var(--muted);
      flex-shrink: 0;
    }
    .section-collapse[open] > summary.summary-with-info::before {
      content: '▾ ';
    }
    .summary-with-info .summary-title { flex: 1 1 auto; }
    .summary-with-info .info-btn { margin-left: auto; }
    .kpi { font-size: 1.5rem; font-weight: 700; color: var(--accent); }
    .kpi small { font-size: 0.85rem; font-weight: 400; color: var(--muted); display: block; }
    table { width: 100%; border-collapse: collapse; font-size: 0.9rem; border: 1px solid var(--border); }
    th, td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      border-right: 1px solid var(--border);
      vertical-align: middle;
    }
    tr th:last-child, tr td:last-child { border-right: none; }
    tbody tr:last-child td { border-bottom: none; }
    thead th {
      background: var(--surface-muted);
      color: var(--muted);
      font-weight: 600;
      text-align: center;
    }
    td { text-align: left; }
    td.num {
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    table:not(.summary-table) td.num { width: 1%; }
    .muted { color: var(--muted); }
    .chart-img { max-width: 100%; height: auto; display: none; margin: 0 auto; }
    canvas { max-height: 320px; }
    .no-data { color: var(--muted); font-size: 0.9rem; padding: 12px 0; }
    .badge-ok { color: var(--ok); }
    .badge-warn { color: var(--warn); }
    .detail-table { font-size: 0.82rem; }
    .day-block, .ticket-block {
      margin-bottom: 20px; padding-bottom: 12px;
      border-bottom: 1px solid var(--border);
    }
    .day-block h3 { margin: 0 0 10px; font-size: 0.95rem; }
    .ticket-head { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 4px; }
    .ticket-title { color: var(--muted); font-size: 0.88rem; margin-bottom: 8px; }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      background: var(--badge-bg); color: var(--badge-text);
      font-size: 0.75rem; font-weight: 600;
    }
    .table-scroll {
      max-width: 100%;
      /* overflow-x di wrapper memecah position:sticky pada thead — scroll horizontal via viewport */
    }
    .summary-table {
      min-width: 1080px;
    }
    .summary-table thead th {
      position: sticky;
      top: var(--sticky-offset);
      z-index: 20;
      background: var(--surface-muted);
      box-shadow: 0 1px 0 var(--border);
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      padding: 10px 12px;
      white-space: nowrap;
      text-align: center;
    }
    .summary-table tbody tr.summary-row { transition: background 0.15s; }
    .summary-table tbody tr.summary-row:hover { background: var(--surface-muted); }
    .summary-table tbody td {
      padding: 12px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
      text-align: left;
    }
    .summary-table td.col-date {
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      background: color-mix(in srgb, var(--surface-muted) 40%, transparent);
    }
    .summary-table td.col-metric {
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .summary-table td.gap-cell { font-weight: 600; }
    .summary-table td.col-status {
      white-space: nowrap;
    }
    .summary-table td.col-grouping,
    .summary-table td.col-layer { min-width: 140px; max-width: 200px; }
    .summary-table td.col-work { min-width: 300px; max-width: 480px; }
    .tag-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: flex-start; }
    .tag {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 4px 9px; border-radius: 6px;
      font-size: 0.72rem; line-height: 1.3;
      background: var(--surface-muted);
      border: 1px solid var(--border);
      color: var(--text);
    }
    .tag-grouping { border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); }
    .tag-layer { font-weight: 600; }
    .tag-hours {
      color: var(--muted);
      font-weight: 500;
      font-variant-numeric: tabular-nums;
    }
    .status-pill {
      display: inline-block;
      min-width: 52px;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-align: center;
    }
    .status-pill.ok {
      background: color-mix(in srgb, var(--ok) 18%, transparent);
      color: var(--ok);
    }
    .status-pill.warn {
      background: color-mix(in srgb, var(--warn) 18%, transparent);
      color: var(--warn);
    }
    .status-pill.off {
      background: var(--surface-muted);
      color: var(--muted);
    }
    .nowrap { white-space: nowrap; }
    .breakdown-list, .work-summary-list {
      list-style: none; margin: 0; padding: 0; font-size: 0.82rem;
    }
    .breakdown-list li {
      display: flex; justify-content: space-between; gap: 12px;
      padding: 2px 0; border-bottom: 1px dashed var(--border);
    }
    .breakdown-list li:last-child { border-bottom: none; }
    .work-summary-list .work-item {
      padding: 8px 0;
      border-bottom: 1px dashed var(--border);
    }
    .work-summary-list .work-item:last-child { border-bottom: none; }
    .work-ticket-list { padding: 0; }
    .work-ticket-group {
      padding: 10px 0;
      border-bottom: 1px dashed var(--border);
    }
    .work-ticket-group:last-child { border-bottom: none; }
    .work-ticket-key {
      display: block;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 6px;
    }
    .work-activity-list {
      list-style: none;
      margin: 0;
      padding: 0 0 0 10px;
    }
    .work-activity-list .work-item {
      padding: 6px 0;
    }
    .work-activity-list .work-item:last-child { border-bottom: none; }
    .activity-label { font-weight: 600; font-size: 0.88rem; }
    .activity-desc {
      font-size: 0.78rem;
      color: var(--muted);
      margin-top: 2px;
      line-height: 1.35;
    }
    .activity-ref-grid {
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    }
    .ref-block h3 {
      margin: 0 0 8px;
      font-size: 0.88rem;
      color: var(--accent);
    }
    .ref-block ul {
      margin: 0;
      padding-left: 18px;
      font-size: 0.82rem;
      color: var(--muted);
    }
    .ref-block li { margin-bottom: 6px; }
    .ref-block strong { color: var(--text); }
    .work-key {
      display: block;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 2px;
    }
    .work-title {
      display: block;
      font-size: 0.8rem;
      color: var(--muted);
      line-height: 1.4;
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .work-summary-wrap:not(:has(.work-summary-more[open])) .work-summary-extra-list {
      display: none;
    }
    .work-summary-more {
      margin-top: 4px;
      padding-top: 8px;
      border-top: 1px dashed var(--border);
      font-size: 0.8rem;
    }
    .work-summary-more summary {
      cursor: pointer;
      color: var(--accent);
      font-weight: 600;
      list-style: none;
      padding: 4px 0;
      user-select: none;
    }
    .work-summary-more summary .summary-less { display: none; }
    .work-summary-more[open] summary .summary-more { display: none; }
    .work-summary-more[open] summary .summary-less { display: inline; }
    .work-summary-more summary::-webkit-details-marker { display: none; }
    .work-summary-more summary::before { content: '▸ '; color: var(--muted); }
    .work-summary-more[open] summary::before { content: '▾ '; }
    .alert-banner {
      background: #fef3c7; border: 1px solid #f59e0b; color: #92400e;
      border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; font-size: 0.88rem;
    }
    [data-theme="dark"] .alert-banner {
      background: #422006; border-color: #b45309; color: #fde68a;
    }
    .alert-banner ul { margin: 8px 0 0; padding-left: 18px; }
    .compare-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
    .compare-card { background: var(--surface-muted); border-radius: 10px; padding: 12px 14px; }
    .compare-card strong { display: block; font-size: 1.1rem; margin-top: 4px; }
    .compare-delta { font-size: 0.85rem; color: var(--muted); }
    .compare-delta.positive { color: var(--ok); }
    .compare-delta.negative { color: var(--warn); }
    .status-ok { color: var(--ok); font-weight: 600; white-space: nowrap; }
    .status-warn { color: var(--warn); font-weight: 600; white-space: nowrap; }
    .excel-grouping { font-size: 0.85rem; white-space: nowrap; }
    .excel-summary { font-size: 0.88rem; line-height: 1.5; }
    .tsv-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 12px 0; }
    .tsv-actions button, .filter-reset, .theme-toggle {
      border: 1px solid var(--border); background: var(--surface); color: var(--text);
      border-radius: 8px; padding: 8px 12px; font-size: 0.85rem; cursor: pointer;
    }
    .tsv-actions button:hover, .filter-reset:hover { border-color: var(--accent); color: var(--accent); }
    .tsv-box {
      margin: 0; padding: 12px; background: var(--surface-muted); border-radius: 8px;
      font-size: 0.8rem; line-height: 1.45; white-space: pre-wrap; word-break: break-word;
      border: 1px solid var(--border); color: var(--text);
    }
    .copy-hint { font-size: 0.82rem; margin: 0 0 8px; }
    .filter-bar {
      position: sticky;
      top: 0;
      z-index: 30;
      margin-bottom: 16px;
      box-shadow: 0 4px 16px var(--shadow);
      background: var(--card);
    }
    .filter-bar:not([open]) {
      padding-bottom: 10px;
    }
    .filter-bar > summary {
      cursor: pointer;
      font-weight: 600;
      font-size: 1rem;
      margin-bottom: 0;
      list-style-position: outside;
      color: var(--text);
      user-select: none;
    }
    .filter-bar[open] > summary { margin-bottom: 12px; }
    .filter-bar > summary::-webkit-details-marker { display: none; }
    .filter-bar > summary::before {
      content: '▸ ';
      color: var(--muted);
      font-size: 0.85rem;
    }
    .filter-bar[open] > summary::before { content: '▾ '; }
    .filter-summary-hint {
      font-weight: 400;
      font-size: 0.82rem;
      color: var(--muted);
      margin-left: 6px;
    }
    .filter-bar[open] .filter-summary-hint { display: none; }
    .filter-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .filter-label {
      display: block; font-size: 0.78rem; font-weight: 600; color: var(--muted);
      margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.03em;
    }
    .chip-group { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip {
      border: 1px solid var(--border); background: var(--surface); color: var(--text);
      border-radius: 999px; padding: 4px 10px; font-size: 0.78rem; cursor: pointer;
    }
    .chip:hover { border-color: var(--accent); color: var(--accent); }
    .chip.active { background: var(--accent); color: #fff; border-color: var(--accent); }
    .filter-search, .filter-select {
      width: 100%; border: 1px solid var(--border); border-radius: 8px;
      padding: 8px 10px; font-size: 0.88rem; background: var(--input-bg); color: var(--text);
    }
    .filter-actions { display: flex; flex-direction: column; gap: 10px; justify-content: flex-end; }
    .filter-check { font-size: 0.85rem; display: flex; align-items: center; gap: 8px; color: var(--text); }
    .filter-meta { margin: 12px 0 0; font-size: 0.85rem; color: var(--muted); }
    .quick-filters { display: flex; gap: 8px; flex-wrap: wrap; }
    .filter-select { flex: 1; min-width: 140px; font-size: 0.85rem; }
    .report-nav { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
    .report-nav a {
      text-decoration: none; color: var(--text); border: 1px solid var(--border);
      border-radius: 999px; padding: 6px 12px; font-size: 0.82rem; background: var(--surface);
    }
    .report-nav a:hover { border-color: var(--accent); color: var(--accent); }
    .section-collapse > summary {
      cursor: pointer; font-weight: 600; font-size: 1rem; margin-bottom: 12px;
      list-style-position: outside; color: var(--text);
    }
    .section-collapse[open] > summary { margin-bottom: 12px; }
    .day-block > summary, .ticket-block > summary { list-style: none; color: var(--text); }
    .day-block > summary::-webkit-details-marker, .ticket-block > summary::-webkit-details-marker { display: none; }
    .day-block > summary::before, .ticket-block > summary::before {
      content: '▸ '; color: var(--muted);
    }
    .day-block[open] > summary::before, .ticket-block[open] > summary::before {
      content: '▾ ';
    }
    [hidden] { display: none !important; }
    @media print {
      body { background: #fff; color: #000; }
      .card { break-inside: avoid; }
      .no-print { display: none; }
      canvas { display: none !important; }
      .chart-fallback { display: block !important; }
    }
    """


def _warnings_banner_html(report: ReportData) -> str:
    if not report.warnings:
        return ""
    items = "".join(f"<li>{html.escape(w)}</li>" for w in report.warnings)
    return f'<div class="alert-banner no-print"><strong>Peringatan data</strong><ul>{items}</ul></div>'


def _period_comparison_html(report: ReportData) -> str:
    comp = report.period_comparison
    if not comp:
        return ""
    pct = comp.hours_pct
    if pct is None:
        delta_class = "compare-delta"
        delta_text = format_gap(comp.hours_delta)
    else:
        delta_class = "compare-delta positive" if pct >= 0 else "compare-delta negative"
        sign = "+" if pct >= 0 else ""
        delta_text = f"{format_gap(comp.hours_delta)} ({sign}{pct:.1f}%)"
    return f"""
    <div class="card" style="margin-bottom:16px">
      <h2>Perbandingan Periode</h2>
      <div class="compare-grid">
        <div class="compare-card">
          <span class="muted">{html.escape(comp.current_label)}</span>
          <strong>{html.escape(format_hours(comp.current_hours))}</strong>
          <span class="muted">{comp.current_days} hari aktif</span>
        </div>
        <div class="compare-card">
          <span class="muted">{html.escape(comp.previous_label)}</span>
          <strong>{html.escape(format_hours(comp.previous_hours))}</strong>
          <span class="muted">{comp.previous_days} hari aktif</span>
        </div>
        <div class="compare-card">
          <span class="muted">Selisih jam</span>
          <strong class="{delta_class.split()[0]}">{html.escape(delta_text)}</strong>
          <span class="{delta_class}">SP DONE: {comp.current_sp_done:g} vs {comp.previous_sp_done:g}</span>
        </div>
      </div>
    </div>"""


def _team_section_html(report: ReportData) -> str:
    if len(report.person_summaries) < 2:
        return ""
    rows = [
        (
            p.name,
            format_hours(p.total_hours),
            str(p.days_active),
            str(p.done_count),
            str(p.entry_count),
        )
        for p in report.person_summaries
    ]
    body = _table_rows(rows)
    return f"""
    <div id="section-team" class="card" style="margin-bottom:16px">
      <h2>Ringkasan Tim ({len(report.person_summaries)} orang)</h2>
      <table>
        <thead><tr><th>Nama</th><th class="num">Jam</th><th class="num">Hari</th><th class="num">DONE</th><th class="num">Entri</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


def _sp_layer_section_html(report: ReportData) -> str:
    layers = report.sp_metrics.by_layer
    if not layers:
        return ""
    rows = [
        (
            m.layer,
            format_hours(m.hours),
            f"{m.story_points:g} SP" if m.story_points else "-",
            format_hours_per_sp(m.hours_per_sp),
        )
        for m in layers
    ]
    return f"""
    <div class="card" style="margin-bottom:16px">
      <h2>Jam / SP per Layer</h2>
      <table>
        <thead><tr><th>Layer</th><th class="num">Jam</th><th class="num">SP</th><th class="num">Jam/SP</th></tr></thead>
        <tbody>{_table_rows(rows)}</tbody>
      </table>
    </div>"""


def render_html(report: ReportData) -> str:
    top_issues = list(report.by_issue.items())[:15]
    issue_rows = []
    for key, hours in top_issues:
        title = report.issue_meta[key].ticket
        if len(title) > 60:
            title = title[:57] + "..."
        issue_rows.append((f"{key} — {title}", format_hours(hours)))

    done_rows = []
    for key in sorted(report.done_keys):
        meta = report.issue_meta[key]
        hrs = sum(e.time_spent for e in report.entries if e.issue_key == key)
        sp = f"SP {meta.story_points:g}" if meta.story_points else "SP -"
        done_rows.append((f"{key} ({sp})", format_hours(hrs)))

    sp_rows = [
        (
            f"{m.issue_key} — SP {m.story_points:g}",
            format_hours(m.hours),
            format_hours_per_sp(m.hours_per_sp),
        )
        for m in report.sp_metrics.per_ticket
    ]

    velocity_rows = [
        (
            v.week,
            f"{v.sp_done:g} SP",
            f"{format_hours(v.hours)} · {v.done_count} tiket",
        )
        for v in report.sp_metrics.weekly_velocity
    ]

    daily_b64 = _daily_target_chart(report)
    ticket_summary_html = _ticket_summary_html(report)
    daily_detail_html = _daily_detail_html(report)
    report_nav = _report_nav_html()
    grouping_b64 = _pie_chart(
        [format_grouping_title_case(k) for k in report.by_grouping.keys()],
        list(report.by_grouping.values()),
        "Grouping Activity",
    )
    layer_b64 = _pie_chart(
        list(report.by_layer.keys()),
        list(report.by_layer.values()),
        "Layer (FE / BE / Genesis)",
    )
    activity_b64 = _pie_chart(
        [format_activity_display(k) for k in report.by_activity.keys()],
        list(report.by_activity.values()),
        "Activity",
    )

    chart_data = _chart_js_config(report)
    filter_data = _build_filter_data(report)
    filter_bar = _filter_bar_html(report)
    warnings_banner = _warnings_banner_html(report)
    period_comparison_html = _period_comparison_html(report)
    team_section_html = _team_section_html(report)
    sp_layer_section_html = _sp_layer_section_html(report)
    target_summary = target_summary_label(report)
    workdays_note = " · hanya hari kerja" if report.workdays_only else ""
    sp_efficiency = format_hours_per_sp(report.sp_metrics.hours_per_sp)

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Work Log — {html.escape(report.period_label)}</title>
  <script>{_theme_init_script()}</script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    {_report_styles()}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="page-header">
      <div>
        <div class="title-row">
          <h1>Work Log Analysis</h1>
          {_activity_info_btn_html()}
        </div>
        <p class="subtitle">{html.escape(report.period_label)} · {html.escape(", ".join(report.names))}{html.escape(workdays_note)}</p>
      </div>
      <button type="button" id="themeToggle" class="theme-toggle no-print" aria-label="Toggle dark mode">🌙 Dark mode</button>
    </div>

    {filter_bar}
    {report_nav}

    {warnings_banner}
    {period_comparison_html}

    <div id="section-kpi" class="grid grid-3" style="margin-bottom:16px">
      <div class="card">
        <h2>Total Jam</h2>
        <div class="kpi" id="kpiTotalHours">{html.escape(format_hours(report.total_hours))}</div>
      </div>
      <div class="card">
        <h2>Target Harian</h2>
        <div class="kpi" id="kpiTarget">{report.target_hours:g} jam<small>{html.escape(target_summary)}</small></div>
      </div>
      <div class="card">
        <h2>Rata-rata / Hari</h2>
        <div class="kpi" id="kpiAvgHours">{html.escape(format_hours(report.avg_hours_per_day))}<small>{len(report.entries)} entri total</small></div>
      </div>
      <div class="card">
        <h2>Efisiensi SP</h2>
        <div class="kpi" id="kpiSpEfficiency">{html.escape(sp_efficiency)}<small>{report.sp_metrics.total_sp:g} SP · {html.escape(format_hours(report.sp_metrics.hours_on_sp_tickets))}</small></div>
      </div>
    </div>

    <details id="section-activity-ref" class="card section-collapse" open style="margin-bottom:16px">
      <summary class="summary-with-info">
        <span class="summary-title">Referensi Jenis Activity</span>
        {_activity_info_btn_html()}
      </summary>
      <p class="muted copy-hint">Standar label activity tim — dipakai untuk mengklasifikasi dan menampilkan tooltip di laporan.</p>
      <div class="activity-ref-grid">{_activity_reference_html()}</div>
    </details>

    <details id="section-summary" class="card section-collapse" open style="margin-bottom:16px">
      <summary class="summary-with-info">
        <span class="summary-title">Ringkasan per Hari &amp; Excel</span>
        {_activity_info_btn_html()}
      </summary>
      <p class="muted copy-hint">Grouping ascending, ringkasan kerja tanpa jam — siap paste ke Excel.</p>
      <div class="table-scroll">
        <table class="summary-table">
          <colgroup>
            <col class="col-date">
            <col class="col-grouping">
            <col class="col-work">
            <col class="col-metric" span="4">
            <col class="col-layer">
            <col class="col-status">
          </colgroup>
          <thead>
            <tr>
              <th>Tanggal</th>
              <th>Grouping</th>
              <th class="col-work">Ringkasan Kerja</th>
              <th class="num">Jam</th>
              <th class="num">vs Target</th>
              <th class="num">Entri</th>
              <th class="num">DONE</th>
              <th>Layer</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="dailySummaryBody">{_daily_compare_rows(report)}</tbody>
        </table>
      </div>
      <div class="tsv-actions no-print">
        <button type="button" id="copyTsvBtn">Salin TSV (A:C)</button>
        <button type="button" id="copyGroupingBtn">Salin grouping saja (kolom B)</button>
        <button type="button" id="copySummaryBtn">Salin ringkasan saja (kolom C)</button>
      </div>
    </details>

    {team_section_html}

    <div id="section-charts">
      <div class="grid grid-2" style="margin-bottom:16px">
        <div class="card">
          <h2>Jam Harian vs Target</h2>
          <p class="muted copy-hint">Klik bar untuk filter tanggal.</p>
          <canvas id="dailyChart"></canvas>
          {_chart_img(daily_b64, "Jam harian")}
        </div>
        <div class="card">
          <h2>Layer</h2>
          <canvas id="layerChart"></canvas>
          {_chart_img(layer_b64, "Layer")}
        </div>
      </div>
      <div class="grid grid-2" style="margin-bottom:16px">
        <div class="card">
          <h2>Grouping Activity</h2>
          <canvas id="groupingChart"></canvas>
          {_chart_img(grouping_b64, "Grouping activity")}
        </div>
        <div class="card">
          <div class="card-heading">
            <h2>Activity</h2>
            {_activity_info_btn_html()}
          </div>
          <p class="muted copy-hint">Klik ⓘ untuk referensi lengkap. Hover label untuk penjelasan singkat.</p>
          <canvas id="activityChart"></canvas>
          {_chart_img(activity_b64, "Activity")}
          <table style="margin-top:12px">
            <thead><tr><th>Activity</th><th class="num">Jam</th></tr></thead>
            <tbody id="activityTableBody">{_activity_table_rows_html(report.by_activity)}</tbody>
          </table>
          <h3 style="margin:16px 0 8px;font-size:0.92rem">Kategori Activity</h3>
          <table>
            <thead><tr><th>Kategori</th><th class="num">Jam</th></tr></thead>
            <tbody id="activityCategoryBody">{_table_rows([(k, format_hours(v)) for k, v in report.by_activity_category.items()])}</tbody>
          </table>
        </div>
      </div>
      {sp_layer_section_html}
      <div class="card" style="margin-bottom:16px">
        <h2>Top Tiket</h2>
        <table>
          <thead><tr><th>Tiket</th><th class="num">Jam</th></tr></thead>
          <tbody id="topTicketBody">{_table_rows(issue_rows)}</tbody>
        </table>
      </div>
    </div>

    <details id="section-tickets" class="card section-collapse" style="margin-bottom:16px">
      <summary id="ticketSectionTitle">Ringkasan per Tiket ({len(report.ticket_summaries)})</summary>
      {ticket_summary_html}
    </details>

    <details id="section-detail" class="card section-collapse" style="margin-bottom:16px">
      <summary id="detailSectionTitle">Detail Work Log ({len(report.entries)} entri)</summary>
      {daily_detail_html}
    </details>

    <div id="section-done" class="grid grid-2">
      <div class="card">
        <h2 id="doneSectionTitle">Tiket DONE ({len(report.done_keys)})</h2>
        <table>
          <thead><tr><th>Tiket</th><th class="num">Jam</th></tr></thead>
          <tbody id="doneTableBody">{_table_rows(done_rows)}</tbody>
        </table>
      </div>
      <div class="card">
        <h2 id="spSectionTitle">Story Points ({len(report.sp_touched)} tiket, total {sum(report.sp_touched.values()):g} SP)</h2>
        <table>
          <thead><tr><th>Tiket</th><th class="num">Jam</th><th class="num">Jam/SP</th></tr></thead>
          <tbody id="spTableBody">{_table_rows(sp_rows)}</tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h2>Velocity SP (tiket DONE per minggu)</h2>
      <table>
        <thead><tr><th>Minggu</th><th class="num">SP</th><th>Jam · Tiket</th></tr></thead>
        <tbody id="spVelocityBody">{_table_rows(velocity_rows)}</tbody>
      </table>
    </div>
  </div>

  {_activity_info_popover_html()}

  <script id="filterData" type="application/json">{filter_data}</script>
  <script>
    const DATA = {chart_data};

    function copyText(text, button) {{
      if (!text) return;
      navigator.clipboard.writeText(text).then(() => {{
        const original = button.textContent;
        button.textContent = 'Tersalin!';
        setTimeout(() => {{ button.textContent = original; }}, 1500);
      }});
    }}

    function showChartFallbacks() {{
      document.querySelectorAll('.chart-fallback').forEach(el => {{
        el.style.display = 'block';
      }});
      document.querySelectorAll('canvas').forEach(el => {{
        el.style.display = 'none';
      }});
    }}

    function makeBarChart(canvasId, labels, values, extraDataset) {{
      const ctx = document.getElementById(canvasId);
      if (!ctx || typeof Chart === 'undefined') return;
      if (!labels.length) return;
      const datasets = [{{
        label: 'Jam',
        data: values,
        backgroundColor: DATA.colors.slice(0, labels.length),
      }}];
      if (extraDataset) datasets.push(extraDataset);
      new Chart(ctx, {{
        type: 'bar',
        data: {{ labels, datasets }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ display: !!extraDataset }} }},
          scales: {{ y: {{ beginAtZero: true }} }}
        }}
      }});
    }}

    function makeDoughnut(canvasId, labels, values) {{
      const ctx = document.getElementById(canvasId);
      if (!ctx || typeof Chart === 'undefined') return;
      if (!labels.length || !values.some(v => v > 0)) return;
      new Chart(ctx, {{
        type: 'doughnut',
        data: {{
          labels,
          datasets: [{{
            data: values,
            backgroundColor: DATA.colors.slice(0, labels.length),
          }}]
        }},
        options: {{ responsive: true }}
      }});
    }}

    if (typeof Chart === 'undefined') {{
      showChartFallbacks();
    }}
  </script>
  <script>{_interactive_filter_script()}</script>
</body>
</html>"""


def export_html(report: ReportData, path: Path) -> None:
    path.write_text(render_html(report), encoding="utf-8")


def _pdf_safe(text: str) -> str:
    replacements = {
        "—": "-",
        "–": "-",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "✓": "OK",
        "✗": "X",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("ascii", "replace").decode("ascii")


def _pdf_next_line(pdf, h: float = 0) -> None:
    from fpdf.enums import XPos, YPos

    pdf.ln(h)
    pdf.set_x(pdf.l_margin)


def _export_pdf_fpdf(report: ReportData, path: Path) -> None:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Work Log Analysis", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=11)
    names = ", ".join(report.names) if report.names else "-"
    pdf.cell(
        0,
        8,
        _pdf_safe(f"{report.period_label} | {names}"),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    _pdf_next_line(pdf, 4)

    if not report.entries:
        pdf.set_font("Helvetica", size=10)
        pdf.cell(
            0,
            6,
            _pdf_safe("Tidak ada entri work log untuk periode ini."),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.output(str(path))
        return

    target_summary = target_summary_label(report)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Ringkasan", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=10)
    sp_line = format_hours_per_sp(report.sp_metrics.hours_per_sp)
    for line in [
        f"Total jam: {format_hours(report.total_hours)}",
        f"Target/hari: {report.target_hours:g} jam ({target_summary})",
        f"Rata-rata/hari: {format_hours(report.avg_hours_per_day)}",
        f"Entri: {len(report.entries)} | DONE: {len(report.done_keys)} tiket",
        f"Efisiensi SP: {sp_line} ({report.sp_metrics.total_sp:g} SP)",
    ]:
        pdf.cell(0, 6, _pdf_safe(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    _pdf_next_line(pdf, 4)

    if report.daily_summaries:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Ringkasan per Hari", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=8)
        for day in report.daily_summaries:
            eligible = (not report.workdays_only) or is_workday(day.day)
            if not eligible:
                status = "libur"
            else:
                status = "OK" if day.target_met else "kurang"
            pdf.cell(
                0,
                5,
                _pdf_safe(
                    f"{day.day}  {day.total_hours:.2f}j  ({format_gap(day.gap_hours)})  "
                    f"{day.entry_count} entri  {day.done_count} DONE  "
                    f"{format_grouping_breakdown(day.by_grouping)}  [{status}]"
                ),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        _pdf_next_line(pdf, 4)

    def add_chart(title: str, labels: list[str], values: list[float], pie: bool = False) -> None:
        b64 = (
            _pie_chart(labels, values, title)
            if pie
            else _chart_png_base64(labels, values, title)
        )
        if not b64:
            return
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(base64.b64decode(b64))
            tmp_path = tmp.name
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, _pdf_safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.image(tmp_path, w=170)
        _pdf_next_line(pdf, 4)
        Path(tmp_path).unlink(missing_ok=True)

    add_chart(
        f"Jam Harian vs Target ({report.target_hours:g}j)",
        [d.day for d in report.daily_summaries],
        [d.total_hours for d in report.daily_summaries],
    )
    add_chart(
        "Grouping Activity",
        list(report.by_grouping.keys()),
        list(report.by_grouping.values()),
        pie=True,
    )
    add_chart(
        "Layer",
        list(report.by_layer.keys()),
        list(report.by_layer.values()),
        pie=True,
    )

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Ringkasan per Tiket", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=8)
    for ticket in report.ticket_summaries[:15]:
        done = "DONE" if ticket.is_done else "-"
        pdf.cell(
            0,
            5,
            _pdf_safe(
                f"{ticket.issue_key} [{ticket.layer}] {done} "
                f"{ticket.total_hours:.2f}j - {ticket.ticket[:65]}"
            ),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        for line in ticket.lines:
            pdf.cell(
                0,
                4,
                _pdf_safe(
                    f"  {line.date.strftime('%m-%d %H:%M')}  "
                    f"{format_time_spent(line.time_spent)}j  "
                    f"{line.grouping_activity}  {line.activity}"
                ),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        _pdf_next_line(pdf, 2)

    pdf.output(str(path))


def export_pdf(report: ReportData, path: Path) -> None:
    import sys

    if sys.platform != "darwin":
        try:
            from weasyprint import HTML

            HTML(string=render_html(report)).write_pdf(str(path))
            return
        except Exception:
            pass

    try:
        _export_pdf_fpdf(report, path)
    except ImportError as exc:
        raise SystemExit(
            "PDF export membutuhkan fpdf2 atau weasyprint. Install: pip install fpdf2"
        ) from exc
