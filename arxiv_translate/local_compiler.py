"""Run a paper's original LaTeX source with a locally installed latexmk."""

from __future__ import annotations

import base64
import hashlib
import locale
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


MAX_LOG_BYTES = 10 * 1024 * 1024
ENGINE_OPTIONS = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
CAS_CLASS = re.compile(r"\\documentclass\s*(?:\[[^]]*\])?\s*\{cas-(?:dc|sc)\}", re.I)
DIRECT_BBL = re.compile(r"\\(?:input|include)\s*\{([^}]+\.bbl)\}", re.I)
UNRESOLVED_CITATIONS = re.compile(r"Latex failed to resolve\s+\d+\s+citation\(s\)", re.I)
MISSING_BIB_ENTRY = re.compile(r'Warning--I didn\'t find a database entry for "([^"]+)"')
CAS_EMAIL_ASSET = Path(__file__).with_name("assets") / "cas-email.jpeg.b64"
CAS_EMAIL_SHA256 = "51cadbff5919f51f76f6f9238b18c9b5d512ea9234cb6d7fef7b53d9767fa845"


def decode_output(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getencoding(), errors="replace")


def setup_hint(output: str) -> str | None:
    if "fresh TeX installation" in output or "finish the setup" in output:
        return "MiKTeX 尚未完成首次设置；请打开 MiKTeX Console 完成初始化。"
    if "RegOpenKeyExW" in output and "Result: 5" in output:
        return "MiKTeX 用户配置访问被拒绝；请在有权限的用户会话中检查安装。"
    return None


