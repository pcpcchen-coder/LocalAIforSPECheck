import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluate_gold import NO_PREDICTION, evaluate, load_json, main


def prediction(block_id, status, **extra):
    return {"standard_name": "規範.txt", "block_id": block_id, "status": status, **extra}


def answer(block_id, status, critical=False):
    return prediction(block_id, status, critical=critical)


def run_export(results, **extra):
    return {
        "id": "run-eval", "mode": "local", "status": "completed", "results": results,
        "documents": [{"role": "product", "blocks": [{"id": "P1", "text": "額定電壓：48 V\n通訊 CAN"}]}],
        **extra,
    }


def test_original_ai_status_is_used_and_critical_false_match_is_not_hidden_by_review():
    run = run_export([
        prediction("B1", "match", review={"decision": "changed", "final_status": "mismatch"}),
        prediction("B2", "partial"), prediction("B3", "uncertain"), prediction("B4", "match"),
    ])
    gold = {"rows": [answer("B1", "mismatch", True), answer("B2", "partial"), answer("B3", "missing"), answer("B4", "match")]}
    original = copy.deepcopy(run)
    report = evaluate(run, gold)
    assert run == original
    assert report["metrics"]["accuracy"] == 0.5
    assert report["metrics"]["difference_recall"] == pytest.approx(1 / 3)
    assert report["metrics"]["review_detection_rate"] == pytest.approx(2 / 3)
    assert report["counts"]["false_match_count"] == 1
    assert report["counts"]["critical_false_match_count"] == 1
    assert report["confusion_matrix"]["mismatch"]["match"] == 1
    assert report["rows"][0]["ai_status"] == "match"


def test_missing_predictions_stay_in_denominator_and_unlabeled_extras_are_visible():
    report = evaluate(run_export([prediction("B1", "mismatch"), prediction("extra", "match")]),
                      {"rows": [answer("B1", "mismatch"), answer("B2", "missing")]})
    assert report["metrics"]["accuracy"] == 0.5
    assert report["metrics"]["difference_recall"] == 0.5
    assert report["metrics"]["prediction_coverage"] == 0.5
    assert report["counts"]["missing_predictions"] == 1
    assert report["counts"]["unscored_predictions"] == 1
    assert report["confusion_matrix"]["missing"][NO_PREDICTION] == 1
    assert report["rows"][1]["ai_status"] is None


@pytest.mark.parametrize("gold_rows", [[], [answer("not-found", "missing")]])
def test_empty_or_unmatched_gold_never_claims_perfect_accuracy(gold_rows):
    report = evaluate(run_export([prediction("B1", "match")]), {"rows": gold_rows})
    assert report["metrics"]["accuracy"] is None
    assert report["metrics"]["difference_recall"] is None
    assert report["metrics"]["review_detection_rate"] is None
    assert report["metrics"]["prediction_coverage"] == (0 if gold_rows else None)
    assert report["citations"]["quote_validity_rate"] is None


def test_no_positive_denominator_is_null_not_one_and_uncertain_is_not_difference():
    report = evaluate(run_export([prediction("B1", "uncertain")]), {"rows": [answer("B1", "uncertain")]})
    assert report["metrics"]["accuracy"] == 1
    assert report["metrics"]["difference_recall"] is None
    assert report["metrics"]["review_detection_rate"] == 1
    assert report["metrics"]["difference_review_capture_rate"] is None


@pytest.mark.parametrize("source", ["gold", "run"])
def test_duplicate_comparison_keys_are_rejected(source):
    rows = [prediction("B1", "match")]
    answers = [answer("B1", "match")]
    if source == "gold":
        answers.append(answer("B1", "missing"))
    else:
        rows.append(prediction("B1", "missing"))
    with pytest.raises(ValueError, match="重複比對鍵"):
        evaluate(run_export(rows), {"rows": answers})


def test_citations_check_actual_source_quote_not_review_or_standard_text():
    report = evaluate(run_export([
        prediction("B1", "match", evidence=[{"block_id": "P1", "quote": "48  V\n通訊"}]),
        prediction("B2", "mismatch", evidence=[{"block_id": "P1", "quote": "電壓：400 V"}]),
        prediction("B3", "partial", evidence=[{"block_id": "NOT-PRODUCT", "quote": "48 V"}]),
        prediction("B4", "match", evidence=[{"block_id": "P1", "quote": "  "}]),
        prediction("B5", "missing", evidence=[]),
    ]), {"rows": [answer("B1", "match")]})
    citations = report["citations"]
    assert citations["scope"] == "all_exported_results"
    assert citations["total_quotes"] == 4
    assert citations["valid_quotes"] == 1
    assert citations["quote_validity_rate"] == 0.25
    assert citations["rows_without_valid_required_evidence"] == 3


