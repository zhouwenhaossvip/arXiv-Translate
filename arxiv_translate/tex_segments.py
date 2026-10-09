"""Conservative extraction of human-readable prose from arXiv TeX files.

Only plain-text spans are replaced. Commands, comments, math, and code-like
environments remain byte-for-byte unchanged for the next compilation stage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .stage1 import active_tex


INPUT_COMMAND = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
COMMAND = re.compile(r"\\([A-Za-z@]+\*?|.)", re.S)
PROTECTED_ENVS = {
    "equation", "equation*", "align", "align*", "gather", "gather*", "multline",
    "multline*", "displaymath", "math", "tikzpicture", "tabular", "tabular*",
    "array", "verbatim", "verbatim*", "lstlisting", "minted", "thebibliography",
    "algorithm", "algorithmic", "forest", "asy", "pspicture",
}
PROTECTED_COMMANDS = {
    "label", "ref", "eqref", "pageref", "autoref", "cref", "Cref", "nameref",
    "url", "href", "path", "doi", "includegraphics", "input", "include", "subfile",
    "bibliography", "bibliographystyle", "footnotemark", "index", "gls", "Gls",
    "ac", "acs", "acl", "citeauthor", "citeyear", "hyperref", "hypertarget",
    "newcommand", "renewcommand", "providecommand", "DeclareMathOperator",
    "setlength", "addtolength", "newcounter", "setcounter", "addtocounter",
}
TEXT_COMMANDS = {
    "title", "subtitle", "section", "subsection", "subsubsection", "paragraph",
    "subparagraph", "chapter", "part", "caption", "footnote", "textbf", "textit",
    "textsc", "emph", "underline", "mbox", "textrm", "textsf", "texttt",
}
ENHANCED_TEXT_COMMANDS = TEXT_COMMANDS | {"abstract", "keywords", "uppercase"}
SPECIAL = set("\\$%{}&#_^~[]")
MAX_SEGMENT_CHARS = 1200


@dataclass(frozen=True)
class Segment:
    id: str
    path: str
    start: int
    end: int
    text: str
    original: str = ""
    protected: tuple[str, ...] = ()
    context_before: str = ""
    context_after: str = ""


PLACEHOLDER = re.compile(r"\[\[LATEX_(\d+)\]\]")
GROUP_BOUNDARY = re.compile(r"\\(?:section|subsection|subsubsection|chapter|part|paragraph|item|caption|begin|end|par)\b")
CONTROL_WORD_END = re.compile(r"\\[A-Za-z@]+\*?$")
RAW_REFERENCES = re.compile(
    r"\\(?:section|chapter)\*?\s*\{\s*(?:references|bibliography|works cited)\s*\}", re.I
)


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    pos = start
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] == opening:
            depth += 1
        elif text[pos] == closing:
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return len(text)


def _skip_arguments(text: str, pos: int) -> int:
    while True:
        start = pos
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos < len(text) and text[pos] in "[{":
            opening = text[pos]
            pos = _balanced_end(text, pos, opening, "]" if opening == "[" else "}")
        else:
            return start


def _skip_environment(text: str, pos: int, name: str) -> int:
    marker = re.compile(r"\\(begin|end)\s*\{" + re.escape(name) + r"\}")
    depth = 1
    for match in marker.finditer(text, pos):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            return match.end()
    return len(text)


def _plain_spans(text: str, start: int, end: int, text_commands: set[str],
                 *, starred_text: bool = False):
    pos = start
    while pos < end:
        char = text[pos]
        if char == "%":
            newline = text.find("\n", pos, end)
            pos = end if newline < 0 else newline + 1
            continue
        if char == "$":
            delimiter = "$$" if text.startswith("$$", pos) else "$"
            close = text.find(delimiter, pos + len(delimiter), end)
            pos = end if close < 0 else close + len(delimiter)
            continue
        if char == "\\":
            match = COMMAND.match(text, pos)
            if not match:
                pos += 1
                continue
            name = match.group(1)
            base_name = name.rstrip("*") if starred_text else name
            pos = match.end()
            if name in ("(", "["):
                close = text.find(r"\)" if name == "(" else r"\]", pos, end)
                pos = end if close < 0 else close + 2
            elif name == "verb" or name == "verb*":
                if pos < end:
                    delimiter = text[pos]
                    close = text.find(delimiter, pos + 1, end)
                    pos = end if close < 0 else close + 1
            elif name in ("begin", "end"):
                while pos < end and text[pos].isspace():
                    pos += 1
                if pos < end and text[pos] == "{":
                    arg_end = _balanced_end(text, pos, "{", "}")
                    env = text[pos + 1:arg_end - 1]
                    pos = arg_end
                    if name == "begin" and env in PROTECTED_ENVS:
                        pos = _skip_environment(text, pos, env)
                    elif name == "begin":
                        pos = _skip_arguments(text, pos)
            elif name in ("def", "gdef", "xdef", "edef"):
                newline = text.find("\n", pos, end)
                pos = end if newline < 0 else newline + 1
            elif name.startswith("cite") or base_name in PROTECTED_COMMANDS or base_name not in text_commands:
                pos = _skip_arguments(text, pos)
            continue
        if char in SPECIAL:
            pos += 1
            continue
        run_start = pos
        while pos < end and text[pos] not in SPECIAL:
            pos += 1
        yield run_start, pos


def _mask_comments(text: str) -> str:
    chars = list(text)
    pos = 0
    while pos < len(chars):
        if chars[pos] == "%":
            backslashes = 0
            previous = pos - 1
            while previous >= 0 and chars[previous] == "\\":
                backslashes += 1
                previous -= 1
            if backslashes % 2 == 0:
                while pos < len(chars) and chars[pos] != "\n":
                    chars[pos] = " "
                    pos += 1
                continue
        pos += 1
    return "".join(chars)


def _split_run(text: str, start: int, end: int):
    pos = start
    while pos < end:
        blank = re.search(r"\n\s*\n", text[pos:end])
        chunk_end = pos + blank.start() if blank else end
        cursor = pos
        while cursor < chunk_end:
            limit = min(cursor + MAX_SEGMENT_CHARS, chunk_end)
            if limit < chunk_end:
                split = max(text.rfind(" ", cursor + MAX_SEGMENT_CHARS // 2, limit),
                            text.rfind("\n", cursor + MAX_SEGMENT_CHARS // 2, limit))
                if split > cursor:
                    limit = split + 1
            piece = text[cursor:limit]
            leading = len(piece) - len(piece.lstrip())
            trailing = len(piece) - len(piece.rstrip())
            a, b = cursor + leading, limit - trailing
            if b > a and sum(ch.isalpha() and ch.isascii() for ch in text[a:b]) >= 3:
                yield a, b
            cursor = limit
        pos = pos + blank.end() if blank else end


def extract_segments(text: str, relative_path: str, *, main_file: bool = False,
                     enhanced: bool = True, modern: bool = True,
                     preamble_metadata: bool = True) -> list[Segment]:
    ranges: list[tuple[int, int]] = []
    if main_file:
        masked = _mask_comments(text)
        begin = re.search(r"\\begin\s*\{document\}", masked)
        if not begin:
            return []
        start = begin.end()
        finish = re.search(r"\\end\s*\{document\}", masked[start:])
        end = start + finish.start() if finish else len(text)
        if modern:
            raw_references = RAW_REFERENCES.search(masked, start, end)
            if raw_references:
                end = raw_references.end()
        metadata = "title|subtitle|abstract|keywords" if enhanced else "title|subtitle"
        metadata_pattern = (r"\\(?:" + metadata +
                            (r")\s*(?:\[([^]\n]*)\])?\s*\{" if preamble_metadata else r")\s*\{"))
        for title in re.finditer(metadata_pattern, masked[:begin.start()]):
            if preamble_metadata and title.group(1):
                ranges.append((title.start(1), title.end(1)))
            opening = title.end() - 1
            closing = _balanced_end(masked, opening, "{", "}")
            ranges.append((opening + 1, closing - 1))
        if preamble_metadata:
            for abstract in re.finditer(r"\\begin\s*\{abstract\}", masked[:begin.start()]):
                closing = re.search(r"\\end\s*\{abstract\}", masked[abstract.end():begin.start()])
                if closing:
                    ranges.append((abstract.end(), abstract.end() + closing.start()))
    else:
        start, end = 0, len(text)
    ranges.append((start, end))
    segments: list[Segment] = []
    for start, end in ranges:
        spans: list[tuple[int, int]] = []
        commands = ENHANCED_TEXT_COMMANDS if enhanced else TEXT_COMMANDS
        for run_start, run_end in _plain_spans(text, start, end, commands,
                                               starred_text=modern):
            spans.extend(_split_run(text, run_start, run_end))
        groups: list[list[tuple[int, int]]] = []
        for a, b in spans:
            gap = text[groups[-1][-1][1]:a] if groups else ""
            if (groups and len(gap) <= 200 and "\n" not in gap and
                    not GROUP_BOUNDARY.search(gap) and b - groups[-1][0][0] <= MAX_SEGMENT_CHARS):
                groups[-1].append((a, b))
            else:
                groups.append([(a, b)])
        for group in groups:
            a, b = group[0][0], group[-1][1]
            original = text[a:b]
            protected: list[str] = []
            masked = text[group[0][0]:group[0][1]]
            for previous, following in zip(group, group[1:]):
                protected.append(text[previous[1]:following[0]])
                masked += f"[[LATEX_{len(protected) - 1}]]" + text[following[0]:following[1]]
            digest = hashlib.sha256(f"{relative_path}\0{a}\0{b}\0{original}".encode("utf-8")).hexdigest()[:16]
            segments.append(Segment(digest, relative_path, a, b, masked, original,
                                    tuple(protected), text[max(start, a - 120):a],
                                    text[b:min(end, b + 120)]))
    return segments


def reachable_tex(source_dir: Path, main_tex: str) -> list[str]:
    """Follow literal input/include commands; exclude code-like figure/table files."""
    root = source_dir.resolve()
    pending = [Path(main_tex)]
    found: set[str] = set()
    while pending:
        relative = pending.pop()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file() or path.suffix.lower() != ".tex":
            continue
        normalized = path.relative_to(root).as_posix()
        if normalized in found:
            continue
        found.add(normalized)
        text = active_tex(path.read_text(encoding="utf-8"))
        if normalized == Path(main_tex).as_posix():
            begin = re.search(r"\\begin\s*\{document\}", text)
            if begin:
                text = text[begin.end():]
                finish = re.search(r"\\end\s*\{document\}", text)
                if finish:
                    text = text[:finish.start()]
        for match in INPUT_COMMAND.finditer(text):
            child = Path(match.group(1))
            if not child.suffix:
                child = child.with_suffix(".tex")
            pending.append(relative.parent / child)
    return sorted(found)


def translatable_file(relative_path: str) -> bool:
    return not any(part.lower() in {"figures", "figure", "tables", "table", "algorithms"}
                   for part in Path(relative_path).parts)


def escape_latex_text(value: str) -> str:
    """Keep model output plain text so it cannot inject TeX syntax."""
    escapes = {
        "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%",
        "$": r"\$", "&": r"\&", "#": r"\#", "_": r"\_",
        "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
    }
    return "".join(escapes.get(char, char) for char in value.strip().replace("\r", " ").replace("\n", " "))


def render_translation(segment: Segment, translated: str) -> str:
    translated = translated.strip().replace("\r", " ").replace("\n", " ")
    found = [int(match.group(1)) for match in PLACEHOLDER.finditer(translated)]
    if found != list(range(len(segment.protected))):
        raise ValueError(f"Protected LaTeX placeholders were changed: {segment.path}:{segment.start}")
    pieces = PLACEHOLDER.split(translated)
    restored = escape_latex_text(pieces[0])
    for index in range(len(segment.protected)):
        protected = segment.protected[index]
        following = escape_latex_text(pieces[2 * index + 2])
        if following and "\u3400" <= following[0] <= "\u9fff" and CONTROL_WORD_END.search(protected):
            protected += "{}"  # Prevent \aid中文 from becoming one control sequence.
        restored += protected + following
    return restored


def apply_translations(text: str, segments: list[Segment], translations: dict[str, str]) -> str:
    for segment in sorted(segments, key=lambda item: item.start, reverse=True):
        if segment.id not in translations:
            raise ValueError(f"Missing translation: {segment.id}")
        if text[segment.start:segment.end] != segment.original:
            raise ValueError(f"Source changed while translating: {segment.path}:{segment.start}")
        text = text[:segment.start] + render_translation(segment, translations[segment.id]) + text[segment.end:]
    return text
