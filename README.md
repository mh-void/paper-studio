# Paper Studio

A small local app that turns Cambridge O-Level past papers into one print-ready PDF. You pick
a subject, papers, variants, years and sessions, press **Build booklet**, and get a bookmarked
A4 PDF with a cover page. Where you would otherwise download eight files by hand, you get one.

Everything runs on your machine. Papers are downloaded once into `cache/`, finished booklets
go to `output/`. No account, no cloud, no telemetry.

## What it does

- Presets for the usual Pakistani O-Level subjects: Physics 5054, Chemistry 5070, Biology 5090,
  Mathematics D 4024, Additional Maths 4037, Computer Science 2210, English Language 1123,
  Islamiyat 2058, Pakistan Studies 2059, Urdu 3248, Accounting 7707, Business Studies 7115,
  Economics 2281. Any other 4-digit code works through **Custom**.
- Build P1, P2, P4 or P6 in one pass. Which papers you get depends on the subject.
- Tick V1 and V2 together and every paper expands to both `41` and `42`, so you don't end up
  sitting an old variant.
- Sessions run 2016 to 2026, May/June and Oct/Nov. Papers that aren't uploaded yet show as
  `missing`.
- Uncached papers download four at a time, capped at three requests per host with jitter in
  between, so no source rate-limits you. Cold builds finish much faster than the old
  one-at-a-time loop, and after that the disk cache is instant.
- Separate QP, MS, GT and ER chips. Pick any combination and you get one file per document
  kind instead of a merged booklet.
- An A\* thresholds tab that parses every grade-threshold PDF into `year, session, option,
  A* mark, max mark, A* %`, with sortable columns, filters, CSV/JSON export and a **Zone 4**
  highlight on mixed-variant combos.
- The UI is one HTML file with no external assets and no build step, in monochrome dark.

## Quick start (Windows)

1. Download the repo as a ZIP and extract it anywhere.
2. Double-click `Run Paper Studio.bat`.

The launcher finds Python (through `py` or `python`), installs `pypdf` on first run, and opens
the app in a Chromium app window. If Python is missing it tells you where to get it instead of
flashing a black window.

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
falling back to your default browser. Closing the window shuts the server down, and so does
`Ctrl+C`. It also quits by itself after 90 seconds with no browser heartbeat.

You need Python 3.10 or newer (tested on 3.15) and `pypdf`.

## Using it

1. Pick a subject pill, or type any 4-digit syllabus code via **Custom**.
2. Choose **Papers**. At least one stays on.
3. Choose **Variants**. V1 plus V2 expands every paper to both `41` and `42`.
4. Pick a year range (2016-2026, inclusive) and the sessions you need.
5. Optional: grade thresholds, examiner reports, student name for the cover.
6. Pick a reading order, or tick one or more **Separate** chips, then press **Build booklet**.
   - **QPs Only** gives you question papers only.
   - **QPs then MS** gives every question paper, then every mark scheme.
   - **Interleaved** goes `QP 41, MS 41, QP 42, MS 42` per session, so you can mark as you go.
   - **Separate QP / MS / GT / ER** gives one file per kind instead (see below).
7. The right card previews the exact files for your selection before you build, then streams
   progress: `queued`, `fetching`, `ok`, `cached`, `missing`.
8. When it finishes, use **Open merged PDF** (or **Open QP PDF** / **Open MS PDF** /
   **Open GT PDF** / **Open ER PDF** in separate mode) or **Show folder**.

Pages and bookmarks always go year ascending, then May/June before Oct/Nov, then component
ascending, whatever order the parallel downloads finish in. Every paper gets an outline entry
like `QP 41 · May/June 2024`, plus a `Cover` entry at page 0.

### Separate QP / MS / GT / ER files

Tick any of **Separate QP**, **Separate MS**, **Separate GT**, **Separate ER** under the
reading-order control and you get one file per chosen kind instead of one merged booklet:

```
PaperStudio_{code}_{min}_{max}_{QP|MS|GT|ER}_{HHMMSS}.pdf
```

The chips are independent, so asking for three kinds gives you three files. Each file gets its
own cover page with a section subtitle (`Question papers`, `Mark schemes`, `Grade thresholds`,
`Examiner reports`) and its own bookmarks. Every requested kind is planned in full even when
the reading order is **QPs only**, since separate files have no combined reading order. The
segmented control dims, its thumb border disappears, and the grade thresholds / examiner
reports extras grey out too, because the separate chips decide what goes in. The completion
card shows **Open QP PDF**, **Open MS PDF**, **Open GT PDF** and **Open ER PDF** for whatever
was actually produced.

If a kind has no downloaded content, you get no file for it instead of a cover-only PDF.
Missing files aren't fatal: the booklet is built from whatever succeeded, and the reason each
file failed is shown inline.

The request field is `separateKinds`, a list drawn from `["qp", "ms", "gt", "er"]`. An empty
list (the default) means one merged booklet.

### A\* thresholds tab

The **A\* thresholds** tab in the header opens a database view built from the same `*_gt.pdf`
files:

1. Pick a subject and a year range, then press **Load thresholds**.
2. Missing grade-threshold PDFs are fetched with 4 parallel workers (about 30 s cold).
3. Each row is `year, session, option, A* mark, max weighted mark, A* %`.
4. Sort any column, filter free-text, or narrow by the **Option / combo** dropdown.
5. **Copy CSV** and **Export JSON** copy or download exactly what is on screen.

