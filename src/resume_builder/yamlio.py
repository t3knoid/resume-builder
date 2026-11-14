"""YAML and Markdown file access for the sync adapter (PRD R7.7 stage 1, R7.12, R10.15).

This is the only module that reads or writes user files. Reading is defensive: size limit,
UTF-8 only, no control characters, no duplicate keys, no anchors/aliases (so a hostile file
cannot blow up memory), and a nesting-depth limit. Writing keeps comments, key order and
formatting by updating the existing document in place (ruamel round-trip mode, D13).
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, NodeEvent

from .errors import Problem

MAX_FILE_BYTES = 1_000_000
MAX_DEPTH = 12
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOC_HEADER_RE = re.compile(r"\A<!--.*?-->\s*\n?", re.S)


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.allow_duplicate_keys = False
    y.preserve_quotes = True
    # Never fold long values onto continuation lines: one value, one line, easy to edit and diff.
    y.width = 100_000
    y.indent(mapping=2, sequence=4, offset=2)
    # Write `null` explicitly: easier to read and to edit than an empty value.
    y.representer.add_representer(type(None), lambda r, d: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def read_text_safely(path: Path, rel: str) -> tuple[str | None, list[Problem]]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, [Problem("File not found.", file=rel)]
    if len(raw) > MAX_FILE_BYTES:
        return None, [Problem(f"File is larger than {MAX_FILE_BYTES // 1000} KB. Split it or remove pasted attachments.", file=rel)]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [Problem(f"File is not valid UTF-8 (byte {exc.start}). Save it as UTF-8 in your editor.", file=rel)]
    text = text.lstrip("﻿")
    m = _CONTROL_RE.search(text)
    if m:
        line = text.count("\n", 0, m.start()) + 1
        return None, [Problem("Contains an invisible control character. Delete it or retype the line.", file=rel, line=line)]
    return text, []


def read_yaml(path: Path, rel: str) -> tuple[Any, list[Problem]]:
    """Return (round-trip document, problems). The document keeps line numbers for error messages."""
    text, problems = read_text_safely(path, rel)
    if text is None:
        return None, problems
    return parse_yaml_text(text, rel)


def check_text(text: str, rel: str) -> list[Problem]:
    """The same limits as files, for text pasted in (e.g. from a chat assistant)."""
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        return [Problem(f"Larger than {MAX_FILE_BYTES // 1000} KB.", file=rel)]
    m = _CONTROL_RE.search(text)
    if m:
        return [Problem("Contains an invisible control character.", file=rel, line=text.count("\n", 0, m.start()) + 1)]
    return []


def parse_yaml_text(text: str, rel: str) -> tuple[Any, list[Problem]]:
    """Parse YAML text with the safety limits in R7.7 stage 1."""
    problems = check_text(text, rel)
    if problems:
        return None, problems
    text = strip_code_fence(text)
    # Structural pre-scan with the event parser: anchors/aliases and depth.
    try:
        depth = 0
        for ev in _yaml().parse(io.StringIO(text)):
            if isinstance(ev, AliasEvent) or (isinstance(ev, NodeEvent) and getattr(ev, "anchor", None)):
                line = ev.start_mark.line + 1 if ev.start_mark else None
                return None, [Problem("YAML anchors and aliases (&name / *name) are not supported. Write the values out in full.", file=rel, line=line)]
            if isinstance(ev, CollectionStartEvent):
                depth += 1
                if depth > MAX_DEPTH:
                    line = ev.start_mark.line + 1 if ev.start_mark else None
                    return None, [Problem(f"Nested more than {MAX_DEPTH} levels deep. Check the indentation.", file=rel, line=line)]
            elif isinstance(ev, CollectionEndEvent):
                depth -= 1
    except YAMLError as exc:
        return None, [_yaml_problem(exc, rel)]
    try:
        doc = _yaml().load(text)
    except YAMLError as exc:
        return None, [_yaml_problem(exc, rel)]
    if doc is None:
        doc = CommentedMap()
    if not isinstance(doc, CommentedMap):
        return None, [Problem("The file must be a set of `key: value` lines at the top level.", file=rel, line=1)]
    return doc, []


_FENCE_RE = re.compile(r"\A\s*```[A-Za-z0-9_-]*[^\n]*\n(.*?)\n```\s*\Z", re.S)


def strip_code_fence(text: str) -> str:
    """Chat assistants often wrap output in ```yaml ... ```; accept that."""
    m = _FENCE_RE.match(text)
    return m.group(1) + "\n" if m else text


def _yaml_problem(exc: YAMLError, rel: str) -> Problem:
    line = None
    if isinstance(exc, MarkedYAMLError) and exc.problem_mark is not None:
        line = exc.problem_mark.line + 1
    text = str(getattr(exc, "problem", None) or exc).split("\n")[0]
    if "duplicate key" in str(exc):
        text = "The same key appears twice. Keep one of them."
    return Problem(f"This is not valid YAML: {text}. Check indentation (spaces, not tabs) and that every `key:` has a colon.", file=rel, line=line)


def to_plain(obj: Any) -> Any:
    """Round-trip document -> plain dicts/lists/strings (dates as ISO strings)."""
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, str):
        return str(obj)
    return obj


def line_of(doc: Any, loc: tuple) -> int | None:
    """Best-effort line number for a field path such as ('roles', 0, 'bullets', 2, 'text')."""
    node = doc
    line = None
    for key in loc:
        try:
            if isinstance(node, CommentedMap) and key in node:
                line = node.lc.key(key)[0] + 1
                node = node[key]
            elif isinstance(node, CommentedSeq) and isinstance(key, int) and key < len(node):
                line = node.lc.item(key)[0] + 1
                node = node[key]
            else:
                break
        except Exception:  # line info is a convenience; never fail on it
            break
    return line


def _to_yaml_value(value: Any) -> Any:
    if isinstance(value, str) and _DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, dict):
        cm = CommentedMap()
        for k, v in value.items():
            cm[k] = _to_yaml_value(v)
        return cm
    if isinstance(value, list):
        cs = CommentedSeq()
        for v in value:
            cs.append(_to_yaml_value(v))
        return cs
    return value


def deep_update(node: Any, new: Any) -> Any:
    """Update a round-trip node in place so comments and order survive (R10.15)."""
    if isinstance(node, CommentedMap) and isinstance(new, dict):
        for key in [k for k in node.keys() if k not in new]:
            del node[key]
        for key, value in new.items():
            if key in node and isinstance(node[key], (CommentedMap, CommentedSeq)) and isinstance(value, (dict, list)):
                node[key] = deep_update(node[key], value)
            elif key in node and to_plain(node[key]) == value:
                continue
            else:
                node[key] = _to_yaml_value(value)
        return node
    if isinstance(node, CommentedSeq) and isinstance(new, list):
        for i, value in enumerate(new):
            if i < len(node) and isinstance(node[i], (CommentedMap, CommentedSeq)) and isinstance(value, (dict, list)):
                node[i] = deep_update(node[i], value)
            elif i < len(node):
                if to_plain(node[i]) != value:
                    node[i] = _to_yaml_value(value)
            else:
                node.append(_to_yaml_value(value))
        while len(node) > len(new):
            node.pop()
        return node
    return _to_yaml_value(new)


def dump_yaml(doc: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def write_yaml(path: Path, plain: dict, template_text: str | None = None) -> str:
    """Write plain data into a YAML file, keeping the existing file's (or the template's) comments."""
    doc = None
    if path.exists():
        text, _ = read_text_safely(path, path.name)
        if text is not None:
            try:
                doc = _yaml().load(text)
            except YAMLError:
                doc = None
    if doc is None and template_text:
        doc = _yaml().load(template_text)
    if not isinstance(doc, CommentedMap):
        doc = CommentedMap()
    deep_update(doc, plain)
    out = dump_yaml(doc)
    if template_text and not path.exists():
        header = _leading_comment_block(template_text)
        if header and not out.startswith(header):
            out = header + out
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.replace(path)
    return out


def _leading_comment_block(text: str) -> str:
    lines = []
    for ln in text.splitlines(keepends=True):
        if ln.startswith("#") or not ln.strip():
            lines.append(ln)
        else:
            break
    return "".join(lines)


def read_markdown(path: Path, rel: str) -> tuple[str | None, list[Problem]]:
    text, problems = read_text_safely(path, rel)
    if text is None:
        return None, problems
    return DOC_HEADER_RE.sub("", text, count=1), []


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
