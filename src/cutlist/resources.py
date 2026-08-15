"""Reaching files that ship inside the package.

Always through `importlib.resources`, never by walking up from `__file__`.

The difference is invisible in development and total in production: a relative
walk resolves correctly in an editable checkout, where the repository layout
exists around the module, and resolves to nothing once the package is installed
and the module sits in site-packages. When that broke in a previous tool, the
scaffolding command fell back to a stub and *still reported success*, and the
prompt that was the actual product shipped to nobody.
"""

from __future__ import annotations

import contextlib
from importlib import resources

from .errors import CutlistError


def _read(package: str, name: str, kind: str) -> str:
    try:
        return resources.files(package).joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        available = ""
        with contextlib.suppress(Exception):  # diagnostics only
            available = ", ".join(
                sorted(p.name for p in resources.files(package).iterdir() if p.is_file())
            )
        raise CutlistError(
            f"no {kind} named {name!r} ships with this version.",
            hint=f"Available: {available or '(none found -- the install may be incomplete)'}",
        ) from exc


def read_prompt(name: str) -> str:
    """A contract meant to be handed to a model, served from the package.

    Served rather than remembered on purpose. These name specific fields in
    `report.json`, and those fields change when the measurement changes -- a
    remembered copy tells the reader to quote a field that no longer exists, and
    nothing errors when they do.
    """
    stem = name if name.endswith(".md") else f"{name}.md"
    return _read("cutlist.prompts", stem, "prompt")


def read_reference(name: str) -> str:
    stem = name if name.endswith(".md") else f"{name}.md"
    return _read("cutlist.references", stem, "reference")


def read_template(name: str) -> str:
    return _read("cutlist.templates", name, "template")
