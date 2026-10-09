import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.stage2 import (deepseek_translate_batch, main, openai_translate_batch,
                                    paper_plan, run_translation)
from arxiv_translate.tex_segments import (apply_translations, extract_segments,
                                           reachable_tex, render_translation)


class StageTwoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pdf").mkdir()
        (self.root / "pdf" / "2608.17220v1.pdf").write_bytes(b"%PDF-1.7\nfixture")
        self.source = self.root / "latex" / "2608.17220v1"
        (self.source / "sections").mkdir(parents=True)
        (self.source / "macro.tex").write_text(r"\newcommand{\sys}{System}", encoding="utf-8")
        (self.source / "main.tex").write_text(
            "% \\begin{document} fake\n"
            "\\documentclass{article}\n"
            "\\input{macro}\n"
            "\\title{A Test Paper}\n"
            "\\begin{document}\n"
            "\\section{Introduction}\n"
            "This is normal prose with a \\cite{secretkey} citation. $E=mc^2$ More prose here.\n\n"
            "\\input{sections/body}\n"
            "\\begin{equation}hidden math words\\end{equation}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        (self.source / "sections" / "body.tex").write_text(
            "Important results are discussed here. % comment words stay\n"
            "\\begin{tabular}{ll}Table words & remain\\end{tabular}\n"
            "\\textbf{Bold conclusion} follows.\n", encoding="utf-8",
        )
        baseline = self.root / "results" / "stage1" / "local" / "baseline.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text(json.dumps({"papers": {"2608.17220v1": {"status": "compiled"}}}),
                            encoding="utf-8")

    def test_extractor_protects_tex_syntax_and_preamble(self):
        entry, _, segments = paper_plan(self.root, "2608.17220v1")
        self.assertEqual(entry["main_tex"], "main.tex")
        self.assertEqual(reachable_tex(self.source, "main.tex"),
                         ["main.tex", "sections/body.tex"])
        combined = " ".join(segment.text for segment in segments)
        self.assertIn("Introduction", combined)
        self.assertIn("A Test Paper", combined)
        self.assertIn("normal prose", combined)
        self.assertIn("Bold conclusion", combined)
        self.assertTrue(any("[[LATEX_0]]" in segment.text for segment in segments))
        for protected in ("secretkey", "E=mc", "hidden math words", "Table words",
                          "comment words", "System"):
            self.assertNotIn(protected, combined)

    def test_enhanced_extractor_includes_custom_abstract_keywords_and_uppercase(self):
        source = self.source / "main.tex"
        text = source.read_text(encoding="utf-8")
        text = text.replace("\\begin{document}",
                            "\\keywords{Trust and Identity}\n"
                            "\\begin{document}\n"
                            "\\abstract{A complete English abstract.}")
        text = text.replace("\\section{Introduction}",
                            "\\section{\\uppercase{Introduction}}")
        source.write_text(text, encoding="utf-8")
        enhanced = extract_segments(text, "main.tex", main_file=True)
        legacy = extract_segments(text, "main.tex", main_file=True, enhanced=False)
        new_text = " ".join(item.text for item in enhanced)
        old_text = " ".join(item.text for item in legacy)
        self.assertIn("Trust and Identity", new_text)
        self.assertIn("A complete English abstract.", new_text)
        self.assertIn("Introduction", new_text)
        self.assertNotIn("Trust and Identity", old_text)
        self.assertNotIn("A complete English abstract.", old_text)
        self.assertNotIn("Introduction", old_text)

    def test_old_checkpoint_keeps_legacy_segment_ids(self):
        entry, _, legacy = paper_plan(self.root, "2608.17220v1")
        checkpoint = self.root / "results" / "stage2" / "2608.17220v1" / "translations.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text(json.dumps({
            "schema_version": 1, "source_sha256": entry["source_sha256"],
            "model": "test-model", "segment_count": len(legacy), "translations": {},
        }), encoding="utf-8")
        source = self.source / "main.tex"
        text = source.read_text(encoding="utf-8").replace(
            "\\begin{document}", "\\begin{document}\\abstract{English abstract here.}")
        source.write_text(text, encoding="utf-8")
        expected = extract_segments(text, "main.tex", main_file=True, enhanced=False, modern=False)
        _, _, resumed = paper_plan(self.root, "2608.17220v1")
        main_resumed = [item for item in resumed if item.path == "main.tex"]
        self.assertEqual([item.id for item in main_resumed], [item.id for item in expected])

    def test_modern_extractor_translates_starred_headings_but_not_raw_references(self):
        text = ("\\documentclass{article}\\begin{document}\n"
                "\\section*{Acknowledgments}\nWe thank our collaborators.\n"
                "\\section*{References}\nSmith, J. (2024). An English paper title.\n"
                "\\end{document}")
        current = extract_segments(text, "main.tex", main_file=True)
        previous = extract_segments(text, "main.tex", main_file=True, modern=False)
        joined = " ".join(item.text for item in current)
        self.assertIn("Acknowledgments", joined)
        self.assertIn("References", joined)
        self.assertNotIn("English paper title", joined)
        self.assertIn("English paper title", " ".join(item.text for item in previous))

    def test_new_extractor_includes_optional_title_and_preamble_abstract(self):
        source = ("\\documentclass{article}\n"
                  "\\title[Short English Title]{A Longer English Paper Title}\n"
                  "\\begin{abstract}An abstract before the document begins.\\end{abstract}\n"
                  "\\begin{document}Chinese-ready body.\\end{document}")
        current = extract_segments(source, "main.tex", main_file=True)
        previous = extract_segments(source, "main.tex", main_file=True,
                                    preamble_metadata=False)
        new_text = " ".join(item.text for item in current)
        old_text = " ".join(item.text for item in previous)
        self.assertIn("Short English Title", new_text)
        self.assertIn("A Longer English Paper Title", new_text)
        self.assertIn("An abstract before the document begins.", new_text)
        self.assertNotIn("Short English Title", old_text)
        self.assertNotIn("An abstract before the document begins.", old_text)

    def test_translation_resumes_then_materializes_without_touching_source(self):
        before = (self.source / "main.tex").read_bytes()

        def translate(batch, model):
            self.assertEqual(model, "test-model")
            return {segment.id: "中文 100%_" + "".join(re.findall(r"\[\[LATEX_\d+\]\]", segment.text))
                    for segment in batch}

        first = run_translation(self.root, "2608.17220v1", "test-model", 1,
                                translate_batch=translate)
        self.assertFalse(first["complete"])
        self.assertEqual(first["segments_translated"], 1)
        self.assertFalse((self.root / "results/stage2/2608.17220v1/translated").exists())
        second = run_translation(self.root, "2608.17220v1", "test-model", None,
                                 translate_batch=translate)
        self.assertTrue(second["complete"])
        self.assertEqual(second["segments_translated"], second["segments_total"])
        translated = self.root / second["translated_source"] / "main.tex"
        output = translated.read_text(encoding="utf-8")
        self.assertIn(r"中文 100\%\_", output)
        self.assertIn(r"\cite{secretkey}", output)
        self.assertIn("$E=mc^2$", output)
        self.assertEqual((self.source / "main.tex").read_bytes(), before)
        self.assertEqual(main(["plan", "--root", str(self.root), "--id", "2608.17220v1"]), 0)

    def test_unverified_paper_and_changed_model_are_rejected(self):
        baseline = self.root / "results/stage1/local/baseline.json"
        baseline.write_text(json.dumps({"papers": {"2608.17220v1": {"status": "validation_failed"}}}),
                            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Stage 1 has not passed"):
            run_translation(self.root, "2608.17220v1", "test-model", 1,
                            translate_batch=lambda batch, model: {})
        first = run_translation(self.root, "2608.17220v1", "test-model", 1,
                                allow_unverified=True,
                                translate_batch=lambda batch, model: {item.id: "译文" for item in batch})
        self.assertEqual(first["segments_translated"], 1)
        with self.assertRaisesRegex(ValueError, "another source or model"):
            run_translation(self.root, "2608.17220v1", "different-model", 1,
                            allow_unverified=True,
                            translate_batch=lambda batch, model: {})

    def test_replacement_checks_original_span(self):
        text = "\\begin{document}Simple text.\\end{document}"
        segment = extract_segments(text, "main.tex", main_file=True)[0]
        with self.assertRaisesRegex(ValueError, "Source changed"):
            apply_translations(text.replace("Simple", "Broken"), [segment], {segment.id: "中文"})

    def test_latex_placeholders_cannot_be_dropped(self):
        segment = next(s for s in paper_plan(self.root, "2608.17220v1")[2]
                       if s.protected)
        with self.assertRaisesRegex(ValueError, "placeholders were changed"):
            render_translation(segment, "中文，但没有保留命令")
        tokens = "".join(f"[[LATEX_{i}]]" for i in range(len(segment.protected)))
        restored = render_translation(segment, "中文" + tokens)
        for original_command in segment.protected:
            self.assertIn(original_command, restored)

    def test_control_word_is_separated_from_following_chinese(self):
        text = "\\begin{document}Name \\aid's effect is useful.\\end{document}"
        segment = extract_segments(text, "main.tex", main_file=True)[0]
        self.assertIn(r"\aid{}中文", render_translation(segment,
                      "名称[[LATEX_0]]中文很有用。"))

    def test_openai_adapter_uses_structured_output_without_network(self):
        segment = paper_plan(self.root, "2608.17220v1")[2][0]
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"translations": [
                {"id": segment.id, "text": "引言"}]}, ensure_ascii=False)}]}]}

        def urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["model"], "test-model")
            self.assertFalse(payload["store"])
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertEqual(payload["input"][1]["role"], "user")
            return io.BytesIO(json.dumps(response, ensure_ascii=False).encode("utf-8"))

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-key"}), \
             patch("arxiv_translate.stage2.urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(openai_translate_batch([segment], "test-model"),
                             {segment.id: "引言"})

    def test_deepseek_adapter_routes_and_disables_reasoning(self):
        segment = paper_plan(self.root, "2608.17220v1")[2][0]
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"translations": [
                {"id": segment.id, "text": "引言"}]}, ensure_ascii=False)}]}]}

        def urlopen(request, timeout):
            self.assertEqual(request.full_url, "https://api.deepseek.com/responses")
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["model"], "deepseek-flash")
            self.assertEqual(payload["reasoning"], {"effort": "none"})
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only-key")
            return io.BytesIO(json.dumps(response, ensure_ascii=False).encode("utf-8"))

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-key"}), \
             patch("arxiv_translate.stage2.urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(deepseek_translate_batch([segment], "deepseek-flash"),
                             {segment.id: "引言"})

    def test_deepseek_run_requires_key_before_writing(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY is not set"):
                run_translation(self.root, "2608.17220v1", "deepseek-flash", 1)
        self.assertFalse((self.root / "results/stage2/2608.17220v1").exists())

    def test_deepseek_recovers_changed_placeholders_piecewise(self):
        segment = next(s for s in paper_plan(self.root, "2608.17220v1")[2]
                       if s.protected)

        def request(items, model, **kwargs):
            if any(item.protected for item in items):
                raise ValueError(f"Protected LaTeX placeholders were changed: {segment.path}:{segment.start}")
            return {item.id: "译文" for item in items}

        with patch("arxiv_translate.stage2._responses_translate_batch", side_effect=request):
            result = deepseek_translate_batch([segment], "deepseek-flash")
        self.assertEqual(result[segment.id].count("[[LATEX_"), len(segment.protected))
        self.assertIn(r"\cite{secretkey}", render_translation(segment, result[segment.id]))

    def test_deepseek_recovers_batch_with_missing_ids(self):
        segments = paper_plan(self.root, "2608.17220v1")[2][:2]

        def request(items, model, **kwargs):
            if len(items) > 1:
                raise RuntimeError("Translation response omitted, duplicated, or added segment IDs")
            return {items[0].id: "译文"}

        with patch("arxiv_translate.stage2._responses_translate_batch", side_effect=request):
            result = deepseek_translate_batch(segments, "deepseek-flash")
        self.assertEqual(set(result), {segment.id for segment in segments})

    def test_deepseek_recovers_malformed_structured_output(self):
        segments = paper_plan(self.root, "2608.17220v1")[2][:2]

        def request(items, model, **kwargs):
            if len(items) > 1:
                raise json.JSONDecodeError("empty structured output", "", 0)
            return {items[0].id: "译文"}

        with patch("arxiv_translate.stage2._responses_translate_batch", side_effect=request):
            result = deepseek_translate_batch(segments, "deepseek-flash")
        self.assertEqual(set(result), {segment.id for segment in segments})


if __name__ == "__main__":
    unittest.main()
