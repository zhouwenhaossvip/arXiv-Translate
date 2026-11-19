import io
import json
import os
import re
import tarfile
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.web_app import (MODELS, NoLatexSource, TaskApp, _extract_source,
                                     detect_arxiv_id, download_source, make_handler)
from arxiv_translate.tex_segments import Segment
from http.server import ThreadingHTTPServer


class WebAppTests(unittest.TestCase):
    def test_model_selector_matches_backend_allowlist(self):
        html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        options = re.findall(r'<option value="([^"]+)"', html)
        self.assertEqual(len(options), len(set(options)))
        self.assertEqual(set(options), set(MODELS))
        self.assertEqual(MODELS["deepseek-v4-pro"], "DEEPSEEK_API_KEY")
        self.assertEqual(MODELS["gpt-4.1-mini"], "OPENAI_API_KEY")

    def test_detect_exact_version_from_filename(self):
        with patch("arxiv_translate.web_app.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(detect_arxiv_id(b"%PDF-fake", "2511.02841v2.pdf"), "2511.02841v2")

    def test_detect_rejects_mismatched_pdf_version(self):
        result = type("Result", (), {"returncode": 0, "stdout": b"arXiv:2511.02841v1"})()
        with patch("arxiv_translate.web_app.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "不一致"):
                detect_arxiv_id(b"%PDF-fake", "2511.02841v2.pdf")

    def test_non_arxiv_pdf_gets_actionable_message(self):
        with patch("arxiv_translate.web_app.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, "当前仅支持 arXiv 论文"):
                detect_arxiv_id(b"%PDF-fake", "some-journal-paper.pdf")

    def test_arxiv_pdf_without_version_gets_specific_message(self):
        result = type("Result", (), {"returncode": 0, "stdout": b"arXiv:2511.02841"})()
        with patch("arxiv_translate.web_app.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "缺少版本号"):
                detect_arxiv_id(b"%PDF-fake", "paper.pdf")

    def test_missing_arxiv_source_gets_specific_message(self):
        with tempfile.TemporaryDirectory() as directory:
            error = urllib.error.HTTPError("https://arxiv.org/src/2511.02841v2", 404,
                                           "Not Found", None, None)
            with patch("arxiv_translate.web_app.urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(NoLatexSource, "2511.02841v2.*LaTeX 源码"):
                    download_source(Path(directory), "2511.02841v2")

    def test_pdf_instead_of_source_gets_same_message(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("arxiv_translate.web_app.urllib.request.urlopen",
                       return_value=io.BytesIO(b"%PDF-source-only")):
                with self.assertRaisesRegex(NoLatexSource, "该版本可用的 LaTeX 源码"):
                    download_source(Path(directory), "2511.02841v2")

    def test_missing_source_stops_before_paid_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TaskApp(Path(directory))
            token = "d" * 32
            app.tasks[token] = {"id": token, "paper_id": "2511.02841v2", "model": "deepseek-flash",
                                "status": "working", "step": "preparing", "message": "", "pdf_ready": False}
            app.busy = True
            with (patch("arxiv_translate.web_app.download_source",
                        side_effect=NoLatexSource("该版本没有可用的 LaTeX 源码")),
                  patch("arxiv_translate.web_app.run_translation") as translate):
                app._run(token, b"%PDF-fake", "private-key")
                translate.assert_not_called()
            self.assertEqual(app.public(token)["status"], "failed")
            self.assertIn("LaTeX 源码", app.public(token)["message"])

    def test_source_archive_rejects_path_traversal(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            payload = b"\\documentclass{article}\\begin{document}x\\end{document}"
            member = tarfile.TarInfo("../outside.tex")
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "不安全"):
                _extract_source(archive.getvalue(), Path(directory) / "latex")
            self.assertFalse((Path(directory) / "outside.tex").exists())

    def test_source_archive_extracts_safe_tex(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            payload = b"\\documentclass{article}\\begin{document}x\\end{document}"
            member = tarfile.TarInfo("paper.tex")
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latex"
            _extract_source(archive.getvalue(), target)
            self.assertEqual((target / "paper.tex").read_bytes(), payload)

    def test_page_mismatch_stops_before_paid_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = TaskApp(root)
            token = "c" * 32
            app.tasks[token] = {"id": token, "paper_id": "2511.02841v2", "model": "deepseek-flash",
                                "status": "working", "step": "preparing", "message": "", "pdf_ready": False}
            app.busy = True
            paper = {"id": "2511.02841v2", "status": "ready"}
            with (patch("arxiv_translate.web_app.download_source"),
                  patch("arxiv_translate.web_app.inventory", return_value={"papers": [paper]}),
                  patch("arxiv_translate.web_app.run_local_baseline", return_value={"papers": {paper["id"]: {"status": "compiled", "page_count_match": False}}}),
                  patch("arxiv_translate.web_app.run_translation") as translate):
                app._run(token, b"%PDF-fake", "super-private-key")
                translate.assert_not_called()
            self.assertEqual(app.public(token)["status"], "failed")

    def test_job_runs_pipeline_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "output" / "pdf").mkdir(parents=True)
            output = root / "output" / "pdf" / "result.pdf"
            output.write_bytes(b"%PDF-result")
            checkpoint = root / "results" / "stage2" / "2511.02841v2" / "translations.json"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(json.dumps({"translations": {"test-id": "译文"}}),
                                  encoding="utf-8")
            app = TaskApp(root)
            token = "a" * 32
            app.tasks[token] = {"id": token, "paper_id": "2511.02841v2", "model": "deepseek-flash",
                                "status": "working", "step": "preparing", "message": "", "pdf_ready": False}
            app.busy = True
            paper = {"id": "2511.02841v2", "status": "ready"}
            old_key = os.environ.get("DEEPSEEK_API_KEY")
            with (patch("arxiv_translate.web_app.download_source"),
                  patch("arxiv_translate.web_app.inventory", return_value={"papers": [paper]}),
                  patch("arxiv_translate.web_app.run_local_baseline", return_value={"papers": {paper["id"]: {"status": "compiled"}}}),
                  patch("arxiv_translate.web_app.preflight_chinese_compiler") as preflight,
                  patch("arxiv_translate.web_app.paper_plan", return_value=(paper, root, [
                      Segment("test-id", "main.tex", 0, 5, "Hello")])),
                  patch("arxiv_translate.web_app.run_translation", return_value={"complete": True, "segments_translated": 1}) as translate,
                  patch("arxiv_translate.web_app.compile_paper", return_value={"status": "compiled", "pdf": "output/pdf/result.pdf", "pages": 3}),
                  patch("arxiv_translate.web_app.audit_paper", return_value={"status": "pass", "issues": []})):
                app._run(token, b"%PDF-fake", "super-private-key")
                self.assertEqual(translate.call_args.kwargs["max_new_segments"], None)
                preflight.assert_not_called()
            public = app.public(token)
            self.assertEqual(public["status"], "done")
            self.assertTrue(public["pdf_ready"])
            self.assertNotIn("pdf_path", public)
            self.assertNotIn("super-private-key", json.dumps(public))
            self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), old_key)
            self.assertFalse(app.busy)

    def test_chinese_preflight_stops_before_paid_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = TaskApp(root)
            token = "g" * 32
            paper = {"id": "2511.02841v2", "status": "ready"}
            app.tasks[token] = {"id": token, "paper_id": paper["id"], "model": "deepseek-flash",
                                "status": "working", "step": "preparing", "message": "", "pdf_ready": False}
            app.busy = True
            with (patch("arxiv_translate.web_app.download_source"),
                  patch("arxiv_translate.web_app.inventory", return_value={"papers": [paper]}),
                  patch("arxiv_translate.web_app.run_local_baseline", return_value={"papers": {
                      paper["id"]: {"status": "compiled"}}}),
                  patch("arxiv_translate.web_app.preflight_chinese_compiler",
                        side_effect=RuntimeError("中文编译环境预检未通过：找不到字体")),
                  patch("arxiv_translate.web_app.paper_plan", return_value=(paper, root, [
                      Segment("test-id", "main.tex", 0, 5, "Hello")])),
                  patch("arxiv_translate.web_app.run_translation") as translate):
                app._run(token, b"%PDF-fake", "super-private-key")
                translate.assert_not_called()
            self.assertIn("找不到字体", app.public(token)["message"])
            self.assertFalse(app.busy)

    def test_compiler_failure_shows_latex_error_and_log_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = TaskApp(root)
            token = "e" * 32
            paper = {"id": "2511.02841v2", "status": "ready"}
            app.tasks[token] = {"id": token, "paper_id": paper["id"], "model": "deepseek-flash",
                                "status": "working", "step": "preparing", "message": "", "pdf_ready": False}
            app.busy = True
            with (patch("arxiv_translate.web_app.download_source"),
                  patch("arxiv_translate.web_app.inventory", return_value={"papers": [paper]}),
                  patch("arxiv_translate.web_app.run_local_baseline", return_value={"papers": {
                      paper["id"]: {"status": "compiled"}}}),
                  patch("arxiv_translate.web_app.preflight_chinese_compiler"),
                  patch("arxiv_translate.web_app.paper_plan", return_value=(paper, root, [
                      Segment("test-id", "main.tex", 0, 5, "Hello")])),
                  patch("arxiv_translate.web_app.run_translation", return_value={
                      "complete": True, "segments_translated": 1}),
                  patch("arxiv_translate.web_app.compile_paper", return_value={
                      "status": "compile_failed", "first_error": "main.tex:1809: Missing { inserted.",
                      "log": "results/stage3/2511.02841v2/compile-fontspec.log"})):
                app._run(token, b"%PDF-fake", "super-private-key")
            public = app.public(token)
            self.assertEqual(public["status"], "failed")
            self.assertIn("Missing { inserted", public["message"])
            self.assertIn("compile-fontspec.log", public["message"])
            self.assertNotIn("super-private-key", json.dumps(public))

    def test_completed_pdf_displays_quality_review_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output" / "pdf" / "result.pdf"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"%PDF-result")
            app = TaskApp(root)
            token = "f" * 32
            paper = {"id": "2511.02841v2", "status": "ready"}
            app.tasks[token] = {"id": token, "paper_id": paper["id"], "model": "deepseek-flash",
                                "status": "working", "step": "preparing", "message": "", "pdf_ready": False}
            app.busy = True
            with (patch("arxiv_translate.web_app.download_source"),
                  patch("arxiv_translate.web_app.inventory", return_value={"papers": [paper]}),
                  patch("arxiv_translate.web_app.run_local_baseline", return_value={"papers": {
                      paper["id"]: {"status": "compiled"}}}),
                  patch("arxiv_translate.web_app.preflight_chinese_compiler"),
                  patch("arxiv_translate.web_app.paper_plan", return_value=(paper, root, [
                      Segment("test-id", "main.tex", 0, 5, "Hello")])),
                  patch("arxiv_translate.web_app.run_translation", return_value={
                      "complete": True, "segments_translated": 1}),
                  patch("arxiv_translate.web_app.compile_paper", return_value={
                      "status": "compiled", "pdf": "output/pdf/result.pdf", "pages": 3}),
                  patch("arxiv_translate.web_app.audit_paper", return_value={
                      "status": "needs_review", "issues": [{"code": "untranslated"},
                                                       {"code": "overfull"}]})):
                app._run(token, b"%PDF-fake", "super-private-key")
            public = app.public(token)
            self.assertEqual(public["status"], "done")
            self.assertTrue(public["pdf_ready"])
            self.assertEqual(public["qa_status"], "needs_review")
            self.assertIn("2 类问题需人工复核", public["message"])

    def test_failed_translation_displays_checkpoint_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "results/stage2/2504.21034v2/translations.json"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(json.dumps({"translations": {str(index): "译文"
                                                               for index in range(188)}}),
                                  encoding="utf-8")
            app = TaskApp(root)
            token = "d" * 32
            app.tasks[token] = {"id": token, "paper_id": "2504.21034v2",
                                "model": "deepseek-flash", "status": "failed",
                                "step": "failed", "message": "任务失败",
                                "segments_done": 0, "segments_total": 353,
                                "pdf_ready": False}
            self.assertEqual(app.public(token)["segments_done"], 188)

    def test_http_serves_preview_and_download_without_leaking_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text("<h1>UI</h1>", encoding="utf-8")
            pdf = root / "ready.pdf"
            pdf.write_bytes(b"%PDF-test")
            app = TaskApp(root)
            token = "b" * 32
            app.tasks[token] = {"id": token, "paper_id": "2511.02841v2", "model": "deepseek-flash",
                                "status": "done", "step": "done", "message": "ready", "pdf_ready": True,
                                "pdf_path": str(pdf)}
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port)
                try:
                    connection.request("GET", f"/api/tasks/{token}")
                except PermissionError:
                    self.skipTest("本机沙箱禁止连接回环接口")
                response = connection.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertNotIn(b"pdf_path", body)
                connection.request("GET", f"/api/tasks/{token}/pdf?download=1")
                response = connection.getresponse()
                self.assertEqual(response.read(), b"%PDF-test")
                self.assertIn("attachment", response.getheader("Content-Disposition"))
                connection.request("POST", "/api/tasks", body=b"{}", headers={
                    "Content-Type": "application/json", "Origin": "http://evil.example"
                })
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
