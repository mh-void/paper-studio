"""Tests for the individual-PDF export (By Year).

Run from the repo root:
    python -m unittest discover -s "Past Paper Studio/tests" -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def entries_for(*specs: tuple[str, int, str, str]) -> list[dict]:
    return [
        {"kind": kind, "comp": comp, "year": year, "sess": sess,
         "filename": app.filename("4024", sess, year, kind, comp)}
        for kind, comp, year, sess in specs
    ]


class ExportIndividualTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cache = base / "cache"
        self.output = base / "output"
        self.cache.mkdir()
        self.output.mkdir()
        self._cache, self._output = app.CACHE, app.OUTPUT
        app.CACHE, app.OUTPUT = self.cache, self.output

    def tearDown(self):
        app.CACHE, app.OUTPUT = self._cache, self._output
        self.tmp.cleanup()

    def seed(self, name: str, body: bytes = b"%PDF-1.4 test\n") -> Path:
        path = self.cache / name
        path.write_bytes(body)
        return path

    def test_copies_into_year_session_folder(self):
        src = self.seed("4024_s24_qp_11.pdf", b"%PDF-1.4 qp11\n")
        entries = entries_for(("qp", "11", 2024, "s"), ("ms", "11", 2024, "w"))
        self.seed("4024_w24_ms_11.pdf", b"%PDF-1.4 ms11\n")

        cfg = {"code": "4024", "years": [2024]}
        result = app.export_individual(cfg, entries)

        dest = self.output / "4024_Individual" / "2024" / "May-June" / "4024_s24_qp_11.pdf"
        self.assertTrue(dest.exists(), dest)
        self.assertEqual(dest.read_bytes(), src.read_bytes())
        self.assertTrue(
            (self.output / "4024_Individual" / "2024" / "Oct-Nov" / "4024_w24_ms_11.pdf").exists()
        )
        self.assertEqual(result["folder"], "4024_Individual")
        self.assertEqual(result["copied"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(len(result["names"]), 2)

    def test_missing_cache_file_is_skipped_not_fatal(self):
        self.seed("4024_s24_qp_11.pdf")
        entries = entries_for(("qp", "11", 2024, "s"), ("qp", "12", 2024, "s"))
        entries.append({"kind": "local", "comp": None, "year": 0, "sess": "s",
                        "filename": "handed-in.pdf"})

        result = app.export_individual({"code": "4024", "years": [2024]}, entries)

        self.assertEqual(result["copied"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["names"], ["4024_s24_qp_11.pdf"])

    def test_cancel_stops_between_copies(self):
        self.seed("4024_s24_qp_11.pdf")
        self.seed("4024_s24_ms_11.pdf")
        entries = entries_for(("qp", "11", 2024, "s"), ("ms", "11", 2024, "s"))

        calls = [0]

        def cancelled() -> bool:
            calls[0] += 1
            return calls[0] > 1

        result = app.export_individual({"code": "4024", "years": [2024]}, entries,
                                       cancelled=cancelled)

        self.assertEqual(result["copied"], 1)
        self.assertEqual(result["names"], ["4024_s24_qp_11.pdf"])


class ExportEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cache = base / "cache"
        self.output = base / "output"
        self.cache.mkdir()
        self.output.mkdir()
        self._cache, self._output = app.CACHE, app.OUTPUT
        app.CACHE, app.OUTPUT = self.cache, self.output
        (self.cache / "4024_s24_qp_11.pdf").write_bytes(b"%PDF-1.4 qp11\n")

        self.server = app.Server(("127.0.0.1", 0), app.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        app.CACHE, app.OUTPUT = self._cache, self._output
        self.tmp.cleanup()

    def test_stream_ends_with_done_and_writes_folder(self):
        payload = {
            "code": "4024", "papers": [1], "variants": ["1"], "sessions": ["s"],
            "from": 2024, "to": 2024, "mode": "qps", "gt": False, "er": False,
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/export-individual",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.assertEqual(resp.status, 200)
            events = [json.loads(line) for line in resp.read().decode().splitlines() if line.strip()]

        kinds = [e["event"] for e in events]
        self.assertIn("plan", kinds)
        self.assertEqual(kinds[-1], "done", kinds)
        done = events[-1]
        self.assertEqual(done["folder"], "4024_Individual")
        self.assertEqual(done["copied"], 1)
        self.assertTrue(
            (self.output / "4024_Individual" / "2024" / "May-June" / "4024_s24_qp_11.pdf").exists()
        )


if __name__ == "__main__":
    unittest.main()
