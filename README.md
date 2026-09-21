# LocalAIforSPECheck｜本機產品規格比對與人工覆核

把一份產品規格，與多份技術規範逐段比對；查看各規範的文件符合度、差異、產品原文證據，並記錄同仁的覆核結果。文件與運算資料留在本機；模型由 LM Studio 或提供相容 API 的本機服務執行。

適合用於規格初篩、供應商文件比較、設計差異清單與覆核交接。**AI 的輸出是待確認的比對建議，不能取代工程師簽核，也不保證找出所有原子條件的差異。**

![多份規範的比較、差異與覆核介面](docs/screenshots/comparison.png)

[人工覆核畫面](docs/screenshots/review.png) · [合成示範 HTML 報告（下載後以瀏覽器開啟）](examples/demo_report.html) · [開發驗證紀錄](docs/TEST_REPORT.md)

## 一般同仁的日常操作

請先由 IT 完成一次安裝。之後：

1. 開啟 LM Studio，載入 IT 選定的模型並啟動本機 API；Bionic 僅在你的版本提供相容本機 API 時使用。
2. Windows 雙擊 `start_windows.bat`；macOS 雙擊 `start_macos.command`。Linux 執行 `./start_linux.sh`。
3. 瀏覽器會開啟 `http://127.0.0.1:8765`；啟動視窗請保持開啟。
4. 首次先使用畫面的示範功能，熟悉比對、覆核與匯出。示範不需要模型。
5. 建立專案，上傳一份產品文件與一份以上規範，預覽擷取文字並逐份確認。
6. 設定模型連線，測試成功後執行比對；依差異逐筆確認、更正或重新開啟覆核。
7. 下載 HTML／Excel／JSON 報告。詳細步驟見 [同仁操作指南](docs/USER_GUIDE.md)。

**每次啟動只在本機提供介面。** 重複雙擊會開啟既有服務；若同一連接埠被其他程式占用，會顯示訊息，不會關閉其他程式。

## 功能範圍

| 功能 | 行為 |
|---|---|
| 文件輸入 | 有文字層的 PDF、DOCX、XLSX、CSV、TXT／Markdown；保留原始文件與擷取位置 |
| 解析確認 | 顯示文字區塊與解析提醒；使用者確認後才能開始正式比對 |
| 多規範排序 | 每份規範顯示「文件符合度參考分數」、各類狀態及覆核進度 |
| 全文比對 | 每個規範文字區塊會檢查全部產品文字視窗，不只取檢索前幾筆 |
| 差異明細 | 原要求、狀態、差異清單、說明、原文引述、文件位置、模型自評信心（非經校準準確率）及警告 |
| 人工覆核 | 確認、更正、重新開啟；保留覆核者、自填意見、時間、版本與歷次事件 |
| 進度保留 | 已完成列持久化；取消／中斷後可續跑；新比對另建一份結果 |
| 匯出 | HTML 可讀報告、XLSX 工作簿、JSON 完整紀錄；包含尚未覆核項目，保留歷次輸出及 SHA-256 |
| 本機資料 | SQLite＋原始文件；不使用 CDN、遙測或雲端模型 API |

功能與格式的精確限制見 [操作指南](docs/USER_GUIDE.md)；模型與 LM Studio／Bionic 設定見 [模型選擇與部署](docs/MODELS.md)。

## IT：第一次安裝

需要 Python **3.11 以上**、可用瀏覽器、足夠磁碟空間，以及能執行所選模型的電腦。模型主機必須在同一部電腦；此版本只接受 loopback API 位址。

可從 GitHub 下載 ZIP 並完整解壓縮，或使用 Git：

```bash
git clone https://github.com/pcpcchen-coder/LocalAIforSPECheck.git
cd LocalAIforSPECheck
```

Windows（命令提示字元）：

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launcher.py
```

macOS／Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
chmod +x start_macos.command start_linux.sh
.venv/bin/python launcher.py
```

若系統有多個 Python，先以 `python --version` 或 `python3 --version` 確認實際版本。macOS 的系統 Python 不一定符合要求。首次下載 Python 套件、模型需要網路；完成安裝且模型已下載後，本機比對不需要對外連線。公司禁止安裝時，應由 IT 在核准環境部署，或提供已核准的 Python 執行環境；目前並非免安裝的單一 EXE。

LM Studio 預設連線欄位為 `http://127.0.0.1:1234/v1`；請依實際 Server 畫面核對。**Bionic 不預設端口**，必須使用該工具實際顯示且支援 OpenAI 相容介面的 loopback URL。模型 ID 必須與 `/v1/models` 回傳一致。請先測試連線，再存設定並執行。

### 手動啟動與特殊端口