def probe_latexmk(executable: str) -> tuple[str | None, str]:
    """Return the executable path and version, or a useful setup error."""
    resolved = shutil.which(executable)
    if resolved is None:
        return None, f"找不到 {executable}；请先安装并初始化 MiKTeX 或 TeX Live。"
    try:
        result = subprocess.run(
            [resolved, "-v"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as error:
        partial = decode_output(error.stdout or b"").strip()
        return None, f"latexmk 版本检查超过 30 秒。{setup_hint(partial) or partial[-500:]}".strip()
    except OSError as error:
        return None, f"无法启动 latexmk: {error}"
    output = decode_output(result.stdout).strip()
    if result.returncode != 0:
        return None, f"latexmk 尚不可用（退出码 {result.returncode}）：{setup_hint(output) or output[-500:]}"
    version = next((line.strip() for line in output.splitlines() if "latexmk" in line.lower()), output[:200])
    return resolved, version


def pdf_page_count(path: Path) -> int | None:
    """Read PDF page count when Poppler's pdfinfo is installed."""
    executable = shutil.which("pdfinfo")
    if executable is None or not path.is_file():
        return None
    try:
        result = subprocess.run(
            [executable, str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"^Pages:\s*(\d+)\s*$", decode_output(result.stdout), re.M)
    return int(match.group(1)) if match else None


def prepare_compatibility(work: Path, main_tex: str) -> tuple[list[str], Path | None]:
    """Repair known incomplete source bundles inside the disposable copy only."""
    text = (work / main_tex).read_text(encoding="utf-8", errors="replace")
    active = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    actions: list[str] = []
    if CAS_CLASS.search(active):
        thumbnail = work / "thumbnails" / "cas-email.jpeg"
        if not thumbnail.exists():
            image = base64.b64decode(CAS_EMAIL_ASSET.read_text(encoding="ascii"), validate=False)
            if (hashlib.sha256(image).hexdigest() != CAS_EMAIL_SHA256
                    or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9")):
                raise ValueError("Bundled CAS email thumbnail is not a valid JPEG")
            thumbnail.parent.mkdir(parents=True, exist_ok=True)
            thumbnail.write_bytes(image)
            actions.append("restored_ctan_cas_email_thumbnail")

    tikz_figure = work / "figures" / "a2a-attestation.tex"
    if tikz_figure.is_file():
        figure_text = tikz_figure.read_text(encoding="utf-8", errors="replace")
        original_label = r"{co-located: \KerA${} = {}$\KerB}"
        if original_label in figure_text:
            tikz_figure.write_text(figure_text.replace(original_label,
                                                       r"{co-located: Kernel$_A$ = Kernel$_B$}"),
                                   encoding="utf-8")
            actions.append("normalized_tikz_node_label")

    # arXiv often supplies the generated .bbl instead of the .bib database.
    # \bibliography{...} still reads this file, so a direct \input is not required.
    bundled_bbl = work / Path(main_tex).with_suffix(".bbl")
    if not bundled_bbl.is_file():
        bundled_bbl = None
    for match in DIRECT_BBL.finditer(active):
        candidate = (work / match.group(1)).resolve()
        if candidate.is_file() and (candidate == work.resolve() or work.resolve() in candidate.parents):
            bundled_bbl = candidate
            break
    if bundled_bbl:
        actions.append("preserved_bundled_bbl_without_bibtex")
    return actions, bundled_bbl


def compile_local(source_dir: Path, files: list[Path], main_tex: str, engine: str,
                  executable: str, timeout: int, output_pdf: Path, log_path: Path,
                  *, prepare_tex: Callable[[Path, str], list[str]] | None = None,
                  temp_root: Path | None = None) -> dict[str, Any]:
    """Compile a disposable copy, never writing generated files into the source tree."""
    if engine not in ENGINE_OPTIONS:
        raise ValueError(f"Unsupported LaTeX engine: {engine}")
    source_root = source_dir.resolve()
    main_source = (source_dir / main_tex).resolve()
    if main_source != source_root and source_root not in main_source.parents:
        raise ValueError(f"Main TeX file escapes source tree: {main_tex}")
    if not main_source.is_file() or main_source not in files:
        raise ValueError(f"Main TeX file is missing: {main_tex}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    scratch = temp_root or output_pdf.parent.parent
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arxiv-compile-", dir=scratch) as temp:
        work = Path(temp)
        for source in files:
            relative = source.relative_to(source_dir)
            destination = work / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        actions, bundled_bbl = prepare_compatibility(work, main_tex)
        if prepare_tex is not None:
            actions.extend(prepare_tex(work, main_tex))
        bundled_bbl_hash = hashlib.sha256(bundled_bbl.read_bytes()).digest() if bundled_bbl else None
        command = [
            executable, "-norc", ENGINE_OPTIONS[engine], "-interaction=nonstopmode",
            "-halt-on-error", "-file-line-error", "-no-shell-escape", "-cd-",
        ]
        if bundled_bbl:
            command.append("-bibtex-")
        command.append(main_tex)
        with log_path.open("wb") as stream:
            try:
                process = subprocess.run(
                    command, cwd=work, stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT, timeout=timeout, check=False,
                )
                returncode = process.returncode
                status = "compiled" if returncode == 0 else "compile_failed"
            except subprocess.TimeoutExpired:
                returncode = None
                status = "timeout"
            except OSError as error:
                returncode = None
                status = "compiler_unavailable"
                stream.write(f"\n无法启动编译器: {error}\n".encode("utf-8", errors="replace"))

        # LaTeX output may use the system code page. Save a bounded UTF-8 report.
        raw_log = log_path.read_bytes()
        log = decode_output(raw_log[:MAX_LOG_BYTES])
        if len(raw_log) > MAX_LOG_BYTES:
            log += "\n[编译日志已截断]\n"
        if status == "timeout":
            log += f"\n编译超过 {timeout} 秒，已终止。\n"
        log_path.write_text(log, encoding="utf-8")
        result: dict[str, Any] = {
            "status": status, "returncode": returncode,
            "duration_seconds": round(time.monotonic() - started, 2),
            "compatibility_actions": actions,
        }
        if status == "compiled":
            if bundled_bbl and (not bundled_bbl.is_file() or
                                hashlib.sha256(bundled_bbl.read_bytes()).digest() != bundled_bbl_hash):
                result["status"] = "validation_failed"
                result["first_error"] = "随源码提供的 .bbl 参考文献在编译时被覆盖或删除。"
            elif UNRESOLVED_CITATIONS.search(log):
                result["status"] = "validation_failed"
                missing_entries = list(dict.fromkeys(MISSING_BIB_ENTRY.findall(log)))
                result["missing_bib_entries"] = missing_entries
                if missing_entries:
                    preview = ", ".join(missing_entries[:3])
                    result["first_error"] = (f"编译完成但仍有未解析的文献引用；.bib 缺少 "
                                             f"{len(missing_entries)} 个条目（{preview}）。")
                else:
                    result["first_error"] = "编译完成，但仍有未解析的文献引用；请检查 .bbl/.bib 是否完整。"
            elif bundled_bbl and re.search(r"Empty [`']thebibliography['`]", log, re.I):
                result["status"] = "validation_failed"
                result["first_error"] = "编译完成，但参考文献列表为空。"
        if result["status"] == "compiled":
            generated = work / f"{Path(main_tex).stem}.pdf"
            if generated.is_file():
                with generated.open("rb") as stream:
                    valid_pdf = stream.read(5) == b"%PDF-"
            else:
                valid_pdf = False
            if not valid_pdf:
                result["status"] = "output_error"
                result["first_error"] = "latexmk 成功退出，但没有找到有效 PDF。"
            else:
                shutil.copy2(generated, output_pdf)
                result["output_pdf_bytes"] = output_pdf.stat().st_size
        return result
