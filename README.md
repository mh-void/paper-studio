# Paper Studio

A minimalist local desktop app for Cambridge O-Level students who want one clean, print-ready
PDF instead of eight separate downloads. Pick your subject, papers, variants, years and
sessions, press **Build booklet**, and get a bookmarked A4 PDF with a cover page.

Everything runs on your machine. Papers are downloaded once into `cache/`, finished booklets
are written to `output/`. No account, no cloud, no telemetry.

## Features

- **Pakistani O-Level presets** — Physics 5054, Chemistry 5070, Biology 5090, Mathematics D
  4024, Additional Maths 4037, Computer Science 2210, English Language 1123, Islamiyat 2058,
  Pakistan Studies 2059, Urdu 3248, Accounting 7707, Business Studies 7115, Economics 2281.
  Any other 4-digit code works through **Custom**.
- **Multi-paper** — build P1, P2, P4 or P6 in one pass (which papers exist depends on the
  subject).
- **Multi-variant** — tick V1 + V2 to expand every paper to both `41` and `42`, so you never
  sit an old variant by mistake.
- **2026 May/June already included** — sessions run 2016 to 2026, May/June and Oct/Nov.
  Papers that are not uploaded yet simply show as `missing`.
- **4× parallel downloads** — uncached papers fetch four at a time, with a per-host cap of
  three requests and jittered pacing so no source rate-limits you. Cold builds finish much
  sooner than the old one-at-a-time loop; after that the disk cache is instant.
- **Separate QP / MS / GT / ER PDFs** — pick any combination of the four separate chips
  to get one file per document kind instead of one merged booklet.
- **A\* Grade Threshold Database** — a second tab that parses every grade-threshold PDF into
  `year, session, option, A* mark, max mark, A* %`, with sortable and filterable columns,
  CSV/JSON export, and a **Zone 4** highlight on mixed-variant combos.
- **Monochrome Apple-dark UI** — one HTML file, no external assets, no build step.

## Quick start (Windows)

1. Download the repo as a ZIP and extract it anywhere.
2. Double-click **`Run Paper Studio.bat`**.

That is it. The launcher finds Python (via `py` or `python`), installs `pypdf` on first run,
and opens the app in a Chromium app window. If Python is missing it tells you where to get it
instead of flashing a black window.

## Quick start (terminal, any OS)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

The server binds `127.0.0.1` on a random free port and opens it in `msedge`, then `chrome`,
falling back to your default browser. Closing the window shuts the server down; so does
`Ctrl+C`. It also exits by itself after 90 seconds with no browser heartbeat.

Requires Python 3.10 or newer (tested on 3.15) and `pypdf`.

## Using it

1. Pick a subject pill, or type any 4-digit syllabus code via **Custom**.
2. Choose **Papers** (at least one stays on).
3. Choose **Variants**. V1 + V2 expands every paper to both `41` and `42`.
4. Pick a year range (2016-2026, inclusive) and the sessions you need.
5. Optional: grade thresholds, examiner reports, student name for the cover.
6. Pick a reading order, or tick one or more **Separate** chips, then press **Build booklet**.
   - **QPs Only** — question papers only.
   - **QPs then MS** — every question paper, then every mark scheme.
   - **Interleaved** — `QP 41, MS 41, QP 42, MS 42` per session, so you can mark as you go.
   - **Separate QP / MS / GT / ER** — one file per kind instead (see below).
7. The right card previews the exact files for your selection before you build, then streams
   progress: `queued`, `fetching`, `ok`, `cached`, `missing`.
8. When it finishes, use **Open merged PDF** (or **Open QP PDF** / **Open MS PDF** /
   **Open GT PDF** / **Open ER PDF** in separate mode) or **Show folder**.

Pages and bookmarks always follow `Year ascending → May/June before Oct/Nov → Component
ascending`, no matter what order the parallel downloads finish in. Every paper gets an
outline entry like `QP 41 · May/June 2024`, plus a `Cover` entry at page 0.

### Separate QP / MS / GT / ER files