在已啟用虛擬環境的終端機執行：

```bash
python launcher.py
python launcher.py --no-browser
python launcher.py --port 8766
```

也可由 IT 直接執行：

```bash
python -m uvicorn spec_check.app:app --host 127.0.0.1 --port 8765 --workers 1
```

請維持 `127.0.0.1` 與單一 worker；不要使用 `--reload`，也不要把此版本公開到區域網路或網際網路。

## 分數與狀態怎麼解讀

| 狀態 | 含義 |
|---|---|
| 符合 `match` | AI 依產品文字與有效引述判定符合；仍需人工確認 |
| 部分符合 `partial` | 部分條件符合，仍有差異或限制 |
| 不符合 `mismatch` | 產品文件有可引述的內容，與要求存在衝突 |
| 文件未載明 `missing` | 所有產品視窗均已檢查，仍找不到支援資訊；不代表實際產品必定不合規 |
| 待確認 `uncertain` | 證據不足、輸出無法驗證、連線失敗或其他無法可靠判定情況 |

文件符合度參考分數：`100 ×（符合數＋0.5 × 部分符合數）／規範文字區塊總數`。所有狀態均計入分母；確認／更正後採人工最終狀態計分。它是可解釋的工作排序指標，**不是認證結論，也不是向量語意相似度**。不同規範的篇幅、切段方式、條件數與重要程度會影響分數，不能僅憑最高分選定標準。未完成或中斷的比對，只能視為暫時結果。

## 已知限制

- **沒有內建 OCR。** 掃描 PDF、截圖、圖片表格、DOCX 圖片及 XLSX 圖片，應先由核准工具轉成文字並人工校核。讀不到文字時不能當成沒有要求。
- 文件中圖面、公差圖、跨頁表格、頁首頁尾、合併儲存格、註脚與公式可能失去原本關係；擷取預覽是必要步驟。
- 一個區塊可能含多個要求。完整處理所有區塊不等於保證每個原子條件都判對；重要規範應再人工逐條拆解及抽核。
- 本工具會驗證引述文字是否存在於產品文件；「引述存在」並不證明模型推理正確。單位換算、條件例外、測試方法與跨段引用仍可能判錯。
- 覆核者姓名是自填，沒有帳號驗證或數位簽章；事件歷史用於追溯，不是不可竄改的稽核憑證。
- 大文件採逐區塊、逐產品視窗循序呼叫，耗時可能很長。此版本無多人伺服器權限與排程叢集。
- 合成示範與自動測試只能驗證系統流程；**沒有以你的真實文件及本地模型完成驗收前，不能聲稱比對精準度或差異召回率。**

## 備份、還原與更新

預設工作資料在專案的 `data/`。可由環境變數 `SPEC_CHECK_DATA` 指向其他本機資料夾，供 IT 管理。原始規格、擷取文字、比對結果、設定與覆核歷史均應視為敏感資料。

1. 等待本次比對結束，或按取消並等待狀態更新。
2. 在啟動視窗按 `Ctrl+C` 停止程式。
3. **備份整個 `data/`（或指定的資料目錄），包括 SQLite、uploads 與 reports。** 不要在比對中只複製單一資料庫檔。
4. 還原時保持程式關閉，將整個備份還原到原資料目錄後啟動。
5. 更新程式前先備份。不要把含規格資料的資料夾、匯出報告、模型金鑰或備份提交到 GitHub。

HTML／XLSX 是交接報告；JSON 保留較完整的結果快照，但本版沒有 JSON 一鍵匯回功能。完整續作仍以整個工作資料夾備份為準。

## 開發、測試與驗收

在虛擬環境中：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

自動測試可在沒有本地模型的環境執行；本地模型、真實文件及一般同仁的實際操作，另依 [驗收清單](docs/ACCEPTANCE.md) 完成。合成文件位於 [examples](examples/)，不可作為工程標準或產品資料引用。

| 文件 | 用途 |
|---|---|
| [USER_GUIDE.md](docs/USER_GUIDE.md) | 一般同仁的完整操作、覆核及排錯 |
| [MODELS.md](docs/MODELS.md) | 模型推薦、硬體取捨、LM Studio／Bionic 設定 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架構、資料流、API、計分與延續處理 |
| [ACCEPTANCE.md](docs/ACCEPTANCE.md) | 可執行驗收、真實模型評估及剩餘風險 |
| [EVALUATION.md](docs/EVALUATION.md) | 人工標準答案格式及 `scripts/evaluate_gold.py` 評估工具 |
| [SECURITY.md](docs/SECURITY.md) | 本機邊界、機密資料、覆核身份與備份 |
| [CONTRACT.md](CONTRACT.md) | 開發者使用的資料格式及模組介面 |
