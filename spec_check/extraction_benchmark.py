"""Small, reproducible extraction probes; exact-field checks are not semantic gold."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from . import analysis_engine as ae, engine


def load_cases(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 必須是非空陣列")
    ids = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]
                or case["id"] in ids or case.get("role") not in ("standard", "product")
                or not isinstance(case.get("text"), str) or not case["text"].strip()
                or not isinstance(case.get("expected"), list) or not case["expected"]):
            raise ValueError("案例 ID、角色、原文或 expected 不合法")
        ids.add(case["id"])
        for expected in case["expected"]:
            if not isinstance(expected, dict) or "kind" not in expected or len(expected) < 2:
                raise ValueError("每個 expected 須含 kind 及至少一個可核對欄位")
            for key, value in expected.items():
                if key not in (*ae._ITEM_FIELDS, "kind", "criticality"):
                    raise ValueError("expected 含未知欄位")
                if not isinstance(value, (str, list)) or (isinstance(value, list) and
                        (not value or not all(isinstance(v, str) for v in value))):
                    raise ValueError("期望值須為字串或非空字串陣列（可接受的替代寫法）")
            kind = expected["kind"]
            if kind not in ("requirement", "specification", "context", "unresolved"):
                raise ValueError("expected.kind 不合法")
    return cases, hashlib.sha256(raw).hexdigest()


def score_case(expected, items):
    """Maximum one-to-one match; one merged prediction cannot satisfy two atoms."""
    candidates = []
    for target in expected:
        candidates.append([i for i, item in enumerate(items) if all(
            item.get(key) in (value if isinstance(value, list) else [value]) for key, value in target.items())])
    assignments = {}

    def assign(gold_index, seen):
        for prediction_index in candidates[gold_index]:
            if prediction_index in seen:
                continue
            seen.add(prediction_index)
            if prediction_index not in assignments or assign(assignments[prediction_index], seen):
                assignments[prediction_index] = gold_index
                return True
        return False

    for i in range(len(expected)):
        assign(i, set())
    reverse = {g: p for p, g in assignments.items()}
    resolved = [i for i, target in enumerate(expected) if target["kind"] in ("requirement", "specification")]
    return {
        "expected_atoms": len(expected), "matched_atoms": len(assignments),
        "expected_resolved_atoms": len(resolved), "matched_resolved_atoms": sum(i in reverse for i in resolved),
        "missing_expected_indices": [i for i in range(len(expected)) if i not in reverse],
        "extra_prediction_indices": [i for i in range(len(items)) if i not in assignments],
        "matches": [{"expected_index": g, "prediction_index": p} for g, p in sorted(reverse.items())],
    }


def run(cases, settings, repeats=1, progress=None):
    records = []
    for repeat in range(1, repeats + 1):
        for case in cases:
            if progress:
                progress(f"第 {repeat}/{repeats} 輪：{case['id']}，等待本機模型回覆…")
            started = time.monotonic()
            result = ae.extract_block({"id": case["id"], "text": case["text"], "location": "合成測試案例"},
                                      case["role"], settings)
            records.append({"case_id": case["id"], "repeat": repeat, "seconds": round(time.monotonic() - started, 3),
                            "source": case["text"], "expected": case["expected"], "result": result,
                            "score": score_case(case["expected"], result["items"])})
    keys = ("expected_atoms", "matched_atoms", "expected_resolved_atoms", "matched_resolved_atoms")
    counts = {key: sum(record["score"][key] for record in records) for key in keys}
    counts["unresolved_predictions"] = sum(item["kind"] == "unresolved" for record in records for item in record["result"]["items"])
    counts["extra_predictions"] = sum(len(record["score"]["extra_prediction_indices"]) for record in records)
    return {"counts": counts, "exact_fixture_recall": counts["matched_atoms"] / counts["expected_atoms"],
            "resolved_fixture_recall": counts["matched_resolved_atoms"] / counts["expected_resolved_atoms"] if counts["expected_resolved_atoms"] else None,
            "seconds": round(sum(record["seconds"] for record in records), 3), "records": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description="在本機模型執行原子萃取探針；保存逐項結果供人工驗收，不自動宣告品質通過。")
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1] / "examples/local-extraction/cases.json")
    parser.add_argument("--base-url", default=engine.DEFAULT_SETTINGS["base_url"])
    parser.add_argument("--model", required=True, help="本機 /models 回傳的模型 ID")
    parser.add_argument("--label", required=True, help="人工填寫精確 GGUF 檔名／量化與 runtime 版本")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--repeat", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--no-structured-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("報告已存在，請使用新檔名以保留歷次結果")
        cases, fixture_sha256 = load_cases(args.cases)
        settings = engine.validate_settings({**engine.DEFAULT_SETTINGS, "model": args.model, "base_url": args.base_url,
            "max_tokens": args.max_tokens, "temperature": args.temperature, "timeout": args.timeout,
            "structured_output": not args.no_structured_output})
        print(f"即將執行 {len(cases)} 個案例 × {args.repeat} 次；只連線本機模型。", flush=True)
        report = {"format": "local-specheck-extraction-probe-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                  "model_label": args.label, "model_identity": "user_reported", "settings": {k: v for k, v in settings.items() if k != "api_key"},
                  "engine_version": ae.ENGINE_VERSION, "extraction_prompt_sha256": ae.EXTRACTION_PROMPT_SHA256,
                  "fixture_sha256": fixture_sha256,
                  "limitations": ["案例為開發用合成資料，尚未經獨立領域專家驗證，不代表正式標準品質。",
                      "計分為欄位精確比對；同義表達可能算未命中，額外／錯誤項目仍須人工檢查。",
                      "計分使用程式防護後的萃取結果，不是模型原始回覆；原文覆蓋不是語意召回率。",
                      "只測萃取，不測 PDF 轉錄、標準初篩、產品比對或硬體記憶體。"],
                  **run(cases, settings, args.repeat, progress=lambda message: print(message, flush=True))}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        print(f"已保存：{args.output}；欄位命中 {report['counts']['matched_atoms']}/{report['counts']['expected_atoms']}。請逐項人工確認。")
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
