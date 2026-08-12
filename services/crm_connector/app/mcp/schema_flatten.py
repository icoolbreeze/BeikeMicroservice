"""Flatten pydantic-generated JSON schemas for LLM-friendly tool exposure.

``model_json_schema()`` emits ``$defs`` + ``$ref`` for nested models (e.g.
``NumericRange`` inside ``RentalListingSearchInput``).  MCP 2.x passes the
tool's ``inputSchema`` through verbatim, and many MCP clients (Hermes and
other agents) forward it to the LLM as a plain OpenAI-style tools schema,
which does not support ``$ref`` — the model then cannot see the real fields
and either omits the ``input`` argument or invents filter keys.  This module
inlines every ``$ref`` so each tool schema is self-contained and flat.
"""

from __future__ import annotations

from typing import Any


def flatten_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return ``schema`` with all ``$defs``/``$ref`` references inlined.

    Handles arbitrarily nested references (``$ref`` pointing into ``$defs``,
    including references that themselves reference other definitions).  The
    ``$defs`` section is dropped once every reference has been inlined.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any, _stack: tuple[str, ...] = ()) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = node["$ref"]
                # Only local "#/$defs/..." refs are produced by pydantic.
                if not target.startswith("#/$defs/"):
                    return dict(node)
                name = target.split("/")[-1]
                if name in _stack:
                    # Cycle (pydantic models in this codebase are acyclic);
                    # fall back to a best-effort definition to stay valid.
                    return dict(node)
                definition = defs.get(name)
                if definition is None:
                    return dict(node)
                resolved = resolve(definition, _stack + (name,))
                # Merge sibling keys that sit next to the $ref (e.g. title).
                resolved.update({k: v for k, v in node.items() if k != "$ref"})
                return resolved
            return {k: resolve(v, _stack) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item, _stack) for item in node]
        return node

    flattened = resolve(schema)
    flattened.pop("$defs", None)
    return flattened