def test_missing_product_snapshot_makes_quotes_invalid():
    report = evaluate(run_export([prediction("B1", "match", evidence=[{"block_id": "P1", "quote": "48 V"}])], documents=[]),
                      {"rows": [answer("B1", "match")]})
    assert report["citations"]["quote_validity_rate"] == 0
    assert any("產品文字快照" in warning for warning in report["warnings"])


def test_duplicate_product_blocks_reject_ambiguous_citations():
    run = run_export([])
    run["documents"][0]["blocks"].append({"id": "P1", "text": "400 V"})
    with pytest.raises(ValueError, match="重複 block_id"):
        evaluate(run, {"rows": []})


def test_demo_and_incomplete_run_warn_and_settings_secrets_are_not_copied():
    report = evaluate(run_export([prediction("B1", "match")], mode="demo", status="cancelled",
                                 settings={"model": "local-x", "api_key": "DO-NOT-EXPORT"}),
                      {"rows": [answer("B1", "match")]})
    assert any("不可作為模型品質" in warning for warning in report["warnings"])
    assert any("尚未完整完成" in warning for warning in report["warnings"])
    assert "DO-NOT-EXPORT" not in json.dumps(report)
    assert report["model"] == "local-x"


@pytest.mark.parametrize("row", [answer("B1", "invalid"), {**answer("B1", "match"), "critical": "false"}])
def test_invalid_gold_does_not_silently_change_denominator(row):
    with pytest.raises(ValueError):
        evaluate(run_export([prediction("B1", "match")]), {"rows": [row]})


def test_json_duplicate_members_reject_instead_of_taking_last_value(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"rows": [], "rows": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="重複欄位"):
        load_json(path)


def test_cli_writes_report_and_prevents_overwriting_inputs(tmp_path, capsys):
    run = tmp_path / "run.json"
    gold = tmp_path / "gold.json"
    run.write_text(json.dumps(run_export([prediction("B1", "match")])), encoding="utf-8")
    gold.write_text(json.dumps({"rows": [answer("B1", "match")]}), encoding="utf-8")
    output = tmp_path / "reports" / "evaluation.json"
    assert main([str(run), str(gold), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["accuracy"] == 1
    original = run.read_bytes()
    assert main([str(run), str(gold), "--output", str(run)]) == 2
    assert run.read_bytes() == original
    assert "不可覆寫" in capsys.readouterr().err


def test_cli_is_runnable_directly_without_project_imports(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_gold.py"
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, capture_output=True, encoding="utf-8")
    assert result.returncode == 0
    assert "--output" in result.stdout


@pytest.mark.parametrize("stdio_encoding", ["ascii", "cp1252"])
def test_cli_preserves_utf8_help_json_and_errors_with_legacy_stdio(tmp_path, stdio_encoding):
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_gold.py"
    environment = {**os.environ, "PYTHONIOENCODING": stdio_encoding}

    def invoke(*arguments):
        return subprocess.run(
            [sys.executable, str(script), *map(str, arguments)], cwd=tmp_path,
            env=environment, capture_output=True, encoding="utf-8",
        )

    help_result = invoke("--help")
    assert help_result.returncode == 0
    assert "人工標準答案" in help_result.stdout
    run = tmp_path / "run.json"
    gold = tmp_path / "gold.json"
    run.write_text(json.dumps(run_export([prediction("B1", "match")], mode="demo"), ensure_ascii=False), encoding="utf-8")
    gold.write_text(json.dumps({"rows": [answer("B1", "match")]}, ensure_ascii=False), encoding="utf-8")
    report_result = invoke(run, gold)
    assert report_result.returncode == 0
    assert "規範.txt" in report_result.stdout
    report = json.loads(report_result.stdout)
    assert report["metrics"]["accuracy"] == 1
    assert any("示範模式" in warning for warning in report["warnings"])
    error_result = invoke(run, gold, "--output", run)
    assert error_result.returncode == 2
    assert "評估失敗" in error_result.stderr
    assert "UnicodeEncodeError" not in error_result.stderr


def test_template_keys_match_current_example_extraction():
    from spec_check.ingestion import extract_document

    root = Path(__file__).resolve().parents[1]
    gold = load_json(root / "examples" / "gold_template.json")
    document = extract_document(root / "examples" / "standard_a_48v.txt", "standard_a_48v.txt", "standard")
    by_id = {block["id"]: block["text"] for block in document["blocks"]}
    assert len(gold["rows"]) == 3
    assert all(row["standard_name"] == document["name"] and row["block_id"] in by_id for row in gold["rows"])
    assert "48 V" in by_id[gold["rows"][0]["block_id"]]
    assert "-30 至 70" in by_id[gold["rows"][1]["block_id"]]
    assert "IP54" in by_id[gold["rows"][2]["block_id"]]