Rows only come from the syllabus grade-thresholds table. A 7-column grade block gives an A\*
value; a 6-column block shows a dash. Combos ending in `2` (like `12, 22, 42`) get a **Zone 4**
pill. Parsed rows are cached in `cache/gt_db.json`, keyed by filename, size and mtime, so the
second visit is instant. **Clear cache** only removes `cache/*.pdf`, so the parsed data
survives.

### Filename convention

Cache files use Cambridge's own names, so you can drop your own PDFs in and the app will use
them without touching the network:

```
{code}_{sess}{yy}_{kind}.pdf          gt / er
{code}_{sess}{yy}_{kind}_{comp}.pdf   qp / ms / ci
```

Examples: `5054_s24_qp_41.pdf`, `5070_w23_ms_42.pdf`, `4024_s22_gt.pdf`. `{sess}` is `s` for
May/June and `w` for Oct/Nov. Use the drag-and-drop zone (or **Browse**) in the app, or copy
files into `cache/` yourself.

## How fetching works

`fetch_pdf()` tries each source in order and keeps the first response that really is a PDF
(`%PDF-` header, at least 10 kB). Failures degrade to `missing: <reason>` instead of crashing.

The build does a sequential disk-cache pass first (those rows resolve instantly), then hands
only the misses to a pool of four workers. Each request takes a per-host semaphore (max three
concurrent) and a 100 ms pause plus up to 120 ms of random jitter.

1. Disk cache. A valid file in `cache/` comes back with zero network calls.
2. Host 1, PastPapers.Co canonical name. The raw download endpoint that returns
   `application/pdf`:
   `https://pastpapers.co/api/file/caie/o-level/{slug}/{year}-{session}/{filename}?download=1`
   with the session page as `Referer`.
3. Host 2, PastPapers.Co folder resolver. Newer sessions publish descriptive filenames
   (`5054-physics-june-2026-question-paper-41.pdf`) instead of canonical ones. The session
   folder page is fetched once per `(subject, year, session)` and shared by every worker, so
   four parallel misses don't fire four identical page requests. Each PDF is classified into a
   kind (`qp`, `ms`, `gt`, `er`, `ci`) plus an optional trailing component, so `41` never
   matches `4`. The real filename is then downloaded under the clean canonical cache name.
4. Host 3, PapaCambridge. `https://pastpapers.papacambridge.com/{filename}`.

All requests use a desktop Chrome user agent, `Accept: application/pdf,*/*` and a 25 second
timeout. Downloads go to a `.tmp` file and then get `os.replace`d into place, so an interrupted
download never leaves a half file that a later run would mistake for a cache hit.

## Project layout

```
app.py                server, planner, fetcher, PDF merge, GT parser, launcher
index.html            the whole UI: markup, CSS, JS (no external assets)
requirements.txt      pypdf>=4.0
Run Paper Studio.bat  one-click Windows launcher
cache/                downloaded and dropped-in past papers, plus gt_db.json
output/               merged booklets and separate QP/MS/GT/ER files
```

`cache/` and `output/` are created on startup and excluded from git, since they hold your
downloads and finished PDFs rather than source code. The cover page is a real A4 page
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

`/api/build-booklet` emits `plan`, `fetch`, `ok`, `cache`, `miss`, `merge`, `done` and `error`
lines. When `separateKinds` is non-empty, `done.files` is a list of `{kind, file, pages}` and
`done.file` is empty. Otherwise `done.files` is an empty list and `done.file` names the merged
booklet.

## Known limits

- Site changes. The scrapers target PastPapers.Co's `/api/file/...` endpoint and PapaCambridge's
  `pastpapers` subdomain. If either restructures its URLs, fetches for that host return
  `missing`. Add a source in `fetch_pdf()` and the rest of the app keeps working.
- Recent sessions. Newest-session papers may not be uploaded yet, and some variants come late.
  They show as `missing` until they appear.
- Subject codes. A 4-digit **Custom** code builds filenames but has no slug, so it can only use
  the disk cache and the PapaCambridge host.
- Cover text is Latin-1/WinAnsi only. Non-Latin-1 characters in the student name become `?` on
  the cover; the papers themselves are untouched.
- One build at a time. A second concurrent build gets HTTP 409.
- Bandwidth. A seven-year, two-session, QP+MS booklet is a few hundred MB the first time. After
  that it's cached and near-instant.

## Troubleshooting

**`pypdf` not found.** You're probably outside the venv. Re-activate it and re-run
`pip install -r requirements.txt`.

**Everything shows `missing`.** Check that you're online and that `https://pastpapers.co/`
opens in a browser. Corporate proxies and some ISPs block one of the two hosts; the other
usually still works. The inline reason tells you which host failed and how (`HTTP 404`,
`timeout`, `not a PDF`).

**The window doesn't open.** `python app.py` prints the URL it's serving. Open that URL
yourself; the app works in any browser.

**Port already in use.** Not possible by default, since the port is `0` (OS-assigned). A bind
error means another instance is holding an address you set by hand.

**Build looks stuck.** A large first-run fetch can take a few minutes even in parallel. The
status line and spinner badge tell you which files are in flight. `Ctrl+C` in the terminal
stops everything safely and keeps the cached files.

**Want to start over.** Use **Clear cache** in the app header, or delete `cache/*.pdf`
yourself. `output/` is left alone and `cache/gt_db.json` survives, so the A\* table doesn't
need re-fetching.
