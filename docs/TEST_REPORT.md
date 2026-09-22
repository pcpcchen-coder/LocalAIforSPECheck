# 開發驗證紀錄

## 0.3.2 規範連結與外網完整成果

日期：2026-09-22。新增公開 HTTPS 規範下載、可跨資料庫的 .standard.json 完整成果、檔案／連結預覽及匯入，以及外網專用 Prompt。

- 連結與完整成果 API／傳輸安全測試 49 項通過：HTTPS／port／帳密限制、私有與混合 DNS、重新導向逐站檢查、固定 IP 與原主機 SNI、NAT64 等特殊位址、大小上限、HTML／壓縮傳輸／不完整回應拒絕、去重、原子匯入、引文與型別、partial 確認阻擋、內容變更指紋、來源紀錄及 Unicode 拒絕。
- Linux Python 3.12 首輪 CI 全套：296 項、48 個子案例通過，1 項 Windows 專用測試依平台略過。
- 真實 Chromium + 真實後端／SQLite 通過：下載狀態、Prompt 下載、成果連結預覽、修改資料使預覽失效、檔案方式去重、人工確認、重新載入、HTML 連結錯誤與 390px 視窗，無 page error。網路傳輸使用合成替身；本機匯入流程實際執行。
- 舊版分批 ChatGPT 流程瀏覽器回歸通過；新介面截圖已檢視：[完整成果預覽](screenshots/link-import.png)。
- Windows 成品驗證已擴充完整成果 Prompt／匯入／來源追溯／重啟保存；CI 另對此 repo 固定 commit 的公開合成檔案執行真實 HTTPS 規範及成果連結下載。發行前須通過既有真模型、覆核、匯出及啟停檢查。

Windows 首輪 CI 抓到成品檢查程式以 LF 分割下載的 Prompt 範例、未處理 CRLF；已正規化換行並以兩種換行的真實 API 回歸案例驗證，保留全部原有斷言。

這些測試不代表 ChatGPT 已正確轉錄真實標準；測試未傳送任何公司文件。原始 PDF 的全文完整性與語意仍須人工核對。模型繼續獨立下載，ZIP 不含 GGUF。

重跑：`python -m pytest tests/test_link_import.py -q`；瀏覽器：`python tests/link_browser_smoke.py`。完整成果格式及外網操作見 [操作指南](LINK_IMPORT.md)。

## 0.3.1 手動 ChatGPT 規範萃取

日期：2026-09-22。新增外部萃取包、完整 Prompt、固定 schema、逐字引文核對、分批暫存及收齊後套用。

| 驗證 | 結果 |
|---|---|
| Linux Python 3.12 完整測試 | 248 項通過、48 個子案例通過；1 項 Windows Job Object 測試依平台略過 |
| 外部交換 API | 23 項：包內僅含標準、產品拒絕匯出、Prompt／schema 一致、預覽不改資料、分批保存、完整批次要求、重複沿用、原文及上下文引文核對、未涵蓋文字保留、JSON 截斷／重複欄位拒絕、版本衝突及資料庫重新開啟保存 |
| 接回既有比對與報告 | 外部項目套用後重新人工確認，再初篩、比對及匯出；報告保留外部來源，舊分析快照不变；再用本機重新抽取會清除過時外部身分 |
| 真實 Chromium | 下載 ZIP、分次匯入、重新載入續作、修改資料使預覽失效、拒絕偽造引文、收齊套用、人工確認與歷程均通過；390px 外部萃取視窗無水平溢出、無 page error |
| Portable 封裝 | Prompt 納入明確檔案清單；Windows 成品測試新增實際 ZIP 內的外部包下載、JSON 預覽／保存／套用與重啟保存檢查。實際發行驗證以該版 Release 的 portable-smoke-report.json 為準 |

