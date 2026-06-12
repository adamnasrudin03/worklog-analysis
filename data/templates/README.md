# Template CSV Work Log

Folder ini berisi **contoh CSV** yang aman di-commit ke Git. Export Jira asli tetap di-ignore (lihat `.gitignore`).

## Mulai cepat (setelah clone)

```bash
pip install -r requirements.txt
make demo          # buka laporan.html dengan data fiktif
# atau tanpa Makefile:
python3 analyze_worklog.py --no-open
```

Jika belum ada `data/export-*.csv`, CLI otomatis memakai file demo ini.

## Isi data demo

`worklog-export.example.csv` = **1 minggu kerja fiktif** (Sen–Jum, 12–16 Jan 2026):

| Hari | Jam (±) | Contoh isi |
|------|---------|------------|
| Sen 12 | ~9j | Read PRD, analysis API, detailing BE/FE |
| Sel 13 | ~10j | Coding payment API, pairing, FE error row |
| Rab 14 | ~8j | Coding + issue staging + DONE |
| Kam 15 | ~7j | Code review, revisit QA, matrix epic |
| Jum 16 | ~9.5j | Sprint demo, DONE tickets, SA docs |

30 entri · 5 hari · mix Worklog harian + tiket DEMO-102…107.

## Salin untuk dipakai sendiri

```bash
cp data/templates/worklog-export.example.csv data/export-my-worklog.csv
# edit / ganti dengan export Jira Anda
```

## Format kolom (wajib)

| Kolom | Contoh | Keterangan |
|-------|--------|------------|
| `Name` | `Jane Doe` | Nama dari export Jira |
| `Issue` | `https://…/browse/DEMO-101` | URL tiket |
| `Ticket` | `[BE][service] Judul tiket` atau `Worklog …` | Judul tiket |
| `Time Spent` | `1.5` | Jam (desimal) |
| `Grouping Activity` | `CODING`, `DETAILING` | Grouping level atas |
| `Activity` | `[CODING] detail pekerjaan` | Jenis activity dalam `[]` + detail |
| `Date` | `2026-01-15T09:00:00+0700` | ISO datetime dengan timezone |
| `SP` | `2` | Story points (0 jika tidak ada) |

## Tips activity

- Gunakan format **`[JENIS ACTIVITY] detail`** di kolom Activity.
- Tiket **Worklog** (judul diawali `Worklog …`): detail diambil dari Activity, bukan judul tiket.
- Lihat referensi lengkap jenis activity di laporan HTML → **Referensi Jenis Activity**.

## Privasi

- Simpan export asli sebagai `data/export-*.csv` — **tidak** di-commit.
- Hanya file di `data/templates/` (contoh fiktif) yang masuk repository.
