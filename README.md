# Work Log Analysis

Analisis work log harian dari export CSV Jira: ringkasan jam, grouping activity, layer (FE/BE/Genesis), story points, dan export ke terminal / HTML / PDF / Excel.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# libur nasional: package `holidays` (Indonesia/ID, offline, tanpa API key)
# atau: pip install -e ".[dev]"
```

## Quick start (data demo)

Setelah clone, tanpa export Jira pun bisa langsung coba:

```bash
make demo              # laporan HTML dari data demo + buka browser
# atau
python3 analyze_worklog.py --no-open
```

Perintah di atas otomatis memakai `data/templates/worklog-export.example.csv` (1 minggu data demo) jika belum ada `data/export-*.csv`.

Alternatif eksplisit:

```bash
python3 analyze_worklog.py data/templates/worklog-export.example.csv --no-open
python3 analyze_worklog.py --terminal --no-export-html data/templates/worklog-export.example.csv
```

Salin template ke `data/` jika ingin mengedit tanpa mengubah file demo asli:

```bash
cp data/templates/worklog-export.example.csv data/export-demo.csv
python3 analyze_worklog.py --no-open
```

## Alur kerja (export Jira Anda)

1. Export work log dari Jira ke CSV.
2. Simpan file ke folder `data/` (`export-*.csv`), atau salin dari template:

```bash
cp data/templates/worklog-export.example.csv data/export-my-worklog.csv
# edit / ganti dengan export Jira Anda
```

3. Jalankan:

```bash
make report          # export laporan.html + buka browser (TTY)
python3 analyze_worklog.py --today
python3 analyze_worklog.py --watch   # auto-regenerate saat CSV berubah
```

## Opsi CLI utama

| Flag | Keterangan |
|------|------------|
| `--today` / `--yesterday` | Filter cepat |
| `--from` / `--to` | Range tanggal |
| `-d` / `--week` / `--month` | Filter periode |
| `--name` | Filter nama (partial match) |
| `--workdays-only` | Target hanya hari kerja + libur nasional (`holidays` lib, ID) |
| `--compare-prev` | Banding vs minggu/bulan sebelumnya |
| `--compare` | Perbandingan antar hari (terminal) |
| `--export-xlsx FILE` | Export Excel `.xlsx` |
| `--watch` | Pantau CSV, regenerate HTML |
| `--no-open` | Jangan buka browser otomatis |
| `--terminal --no-export-html` | Mode terminal saja |

Default: export `laporan.html` dan buka browser jika terminal interaktif.

## Fitur laporan HTML

- Dark mode toggle (preferensi tersimpan)
- Filter tanggal, minggu, bulan, **nama**, grouping, layer
- Ringkasan kerja dengan **"Lihat N tiket lainnya"** (dropdown)
- Chart jam harian: **merah** = di bawah target
- Ringkasan tim (multi-orang)
- Perbandingan periode, jam/SP per layer
- Banner peringatan data (jam anomali, SP tidak konsisten)

## Makefile

```bash
make report   # generate HTML (auto demo jika belum ada export)
make demo     # generate HTML dari template demo + buka browser
make open     # generate + buka browser
make watch    # watch mode
make test     # unit tests
make lint     # ruff check
```

## Privasi & data di repo

| Path | Git | Isi |
|------|-----|-----|
| `data/templates/*.csv` | ✅ Commit | Template contoh (data fiktif) |
| `data/export-*.csv` | ❌ Ignore | Export Jira asli Anda |
| `laporan.html` | ❌ Ignore | Output laporan |

Jangan commit export Jira berisi data sensitif. Panduan kolom CSV: [data/templates/README.md](data/templates/README.md).

## Test

```bash
python3 -m unittest test_worklog.py -v
```

## Struktur

```
analyze_worklog.py   # CLI
worklog_lib.py       # parsing, agregasi, metrik
activity_reference.py # katalog jenis activity
export_report.py     # HTML & PDF
export_xlsx.py       # Excel export
validation.py        # validasi CSV & warnings
data/templates/      # template CSV contoh (di-commit)
tests/fixtures/      # sample CSV untuk unit test
```
