"""Every schema handed to the gateway has to survive OpenAI's strict mode.

`response_format` is validated before the model is ever invoked, so a schema the
API cannot express fails the whole call rather than degrading. For a one-call
feature like Story DNA that means the button simply never works, and the only
signal is a 400 quoting a JSON Schema rule. These checks run the same transform
the OpenAI client runs and assert the result is something the API will accept.
"""

import ast
from pathlib import Path
from typing import Any

import daastaan_contracts.models as models
import pytest
from openai.lib._pydantic import to_strict_json_schema

AGENT_SRC = Path(__file__).resolve().parents[1] / "services" / "agent" / "src" / "daastaan_agent"


def _gateway_schema_names() -> list[str]:
    """Every `schema=` argument the agent passes to the gateway.

    Discovered rather than listed so a new stage is covered the day it is
    written, which is the only time this class of bug is cheap to fix.
    """
    names: set[str] = set()
    for path in AGENT_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "schema" and isinstance(keyword.value, ast.Name):
                        names.add(keyword.value.id)
    return sorted(names)


SCHEMA_NAMES = _gateway_schema_names()


def _open_ended_objects(node: Any, path: str = "") -> list[str]:
    """Paths of `object` subschemas that declare no properties.

    A `dict[str, X]` field compiles to one of these. OpenAI drops it from
    `properties` but keeps it in `required`, then rejects the request for the
    mismatch it just created, which makes the error message point away from the
    actual offending field.
    """
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" not in node:
            found.append(path or "<root>")
        for key, value in node.items():
            found.extend(_open_ended_objects(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_open_ended_objects(value, f"{path}[{index}]"))
    return found


def test_call_sites_are_discoverable():
    """Guards the scan itself, so a refactor that hides the call sites from it
    fails here instead of quietly passing every test below."""
    assert len(SCHEMA_NAMES) >= 10


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_gateway_schema_is_strict_mode_compatible(name):
    model = getattr(models, name, None)
    assert model is not None, f"{name} is used as a gateway schema but is not a contract model"

    offenders = _open_ended_objects(to_strict_json_schema(model))
    assert not offenders, (
        f"{name} has an open-ended object at {offenders} — most likely a "
        "`dict[str, ...]` field. Structured outputs cannot express one: compute "
        "the value locally, or model it as a list of key/value objects."
    )
