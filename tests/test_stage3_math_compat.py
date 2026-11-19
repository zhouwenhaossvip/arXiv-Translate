"""Real XeLaTeX regression checks; opt in with ARXIV_TEST_LATEXMK."""

import os
import tempfile
import unittest
from pathlib import Path

from arxiv_translate.local_compiler import compile_local
from arxiv_translate.stage1 import source_files
from arxiv_translate.stage3 import add_chinese_support, classify_missing_characters


@unittest.skipUnless(os.environ.get("ARXIV_TEST_LATEXMK"), "requires local XeLaTeX")
class MathDelimiterCompatibilityTests(unittest.TestCase):
    def compile_case(self, preamble, assertions, formula):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            main = source / "main.tex"
            main.write_text("\\documentclass{article}\n" + preamble +
                            "\n\\begin{document}\n" + assertions +
                            "\n\\input{section}\n\\end{document}\n", encoding="utf-8")
            section = source / "section.tex"
            section.write_text("中文公式：\\[" + formula + "\\]\n", encoding="utf-8")
            original = {path.name: path.read_bytes() for path in source.iterdir()}
            log = root / "compile.log"
            result = compile_local(
                source, source_files(source), "main.tex", "xelatex",
                os.environ["ARXIV_TEST_LATEXMK"], 120, root / "result.pdf", log,
                prepare_tex=lambda work, main: add_chinese_support(
                    work, main, os.environ.get("ARXIV_TEST_CJK_FONT", "Noto Serif SC"), "fontspec"),
                temp_root=root,
            )
            contents = log.read_text(encoding="utf-8")
            self.assertEqual(result["status"], "compiled", contents[-5000:])
            self.assertEqual(classify_missing_characters(contents)["missing_glyph_warnings"], 0)
            self.assertEqual({path.name: path.read_bytes() for path in source.iterdir()}, original)

    def test_unicode_math_aliases_render_scalable_brackets_in_included_section(self):
        self.compile_case(
            r"\usepackage{unicode-math}\setmathfont{Latin Modern Math}",
            r"\ifx\llbracket\lBrack\else\errmessage{Missing left alias}\fi"
            r"\ifx\rrbracket\rBrack\else\errmessage{Missing right alias}\fi",
            r"\alpha(P)\subseteq\llbracket P\rrbracket"
            r"\quad\left\llbracket\frac{P}{Q}\right\rrbracket"
            r"\quad\bigl\llbracket P\bigr\rrbracket",
        )

    def test_existing_delimiter_definitions_are_preserved(self):
        self.compile_case(
            r"\usepackage{unicode-math}\setmathfont{Latin Modern Math}"
            r"\def\llbracket{[}\def\rrbracket{]}"
            r"\let\ExpectedLeft\llbracket\let\ExpectedRight\rrbracket",
            r"\ifx\llbracket\ExpectedLeft\else\errmessage{Left overwritten}\fi"
            r"\ifx\rrbracket\ExpectedRight\else\errmessage{Right overwritten}\fi",
            r"\llbracket P\rrbracket",
        )

    def test_without_unicode_math_does_not_create_broken_commands(self):
        self.compile_case(
            "",
            r"\ifdefined\llbracket\errmessage{Unexpected left alias}\fi"
            r"\ifdefined\rrbracket\errmessage{Unexpected right alias}\fi",
            r"x^2 + y^2 = z^2",
        )


if __name__ == "__main__":
    unittest.main()
