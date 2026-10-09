import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.stage1 import inventory, make_source_archive, run_baseline, source_files


class StageOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pdf").mkdir()
        (self.root / "latex").mkdir()
        (self.root / "pdf" / "2511.02841v2.pdf").write_bytes(b"%PDF-1.7\nfixture")
        (self.root / "pdf" / "2504.10915v2.pdf").write_bytes(b"%PDF-1.7\nfixture")
        self.source = self.root / "latex" / "2511.02841v2"
        self.source.mkdir()
        (self.source / "main.tex").write_text(
            "% \\documentclass{ignored}\n"
            "\\documentclass{article}\n"
            "\\begin{document}\nHello \\% world.\\end{document}\n",
            encoding="utf-8",
        )
        (self.source / "figure.png").write_bytes(b"image-fixture")
        (self.source / "source.tar.gz").write_bytes(b"original-archive")

    def test_inventory_matches_papers_and_finds_main(self):
        data = inventory(self.root)
        by_id = {entry["id"]: entry for entry in data["papers"]}
        self.assertEqual(data["counts"], {"pdfs": 2, "ready": 1, "no_source": 1})
        self.assertEqual(by_id["2511.02841v2"]["main_tex"], "main.tex")
        self.assertEqual(by_id["2511.02841v2"]["source_files"], 2)
        self.assertEqual(by_id["2504.10915v2"]["status"], "no_source")

    def test_archive_keeps_project_structure_without_download_archive(self):
        payload = make_source_archive(self.source, source_files(self.source))
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:bz2") as archive:
            self.assertEqual(sorted(archive.getnames()), ["figure.png", "main.tex"])

    def test_baseline_persists_pdf_and_skips_same_source(self):
        manifest = inventory(self.root)
        output_dir = self.root / "results" / "stage1"
        with patch("arxiv_translate.stage1.online_compile", return_value=(200, b"%PDF-1.7\ncompiled", "application/pdf")) as compiler:
            first = run_baseline(self.root, manifest, output_dir, ids=None,
                                 endpoint="https://example.test/data", timeout=3, delay=0, force=False)
            second = run_baseline(self.root, manifest, output_dir, ids=None,
                                  endpoint="https://example.test/data", timeout=3, delay=0, force=False)
        self.assertEqual(compiler.call_count, 1)
        self.assertEqual(first["papers"]["2511.02841v2"]["status"], "compiled")
        self.assertEqual(second["papers"]["2504.10915v2"]["status"], "no_source")
        self.assertTrue((output_dir / "compiled" / "2511.02841v2.pdf").is_file())
        self.assertEqual(json.loads((output_dir / "baseline.json").read_text(encoding="utf-8"))["papers"]["2511.02841v2"]["status"], "compiled")

    def test_compiler_failure_retains_log(self):
        manifest = inventory(self.root)
        output_dir = self.root / "results" / "stage1"
        with patch("arxiv_translate.stage1.online_compile", return_value=(400, b"Undefined control sequence", "text/plain")):
            result = run_baseline(self.root, manifest, output_dir, ids={"2511.02841v2"},
                                  endpoint="https://example.test/data", timeout=3, delay=0, force=False)
        self.assertEqual(result["papers"]["2511.02841v2"]["status"], "compile_failed")
        self.assertIn("Undefined control sequence", (output_dir / "logs" / "2511.02841v2.txt").read_text(encoding="utf-8"))

    def test_service_limit_and_timeout_are_not_source_errors(self):
        manifest = inventory(self.root)
        output_dir = self.root / "results" / "stage1"
        with patch("arxiv_translate.stage1.online_compile", return_value=(413, b"Request Entity Too Large", "text/html")):
            limited = run_baseline(self.root, manifest, output_dir, ids={"2511.02841v2"},
                                   endpoint="https://example.test/data", timeout=3, delay=0, force=False)
        self.assertEqual(limited["papers"]["2511.02841v2"]["status"], "service_limit")
        with patch("arxiv_translate.stage1.online_compile", side_effect=TimeoutError("The read operation timed out")):
            timed_out = run_baseline(self.root, manifest, output_dir, ids={"2511.02841v2"},
                                     endpoint="https://example.test/data", timeout=3, delay=0, force=False)
        self.assertEqual(timed_out["papers"]["2511.02841v2"]["status"], "network_error")


if __name__ == "__main__":
    unittest.main()
