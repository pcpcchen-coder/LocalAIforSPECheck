import json
from pathlib import Path
from unittest.mock import patch

import pytest

from spec_check import extraction_benchmark as benchmark
from spec_check import analysis_engine as ae, engine

CASES = Path(__file__).resolve().parents[1] / "examples/local-extraction/cases.json"


def test_checked_in_cases_cover_distinct_atoms_and_load():
    cases, digest = benchmark.load_cases(CASES)
    assert len(cases) == 16
    assert sum(len(c["expected"]) for c in cases) == 22
    assert len(digest) == 64


def test_one_prediction_cannot_satisfy_two_expected_atoms():
    target = {"kind": "requirement", "value": "48"}
    score = benchmark.score_case([target, target], [target])
    assert score["matched_atoms"] == 1
    assert len(score["missing_expected_indices"]) == 1


def test_matching_reassigns_flexible_target_to_preserve_strict_target():
    expected = [{"kind": "requirement", "value": ["24", "48"]}, {"kind": "requirement", "value": "24"}]
    assert benchmark.score_case(expected, [{"kind": "requirement", "value": "24"},
                                         {"kind": "requirement", "value": "48"}])["matched_atoms"] == 2


def test_all_unresolved_does_not_pass_resolved_recall():
    cases = [{"id": "one", "role": "standard", "text": "電壓48 V。",
              "expected": [{"kind": "requirement", "value": "48"}]}]
    with patch.object(ae, "extract_block", return_value={"items": [{"kind": "unresolved", "value": "48"}]}):
        report = benchmark.run(cases, {**engine.DEFAULT_SETTINGS, "model": "fake"}, repeats=3)
    assert report["resolved_fixture_recall"] == 0
    assert report["counts"]["expected_resolved_atoms"] == 3
    assert report["counts"]["unresolved_predictions"] == 3


def test_cli_saves_metadata_and_never_overwrites_results(tmp_path):
    output = tmp_path / "result.json"
    with patch.object(ae, "extract_block", return_value={"items": [], "warnings": [], "coverage": "needs_review"}):
        benchmark.main(["--model", "fake", "--label", "test-only", "--repeat", "1", "--output", str(output)])
    result = json.loads(output.read_text())
    assert result["extraction_prompt_sha256"] == ae.EXTRACTION_PROMPT_SHA256
    assert result["counts"]["matched_atoms"] == 0
    assert "api_key" not in result["settings"]
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        benchmark.main(["--model", "fake", "--label", "test", "--output", str(output)])
    assert before == output.read_bytes()


def test_cli_rejects_remote_model_before_sending_any_data(tmp_path):
    with patch.object(ae, "extract_block") as extract, pytest.raises(SystemExit):
        benchmark.main(["--base-url", "https://example.com/v1", "--model", "fake", "--label", "test",
                        "--output", str(tmp_path / "result.json")])
    extract.assert_not_called()


def test_duplicate_case_ids_rejected(tmp_path):
    cases, _ = benchmark.load_cases(CASES)
    file = tmp_path / "bad.json"
    file.write_text(json.dumps({"cases": [cases[0], cases[0]]}))
    with pytest.raises(ValueError):
        benchmark.load_cases(file)
