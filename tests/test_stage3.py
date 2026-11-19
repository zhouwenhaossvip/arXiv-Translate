import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.stage1 import inventory
from arxiv_translate.stage3 import (add_chinese_support, classify_missing_characters,
                                    compile_paper, preflight_chinese_compiler,
                                    _first_latex_error)


class StageThreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paper_id = "2608.17220v1"
        (self.root / "pdf").mkdir()
        (self.root / "pdf" / f"{paper_id}.pdf").write_bytes(b"%PDF-1.7\nfixture")
        source = self.root / "latex" / paper_id
        source.mkdir(parents=True)
        original = "\\documentclass{article}\n\\begin{document}English text.\\end{document}\n"
        (source / "main.tex").write_text(original, encoding="utf-8")
        self.entry = inventory(self.root)["papers"][0]
        baseline = self.root / "results" / "stage1" / "local" / "baseline.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text(json.dumps({"papers": {paper_id: {"status": "compiled"}}}), encoding="utf-8")
        self.stage2 = self.root / "results" / "stage2" / paper_id
        translated = self.stage2 / "translated"
        translated.mkdir(parents=True)
        (translated / "main.tex").write_text(original.replace("English text", "中文正文"), encoding="utf-8")
        (self.stage2 / "report.json").write_text(json.dumps({
            "complete": True, "source_sha256": self.entry["source_sha256"], "model": "deepseek-flash",
        }), encoding="utf-8")
        self.paper_id = paper_id

    def test_injection_changes_only_disposable_copy(self):
        original = (self.stage2 / "translated" / "main.tex").read_bytes()
        work = self.root / "work"
        work.mkdir()
        (work / "main.tex").write_text(
            "\\pdfoutput=1\n\\documentclass{article}\n\\begin{document}中文\\end{document}",
            encoding="utf-8",
        )
        actions = add_chinese_support(work, "main.tex", "SimSun")
        compiled_text = (work / "main.tex").read_text(encoding="utf-8")
        self.assertIn(r"\usepackage{xeCJK}", compiled_text)
        self.assertIn(r"\setCJKmainfont{SimSun}", compiled_text)
        self.assertNotIn(r"\pdfoutput=1", compiled_text)
        self.assertIn("disabled_pdflatex_pdfoutput_directive", actions)
        self.assertEqual((self.stage2 / "translated" / "main.tex").read_bytes(), original)
        second_work = self.root / "fontspec-work"
        second_work.mkdir()
        (second_work / "main.tex").write_bytes(original)
        add_chinese_support(second_work, "main.tex", "SimSun", "fontspec")
        fallback_text = (second_work / "main.tex").read_text(encoding="utf-8")
        self.assertIn(r"\XeTeXlinebreaklocale", fallback_text)
        self.assertIn(r"\setmonofont{SimSun}", fallback_text)
        (second_work / "main.tex").write_text(original.decode("utf-8").replace(
            "中文", "中\u200b文"), encoding="utf-8")
        actions = add_chinese_support(second_work, "main.tex", "SimSun", "fontspec")
        self.assertIn("removed_zero_width_spaces", actions)
        self.assertNotIn("\u200b", (second_work / "main.tex").read_text(encoding="utf-8"))

    def test_compile_requires_complete_stage2(self):
        report = self.stage2 / "report.json"
        value = json.loads(report.read_text(encoding="utf-8"))
        value["complete"] = False
        report.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            compile_paper(self.root, self.paper_id)

    def test_compile_rejects_stage2_report_with_old_translation_coverage(self):
        report = self.stage2 / "report.json"
        value = json.loads(report.read_text(encoding="utf-8"))
        value["segments_total"] = 0
        report.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            compile_paper(self.root, self.paper_id)

    def test_fontspec_uses_latin_font_for_accented_names_only_in_compile_copy(self):
        work = self.root / "latin-work"
        work.mkdir()
        draft = self.stage2 / "translated" / "main.tex"
        original = draft.read_bytes()
        (work / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}"
            "中文与 İĞšğ\u200c \\bibliography{refs}"
            "\\end{document}", encoding="utf-8")
        (work / "main.bbl").write_text("Š中文", encoding="utf-8")
        actions = add_chinese_support(work, "main.tex", "Noto Serif SC", "fontspec")
        main = (work / "main.tex").read_text(encoding="utf-8")
        bibliography = (work / "main.bbl").read_text(encoding="utf-8")
        self.assertIn(r"\newfontfamily\StageThreeLatinFont{Latin Modern Roman}", main)
        self.assertIn(r'{\StageThreeLatinFont\char"0130}', main)
        self.assertIn(r'{\StageThreeLatinFont\char"0160}', bibliography)
        self.assertIn("中文", main)
        self.assertNotIn("\u200c", main)
        self.assertIn("wrapped_latin_extended_glyphs:5", actions)
        self.assertIn("removed_zero_width_non_joiners:1", actions)
        self.assertEqual(draft.read_bytes(), original)

    def test_fontspec_separates_control_words_from_chinese_in_compile_copy(self):
        work = self.root / "macro-cjk-work"
        work.mkdir()
        (work / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\\sys的协议\\end{document}",
            encoding="utf-8",
        )
        add_chinese_support(work, "main.tex", "Noto Serif SC", "fontspec")
        self.assertIn(r"\sys{}的协议", (work / "main.tex").read_text(encoding="utf-8"))

    def test_fontspec_routes_polish_letter_commands_to_latin_font(self):
        work = self.root / "polish-command-work"
        work.mkdir()
        original = ("\\documentclass{article}\n\\begin{document}\n"
                    r"中文 \L{}ukasz \textit{Pawe\l{}} Łukasz $L_\ell$"
                    "\n\\end{document}\n")
        draft = self.stage2 / "translated" / "main.tex"
        draft.write_text(original, encoding="utf-8")
        (work / "main.tex").write_text(original, encoding="utf-8")
        bibliography = r"\bibitem{kaiser} \L{}ukasz Kaiser and Pawe\l{}."
        (work / "main.bbl").write_text(bibliography, encoding="utf-8")

        add_chinese_support(work, "main.tex", "Noto Serif SC", "fontspec")

        compiled = (work / "main.tex").read_text(encoding="utf-8")
        for command, codepoint in (("L", "0141"), ("l", "0142")):
            declaration = ('\\DeclareTextCommand{\\' + command +
                           '}{TU}{{\\StageThreeLatinFont\\char"' + codepoint + '}}')
            self.assertIn(declaration, compiled)
            self.assertLess(compiled.index(declaration), compiled.index(r"\begin{document}"))
        self.assertIn(r"\L{}ukasz \textit{Pawe\l{}}", compiled)
        self.assertIn(r'{\StageThreeLatinFont\char"0141}ukasz', compiled)
        self.assertIn(r"$L_\ell$", compiled)
        self.assertEqual((work / "main.bbl").read_text(encoding="utf-8"), bibliography)
        self.assertEqual(draft.read_text(encoding="utf-8"), original)

    def test_braces_math_alphabet_subscripts_only_in_compile_copy(self):
        work = self.root / "mathcal-work"
        work.mkdir()
        original = ("\\documentclass{article}\n"
                    r"\newcommand{\Honest}{\mathcal{H}}" "\n"
                    "\\begin{document}\n"
                    r"$\mathrm{majority}_\mathcal{H}(V)$ and "
                    r"$\mathrm{majority}_{\mathcal{H}}(V)$ and "
                    r"$\mathbf{c}_\Honest$ and $x_\alpha$"
                    "\n\\end{document}\n")
        draft = self.stage2 / "translated" / "main.tex"
        draft.write_text(original, encoding="utf-8")
        (work / "main.tex").write_text(original, encoding="utf-8")
        actions = add_chinese_support(work, "main.tex", "SimSun", "fontspec")
        compiled = (work / "main.tex").read_text(encoding="utf-8")
        self.assertIn("braced_math_alphabet_subscripts:2", actions)
        self.assertEqual(compiled.count(r"_{\mathcal{H}}"), 2)
        self.assertIn(r"\mathbf{c}_{\Honest}", compiled)
        self.assertIn(r"x_\alpha", compiled)
        self.assertEqual(draft.read_text(encoding="utf-8"), original)

    def test_first_error_reports_tex_missing_brace_instead_of_generic_status(self):
        log = self.root / "compile.log"
        log.write_text("main.tex:1809: Missing { inserted.\n"
                       "LaTeX Warning: Reference undefined\n"
                       "Latexmk: Errors, so I did not complete making targets\n",
                       encoding="utf-8")
        self.assertEqual(_first_latex_error(log), "main.tex:1809: Missing { inserted.")

    def test_first_error_includes_fontspec_reason_on_next_line(self):
        log = self.root / "fontspec.log"
        log.write_text("./main.tex:174: Package fontspec Error:\n\n"
                       "(fontspec)                The font \"Noto Serif SC\" cannot be found.\n"
                       "(fontspec)                Please check the font name.\n",
                       encoding="utf-8")
        self.assertEqual(_first_latex_error(log),
                         './main.tex:174: Package fontspec Error: The font "Noto Serif SC" cannot be found.')

    def test_chinese_compiler_preflight_uses_temporary_source_and_cleans_up(self):
        def fake_compile(source_dir, files, main_tex, engine, executable, timeout,
                         output_pdf, log_path, **kwargs):
            self.assertEqual(engine, "xelatex")
            self.assertEqual(main_tex, "main.tex")
            self.assertIn("中文编译预检", files[0].read_text(encoding="utf-8"))
            self.assertEqual(kwargs["temp_root"], source_dir)
            return {"status": "compiled"}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=fake_compile):
            preflight_chinese_compiler(self.root, font="SimSun")
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_chinese_compiler_preflight_reports_missing_font(self):
        def failed_compile(source_dir, files, main_tex, engine, executable, timeout,
                           output_pdf, log_path, **kwargs):
            log_path.write_text("./main.tex:4: Package fontspec Error:\n"
                                '(fontspec) The font "SimSun" cannot be found.\n',
                                encoding="utf-8")
            return {"status": "compile_failed"}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=failed_compile):
            with self.assertRaisesRegex(RuntimeError, 'The font "SimSun" cannot be found'):
                preflight_chinese_compiler(self.root, font="SimSun")
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_fontspec_disables_legacy_font_setup_in_disposable_files(self):
        work = self.root / "legacy-font-work"
        work.mkdir()
        (work / "main.tex").write_text(
            "\\documentclass{local}\n\\usepackage{newtxtext,newtxmath}\n"
            "\\begin{document}中文\\end{document}", encoding="utf-8")
        (work / "local.cls").write_text(
            "\\RequirePackage[T1]{fontenc}\n\\RequirePackage{graphicx}\n",
            encoding="utf-8")
        actions = add_chinese_support(work, "main.tex", "Noto Serif SC", "fontspec")
        self.assertIn("disabled_legacy_font_packages:2", actions)
        self.assertIn("% Stage 3: disabled legacy font setup \\usepackage{newtxtext,newtxmath}",
                      (work / "main.tex").read_text(encoding="utf-8"))
        self.assertIn(r"\RequirePackage{graphicx}",
                      (work / "local.cls").read_text(encoding="utf-8"))

    def test_compile_writes_versioned_pdf_and_uses_cache(self):
        calls = []

        def fake_compile(source_dir, files, main_tex, engine, executable, timeout,
                         output_pdf, log_path, **kwargs):
            calls.append(engine)
            self.assertEqual(engine, "xelatex")
            self.assertEqual(kwargs["temp_root"], self.root / "tmp" / "pdfs")
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.write_bytes(b"%PDF-1.7\nfixture")
            log_path.write_text("success", encoding="utf-8")
            return {"status": "compiled", "duration_seconds": 1,
                    "compatibility_actions": ["injected_xeCJK_font:SimSun"]}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=fake_compile), \
             patch("arxiv_translate.stage3.pdf_page_count", return_value=2):
            first = compile_paper(self.root, self.paper_id, font="SimSun")
            second = compile_paper(self.root, self.paper_id, font="SimSun")
        self.assertEqual(first["status"], "compiled")
        self.assertTrue((self.root / first["pdf"]).is_file())
        self.assertTrue(first["pdf"].startswith("output/pdf/"))
        self.assertTrue(second["cached"])
        self.assertEqual(calls, ["xelatex"])

    def test_missing_glyphs_fail_validation_without_publishing_pdf(self):
        def fake_compile(source_dir, files, main_tex, engine, executable, timeout,
                         output_pdf, log_path, **kwargs):
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.write_bytes(b"%PDF-1.7\nfixture")
            log_path.write_text("Missing character: There is no text in font sample!", encoding="utf-8")
            return {"status": "compiled", "duration_seconds": 1, "compatibility_actions": []}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=fake_compile):
            report = compile_paper(self.root, self.paper_id, font="SimSun", cjk_mode="fontspec")
        self.assertEqual(report["status"], "validation_failed")
        self.assertEqual(report["missing_glyph_warnings"], 1)
        self.assertEqual(list((self.root / "output" / "pdf").glob("*.pdf")) if
                         (self.root / "output" / "pdf").exists() else [], [])

    def test_nullfont_diagnostics_do_not_count_as_missing_pdf_glyphs(self):
        def fake_compile(source_dir, files, main_tex, engine, executable, timeout,
                         output_pdf, log_path, **kwargs):
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.write_bytes(b"%PDF-1.7\nfixture")
            log_path.write_text("Missing character: There is no . in font nullfont!\n",
                                encoding="utf-8")
            return {"status": "compiled", "duration_seconds": 1, "compatibility_actions": []}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=fake_compile):
            report = compile_paper(self.root, self.paper_id, font="SimSun", cjk_mode="fontspec")
        self.assertEqual(report["status"], "compiled")
        self.assertEqual(report["nullfont_warnings"], 1)
        self.assertEqual(report["missing_glyph_warnings"], 0)

    def test_academicons_initialization_spaces_are_not_pdf_missing_glyphs(self):
        warning = "Missing character: There is no   in font [academicons.otf]/OT!"
        localized = "Missing character: There is no   in font [academicons.otf]/OT:language=dflt;!"
        summary = "  " + warning
        real_glyph = "Missing character: There is no 中 in font [academicons.otf]/OT!"
        counts = classify_missing_characters("\n".join((warning, localized, summary, real_glyph)))
        self.assertEqual(counts["icon_font_setup_warnings"], 3)
        self.assertEqual(counts["missing_glyph_warnings"], 1)

        def fake_compile(source_dir, files, main_tex, engine, executable, timeout,
                         output_pdf, log_path, **kwargs):
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.write_bytes(b"%PDF-1.7\nfixture")
            log_path.write_text("\n".join((warning, localized, summary)), encoding="utf-8")
            return {"status": "compiled", "duration_seconds": 1, "compatibility_actions": []}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=fake_compile):
            report = compile_paper(self.root, self.paper_id, font="SimSun", cjk_mode="fontspec")
        self.assertEqual(report["status"], "compiled")
        self.assertEqual(report["icon_font_setup_warnings"], 3)
        self.assertEqual(report["missing_glyph_warnings"], 0)

    def test_auto_mode_falls_back_to_fontspec_after_xecjk_failure(self):
        modes = []

        def fake_compile(source_dir, files, main_tex, engine, executable, timeout,
                         output_pdf, log_path, **kwargs):
            mode = "xecjk" if "xecjk" in log_path.name else "fontspec"
            modes.append(mode)
            if mode == "xecjk":
                log_path.write_text("xtemplate conflict", encoding="utf-8")
                return {"status": "compile_failed", "compatibility_actions": []}
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.write_bytes(b"%PDF-1.7\nfixture")
            log_path.write_text("Missing character: There is no 2 in font nullfont!\n",
                                encoding="utf-8")
            return {"status": "compiled", "compatibility_actions": []}

        with patch("arxiv_translate.stage3.probe_latexmk", return_value=("latexmk", "version")), \
             patch("arxiv_translate.stage3.shutil.which", return_value="xelatex"), \
             patch("arxiv_translate.stage3.compile_local", side_effect=fake_compile):
            report = compile_paper(self.root, self.paper_id, font="SimSun")
        self.assertEqual(modes, ["xecjk", "fontspec"])
        self.assertEqual(report["status"], "compiled")
        self.assertEqual(report["cjk_mode"], "fontspec")


if __name__ == "__main__":
    unittest.main()