發行來源 commit：`37364cbd0361cdc0e612e86e64df1beb91a1649f`。[六組跨平台 CI](https://github.com/pcpcchen-coder/LocalAIforSPECheck/actions/runs/35706919907) 全部通過；[Windows 成品建置及整合驗證](https://github.com/pcpcchen-coder/LocalAIforSPECheck/actions/runs/35706920298) 也通過，Python 3.13.15 為 249 項與 48 個子案例通過。成品實際驗證新增的 `external_prompt_export_preview_import_activate` 與 `external_extraction_restart_persistence`，並保留所有既有模型、覆核、匯出及啟停檢查。

[v0.3.1 Release](https://github.com/pcpcchen-coder/LocalAIforSPECheck/releases/tag/windows-portable-v0.3.1-37364cbd) 已公開 ZIP、檢查碼及成品報告。ZIP 為 41,305,193 bytes，SHA-256：`8e41e2b98e9c7a5cc65cf337fe9d25836eea745f888a8e6bb0ecd44e5a1890d9`；模型仍獨立下載。原有本機 2B 模型的新流程合成條款仍回傳 `uncertain`／`high`，此次沒有把外部匯入流程通過解讀為 ChatGPT 或本機模型的工程判斷準確率已驗收。

Windows 成品測試首次發現新舊測試範例使用相同原文，正確觸發內容去重，導致後續檢查的文件已被重新套用。已將獨立測試改用不同原文，新增直接執行 Portable 外部交換 helper 的 API 回歸測試，並保留原本重啟確認狀態檢查。

瀏覽器使用合成 JSON 模擬 ChatGPT 交付，不代表已呼叫 ChatGPT 或驗收真實規範的萃取準確率。沒有把產品或公司文件送到外部服務。[操作指南](EXTERNAL_EXTRACTION.md) 含完整流程、批次續作與修正命令；[完整 Prompt](CHATGPT_STANDARD_EXTRACTION_PROMPT.md) 與執行包使用相同內容。

重跑：`python -m pytest tests/test_external_extraction.py tests/test_workflows.py -q`；瀏覽器：`python tests/external_browser_smoke.py`（需開發用 Playwright，與使用者 Portable 無關）。

## 0.3.0 標準文件庫與風險覆核

日期：2026-09-22。新版 API 測試使用合成文件與模型函式替身，瀏覽器測試則使用真實後端及可控制回覆的本地 HTTP 模型服務。這些測試驗證軟體流程與資料完整性，不代表真實模型對公司規格的準確率。

| 驗證 | 結果 |
|---|---|
| Linux Python 3.12 完整自動測試 | 224 項通過、48 個子案例通過；1 項 Windows Job Object 測試依平台略過。引擎含 27 項原子抽取／相關性／證據與候選覆蓋測試 |
| Windows／macOS／Linux × Python 3.11／3.12 | [六組 CI 全部通過](https://github.com/pcpcchen-coder/LocalAIforSPECheck/actions/runs/35684205240)，來源 commit `da97e9a53983171e2de63da0367e59c0d591c4d2`。兩組 Windows 均為 225 項及 48 個子案例通過，包含 Windows Job Object 測試 |
| Windows Portable 鎖定依賴測試 | Python 3.13.15，225 項及 48 個子案例通過；接著建置並驗證實際 ZIP |
| 模型分離成品 | ZIP 不含 GGUF；檔案 manifest／SHA-256、缺模型提示、獨立模型匯入校驗、無效代理下沿用模型、含中文及空白的路徑、受限 PATH 與埠號衝突均通過 |
| Windows 真實 CPU 模型流程 | 舊版 48 V 合成條款得到有原文證據的 `match`；新版實際執行產品／標準抽取、來源保留、全庫初篩、逐項比對、風險 JSON 匯出及重啟保存。新版一筆結果為 `uncertain`、覆核優先度 `high`；沒有把待釐清結果冒充正確判定 |
| 前端與語法 | `node --check`、Python `compileall`、`git diff --check` 通過 |
| 新版 API 流程 | `tests/test_workflows.py` 11 項通過；涵蓋來源確認、項目修改／拆分與版本衝突、人工排除理由、不可變分析快照、風險覆核及完整歷程 |
| 300 份標準的合成負載 | 實際經 API 上傳 300 份標準，批次抽取並確認；以每頁 100 份讀取。全庫初篩得到 300 筆，不因相關性低而自動排除，輕量進度不帶原文與完整結果 |
| 項目來源追溯 | 不存在的引文遭拒；即使另一區塊有相同文字，也不能將既有項目的來源搬到另一區塊；拆分後未涵蓋原文保留待處理 |
| 覆蓋與排除 | 所選範圍完成與全庫完成分開；存在人工排除標準時，全庫覆蓋及未對應產品清單不能宣稱完整 |
| 中斷及續跑 | 比對取消不保存未完成結果；續跑不重複已保存項目；批次抽取已完成且已人工確認的文件不會被重抽或取消確認；重啟將活動工作標示中斷並沿檢查點繼續 |
| 三種報告與封存 | HTML／XLSX／JSON 保留風險與人工理由；後續重新覆核不改動既有封存報告，下載 SHA-256 與封存紀錄一致 |
| 舊版專案匯入 | 產品與標準原檔重用，新版須重新抽取確認；重複匯入不產生重複文件，舊版原文與確認狀態保持不變 |
| 真實 Chromium 操作 | 完整上傳／抽取／確認／初篩／人工排除／逐項比對／風險覆核／HTML 下載及封存／重新開啟通過；阻塞模型時顯示真實等待、輕量輪詢不重讀原文，斷線停止動畫且可恢復；五個主要頁面在 390px 無水平溢出，無非預期 console／page error |
| 舊版執行現況回歸 | `/classic` 的真實 Chromium 測試通過：首筆 0% 等待、計時與輕量輪詢、斷線恢復、完成後結果讀取失敗及重試、終止時計時凍結、取消、檔名轉義與窄版面均正常 |
| 錯誤保留與機密 | 模型工作拋出錯誤時保留「待釐清」結果，不宣稱未載明／符合；API 與報告不包含 API key 或模型拋出的敏感內容 |

Windows CI 期間修正兩項跨平台問題：模型下載工具在隔離模式 CLI 下主動將標準輸出／錯誤設為 UTF-8，避免中文路徑或訊息遇到系統編碼而失敗；300 份文件案例的抽取及初篩等待上限改為 180 秒、輪詢間隔 0.2 秒，以容納 Windows 檔案與 SQLite 寫入時間。文件數與全部行為斷言維持不變；這是流程正確性測試，不是處理速度基準。

成品驗證來源：[Windows Portable 建置與真模型驗證](https://github.com/pcpcchen-coder/LocalAIforSPECheck/actions/runs/35684205262)。[v0.3.0 Release](https://github.com/pcpcchen-coder/LocalAIforSPECheck/releases/tag/windows-portable-v0.3.0-da97e9a5) 已公開程式 ZIP、`SHA256SUMS.txt` 與 `portable-smoke-report.json`。ZIP 為 41,118,695 bytes（約 41 MB），SHA-256 為 `77f35f2a31eceba6815b80911842168d288f9c16e5a7396a577ec2b856fd43bb`；獨立模型為 1,556,390,368 bytes。Windows runner 並非乾淨的公司電腦；新版真模型案例的待釐清結果也顯示，入門 2B 模型尚不能據此視為已通過工程判斷驗收。

新版畫面（僅合成資料）：[差異與風險清單](screenshots/workspace-risks.png)、[證據與人工確認歷程](screenshots/workspace-review.png)。截圖已檢視，未使用公司文件。

重跑新版流程驗證（開發環境）：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_workflows.py tests/test_analysis_engine.py tests/test_analysis_exports.py -q
python -m pip install playwright
python -m playwright install chromium
python tests/workspace_browser_smoke.py
```

`SPEC_CHECK_BROWSER` 可指定既有 Chromium；`SPEC_CHECK_SCREENSHOTS=0` 可避免重寫合成案例截圖。舊版專案及執行現況測試仍可分別執行 `tests/browser_smoke.py`、`tests/progress_browser_smoke.py`，使用 `/classic` 入口。

**驗證界線：**300 份測試每份只有少量合成文字，模型函式回覆固定且迅速。這證明全庫清單、工作流程及持久化能處理此文件數量，不是數百份大型 PDF 的真實推論時間、記憶體用量或差異召回率。模型抽取／引用的軟體防線也不等於語意正確。正式文件、掃描頁、複雜表格及跨章條件仍依 [EVALUATION.md](EVALUATION.md) 與 [ACCEPTANCE.md](ACCEPTANCE.md) 驗收。

## 0.2.1 執行現況

日期：2026-09-21。

| 驗證 | 結果 |
|---|---|
| Linux Python 3.12 自動測試 | 150 項通過、38 個子案例通過；1 項 Windows Job Object 測試依平台略過 |
| 模型尚未回覆時的狀態查詢 | 刻意阻擋模型 HTTP 回覆，進度 API 仍可查詢；沒有讀取整份文件快照，也不會偽造模型回覆時間 |
| 條目與產品分段 | 第一條規範在 3 個產品視窗尚未全部完成前維持 0/1；每次實際回覆後才更新視窗與請求計數 |
| 資料一致性 | 結果列與完成數同一交易提交；取消、續跑、排隊取消與重啟的狀態正確；舊版資料自動補進度記錄 |
| 事件與機密 | 最近事件最多保留 30 筆；錯誤不洩漏提示、回應內容或 API key |
| 真實 Chromium 操作 | 等待、輕量輪詢、斷線及恢復、已完成計時凍結、停止等待、完成後完整結果載入失敗與恢復、檔名安全、390px 版面均通過，無非預期 console／page error |
| 前端語法 | `node --check spec_check/static/app.js` 通過 |

瀏覽器測試使用真實後端與可控制等待時間的合成 HTTP 模型；這驗證狀態呈現，不是模型準確率測試。操作畫面見 [執行中](screenshots/execution-progress.png)、[無法確認最新狀態](screenshots/execution-offline.png)。

Windows Portable 工作流程使用內附 Qwen3.5-2B Q6_K 做真實 CPU 推論，另外驗證輕量進度、已結束請求數、實際回覆時間與重啟後保留。**只有成品驗證通過才公開 Release**；同版附帶的 `portable-smoke-report.json` 是該 ZIP 的驗證結果。流程與方法見 [BUILD_PORTABLE.md](BUILD_PORTABLE.md)。

重跑針對性的瀏覽器驗證（開發環境）：

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/progress_browser_smoke.py
```

既有 Chromium 可透過 `SPEC_CHECK_BROWSER` 指定。此測試會產生上述合成案例截圖。

## 0.2.0 Windows Portable

日期：2026-09-21。成品來源 commit：`da9c1018d18c57204c47cbf347d1ca5f2db87bf1`。

| 驗證 | 結果 |
|---|---|
| Linux 本機 Python 3.12 | 143 項通過、38 個子案例通過；1 項 Windows Job Object 測試依平台略過 |
| GitHub Windows Python 3.13，依賴與發行包鎖定一致 | 144 項及 38 個子案例通過，含實際 Windows 程序清理 |
| Windows／macOS／Linux × Python 3.11／3.12 | 六組 CI 全部通過 |
| 完整 ZIP 內容 | ZIP SHA-256、每檔 manifest、內附 GGUF、Python 與原生套件驗證通過；不含使用者資料庫 |
| 免安裝執行環境 | 使用 ZIP 內的 Python；PATH 只保留 Windows 系統路徑，未使用系統 Python 或 LM Studio |
| 路徑及連線 | 含繁體中文與空白的解壓路徑、兩個預設連接埠被占用、自動改用空埠、無效代理設定均通過 |
| 真實本機模型推論 | 內附 Qwen3.5-2B Q6_K／llama.cpp CPU，合成的 48 V 條款得到 `match`，且引述逐字存在於產品原文；不是固定示範模式 |
| 覆核及報告 | 保存確認人、註記與事件版本；HTML／XLSX／JSON 三種匯出及歷次報告保存通過 |
| 啟停及重新開啟 | 實際呼叫 Start.bat／Stop.bat；兩個子服務停止、覆核與匯出紀錄重啟後保留 |
| 模型追溯與續跑 | 單元/API 測試確認指紋、模型身分、重啟埠號與金鑰更換、原始快照及續跑事件保存；換模型不得續跑舊批次 |

驗證來源：[完整 Portable 建置與成品測試](https://github.com/pcpcchen-coder/LocalAIforSPECheck/actions/runs/35572596953)、[六組跨平台測試](https://github.com/pcpcchen-coder/LocalAIforSPECheck/actions/runs/35572596940)。發佈包附有 `portable-smoke-report.json` 及 `SHA256SUMS.txt`，可從 [該版 Release](https://github.com/pcpcchen-coder/LocalAIforSPECheck/releases/tag/windows-portable-v0.2.0-da9c1018) 取得。

本次實際成品驗收修正了上游 API key 讀檔在繁體中文絕對路徑下失敗的問題，改用相對路徑，保留原本的中文路徑測試。另修正 Windows Job Object 測試的退出碼假設，以程序確實停止且未自然完成作為依據。

**驗證界線：**Windows CI runner 已預裝系統元件；上述結果不是每一台全新 Windows 10／11 企業電腦、所有舊 CPU 或公司端點政策的實機保證。成品已攜帶所需 application-local CRT，仍需在目標電腦核准並試用。單一合成條款確認的是推論與證據流程，沒有量測公司真實文件的差異召回率、誤判率或速度。正式採用仍依 [EVALUATION.md](EVALUATION.md) 與 [ACCEPTANCE.md](ACCEPTANCE.md) 驗收。

## 0.1.0 原始碼版的歷史驗證

以下保留前版開發紀錄；其中「尚待驗收」描述的是當時的原始碼版。Windows Portable 的新增實測以上節為準。

日期：2026-09-21。此紀錄驗證軟體行為，不宣稱模型在真實規格文件上的準確率。

### 已執行

| 驗證 | 結果 |
|---|---|
| `python -m pytest -q` | 82 項通過、38 個 unittest 子案例通過 |
| 真實本機 HTTP 測試服務 | `/v1/models`、chat completions、Bearer header、JSON Schema、引用驗證通過；服務是假模型，不是 LM Studio |
| Chromium 153 瀏覽器操作 | 示範 10 列、兩規範排序、狀態篩選、改判、確認歷史、HTML 下載、歷次匯出、上傳產品與兩份規範、逐份原文確認、儲存設定、重新載入均通過 |
| 前端基本品質 | 無 JavaScript page error；390px 視窗沒有頁面水平溢出；桌面畫面已檢視 |
| `node --check spec_check/static/app.js` | 通過 |
| `python -m compileall -q spec_check launcher.py scripts` | 通過 |
| 啟動器 | 自家健康識別、重複啟動、他人服務占用端口均驗證；不會終止他人服務 |
| Shell 入口 | `bash -n start_linux.sh start_macos.command` 通過 |

Python 3.12.14，Linux；FastAPI 0.141.1、Uvicorn 0.53.0、pypdf 6.10.0、python-docx 1.2.0、openpyxl 3.1.5、python-multipart 0.0.32、pytest 9.1.1、httpx 0.28.1。測試依賴有兩項 Starlette/httpx/anyio 棄用提醒，未影響通過結果。

測試類別：

- 引擎：31 項。完整產品視窗掃描、跨視窗矛盾、錯誤 JSON、截斷、偽造引用、無證據、逾時、取消、loopback 邊界、proxy/redirect 限制、計分及提示上下文。
- 解析與匯出：18 項。六種檔案、完整分段、位置、掃描 PDF 警告、表格上下文、公式快取、HTML／Excel 安全、長文字續列、秘密欄位排除、XLSX 錯誤 dimension。
- API／資料：14 項。合成示範、全列確認、改判／重開、409 版本衝突、快照隔離、取消／續跑、重啟恢復、來源／主機限制、匯出封存與雜湊、一致交易快照。
- 模型驗收工具：19 項。原始 AI 判定、混淆矩陣、缺列、關鍵錯判、零分母、重複鍵、引用有效率、示範警告，以及 ASCII／cp1252 管道的中文輸出。

### 開發期間修正

1. XLSX 檔案內的 worksheet dimension 可能過小、過大或偏移；原始唯讀迭代可能漏掉後方條文。改為先檢查實際 XML 儲存格座標，再重設兩份工作表的維度，並按實際內容檢查大小限制。
2. 模型回應 token 欄位的 HTML step 與預設 1800 不相容，可能使瀏覽器拒絕儲存。已改為整數步進，與 API 範圍一致。
3. 人工改判後畫面中的人工狀態標籤需即時刷新；已修正並加入瀏覽器驗證。
4. 讀取報告時使用同一個 SQLite 交易快照，避免同時有人覆核造成結果版本與歷史版本不一致。
5. GitHub Windows CI 發現驗收 CLI 在 cp1252 終端管道輸出中文會失敗；CLI 已明確使用 UTF-8，並新增說明、JSON 及錯誤訊息的跨編碼回歸測試。

### 重跑

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

可選的瀏覽器驗證不列入一般 runtime 依賴：

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
```

瀏覽器腳本在暫存目錄啟動真實後端並清理，使用合成資料；它會重新產生 `docs/screenshots/` 的示範截圖與 `examples/demo_report.html`。若已有 Chromium，可用 `SPEC_CHECK_BROWSER` 指定執行檔。

GitHub Actions 已提供 Windows／macOS／Linux × Python 3.11／3.12 測試設定。設定存在不等於雲端 CI 已成功；請以儲存庫 Actions 實際紀錄為準。

### 尚待使用者環境驗收

- 真正 LM Studio＋指定 GGUF 模型的推論、JSON 穩定性、速度及 RAM／VRAM 使用量。
- Bionic 版本是否實際提供相容 API。
- Windows／macOS 的雙擊啟動及一般同仁首次使用；本次只實測 Linux。
- 你們的正式規範、掃描文件、複雜表格、關鍵安全條文與人工標準答案。

以 [EVALUATION.md](EVALUATION.md) 建立評估集，並依 [ACCEPTANCE.md](ACCEPTANCE.md) 完成現場驗收。未經此步驟，不應把通過軟體測試視為模型已達工程簽核標準。
