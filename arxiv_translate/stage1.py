"""Inventory papers and establish an original-source compilation baseline.

Local latexmk is the default. The former LaTeX.Online backend remains available
explicitly for comparing results with the earlier online baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .local_compiler import compile_local, pdf_page_count, probe_latexmk


ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.I)
DOCUMENT_CLASS = re.compile(r"\\documentclass\s*(?:\[[^]]*\])?\s*\{", re.I)
BEGIN_DOCUMENT = re.compile(r"\\begin\s*\{document\}", re.I)
DEFAULT_ENDPOINT = "https://latexonline.cc/data"
CLIENT_TIMEZONE = timezone(timedelta(hours=8))
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
LOCAL_BASELINE_VERSION = 2


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    root = folder.resolve()
    for path in folder.rglob("*"):
        if path.name == "source.tar.gz" or not path.is_file():
            continue
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError(f"Unsafe source path: {path}")
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(folder).as_posix())


def active_tex(text: str) -> str:
    """Remove ordinary TeX comments while keeping escaped percent signs."""
    lines = []
    for line in text.splitlines():
        for index, char in enumerate(line):
            if char == "%":
                backslashes = 0
                cursor = index - 1
                while cursor >= 0 and line[cursor] == "\\":
                    backslashes += 1
                    cursor -= 1
                if backslashes % 2 == 0:
                    line = line[:index]
                    break
        lines.append(line)
    return "\n".join(lines)


def choose_main(folder: Path, files: list[Path]) -> tuple[str | None, list[str], str | None]:
    candidates: list[str] = []
    selected_text: str | None = None
    for path in files:
        if path.suffix.lower() != ".tex":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        active = active_tex(text)
        if DOCUMENT_CLASS.search(active) and BEGIN_DOCUMENT.search(active):
            candidates.append(path.relative_to(folder).as_posix())
            selected_text = text
    if len(candidates) != 1:
        return None, candidates, None
    main = candidates[0]
    if selected_text is None:
        return None, candidates, None
    directive = re.search(r"^\s*%\s*!\s*TeX\s+program\s*=\s*(pdf|xe|lua)latex\b", selected_text[:3000], re.I | re.M)
    if directive:
        engine = directive.group(1).lower() + "latex"
    elif re.search(r"\\usepackage(?:\[[^]]*\])?\s*\{[^}]*(?:fontspec|xeCJK|polyglossia)[^}]*\}", selected_text, re.I):
        engine = "xelatex"
    elif re.search(r"\\usepackage(?:\[[^]]*\])?\s*\{[^}]*luatexja[^}]*\}", selected_text, re.I):
        engine = "lualatex"
    else:
        engine = "pdflatex"
    return main, candidates, engine


def fingerprint(folder: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(folder).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, Any]:
    pdf_dir = root / "pdf"
    latex_dir = root / "latex"
    if not pdf_dir.is_dir() or not latex_dir.is_dir():
        raise FileNotFoundError("Expected both pdf/ and latex/ directories")

    entries: list[dict[str, Any]] = []
    pdf_ids: set[str] = set()
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        paper_id = pdf.stem
        pdf_ids.add(paper_id)
        entry: dict[str, Any] = {
            "id": paper_id,
            "pdf": pdf.relative_to(root).as_posix(),
            "pdf_bytes": pdf.stat().st_size,
            "pdf_sha256": sha256_file(pdf),
            "source_dir": None,
            "source_files": 0,
            "tex_files": 0,
            "source_sha256": None,
            "main_tex": None,
            "main_candidates": [],
            "engine": None,
            "status": "unclassified",
        }
        with pdf.open("rb") as stream:
            header = stream.read(5)
        if header != b"%PDF-":
            entry["status"] = "invalid_pdf"
        elif not ARXIV_ID.fullmatch(paper_id):
            entry["status"] = "unrecognized_id"
        else:
            source_dir = latex_dir / paper_id
            if not source_dir.is_dir():
                entry["status"] = "no_source"
            else:
                entry["source_dir"] = source_dir.relative_to(root).as_posix()
                try:
                    files = source_files(source_dir)
                    entry["source_files"] = len(files)
                    entry["tex_files"] = sum(path.suffix.lower() == ".tex" for path in files)
                    entry["source_sha256"] = fingerprint(source_dir, files)
                    main, candidates, engine = choose_main(source_dir, files)
                    entry["main_tex"] = main
                    entry["main_candidates"] = candidates
                    entry["engine"] = engine
                    entry["status"] = "ready" if main else "main_ambiguous" if candidates else "main_missing"
                except (OSError, ValueError) as error:
                    entry["status"] = "source_error"
                    entry["error"] = str(error)
        entries.append(entry)

    orphan_sources = sorted(path.name for path in latex_dir.iterdir() if path.is_dir() and path.name not in pdf_ids)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(CLIENT_TIMEZONE).isoformat(),
        "root": str(root),
        "counts": {
            "pdfs": len(entries),
            "ready": sum(entry["status"] == "ready" for entry in entries),
            "no_source": sum(entry["status"] == "no_source" for entry in entries),
        },
        "orphan_source_dirs": orphan_sources,
        "papers": entries,
    }


def make_source_archive(folder: Path, files: list[Path]) -> bytes:
    size = sum(path.stat().st_size for path in files)
    if size > MAX_SOURCE_BYTES:
        raise ValueError(f"Source tree exceeds {MAX_SOURCE_BYTES} bytes")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        for path in files:
            arcname = path.relative_to(folder).as_posix()
            data = path.read_bytes()
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def online_compile(archive: bytes, main_tex: str, engine: str, endpoint: str, timeout: int) -> tuple[int, bytes, str]:
    boundary = uuid.uuid4().hex
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="source.tar.bz2"\r\n'
        "Content-Type: application/x-bzip2\r\n\r\n"
    ).encode("ascii")
    body = prefix + archive + f"\r\n--{boundary}--\r\n".encode("ascii")
    query = urllib.parse.urlencode({"target": main_tex, "command": engine, "force": "true"})
    url = endpoint.rstrip("?") + "?" + query
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "arxiv-translate-stage1/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("Compiler response exceeds allowed size")
            return response.status, payload, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        payload = error.read(MAX_RESPONSE_BYTES + 1)
        return error.code, payload[:MAX_RESPONSE_BYTES], error.headers.get("Content-Type", "")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def first_error_line(log: str) -> str:
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    markers = ("latex error:", "package ", "undefined control sequence", "critical package ",
               "fatal error", "i found no \\bibdata", "not found")
    line = next((line for line in lines if any(marker in line.lower() for marker in markers)
                 and ("error" in line.lower() or "undefined control" in line.lower()
                      or "i found no" in line.lower() or "not found" in line.lower())),
                next((line for line in lines if line != "!" and not line.startswith("Initial Win CP")
                      and not line.startswith("perl: warning:")), ""))
    return line[:240]


def local_result_summary(manifest: dict[str, Any], results: dict[str, Any]) -> str:
    by_id = results.get("papers", {})
    statuses = [by_id.get(entry["id"], {}).get("status", "not_run" if entry["status"] == "ready" else entry["status"])
                for entry in manifest["papers"]]
    rows = [
        "# 阶段 1：原文本地编译基线", "",
        f"生成时间：{datetime.now(CLIENT_TIMEZONE).isoformat()}",
        f"编译器：{results.get('latexmk', '未检测')}；{results.get('latexmk_version') or '版本未检测'}", "",
        f"PDF：{manifest['counts']['pdfs']} 篇；可识别源码：{manifest['counts']['ready']} 篇；无源码：{manifest['counts']['no_source']} 篇。",
        f"本地结果：成功 {statuses.count('compiled')}；编译报错 {statuses.count('compile_failed')}；内容校验未通过 {statuses.count('validation_failed')}；超时 {statuses.count('timeout')}；编译器不可用 {statuses.count('compiler_unavailable')}；未运行 {statuses.count('not_run')}。",
        "", "| arXiv 版本 | 主文件 | 编译器 | 结果 | 原稿/重编页数 | 兼容处理 | 首条错误 |", "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in manifest["papers"]:
        result = by_id.get(entry["id"], {})
        status = result.get("status", "not_run" if entry["status"] == "ready" else entry["status"])
        error = (result.get("first_error") or result.get("error") or "—").replace("|", "\\|").replace("`", "'")[:180]
        actions = ", ".join(result.get("compatibility_actions", [])) or "—"
        pages = (f"{result['original_pages']}/{result['compiled_pages']}"
                 if result.get("original_pages") is not None and result.get("compiled_pages") is not None else "—")
        if result.get("page_count_match") is False:
            pages += " ⚠"
        rows.append(f"| {entry['id']} | {entry['main_tex'] or '—'} | {entry['engine'] or '—'} | {status} | {pages} | {actions} | {error} |")
    rows.extend(["", "`compile_failed` 表示本机 LaTeX 返回错误；`validation_failed` 表示虽然生成了 PDF，但参考文献等内容校验未通过。`compiler_unavailable` 与 `timeout` 不代表论文源码有问题。",
                 "页数列需安装 Poppler 的 `pdfinfo` 才能自动填写；⚠ 表示页数不同，仍需人工核对内容与排版。",
                 "编译在源码的临时副本中运行，原始 PDF 和 LaTeX 文件未修改。", ""])
    return "\n".join(rows)


def run_local_baseline(root: Path, manifest: dict[str, Any], output_dir: Path, *, ids: set[str] | None,
                       latexmk: str, timeout: int, force: bool) -> dict[str, Any]:
    result_path = output_dir / "baseline.json"
    results = (json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else
               {"schema_version": 1, "compiler": "local-latexmk", "papers": {}})
    executable, version_or_error = probe_latexmk(latexmk)
    results.update({"compiler": "local-latexmk", "latexmk": executable or latexmk,
                    "latexmk_version": version_or_error if executable else None,
                    "generated_at": datetime.now(CLIENT_TIMEZONE).isoformat()})
    for entry in manifest["papers"]:
        paper_id = entry["id"]
        if ids is not None and paper_id not in ids:
            continue
        if entry["status"] != "ready":
            results["papers"][paper_id] = {"status": entry["status"]}
            continue
        previous = results["papers"].get(paper_id, {})
        if (not force and executable and previous.get("status") == "compiled"
                and previous.get("pipeline_version") == LOCAL_BASELINE_VERSION
                and previous.get("latexmk") == executable
                and previous.get("latexmk_version") == version_or_error
                and previous.get("main_tex") == entry["main_tex"]
                and previous.get("engine") == entry["engine"]
                and previous.get("source_sha256") == entry["source_sha256"]):
            print(f"{paper_id}: skipped ({previous['status']})", flush=True)
            continue
        result: dict[str, Any] = {
            "status": "not_run", "main_tex": entry["main_tex"], "engine": entry["engine"],
            "pipeline_version": LOCAL_BASELINE_VERSION,
            "source_sha256": entry["source_sha256"], "latexmk": executable or latexmk,
            "latexmk_version": version_or_error if executable else None,
        }
        log_path = output_dir / "logs" / f"{paper_id}.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if executable is None:
            result["status"] = "compiler_unavailable"
            result["error"] = version_or_error
            log_path.write_text(version_or_error + "\n", encoding="utf-8")
        else:
            try:
                source_dir = root / entry["source_dir"]
                pdf_path = output_dir / "compiled" / f"{paper_id}.pdf"
                outcome = compile_local(source_dir, source_files(source_dir), entry["main_tex"],
                                        entry["engine"], executable, timeout, pdf_path, log_path)
                result.update(outcome)
                if outcome["status"] == "compiled":
                    result["output_pdf"] = pdf_path.relative_to(root).as_posix()
                elif pdf_path.is_file():
                    # A previous PDF must not remain under compiled/ after a failed rerun.
                    previous_dir = output_dir / "previous"
                    previous_dir.mkdir(parents=True, exist_ok=True)
                    archived = previous_dir / f"{paper_id}-{datetime.now(CLIENT_TIMEZONE):%Y%m%dT%H%M%S%f}.pdf"
                    shutil.move(str(pdf_path), str(archived))
                    result["previous_output_pdf"] = archived.relative_to(root).as_posix()
            except (OSError, ValueError) as error:
                result["status"] = "source_error"
                result["error"] = str(error)
                log_path.write_text(str(error) + "\n", encoding="utf-8")
        result["log"] = log_path.relative_to(root).as_posix()
        if result["status"] != "compiled" and not result.get("first_error") and log_path.is_file():
            result["first_error"] = first_error_line(log_path.read_text(encoding="utf-8", errors="replace"))
        results["papers"][paper_id] = result
        write_json(result_path, results)
        (output_dir / "summary.md").write_text(local_result_summary(manifest, results), encoding="utf-8")
        print(f"{paper_id}: {result['status']}", flush=True)
    for entry in manifest["papers"]:
        result = results["papers"].get(entry["id"], {})
        if result.get("status") != "compiled" or not result.get("output_pdf"):
            continue
        original_pages = pdf_page_count(root / entry["pdf"])
        compiled_pages = pdf_page_count(root / result["output_pdf"])
        if original_pages is not None and compiled_pages is not None:
            result.update({"original_pages": original_pages, "compiled_pages": compiled_pages,
                           "page_count_match": original_pages == compiled_pages})
    write_json(result_path, results)
    (output_dir / "summary.md").write_text(local_result_summary(manifest, results), encoding="utf-8")
    return results


def normalize_results(root: Path, results: dict[str, Any]) -> None:
    """Upgrade previously saved results without sending another request."""
    stored_endpoint = results.get("endpoint", DEFAULT_ENDPOINT)
    for result in results.get("papers", {}).values():
        result.setdefault("endpoint", stored_endpoint)
        if result.get("http_status") in {413, 429}:
            result["status"] = "service_limit"
            result["first_error"] = ("HTTP 413: 上传文件超过在线服务限制" if result["http_status"] == 413
                                     else "HTTP 429: 在线服务请求频率受限")
        elif result.get("status") == "source_error" and "timed out" in result.get("error", "").lower():
            result["status"] = "network_error"
        log_name = result.get("log")
        if log_name and not result.get("first_error"):
            log_path = root / log_name
            if log_path.is_file():
                result["first_error"] = first_error_line(log_path.read_text(encoding="utf-8", errors="replace"))


def result_summary(manifest: dict[str, Any], results: dict[str, Any]) -> str:
    statuses = [results.get("papers", {}).get(entry["id"], {}).get("status", entry["status"])
                for entry in manifest["papers"]]
    rows = [
        "# 阶段 1：原文在线编译基线",
        "",
        f"生成时间：{datetime.now(CLIENT_TIMEZONE).isoformat()}",
        "",
        f"PDF：{manifest['counts']['pdfs']} 篇；含可识别主文件的源码：{manifest['counts']['ready']} 篇；无源码：{manifest['counts']['no_source']} 篇。",
        f"在线结果：成功 {statuses.count('compiled')}；编译报错 {statuses.count('compile_failed')}；服务限额 {statuses.count('service_limit')}；网络或超时 {statuses.count('network_error')}。",
        "",
        "| arXiv 版本 | 主文件 | 编译器 | 基线结果 | 首条错误 |",
        "| --- | --- | --- | --- | --- |",
    ]
    by_id = results.get("papers", {})
    for entry in manifest["papers"]:
        result = by_id.get(entry["id"], {})
        status = result.get("status", "not_run" if entry["status"] == "ready" else entry["status"])
        error = (result.get("first_error") or result.get("error") or "—").replace("|", "\\|").replace("`", "'")[:180]
        rows.append(f"| {entry['id']} | {entry['main_tex'] or '—'} | {entry['engine'] or '—'} | {status} | {error} |")
    rows.extend([
        "",
        "`compiled` 表示该线上环境返回了可读取的 PDF；`compile_failed` 表示编译服务返回错误日志。",
        "`service_limit`、`service_error`、`network_error` 和 `protocol_error` 不说明论文源码有问题。",
        "原始 PDF 和源码均未修改。",
        "",
    ])
    return "\n".join(rows)


def run_baseline(root: Path, manifest: dict[str, Any], output_dir: Path, *, ids: set[str] | None,
                 endpoint: str, timeout: int, delay: float, force: bool) -> dict[str, Any]:
    result_path = output_dir / "baseline.json"
    if result_path.is_file():
        results = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        results = {"schema_version": 1, "compiler": "latexonline", "endpoint": endpoint, "papers": {}}
    normalize_results(root, results)
    results["endpoint"] = endpoint
    results["generated_at"] = datetime.now(CLIENT_TIMEZONE).isoformat()
    last_request = 0.0

    for entry in manifest["papers"]:
        paper_id = entry["id"]
        if ids is not None and paper_id not in ids:
            continue
        if entry["status"] != "ready":
            results["papers"][paper_id] = {"status": entry["status"]}
            continue
        previous = results["papers"].get(paper_id, {})
        if (not force and previous.get("endpoint") == endpoint
                and previous.get("main_tex") == entry["main_tex"]
                and previous.get("engine") == entry["engine"]
                and previous.get("source_sha256") == entry["source_sha256"]
                and previous.get("status") in {"compiled", "compile_failed"}):
            print(f"{paper_id}: skipped ({previous['status']})", flush=True)
            continue

        source_dir = root / entry["source_dir"]
        started = time.monotonic()
        result: dict[str, Any] = {
            "status": "not_run",
            "main_tex": entry["main_tex"],
            "engine": entry["engine"],
            "source_sha256": entry["source_sha256"],
            "endpoint": endpoint,
        }
        try:
            archive = make_source_archive(source_dir, source_files(source_dir))
            if last_request:
                time.sleep(max(0.0, delay - (time.monotonic() - last_request)))
            last_request = time.monotonic()
            status_code, payload, content_type = online_compile(archive, entry["main_tex"], entry["engine"], endpoint, timeout)
            result["http_status"] = status_code
            if 200 <= status_code < 300 and payload.startswith(b"%PDF-"):
                pdf_path = output_dir / "compiled" / f"{paper_id}.pdf"
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.write_bytes(payload)
                result["status"] = "compiled"
                result["output_pdf"] = pdf_path.relative_to(root).as_posix()
                result["output_pdf_bytes"] = len(payload)
            else:
                result["status"] = ("service_limit" if status_code in {413, 429} else
                                    "compile_failed" if status_code in {400, 422} else
                                    "service_error" if status_code >= 500 or status_code in {401, 403, 404} else
                                    "protocol_error")
                log_path = output_dir / "logs" / f"{paper_id}.txt"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(payload.decode("utf-8", errors="replace"), encoding="utf-8")
                result["log"] = log_path.relative_to(root).as_posix()
                result["content_type"] = content_type
                result["first_error"] = ("HTTP 413: 上传文件超过在线服务限制" if status_code == 413 else
                                         "HTTP 429: 在线服务请求频率受限" if status_code == 429 else
                                         first_error_line(payload.decode("utf-8", errors="replace")))
        except (TimeoutError, urllib.error.URLError) as error:
            result["status"] = "network_error"
            result["error"] = str(error)
        except (OSError, ValueError) as error:
            result["status"] = "source_error"
            result["error"] = str(error)
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        results["papers"][paper_id] = result
        write_json(result_path, results)
        (output_dir / "summary.md").write_text(result_summary(manifest, results), encoding="utf-8")
        print(f"{paper_id}: {result['status']} ({result['duration_seconds']}s)", flush=True)
    write_json(result_path, results)
    (output_dir / "summary.md").write_text(result_summary(manifest, results), encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "baseline", "doctor"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project directory containing pdf/ and latex/")
    parser.add_argument("--id", action="append", dest="ids", help="Compile only this arXiv version; may be repeated")
    parser.add_argument("--backend", choices=("local", "online"), default="local", help="Compilation backend (default: local)")
    parser.add_argument("--latexmk", default="latexmk", help="Path or command name for local latexmk")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Online compiler multipart endpoint (online backend only)")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout per compilation in seconds")
    parser.add_argument("--delay", type=float, default=3.0, help="Minimum seconds between online requests")
    parser.add_argument("--force", action="store_true", help="Recompile already classified papers")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = root / "results" / "stage1"
    try:
        if args.command == "doctor":
            executable, detail = probe_latexmk(args.latexmk)
            print(f"latexmk: {executable or 'unavailable'}\n{detail}")
            return 0 if executable else 1
        manifest = inventory(root)
        write_json(output_dir / "manifest.json", manifest)
        if args.command == "scan":
            prior_path = output_dir / "baseline.json"
            prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.is_file() else {"papers": {}}
            normalize_results(root, prior)
            if prior_path.is_file():
                write_json(prior_path, prior)
            (output_dir / "summary.md").write_text(result_summary(manifest, prior), encoding="utf-8")
            local_path = output_dir / "local" / "baseline.json"
            if local_path.is_file():
                local = json.loads(local_path.read_text(encoding="utf-8"))
                (local_path.parent / "summary.md").write_text(local_result_summary(manifest, local), encoding="utf-8")
            print(f"Scanned {manifest['counts']['pdfs']} PDFs; {manifest['counts']['ready']} ready; {manifest['counts']['no_source']} without source")
            return 0
        selected = set(args.ids) if args.ids else None
        if selected is not None:
            unknown = selected - {entry["id"] for entry in manifest["papers"]}
            if unknown:
                parser.error(f"Unknown paper IDs: {', '.join(sorted(unknown))}")
        if args.backend == "local":
            results = run_local_baseline(root, manifest, output_dir / "local", ids=selected,
                                         latexmk=args.latexmk, timeout=args.timeout, force=args.force)
        else:
            results = run_baseline(root, manifest, output_dir, ids=selected, endpoint=args.endpoint,
                                   timeout=args.timeout, delay=args.delay, force=args.force)
        statuses = [results["papers"].get(entry["id"], {}).get("status", "not_run") for entry in manifest["papers"]]
        print(f"Baseline: {statuses.count('compiled')} compiled, {statuses.count('compile_failed')} compiler failures, {statuses.count('validation_failed')} validation failures, {statuses.count('not_run')} not run")
        local_failures = {"compiler_unavailable", "compile_failed", "validation_failed", "timeout", "source_error", "output_error"}
        return 1 if args.backend == "local" and any(status in local_failures for status in statuses) else 0
    except (OSError, ValueError) as error:
        print(f"Stage 1 failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
