"""Render the slashdhan reference from live signatures and docstrings so docs cannot drift."""

from __future__ import annotations

import inspect
import sys
import textwrap
from collections.abc import Callable
from typing import Any


def _sig(fn: Callable[..., Any]) -> str:
    try:
        s = str(inspect.signature(fn))
    except (TypeError, ValueError):
        return "(...)"
    return (
        s.replace("'", "")
        .replace('"', "")
        .replace("typing.", "")
        .replace("collections.abc.", "")
        .replace("Literal", "")
        .replace(" | None", "?")
        .replace("Row", "dict")
    )


def reference(brief: bool = True) -> str:
    from . import api

    lines: list[str] = []
    for name in api.__all__:
        obj = getattr(api, name)
        if not callable(obj) or inspect.isclass(obj):
            continue
        doc = inspect.getdoc(obj) or ""
        if brief:
            first = doc.split("\n\n")[0].replace("\n", " ")
            lines.append(f"- {name}{_sig(obj)}\n    {first}")
        else:
            lines.append(f"### {name}{_sig(obj)}\n{textwrap.indent(doc, '    ')}\n")
    return "\n".join(lines)


if __name__ == "__main__":
    print(reference(brief="--full" not in sys.argv))
