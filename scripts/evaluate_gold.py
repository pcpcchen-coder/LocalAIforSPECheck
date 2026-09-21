#!/usr/bin/env python3
"""Evaluate original AI row statuses against an independently authored gold set.

Uses only the standard library. No model requests or source-file modifications.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("match", "partial", "mismatch", "missing", "uncertain")
DIFFERENCES = frozenset({"partial", "mismatch", "missing"})
REVIEW_NEEDED = DIFFERENCES | {"uncertain"}
NO_PREDICTION = "__missing_prediction__"


def _unique_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 物件含重複欄位：{key}")
        result[key] = value
    return result


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_members)


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必須是非空白字串。")
    return value


def _key(row: dict, origin: str) -> tuple[str, str]:
    if not isinstance(row, dict):
        raise ValueError(f"{origin} 的每列必須是 JSON 物件。")
    return (
        _text(row.get("standard_name"), f"{origin}.standard_name"),
        _text(row.get("block_id"), f"{origin}.block_id"),
    )


def _index(rows, origin: str, *, gold: bool = False) -> dict:
    if not isinstance(rows, list):
        raise ValueError(f"{origin} 必須是陣列。")
    indexed = {}
    for row in rows:
        key = _key(row, origin)
        if key in indexed:
            raise ValueError(f"{origin} 含重複比對鍵：{key[0]} / {key[1]}")
        if not isinstance(row.get("status"), str) or row["status"] not in STATUSES:
            raise ValueError(f"{origin} {key} 的 status 必須是 {', '.join(STATUSES)}。")
        if gold and not isinstance(row.get("critical"), bool):
            raise ValueError(f"{origin} {key} 的 critical 必須明確填 true 或 false。")
        if not gold:
            evidence = row.get("evidence", [])
            if not isinstance(evidence, list):
                raise ValueError(f"{origin} {key} 的 evidence 必須是陣列。")
        indexed[key] = row
    return indexed


def _ratio(numerator: int, denominator: int, *, meaningful: bool = True):
    return numerator / denominator if meaningful and denominator else None


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _citation_metrics(documents: list, rows: list) -> dict:
    if any(not isinstance(document, dict) for document in documents):
        raise ValueError("documents 的每個項目必須是 JSON 物件。")
    products = [document for document in documents if document.get("role") == "product"]
    if len(products) > 1:
        raise ValueError("匯出資料含多份產品文件，無法僅依 block_id 唯一驗證引用。")
    blocks = {}
    for product in products:
        source_blocks = product.get("blocks", [])
        if not isinstance(source_blocks, list):
            raise ValueError("產品文件 blocks 必須是陣列。")
        for block in source_blocks:
            if not isinstance(block, dict):
                raise ValueError("產品文件 block 必須是 JSON 物件。")
            block_id = _text(block.get("id"), "產品 block.id")
            if block_id in blocks:
                raise ValueError(f"產品文件含重複 block_id：{block_id}")
            if not isinstance(block.get("text"), str):
                raise ValueError("產品 block.text 必須是字串。")
            blocks[block_id] = _normalized(block["text"])
    total = valid = required = without_valid = 0
    invalid = []
    for row in rows:
        valid_in_row = 0
        for index, evidence in enumerate(row.get("evidence", [])):
            total += 1
            reason = None
            if not isinstance(evidence, dict):
                reason = "引用不是 JSON 物件"
            elif not isinstance(evidence.get("block_id"), str) or evidence["block_id"] not in blocks:
                reason = "產品來源區塊不存在"
            elif not isinstance(evidence.get("quote"), str) or not _normalized(evidence["quote"]):
                reason = "引文為空白或不是字串"
            elif _normalized(evidence["quote"]) not in blocks[evidence["block_id"]]:
                reason = "引文不是所指產品區塊的連續原文（僅正規化空白）"
            if reason:
                invalid.append({
                    "standard_name": row["standard_name"], "block_id": row["block_id"],
                    "evidence_index": index, "reason": reason,
                })
            else:
                valid += 1
                valid_in_row += 1
        if row["status"] in {"match", "partial", "mismatch"}:
            required += 1
            if not valid_in_row:
                without_valid += 1
    return {
        "scope": "all_exported_results",
        "product_block_count": len(blocks),
        "total_quotes": total,
        "valid_quotes": valid,
        "invalid_quotes": total - valid,
        "quote_validity_rate": _ratio(valid, total),
        "rows_requiring_evidence": required,
        "rows_without_valid_required_evidence": without_valid,
        "invalid_details": invalid,
        "limitation": "只驗證匯出引文存在於產品快照；不驗證語意相關性，亦無法計算推論時已被移除的無效引文。",
    }


def evaluate(run: dict, gold: dict) -> dict:
    """Return transparent row-level metrics; never use manual final_status."""
    if not isinstance(run, dict) or not isinstance(gold, dict):
        raise ValueError("匯出與人工答案的頂層必須是 JSON 物件。")
    predictions = _index(run.get("results"), "results")
    answers = _index(gold.get("rows"), "rows", gold=True)
    documents = run.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError("documents 必須是陣列。")
    warnings = []
    if run.get("mode") == "demo":
        warnings.append("示範模式未呼叫模型；本報告只能驗證評估流程，不可作為模型品質成績。")
    elif run.get("mode") != "local":
        warnings.append("匯出未標示 mode=local，不能確認結果來自本地模型。")
    if run.get("status") != "completed":
        warnings.append("執行尚未完整完成；缺少的預測會列入報告，不能忽略後宣稱通過。")
    if not answers:
        warnings.append("人工答案沒有任何列，無法計算品質指標。")
    confusion = {actual: {predicted: 0 for predicted in (*STATUSES, NO_PREDICTION)} for actual in STATUSES}
    matched = correct = false_matches = critical_false_matches = 0
    difference_total = difference_detected = difference_review_captured = 0
    review_total = review_detected = 0
    gold_counts = dict.fromkeys(STATUSES, 0)
    predicted_counts = dict.fromkeys((*STATUSES, NO_PREDICTION), 0)
    missing = []
    rows = []
    for key, expected in answers.items():
        prediction = predictions.get(key)
        actual = expected["status"]
        predicted = prediction["status"] if prediction is not None else NO_PREDICTION
        gold_counts[actual] += 1
        predicted_counts[predicted] += 1
        confusion[actual][predicted] += 1
        if prediction is not None:
            matched += 1
        else:
            missing.append({"standard_name": key[0], "block_id": key[1]})
        is_correct = actual == predicted
        correct += int(is_correct)
        false_match = predicted == "match" and actual != "match"
        false_matches += int(false_match)
        critical_false_matches += int(false_match and expected["critical"])
        if actual in DIFFERENCES:
            difference_total += 1
            difference_detected += int(predicted in DIFFERENCES)
            difference_review_captured += int(predicted in REVIEW_NEEDED)
        if actual in REVIEW_NEEDED:
            review_total += 1
            review_detected += int(predicted in REVIEW_NEEDED)
        rows.append({
            "standard_name": key[0], "block_id": key[1], "gold_status": actual,
            "ai_status": None if prediction is None else predicted,
            "critical": expected["critical"], "correct": is_correct,
            "false_match": false_match,
        })
    if answers and not matched:
        warnings.append("沒有任何人工答案鍵能對上匯出結果；品質比率為 null，請確認檔名、區塊 ID 與版本。")
    if missing:
        warnings.append("部分人工答案缺少預測；只要有對上資料，品質比率的分母仍包括這些缺列。")
    extras = [{"standard_name": key[0], "block_id": key[1]} for key in predictions if key not in answers]
    if extras:
        warnings.append("部分匯出結果未納入人工答案；本報告只評估標註子集，不能代表整份文件。")
    meaningful = bool(matched)
    citations = _citation_metrics(documents, list(predictions.values()))
    if not citations["product_block_count"]:
        warnings.append("匯出沒有產品文字快照，無法證實任何引文有效。")
    return {
        "schema_version": 1,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run.get("id"), "mode": run.get("mode"), "run_status": run.get("status"),
        "model": (run.get("settings") or {}).get("model") if isinstance(run.get("settings"), dict) else None,
        "prediction_source": "results[].status (original AI); review.final_status is ignored",
        "rates_are_fractions": True,
        "warnings": warnings,
        "counts": {
            "gold_rows": len(answers), "exported_predictions": len(predictions),
            "matched_predictions": matched, "missing_predictions": len(missing),
            "unscored_predictions": len(extras), "correct": correct,
            "gold_by_status": gold_counts, "predicted_by_status_for_gold": predicted_counts,
            "gold_difference_rows": difference_total, "detected_difference_rows": difference_detected,
            "gold_review_needed_rows": review_total, "detected_review_needed_rows": review_detected,
            "difference_rows_captured_for_review": difference_review_captured,
            "false_match_count": false_matches, "critical_false_match_count": critical_false_matches,
        },
        "metrics": {
            "prediction_coverage": _ratio(matched, len(answers)),
            "accuracy": _ratio(correct, len(answers), meaningful=meaningful),
            "difference_recall": _ratio(difference_detected, difference_total, meaningful=meaningful),
            "review_detection_rate": _ratio(review_detected, review_total, meaningful=meaningful),
            "difference_review_capture_rate": _ratio(difference_review_captured, difference_total, meaningful=meaningful),
        },
        "confusion_matrix": confusion,
        "missing_predictions": missing,
        "unscored_predictions": extras,
        "citations": citations,
        "rows": rows,
    }


def main(argv=None) -> int:
    # Windows redirected streams may use cp1252; argparse help and Chinese JSON
    # must use the same explicit encoding as our UTF-8 report files.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="以獨立人工答案評估 JSON 匯出的 AI 原始判定；不呼叫模型。")
    parser.add_argument("run_export", type=Path, help="本工具匯出的執行結果 JSON")
    parser.add_argument("gold", type=Path, help="人工標準答案 JSON（包含 rows）")
    parser.add_argument("--output", type=Path, help="另存 JSON 評估報告；省略時輸出至終端機")
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() in {args.run_export.resolve(), args.gold.resolve()}:
            raise ValueError("輸出檔不可覆寫執行匯出或人工答案。")
        report = evaluate(load_json(args.run_export), load_json(args.gold))
        output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(f"已輸出評估報告：{args.output}")
        else:
            print(output, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(f"評估失敗：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
