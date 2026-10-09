"""Small, loopback-only web UI for translating one arXiv PDF at a time.

This is a personal desktop workflow, not a multi-user or public server. LaTeX
source is untrusted code; compilation is not a security sandbox.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .qa import audit_paper
from .stage1 import (ARXIV_ID, MAX_SOURCE_BYTES, choose_main, inventory,
                     run_local_baseline, sha256_file, source_files)
from .stage2 import paper_plan, run_translation
from .stage3 import compile_paper


MODELS = {"deepseek-flash": "DEEPSEEK_API_KEY", "gpt-4o-mini": "OPENAI_API_KEY"}
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 36 * 1024 * 1024
ID_IN_TEXT = re.compile(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}v\d+)", re.I)
ID_IN_NAME = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5}v\d+)(?!\d)", re.I)


def detect_arxiv_id(pdf: bytes, filename: str) -> str:
    """Require an explicit version so the source matches the uploaded PDF."""
    from_name = ID_IN_NAME.search(filename)
    from_pdf = None
    with tempfile.TemporaryDirectory(prefix="arxiv-id-") as temp:
        path = Path(temp) / "paper.pdf"
        path.write_bytes(pdf)
        try:
            result = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "1", str(path), "-"],
                capture_output=True, timeout=20, check=False,
            )
            if result.returncode == 0:
                from_pdf = ID_IN_TEXT.search(result.stdout.decode("utf-8", errors="replace"))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    if from_pdf and from_name and from_pdf.group(1).lower() != from_name.group(1).lower():
        raise ValueError("PDF 内的 arXiv 版本号与文件名不一致，请核对论文后重试。")
    paper_id = (from_pdf or from_name).group(1) if (from_pdf or from_name) else None
    if not paper_id or not ARXIV_ID.fullmatch(paper_id):
        raise ValueError("无法识别带版本的 arXiv 编号。请将 PDF 命名为 2511.02841v2.pdf 后上传。")
    return paper_id.lower()


def _safe_source_name(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise ValueError("源码包包含不安全的文件路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or ":" in name:
        raise ValueError("源码包包含不安全的文件路径")
    return path


def _extract_source(data: bytes, target: Path) -> None:
    if data.startswith(b"%PDF-") or data.lstrip().startswith(b"<"):
        raise ValueError("arXiv 没有提供可用的 LaTeX 源码（返回了 PDF 或网页）")
    target.mkdir(parents=True, exist_ok=False)
    if tarfile.is_tarfile(io.BytesIO(data)):
        total = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive:
                relative = _safe_source_name(member.name)
                if member.isdir():
                    continue
                if not member.isfile() or member.size < 0:
                    raise ValueError("源码包包含链接或特殊文件，已拒绝解压")
                total += member.size
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("解压后的 LaTeX 源码过大")
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                reader = archive.extractfile(member)
                if reader is None:
                    raise ValueError("源码包读取失败")
                with reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
    else:
        try:
            if data.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                    source = compressed.read(MAX_SOURCE_BYTES + 1)
            else:
                source = data
        except OSError as error:
            raise ValueError("无法解压 arXiv 源码") from error
        if len(source) > MAX_SOURCE_BYTES or b"\\documentclass" not in source[:2_000_000]:
            raise ValueError("下载内容不是可识别的 LaTeX 源码")
        (target / "main.tex").write_bytes(source)
    files = source_files(target)
    main, _, _ = choose_main(target, files)
    if not main:
        raise ValueError("下载的源码没有唯一的主 TeX 文件，需要人工检查")


def download_source(root: Path, paper_id: str) -> None:
    target = root / "latex" / paper_id
    if target.exists():
        return
    request = urllib.request.Request(
        f"https://arxiv.org/src/{paper_id}",
        headers={"User-Agent": "ARXIV-Translate/0.1 (personal translation workflow)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_SOURCE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise ValueError(f"arXiv 源码下载失败（HTTP {error.code}）") from error
    except urllib.error.URLError as error:
        raise ValueError("无法连接 arXiv 下载 LaTeX 源码") from error
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("arXiv 源码包超过 50 MB")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{paper_id}-{uuid.uuid4().hex}.tmp"
    try:
        _extract_source(data, staging)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def save_pdf(root: Path, paper_id: str, data: bytes) -> None:
    target = root / "pdf" / f"{paper_id}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".pdf", delete=False) as temp:
            temp.write(data)
            temporary = Path(temp.name)
        try:
            if sha256_file(target) != sha256_file(temporary):
                raise ValueError(f"本地已有不同内容的 {paper_id}.pdf，请先核对版本；原文件未被覆盖。")
        finally:
            temporary.unlink(missing_ok=True)
    else:
        target.write_bytes(data)


class TaskApp:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.web = self.root / "web"
        self.tasks: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.busy = False

    def create(self, pdf: bytes, filename: str, model: str, key: str) -> dict:
        if model not in MODELS:
            raise ValueError("不支持的翻译模型")
        if not key.strip() or len(key) > 512 or "\n" in key or "\r" in key:
            raise ValueError("请输入有效的 API Key")
        if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF_BYTES:
            raise ValueError("请上传不超过 25 MB 的有效 PDF 文件")
        paper_id = detect_arxiv_id(pdf, filename)
        with self.lock:
            if self.busy:
                raise RuntimeError("已有任务正在运行，请等待完成后再提交。")
            self.busy = True
            token = uuid.uuid4().hex
            task = {"id": token, "paper_id": paper_id, "model": model,
                    "status": "working", "step": "preparing", "message": "正在准备论文…",
                    "segments_done": 0, "segments_total": None, "pdf_ready": False}
            self.tasks[token] = task
        threading.Thread(target=self._run, args=(token, pdf, key), daemon=True).start()
        return self.public(token)

    def public(self, token: str) -> dict | None:
        with self.lock:
            item = self.tasks.get(token)
            if item is None:
                return None
            result = item.copy()
        result.pop("pdf_path", None)
        if result["step"] == "translating":
            checkpoint = self.root / "results" / "stage2" / result["paper_id"] / "translations.json"
            if checkpoint.is_file():
                try:
                    state = json.loads(checkpoint.read_text(encoding="utf-8"))
                    result["segments_done"] = len(state.get("translations", {}))
                except (OSError, ValueError):
                    pass
        return result

    def _update(self, token: str, **fields: object) -> None:
        with self.lock:
            self.tasks[token].update(fields)

    def _run(self, token: str, pdf: bytes, key: str) -> None:
        item = self.public(token)
        assert item is not None
        paper_id, model = item["paper_id"], item["model"]
        key_name = MODELS[model]
        try:
            save_pdf(self.root, paper_id, pdf)
            self._update(token, step="source", message="正在查找 arXiv LaTeX 源码…")
            download_source(self.root, paper_id)
            manifest = inventory(self.root)
            entry = next((p for p in manifest["papers"] if p["id"] == paper_id), None)
            if not entry or entry["status"] != "ready":
                raise ValueError("LaTeX 源码未就绪；请检查是否有唯一的主 TeX 文件。")
            self._update(token, step="baseline", message="正在验证原论文可以编译…")
            baseline = run_local_baseline(
                self.root, manifest, self.root / "results" / "stage1" / "local",
                ids={paper_id}, latexmk="latexmk", timeout=180, force=False,
            )
            original = baseline["papers"].get(paper_id, {})
            if original.get("status") != "compiled":
                raise ValueError(f"原论文编译未通过（{original.get('status', 'unknown')}）；请查看阶段一日志。")
            if original.get("page_count_match") is False:
                raise ValueError("源码重编页数与上传 PDF 不一致；为避免翻译错误版本，已停止任务。")
            _, _, segments = paper_plan(self.root, paper_id)
            self._update(token, step="translating", message="正在翻译正文…", segments_total=len(segments))
            old_key = os.environ.get(key_name)
            os.environ[key_name] = key
            try:
                report = run_translation(self.root, paper_id, model, max_new_segments=None)
            finally:
                if old_key is None:
                    os.environ.pop(key_name, None)
                else:
                    os.environ[key_name] = old_key
            if not report["complete"]:
                raise RuntimeError("翻译尚未完成")
            self._update(token, step="compiling", message="正在编译中文 PDF…",
                         segments_done=report["segments_translated"])
            result = compile_paper(self.root, paper_id)
            if result.get("status") != "compiled":
                raise ValueError(f"中文 PDF 编译失败：{result.get('first_error') or result.get('status')}")
            pdf_path = (self.root / result["pdf"]).resolve()
            if self.root not in pdf_path.parents or not pdf_path.is_file():
                raise ValueError("编译结果文件不存在")
            try:
                audit_paper(self.root, paper_id, render_pages=False)
            except Exception:
                pass  # PDF is still available for manual review.
            self._update(token, status="done", step="done", message="翻译完成，请检查译文与排版。",
                         pdf_ready=True, pdf_path=str(pdf_path), pages=result.get("pages"))
        except Exception as error:
            message = str(error).replace(key, "[已隐藏]")[:500] or "任务失败，请检查日志。"
            self._update(token, status="failed", step="failed", message=message)
        finally:
            with self.lock:
                self.busy = False


def make_handler(app: TaskApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            # Never log requests, keys or error bodies.
            pass

        def _send(self, code: int, data: bytes, content_type: str, *, disposition: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'self'")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, value: dict) -> None:
            self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _allowed_origin(self) -> bool:
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            return self._allowed_host() and origin == f"http://{host}"

        def _allowed_host(self) -> bool:
            return self.headers.get("Host", "") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def do_POST(self) -> None:
            if self.path != "/api/tasks":
                return self._json(404, {"error": "未找到接口"})
            if not self._allowed_origin():
                return self._json(403, {"error": "只能从本机页面提交任务"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST_BYTES:
                    raise ValueError("请求过大或为空")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise ValueError("请求格式必须是 JSON")
                payload = json.loads(self.rfile.read(length))
                pdf = base64.b64decode(payload["pdf_base64"], validate=True)
                result = app.create(pdf, str(payload["filename"]), str(payload["model"]), str(payload["api_key"]))
            except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError) as error:
                return self._json(400, {"error": str(error)[:300]})
            except RuntimeError as error:
                return self._json(409, {"error": str(error)})
            self._json(202, result)

        def do_GET(self) -> None:
            if not self._allowed_host():
                return self._json(403, {"error": "只能从本机访问"})
            url = urlsplit(self.path)
            if url.path in {"/", "/index.html", "/styles.css", "/app.js"}:
                filename = "index.html" if url.path == "/" else url.path.lstrip("/")
                mime = {"index.html": "text/html; charset=utf-8", "styles.css": "text/css; charset=utf-8",
                        "app.js": "text/javascript; charset=utf-8"}[filename]
                return self._send(200, (app.web / filename).read_bytes(), mime)
            match = re.fullmatch(r"/api/tasks/([0-9a-f]{32})(/pdf)?", url.path)
            if not match:
                return self._json(404, {"error": "未找到内容"})
            token, wants_pdf = match.groups()
            task = app.public(token)
            if task is None:
                return self._json(404, {"error": "任务不存在"})
            if not wants_pdf:
                return self._json(200, task)
            with app.lock:
                pdf_path = app.tasks[token].get("pdf_path")
            if not task["pdf_ready"] or not pdf_path:
                return self._json(404, {"error": "PDF 尚未生成"})
            path = Path(pdf_path)
            disposition = ("attachment" if url.query == "download=1" else "inline") + "; filename=\"" + task["paper_id"] + "-zh.pdf\""
            self._send(200, path.read_bytes(), "application/pdf", disposition=disposition)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local arXiv translation page")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    app = TaskApp(args.root)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), make_handler(app))
    print("打开 http://127.0.0.1:8765/ （仅限本机）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
