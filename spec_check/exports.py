"""Self-contained review exports with complete source snapshots and no API keys."""

from __future__ import annotations

import html
import io
import json
import re
from datetime import datetime, timezone

STATUSES = {"match": "符合", "partial": "部分符合", "mismatch": "不符", "missing": "產品文件未載明", "uncertain": "待釐清"}
DECISIONS = {"pending": "待覆核", "confirmed": "確認 AI 判定", "changed": "人工更正", "reopened": "重新開啟"}
_SECRET_KEYS = {"api_key", "apikey", "authorization", "access_token", "refresh_token", "password", "secret", "client_secret"}


def _sanitize(value):
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items() if str(key).lower().replace("-", "_") not in _SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _escape(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _effective(row: dict) -> str:
    review = row.get("review") or {}
    if review.get("decision") in {"confirmed", "changed"} and review.get("final_status"):
        return review["final_status"]
    return row.get("status", "uncertain")


def _paragraph(value, css="") -> str:
    return f'<pre class="{css}">{_escape(value)}</pre>'


def _list(values) -> str:
    return "<ul>" + "".join(f"<li>{_escape(value)}</li>" for value in values) + "</ul>" if values else "<p>無</p>"


def _html(run: dict) -> bytes:
    sections = ["""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>規格比對與人工覆核報告</title>
<style>body{font-family:system-ui,'Microsoft JhengHei',sans-serif;color:#172b3a;max-width:1200px;margin:32px auto;padding:0 24px;line-height:1.65}h1,h2,h3{line-height:1.3}h2{border-bottom:2px solid #17696a;padding-bottom:8px;margin-top:40px}h3{margin-top:12px}pre{font-family:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}table{border-collapse:collapse;width:100%;margin:12px 0}td,th{border:1px solid #c8d5dc;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf4f6}.row,.source{border:1px solid #c8d5dc;border-radius:8px;padding:18px;margin:16px 0}.muted{color:#526571;font-size:.9em}.notice{background:#fff4d5;border-left:4px solid #ad7700;padding:14px}.badge{display:inline-block;background:#e8f3f2;padding:3px 12px;border-radius:12px}.evidence{border-left:3px solid #408787;padding-left:14px}.metadata{font-size:.85em}a{color:#136e70}@media print{body{max-width:none;margin:0;padding:0;font-size:10pt}.row,.source{border-radius:0}h2,h3{break-after:avoid}thead{display:table-header-group}pre,td{overflow-wrap:anywhere}.row{break-inside:auto}@page{margin:16mm}}</style></head><body>"""]
    sections.append("<h1>規格比對與人工覆核報告</h1>")
    sections.append(_paragraph(f"執行 ID：{run.get('id', '')}　狀態：{run.get('status', '')}\n建立時間：{run.get('created_at', '')}　完成時間：{run.get('finished_at') or '尚未完成'}\n模式：{run.get('mode', '')}　完成區塊：{run.get('completed', 0)} / {run.get('total', 0)}"))
    notice = "此報告保留已產生的全部比對列、原文證據、文件抽取快照及覆核歷程。文件符合度參考分數不代表法規認證；區塊覆蓋不保證所有語意差異已被找出。產品文件未載明不等於產品本身不合規。覆核人員為自行填寫，非驗證身分或電子簽章。"
    if run.get("status") != "completed":
        notice += " 此次執行尚未完整完成，分數及結果屬暫定；未產生結果的區塊仍需比對。"
    if run.get("mode") == "demo":
        notice += " 這是合成範例示範結果，未呼叫本地模型，不可作為真實產品判定。"
    sections.append(f'<p class="notice">{_escape(notice)}</p>')
    if run.get("error"):
        sections.append("<h3>執行診斷</h3>" + _paragraph(run["error"]))
    sections.append('<p><a href="#results">逐條差異與覆核</a> · <a href="#sources">原始文件文字快照</a> · <a href="#run">執行設定</a></p>')
    sections.append("<h2>文件符合度參考分數</h2><table><thead><tr><th>規範文件</th><th>參考分數</th><th>完成／總區塊</th><th>已覆核</th><th>各狀態與覆蓋率</th></tr></thead><tbody>")
    for ranking in run.get("rankings", []):
        score = ranking.get("score", 0)
        score_text = f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
        sections.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [ranking.get("standard_name", ""), score_text, f"{ranking.get('completed', 0)} / {ranking.get('total', 0)}", ranking.get("reviewed", 0), _json({"counts": ranking.get("counts", {}), "coverage": ranking.get("coverage")})]) + "</tr>")
    sections.append("</tbody></table><p class=muted>分數＝（符合＋0.5 × 部分符合）÷ 全部規範區塊 × 100；採用已確認／更正的人工判定，其餘採 AI 判定。</p>")
    sections.append('<h2 id="results">逐條差異與人工覆核</h2>')
    for index, row in enumerate(run.get("results", []), 1):
        review = row.get("review") or {}
        sections.append(f'<article class="row"><h3>{index}. {_escape(row.get("standard_name", ""))} — {_escape(row.get("location", ""))}</h3>')
        sections.append(f'<p><span class="badge">目前判定：{_escape(STATUSES.get(_effective(row), _effective(row)))}</span>　AI：{_escape(STATUSES.get(row.get("status"), row.get("status", "")))}　信心值：{_escape(row.get("confidence", ""))}</p>')
        sections.append(_paragraph(f"結果 ID：{row.get('id', '')}　規範 ID：{row.get('standard_id', '')}　區塊：{row.get('block_id', '')}", "muted"))
        sections.append("<h4>規範原文</h4>" + _paragraph(row.get("requirement", "")))
        sections.append("<h4>判定說明</h4>" + _paragraph(row.get("explanation", "")))
        sections.append("<h4>差異點</h4>" + _list(row.get("differences") or []))
        sections.append("<h4>產品原文證據</h4>")
        for evidence in row.get("evidence") or []:
            sections.append('<div class="evidence">' + _paragraph(f"{evidence.get('block_id', '')}　{evidence.get('location', '')}", "muted") + _paragraph(evidence.get("quote", "")) + "</div>")
        if not row.get("evidence"):
            sections.append("<p>無有效原文引述。</p>")
        sections.append("<h4>抽取／比對警告與產品覆蓋</h4>" + _list(row.get("warnings") or []) + _paragraph(_json(row.get("product_coverage", {})), "metadata"))
        sections.append("<h4>目前覆核</h4>" + _paragraph(f"狀態：{DECISIONS.get(review.get('decision', 'pending'), review.get('decision', 'pending'))}\n覆核人員：{review.get('reviewer', '')}\n人工判定：{STATUSES.get(review.get('final_status'), review.get('final_status') or '')}\n更新：{review.get('updated_at') or ''}　版本：{review.get('version', 0)}\n註記：{review.get('note', '')}"))
        sections.append("<h4>完整覆核歷程</h4>" + _paragraph(_json(row.get("history", [])), "metadata") + "</article>")
    sections.append('<h2 id="sources">原始文件文字快照與來源資訊</h2><p>以下保留本次執行使用的全部抽取區塊。圖像與抽取限制請參考各文件警告；原始上傳檔另由系統保存。</p>')
    for document in run.get("documents", []):
        metadata = {key: value for key, value in document.items() if key != "blocks"}
        sections.append(f'<section class="source"><h3>{_escape(document.get("name", ""))}</h3>' + _paragraph(_json(metadata), "metadata"))
        for block in document.get("blocks", []):
            sections.append(f'<h4>{_escape(block.get("id", ""))} — {_escape(block.get("location", ""))}</h4>' + _paragraph(block.get("text", "")))
        sections.append("</section>")
    sections.append('<h2 id="run">執行設定與中繼資料（不含密鑰）</h2>')
    sections.append(_paragraph(_json({key: value for key, value in run.items() if key not in {"documents", "results", "rankings"}}), "metadata"))
    # Keep unknown extension fields too, without exposing executable script content.
    sections.append("<h2>完整執行資料快照（JSON）</h2>" + _paragraph(_json(run), "metadata"))
    sections.append("</body></html>")
    return "".join(sections).encode("utf-8")


