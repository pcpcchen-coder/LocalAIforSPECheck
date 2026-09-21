# 開發驗證紀錄

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
