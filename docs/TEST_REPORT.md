# 0.1.0 開發驗證紀錄

日期：2026-09-21。此紀錄驗證軟體行為，不宣稱模型在真實規格文件上的準確率。

## 已執行

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

## 開發期間修正

1. XLSX 檔案內的 worksheet dimension 可能過小、過大或偏移；原始唯讀迭代可能漏掉後方條文。改為先檢查實際 XML 儲存格座標，再重設兩份工作表的維度，並按實際內容檢查大小限制。
2. 模型回應 token 欄位的 HTML step 與預設 1800 不相容，可能使瀏覽器拒絕儲存。已改為整數步進，與 API 範圍一致。
3. 人工改判後畫面中的人工狀態標籤需即時刷新；已修正並加入瀏覽器驗證。
4. 讀取報告時使用同一個 SQLite 交易快照，避免同時有人覆核造成結果版本與歷史版本不一致。
5. GitHub Windows CI 發現驗收 CLI 在 cp1252 終端管道輸出中文會失敗；CLI 已明確使用 UTF-8，並新增說明、JSON 及錯誤訊息的跨編碼回歸測試。

## 重跑

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

## 尚待使用者環境驗收

- 真正 LM Studio＋指定 GGUF 模型的推論、JSON 穩定性、速度及 RAM／VRAM 使用量。
- Bionic 版本是否實際提供相容 API。
- Windows／macOS 的雙擊啟動及一般同仁首次使用；本次只實測 Linux。
- 你們的正式規範、掃描文件、複雜表格、關鍵安全條文與人工標準答案。

以 [EVALUATION.md](EVALUATION.md) 建立評估集，並依 [ACCEPTANCE.md](ACCEPTANCE.md) 完成現場驗收。未經此步驟，不應把通過軟體測試視為模型已達工程簽核標準。