Tick any of **Separate QP**, **Separate MS**, **Separate GT**, **Separate ER** under the
reading-order control to get one file per chosen document kind instead of one merged booklet:

```
PaperStudio_{code}_{min}_{max}_{QP|MS|GT|ER}_{HHMMSS}.pdf
```

Each chip is independent, so you can ask for three kinds and get three files. Every file gets
its own cover page with a section subtitle (`Question papers`, `Mark schemes`,
`Grade thresholds`, `Examiner reports`), its own bookmarks, and every requested kind is
planned in full even when the reading order is **QPs only** — separate files have no combined
reading order, so the segmented control dims, its thumb border disappears, and the
Grade thresholds / Examiner reports extras grey out too (the separate chips are the source of
truth there). The completion card shows **Open QP PDF**, **Open MS PDF**, **Open GT PDF**, and
**Open ER PDF** for whichever files were produced.

A kind with no downloaded content produces no file rather than a cover-only PDF. Missing files
are not fatal. The booklet is built from whatever succeeded, and the reason each file failed is
shown inline.

The request field is `separateKinds` — a list drawn from `["qp", "ms", "gt", "er"]`. An empty
list (the default) means one merged booklet.

### A\* thresholds tab

The **A\* thresholds** tab in the header opens a database view built from the same `*_gt.pdf`
files:

1. Pick a subject and a year range, then press **Load thresholds**.
2. Missing grade-threshold PDFs are fetched with 4 parallel workers (about 30 s cold).
3. Each row is `year, session, option, A* mark, max weighted mark, A* %`.
4. Sort any column, filter free-text, or narrow by the **Option / combo** dropdown.
5. **Copy CSV** and **Export JSON** copy or download exactly what is on screen.

Rows are parsed only from the syllabus grade-thresholds table. A 7-column grade block gives an
A\* value; a 6-column block shows `—` rather than guessing. Combos ending in `2` (e.g.
`12, 22, 42`) get a **Zone 4** pill. Parsed rows are cached in `cache/gt_db.json`, keyed by
filename, size and mtime, so the second visit is instant and **Clear cache** (which only
removes `cache/*.pdf`) keeps the parsed data.

### Filename convention

Cache files use Cambridge's own names, so you can drop your own PDFs in and the app uses them
without touching the network:

```
{code}_{sess}{yy}_{kind}.pdf          gt / er
{code}_{sess}{yy}_{kind}_{comp}.pdf   qp / ms / ci
```

Examples: `5054_s24_qp_41.pdf`, `5070_w23_ms_42.pdf`, `4024_s22_gt.pdf`. `{sess}` is `s` for
May/June and `w` for Oct/Nov. Use the drag-and-drop zone (or **Browse**) in the app, or copy
files into `cache/` yourself.

## How fetching works

`fetch_pdf()` tries each source in order and keeps the first response that actually is a PDF
(`%PDF-` header, at least 10 kB). Failures degrade to `missing: <reason>`; nothing crashes.

The build does a sequential disk-cache pass first (those rows resolve instantly), then hands
only the misses to a pool of four workers. Each request takes a per-host semaphore (max three
concurrent) and a 100 ms pause plus up to 120 ms of random jitter.

1. **Disk cache** — a valid file in `cache/` is returned with zero network calls.
2. **Host 1, PastPapers.Co canonical name** — the raw download endpoint that returns
   `application/pdf`:
   `https://pastpapers.co/api/file/caie/o-level/{slug}/{year}-{session}/{filename}?download=1`
   with the session page as `Referer`.
3. **Host 2, PastPapers.Co folder resolver** — newer sessions publish descriptive filenames
   (`5054-physics-june-2026-question-paper-41.pdf`) instead of canonical ones. The session
   folder page is fetched once per `(subject, year, session)` and shared by every worker, so
   four parallel misses never fire four identical page requests. Each PDF is classified into
   a kind (`qp`, `ms`, `gt`, `er`, `ci`) plus an optional trailing component, so `41` never
   matches `4`. The real filename is then downloaded under the clean canonical cache name.