def _excel_text(value) -> str:
    text = _json(value) if isinstance(value, (dict, list, tuple)) else str(value if value is not None else "")
    # XML 1.0 disallows these; retain an explicit visible encoding instead of dropping.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda match: f"\\u{ord(match.group(0)):04x}", text)


def _xlsx(run: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook()
    workbook.remove(workbook.active)

    def sheet(name: str, headings: list[str]):
        current = workbook.create_sheet(name)
        current.append(headings + ["分段"])
        current.freeze_panes = "A2"
        for cell in current[1]:
            cell.fill = PatternFill("solid", fgColor="17696A")
            cell.font = Font(color="FFFFFF", bold=True)
            current.column_dimensions[cell.column_letter].width = 24
        return current

    def append(current, values: list):
        # Excel silently truncates cells >32767 chars; use explicit continuation rows.
        texts = [_excel_text(value) for value in values]
        pieces = [[text[index:index + 30_000] for index in range(0, len(text), 30_000)] or [""] for text in texts]
        count = max(len(part) for part in pieces)
        for index in range(count):
            row = [part[index] if index < len(part) else "" for part in pieces]
            row.append(f"{index + 1}/{count}")
            current.append(row)
            for cell in current[current.max_row]:
                # Explicit text cells prevent =,+,-,@ from becoming executable formulas.
                cell.data_type = "s"
                cell.number_format = "@"
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    results = sheet("Results", ["結果 ID", "規範 ID", "規範文件", "區塊", "位置", "規範原文", "AI 判定", "目前判定", "信心值", "說明", "全部差異點 JSON", "全部產品證據 JSON", "產品覆蓋 JSON", "警告 JSON", "覆核狀態", "人工判定", "覆核人員", "覆核註記", "覆核時間", "覆核版本", "完整結果 JSON"])
    history = sheet("History", ["結果 ID", "事件 ID", "決定", "最終狀態", "覆核人員", "註記", "時間", "版本", "完整事件 JSON"])
    for row in run.get("results", []):
        review = row.get("review") or {}
        append(results, [row.get("id"), row.get("standard_id"), row.get("standard_name"), row.get("block_id"), row.get("location"), row.get("requirement"), row.get("status"), _effective(row), row.get("confidence"), row.get("explanation"), row.get("differences", []), row.get("evidence", []), row.get("product_coverage", {}), row.get("warnings", []), review.get("decision"), review.get("final_status"), review.get("reviewer"), review.get("note"), review.get("updated_at"), review.get("version", 0), row])
        for event in row.get("history", []):
            append(history, [row.get("id"), event.get("id"), event.get("decision"), event.get("final_status"), event.get("reviewer"), event.get("note"), event.get("created_at"), event.get("version"), event])
    documents = sheet("Documents", ["文件 ID", "檔名", "角色", "SHA256", "建立時間", "抽取已確認", "警告 JSON", "區塊 ID", "來源位置", "全部抽取文字", "文件中繼資料 JSON"])
    for document in run.get("documents", []):
        metadata = {key: value for key, value in document.items() if key != "blocks"}
        for block in document.get("blocks") or [{}]:
            append(documents, [document.get("id"), document.get("name"), document.get("role"), document.get("sha256"), document.get("created_at"), document.get("extraction_confirmed"), document.get("warnings", []), block.get("id"), block.get("location"), block.get("text"), metadata])
    metadata_sheet = sheet("Run", ["欄位", "值"])
    append(metadata_sheet, ["exported_at", datetime.now(timezone.utc).isoformat()])
    append(metadata_sheet, ["說明", "文件符合度參考分數不是法規認證。區塊覆蓋不保證全部語意差異已找出。產品文件未載明不等於產品不合規。未完整執行的結果與分數屬暫定。覆核身分為自行填寫，非電子簽章。示範模式僅供展示。"])
    append(metadata_sheet, ["長文字與安全", "所有儲存格均為純文字；超過 30,000 字元拆為連續分段列，續列順序依「分段」欄。XML 不允許的控制字元以 \\uXXXX 顯示。完整結果 JSON 可依分段順序還原。"])
    for key, value in run.items():
        if key not in {"documents", "results"}:
            append(metadata_sheet, [key, value])
    for current in workbook.worksheets:
        current.auto_filter.ref = current.dimensions
    data = io.BytesIO()
    workbook.save(data)
    return data.getvalue()


def export_run(run: dict, format: str) -> tuple[bytes, str, str]:
    """Return report bytes, content type and a safe attachment filename."""
    if format not in {"html", "xlsx", "json"}:
        raise ValueError("匯出格式必須是 html、xlsx 或 json。")
    snapshot = _sanitize(run)
    identifier = re.sub(r"[^A-Za-z0-9_-]", "_", str(run.get("id", "report")))[:80] or "report"
    name = f"spec-check-{identifier}.{format}"
    if format == "json":
        return _json(snapshot).encode("utf-8"), "application/json; charset=utf-8", name
    if format == "html":
        return _html(snapshot), "text/html; charset=utf-8", name
    return _xlsx(snapshot), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", name
