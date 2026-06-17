#!/usr/bin/env python3
"""Unit & integration tests untuk work log analysis."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from export_report import export_html, export_pdf, render_html
from worklog_lib import (
    build_report,
    detect_ticket_layer,
    filter_entries,
    format_day_grouping_field,
    format_day_excel_field,
    format_hours,
    format_hours_per_sp,
    format_grouping_title_case,
    format_worklog_summary_line,
    is_workday,
    is_worklog_ticket,
    load_all_csv,
    load_entries,
    normalize_activity,
    parse_date,
    render_excel_tsv,
    sum_hours,
    target_summary_label,
)

CSV = Path(__file__).parent / "tests" / "fixtures" / "worklog_sample.csv"
CSV_WORKLOG = Path(__file__).parent / "tests" / "fixtures" / "worklog_worklog_sample.csv"
SCRIPT = Path(__file__).parent / "analyze_worklog.py"

SAMPLE_ROW = (
    '"Demo User","https://example.atlassian.net/browse/DEMO-99",'
    '"[BE][test] Sample ticket","1.5","DETAILING","[DETAILING]",'
    '"2026-06-10T09:00:00.000+0700","2"\n'
)
HEADER = (
    "Name,Issue,Ticket,Time Spent,Grouping Activity,Activity,Date,SP\n"
)


class TestWorklogLib(unittest.TestCase):
    def test_load_sample_csv(self):
        entries = load_entries(CSV)
        self.assertEqual(len(entries), 34)
        self.assertAlmostEqual(sum_hours(entries), 8.95, places=2)

    def test_normalize_activity(self):
        self.assertEqual(normalize_activity("[**DETAILING**]"), "DETAILING")
        self.assertEqual(normalize_activity("[DONE]"), "DONE")

    def test_detect_layer(self):
        self.assertEqual(detect_ticket_layer("[FE][Clients] foo"), "FE")
        self.assertEqual(detect_ticket_layer("[BE][horde] bar"), "BE")
        self.assertEqual(
            detect_ticket_layer("[Genesis] - Enhancement"), "Genesis"
        )

    def test_parse_date_offset(self):
        dt = parse_date("2026-06-11T14:18:52.142+0700")
        self.assertEqual(dt.strftime("%Y-%m-%d"), "2026-06-11")
        dt_z = parse_date("2026-06-11T09:00:00.000Z")
        self.assertEqual(dt_z.strftime("%Y-%m-%d"), "2026-06-11")

    def test_load_csv_with_bom(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write(HEADER + SAMPLE_ROW)
            bom_path = Path(f.name)
        try:
            entries = load_entries(bom_path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].issue_key, "DEMO-99")
        finally:
            bom_path.unlink()

    def test_filter_day(self):
        entries = load_entries(CSV)
        filtered = filter_entries(entries, day="2026-06-11")
        self.assertEqual(len(filtered), 34)

    def test_filter_empty_day(self):
        entries = load_entries(CSV)
        filtered = filter_entries(entries, day="2025-01-01")
        self.assertEqual(len(filtered), 0)

    def test_week_filter(self):
        entries = load_entries(CSV)
        week = entries[0].iso_week
        filtered = filter_entries(entries, week=week)
        self.assertEqual(len(filtered), 34)

    def test_format_hours_zero(self):
        self.assertIn("0.00 jam", format_hours(0.0))

    def test_excel_paste_field(self):
        report = build_report(load_entries(CSV), "test", target_hours=8)
        day = report.daily_summaries[0]
        summary = format_day_excel_field(day, "summary")
        self.assertIn("[Detailing]", summary)
        self.assertIn(";", summary)
        self.assertNotRegex(summary, r"\(\d+\.\d+j\)")
        self.assertRegex(day.work_summary_lines[0], r"\([\d.]+j\)$")
        hours = format_day_excel_field(day, "hours")
        self.assertIn("8.95", hours)
        grouping = format_day_grouping_field(day)
        self.assertIn("Detailing", grouping)
        self.assertIn("Operation", grouping)
        tsv = render_excel_tsv(report, "summary", with_date=True)
        self.assertIn("2026-06-11\t", tsv)
        self.assertIn("Detailing", tsv)
        self.assertIn("[Detailing]", tsv)
        tsv_summary_only = render_excel_tsv(report, "summary", with_date=False)
        self.assertIn("[Detailing]", tsv_summary_only)
        self.assertNotIn("\t", tsv_summary_only)

    def test_activity_kind(self):
        from activity_reference import (
            activity_reference_sections,
            canonical_activity_kind,
            lookup_activity_info,
        )
        from worklog_lib import activity_kind_from_entry, activity_kind_from_raw, load_entries

        self.assertEqual(canonical_activity_kind("GROOMING-2"), "GROOMING")
        self.assertEqual(canonical_activity_kind("REVISI"), "REVISIT")
        self.assertEqual(
            activity_kind_from_raw("[CODING] feat foo"),
            "CODING",
        )
        self.assertEqual(activity_kind_from_raw("[Meeting]"), "MEETING")
        info = lookup_activity_info("CHECK EXISTING FEATURE")
        self.assertIsNotNone(info)
        self.assertIn("features", info.description.lower())
        sections = activity_reference_sections()
        self.assertEqual(sections[0].title, "Work Log Epic")
        self.assertEqual(sections[1].title, "Work Log Ticket")
        self.assertEqual(sections[2].title, "Additional Task (PTD)")
        self.assertEqual(sections[0].items[0].label, "GROOMING-n")
        entries = load_entries(CSV_WORKLOG)
        kinds = {activity_kind_from_entry(e) for e in entries}
        self.assertIn("CODING", kinds)
        self.assertIn("DISCUSSION", kinds)
        report = build_report(entries, "worklog", target_hours=8)
        self.assertIn("CODING", report.by_activity)
        self.assertIn("Development", report.by_activity_category)
        self.assertIn("Kolaborasi", report.by_activity_category)

    def test_grouping_title_case(self):
        self.assertEqual(
            format_grouping_title_case("ANALYSIS & BREAKDOWN"),
            "Analysis & Breakdown",
        )
        self.assertEqual(format_grouping_title_case("DETAILING"), "Detailing")
        report = build_report(load_entries(CSV), "test", target_hours=8)
        day = report.daily_summaries[0]
        grouping = format_day_grouping_field(day)
        self.assertIn("Detailing", grouping)
        self.assertNotIn("DETAILING", grouping)

    def test_work_summary_by_ticket(self):
        entries = load_entries(CSV_WORKLOG)
        report = build_report(entries, "worklog", target_hours=8)
        day = report.daily_summaries[0]
        self.assertEqual(len(day.work_summary_tickets), 2)
        self.assertEqual(day.work_summary_tickets[0].issue_key, "DEMO-2716")
        self.assertGreater(len(day.work_summary_tickets[0].lines), 1)
        summary = format_day_excel_field(day, "summary")
        self.assertIn("DEMO-2716:", summary)
        self.assertIn("[Coding]", summary)
        self.assertIn("DEMO-2714:", summary)
        self.assertNotIn("Worklog 12 Juni 2025", summary)

    def test_worklog_summary_line(self):
        self.assertTrue(is_worklog_ticket("Worklog 12 Juni 2025"))
        self.assertFalse(is_worklog_ticket("[BE][horde] foo"))
        entries = load_entries(CSV_WORKLOG)
        self.assertEqual(len(entries), 4)
        worklog_entry = next(e for e in entries if "[DISCUSSION]" in e.activity)
        self.assertEqual(
            format_worklog_summary_line(worklog_entry),
            "[Discussion] Check and support development terkait flow register sender dashboard",
        )
        meeting_entry = next(e for e in entries if e.issue_key == "DEMO-2714")
        self.assertEqual(
            format_worklog_summary_line(meeting_entry),
            "[Meeting] Release genesis 11 juni 2026",
        )
        report = build_report(entries, "worklog", target_hours=8)
        summary = format_day_excel_field(report.daily_summaries[0], "summary")
        self.assertIn("[Check Existing Feature]", summary)
        self.assertIn("[Coding]", summary)
        self.assertNotIn("Worklog 12 Juni 2025", summary)

    def test_build_report_done_count(self):
        report = build_report(load_entries(CSV), "test", target_hours=8)
        self.assertEqual(len(report.done_keys), 14)
        self.assertTrue(report.daily_summaries[0].target_met)
        self.assertEqual(len(report.ticket_summaries), len(report.issue_meta))
        self.assertEqual(sum(len(v) for v in report.entries_by_day.values()), 34)
        self.assertTrue(report.daily_summaries[0].work_summary_lines)
        joined = "; ".join(report.daily_summaries[0].work_summary_lines)
        self.assertIn("[Detailing]", joined)
        self.assertGreater(report.sp_metrics.total_sp, 0)
        self.assertIsNotNone(report.sp_metrics.hours_per_sp)

    def test_workdays_only(self):
        report = build_report(
            load_entries(CSV), "test", target_hours=8, workdays_only=True
        )
        self.assertEqual(report.target_day_count, 1)
        self.assertIn("(hari kerja)", target_summary_label(report))

    def test_is_workday(self):
        self.assertTrue(is_workday("2026-06-11"))  # Thursday, bukan libur
        self.assertFalse(is_workday("2026-06-13"))  # Saturday
        self.assertFalse(is_workday("2026-01-01"))  # Tahun Baru (libur nasional)

    def test_load_holidays_from_library(self):
        from worklog_lib import is_public_holiday, load_holidays

        ids = load_holidays(min_year=2026, max_year=2026)
        self.assertIn("2026-01-01", ids)
        self.assertTrue(is_public_holiday("2026-01-01", ids))
        self.assertFalse(is_public_holiday("2026-06-11", ids))

    def test_filter_name(self):
        entries = load_entries(CSV)
        name = entries[0].name
        filtered = filter_entries(entries, name=name)
        self.assertEqual(len(filtered), 34)
        self.assertEqual(filter_entries(entries, name="nobody"), [])

    def test_entry_dedup(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(HEADER + SAMPLE_ROW)
            extra_path = Path(f.name)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(HEADER + SAMPLE_ROW + SAMPLE_ROW)
            dup_path = Path(f.name)
        try:
            single = load_entries(CSV)
            merged, skipped = load_all_csv([CSV, extra_path])
            self.assertEqual(len(merged), len(single) + 1)
            self.assertEqual(skipped, 0)

            merged_dup, skipped_dup = load_all_csv([dup_path])
            self.assertEqual(len(merged_dup), 1)
            self.assertEqual(skipped_dup, 1)

            merged_both, skipped_both = load_all_csv([CSV, dup_path])
            self.assertEqual(len(merged_both), len(single) + 1)
            self.assertEqual(skipped_both, 1)
        finally:
            extra_path.unlink()
            dup_path.unlink()

    def test_sp_metrics_velocity(self):
        report = build_report(load_entries(CSV), "test", target_hours=8)
        self.assertTrue(report.sp_metrics.per_ticket)
        self.assertEqual(
            format_hours_per_sp(report.sp_metrics.hours_per_sp),
            f"{report.sp_metrics.hours_per_sp:.2f} jam/SP",
        )

    def test_empty_report(self):
        report = build_report([], "kosong", target_hours=8)
        self.assertEqual(report.total_hours, 0.0)
        self.assertEqual(report.avg_hours_per_day, 0.0)
        self.assertEqual(len(report.daily_summaries), 0)


class TestExport(unittest.TestCase):
    def test_render_html_nonempty(self):
        report = build_report(load_entries(CSV), "test", target_hours=8)
        html_out = render_html(report)
        self.assertIn("Work Log Analysis", html_out)
        self.assertIn("8.95", html_out)
        self.assertIn("dailyChart", html_out)
        self.assertIn("Detail Work Log", html_out)
        self.assertIn("Ringkasan per Tiket", html_out)
        self.assertIn("Ringkasan per Hari", html_out)
        self.assertIn("Ringkasan Kerja", html_out)
        self.assertIn("Ringkasan per Hari &amp; Excel", html_out)
        self.assertIn("Filter Laporan", html_out)
        self.assertIn("filterData", html_out)
        self.assertIn("report-nav", html_out)
        self.assertIn("filterWeek", html_out)
        self.assertIn("copyTsvBtn", html_out)
        self.assertIn("copyGroupingBtn", html_out)
        self.assertIn("topTicketBody", html_out)
        self.assertIn("doneTableBody", html_out)
        self.assertIn("spTableBody", html_out)
        self.assertIn("<th>Grouping</th>", html_out)
        self.assertIn("[Detailing]", html_out)
        self.assertNotRegex(html_out, r"CORB-69 \(1\.50j\)")
        self.assertIn("section-tickets", html_out)
        self.assertIn("section-detail", html_out)
        self.assertIn("kpiSpEfficiency", html_out)
        self.assertIn("spVelocityBody", html_out)
        self.assertIn("Jam/SP", html_out)
        self.assertIn("Velocity SP", html_out)
        self.assertIn("themeToggle", html_out)
        self.assertIn("worklog-theme", html_out)
        self.assertIn("activity-ref-grid", html_out)
        self.assertIn("Referensi Jenis Activity", html_out)
        self.assertIn("Work Log Epic", html_out)
        self.assertIn("Work Log Ticket", html_out)
        self.assertIn("Additional Task (PTD)", html_out)
        self.assertIn("GROOMING-n", html_out)
        self.assertIn("activityInfoPopover", html_out)
        self.assertIn("data-activity-info-btn", html_out)
        self.assertIn("activityCategoryBody", html_out)
        self.assertIn("Lihat", html_out)
        self.assertIn('filterNames', html_out)

    def test_work_summary_split(self):
        report = build_report(load_entries(CSV), "test", target_hours=8)
        day = report.daily_summaries[0]
        total = len(day.work_summary_plain_visible) + len(day.work_summary_plain_hidden)
        self.assertGreater(total, len(day.work_summary_plain_visible))
        self.assertNotIn("tiket lainnya", "; ".join(day.work_summary_plain_visible))

    def test_filter_name_partial(self):
        entries = load_entries(CSV)
        self.assertEqual(len(filter_entries(entries, name="demo")), 34)
        self.assertEqual(len(filter_entries(entries, name="User")), 34)

    def test_date_range_filter(self):
        entries = load_entries(CSV)
        filtered = filter_entries(
            entries, date_from="2026-06-11", date_to="2026-06-11"
        )
        self.assertEqual(len(filtered), 34)

    def test_invalid_csv_header(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write("Name,Issue\n")
            bad = Path(f.name)
        try:
            with self.assertRaises(ValueError):
                load_entries(bad)
        finally:
            bad.unlink()

    def test_render_html_empty(self):
        report = build_report([], "kosong", target_hours=8)
        html = render_html(report)
        self.assertIn("Work Log Analysis", html)
        self.assertIn("tidak ada data harian", html)

    def test_export_files(self):
        report = build_report(load_entries(CSV), "test", target_hours=8)
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "r.html"
            pdf_path = Path(tmp) / "r.pdf"
            export_html(report, html_path)
            self.assertTrue(html_path.stat().st_size > 1000)
            try:
                export_pdf(report, pdf_path)
                self.assertTrue(pdf_path.stat().st_size > 1000)
            except SystemExit:
                self.skipTest("fpdf2 tidak terinstall")


class TestCLI(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=SCRIPT.parent,
        )

    def test_basic_analysis(self):
        r = self.run_cli(str(CSV), "--terminal", "--no-export-html")
        self.assertEqual(r.returncode, 0)
        self.assertIn("8.95 jam", r.stdout)

    def test_filter_empty_date(self):
        r = self.run_cli(str(CSV), "-d", "2025-01-01", "--terminal", "--no-export-html")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Tidak ada entri", r.stdout)

    def test_default_exports_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "laporan.html"
            r = self.run_cli(str(CSV), "--export-html", str(out), "-q", "--no-open")
            self.assertEqual(r.returncode, 0)
            self.assertIn("HTML disimpan", r.stderr)
            self.assertTrue(out.exists())
            self.assertIn("Work Log Analysis", out.read_text(encoding="utf-8"))

    def test_no_export_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            default_path = Path(tmp) / "laporan.html"
            r = subprocess.run(
                [sys.executable, str(SCRIPT), str(CSV), "--no-export-html", "-q"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("HTML disimpan", r.stderr)
            self.assertFalse(default_path.exists())

    def test_list_dates(self):
        r = self.run_cli(str(CSV), "--list-dates")
        self.assertEqual(r.returncode, 0)
        self.assertIn("2026-06-11", r.stdout)

    def test_export_html_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.html"
            r = self.run_cli(str(CSV), "--export-html", str(out), "-q", "--no-open")
            self.assertEqual(r.returncode, 0)
            self.assertTrue(out.exists())

    def test_paste_flag(self):
        r = self.run_cli(str(CSV), "--paste")
        self.assertEqual(r.returncode, 0)
        self.assertIn("[Detailing]", r.stdout)
        self.assertNotIn("Ringkasan work log", r.stdout)

    def test_export_excel_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "paste.tsv"
            r = self.run_cli(str(CSV), "--export-excel", str(out), "-q")
            self.assertEqual(r.returncode, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("2026-06-11\t", text)
            self.assertIn("[Detailing]", text)

    def test_merge_multi_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            day2 = Path(tmp) / "export-day2.csv"
            day2.write_text(HEADER + SAMPLE_ROW, encoding="utf-8")
            r = self.run_cli(str(CSV), str(day2), "--compare", "--terminal", "--no-export-html")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Perbandingan antar hari", r.stdout)
            self.assertIn("2026-06-10", r.stdout)
            self.assertIn("2026-06-11", r.stdout)

    def test_auto_detect_multiple_csv(self):
        data_dir = SCRIPT.parent / "data"
        day2 = data_dir / "export-test-temp-day2.csv"
        day2.write_text(HEADER + SAMPLE_ROW, encoding="utf-8")
        try:
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--compare", "--terminal", "--no-export-html"],
                capture_output=True,
                text=True,
                cwd=SCRIPT.parent,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Menggabungkan", r.stderr)
            self.assertIn("Perbandingan antar hari", r.stdout)
        finally:
            day2.unlink(missing_ok=True)

    def test_list_names(self):
        r = self.run_cli(str(CSV), "--list-names")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Demo", r.stdout)

    def test_workdays_only_cli(self):
        r = self.run_cli(str(CSV), "--workdays-only", "-q")
        self.assertEqual(r.returncode, 0)

    def test_name_filter_cli(self):
        entries = load_entries(CSV)
        name = entries[0].name
        r = self.run_cli(str(CSV), "--name", name, "--terminal", "--no-export-html")
        self.assertEqual(r.returncode, 0)
        self.assertIn("8.95 jam", r.stdout)

    def test_dedup_message(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(HEADER + SAMPLE_ROW + SAMPLE_ROW)
            dup_path = Path(f.name)
        try:
            r = self.run_cli(str(CSV), str(dup_path), "-q")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("duplikat", r.stderr)
        finally:
            dup_path.unlink()

    def test_detail_flag(self):
        r = self.run_cli(str(CSV), "--detail", "--terminal", "--no-export-html")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Detail Work Log", r.stdout)
        self.assertIn("Ringkasan per Tiket", r.stdout)

    def test_no_csv_error(self):
        r = self.run_cli("--no-auto-detect")
        self.assertNotEqual(r.returncode, 0)

    def test_multi_csv_explicit_without_flag(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write((HEADER + SAMPLE_ROW).encode("utf-8"))
            f.flush()
            extra = Path(f.name)
        try:
            r = self.run_cli(str(CSV), str(extra), "--compare", "--terminal", "--no-export-html")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Menggabungkan 2 file CSV", r.stderr)
        finally:
            extra.unlink()

    def test_run_without_args(self):
        r = self.run_cli(str(CSV), "-q", "--no-open")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            "Menggunakan file:" in r.stderr or "Menggabungkan" in r.stderr,
            r.stderr,
        )
        self.assertIn("HTML disimpan", r.stderr)

    def test_duplicate_path_not_double_counted(self):
        merged, skipped = load_all_csv([CSV, CSV])
        single = load_entries(CSV)
        self.assertAlmostEqual(sum_hours(merged), sum_hours(single), places=2)
        self.assertEqual(skipped, 0)

    def test_empty_csv_file(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(HEADER.encode("utf-8"))
            f.flush()
            empty_path = Path(f.name)
        try:
            entries = load_entries(empty_path)
            self.assertEqual(entries, [])
        finally:
            empty_path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
