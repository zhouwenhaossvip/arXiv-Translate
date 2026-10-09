import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.local_compiler import compile_local, probe_latexmk
from arxiv_translate.stage1 import first_error_line, inventory, main, run_local_baseline


class LocalCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pdf").mkdir()
        (self.root / "latex").mkdir()
        (self.root / "pdf" / "2511.02841v2.pdf").write_bytes(b"%PDF-1.7\nfixture")
        self.source = self.root / "latex" / "2511.02841v2"
        self.source.mkdir()
        (self.source / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}Hello\\end{document}\n", encoding="utf-8"
        )
        self.output = self.root / "results" / "stage1" / "local"

    def test_probe_reports_missing_or_uninitialized_installation(self):
        with patch("arxiv_translate.local_compiler.shutil.which", return_value=None):
            self.assertIsNone(probe_latexmk("latexmk")[0])
        with patch("arxiv_translate.local_compiler.shutil.which", return_value="latexmk.exe"), \
             patch("arxiv_translate.local_compiler.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 1, b"Finish the setup")):
            self.assertIn("尚不可用", probe_latexmk("latexmk")[1])
        with patch("arxiv_translate.local_compiler.shutil.which", return_value="latexmk.exe"), \
             patch("arxiv_translate.local_compiler.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 1, b"It seems that this is a fresh TeX installation.")):
            self.assertIn("首次设置", probe_latexmk("latexmk")[1])

    def test_local_compile_uses_disposable_copy_and_appropriate_engine(self):
        for engine, expected in [("pdflatex", "-pdf"), ("xelatex", "-xelatex"), ("lualatex", "-lualatex")]:
            with self.subTest(engine=engine):
                def run(command, **kwargs):
                    self.assertIn(expected, command)
                    self.assertIn("-no-shell-escape", command)
                    self.assertIn("-norc", command)
                    self.assertNotIn("-bibtex-", command)
                    self.assertNotEqual(Path(kwargs["cwd"]), self.source)
                    (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
                    return subprocess.CompletedProcess(command, 0)

                pdf = self.output / "compiled" / "2511.02841v2.pdf"
                log = self.output / "logs" / "2511.02841v2.txt"
                with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=run):
                    result = compile_local(self.source, [self.source / "main.tex"], "main.tex",
                                           engine, "latexmk", 3, pdf, log)
                self.assertEqual(result["status"], "compiled")
                self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
                self.assertFalse((self.source / "main.pdf").exists())

    def test_cas_thumbnail_is_restored_only_in_temporary_copy(self):
        (self.source / "main.tex").write_text(
            "\\documentclass[a4paper]{cas-dc}\n\\begin{document}Test\\end{document}\n", encoding="utf-8"
        )

        def run(command, **kwargs):
            thumbnail = Path(kwargs["cwd"]) / "thumbnails" / "cas-email.jpeg"
            self.assertTrue(thumbnail.read_bytes().startswith(b"\xff\xd8"))
            (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
            return subprocess.CompletedProcess(command, 0)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=run):
            result = compile_local(self.source, [self.source / "main.tex"], "main.tex",
                                   "pdflatex", "latexmk", 3,
                                   self.output / "compiled" / "2511.02841v2.pdf",
                                   self.output / "logs" / "2511.02841v2.txt")
        self.assertIn("restored_ctan_cas_email_thumbnail", result["compatibility_actions"])
        self.assertFalse((self.source / "thumbnails").exists())

    def test_tikz_label_is_repaired_only_in_temporary_copy(self):
        figures = self.source / "figures"
        figures.mkdir()
        figure = figures / "a2a-attestation.tex"
        original_label = r"{co-located: \KerA${} = {}$\KerB}"
        figure.write_text(original_label, encoding="utf-8")

        def run(command, **kwargs):
            patched = (Path(kwargs["cwd"]) / "figures" / "a2a-attestation.tex").read_text(encoding="utf-8")
            self.assertIn(r"{co-located: Kernel$_A$ = Kernel$_B$}", patched)
            (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
            return subprocess.CompletedProcess(command, 0)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=run):
            result = compile_local(self.source, [self.source / "main.tex", figure], "main.tex",
                                   "pdflatex", "latexmk", 3,
                                   self.output / "compiled" / "2511.02841v2.pdf",
                                   self.output / "logs" / "2511.02841v2.txt")
        self.assertEqual(result["status"], "compiled")
        self.assertIn("normalized_tikz_node_label", result["compatibility_actions"])
        self.assertEqual(figure.read_text(encoding="utf-8"), original_label)

    def test_direct_bbl_input_disables_bibtex(self):
        (self.source / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}Test\\input{main.bbl}\\end{document}\n", encoding="utf-8"
        )
        bbl = self.source / "main.bbl"
        bbl.write_text("\\begin{thebibliography}{1}\\end{thebibliography}\n", encoding="utf-8")

        def run(command, **kwargs):
            self.assertIn("-bibtex-", command)
            self.assertTrue((Path(kwargs["cwd"]) / "main.bbl").is_file())
            (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
            return subprocess.CompletedProcess(command, 0)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=run):
            result = compile_local(self.source, [self.source / "main.tex", bbl], "main.tex",
                                   "pdflatex", "latexmk", 3,
                                   self.output / "compiled" / "2511.02841v2.pdf",
                                   self.output / "logs" / "2511.02841v2.txt")
        self.assertIn("preserved_bundled_bbl_without_bibtex", result["compatibility_actions"])
        self.assertTrue(bbl.is_file())

    def test_bibliography_command_preserves_bundled_bbl(self):
        (self.source / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}Text\\cite{real}"
            "\\bibliographystyle{plain}\\bibliography{references}\\end{document}\n",
            encoding="utf-8",
        )
        bbl = self.source / "main.bbl"
        bbl.write_text("\\begin{thebibliography}{1}\n\\bibitem{real}Real source\n"
                       "\\end{thebibliography}\n", encoding="utf-8")

        def run(command, **kwargs):
            self.assertIn("-bibtex-", command)
            self.assertEqual((Path(kwargs["cwd"]) / "main.bbl").read_bytes(), bbl.read_bytes())
            (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
            return subprocess.CompletedProcess(command, 0)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=run):
            result = compile_local(self.source, [self.source / "main.tex", bbl], "main.tex",
                                   "pdflatex", "latexmk", 3,
                                   self.output / "compiled" / "2511.02841v2.pdf",
                                   self.output / "logs" / "2511.02841v2.txt")
        self.assertEqual(result["status"], "compiled")
        self.assertIn("preserved_bundled_bbl_without_bibtex", result["compatibility_actions"])

    def test_overwritten_bbl_or_unresolved_citations_fail_validation(self):
        bbl = self.source / "main.bbl"
        bbl.write_text("\\begin{thebibliography}{1}\\bibitem{real}Real\\end{thebibliography}\n",
                       encoding="utf-8")
        pdf = self.output / "compiled" / "2511.02841v2.pdf"
        log = self.output / "logs" / "2511.02841v2.txt"

        def overwrite(command, **kwargs):
            (Path(kwargs["cwd"]) / "main.bbl").write_text("", encoding="utf-8")
            (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
            return subprocess.CompletedProcess(command, 0)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=overwrite):
            result = compile_local(self.source, [self.source / "main.tex", bbl], "main.tex",
                                   "pdflatex", "latexmk", 3, pdf, log)
        self.assertEqual(result["status"], "validation_failed")
        self.assertFalse(pdf.exists())

        def unresolved(command, **kwargs):
            kwargs["stdout"].write(b"Latex failed to resolve 2 citation(s)\n")
            (Path(kwargs["cwd"]) / "main.pdf").write_bytes(b"%PDF-1.7\ncompiled")
            return subprocess.CompletedProcess(command, 0)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=unresolved):
            result = compile_local(self.source, [self.source / "main.tex", bbl], "main.tex",
                                   "pdflatex", "latexmk", 3, pdf, log)
        self.assertEqual(result["status"], "validation_failed")
        self.assertIn("未解析", result["first_error"])
        self.assertFalse(pdf.exists())

    def test_failure_and_timeout_keep_logs_without_pdf(self):
        pdf = self.output / "compiled" / "2511.02841v2.pdf"
        log = self.output / "logs" / "2511.02841v2.txt"

        def fail(command, **kwargs):
            kwargs["stdout"].write(b"! Undefined control sequence\n")
            return subprocess.CompletedProcess(command, 12)

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=fail):
            result = compile_local(self.source, [self.source / "main.tex"], "main.tex",
                                   "pdflatex", "latexmk", 3, pdf, log)
        self.assertEqual(result["status"], "compile_failed")
        self.assertIn("Undefined", log.read_text(encoding="utf-8"))
        self.assertFalse(pdf.exists())

        with patch("arxiv_translate.local_compiler.subprocess.run", side_effect=subprocess.TimeoutExpired("latexmk", 3)):
            result = compile_local(self.source, [self.source / "main.tex"], "main.tex",
                                   "pdflatex", "latexmk", 3, pdf, log)
        self.assertEqual(result["status"], "timeout")
        self.assertIn("超过 3 秒", log.read_text(encoding="utf-8"))

    def test_baseline_records_and_caches_local_results(self):
        manifest = inventory(self.root)
        def fake_compile(source_dir, files, main_tex, engine, executable, timeout, pdf_path, log_path):
            log_path.write_text("Latexmk finished\n", encoding="utf-8")
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.7\ncompiled")
            return {"status": "compiled", "output_pdf_bytes": pdf_path.stat().st_size}
        with patch("arxiv_translate.stage1.probe_latexmk", return_value=("latexmk.exe", "Latexmk 4.88")), \
             patch("arxiv_translate.stage1.compile_local", side_effect=fake_compile) as compiler:
            first = run_local_baseline(self.root, manifest, self.output, ids=None,
                                       latexmk="latexmk", timeout=3, force=False)
            second = run_local_baseline(self.root, manifest, self.output, ids=None,
                                        latexmk="latexmk", timeout=3, force=False)
        self.assertEqual(compiler.call_count, 1)
        self.assertEqual(first["papers"]["2511.02841v2"]["status"], "compiled")
        self.assertEqual(second["papers"]["2511.02841v2"]["status"], "compiled")
        self.assertEqual(json.loads((self.output / "baseline.json").read_text(encoding="utf-8"))["compiler"], "local-latexmk")

    def test_failed_baseline_retries_without_force(self):
        manifest = inventory(self.root)
        attempts = 0

        def fake_compile(source_dir, files, main_tex, engine, executable, timeout, pdf_path, log_path):
            nonlocal attempts
            attempts += 1
            log_path.write_text("main.tex:3: Undefined control sequence.\n", encoding="utf-8")
            if attempts == 1:
                return {"status": "compile_failed"}
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.7\ncompiled")
            return {"status": "compiled", "output_pdf_bytes": pdf_path.stat().st_size}

        with patch("arxiv_translate.stage1.probe_latexmk", return_value=("latexmk.exe", "Latexmk 4.88")), \
             patch("arxiv_translate.stage1.compile_local", side_effect=fake_compile):
            run_local_baseline(self.root, manifest, self.output, ids=None,
                               latexmk="latexmk", timeout=3, force=False)
            final = run_local_baseline(self.root, manifest, self.output, ids=None,
                                       latexmk="latexmk", timeout=3, force=False)
        self.assertEqual(attempts, 2)
        self.assertEqual(final["papers"]["2511.02841v2"]["status"], "compiled")

    def test_failed_validation_archives_old_compiled_pdf(self):
        manifest = inventory(self.root)
        old_pdf = self.output / "compiled" / "2511.02841v2.pdf"
        old_pdf.parent.mkdir(parents=True, exist_ok=True)
        old_pdf.write_bytes(b"%PDF-1.7\nold")

        def fake_compile(source_dir, files, main_tex, engine, executable, timeout, pdf_path, log_path):
            log_path.write_text("Latex failed to resolve 1 citation(s)\n", encoding="utf-8")
            return {"status": "validation_failed", "first_error": "未解析的文献引用"}

        with patch("arxiv_translate.stage1.probe_latexmk", return_value=("latexmk.exe", "Latexmk 4.88")), \
             patch("arxiv_translate.stage1.compile_local", side_effect=fake_compile):
            result = run_local_baseline(self.root, manifest, self.output, ids=None,
                                        latexmk="latexmk", timeout=3, force=False)
        paper = result["papers"]["2511.02841v2"]
        self.assertEqual(paper["status"], "validation_failed")
        self.assertFalse(old_pdf.exists())
        archived = self.root / paper["previous_output_pdf"]
        self.assertEqual(archived.read_bytes(), b"%PDF-1.7\nold")

    def test_cli_returns_failure_when_validation_fails(self):
        def fake_compile(source_dir, files, main_tex, engine, executable, timeout, pdf_path, log_path):
            log_path.write_text("Latex failed to resolve 1 citation(s)\n", encoding="utf-8")
            return {"status": "validation_failed", "first_error": "未解析的文献引用"}

        with patch("arxiv_translate.stage1.probe_latexmk", return_value=("latexmk.exe", "Latexmk 4.88")), \
             patch("arxiv_translate.stage1.compile_local", side_effect=fake_compile):
            self.assertEqual(main(["baseline", "--root", str(self.root)]), 1)

    def test_page_count_mismatch_is_flagged(self):
        manifest = inventory(self.root)

        def fake_compile(source_dir, files, main_tex, engine, executable, timeout, pdf_path, log_path):
            log_path.write_text("Latexmk finished\n", encoding="utf-8")
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.7\ncompiled")
            return {"status": "compiled", "output_pdf_bytes": pdf_path.stat().st_size}

        with patch("arxiv_translate.stage1.probe_latexmk", return_value=("latexmk.exe", "Latexmk 4.88")), \
             patch("arxiv_translate.stage1.compile_local", side_effect=fake_compile), \
             patch("arxiv_translate.stage1.pdf_page_count", side_effect=[18, 16]):
            result = run_local_baseline(self.root, manifest, self.output, ids=None,
                                        latexmk="latexmk", timeout=3, force=False)
        self.assertFalse(result["papers"]["2511.02841v2"]["page_count_match"])
        self.assertIn("18/16 ⚠", (self.output / "summary.md").read_text(encoding="utf-8"))

    def test_error_summary_ignores_console_banner(self):
        log = "Initial Win CP for (console input, console output, system): (CP936, CP65001, CP936)\n!\nmain.tex:3: Undefined control sequence.\n"
        self.assertEqual(first_error_line(log), "main.tex:3: Undefined control sequence.")

    def test_baseline_does_not_mislabel_missing_compiler_as_source_failure(self):
        manifest = inventory(self.root)
        with patch("arxiv_translate.stage1.probe_latexmk", return_value=(None, "not initialized")):
            result = run_local_baseline(self.root, manifest, self.output, ids=None,
                                        latexmk="latexmk", timeout=3, force=False)
        self.assertEqual(result["papers"]["2511.02841v2"]["status"], "compiler_unavailable")
        self.assertIn("not initialized", (self.output / "logs" / "2511.02841v2.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
