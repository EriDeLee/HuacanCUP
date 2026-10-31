from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from eda_agent.config import REPO_ROOT


@pytest.fixture(scope="session")
def trace_validator() -> Draft202012Validator:
    schema: dict[str, Any] = json.loads(
        (REPO_ROOT / "schemas" / "trace.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@pytest.fixture()
def led_design_path() -> Path:
    return REPO_ROOT / "examples" / "led" / "design.json"
