# v0.3 工作流程 API

此文件定義新版前後端介面。原有 `/api/projects`、`/api/runs` 與覆核資料繼續保留。
新版入口 `/`，舊版入口 `/classic`。新版前端使用 `/api/v2`；模型設定沿用 `/api/settings`、`/api/connection`。

所有清單回傳 `{items: [], total, offset, limit}`，`offset=0`、`limit=50`，上限 200。
文件摘要不包含原文及項目全文。時間為 ISO 8601 UTC。錯誤 `{detail: string}`。

## 文件庫與項目

- `POST /api/v2/import-project`：`{project_id}` 將舊版專案文件匯入新版文件庫／產品清單，回傳 `{items,total,reused}`；相同內容沿用、不修改舊紀錄，新的項目仍須抽取及確認。

- `GET /api/v2/library?q=&offset=&limit=`：標準文件摘要。
- `POST /api/v2/library`：multipart `file` 上傳標準。
- `GET /api/v2/products`、`POST /api/v2/products`：產品文件，POST 同上。
- `GET /api/v2/documents/{id}`：文件原文 blocks、metadata、warnings、version、index_status、confirmed、item_count、unresolved_count。
- `GET /api/v2/documents/{id}/original`：原始檔案。
- `PATCH /api/v2/documents/{id}`：`{expected_version,reviewer,note,metadata:{category,scope,version,region}}`，可空白，空白意義為未知。
- `POST /api/v2/documents/{id}/extract`：`{force:false}`，回傳 job。使用已設定本地模型。已完成且非 force 時沿用既有項目。
- `POST /api/v2/library/extract`：`{document_ids:[]}`，選定標準批次抽取，回傳 job。
- `GET /api/v2/jobs/{id}`：`{id,status,phase,completed,total,document_id,document_name,progress,error,...}`。
- `POST /api/v2/jobs/{id}/cancel`、`/resume`：停止／繼續。
- `GET /api/v2/documents/{id}/items?offset=&limit=&kind=&q=`：項目清單，另帶 document 摘要。
- `PUT /api/v2/documents/{id}/items/{item_id}`：`{expected_version,reviewer,note,item:{name,parameter,value,unit,operator,conditions,exceptions,test_method,criticality,criticality_basis,quote,block_id,kind}}`。expected_version 是文件版本；來源引文必須可驗證。kind 可為 requirement/specification/context/unresolved，criticality 可 high/medium/low/unknown。
- `POST /api/v2/documents/{id}/items/{item_id}/split`：同上，以 `items:[...]` 取代 item；保留拆分紀錄及未涵蓋原文。
- `POST /api/v2/documents/{id}/confirm`：`{expected_version,reviewer,note,acknowledge_warnings:true}`；須完成抽取、已看過來源及待處理提示。
- `POST /api/v2/library/confirm`：`{documents:[{id,expected_version}],reviewer,note,acknowledge_warnings:true}`，明確確認所勾選文件。
- `GET /api/v2/documents/{id}/audit`：文件及項目操作歷程。

## 分析

- `GET /api/v2/analyses`：歷次分析摘要。
- `POST /api/v2/analyses`：`{name,product_id,library_ids:[],context:{purpose,environment,market,notes},comparison_mode:"focused"}`。library_ids 空陣列表示全部未封存標準；所有文件須完成抽取並確認。建立不可變文件／項目快照並啟動全庫初篩。模式 focused 或 exhaustive。
- `GET /api/v2/analyses/{id}`：輕量摘要，含 `status,phase,version,job_id,progress,counts,coverage,product_name,comparison_mode`。
- `GET /api/v2/analyses/{id}/screening`：依高／中／未知／低相關分頁，每列 `{id,standard_id,standard_name,relevance,relevance_score,applicability,reason,evidence,warnings,selected,review}`。所有標準起始 selected=true；模型不會靜默排除文件。
- `POST /api/v2/analyses/{id}/selection`：`{expected_version,reviewer,note,decisions:[{standard_id,selected,reason}]}`。只能在 awaiting_selection 操作；排除須具理由。版本是 analysis.version。
- `POST /api/v2/analyses/{id}/compare`：`{expected_version}`，依確認選取的標準開始項目比對，回傳分析摘要。
- `POST /api/v2/analyses/{id}/cancel`、`/resume`：控制目前分析工作。
- `GET /api/v2/analyses/{id}/results?status=&risk=&standard_id=&review=&q=&offset=&limit=`：依高／待評估／中／低／未發現差異優先度分頁。status=match/partial/mismatch/missing/uncertain，risk=high/medium/low/unknown/none，review=pending/confirmed/changed/reopened。
- `GET /api/v2/analyses/{id}/results/{result_id}`：完整單筆，含 history。
- `POST /api/v2/analyses/{id}/results/{result_id}/reviews`：`{expected_version,decision,final_status,reviewer,note,risk_level}`。expected_version=result.review.version。risk_level 可省略；改判、調整風險及重新開啟必須原因。
- `GET /api/v2/analyses/{id}/product-extras`：尚未建立對應的產品項目；`coverage_complete` 另列，不可當成規範沒有要求的證明。
- `GET /api/v2/analyses/{id}/audit`：所有分析／選取／覆核操作紀錄。
- `GET /api/v2/analyses/{id}/export?format=html|xlsx|json`：包含相關標準、風險、待補證據、未對應產品、原文、抽取及覆核歷程的報告。
- `GET /api/v2/analyses/{id}/exports`：匯出封存清單；下載沿用 `/api/exports/{export_id}/download`。

