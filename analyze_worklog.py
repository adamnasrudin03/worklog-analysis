#!/usr/bin/env python3
"""Analisis work log harian dari export CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from export_report import export_html, export_pdf
from export_xlsx import export_xlsx
from validation import collect_data_warnings
from worklog_lib import (
    DATA_DIR,
    DEFAULT_HTML_REPORT,
    DEMO_CSV,
    EXCEL_FIELDS,
    ReportData,
    WorkLogEntry,
    build_period_comparison,
    build_report,
    filter_entries,
    format_gap,
    format_grouping_breakdown,
    format_hours,
    format_hours_per_sp,
    format_time_spent,
    list_available_dates,
    list_available_months,
    list_available_names,
    list_available_weeks,
    load_all_csv,
    period_label_for,
    render_excel_tsv,
    sum_hours,
    target_summary_label,
    today_str,
    yesterday_str,
)


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def print_table(rows: list[tuple[str, str]], label_header: str = "Item") -> None:
    if not rows:
        print("  (tidak ada data)")
        return
    label_width = max(len(label_header), max(len(r[0]) for r in rows))
    print(f"  {label_header:<{label_width}}  Jam")
    for label, value in rows:
        print(f"  {label:<{label_width}}  {value}")


def print_compare(report: ReportData) -> None:
    print_section("Perbandingan antar hari")
    if len(report.daily_summaries) < 2:
        print("  Perlu minimal 2 hari data untuk perbandingan.")
        return

    header = (
        f"  {'Tanggal':<12}  {'Jam':>8}  {'vs Target':>12}  {'Entri':>5}  "
        f"{'DONE':>4}  {'Status':<8}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for d in report.daily_summaries:
        status = "OK" if d.target_met else "kurang"
        print(
            f"  {d.day:<12}  {d.total_hours:>8.2f}  {format_gap(d.gap_hours):>12}  "
            f"{d.entry_count:>5}  {d.done_count:>4}  {status:<8}"
        )
        print(f"    Grouping : {format_grouping_breakdown(d.by_grouping)}")
        print(f"    Layer    : {format_grouping_breakdown(d.by_layer)}")
        if d.work_summary_lines:
            print("    Kerja    :")
            for line in d.work_summary_lines:
                print(f"      - {line}")
            if d.work_summary_plain_hidden:
                n = len(d.work_summary_plain_hidden)
                print(f"      ... lihat {n} tiket lainnya di laporan HTML")
        print()

    best = max(report.daily_summaries, key=lambda d: d.total_hours)
    worst = min(report.daily_summaries, key=lambda d: d.total_hours)
    print()
    print(f"  Hari terproduktif : {best.day} ({format_hours(best.total_hours)})")
    print(f"  Hari terendah     : {worst.day} ({format_hours(worst.total_hours)})")
    print(
        f"  Rata-rata/hari    : {format_hours(report.avg_hours_per_day)} "
        f"({target_summary_label(report)} · target {report.target_hours:g}j)"
    )


def print_terminal(report: ReportData) -> None:
    if not report.entries:
        print("Tidak ada entri work log untuk filter ini.")
        return

    print(f"Ringkasan work log — {report.period_label}")
    print(f"  Orang        : {', '.join(report.names)}")
    print(f"  Tanggal      : {', '.join(report.days)}")
    print(f"  Entri        : {len(report.entries)}")
    print(f"  Total jam    : {format_hours(report.total_hours)}")
    print(f"  Target/hari  : {report.target_hours:g} jam")

    if len(report.daily_summaries) == 1:
        d = report.daily_summaries[0]
        status = "tercapai" if d.target_met else f"kurang {abs(d.gap_hours):.2f} jam"
        print(f"  vs target    : {status}")

    print_section("Jam per Grouping Activity")
    print_table(
        [(k, format_hours(v)) for k, v in report.by_grouping.items()],
        "Grouping Activity",
    )

    print_section("Jam per Activity")
    print_table(
        [(k, format_hours(v)) for k, v in report.by_activity.items()],
        "Activity",
    )

    print_section("Jam per Layer (FE / BE / Genesis)")
    print_table(
        [(k, format_hours(v)) for k, v in report.by_layer.items()],
        "Layer",
    )

    top_issues = list(report.by_issue.items())[:15]
    rows = []
    for key, hours in top_issues:
        title = report.issue_meta[key].ticket
        if len(title) > 55:
            title = title[:52] + "..."
        rows.append((f"{key} — {title}", format_hours(hours)))

    print_section("Jam per Tiket (top 15)")
    print_table(rows, "Tiket")

    print_section(f"Tiket dengan status DONE ({len(report.done_keys)})")
    if report.done_keys:
        done_rows = []
        for key in sorted(report.done_keys):
            meta = report.issue_meta[key]
            hrs = sum(e.time_spent for e in report.entries if e.issue_key == key)
            sp_label = f"SP {meta.story_points:g}" if meta.story_points else "SP -"
            done_rows.append((f"{key} ({sp_label})", format_hours(hrs)))
        print_table(done_rows, "Tiket")
    else:
        print("  (tidak ada)")

    print_section("Story Points (tiket yang disentuh)")
    if report.sp_touched:
        total_sp = sum(report.sp_touched.values())
        print(f"  Total SP unik: {total_sp:g} ({len(report.sp_touched)} tiket)")
        sp_rows = [
            (f"{k} — SP {v:g}", format_hours(report.by_issue.get(k, 0)))
            for k, v in sorted(report.sp_touched.items(), key=lambda x: -x[1])
        ]
        print_table(sp_rows, "Tiket")
    else:
        print("  (tidak ada SP > 0)")

    print_section("Efisiensi Story Points")
    metrics = report.sp_metrics
    if metrics.total_sp > 0:
        print(f"  Total SP       : {metrics.total_sp:g}")
        print(f"  Jam pada SP    : {format_hours(metrics.hours_on_sp_tickets)}")
        print(f"  Rata jam/SP    : {format_hours_per_sp(metrics.hours_per_sp)}")
        if metrics.per_ticket:
            eff_rows = [
                (
                    f"{m.issue_key} — SP {m.story_points:g}",
                    f"{format_hours(m.hours)} · {format_hours_per_sp(m.hours_per_sp)}",
                )
                for m in metrics.per_ticket[:15]
            ]
            print_table(eff_rows, "Tiket (jam & jam/SP)")
        if metrics.weekly_velocity:
            print_section("Velocity SP (tiket DONE per minggu)")
            vel_rows = [
                (
                    v.week,
                    f"{v.sp_done:g} SP · {format_hours(v.hours)} · {v.done_count} tiket",
                )
                for v in metrics.weekly_velocity
            ]
            print_table(vel_rows, "Minggu")
    else:
        print("  (tidak ada SP > 0)")

    if report.zero_time_entries:
        print_section(
            f"Entri tanpa waktu ({len(report.zero_time_entries)}) — biasanya penanda DONE"
        )
        for e in report.zero_time_entries:
            print(f"  {e.date.strftime('%H:%M')}  {e.issue_key}  {e.activity}")


def print_ticket_detail(report: ReportData) -> None:
    print_section(f"Ringkasan per Tiket ({len(report.ticket_summaries)})")
    if not report.ticket_summaries:
        print("  (tidak ada data)")
        return

    for ticket in report.ticket_summaries:
        done = "DONE" if ticket.is_done else "-"
        sp = f"SP {ticket.story_points:g}" if ticket.story_points else "SP -"
        print(
            f"\n  {ticket.issue_key} [{ticket.layer}] {done} | "
            f"{format_hours(ticket.total_hours)} | {sp}"
        )
        title = ticket.ticket
        if len(title) > 72:
            title = title[:69] + "..."
        print(f"  {title}")
        for line in ticket.lines:
            print(
                f"    {line.date.strftime('%Y-%m-%d %H:%M')}  "
                f"{format_time_spent(line.time_spent):>5}j  "
                f"{line.grouping_activity:<22}  {line.activity}"
            )


def print_daily_detail(report: ReportData) -> None:
    print_section(f"Detail Work Log ({len(report.entries)} entri)")
    if not report.entries_by_day:
        print("  (tidak ada data)")
        return

    for day, rows in report.entries_by_day.items():
        day_hours = sum(e.time_spent for e in rows)
        print(f"\n  === {day} — {format_hours(day_hours)} ({len(rows)} entri) ===")
        for entry in rows:
            sp = f"{entry.story_points:g}" if entry.story_points else "-"
            title = entry.ticket
            if len(title) > 50:
                title = title[:47] + "..."
            print(
                f"  {entry.date.strftime('%H:%M')}  {entry.issue_key:<10}  "
                f"{format_time_spent(entry.time_spent):>5}j  "
                f"{entry.grouping_activity:<20}  {entry.activity:<22}  "
                f"SP:{sp:<4}  {title}"
            )


def print_period_comparison(report: ReportData) -> None:
    comp = report.period_comparison
    if not comp:
        return
    print_section(f"Perbandingan vs {comp.previous_label}")
    print(f"  {comp.current_label:<20}  {format_hours(comp.current_hours):>14}  ({comp.current_days} hari)")
    print(f"  {comp.previous_label:<20}  {format_hours(comp.previous_hours):>14}  ({comp.previous_days} hari)")
    pct = comp.hours_pct
    if pct is not None:
        print(f"  Selisih               {format_gap(comp.hours_delta):>14}  ({pct:+.1f}%)")
    else:
        print(f"  Selisih               {format_gap(comp.hours_delta):>14}")
    print(
        f"  SP DONE               {comp.current_sp_done:g} vs {comp.previous_sp_done:g}"
    )


def print_warnings(warnings: list[str]) -> None:
    if not warnings:
        return
    print_section("Peringatan data")
    for w in warnings:
        print(f"  ⚠ {w}")


def run_report_pipeline(
    entries: list[WorkLogEntry],
    args: argparse.Namespace,
) -> ReportData:
    filtered = filter_entries(
        entries,
        day=args.date,
        week=args.week,
        month=args.month,
        name=args.name,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    label = period_label_for(
        args.date,
        args.week,
        args.month,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    if args.name:
        label = f"{label} · {args.name.strip()}"
    warnings = collect_data_warnings(filtered)
    comparison = None
    if args.compare_prev and (args.week or args.month):
        comparison = build_period_comparison(
            entries, filtered, week=args.week, month=args.month
        )
    return build_report(
        filtered,
        label,
        target_hours=args.target,
        workdays_only=args.workdays_only,
        warnings=warnings,
        period_comparison=comparison,
    )


def export_and_open(report: ReportData, path: Path, *, open_browser: bool) -> None:
    export_html(report, path)
    print(f"HTML disimpan: {path.resolve()}", file=sys.stderr)
    if open_browser:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())


def _log_csv_sources(paths: list[Path]) -> None:
    if len(paths) == 1:
        print(f"Menggunakan file: {paths[0]}", file=sys.stderr)
        return
    print(f"Menggabungkan {len(paths)} file CSV:", file=sys.stderr)
    for path in paths:
        print(f"  {path}", file=sys.stderr)


def watch_mode(args: argparse.Namespace, paths: list[Path]) -> int:
    import time

    print("Watch mode: regenerate saat CSV berubah (Ctrl+C stop)", file=sys.stderr)
    last_sig = ""
    while True:
        try:
            sig = "|".join(f"{p}:{p.stat().st_mtime_ns}" for p in paths if p.exists())
            if sig != last_sig:
                entries, dedup_skipped = load_all_csv(paths)
                if dedup_skipped:
                    print(
                        f"Melewati {dedup_skipped} entri duplikat.",
                        file=sys.stderr,
                    )
                report = run_report_pipeline(entries, args)
                print_warnings(report.warnings)
                export_path = DEFAULT_HTML_REPORT
                if args.export_html is not None:
                    export_path = args.export_html
                export_and_open(
                    report,
                    export_path,
                    open_browser=args.open and not args.no_open,
                )
                last_sig = sig
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nWatch mode dihentikan.", file=sys.stderr)
            return 0
    if len(paths) == 1:
        print(f"Menggunakan file: {paths[0]}", file=sys.stderr)
        return
    print(f"Menggabungkan {len(paths)} file CSV:", file=sys.stderr)
    for path in paths:
        print(f"  {path}", file=sys.stderr)


def resolve_csv_files(args: argparse.Namespace) -> list[Path]:
    if args.csv_files:
        paths = sorted({Path(p).resolve() for p in args.csv_files})
        missing = [p for p in paths if not p.exists()]
        if missing:
            print(f"File tidak ditemukan: {', '.join(str(p) for p in missing)}", file=sys.stderr)
            raise SystemExit(1)
        _log_csv_sources(paths)
        return paths

    if args.no_auto_detect:
        print("Berikan path file CSV.", file=sys.stderr)
        raise SystemExit(1)

    csv_files = sorted(DATA_DIR.glob("export-*.csv"))
    if not csv_files:
        if DEMO_CSV.exists():
            print(
                f"Tidak ada export-*.csv di {DATA_DIR}. "
                f"Menggunakan data demo: {DEMO_CSV}",
                file=sys.stderr,
            )
            return [DEMO_CSV]
        print(
            f"Tidak ada file export-*.csv di {DATA_DIR}. "
            "Letakkan export CSV di folder data/, atau jalankan dengan path template demo:\n"
            f"  python3 analyze_worklog.py {DEMO_CSV}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _log_csv_sources(csv_files)
    return csv_files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analisis work log harian dari file export CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  %(prog)s                              # export HTML ke laporan.html
  %(prog)s --today
  %(prog)s --from 2026-06-01 --to 2026-06-11
  %(prog)s --week 2026-W24 --compare-prev
  %(prog)s --watch
  %(prog)s --terminal --no-export-html
  %(prog)s data-a.csv data-b.csv
        """,
    )
    parser.add_argument(
        "csv_files",
        nargs="*",
        type=Path,
        help="File CSV opsional; default: semua export-*.csv di folder data/",
    )
    parser.add_argument(
        "-d",
        "--date",
        help="Filter tanggal (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Filter hari ini",
    )
    parser.add_argument(
        "--yesterday",
        action="store_true",
        help="Filter kemarin",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        metavar="YYYY-MM-DD",
        help="Filter tanggal mulai (range)",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        metavar="YYYY-MM-DD",
        help="Filter tanggal akhir (range)",
    )
    parser.add_argument(
        "--week",
        help="Filter minggu ISO (contoh: 2026-W24)",
    )
    parser.add_argument(
        "--month",
        help="Filter bulan (YYYY-MM)",
    )
    parser.add_argument(
        "--name",
        help="Filter nama (partial match, case-insensitive)",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=8.0,
        help="Target jam kerja per hari (default: 8)",
    )
    parser.add_argument(
        "--workdays-only",
        action="store_true",
        help="Hitung target/rata-rata hanya hari kerja (Sen–Jum, exclude libur nasional)",
    )
    parser.add_argument(
        "--no-auto-detect",
        action="store_true",
        help="Jangan otomatis pakai export-*.csv di folder data/",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Tampilkan detail work log per hari dan per tiket",
    )
    parser.add_argument(
        "--compare-prev",
        action="store_true",
        help="Bandingkan dengan minggu/bulan sebelumnya (butuh --week atau --month)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Tampilkan perbandingan antar hari di terminal",
    )
    parser.add_argument(
        "--list-dates",
        action="store_true",
        help="Daftar tanggal di file",
    )
    parser.add_argument(
        "--list-weeks",
        action="store_true",
        help="Daftar minggu ISO di file",
    )
    parser.add_argument(
        "--list-months",
        action="store_true",
        help="Daftar bulan di file",
    )
    parser.add_argument(
        "--list-names",
        action="store_true",
        help="Daftar nama di file",
    )
    parser.add_argument(
        "--export-html",
        nargs="?",
        const=DEFAULT_HTML_REPORT,
        default=None,
        type=Path,
        metavar="FILE",
        help=f"Export laporan HTML (default: {DEFAULT_HTML_REPORT.name})",
    )
    parser.add_argument(
        "--no-export-html",
        action="store_true",
        help="Jangan export HTML (hanya ringkasan terminal)",
    )
    parser.add_argument(
        "--terminal",
        action="store_true",
        help="Tampilkan ringkasan di terminal (selain export HTML)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Buka laporan HTML di browser (default jika terminal interaktif)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Jangan buka browser otomatis",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Pantau folder CSV dan regenerate laporan HTML",
    )
    parser.add_argument(
        "--export-pdf",
        type=Path,
        metavar="FILE",
        help="Export laporan ke PDF (fpdf2 + chart; weasyprint di Linux)",
    )
    parser.add_argument(
        "--export-excel",
        type=Path,
        metavar="FILE",
        help="Export TSV untuk paste ke Excel (kolom tanggal + ringkasan)",
    )
    parser.add_argument(
        "--export-xlsx",
        type=Path,
        metavar="FILE",
        help="Export laporan ke Excel .xlsx",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Cetak ringkasan per hari ke stdout (siap paste ke Excel kolom B)",
    )
    parser.add_argument(
        "--paste-field",
        choices=EXCEL_FIELDS,
        default="summary",
        help="Isi kolom paste Excel: summary (default), hours, atau full",
    )
    parser.add_argument(
        "--paste-with-date",
        action="store_true",
        help="Sertakan tanggal di --paste (paste ke kolom A:B)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Jangan tampilkan ringkasan terminal (hanya export)",
    )
    args = parser.parse_args()

    if args.today and args.yesterday:
        parser.error("Gunakan hanya satu dari --today atau --yesterday.")
    if args.date and (args.today or args.yesterday):
        parser.error("Gunakan hanya satu filter tanggal.")
    if args.date_from and args.date_to and args.date_from > args.date_to:
        parser.error("--from tidak boleh setelah --to.")
    if args.compare_prev and not (args.week or args.month):
        parser.error("--compare-prev membutuhkan --week atau --month.")

    if args.today:
        args.date = today_str()
    elif args.yesterday:
        args.date = yesterday_str()

    if args.target < 0:
        parser.error("--target tidak boleh negatif.")

    paths = resolve_csv_files(args)

    if args.watch:
        return watch_mode(args, paths)

    try:
        entries, dedup_skipped = load_all_csv(paths)
    except (KeyError, ValueError, OSError) as exc:
        print(f"Gagal membaca CSV: {exc}", file=sys.stderr)
        return 1

    if dedup_skipped:
        print(
            f"Melewati {dedup_skipped} entri duplikat (merge CSV).",
            file=sys.stderr,
        )

    if args.list_names:
        for name in list_available_names(entries):
            name_entries = filter_entries(entries, name=name)
            print(
                f"{name}  —  {len(name_entries)} entri, "
                f"{format_hours(sum_hours(name_entries))}"
            )
        return 0

    if args.list_dates:
        for d in list_available_dates(entries):
            day_entries = filter_entries(entries, day=d)
            print(f"{d}  —  {len(day_entries)} entri, {format_hours(sum_hours(day_entries))}")
        return 0

    if args.list_weeks:
        for w in list_available_weeks(entries):
            week_entries = filter_entries(entries, week=w)
            print(f"{w}  —  {len(week_entries)} entri, {format_hours(sum_hours(week_entries))}")
        return 0

    if args.list_months:
        for m in list_available_months(entries):
            month_entries = filter_entries(entries, month=m)
            print(f"{m}  —  {len(month_entries)} entri, {format_hours(sum_hours(month_entries))}")
        return 0

    if args.date and (args.week or args.month):
        parser.error("Gunakan hanya satu dari --date, --week, atau --month.")
    if args.week and args.month:
        parser.error("Gunakan hanya satu dari --week atau --month.")

    report = run_report_pipeline(entries, args)
    print_warnings(report.warnings)

    paste_mode = args.paste or args.export_excel is not None or args.export_xlsx is not None
    list_mode = (
        args.list_names
        or args.list_dates
        or args.list_weeks
        or args.list_months
    )

    if list_mode or paste_mode or args.no_export_html:
        export_html_path = None
    elif args.export_html is not None:
        export_html_path = args.export_html
    else:
        export_html_path = DEFAULT_HTML_REPORT

    should_open = (args.open or (sys.stdout.isatty() and not args.no_open)) and export_html_path

    show_terminal = (
        not args.quiet
        and not paste_mode
        and not list_mode
        and (
            args.no_export_html
            or args.terminal
            or args.compare
            or args.detail
            or args.compare_prev
        )
    )
    if show_terminal:
        print_terminal(report)
        if args.compare or (not args.date and len(report.daily_summaries) > 1):
            print_compare(report)
        if args.compare_prev:
            print_period_comparison(report)
        if args.detail:
            print_ticket_detail(report)
            print_daily_detail(report)

    if args.paste:
        sys.stdout.write(
            render_excel_tsv(
                report, args.paste_field, with_date=args.paste_with_date
            )
        )

    if args.export_excel:
        tsv = render_excel_tsv(report, args.paste_field, with_date=True)
        args.export_excel.write_text(tsv, encoding="utf-8")
        print(
            f"TSV disimpan: {args.export_excel.resolve()}",
            file=sys.stderr,
        )

    if args.export_xlsx:
        try:
            export_xlsx(report, args.export_xlsx, args.paste_field)
        except ImportError as exc:
            print(f"Gagal export xlsx: {exc}", file=sys.stderr)
            return 1
        print(f"XLSX disimpan: {args.export_xlsx.resolve()}", file=sys.stderr)

    if not report.entries and export_html_path:
        print(
            "Peringatan: tidak ada data untuk filter ini, export berisi laporan kosong.",
            file=sys.stderr,
        )

    if export_html_path:
        export_and_open(report, export_html_path, open_browser=should_open)

    if args.export_pdf:
        try:
            export_pdf(report, args.export_pdf)
        except Exception as exc:
            print(f"Gagal export PDF: {exc}", file=sys.stderr)
            return 1
        print(f"PDF disimpan: {args.export_pdf.resolve()}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
