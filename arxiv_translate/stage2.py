"""Stage 2: translate conservative TeX prose spans to a resumable draft tree."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Callable

from .stage1 import inventory, source_files
from .tex_segments import (PLACEHOLDER, Segment, apply_translations, extract_segments,
                           reachable_tex, render_translation, translatable_file)


OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEEPSEEK_API_URL = "https://api.deepseek.com/responses"
STATE_VERSION = 1
EXTRACTOR_VERSION = 5
SYSTEM_PROMPT = (
    "You translate academic English into accurate, fluent Simplified Chinese. "
    "Preserve technical meaning, numbers, names, and terminology. Each item has a text excerpt "
    "from a LaTeX paper plus nearby context; translate only the text field, using context only "
    "to understand the sentence. Return only the translated plain text for each id; "
    "Tokens such as [[LATEX_0]] protect original LaTeX. Keep every token exactly once, "
    "unchanged and in the same order, at the semantically corresponding position. "
    "do not add LaTeX commands, Markdown, explanations, or extra items. Keep proper nouns in "
    "English when translation would obscure their identity."
)


class IncompleteTranslationBatch(RuntimeError):
    """A model reply needs recovery, but some requested rows are usable."""

    def __init__(self, valid: dict[str, str]):
        super().__init__("Translation response omitted, duplicated, or added segment IDs")
        self.valid = valid


def _segments_for_version(source_dir: Path, main_tex: str, version: int,
                          *, exclude: dict[str, tuple[tuple[int, int], ...]] | None = None
                          ) -> list[Segment]:
    segments: list[Segment] = []
    for relative in reachable_tex(source_dir, main_tex, root_relative_inputs=version >= 5):
        if not translatable_file(relative):
            continue
        text = (source_dir / relative).read_text(encoding="utf-8")
        segments.extend(extract_segments(text, relative, main_file=relative == main_tex,
                                         enhanced=version >= 2, modern=version >= 3,
                                         preamble_metadata=version >= 4,
                                         custom_text_commands=version >= 5,
                                         exclude_ranges=(exclude or {}).get(relative, ())))
    return segments


def paper_plan(root: Path, paper_id: str) -> tuple[dict, Path, list[Segment]]:
    manifest = inventory(root)
    entry = next((item for item in manifest["papers"] if item["id"] == paper_id), None)
    if entry is None:
        raise ValueError(f"Unknown paper ID: {paper_id}")
    if entry["status"] != "ready":
        raise ValueError(f"No usable LaTeX source for {paper_id}: {entry['status']}")
    checkpoint = root / "results" / "stage2" / paper_id / "translations.json"
    existing_state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.is_file() else {}
    extractor_version = existing_state.get("extractor_version", 1 if existing_state else EXTRACTOR_VERSION)
    source_dir = root / entry["source_dir"]
    segments = _segments_for_version(source_dir, entry["main_tex"], extractor_version)
    if extractor_version < 5:
        covered: dict[str, list[tuple[int, int]]] = {}
        for segment in segments:
            covered.setdefault(segment.path, []).append((segment.start, segment.end))
        extras = _segments_for_version(source_dir, entry["main_tex"], 5,
                                       exclude={path: tuple(ranges)
                                                for path, ranges in covered.items()})
        segments.extend(extras)
    return entry, source_dir, segments


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["translations"],
        "additionalProperties": False,
    }


def _single_schema() -> dict:
    return {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }


def _validated_translation(segment: Segment, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RuntimeError("Translation response contains an empty or invalid text item")
    if "-part-" in segment.id and not segment.protected:
        # Piecewise recovery reinserts protected tokens itself. Ignore tokens
        # copied from context rather than from this particular piece.
        value = PLACEHOLDER.sub("", value).strip()
        if not value:
            raise RuntimeError(f"Empty piecewise translation: {segment.id}")
    if len(value) > max(500, len(segment.text) * 3):
        raise RuntimeError(f"Translation is unexpectedly long: {segment.id}")
    render_translation(segment, value)
    return value


def _responses_translate_batch(segments: list[Segment], model: str, *,
                               provider: str, api_url: str, api_key_env: str,
                               disable_reasoning: bool = False,
                               single_text: bool = False) -> dict[str, str]:
    key = os.environ.get(api_key_env)
    if not key:
        raise ValueError(f"{api_key_env} is not set; no translation request was sent")
    if single_text and len(segments) != 1:
        raise ValueError("Single-text translation requires exactly one segment")
    if single_text:
        user_content = {"text": segments[0].text,
                        "context_before": segments[0].context_before,
                        "context_after": segments[0].context_after}
        system_prompt = ("Translate only the text field into accurate Simplified Chinese. "
                         "Use context only for understanding. Preserve every [[LATEX_N]] token "
                         "exactly once, unchanged and in order. Return one translated text field "
                         "without LaTeX commands, Markdown, or explanation.")
        response_schema = _single_schema()
    else:
        user_content = {"translations_required": [
            {"id": s.id, "text": s.text,
             "context_before": s.context_before, "context_after": s.context_after}
            for s in segments]}
        system_prompt = SYSTEM_PROMPT
        response_schema = _schema()
    payload = {
        "model": model,
        "store": False,
        "max_output_tokens": 6000,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": "latex_translation_batch",
                            "strict": True, "schema": response_schema}},
    }
    if disable_reasoning:
        payload["reasoning"] = {"effort": "none"}
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "arxiv-translate-stage2/0.1"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                detail = error.read(1000).decode("utf-8", errors="replace")
                raise RuntimeError(f"{provider} API HTTP {error.code}: {detail}") from error
            time.sleep(2 ** attempt)
        except json.JSONDecodeError as error:
            if attempt == 2:
                raise RuntimeError(f"{provider} API returned invalid JSON") from error
            time.sleep(2 ** attempt)
    if body.get("status") == "incomplete":
        raise IncompleteTranslationBatch({})
    if body.get("status") != "completed":
        raise RuntimeError(f"{provider} response not completed: {body.get('status')}")
    output_texts: list[str] = []
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise RuntimeError("The translation request was refused")
            if content.get("type") == "output_text":
                output_texts.append(content["text"])
    if len(output_texts) != 1:
        raise IncompleteTranslationBatch({})
    parsed = json.loads(output_texts[0])
    if not isinstance(parsed, dict):
        raise IncompleteTranslationBatch({})
    if single_text:
        translations = {segments[0].id: parsed.get("text")}
    else:
        rows = parsed.get("translations")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise IncompleteTranslationBatch({})
        if len(segments) == 1 and len(rows) == 1:
            # One requested row is unambiguous even if the model rewrites its ID.
            translations = {segments[0].id: rows[0].get("text")}
        else:
            expected = {segment.id for segment in segments}
            grouped: dict[str, list[object]] = {}
            for row in rows:
                row_id = row.get("id")
                if isinstance(row_id, str) and row_id in expected:
                    grouped.setdefault(row_id, []).append(row.get("text"))
            valid: dict[str, str] = {}
            for segment in segments:
                values = grouped.get(segment.id, [])
                if len(values) != 1:
                    continue  # Missing or duplicated IDs must be requested again.
                try:
                    valid[segment.id] = _validated_translation(segment, values[0])
                except (RuntimeError, ValueError):
                    continue  # Never reuse malformed text or altered LaTeX tokens.
            if len(valid) != len(segments):
                raise IncompleteTranslationBatch(valid)
            return valid
    return {segment.id: _validated_translation(segment, translations[segment.id])
            for segment in segments}


def openai_translate_batch(segments: list[Segment], model: str) -> dict[str, str]:
    return _responses_translate_batch(segments, model, provider="OpenAI",
                                      api_url=OPENAI_API_URL, api_key_env="OPENAI_API_KEY")


def deepseek_translate_batch(segments: list[Segment], model: str) -> dict[str, str]:
    def request(items: list[Segment]) -> dict[str, str]:
        return _responses_translate_batch(items, model, provider="DeepSeek",
                                          api_url=DEEPSEEK_API_URL, api_key_env="DEEPSEEK_API_KEY",
                                          disable_reasoning=True)

    def request_single(segment: Segment) -> dict[str, str]:
        return _responses_translate_batch([segment], model, provider="DeepSeek",
                                          api_url=DEEPSEEK_API_URL, api_key_env="DEEPSEEK_API_KEY",
                                          disable_reasoning=True, single_text=True)

    try:
        return request(segments)
    except (ValueError, RuntimeError) as error:
        if isinstance(error, IncompleteTranslationBatch) and error.valid:
            pending = [segment for segment in segments if segment.id not in error.valid]
            if not pending:
                return error.valid
            return {**error.valid, **deepseek_translate_batch(pending, model)}
        placeholder_error = "Protected LaTeX placeholders were changed" in str(error)
        missing_ids = "Translation response omitted, duplicated, or added segment IDs" in str(error)
        malformed_output = isinstance(error, json.JSONDecodeError)
        invalid_text = ("Translation response contains an empty or invalid text item" in str(error)
                        or "Translation is unexpectedly long" in str(error))
        if not (placeholder_error or missing_ids or malformed_output or invalid_text):
            raise
        if len(segments) > 1:
            middle = len(segments) // 2
            return {**deepseek_translate_batch(segments[:middle], model),
                    **deepseek_translate_batch(segments[middle:], model)}
        if missing_ids or malformed_output or invalid_text:
            try:
                return request_single(segments[0])
            except ValueError as fallback_error:
                if "Protected LaTeX placeholders were changed" not in str(fallback_error):
                    raise
        segment = segments[0]
        markers = list(PLACEHOLDER.finditer(segment.text))
        if not markers:
            raise RuntimeError(
                f"The model did not return a valid translation for segment {segment.id}; "
                "the completed batches remain saved, so retry this task"
            ) from error
        starts = [0] + [match.end() for match in markers]
        ends = [match.start() for match in markers] + [len(segment.text)]
        parts = [segment.text[a:b] for a, b in zip(starts, ends)]
        piece_segments = []
        for index, (a, b) in enumerate(zip(starts, ends)):
            if not any(char.isascii() and char.isalpha() for char in parts[index]):
                continue
            piece_segments.append(Segment(
                f"{segment.id}-part-{index}", segment.path, segment.start + a,
                segment.start + b, parts[index],
                context_before=PLACEHOLDER.sub("", (segment.context_before +
                    segment.text[max(0, a - 180):a])[-240:]),
                context_after=PLACEHOLDER.sub("", (segment.text[b:b + 180] +
                    segment.context_after)[:240]),
            ))
        translated_parts: dict[str, str] = {}
        for batch in _batches(piece_segments):
            translated_parts.update(deepseek_translate_batch(batch, model))
        rebuilt = "".join(
            translated_parts.get(f"{segment.id}-part-{index}", part) +
            (markers[index].group() if index < len(markers) else "")
            for index, part in enumerate(parts)
        )
        render_translation(segment, rebuilt)
        return {segment.id: rebuilt}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _batches(segments: list[Segment]):
    batch: list[Segment] = []
    size = 0
    for segment in segments:
        if batch and (len(batch) >= 8 or size + len(segment.text) > 4000):
            yield batch
            batch, size = [], 0
        batch.append(segment)
        size += len(segment.text)
    if batch:
        yield batch


def _materialize(source_dir: Path, paper_dir: Path, segments: list[Segment],
                 translations: dict[str, str], *, refresh: bool = False) -> Path:
    paper_dir.mkdir(parents=True, exist_ok=True)
    target = paper_dir / "translated"
    if target.is_symlink():
        raise ValueError("Translated output path is a symlink")
    if target.exists() and not refresh:
        return target
    temporary = Path(tempfile.mkdtemp(prefix="translated-", dir=paper_dir))
    paper_root = paper_dir.resolve()
    if temporary.resolve().parent != paper_root or target.resolve().parent != paper_root:
        raise ValueError("Temporary translation output escaped the project directory")
    try:
        for source in source_files(source_dir):
            destination = temporary / source.relative_to(source_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        by_path: dict[str, list[Segment]] = {}
        for segment in segments:
            by_path.setdefault(segment.path, []).append(segment)
        for relative, file_segments in by_path.items():
            path = temporary / relative
            text = path.read_text(encoding="utf-8")
            path.write_text(apply_translations(text, file_segments, translations), encoding="utf-8")
        backup = None
        if target.exists():
            backup = paper_dir / f"translated-backup-{uuid.uuid4().hex[:8]}"
            target.rename(backup)
        try:
            temporary.rename(target)
        except OSError:
            if backup is not None:
                backup.rename(target)
            raise
    finally:
        if temporary.exists():
            if temporary.resolve().parent != paper_root:
                raise ValueError("Refusing to delete a temporary directory outside the paper output")
            shutil.rmtree(temporary)
    return target


def run_translation(root: Path, paper_id: str, model: str, max_new_segments: int | None = 20,
                    *, allow_unverified: bool = False,
                    translate_batch: Callable[[list[Segment], str], dict[str, str]] | None = None) -> dict:
    if translate_batch is None:
        translate_batch = deepseek_translate_batch if model.startswith("deepseek-") else openai_translate_batch
    entry, source_dir, segments = paper_plan(root, paper_id)
    baseline_path = root / "results" / "stage1" / "local" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    baseline_status = baseline.get("papers", {}).get(paper_id, {}).get("status")
    if baseline_status != "compiled" and not allow_unverified:
        raise ValueError(f"Stage 1 has not passed for {paper_id} ({baseline_status or 'not_run'}); "
                         "use --allow-unverified only after reviewing the source")
    paper_dir = root / "results" / "stage2" / paper_id
    state_path = paper_dir / "translations.json"
    report_path = paper_dir / "report.json"
    state = (json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else
             {"schema_version": STATE_VERSION, "source_sha256": entry["source_sha256"],
              "extractor_version": EXTRACTOR_VERSION, "model": model,
              "coverage_version": EXTRACTOR_VERSION,
              "segment_count": len(segments), "translations": {}})
    previous_report = (json.loads(report_path.read_text(encoding="utf-8"))
                       if report_path.is_file() else {})
    extractor_version = state.get("extractor_version", 1)
    migrated = False
    if (state.get("segment_count") != len(segments) and extractor_version < 5 and
            state.get("coverage_version", extractor_version) < 5):
        legacy_count = len(_segments_for_version(source_dir, entry["main_tex"], extractor_version))
        if state.get("segment_count") == legacy_count:
            state["segment_count"] = len(segments)
            state["coverage_version"] = 5
            migrated = True
    if (state.get("schema_version") != STATE_VERSION or
            state.get("source_sha256") != entry["source_sha256"] or
            state.get("model") != model or state.get("segment_count") != len(segments)):
        raise ValueError("Existing Stage 2 checkpoint uses another source or model; preserve it and use a new output directory")
    translations = state["translations"]
    unknown = set(translations) - {segment.id for segment in segments}
    if unknown:
        raise ValueError("Existing Stage 2 checkpoint contains unknown segment IDs")
    pending = [segment for segment in segments if segment.id not in translations]
    selected = pending if max_new_segments is None else pending[:max_new_segments]
    # Fail before writing a checkpoint when a paid run lacks credentials.
    if selected:
        required_key = ("DEEPSEEK_API_KEY" if translate_batch is deepseek_translate_batch else
                        "OPENAI_API_KEY" if translate_batch is openai_translate_batch else None)
        if required_key and not os.environ.get(required_key):
            raise ValueError(f"{required_key} is not set; no translation request was sent")
    if not state_path.is_file() or migrated:
        _write_json(state_path, state)
    if migrated and previous_report.get("complete"):
        _write_json(report_path, {**previous_report, "complete": False,
                                  "segments_total": len(segments),
                                  "segments_translated": len(translations),
                                  "note": "Additional prose found; translation needs to resume."})
    for batch in _batches(selected):
        result = translate_batch(batch, model)
        if set(result) != {segment.id for segment in batch}:
            raise ValueError("Translator returned the wrong segment IDs")
        if any(not isinstance(value, str) or not value.strip() for value in result.values()):
            raise ValueError("Translator returned an empty translation")
        for segment in batch:
            if len(result[segment.id]) > max(500, len(segment.text) * 3):
                raise ValueError(f"Translation is unexpectedly long: {segment.id}")
            render_translation(segment, result[segment.id])
        translations.update(result)
        _write_json(state_path, state)
    complete = len(translations) == len(segments)
    refresh = bool(previous_report and not previous_report.get("complete")) or migrated
    target = (_materialize(source_dir, paper_dir, segments, translations, refresh=refresh)
              if complete else None)
    report = {
        "paper_id": paper_id, "model": model, "source_sha256": entry["source_sha256"],
        "baseline_status": baseline_status, "segments_total": len(segments),
        "segments_translated": len(translations), "complete": complete,
        "coverage_version": EXTRACTOR_VERSION,
        "translated_source": target.relative_to(root).as_posix() if target else None,
        "segments_by_file": dict(Counter(segment.path for segment in segments)),
        "note": "Conservative prose draft; commands, math, tables, bibliography and figures need human review before PDF compilation.",
    }
    _write_json(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "translate"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--id", required=True, help="Exact arXiv paper ID including version")
    parser.add_argument("--model", help="Model ID; deepseek-* uses DeepSeek, others use OpenAI")
    parser.add_argument("--max-new-segments", type=int, default=20,
                        help="Maximum new paid translation segments this run (default: 20)")
    parser.add_argument("--all", action="store_true", help="Translate all remaining segments")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="Allow a paper that has not passed the Stage 1 baseline")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        entry, _, segments = paper_plan(root, args.id)
        if args.command == "plan":
            print(json.dumps({"paper_id": args.id, "main_tex": entry["main_tex"],
                              "segments_total": len(segments),
                              "segments_by_file": dict(Counter(segment.path for segment in segments)),
                              "preview": [segment.text[:120] for segment in segments[:5]]},
                             ensure_ascii=False, indent=2))
            return 0
        if not args.model:
            parser.error("--model is required for translate")
        if args.max_new_segments <= 0:
            parser.error("--max-new-segments must be positive")
        report = run_translation(root, args.id, args.model,
                                 None if args.all else args.max_new_segments,
                                 allow_unverified=args.allow_unverified)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"Stage 2 failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
