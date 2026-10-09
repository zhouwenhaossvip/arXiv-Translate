"""Audit translation coverage, TeX structure, compiler warnings and PDF pages."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .local_compiler import pdf_page_count
from .stage1 import active_tex, fingerprint, inventory, sha256_file, source_files
from .tex_segments import _balanced_end, extract_segments, reachable_tex


FIELD = re.compile(r"\\(title|abstract|keywords|section|subsection|subsubsection|caption)\*?\s*\{", re.I)
REFERENCE = re.compile(r"\\(?:cite\w*|ref|eqref|autoref|cref|Cref|label)\*?\s*(?:\[[^]]*\]\s*)*\{[^{}]+\}")
MATH_ENV = re.compile(r"\\(?:begin|end)\s*\{(?:equation\*?|align\*?|gather\*?|multline\*?|math|displaymath)\}")
OVERFULL = re.compile(r"Overfull \\hbox \(([\d.]+)pt too wide\) in paragraph at lines? ([\d-]+)")
CJK = re.compile(r"[\u3400-\u9fff]")
ASCII_LETTERS = re.compile(r"[A-Za-z]")


def _visible_text(value: str) -> str:
    value = re.sub(r"\\[A-Za-z@]+\*?", "", value)
    value = re.sub(r"\\.", "", value)
    return re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()


def _fields(text: str, path: str) -> list[dict[str, Any]]:
    rows = []
    for match in FIELD.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if "%" in text[line_start:match.start()]:
            continue
        end = _balanced_end(text, match.end() - 1, "{", "}")
        if end <= match.end() or end > len(text):
            continue
        visible = _visible_text(text[match.end():end - 1])
        if len(ASCII_LETTERS.findall(visible)) >= 8 and not CJK.search(visible):
            rows.append({"path": path, "line": text.count("\n", 0, match.start()) + 1,
                         "field": match.group(1).lower(), "preview": visible[:160]})
    return rows


def _structure(text: str) -> dict[str, Counter[str]]:
    active = active_tex(text)
    return {
        "references": Counter(REFERENCE.findall(active)),
        "math_environments": Counter(MATH_ENV.findall(active)),
        "math_delimiters": Counter({"$": len(re.findall(r"(?<!\\)\$", active)),
                                    r"\(": active.count(r"\("), r"\)": active.count(r"\)"),
                                    r"\[": active.count(r"\["), r"\]": active.count(r"\]")}),
    }


def _counter_difference(before: Counter[str], after: Counter[str]) -> dict[str, Any]:
    removed = list((before - after).elements())
    added = list((after - before).elements())
    return {"removed": removed[:30], "added": added[:30],
            "removed_count": len(removed), "added_count": len(added)}


def _render_previews(pdf: Path, output_dir: Path, pages: int) -> list[str]:
    executable = shutil.which("pdftoppm")
    if executable is None:
        return []
    preview_dir = output_dir / "previews" / sha256_file(pdf)[:12]
    preview_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for page in range(1, pages + 1):
        prefix = preview_dir / f"page-{page:03d}"
        image = prefix.with_suffix(".png")
        if not image.is_file():
            result = subprocess.run(
                [executable, "-f", str(page), "-l", str(page), "-scale-to", "1100",
                 "-png", "-singlefile", str(pdf), str(prefix)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                timeout=60, check=False,
            )
            if result.returncode != 0 or not image.is_file():
                raise RuntimeError(f"Could not render PDF page {page}: "
                                   f"{result.stderr.decode('utf-8', errors='replace')[-300:]}")
        paths.append(image.relative_to(output_dir).as_posix())
    return paths


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    lines = [f"# {report['paper_id']} 中文论文验收报告", "",
             f"状态：**{report['status']}**", "",
             f"阶段二片段：{report['stage2_segments_translated']}/{report['stage2_segments_total']}；"
             f"增强提取器发现尚未处理的原文片段：{len(report['untranslated_spans'])}。", "",
             f"引用命令差异：{report['reference_differences']['removed_count']} 个删除、"
             f"{report['reference_differences']['added_count']} 个新增；"
             f"数学结构差异：{report['math_differences_count']}。", "",
             f"PDF：{'与当前草稿一致' if report['pdf_current'] else '已过期或不存在'}；"
             f"页数：{report['pdf_pages'] or '未知'}；缺字警告：{report['missing_glyph_warnings']}；"
             f"超宽文字盒：{len(report['overfull_hboxes'])} 处。", ""]
    if report["issues"]:
        lines.extend(["## 待处理事项", ""])
        lines.extend(f"- [{item['severity']}] {item['message']}" for item in report["issues"])
        lines.append("")
    if report["english_only_fields"]:
        lines.extend(["## 疑似漏译字段", "", "| 位置 | 字段 | 内容预览 |", "|---|---|---|"])
        for field in report["english_only_fields"]:
            preview = field["preview"].replace("|", r"\|")
            lines.append(f"| {field['path']}:{field['line']} | {field['field']} | {preview} |")
        lines.append("")
    if report["untranslated_spans"]:
        lines.extend(["## 增强提取器发现的原文残留", ""])
        for item in report["untranslated_spans"][:30]:
            lines.append(f"- {item['path']}:{item['line']} — {item['preview']}")
        lines.append("")
    if report["previews"]:
        lines.extend(["## 逐页预览", ""])
        if not report["pdf_current"]:
            lines.extend(["**注意：以下来自旧版 PDF，不代表当前草稿的排版。**", ""])
        for index, path in enumerate(report["previews"], 1):
            lines.extend([f"### 第 {index} 页", "", f"![第 {index} 页]({path})", ""])
    markdown = output_dir / "report.md"
    temporary_md = markdown.with_suffix(".md.tmp")
    temporary_md.write_text("\n".join(lines), encoding="utf-8")
    temporary_md.replace(markdown)


def audit_paper(root: Path, paper_id: str, *, render_pages: bool = True) -> dict[str, Any]:
    entry = next((paper for paper in inventory(root)["papers"] if paper["id"] == paper_id), None)
    if entry is None or entry["status"] != "ready":
        raise ValueError(f"No usable source for {paper_id}")
    source_dir = root / entry["source_dir"]
    stage2_dir = root / "results" / "stage2" / paper_id
    stage2_path = stage2_dir / "report.json"
    if not stage2_path.is_file():
        raise ValueError(f"Stage 2 report is missing for {paper_id}")
    stage2 = json.loads(stage2_path.read_text(encoding="utf-8"))
    if not stage2.get("complete"):
        raise ValueError(f"Stage 2 draft is incomplete for {paper_id}")
    translated = stage2_dir / "translated"
    if translated.is_symlink() or not translated.is_dir() or translated.resolve().parent != stage2_dir.resolve():
        raise ValueError("Translated source directory is missing or unsafe")
    files = source_files(translated)
    draft_sha256 = fingerprint(translated, files)
    source_paths = reachable_tex(source_dir, entry["main_tex"])
    source_structure = {name: Counter() for name in ("references", "math_environments", "math_delimiters")}
    draft_structure = {name: Counter() for name in source_structure}
    english_fields: list[dict[str, Any]] = []
    untranslated_spans: list[dict[str, Any]] = []
    for relative in source_paths:
        source_text = (source_dir / relative).read_text(encoding="utf-8")
        draft_path = translated / relative
        if not draft_path.is_file():
            raise ValueError(f"Translated source file is missing: {relative}")
        draft_text = draft_path.read_text(encoding="utf-8")
        english_fields.extend(_fields(draft_text, relative))
        for name, tokens in _structure(source_text).items():
            source_structure[name].update(tokens)
        for name, tokens in _structure(draft_text).items():
            draft_structure[name].update(tokens)
        for segment in extract_segments(source_text, relative,
                                        main_file=relative == entry["main_tex"], enhanced=True):
            # A completed checkpoint does not prove that the model changed the prose.
            # Look for unchanged visible source spans, including legacy segments.
            if segment.original in draft_text:
                untranslated_spans.append({
                    "path": relative, "line": source_text.count("\n", 0, segment.start) + 1,
                    "preview": _visible_text(segment.text)[:160],
                })
    reference_diff = _counter_difference(source_structure["references"],
                                         draft_structure["references"])
    math_env_diff = _counter_difference(source_structure["math_environments"],
                                        draft_structure["math_environments"])
    math_delimiter_diff = _counter_difference(source_structure["math_delimiters"],
                                              draft_structure["math_delimiters"])
    math_diff_count = sum(diff["removed_count"] + diff["added_count"]
                          for diff in (math_env_diff, math_delimiter_diff))
    stage3_path = root / "results" / "stage3" / paper_id / "report.json"
    stage3 = json.loads(stage3_path.read_text(encoding="utf-8")) if stage3_path.is_file() else {}
    pdf = root / stage3["pdf"] if stage3.get("pdf") else None
    pdf_root = (root / "output" / "pdf").resolve()
    if pdf and (pdf.suffix.lower() != ".pdf" or pdf.resolve().parent != pdf_root):
        raise ValueError("Stage 3 report points outside output/pdf")
    pdf_current = bool(pdf and pdf.is_file() and stage3.get("status") == "compiled" and
                       stage3.get("cache_key", {}).get("draft_sha256") == draft_sha256)
    pdf_pages = pdf_page_count(pdf) if pdf and pdf.is_file() else None
    log_path = root / stage3["log"] if stage3.get("log") else None
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path and log_path.is_file() else ""
    overfull = list(dict.fromkeys((float(width), lines) for width, lines in OVERFULL.findall(log)))
    issues: list[dict[str, str]] = []

    def flag(severity: str, code: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "message": message})

    if not pdf_current:
        flag("阻断", "stale_pdf", "PDF 不对应当前草稿；更新编译结果后才能验收最新排版。")
    if reference_diff["removed_count"] or reference_diff["added_count"]:
        flag("阻断", "reference_change", "引用或标签命令与原文不一致。")
    if math_diff_count:
        flag("阻断", "math_change", "数学环境或公式分隔符与原文不一致。")
    missing_glyphs = log.count("Missing character:")
    if missing_glyphs:
        flag("阻断", "missing_glyph", f"编译日志包含 {missing_glyphs} 条缺字警告。")
    if english_fields or untranslated_spans:
        flag("复核", "untranslated", f"发现 {len(english_fields)} 个疑似英文可见字段、"
             f"{len(untranslated_spans)} 个原文残留片段。")
    if overfull:
        flag("复核", "overfull", f"编译日志包含 {len(overfull)} 处不同的超宽文字盒。")
    if pdf_pages is None and pdf and pdf.is_file():
        flag("复核", "page_count_unavailable", "无法读取 PDF 页数。")
    status = "blocked" if any(item["severity"] == "阻断" for item in issues) else \
             "needs_review" if issues else "pass"
    output_dir = root / "results" / "qa" / paper_id
    if output_dir.is_symlink():
        raise ValueError("QA output directory is a symlink")
    previews = _render_previews(pdf, output_dir, pdf_pages) if (render_pages and pdf and
                                                             pdf.is_file() and pdf_pages) else []
    report = {
        "paper_id": paper_id, "status": status,
        "source_sha256": entry["source_sha256"], "draft_sha256": draft_sha256,
        "stage2_segments_translated": stage2.get("segments_translated"),
        "stage2_segments_total": stage2.get("segments_total"),
        "english_only_fields": english_fields, "untranslated_spans": untranslated_spans,
        "reference_differences": reference_diff,
        "math_environment_differences": math_env_diff,
        "math_delimiter_differences": math_delimiter_diff,
        "math_differences_count": math_diff_count,
        "pdf": stage3.get("pdf"), "pdf_current": pdf_current,
        "pdf_pages": pdf_pages, "original_pages": pdf_page_count(root / entry["pdf"]),
        "missing_glyph_warnings": missing_glyphs,
        "overfull_hboxes": [{"width_pt": width, "source_lines": lines} for width, lines in overfull],
        "previews": previews, "issues": issues,
    }
    _write_report(output_dir, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--id")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.all:
            paper_ids = []
            for paper in inventory(root)["papers"]:
                report_path = root / "results" / "stage2" / paper["id"] / "report.json"
                if not report_path.is_file():
                    continue
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if report.get("complete"):
                    paper_ids.append(paper["id"])
        else:
            paper_ids = [args.id]
        reports = [audit_paper(root, paper_id, render_pages=not args.no_previews)
                   for paper_id in paper_ids]
        print(json.dumps([{"id": item["paper_id"], "status": item["status"],
                           "issues": len(item["issues"]), "pages": item["pdf_pages"]}
                          for item in reports], ensure_ascii=False, indent=2))
        return 0 if all(item["status"] == "pass" for item in reports) else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"QA failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
