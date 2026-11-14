"""Paper Studio - local Cambridge O-Level past-paper booklet builder.

Python 3 standard library + pypdf only. No other dependencies.
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
OUTPUT = ROOT / "output"
CACHE.mkdir(parents=True, exist_ok=True)
OUTPUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MIN_PDF_BYTES = 10_000

SUBJECTS = {
    "4024": ("Mathematics D", "o-level-mathematics-d-calculator-version-4024", "mathematics-d-4024", [1, 2]),
    "4037": ("Additional Maths", "o-level-mathematics-additional-4037", "additional-mathematics-4037", [1, 2]),
    "5054": ("Physics", "o-level-physics-5054", "physics-5054", [1, 2, 4]),
    "5070": ("Chemistry", "o-level-chemistry-5070", "chemistry-5070", [1, 2, 4]),
    "5090": ("Biology", "o-level-biology-5090", "biology-5090", [1, 2, 6]),
    "2210": ("Computer Science", "o-level-computer-science-2210", "computer-science-2210", [1, 2]),
    "1123": ("English Language", "o-level-english-language-1123", "english-language-1123", [1, 2]),
    "2058": ("Islamiyat", "o-level-islamiyat-2058", "islamiyat-2058", [1, 2]),
    "2059": ("Pakistan Studies", "o-level-pakistan-studies-2059", "pakistan-studies-2059", [1, 2]),
    "3248": ("Urdu 2nd Language", "o-level-urdu-3248", "urdu-second-language-3248", [1, 2]),
    "7707": ("Accounting", "o-level-accounting-7707", "accounting-7707", [1, 2]),
    "7115": ("Business Studies", "o-level-business-studies-7115", "business-studies-7115", [1, 2]),
    "2281": ("Economics", "o-level-economics-2281", "economics-2281", [1, 2]),
}
CUSTOM = ("Custom subject", None, None, [1, 2])

SESSIONS = {"s": ("May/June", "may-june"), "w": ("Oct/Nov", "oct-nov")}
SESSION_ORDER = {"s": 0, "w": 1}
MODES = ("qps", "qps_then_ms", "interleaved")


def subject(code: str):
    code = str(code).strip()
    return code, SUBJECTS.get(code, CUSTOM)


def filename(code: str, sess: str, year: int, kind: str, comp=None) -> str:
    base = f"{code}_{sess}{year % 100:02d}_{kind}"
    if kind in ("gt", "er"):
        return base + ".pdf"
    return f"{base}_{comp}.pdf"


def _looks_like_pdf(body: bytes) -> bool:
    return body.startswith(b"%PDF-") and len(body) >= MIN_PDF_BYTES


def _cache_path(name: str) -> Path:
    return CACHE / name


def _http_get(url: str, referer: str | None = None,
              accept: str = "application/pdf,*/*") -> bytes:
    headers = {"User-Agent": UA, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def _store(name: str, body: bytes) -> Path:
    path = _cache_path(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, path)
    return path


KIND_TOKENS = {
    "qp": ("question-paper",),
    "ms": ("mark-scheme",),
    "gt": ("grade-thresholds", "grade-threshold"),
    "er": ("examiner-report", "examiners-report"),
    "ci": ("confidential-instructions",),
}

_FOLDER_CACHE: dict[tuple[str, int, str], dict[str, str] | None] = {}
_FOLDER_LOCK = threading.Lock()
_FOLDER_INFLIGHT: dict[tuple[str, int, str], threading.Event] = {}

# Host pacing: never more than 3 concurrent requests to one host.
_HOST_SEM = {
    "pastpapers.co": threading.Semaphore(3),
    "pastpapers.papacambridge.com": threading.Semaphore(3),
}
REQUEST_PAUSE = 0.1
REQUEST_JITTER = 0.12


def _pace() -> None:
    """Sleep with jitter before a request so bursts never look like a hammer."""
    time.sleep(REQUEST_PAUSE + random.uniform(0, REQUEST_JITTER))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _classify(stem: str) -> tuple[str, str | None] | None:
    """Map a folder filename stem to (kind, comp) or None if unrecognised."""
    low = stem.lower()
    kind = None
    # Longest tokens first so 'mark-scheme' wins over a stray 'ms'.
    for key in ("ci", "er", "gt", "ms", "qp"):
        for token in KIND_TOKENS[key]:
            if token in low:
                kind = key
                break
        if kind:
            break
    if kind is None:
        m = re.search(r"_(qp|ms|gt|er|ci)(?=_|\d|\.|$)", low)
        if m:
            kind = m.group(1)
    if kind is None:
        return None
    if kind in ("gt", "er"):
        return kind, None
    m = re.search(r"[-_](\d{2})\.pdf$", low)
    return kind, (m.group(1) if m else None)


def _folder_map(co_slug: str, year: int, session_slug: str) -> dict[str, str] | None:
    """clean canonical name -> real filename, scraped once per session folder."""
    key = (co_slug, year, session_slug)
    with _FOLDER_LOCK:
        if key in _FOLDER_CACHE:
            return _FOLDER_CACHE[key]
        # First caller claims the fetch; the rest wait for its result so four
        # workers never fire the same folder request at once.
        event = _FOLDER_INFLIGHT.get(key)
        owner = event is None
        if owner:
            event = threading.Event()
            _FOLDER_INFLIGHT[key] = event

    if not owner:
        event.wait(timeout=60)
        with _FOLDER_LOCK:
            return _FOLDER_CACHE.get(key)

    url = f"https://pastpapers.co/caie/o-level/{co_slug}/{year}-{session_slug}"
    mapping: dict[str, str] | None = None
    try:
        with _HOST_SEM["pastpapers.co"]:
            _pace()
            html = _http_get(url, accept="text/html,*/*").decode("utf-8", "replace")
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        html = ""
    if html:
        mapping = {}
        stems = {
            href.rsplit("/", 1)[-1]
            for href in re.findall(r'href="([^"]+\.pdf)"', html)
            if "/api/file/" not in href
        }
        for real in sorted(stems):
            classified = _classify(real)
            if not classified:
                continue
            kind, comp = classified
            mapping[_norm(real)] = real
            mapping[kind + ("_" + comp if comp else "")] = real
    with _FOLDER_LOCK:
        _FOLDER_CACHE[key] = mapping
        _FOLDER_INFLIGHT.pop(key, None)
    event.set()
    return mapping


def cache_hit(code: str, sess: str, year: int, kind: str, comp=None) -> str | None:
    """Return the cached path if a valid PDF is already on disk, else None."""
    cached = _cache_path(filename(code, sess, year, kind, comp))
    if not cached.exists():
        return None
    try:
        body = cached.read_bytes()
    except OSError:
        return None
    return str(cached) if _looks_like_pdf(body) else None


def fetch_pdf(code: str, sess: str, year: int, kind: str, comp=None,
              papa_slug: str | None = None, co_slug: str | None = None):
    """Return (status, value). status is 'cache', 'ok' or 'missing'."""
    name = filename(code, sess, year, kind, comp)
    cached = _cache_path(name)
    hit = cache_hit(code, sess, year, kind, comp)
    if hit:
        return "cache", hit

    session_slug = SESSIONS[sess][1]
    failures: list[str] = []

    def attempt(label: str, url: str, host: str, referer: str | None = None):
        with _HOST_SEM[host]:
            _pace()
            try:
                body = _http_get(url, referer)
            except urllib.error.HTTPError as exc:
                failures.append(f"{label} HTTP {exc.code}")
                return None
            except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
                failures.append(f"{label} {type(exc).__name__}")
                return None
            if not _looks_like_pdf(body):
                failures.append(f"{label} not a PDF")
                return None
            _store(name, body)
            return str(cached)

    api_base = None
    if co_slug:
        folder = f"https://pastpapers.co/caie/o-level/{co_slug}/{year}-{session_slug}"
        api_base = f"https://pastpapers.co/api/file/caie/o-level/{co_slug}/{year}-{session_slug}"

        # Host 1: canonical filename on the download endpoint.
        got = attempt("pastpapers.co", f"{api_base}/{name}?download=1",
                      "pastpapers.co", referer=folder)
        if got:
            return "ok", got

        # Host 2: resolve the real filename from the session folder listing.
        # Newer sessions publish descriptive slugs (e.g. 5054-physics-june-2026-
        # question-paper-41.pdf) instead of canonical ones.
        mapping = _folder_map(co_slug, year, session_slug)
        if mapping:
            wanted = kind + ("_" + str(comp) if comp is not None else "")
            real = mapping.get(wanted) or mapping.get(_norm(name))
            if real:
                got = attempt("pastpapers.co folder", f"{api_base}/{real}?download=1",
                              "pastpapers.co", referer=folder)
                if got:
                    return "ok", got
            else:
                failures.append("pastpapers.co folder no match")
        else:
            failures.append("pastpapers.co folder unavailable")

    # Host 3: PapaCambridge past-papers subdomain.
    got = attempt("papacambridge", f"https://pastpapers.papacambridge.com/{name}",
                  "pastpapers.papacambridge.com")
    if got:
        return "ok", got

    return "missing", "; ".join(failures) or "not found"

def plan_entries(cfg: dict) -> list[dict]:
    """Turn a validated config into the ordered list of PDF entries."""
    code = str(cfg["code"]).strip()
    _, (_, _, _, default_papers) = subject(code)
    papers = sorted({int(p) for p in cfg.get("papers") or default_papers})
    variants = sorted({str(v) for v in cfg.get("variants") or ["1"]})
    comps = [f"{p}{v}" for p in papers for v in variants]
    sessions = [s for s in ("s", "w") if s in (cfg.get("sessions") or ["s"])] or ["s"]
    mode = cfg.get("mode") if cfg.get("mode") in MODES else "qps_then_ms"
    years = sorted({int(y) for y in cfg.get("years") or []})
    with_ms = mode != "qps"

    entries: list[dict] = []

    def add(year, sess, kind, comp=None):
        entries.append({
            "kind": kind, "comp": comp, "year": year, "sess": sess,
            "filename": filename(code, sess, year, kind, comp),
        })

    separate = cfg.get("separateKinds") or []
    if separate:
        # Separate files have no combined reading order, so `mode` is ignored
        # and every requested kind is planned in full.
        for kind in separate:
            for year in years:
                for sess in sessions:
                    if kind in ("qp", "ms"):
                        for comp in comps:
                            add(year, sess, kind, comp)
                    else:
                        add(year, sess, kind)
        return entries

    for year in years:
        for sess in sessions:
            if mode == "interleaved":
                for comp in comps:
                    add(year, sess, "qp", comp)
                    if with_ms:
                        add(year, sess, "ms", comp)
            else:
                for comp in comps:
                    add(year, sess, "qp", comp)

    if mode == "qps_then_ms":
        for year in years:
            for sess in sessions:
                for comp in comps:
                    add(year, sess, "ms", comp)

    if cfg.get("gt"):
        for year in years:
            for sess in sessions:
                add(year, sess, "gt")
    if cfg.get("er"):
        for year in years:
            for sess in sessions:
                add(year, sess, "er")

    return entries


def _s(text: str) -> str:
    """Escape text for a PDF literal string (WinAnsi / Latin-1)."""
    out = []
    for ch in str(text):
        if ch in "\\()":
            out.append("\\" + ch)
        else:
            try:
                ch.encode("cp1252")
                out.append(ch)
            except UnicodeEncodeError:
                out.append("?")
    return "".join(out)


def _wrap(text: str, width: int = 92) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if len(candidate) > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


SECTION_LABELS = {
    "qp": "Question papers",
    "ms": "Mark schemes",
    "gt": "Grade thresholds",
    "er": "Examiner reports",
}
SEPARATE_KINDS = ("qp", "ms", "gt", "er")


def _cover_page(writer: PdfWriter, cfg: dict, section: str | None = None) -> None:
    width, height = 595.28, 841.89
    page = writer.add_blank_page(width=width, height=height)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    font_ref = writer._add_object(font)
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })
    page[NameObject("/Resources")] = resources

    code = str(cfg["code"])
    _, (name, _, _, _) = subject(code)
    papers = sorted({int(p) for p in cfg.get("papers") or [1]})
    variants = sorted({str(v) for v in cfg.get("variants") or ["1"]})
    sessions = [SESSIONS[s][0] for s in ("s", "w") if s in (cfg.get("sessions") or ["s"])] or ["May/June"]
    years = sorted({int(y) for y in cfg.get("years") or []})
    year_text = f"{years[0]}-{years[-1]}" if years else "-"
    mode_label = {"qps": "Questions only", "qps_then_ms": "Questions then mark schemes",
                  "interleaved": "Questions and mark schemes interleaved"}.get(cfg.get("mode"), "")

    ops = []

    def text(x, y, size, value, gray=0.0):
        ops.append(
            f"BT /F1 {size} Tf {gray:.3f} g 1 0 0 1 {x:.2f} {y:.2f} Tm ({_s(value)}) Tj ET"
        )

    text(56, height - 96, 28, "Paper Studio", 0.06)
    ops.append(f"0.7 G 0.6 w 56 {height - 116:.2f} m {width - 56:.2f} {height - 116:.2f} l S")
    text(56, height - 150, 14, f"{code} · {name} · Variants {','.join(variants)}", 0.18)
    if section:
        text(56, height - 174, 12, SECTION_LABELS.get(section, section), 0.10)
        offset = 18
    else:
        offset = 0
    text(56, height - 176 - offset, 11, f"Sessions: {', '.join(sessions)}", 0.28)
    text(56, height - 194 - offset, 11, f"Years: {year_text}", 0.28)
    text(56, height - 212 - offset, 11, f"Papers: {', '.join('P' + str(p) for p in papers)}", 0.28)
    text(56, height - 230 - offset, 11, f"Mode: {mode_label}", 0.28)

    y = height - 272 - offset
    for line in _wrap("Contents: " + " / ".join(str(e["filename"]) for e in cfg.get("_entries", []))):
        if y < 150:
            break
        text(56, y, 10, line, 0.42)
        y -= 14

    student = str(cfg.get("student") or "").strip()
    if student:
        text(56, 112, 13, f"Student: {student}", 0.1)
    text(56, 84, 9, "Compiled locally by Paper Studio. Past papers remain the property of Cambridge.", 0.55)

    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("cp1252", "replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def build_booklet(cfg: dict, entries: list[dict], progress=None) -> Path:
    writer = PdfWriter()
    cfg = dict(cfg, _entries=entries)
    _cover_page(writer, cfg)
    writer.add_outline_item("Cover", 0)

    for entry in entries:
        path = _cache_path(entry["filename"])
        if not path.exists():
            continue
        try:
            reader = PdfReader(str(path))
            start = len(writer.pages)
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue
        head = entry["kind"].upper() + (" " + entry["comp"] if entry["comp"] else "")
        label = f"{head} · {SESSIONS[entry['sess']][0]} {entry['year']}"
        writer.add_outline_item(label.strip(), start)
        if progress:
            progress(entry)

    code = str(cfg["code"])
    years = sorted({int(y) for y in cfg.get("years") or [0]})
    stamp = time.strftime("%H%M%S")
    out = OUTPUT / f"PaperStudio_{code}_{years[0]}_{years[-1]}_{cfg.get('mode', 'qps')}_{stamp}.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def build_separate(cfg: dict, entries: list[dict]) -> list[dict]:
    """Write one PDF per requested kind. Returns [{kind, file, pages}, ...]."""
    code = str(cfg["code"])
    years = sorted({int(y) for y in cfg.get("years") or [0]})
    stamp = time.strftime("%H%M%S")
    files: list[dict] = []
    kinds = cfg.get("separateKinds") or []
    for kind in kinds:
        group = [e for e in entries if e["kind"] == kind]
        if not group:
            continue
        writer = PdfWriter()
        _cover_page(writer, dict(cfg, _entries=group), kind)
        writer.add_outline_item("Cover", 0)
        for entry in group:
            path = _cache_path(entry["filename"])
            if not path.exists():
                continue
            try:
                reader = PdfReader(str(path))
                start = len(writer.pages)
                for page in reader.pages:
                    writer.add_page(page)
            except Exception:
                continue
            head = entry["kind"].upper() + (" " + entry["comp"] if entry["comp"] else "")
            label = f"{head} · {SESSIONS[entry['sess']][0]} {entry['year']}"
            writer.add_outline_item(label.strip(), start)
        if len(writer.pages) < 2:
            # Nothing but the cover: this kind has no downloaded content.
            continue
        out = OUTPUT / f"PaperStudio_{code}_{years[0]}_{years[-1]}_{kind.upper()}_{stamp}.pdf"
        with out.open("wb") as fh:
            writer.write(fh)
        files.append({"kind": kind, "file": out.name,
                      "pages": len(PdfReader(str(out)).pages)})
    return files


def _gt_session(sess: str, year: int) -> str:
    return f"{SESSIONS[sess][0]} {year}"


# --- A* grade-threshold database -------------------------------------------

GT_DB = CACHE / "gt_db.json"
_GT_LOCK = threading.Lock()

# Rows differ by era. Grade values are extracted only after the syllabus table
# header, so component rows ("Component 11 40 29 24 18 16 15") never leak in.
_GT_HEADER = re.compile(r"Combination of\s+components", re.I)
# 2018-2021: "X 11, 21 180 149 118 87 66 46" (max mark only in prose)
_GT_ROW_OLD = re.compile(
    r"^\s*[A-Z]{1,2}\s+(\d{1,2}(?:\s*,\s*\d{1,2}){0,4})\s+"
    r"((?:\d{1,3}\s+){4,6}\d{1,3})\s*$"
)
# 2022-2025: "AY 200 12, 22 175 150 109 68 54 40"
_GT_ROW_MID = re.compile(
    r"^\s*[A-Z]{1,2}\s+(\d{2,3})\s+(\d{1,2}(?:\s*,\s*\d{1,2}){0,4})\s+"
    r"((?:\d{1,3}\s+){4,6}\d{1,3})\s*$"
)
# 2026: "12, 22 200 173 147 104 62 49 37" and single component "50 90 78 ..."
_GT_ROW_NEW = re.compile(
    r"^\s*(\d{1,2}(?:\s*,\s*\d{1,2}){0,4})\s+(\d{2,3})\s+"
    r"((?:\d{1,3}\s+){4,6}\d{1,3})\s*$"
)
_GT_MAX_PROSE = re.compile(
    r"maximum\s+total\s+mark.{0,160}?is\s+(\d{2,3})", re.I | re.S
)
PARSER_VERSION = 2


def _gt_rows_from_text(text: str) -> list[dict]:
    """Tolerant parse of the syllabus-grade table. Never invents an A* value."""
    marker = _GT_HEADER.search(text)
    body = text[marker.end():] if marker else text
    prose = _GT_MAX_PROSE.search(text)
    prose_max = int(prose.group(1)) if prose else None
    rows: list[dict] = []
    for line in body.splitlines():
        m = _GT_ROW_NEW.match(line)
        if m:
            option_raw, max_mark, grade_blob = m.group(1), int(m.group(2)), m.group(3)
        else:
            m = _GT_ROW_MID.match(line)
            if m:
                option_raw, max_mark, grade_blob = m.group(2), int(m.group(1)), m.group(3)
            else:
                m = _GT_ROW_OLD.match(line)
                if not m or prose_max is None:
                    continue
                option_raw, max_mark, grade_blob = m.group(1), prose_max, m.group(2)
        option = ", ".join(part.strip() for part in option_raw.split(","))
        grades = [int(n) for n in grade_blob.split()]
        if not 5 <= len(grades) <= 6 or max_mark <= 0 or max(grades) > max_mark:
            continue
        # 6 grade columns = (A*, A, B, C, D, E). 5 = no A* column at all.
        a_star = grades[0] if len(grades) == 6 else None
        rows.append({
            "option": option,
            "a_star": a_star,
            "max_mark": max_mark,
            "pct": round(a_star / max_mark * 100, 1) if a_star is not None else None,
        })
    return rows


_GT_NAME = re.compile(r"^(?P<code>\d{4})_(?P<sess>[sw])(?P<year>\d{2})_gt\.pdf$", re.I)


def parse_gt(path: Path) -> list[dict]:
    """Return [{year, session, option, a_star, max_mark, pct}, ...] for one GT PDF."""
    m = _GT_NAME.match(path.name)
    if not m:
        return []
    sess = m.group("sess").lower()
    year = 2000 + int(m.group("year"))
    try:
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return []
    return [dict(row, year=year, session=_gt_session(sess, year))
            for row in _gt_rows_from_text(text)]


def _gt_db_load() -> dict:
    try:
        data = json.loads(GT_DB.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("_parser") != PARSER_VERSION:
        # Old parser output stays on disk but the PDFs are re-read from cache.
        return {}
    return data


def _gt_db_save(db: dict) -> None:
    db["_parser"] = PARSER_VERSION
    tmp = GT_DB.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(db), encoding="utf-8")
        os.replace(tmp, GT_DB)
    except OSError:
        pass


def gt_database(code: str, from_year: int, to_year: int,
                sessions: list[str], refresh: bool = False) -> dict:
    """Fetch missing GT PDFs (4 workers) and return the parsed table."""
    sessions = [s for s in ("s", "w") if s in sessions] or ["s"]
    _, (_, papa_slug, co_slug, _) = subject(code)
    wanted = [(year, sess) for year in range(from_year, to_year + 1) for sess in sessions]

    with _GT_LOCK:
        db = _gt_db_load()

    parsed: list[dict] = []
    missing: list[str] = []
    fetched = cached = 0
    to_fetch: list[tuple[int, str, Path]] = []

    for year, sess in wanted:
        name = filename(code, sess, year, "gt")
        path = _cache_path(name)
        meta = db.get(name) if not refresh else None
        if isinstance(meta, dict):
            try:
                stat = path.stat()
            except OSError:
                meta = None
            else:
                if (meta.get("size") != stat.st_size
                        or meta.get("mtime") != int(stat.st_mtime)):
                    meta = None
        if isinstance(meta, dict) and isinstance(meta.get("rows"), list):
            cached += 1
            parsed.extend(dict(r, year=year, session=_gt_session(sess, year))
                          for r in meta["rows"])
            continue
        to_fetch.append((year, sess, path))

    def fetch_one(item):
        year, sess, path = item
        status, value = fetch_pdf(code, sess, year, "gt", None, papa_slug, co_slug)
        return year, sess, path, status, value

    if to_fetch:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(fetch_one, to_fetch))
        fresh = {}
        for year, sess, path, status, value in results:
            if status == "missing" or not path.exists():
                missing.append(filename(code, sess, year, "gt"))
                continue
            if status == "ok":
                fetched += 1
            else:
                cached += 1
            rows = parse_gt(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            fresh[path.name] = {
                "size": stat.st_size, "mtime": int(stat.st_mtime), "rows": rows,
            }
            parsed.extend(dict(r, year=year, session=_gt_session(sess, year)) for r in rows)
        if fresh:
            with _GT_LOCK:
                db = _gt_db_load()
                db.update(fresh)
                _gt_db_save(db)

    parsed.sort(key=lambda r: (-r["year"], r["session"], r["option"]))
    return {
        "rows": parsed,
        "counts": {"fetched": fetched, "cached": cached, "missing": len(missing)},
        "missing": missing,
    }

def _as_year(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_config(payload: dict) -> dict:
    from_year = _as_year(payload.get("from"), 2020)
    to_year = _as_year(payload.get("to"), 2026)
    if from_year > to_year:
        from_year, to_year = to_year, from_year
    lo, hi = 2016, 2026
    from_year = max(lo, min(hi, from_year))
    to_year = max(lo, min(hi, to_year))
    if from_year > to_year:
        from_year = to_year
    code = str(payload.get("code", "4024")).strip()
    if not re.fullmatch(r"\d{4}", code):
        code = "4024"
    asked = {str(k) for k in (payload.get("separateKinds") or [])}
    return {
        "code": code,
        "papers": payload.get("papers") or [1],
        "variants": [str(v) for v in (payload.get("variants") or ["1"])],
        "sessions": payload.get("sessions") or ["s"],
        "years": list(range(from_year, to_year + 1)),
        "mode": payload.get("mode") if payload.get("mode") in MODES else "qps_then_ms",
        "gt": bool(payload.get("gt")),
        "er": bool(payload.get("er")),
        "separateKinds": [k for k in SEPARATE_KINDS if k in asked],
        "student": str(payload.get("student") or "")[:48],
    }


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.busy = False
        self.cancel = threading.Event()
        self.last_ping = time.time()
        self.stopping = threading.Event()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PaperStudio/1.0"

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 2_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _ping(self) -> None:
        self.server.last_ping = time.time()

    def do_GET(self):
        parts = urlparse(self.path)
        path = parts.path
        if path in ("/", "/index.html"):
            html = (ROOT / "index.html").read_bytes()
            self._ping()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/open-output":
            self._ping()
            query = parse_qs(parts.query)
            if (query.get("folder") or [""])[0] == "1":
                target = OUTPUT
            else:
                name = (query.get("file") or [""])[0]
                if not name or "/" in name or "\\" in name or ".." in name:
                    self._json(400, {"ok": False, "error": "bad filename"})
                    return
                target = (OUTPUT / name).resolve()
                if target.parent != OUTPUT.resolve() or not target.exists():
                    self._json(400, {"ok": False, "error": "not in output folder"})
                    return
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(target))  # noqa: S606 - local desktop app
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except OSError as exc:
                self._json(500, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parts = urlparse(self.path)
        path = parts.path
        self._ping()

        if path == "/api/ping":
            self._json(200, {"ok": True})
            return

        if path == "/api/plan":
            payload = self._read_json()
            cfg = validate_config(payload)
            entries = plan_entries(cfg)
            self._json(200, {"entries": entries})
            return

        if path == "/api/upload":
            name = (parse_qs(parts.query).get("name") or [""])[0]
            if not re.fullmatch(r"[\w.\- ]+\.pdf", name):
                self._json(400, {"ok": False, "error": "invalid filename"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if not body.startswith(b"%PDF-"):
                self._json(400, {"ok": False, "error": "not a PDF"})
                return
            _store(name, body)
            self._json(200, {"ok": True, "matched": True, "filename": name, "bytes": len(body)})
            return

        if path == "/api/clear-cache":
            if self.server.busy:
                self._json(409, {"ok": False, "error": "a build is running"})
                return
            removed = 0
            # Only PDFs: cache/gt_db.json (parsed thresholds) must survive.
            for item in CACHE.glob("*.pdf"):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
            self._json(200, {"ok": True, "removed": removed})
            return

        if path == "/api/cancel-build":
            # Safe to call when nothing is running: the flag is cleared on
            # the next build, and the UI only shows the button mid-build.
            self.server.cancel.set()
            self._json(200, {"ok": True, "busy": self.server.busy})
            return

        if path == "/api/build-booklet":
            payload = self._read_json()
            cfg = validate_config(payload)
            if self.server.busy:
                self._json(409, {"ok": False, "error": "a build is already running"})
                return
            self._stream_build(cfg, payload)
            return

        if path == "/api/gt-database":
            payload = self._read_json()
            code = str(payload.get("code") or "").strip()
            if not re.fullmatch(r"\d{4}", code):
                self._json(400, {"ok": False, "error": "code must be 4 digits"})
                return
            from_year = _as_year(payload.get("from"), 2016)
            to_year = _as_year(payload.get("to"), 2026)
            if from_year > to_year:
                from_year, to_year = to_year, from_year
            from_year = max(2016, min(2026, from_year))
            to_year = max(2016, min(2026, to_year))
            sessions = payload.get("sessions") or ["s"]
            try:
                result = gt_database(code, from_year, to_year,
                                     [str(s) for s in sessions],
                                     bool(payload.get("refresh")))
            except Exception as exc:  # noqa: BLE001 - report to the UI
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            self._json(200, dict(result, ok=True))
            return

        self._json(404, {"ok": False, "error": "not found"})

    def _stream_build(self, cfg: dict, payload: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        events: queue.Queue = queue.Queue()

        def emit(kind: str, **data):
            events.put(dict(data, event=kind))

        def cancelled() -> bool:
            return self.server.cancel.is_set()

        def work():
            try:
                entries = plan_entries(cfg)
                if cancelled():
                    emit("cancelled", counts={"fetched": 0, "cached": 0, "missing": 0})
                    return
                uploaded = [str(n) for n in (payload.get("localFiles") or []) if (CACHE / str(n)).exists()]
                for name in uploaded:
                    if not any(e["filename"] == name for e in entries):
                        entries.append({
                            "kind": "local", "comp": None, "year": 0, "sess": "s",
                            "filename": name,
                        })
                emit("plan", entries=[e["filename"] for e in entries])
                _, (_, papa_slug, co_slug, _) = subject(str(cfg["code"]))
                code = str(cfg["code"])

                # Pass 1: disk hits answer instantly so the UI fills in order.
                counts = {"fetched": 0, "cached": 0, "missing": 0}
                pending = []
                for entry in entries:
                    if cancelled():
                        emit("cancelled", counts=dict(counts))
                        return
                    if entry["kind"] == "local":
                        counts["cached"] += 1
                        emit("cache", filename=entry["filename"])
                        continue
                    if cache_hit(code, entry["sess"], int(entry["year"]),
                                 entry["kind"], entry["comp"]):
                        counts["cached"] += 1
                        emit("cache", filename=entry["filename"])
                    else:
                        pending.append(entry)

                # Pass 2: only the misses go to the network, four at a time.
                def task(entry):
                    if cancelled():
                        return "cancelled", None
                    emit("fetch", filename=entry["filename"])
                    try:
                        return fetch_pdf(code, entry["sess"], int(entry["year"]),
                                         entry["kind"], entry["comp"],
                                         papa_slug, co_slug)
                    except Exception as exc:  # noqa: BLE001 - one paper must not kill the build
                        return "missing", f"{type(exc).__name__}: {exc}"

                if pending:
                    pool = ThreadPoolExecutor(max_workers=4)
                    try:
                        futures = {pool.submit(task, e): e for e in pending}
                        for future in as_completed(futures):
                            if cancelled():
                                break
                            entry = futures[future]
                            try:
                                status, value = future.result()
                            except Exception as exc:  # noqa: BLE001
                                status, value = "missing", f"{type(exc).__name__}: {exc}"
                            if status == "cancelled":
                                continue
                            if status == "cache":
                                counts["cached"] += 1
                                emit("cache", filename=entry["filename"],
                                     counts=dict(counts))
                            elif status == "ok":
                                counts["fetched"] += 1
                                emit("ok", filename=entry["filename"],
                                     counts=dict(counts))
                            else:
                                counts["missing"] += 1
                                emit("miss", filename=entry["filename"], reason=value,
                                     counts=dict(counts))
                    finally:
                        # Don't wait out in-flight downloads on cancel; the
                        # .tmp file is discarded by the worker, not replaced.
                        pool.shutdown(wait=False, cancel_futures=True)

                if cancelled():
                    emit("cancelled", counts=dict(counts))
                    return

                # Merge still walks `entries` in plan order, so pages and
                # bookmarks stay Year -> May/June before Oct/Nov -> Component.
                emit("merge", files=len(entries))
                if cfg.get("separateKinds"):
                    files = build_separate(cfg, entries)
                    emit("done", file="", pages=sum(f["pages"] for f in files),
                         files=files, counts=counts)
                else:
                    out = build_booklet(cfg, entries)
                    emit("done", file=out.name, pages=len(PdfReader(str(out)).pages),
                         files=[], counts=counts)
            except Exception as exc:  # noqa: BLE001 - report to the UI
                emit("error", message=f"{type(exc).__name__}: {exc}")
            finally:
                events.put(None)

        self.server.cancel.clear()
        self.server.busy = True
        threading.Thread(target=work, daemon=True).start()
        try:
            while True:
                item = events.get()
                if item is None:
                    break
                self.wfile.write((json.dumps(item) + "\n").encode("utf-8"))
                self.wfile.flush()
                if item.get("event") == "fetch":
                    self.server.last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError):
            # The client went away (closed tab or aborted fetch), so stop the
            # worker instead of finishing a booklet nobody will see.
            self.server.cancel.set()
        finally:
            self.server.busy = False


def idle_watchdog(server: Server) -> None:
    while not server.stopping.wait(5):
        if not server.busy and time.time() - server.last_ping > 90:
            server.stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
            return


def launch_app(url: str) -> subprocess.Popen | None:
    profile = Path(tempfile.gettempdir()) / "paper-studio"
    flags = [
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--window-size=1280,860",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    for exe in ("msedge", "chrome"):
        found = shutil.which(exe)
        if found:
            try:
                return subprocess.Popen([found, *flags])
            except OSError:
                continue
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for rel in (
            r"Microsoft\Edge\Application\msedge.exe",
            r"Google\Chrome\Application\chrome.exe",
        ):
            candidate = Path(base) / rel
            if candidate.exists():
                try:
                    return subprocess.Popen([str(candidate), *flags])
                except OSError:
                    continue
    webbrowser.open(url)
    return None


def main() -> None:
    server = Server(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=idle_watchdog, args=(server,), daemon=True).start()
    print(f"Paper Studio running at {url}")
    proc = launch_app(url)
    try:
        if proc is not None:
            proc.wait()
        else:
            while not server.stopping.wait(1):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        print("Paper Studio stopped.")


if __name__ == "__main__":
    main()