分析狀態：queued/screening/awaiting_selection/comparing/completed/cancelled/interrupted/failed。
job 狀態：queued/running/completed/cancelled/interrupted/failed。
progress 包含 stage、started_at、updated_at、request_started_at、last_response_at、finished_at、requests_completed、window_index、window_total、events；只在實際回覆時更新 last_response_at。

## 匯出資料

分析快照具有 `schema_version:2,kind:"analysis",id,name,project_name,status,documents,results,screenings,product_extras,audit,coverage,counts,rankings:[]`。
documents 包含不可變原文 blocks、抽取 items 與 extraction_audit。results 保留舊版欄位及 `item,risk,ai_risk,retrieval,matched_product_item_ids`。
`execution_jobs` 保存各階段模型設定、可用的模型 SHA256 與續跑紀錄。`coverage.selected_scope_complete` 表示所選要求已完成全部產品來源掃描；`coverage.coverage_complete` 還要求沒有排除任何本次初篩標準。兩者都不代表模型判斷正確或語意差異零遺漏。`unresolved_items` 保留未可靠拆解的項目數。
人工風險覆核保存在 review.risk_level 與 history。相關性、符合狀態及風險優先度分開呈現。

## 外部規範萃取（v0.3.1）

僅 `role=standard` 開放，產品請求遭拒；不呼叫本機或外部模型。
路徑基底 `D=/api/v2/documents/{id}/external`。

| 方法 | 路徑 | 輸入／結果 |
|---|---|---|
| GET | D | `{items:[package summary]}`，此清單不分頁；含 received_count、batch_count、missing_batches、stale、version |
| POST | D | `{expected_version:文件版本,reviewer,acknowledge_public:true,blocks_per_batch:4}`；每批 1–8 區塊，回傳 package summary |
| GET | D/{package_id}/download | ZIP：Prompt、schema、source、全部 batch input；檔名由系統生成 |
| POST | D/{package_id}/preview | multipart 多個 `files` + JSON 文字 `values`，回傳預覽、preview_sha256；不保存批次或更改 index |
| POST | D/{package_id}/import | 同上，values 加 preview_sha256；重新驗證並原子保存本輪所有批次及 audit |
| POST | D/{package_id}/activate | `{expected_version:包版本,reviewer,note,acknowledge_warnings:true}`；必須全部收齊，建立新 index 並清除 confirmed |

preview/import 的 values：`{expected_version:包版本,reviewer,note,model_label}`。同次 1–50 個檔案、合計上限 16 MB；model_label 為使用者填報，不保證是實際模型身分。改檔案、填寫資料或版本須重新預覽。相同內容與模型標籤沿用；修正暫存批次會保存替換前雜湊與操作歷程。

輸出格式由包內 `result.schema.json` 定義：format=`local-specheck-extraction-result`、schema_version=1，必須照抄 package_id/document_id/source_sha256/batch_id，blocks 恰含本批全部 id。每 block 有 items，每 item 為既有文字欄位（block_id 在外層）與 `context_evidence:[{block_id,quote}]`。主要引文與上下文引文須逐字存在於同一文件；context 中的疑似要求保守降為 unresolved，未涵蓋原文補 unresolved。

文件版本改變或正在本地抽取、包已套用、來源雜湊不同皆拒絕套用。套用為單一資料庫交易；舊 index 及已建立分析快照不變。保存的是正規化項目及原始檔內容雜湊，不保存 ChatGPT 對話或原上傳 bytes。詳細操作與 Prompt 見 [EXTERNAL_EXTRACTION.md](EXTERNAL_EXTRACTION.md)。

## v0.3.2 連結及完整成果

- `POST /api/v2/library/link`：JSON `{url, filename?}`，公開 HTTPS 下載規範，回傳 document summary；同內容重用。
- `GET /api/v2/library/standalone/prompt`：下載外網專用 Prompt。
- `POST /api/v2/library/standalone/preview`、`/import`：multipart `file`（一份完整成果）、`values`（JSON 文字）。
- `POST /api/v2/library/standalone-link/preview`、`/import`：JSON，含 `url`、選填 `filename` 與操作欄位。

操作欄位：`reviewer`、`model_label`、`note` 必填；import 另須 preview 回傳的 `preview_sha256`、`acknowledge_warnings: true`。預覽只回傳名稱、數量、coverage、warnings、sample_items（最多 10）與重用狀態，不写文件库；import 回傳 document。連結保存重新下載，位元組或操作內容不同回 409。格式錯誤或正式匯入 partial 成果回 400；partial 可以 preview，但不寫入文件庫。完整成果每檔 16 MB。file 與 link 入口不得混用預覽指紋。

完整成果格式與限制見 [Prompt](CHATGPT_EXTERNAL_LINK_PROMPT.md) 和 [合成範例](../examples/external-extraction/complete.standard.json)。此格式不含原系統的 document_id／package_id，不適用於舊批次 `.result.json`。
