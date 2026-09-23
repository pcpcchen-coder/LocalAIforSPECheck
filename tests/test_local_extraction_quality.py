"""Fault-injection regression tests; these do not measure any real model."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from spec_check import analysis_engine as ae, engine

SETTINGS = {**engine.DEFAULT_SETTINGS, "model": "synthetic"}


def item(quote, **changes):
    return {**{k: "" for k in ae._ITEM_FIELDS}, "name": "輸出電壓", "parameter": "輸出電壓",
            "value": "48", "unit": "V", "quote": quote, "kind": "requirement",
            "criticality": "medium", "criticality_basis": "輸出性能", **changes}


def extract(text, items, **settings):
    response = {"choices": [{"message": {"content": json.dumps({"items": items})}, "finish_reason": "stop"}]}
    with patch.object(engine, "_request_json", return_value=response):
        return ae.extract_block({"id": "B", "text": text}, "standard", {**SETTINGS, **settings})


def test_whole_quote_does_not_hide_missing_second_numeric_requirement():
    text = "輸出電壓48 V；輸出電流10 A。"
    result = extract(text, [item(text)])
    assert result["coverage"] == "needs_review"
    assert any(i["kind"] == "unresolved" and i["quote"] == text and "10" in i["criticality_basis"] for i in result["items"])


@pytest.mark.parametrize("field,value", [("value", "480"), ("unit", "kV"), ("operator", "≥"),
    ("conditions", "在40°C下"), ("exceptions", "戶外除外"), ("test_method", "依IEC 9999")])
def test_hallucinated_fields_are_cleared_and_item_not_accepted(field, value):
    text = "輸出電壓大於48 V。"
    result = extract(text, [item(text, operator="大於", **({field: value} if field != "operator" else {}))] if field != "operator"
                     else [item(text, operator=value)])
    assert result["items"][0]["kind"] == "unresolved"
    assert result["items"][0][field] == ""
    assert result["items"][0]["quote"] == text


@pytest.mark.parametrize("text", ["在25°C下，輸出電壓48 V。", "若為戶外型，輸出電壓48 V。",
    "輸出電壓48 V，但維修模式除外。", "Unless servicing, output voltage is 48 V."])
def test_omitted_conditions_or_exceptions_require_review(text):
    assert extract(text, [item(text)])["items"][0]["kind"] == "unresolved"


def test_literal_condition_exception_and_operator_pass():
    text = "在25°C下，輸出電壓大於48 V，但維修模式除外。"
    result = extract(text, [item(text, operator="大於", conditions="在25°C下", exceptions="但維修模式除外")])
    assert result["coverage"] == "complete"
    assert result["items"][0]["kind"] == "requirement"


def test_same_number_wrong_parameter_is_explicit_limit_not_false_claim_of_guard():
    text = "輸出電壓48 V；輸出電流48 A。"
    # A number-set guard cannot discover that the second 48 has a different meaning.
    result = extract(text, [item(text)])
    assert "非語意完整性保證" in result["warnings"][-1]


def test_no_numeric_function_cannot_be_discarded_as_context():
    text = "介面須支援通訊。"
    result = extract(text, [item(text, kind="context", value="", unit="")])
    assert result["items"][0]["kind"] == "unresolved"


def test_extra_field_rejected_even_without_structured_output():
    text = "輸出電壓48 V。"
    result = extract(text, [item(text, fabricated=True)], structured_output=False)
    assert result["items"][0]["kind"] == "unresolved"
    assert result["items"][0]["quote"] == text


def test_prompt_is_runtime_file_and_contains_explicit_output_contract():
    path = Path(ae.__file__).parent / "prompts/LOCAL_ATOMIC_EXTRACTION_PROMPT.md"
    assert ae._EXTRACT_PROMPT == path.read_text(encoding="utf-8")
    assert all(field in ae._EXTRACT_PROMPT for field in ae._ITEM_FIELDS)
