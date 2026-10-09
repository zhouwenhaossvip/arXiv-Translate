"""Stage 3: compile a completed Chinese LaTeX draft into a checked local PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any

from .local_compiler import compile_local, pdf_page_count, probe_latexmk
from .stage1 import fingerprint, inventory, source_files


STAGE_VERSION = 22
WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
SYMBOL_FONT = "Segoe UI Symbol" if sys.platform == "win32" else "DejaVu Sans"
DEFAULT_FONT = ("Noto Serif SC" if (sys.platform == "win32" and
                                    (WINDOWS_FONTS / "NotoSerifSC-VF.ttf").is_file())
                else "SimSun" if sys.platform == "win32" else "FandolSong-Regular")
CJK_PACKAGE = re.compile(r"\\usepackage(?:\[[^]]*\])?\s*\{[^}]*(?:xeCJK|ctex|luatexja)[^}]*\}", re.I)
BEGIN_DOCUMENT = re.compile(r"\\begin\s*\{document\}")
PDFOUTPUT_LINE = re.compile(r"(?m)^\s*\\pdfoutput\s*=\s*1\s*$")
FONT_NAME = re.compile(r"^[\w ._-]+$", re.UNICODE)
LATIN_EXTENDED = re.compile(r"[\u00c0-\u024f\u1e00-\u1eff]")
CONTROL_CJK = re.compile(r"(\\[A-Za-z@]+)(?=[\u3400-\u9fff])")
LEGACY_FONT_LINE = re.compile(
    r"(?m)^[ \t]*\\(?:usepackage|RequirePackage)(?:\[[^]\n]*\])?"
    r"\{(?:fontenc|newtxtext,newtxmath|newtxtext|newtxmath|mathptmx)\}[ \t]*$"
)
SPECIAL_FONT_LINE = re.compile(
    r"(?m)^[ \t]*\\(?:usepackage|RequirePackage)(?:\[[^]\n]*\])?"
    r"\{(?:fontenc|newtxtext,newtxmath|newtxtext|newtxmath|mathptmx|"
    r"cmap|tgpagella|inconsolata|XCharter|zlmtt|mathpazo|fdsymbol)\}[ \t]*$"
)


def _fontspec_latin_fallback(work: Path) -> list[str]:
    """Keep CJK as the main font but render extended Latin in a Latin font.

    Only disposable TeX and bundled bibliography copies are modified.  Some
    CJK fonts omit accented author names even though ordinary ASCII is present.
    """
    wrapped = 0
    removed_invisible = 0
    for path in work.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".tex", ".bbl"}:
            continue
        source = path.read_text(encoding="utf-8")
        removed_invisible += source.count("\u200c")
        updated = CONTROL_CJK.sub(r"\1{}", source.replace("\u200c", ""))

        def replace(match: re.Match[str]) -> str:
            nonlocal wrapped
            char = match.group()
            if not unicodedata.name(char, "").startswith("LATIN "):
                return char
            wrapped += 1
            return "{\\StageThreeLatinFont\\char\"%04X}" % ord(char)

        updated = LATIN_EXTENDED.sub(replace, updated)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
    actions = []
    if wrapped:
        actions.append(f"wrapped_latin_extended_glyphs:{wrapped}")
    if removed_invisible:
        actions.append(f"removed_zero_width_non_joiners:{removed_invisible}")
    return actions


def _disable_legacy_font_packages(work: Path, *, extended: bool = False) -> list[str]:
    """Avoid T1/Type-1 text font setup that conflicts with XeTeX fontspec."""
    count = 0
    for path in work.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".tex", ".cls"}:
            continue
        source = path.read_text(encoding="utf-8")
        pattern = SPECIAL_FONT_LINE if extended else LEGACY_FONT_LINE
        updated, replacements = pattern.subn(
            lambda match: "% Stage 3: disabled legacy font setup " + match.group().strip(), source
        )
        if replacements:
            path.write_text(updated, encoding="utf-8")
            count += replacements
    return [f"disabled_legacy_font_packages:{count}"] if count else []


def add_chinese_support(work: Path, main_tex: str, font: str,
                        mode: str = "xecjk") -> list[str]:
    """Adjust only the disposable compile copy, never the Stage 2 draft."""
    if not FONT_NAME.fullmatch(font) or len(font) > 100:
        raise ValueError("Invalid CJK font name")
    if mode not in {"xecjk", "fontspec"}:
        raise ValueError(f"Unknown CJK mode: {mode}")
    actions = []
    special_class = (work / "mystyle.cls").is_file()
    if mode in {"xecjk", "fontspec"}:
        actions.extend(_disable_legacy_font_packages(work, extended=special_class))
    if mode == "fontspec" and not special_class:
        actions.extend(_fontspec_latin_fallback(work))
    path = work / main_tex
    source = path.read_text(encoding="utf-8")
    if "\u200b" in source:
        source = source.replace("\u200b", "")
        actions.append("removed_zero_width_spaces")
    updated, count = PDFOUTPUT_LINE.subn("% Stage 3: pdfoutput omitted for XeLaTeX", source)
    if count:
        actions.append("disabled_pdflatex_pdfoutput_directive")
    if not CJK_PACKAGE.search(updated):
        match = BEGIN_DOCUMENT.search(updated)
        if not match:
            raise ValueError(f"No document start found in {main_tex}")
        if mode == "xecjk":
            injection = f"\\usepackage{{xeCJK}}\n\\setCJKmainfont{{{font}}}\n"
        else:
            if special_class:
                direct_font = (f"[Path={WINDOWS_FONTS.as_posix()}/]{{NotoSansSC-Medium.otf}}"
                               if font == "Noto Serif SC" and
                               (WINDOWS_FONTS / "NotoSansSC-Medium.otf").is_file()
                               else f"{{{font}}}")
                font_setup = (f"\\setmainfont{direct_font}\n"
                              "\\renewcommand{\\sfdefault}{\\rmdefault}\n"
                              "\\renewcommand{\\ttdefault}{\\rmdefault}\n")
                injection = "\\usepackage{fontspec}\n" + font_setup
            else:
                font_setup = (f"\\setmainfont{{{font}}}\n"
                              f"\\setsansfont{{{font}}}\n\\setmonofont{{{font}}}\n"
                              f"\\AtBeginDocument{{\\setmainfont{{{font}}}\\setsansfont{{{font}}}\\setmonofont{{{font}}}}}\n")
                injection = ("\\usepackage{fontspec}\n" + font_setup +
                             "\\newfontfamily\\StageThreeLatinFont{Latin Modern Roman}\n"
                             f"\\newfontfamily\\StageThreeSymbolFont{{{SYMBOL_FONT}}}\n"
                             "\\AtBeginDocument{\\ifdefined\\checkmark\\renewcommand{\\checkmark}{\\hbox{{\\StageThreeSymbolFont\\char\"2713}}}\\fi}\n")
            injection += ("\\XeTeXlinebreaklocale \"zh\"\n"
                          "\\XeTeXlinebreakskip=0pt plus 1pt\n")
        injection += "% Stage 3: CJK support added only in temporary compilation copy\n"
        updated = updated[:match.start()] + injection + updated[match.start():]
        actions.append(f"injected_{mode}_font:{font}")
    if updated != path.read_text(encoding="utf-8"):
        path.write_text(updated, encoding="utf-8")
    return actions


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _first_latex_error(log_path: Path) -> str | None:
    if not log_path.is_file():
        return None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if (stripped.startswith("! ") or "LaTeX Err" in stripped or
                re.search(r"\.tex:\d+:.*(?:error|undefined)", stripped, re.I)):
            return stripped[:500]
    return None


def doctor(font: str = DEFAULT_FONT, latexmk: str = "latexmk") -> dict[str, Any]:
    executable, version = probe_latexmk(latexmk)
    xelatex = shutil.which("xelatex")
    return {
        "ready": bool(executable and xelatex),
        "latexmk": executable,
        "latexmk_version_or_error": version,
        "xelatex": xelatex,
        "cjk_font": font,
        "note": "Font and package availability are confirmed by an actual compile, not by this probe.",
    }


def compile_paper(root: Path, paper_id: str, *, font: str = DEFAULT_FONT,
                  latexmk: str = "latexmk", timeout: int = 300,
                  force: bool = False, cjk_mode: str = "auto") -> dict[str, Any]:
    if timeout <= 0:
        raise ValueError("--timeout must be positive")
    if cjk_mode not in {"auto", "xecjk", "fontspec"}:
        raise ValueError("--cjk-mode must be auto, xecjk or fontspec")
    manifest = inventory(root)
    entry = next((paper for paper in manifest["papers"] if paper["id"] == paper_id), None)
    if entry is None or entry["status"] != "ready":
        raise ValueError(f"No usable LaTeX source for {paper_id}")
    baseline_path = root / "results" / "stage1" / "local" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    if baseline.get("papers", {}).get(paper_id, {}).get("status") != "compiled":
        raise ValueError(f"Stage 1 local baseline is not compiled for {paper_id}")
    draft_dir = root / "results" / "stage2" / paper_id
    stage2_report_path = draft_dir / "report.json"
    if not stage2_report_path.is_file():
        raise ValueError(f"Stage 2 report is missing for {paper_id}")
    stage2_report = json.loads(stage2_report_path.read_text(encoding="utf-8"))
    if not stage2_report.get("complete") or stage2_report.get("source_sha256") != entry["source_sha256"]:
        raise ValueError(f"Stage 2 draft is incomplete or based on another source for {paper_id}")
    translated = draft_dir / "translated"
    if translated.is_symlink() or not translated.is_dir() or translated.resolve().parent != draft_dir.resolve():
        raise ValueError("Stage 2 translated source directory is missing or unsafe")
    files = source_files(translated)
    main_tex = entry["main_tex"]
    if (translated / main_tex).resolve() not in files:
        raise ValueError(f"Translated main TeX file is missing: {main_tex}")
    draft_sha256 = fingerprint(translated, files)
    executable, version = probe_latexmk(latexmk)
    if executable is None:
        raise RuntimeError(version)
    if not shutil.which("xelatex"):
        raise RuntimeError("xelatex is unavailable; install XeLaTeX with CJK packages and a Chinese font")
    paper_dir = root / "results" / "stage3" / paper_id
    report_path = paper_dir / "report.json"
    cache_key = {"version": STAGE_VERSION, "draft_sha256": draft_sha256,
                 "compiler_version": version, "font": font, "cjk_mode": cjk_mode}
    config_sha = hashlib.sha256(json.dumps(cache_key, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    output = root / "output" / "pdf" / f"{paper_id}-zh-{draft_sha256[:10]}-{config_sha}.pdf"
    if not force and report_path.is_file():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        if (previous.get("status") == "compiled" and previous.get("cache_key") == cache_key
                and output.is_file()):
            with output.open("rb") as stream:
                if stream.read(5) == b"%PDF-":
                    return {**previous, "cached": True}
    paper_dir.mkdir(parents=True, exist_ok=True)
    candidate = root / "tmp" / "pdfs" / f"{paper_id}-pending-{draft_sha256[:10]}.pdf"
    modes = ("xecjk", "fontspec") if cjk_mode == "auto" else (cjk_mode,)
    attempt_logs: list[str] = []
    for mode in modes:
        log_path = paper_dir / f"compile-{mode}.log"
        engine = "xelatex"
        result = compile_local(
            translated, files, main_tex, engine, executable, timeout, candidate, log_path,
            prepare_tex=lambda work, main: add_chinese_support(work, main, font, mode),
            temp_root=root / "tmp" / "pdfs",
        )
        attempt_logs.append(log_path.relative_to(root).as_posix())
        if result["status"] == "compiled" or result["status"] in {"timeout", "compiler_unavailable"}:
            break
    report: dict[str, Any] = {
        "paper_id": paper_id, "status": result["status"], "cache_key": cache_key,
        "main_tex": main_tex, "model": stage2_report.get("model"),
        "engine": engine, "cjk_mode": mode,
        "duration_seconds": result.get("duration_seconds"),
        "compatibility_actions": result.get("compatibility_actions", []),
        "log": log_path.relative_to(root).as_posix(), "attempt_logs": attempt_logs,
        "cached": False,
    }
    compiler_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    report["missing_glyph_warnings"] = compiler_log.count("Missing character:")
    report["overfull_hbox_warnings"] = compiler_log.count("Overfull \\hbox")
    if result["status"] == "compiled" and report["missing_glyph_warnings"]:
        report["status"] = "validation_failed"
        report["first_error"] = (f"PDF contains {report['missing_glyph_warnings']} missing glyph warnings; "
                                 "adjust CJK font settings before publishing")
    if report["status"] == "compiled":
        output.parent.mkdir(parents=True, exist_ok=True)
        candidate.replace(output)
        report["pdf"] = output.relative_to(root).as_posix()
        report["pdf_bytes"] = output.stat().st_size
        report["pages"] = pdf_page_count(output)
        report["original_pages"] = pdf_page_count(root / entry["pdf"])
        report["note"] = "Page counts may differ after translation; visual and linguistic review is still required."
    else:
        report.setdefault("first_error", result.get("first_error") or _first_latex_error(log_path))
        if candidate.exists():
            candidate.unlink()
    _write_json(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "compile"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--id", help="Exact arXiv paper ID including version")
    selection.add_argument("--all", action="store_true", help="Compile all completed Stage 2 drafts")
    parser.add_argument("--font", default=DEFAULT_FONT, help="Installed Chinese font name")
    parser.add_argument("--cjk-mode", choices=("auto", "xecjk", "fontspec"), default="auto")
    parser.add_argument("--latexmk", default="latexmk")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            report = doctor(args.font, args.latexmk)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ready"] else 1
        if not args.id and not args.all:
            parser.error("--id or --all is required for compile")
        root = args.root.resolve()
        if args.all:
            paper_ids = [paper["id"] for paper in inventory(root)["papers"]
                         if (root / "results" / "stage2" / paper["id"] / "report.json").is_file()
                         and json.loads((root / "results" / "stage2" / paper["id"] / "report.json")
                                        .read_text(encoding="utf-8")).get("complete")]
            if not paper_ids:
                raise ValueError("No completed Stage 2 drafts found")
        else:
            paper_ids = [args.id]
        reports = []
        for paper_id in paper_ids:
            try:
                reports.append(compile_paper(root, paper_id, font=args.font,
                                             latexmk=args.latexmk, timeout=args.timeout,
                                             force=args.force, cjk_mode=args.cjk_mode))
            except (OSError, ValueError, RuntimeError) as error:
                reports.append({"paper_id": paper_id, "status": "error", "first_error": str(error)})
        print(json.dumps(reports if args.all else reports[0], ensure_ascii=False, indent=2))
        return 0 if all(report["status"] == "compiled" for report in reports) else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Stage 3 failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
