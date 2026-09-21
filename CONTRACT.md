# Internal implementation contract (v1)

Python 3.11+, FastAPI app `spec_check.app:app`; stdlib sqlite3. Vanilla static UI. UI and docs in Traditional Chinese. Localhost only, no CDN or telemetry. Runtime files under env `SPEC_CHECK_DATA` or `./data`. All timestamps UTC ISO8601. IDs uuid hex.

## Domain JSON

- Document: `{id,name,role:product|standard,sha256,created_at,blocks:[{id,location,text}],warnings:[string],extraction_confirmed:bool}`. Full original preserved in data/uploads, not repo. Block IDs B00001... unique per document. location PDF page, DOCX paragraph/table/row, XLSX sheet/row, text line. Each text block <=1400 chars, never silently truncate. Warnings for scan/image and extraction limitations. Confirmation explicit after preview. title headers preserved too.
- Settings: `{base_url:"http://127.0.0.1:1234/v1",model:"",api_key:"",temperature:0.1,max_tokens:1800,timeout:180,context_chars:6000,structured_output:true}`. API key never exported/logged or returned except has_api_key. Require numeric loopback host or localhost; no public endpoints, redirects, env proxy. Bionic custom URL, no guessed port.
- Project `{id,name,created_at,documents:[Document]}`. One product, >=1 standard per run. Upload new docs does not alter existing runs.
- Run `{id,project_id,status:queued|running|completed|cancelled|interrupted|failed,created_at,finished_at,mode:local|demo,settings:{no api_key},document_ids,documents:[snapshots],total,completed,error,results:[],rankings:[]}`. total all standard blocks. Never drop a block. Failure produces row `uncertain` with diagnostic, not implicit compliance. Each row saved as completed. Resume skips saved rows; fresh run is separate immutable results set. Model request fail => row uncertain. Stop after current HTTP call.
- Result `{id,run_id,standard_id,standard_name,block_id,location,requirement,status:match|partial|mismatch|missing|uncertain,explanation,differences:[string],evidence:[{block_id,location,quote}],confidence:0..1,product_coverage:{scanned,total},warnings:[],review:{decision:pending|confirmed|changed|reopened,final_status:null|status,reviewer,note,updated_at,version},history:[]}`. Quote validated against cited product block (whitespace normalized); invalid quote removed and row uncertain. Product coverage means all product windows, not only search top-K. `missing` only all windows completed and no evidence, and means unspecified in product doc not proof of non-compliance. No valid quote => cannot match/partial/mismatch. Compound requirement differences retained as list. Prompt states differences per atomic condition within block; structural coverage is not guaranteed semantic completeness.
- Review event append only `{id,result_id,decision,final_status,reviewer,note,created_at,version}`. optimistic concurrency expected_version; reject mismatch 409. Confirm final_status same as AI status; changed requires final_status + non-empty note; reopen returns pending effective AI status. identity is self-declared, not authenticated signature.
- Rankings per standard `{standard_id,standard_name,total,completed,score,counts,reviewed,coverage}`. score = (match+0.5*partial)/total*100; all statuses denominator. effective manual verdicts used if confirmed/changed. Label "文件符合度參考分數" not legal certification nor vector similarity. incomplete/cancelled/interrupted run provisional. Never claim all differences known.

## API (JSON errors `{detail:message}`)

GET /api/settings => Settings sans api_key plus has_api_key
PUT /api/settings => Settings input; blank api_key keeps previous, clear_api_key true clears
POST /api/connection {settings?:Settings} => `{models:[{id}],message}` (does not save)
GET/POST /api/projects ; POST body `{name}` returns Project
GET /api/projects/{id} => Project
POST /api/projects/{id}/documents multipart `file`, `role` => Document
POST /api/projects/{id}/documents/{docid}/confirm => Document
GET /api/projects/{id}/documents/{docid}/original => attachment
POST /api/demo => Project with synthetic docs (confirmed) (does not call model)
POST /api/projects/{id}/runs `{standard_ids:[id],mode:local|demo}` => Run (demo only for synthetic demo project)
GET /api/projects/{id}/runs => brief Runs
GET /api/runs/{id} => full Run including results/rankings/history
POST /api/runs/{id}/cancel ; POST /api/runs/{id}/resume => Run
POST /api/results/{id}/reviews `{decision,final_status,reviewer,note,expected_version}` => updated Result
GET /api/runs/{id}/export?format=html|xlsx|json => attachment
GET /api/health => `{status:"ok",version:"0.1.0"}`

## Module ownership/interfaces

- ingestion.py: `extract_document(path:Path,name:str,role:str)->dict` document without id/created_at/extraction_confirmed (app fills); blocks/warnings/name/role/sha256.
- engine.py: `DEFAULT_SETTINGS`, `validate_settings(dict)->dict`, `check_connection(settings)->list[dict]`, `compare_block(requirement:dict, product_blocks:list[dict],settings:dict,mode="local")->dict` result payload excluding IDs/doc name/review/history. `rank_results(results:list,documents:list)->list[dict]`. Use urllib stdlib HTTP. All product chunks exhaustive sequential. `demo` deterministic synthetic only and labeled.
- storage.py/app.py owner root. `spec_check/__init__.py` version.
- exports.py: `export_run(run:dict,format:str)->tuple[bytes,str,str]` bytes, MIME, filename. Export all rows including pending and histories, source snapshots and configuration sans secrets in JSON/HTML; XLSX sheets Results, History, Documents, Run. Escape formulas and HTML.
- static/index.html, app.js, styles.css owned frontend agent. Use API above. Poll runs while active; show per-row comparisons/evidence/review and source preview. Don't rely on external assets. Errors visible; preserve draft reviews during polling.

## Implemented additions

- `GET /api/health` also returns `app:"LocalAIforSPECheck"` for launcher identity checks.
- Project includes `is_demo`; demo documents are immutable through upload UI.
- `DELETE /api/projects/{id}/documents/{docid}` soft-removes current input while retaining old snapshots/originals.
- Run records `prompt_version`, `prompt_sha256`, `app_version`, scoring and limitations; results/review histories are read in one database transaction.
- `compare_block(..., cancel_check=None)` raises `ComparisonCancelled` between requests; caller does not persist partial rows. `requirement.context` carries immediately adjacent standard blocks, which are supporting context, not additional focus requirements.
- Every export saves exact bytes under the data root's `reports/`, with ID, time, format and SHA-256; payload includes `export_id` and `exported_at`.
- `GET /api/runs/{id}/exports` lists exported versions; `GET /api/exports/{id}/download` returns original bytes and rejects a mismatched hash. API does not provide edits to old reports; this is not tamper-proof storage.
- Product coverage counts successfully parsed model windows; evidence validation warnings still force an uncertain row. Structural coverage does not measure semantic recall.
