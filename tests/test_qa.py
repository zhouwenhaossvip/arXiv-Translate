import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.qa import _fields, _structure, audit_paper
from arxiv_translate.stage1 import fingerprint, source_files
from arxiv_translate.stage2 import run_translation
from arxiv_translate.tex_segments import PLACEHOLDER


class QualityAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paper_id = "2608.17220v1"
        source = self.root / "latex" / paper_id
        source.mkdir(parents=True)
        (source / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\title{A Test Paper}\n"
            "\\begin{document}\n"
            "\\abstract{An English abstract needs translation.}\n"
            "\\section{Introduction}\n"
            "Research text with a \\cite{paper} citation and $x=1$.\n"
            "\\end{document}\n", encoding="utf-8")
        pdf_dir = self.root / "pdf"
        pdf_dir.mkdir()
        (pdf_dir / f"{paper_id}.pdf").write_bytes(b"%PDF-1.7\nfixture")
        baseline = self.root / "results" / "stage1" / "local" / "baseline.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text(json.dumps({"papers": {paper_id: {"status": "compiled"}}}),
                            encoding="utf-8")
        self.paper_id = paper_id

    def _translate(self):
        def translator(batch, model):
            return {item.id: "中文译文 " + "".join(match.group() for match in
                    PLACEHOLDER.finditer(item.text))
                    for item in batch}
        run_translation(self.root, self.paper_id, "test-model", None,
                        translate_batch=translator)
        translated = self.root / "results" / "stage2" / self.paper_id / "translated"
        draft_sha = fingerprint(translated, source_files(translated))
        output = self.root / "output" / "pdf"
        output.mkdir(parents=True)
        (output / "test.pdf").write_bytes(b"%PDF-1.7\nfixture")
        stage3 = self.root / "results" / "stage3" / self.paper_id
        stage3.mkdir(parents=True)
        (stage3 / "report.json").write_text(json.dumps({
            "status": "compiled", "pdf": "output/pdf/test.pdf",
            "cache_key": {"draft_sha256": draft_sha},
        }), encoding="utf-8")
        return translated

    def test_english_field_detector_and_math_reference_scan(self):
        fields = _fields(r"\abstract{English abstract text.}\section{\uppercase{Introduction}}",
                         "main.tex")
        self.assertEqual([item["field"] for item in fields], ["abstract", "section"])
        structure = _structure(r"Text \cite{paper} and $x=1$ % \cite{fake}")
        self.assertEqual(structure["references"][r"\cite{paper}"], 1)
        self.assertNotIn(r"\cite{fake}", structure["references"])
        self.assertEqual(structure["math_delimiters"]["$"], 2)

    def test_audit_detects_stale_pdf_and_changed_citation(self):
        translated = self._translate()
        with patch("arxiv_translate.qa.pdf_page_count", return_value=1):
            clean = audit_paper(self.root, self.paper_id, render_pages=False)
        self.assertEqual(clean["status"], "pass")
        self.assertTrue(clean["pdf_current"])
        self.assertEqual(clean["reference_differences"]["removed_count"], 0)

        main = translated / "main.tex"
        main.write_text(main.read_text(encoding="utf-8").replace(
            r"\cite{paper}", r"\cite{other}"), encoding="utf-8")
        with patch("arxiv_translate.qa.pdf_page_count", return_value=1):
            changed = audit_paper(self.root, self.paper_id, render_pages=False)
        self.assertEqual(changed["status"], "blocked")
        self.assertFalse(changed["pdf_current"])
        self.assertEqual(changed["reference_differences"]["removed_count"], 1)
        self.assertEqual(changed["reference_differences"]["added_count"], 1)
        self.assertEqual({item["code"] for item in changed["issues"]},
                         {"stale_pdf", "reference_change"})


if __name__ == "__main__":
    unittest.main()