4. **Host 3, PapaCambridge** — `https://pastpapers.papacambridge.com/{filename}`

All requests use a desktop Chrome user agent, `Accept: application/pdf,*/*` and a 25 second
timeout. Downloads are written to a `.tmp` file and then `os.replace`d into place, so an
interrupted download never leaves a half file that would be mistaken for a cache hit.

## Project layout

```
app.py                server, planner, fetcher, PDF merge, GT parser, launcher
index.html            the whole UI: markup, CSS, JS (no external assets)
requirements.txt      pypdf>=4.0
Run Paper Studio.bat  one-click Windows launcher
cache/                downloaded and dropped-in past papers, plus gt_db.json
output/               merged booklets and separate QP/MS/GT/ER files
```

`cache/` and `output/` are created on startup and are excluded from git — they hold your
downloads and finished PDFs, not source code. The cover page is a real A4 page
(595.28 × 841.89 pt) drawn with a Base-14 Helvetica content stream, so it needs no font files.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | the UI |
| POST | `/api/plan` | `{entries: [...]}` from the same planner the build uses; powers the live queue preview |
| POST | `/api/upload?name=` | store a dropped PDF in `cache/` |
| POST | `/api/build-booklet` | NDJSON progress stream, then `done` or `error` |
| POST | `/api/gt-database` | `{code, from, to, sessions, refresh}` → parsed A\* threshold rows |
| POST | `/api/clear-cache` | delete `cache/*.pdf` only; `gt_db.json` and `output/` survive (409 while a build runs) |
| GET | `/api/open-output?file=` | open a booklet; add `&folder=1` for the folder |
| POST | `/api/ping` | browser heartbeat for the idle shutdown |

`/api/build-booklet` emits `plan`, `fetch`, `ok`, `cache`, `miss`, `merge`, `done` and
`error` lines. When `separateKinds` is non-empty, `done.files` is a list of
`{kind, file, pages}` and `done.file` is empty; otherwise `done.files` is an empty list and
`done.file` names the merged booklet.

## Known limits

- **Site changes.** The scrapers target PastPapers.Co's `/api/file/...` endpoint and
  PapaCambridge's `pastpapers` subdomain. If either restructures its URLs, fetches for that
  host return `missing`. Fix by adding a source in `fetch_pdf()`; the rest of the app is
  unaffected.
- **Recent sessions.** Papers from the newest session may not be uploaded yet, and some
  variants are published late. Those show as `missing` until they appear.
- **Subject codes.** The 4-digit **Custom** code builds filenames but has no slugs, so it can
  only use the disk cache and the PapaCambridge host.
- **Cover text** is Latin-1/WinAnsi only. Non-Latin-1 characters in the student name are
  replaced with `?` on the cover (the papers themselves are untouched).
- **One build at a time.** A second concurrent build is rejected with HTTP 409.
- **Bandwidth.** A seven-year, two-session, QP+MS booklet is a few hundred MB the first time.
  After that it is cached and near-instant.

## Troubleshooting

**`pypdf` not found** — you are probably outside the venv. Re-activate it and re-run
`pip install -r requirements.txt`.

**Everything shows `missing`** — check that you are online and that you can open
`https://pastpapers.co/` in a browser. Corporate proxies and some ISPs block one of the two
hosts; the other usually still works. The inline reason tells you which host failed and how
(`HTTP 404`, `timeout`, `not a PDF`).

**The window does not open** — `python app.py` prints the URL it is serving. Open that URL
yourself; the app works in any browser.

**Port already in use** — it cannot happen by default; the port is `0` (OS-assigned). If you
see a bind error, another instance is holding an address you set manually.

**Build looks stuck** — a large first-run fetch can take a few minutes even in parallel. The
status line and spinner badge tell you which files are in flight. `Ctrl+C` in the terminal
stops everything safely; cached files are kept.

**Delete the cache to start over** — use **Clear cache** in the app header, or remove
`cache/*.pdf` yourself. `output/` (finished booklets) is left alone, and `cache/gt_db.json`
survives so the A\* table does not need re-fetching.
