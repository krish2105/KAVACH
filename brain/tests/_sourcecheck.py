"""Read a module's *code* without its prose.

Several tests in this suite forbid a literal from appearing in a module —
an app name in `agent.py`, a model name in `voice/__main__.py`, a shell
pattern in `policy.py`. Every one of those bugs was the same shape: a fact
written down in two places, where one copy went stale and nothing noticed.

Grepping raw source cannot do it. The modules that must not *contain* a
literal are exactly the ones whose comments must *explain* why — `agent.py`
carries a paragraph about the Chrome refusal, and `policy.py` quotes
`rm -rf` as evidence that pattern matching fails. Grep sees those and fails
the build for documentation.

Parsing separates them. `ast` discards comments for free, and docstrings are
dropped explicitly, so what is left is what actually executes.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


def code_text(module) -> str:
    """Every string literal and identifier in `module`, minus its prose.

    Comments and docstrings are excluded; names, attributes and string
    constants are kept, because a hardcoded fact will be one of those.
    """
    source = Path(inspect.getfile(module)).read_text()
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            found = ast.get_docstring(node, clean=False)
            if found:
                docstrings.add(found)

    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
    return "\n".join(parts)
