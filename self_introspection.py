"""Codebase AST Introspection Engine for J.A.R.V.I.S. — MARK VIII.

Provides semantic AST parsing of Python and C# codebase files, building a structural
symbol map (classes, methods, docstrings, signatures, line numbers) into data/codebase_index.json,
and retrieving grounded architectural context for system prompt injection.
"""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
INDEX_FILE = DATA_DIR / "codebase_index.json"

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__",
    "node_modules", "build", "dist", ".pytest_cache", ".idea", ".vscode"
}


def _parse_python_ast(file_path: Path) -> Dict[str, Any]:
    """Parse a python file into structured symbols using ast module."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as exc:
        return {"error": str(exc), "classes": [], "functions": [], "docstring": None}

    module_doc = ast.get_docstring(tree)
    classes = []
    functions = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node)
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_doc = ast.get_docstring(item)
                    args = [a.arg for a in item.args.args]
                    methods.append({
                        "name": item.name,
                        "line": item.lineno,
                        "args": args,
                        "docstring": m_doc,
                        "is_async": isinstance(item, ast.AsyncFunctionDef),
                    })
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "docstring": class_doc,
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f_doc = ast.get_docstring(node)
            args = [a.arg for a in node.args.args]
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args": args,
                "docstring": f_doc,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })

    return {
        "docstring": module_doc,
        "classes": classes,
        "functions": functions,
        "lines_of_code": len(content.splitlines()),
    }


def _parse_csharp_symbols(file_path: Path) -> Dict[str, Any]:
    """Parse C# files using regex for classes, methods, and line numbers."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"error": str(exc), "classes": [], "functions": [], "docstring": None}

    lines = content.splitlines()
    classes = []
    functions = []

    class_pattern = re.compile(
        r"^\s*(?:public|private|internal|protected)?\s*(?:static|sealed|abstract|partial)?\s*class\s+([A-Za-z0-9_]+)",
        re.MULTILINE
    )
    method_pattern = re.compile(
        r"^\s*(?:public|private|internal|protected)\s+(?:static|async|virtual|override|abstract)?\s*([A-Za-z0-9_<>\[\]]+)\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)",
        re.MULTILINE
    )

    for i, line in enumerate(lines, start=1):
        c_match = class_pattern.search(line)
        if c_match:
            classes.append({
                "name": c_match.group(1),
                "line": i,
                "docstring": None,
                "methods": [],
            })
            continue

        m_match = method_pattern.search(line)
        if m_match and "class " not in line:
            return_type = m_match.group(1)
            method_name = m_match.group(2)
            raw_args = m_match.group(3)
            args = [a.strip().split()[-1] for a in raw_args.split(",") if a.strip()]
            functions.append({
                "name": method_name,
                "return_type": return_type,
                "line": i,
                "args": args,
                "docstring": None,
                "is_async": "async" in line,
            })

    return {
        "docstring": None,
        "classes": classes,
        "functions": functions,
        "lines_of_code": len(lines),
    }


def index_codebase(
    root_dir: Optional[str] = None,
    output_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Walk repository, extract AST structural symbols, and save codebase index."""
    root = Path(root_dir).resolve() if root_dir else BASE_DIR
    out_path = Path(output_file).resolve() if output_file else INDEX_FILE

    out_path.parent.mkdir(parents=True, exist_ok=True)
    registry = get_registry()

    index: Dict[str, Any] = {
        "root": str(root),
        "total_files": 0,
        "files": {},
    }

    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue

            rel_path = path.relative_to(root).as_posix()

            if path.suffix == ".py":
                symbols = _parse_python_ast(path)
                index["files"][rel_path] = symbols
                index["total_files"] += 1
            elif path.suffix == ".cs":
                symbols = _parse_csharp_symbols(path)
                index["files"][rel_path] = symbols
                index["total_files"] += 1

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

        registry.set_capability_evidence(
            "SELF_INTROSPECTION",
            EvidenceLevel.LIVE,
            f"Indexed {index['total_files']} source files into {out_path.name}",
            source="self introspection",
        )
        return index

    except Exception as exc:
        error_handler.log_and_demote(
            "SELF_INTROSPECTION",
            exc,
            "Codebase AST indexing",
            SubsystemState.DEGRADED,
        )
        return index


def get_codebase_context(
    query: str,
    max_tokens: int = 2000,
    index_path: Optional[str] = None,
) -> str:
    """Retrieve precise AST-grounded references (file, line number, docstring, signature) for system prompt injection.

    Args:
        query: Search keywords or query string
        max_tokens: Approximate token budget (1 token ~ 4 chars)
        index_path: Optional path to pre-built index file
    """
    path = Path(index_path).resolve() if index_path else INDEX_FILE
    if not path.exists():
        index_data = index_codebase(output_file=str(path))
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        except Exception:
            index_data = index_codebase(output_file=str(path))

    keywords = set(re.findall(r"\w+", query.lower()))
    if not keywords:
        return "No specific codebase symbols requested."

    matches: List[Tuple[int, str]] = []
    max_chars = max_tokens * 4

    for rel_path, info in index_data.get("files", {}).items():
        score = 0
        lines = []

        path_words = set(re.findall(r"\w+", rel_path.lower()))
        path_matches = keywords.intersection(path_words)
        if path_matches:
            score += len(path_matches) * 5

        doc = info.get("docstring")
        if doc and any(kw in doc.lower() for kw in keywords):
            score += 2

        # Check classes & methods
        for c in info.get("classes", []):
            c_name = c.get("name", "")
            c_line = c.get("line", 1)
            c_doc = c.get("docstring") or ""
            if any(kw in c_name.lower() or kw in c_doc.lower() for kw in keywords):
                score += 4
                lines.append(f"  Class `{c_name}` (L{c_line}): {c_doc[:80]}")
            for m in c.get("methods", []):
                m_name = m.get("name", "")
                m_line = m.get("line", 1)
                m_args = ", ".join(m.get("args", []))
                if any(kw in m_name.lower() for kw in keywords):
                    score += 3
                    lines.append(f"    Method `{c_name}.{m_name}({m_args})` (L{m_line})")

        # Check functions
        for f in info.get("functions", []):
            f_name = f.get("name", "")
            f_line = f.get("line", 1)
            f_args = ", ".join(f.get("args", []))
            f_doc = f.get("docstring") or ""
            if any(kw in f_name.lower() or kw in f_doc.lower() for kw in keywords):
                score += 4
                lines.append(f"  Function `{f_name}({f_args})` (L{f_line}): {f_doc[:80]}")

        if score > 0 and lines:
            header = f"File `{rel_path}` (Matches: {score}):\n" + "\n".join(lines)
            matches.append((score, header))

    matches.sort(key=lambda x: x[0], reverse=True)

    result_blocks = []
    current_chars = 0

    for _, block in matches:
        if current_chars + len(block) > max_chars:
            break
        result_blocks.append(block)
        current_chars += len(block) + 2

    if not result_blocks:
        return f"No direct codebase AST symbols matched '{query}'."

    return "### Relevant Codebase AST Architecture:\n\n" + "\n\n".join(result_blocks)
