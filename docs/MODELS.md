# 本地模型推薦與連線設定

查證日期：2026-09-21。本文件區分「官方已確認能力」、「本工具設定」及「尚待實測的部署建議」。本專案沒有使用者的真實公司文件，因此**沒有宣稱已達成正式文件的準確率或差異召回率目標**。可攜版的組件固定版本見 [PORTABLE_COMPONENTS.md](PORTABLE_COMPONENTS.md)；建置與冒煙測試結果以各 GitHub Release 對應的 CI 紀錄為準。

## 1. 先選哪個模型

### Windows Portable：獨立下載入門模型

v0.3 程式 ZIP 內含 llama.cpp CPU 服務，**不含模型**。第一次以 `Download_model.bat` 取得 **Qwen3.5-2B Q6_K**（約 1.56 GB），或從 [獨立模型下載頁](../models/README.md) 手動取得並放入 `models`；已有同版本模型可沿用。它的用途是讓一般同仁先完成離線操作、格式相容與小型初篩；2B 模型的能力不宜直接視為能可靠理解複雜公差、例外、跨條引用與全部差異。即使成功產生 JSON，仍要閱讀產品證據並逐筆覆核。

[Qwen3.5-2B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-2B) · [GGUF 檔案來源](https://huggingface.co/lmstudio-community/Qwen3.5-2B-GGUF/tree/main)

可攜版將模型放在 `models`。較大的相容 GGUF 也可先在有網路且獲准的電腦下載，再複製進 `models`；停止程式後用 `Choose_model.bat` 選擇，重新執行 `Start.bat`。本版打包 CPU 執行服務，沒有內附 GPU 後端；大模型能載入不代表 CPU 處理時間適合大量條文。詳細操作見 [可攜版指南](WINDOWS_PORTABLE.md#換用較大的模型)。

### 正式文件的候選模型

建議先以 **Qwen3.5-9B** 完成安裝與一小組真實推論測試；若有 **32GB Apple Silicon Mac**，再試 **Qwen3.8-27B Q4_K_M** 作為主力候選。先用較小模型打通流程，較容易分辨連線、文件解析及模型能力問題。

Qwen3.8-27B 的官方模型卡與 LM Studio Community GGUF 已公開。它可處理文字與影像，官方亦公布文件理解評測；這些評測不能直接換算成本工具的逐條比對準確率。[Qwen3.8-27B 官方模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)

| 電腦記憶體 | 建議模型與量化 | 單一文字模型權重檔大小 | 建議用途 |
|---|---|---:|---|
| 16GB | Qwen3.5-9B Q4_K_M | 5.63GB | 入門與短條文比對；保留記憶體給系統和文件處理 |
| 24GB | Qwen3.5-9B Q6_K 或 Q8_0 | 7.36GB 或 9.53GB | 較少量化損失；優先維持可用記憶體餘裕 |
| 32GB | 先用 Qwen3.5-9B Q6_K；再評估 Qwen3.8-27B Q4_K_M | 7.36GB；16.8GB | 9B 驗證操作流程，27B 作為較複雜條件判讀的候選 |
| 64GB | Qwen3.8-27B Q6_K 或 Q8_0 | 22.4GB 或 29GB | 可減少量化；仍須驗證真實文件效果及處理時間 |

檔案大小來源：[Qwen3.5-9B GGUF 檔案清單](https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF/tree/main)、[Qwen3.8-27B GGUF 檔案清單](https://huggingface.co/lmstudio-community/Qwen3.8-27B-GGUF/tree/main)。下載時選一種量化即可，不需要把整個模型儲存庫的所有量化版本全部下載。

### 檔案大小不是實際 RAM／VRAM 需求

上表為可查證的下載檔案大小，**不是實測的執行記憶體或硬體最低需求**。執行時還需要模型引擎、KV cache、工作緩衝，以及作業系統和其他應用程式使用的記憶體。影像相關權重也可能另占空間。

硬體建議以單一模型、單一請求及短段落比對為前提。先將模型 context 設為 8K–16K，觀察記憶體壓力、交換空間及完成時間，再決定是否增加。這個 context 是本工具短文本工作負載的起始建議，不是模型官方最大能力的設定。

若 27B 造成明顯交換或每條處理太慢，改用 9B，不必勉強載入大模型。Windows 的系統 RAM 與獨立顯示卡 VRAM 也不能直接相加後當成同一個高速記憶體池；只能依實際 offload 和速度決定是否適用。上述選型尚待使用者的硬體與真實文件測試。

本工具目前將文件抽為文字區塊後送至模型，沒有直接傳送頁面影像。模型具有視覺能力，不代表本工具已自動支援掃描 PDF、圖片尺寸線或電路圖；這些內容須先處理並人工確認文字抽取結果。

## 2. LM Studio：原始碼版與自管模型的連線方式

**Portable 版不需要以下安裝步驟。** 下列方式適用於原始碼版，或經核准且由熟悉電腦的同仁自行管理模型服務的環境。

1. 安裝 LM Studio，並更新相應模型執行引擎。
2. 搜尋上表模型，選擇 `lmstudio-community/Qwen3.5-9B-GGUF` 或 `lmstudio-community/Qwen3.8-27B-GGUF`，下載所需量化。
3. 載入模型。先使用 8K–16K context，避免同時載入多個大模型。
4. 開啟 **Developer → Start server**。
5. 記下伺服器的實際 port。若使用 1234，工具的 Base URL 填入 `http://127.0.0.1:1234/v1`；`http://localhost:1234/v1` 也可。
6. 在工具設定中點選連線測試／讀取模型，從回傳清單選擇模型 ID，再儲存。
7. 按下節的「真實模型冒煙測試」完成一次 Local 模式比對。

LM Studio 官方確認 Developer 頁面可啟動 API server，且 port 可自行修改。1234 是官方文件使用的示例，不是不可修改的值。[啟動 API server](https://lmstudio.ai/docs/developer/core/server)、[Server Settings](https://lmstudio.ai/docs/developer/core/server/settings)

本工具只接受同一台電腦的 `localhost` 或數字 loopback 地址，可修改 port。此版本不支援連至其他同仁電腦、區網模型伺服器或雲端服務；不要把遠端地址填入此版本。工具後端呼叫模型 API，因此此使用方式不需要額外打開瀏覽器 CORS 或 Serve on Local Network。

### API 與認證

本工具以 `GET /v1/models` 讀取模型清單，並以 `POST /v1/chat/completions` 執行比對。Model 欄位需採伺服器回傳的 ID，不要依模型顯示名稱猜測。[LM Studio OpenAI-compatible API](https://lmstudio.ai/docs/developer/openai-compat)

LM Studio 預設不要求 API 認證。若管理員在 **Server Settings** 開啟 **Require Authentication**，請由 **Manage Tokens** 建立 token，填入本工具的 API Key 欄位；請求會以 Bearer token 傳送。無須購買或填入 OpenAI 雲端 API key。[LM Studio Authentication](https://lmstudio.ai/docs/developer/core/authentication)

### 本工具預設值

以下為原始碼版預設值。**Portable 每次啟動會自動設定所選本地模型連線**，通常使用 `127.0.0.1:1235`（占用時改用其他可用埠）、生成新的本機 API key、將 timeout 設為 600 秒及 temperature 設為 0；模型以 16K context 載入並關閉思考模式。不必手動輸入這些設定，亦不要將預設的 LM Studio 1234 位址覆蓋可攜版自動設定。

| 設定 | 預設 | 說明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:1234/v1` | 依 LM Studio 實際 port 修改；末尾保留 `/v1` |
| `model` | 尚未選擇 | 先讀取模型清單再選擇 |
| `api_key` | 空白 | 僅在伺服器要求認證時填入 |
| `structured_output` | `true` | 使用 JSON Schema 約束輸出格式 |
| `context_chars` | `6000` | 產品證據分窗的字元預算；不是模型 token context，也不是整份文件長度上限 |
| `max_tokens` | `1800` | 單次回覆上限；過短可能使結果未完成 |
| `timeout` | `180` 秒 | 單次模型請求的等待上限 |
| `temperature` | `0.1` | 本工具為一致性採用的起始值，並非 Qwen 官方建議值 |

工具依產品文字分窗逐次分析，不會因產品文件超過 6000 字元就把後面的內容丟掉。文件越多、條文越多、產品分窗越多，模型請求數和總時間會增加。應先用少量代表條文量測，再估計正式批次時間。

LM Studio 支援 `response_format` 的 JSON Schema。**結構化輸出只能約束格式，不能保證結論正確。**本工具還需檢查來源與引用；解析失敗或引用無效會留下待確認結果，不應當成符合。[Structured Output](https://lmstudio.ai/docs/developer/openai-compat/structured-output)

### 思考模式與回覆長度

Qwen3.8 預設開啟思考。若模型只產生大量推理而沒有最終 JSON，1800 tokens 可能不足。可在執行引擎提供的模型設定中先停用思考，或增加工具回覆上限與 timeout，再用同一小組案例測試。不要假設所有引擎均支援相同的 API extension，也不要把 `/no_think` 視為所有 Qwen 版本通用的控制方式。

Qwen3.8 官方 non-thinking 生成建議包含 `temperature=0.7`，本工具使用 0.1 是應用層選擇；低 temperature 不會消除幻覺，也不保證逐次完全相同。模型、引擎及生成設定均須隨驗證結果記錄。[Qwen3.8 生成設定](https://huggingface.co/Qwen/Qwen3.8-27B)

## 3. 三種測試不可混為一談

本節的「建立專案／Local 模式」為 `/classic` 的舊版冒煙測試。新版工作流程另依第 6 節驗證三個模型階段，不以舊比較成功代替新版抽取與初篩驗收。

| 測試 | 實際確認的內容 | 尚未確認的內容 |
|---|---|---|
| 示範模式 | 上傳後流程、結果頁、人工審核與匯出等操作 | 不呼叫模型，不測推論能力或 JSON 相容性 |
| 連線測試 | `/v1/models` 可連線並列出模型 | 不執行 Chat Completions，不測 JSON Schema 能力或比對準確率 |
| **Local 模式小型比對** | 實際呼叫已載入模型，檢查回覆、證據與輸出 | 少量案例通過仍不代表正式文件已達驗收 |

### 真實模型冒煙測試

1. 建立新專案，使用兩個簡單文字文件。
2. 產品文件寫入三項資料：`輸入電壓：200–240 V AC`、`額定輸出功率：10 kW`、`通訊介面：CAN 2.0B`。
3. 規範文件寫入三項要求：`輸入電壓須支援 200–240 V AC`、`額定輸出功率須不低於 12 kW`、`防護等級須達 IP65`。
4. 在抽取預覽確認三項內容均存在，再使用 **Local／本地模型模式** 啟動比對；不要選示範模式。
5. 人工檢查：電壓有對應證據；功率應指出 10 kW 小於 12 kW；防護等級應為產品文件未提供證據，不可因未記載就斷言硬體不符合。
6. 確認結果含有效引用、原文位置與可審核欄位，完成一次人工確認及匯出。

若只剩待確認結果，先讀取每列診斷，再查 LM Studio 的請求記錄。若是回覆格式被拒絕，可暫時關閉 `structured_output` 後重跑同一小組測試，確認是否為引擎的 Schema 相容性問題；程式仍會驗證回傳內容。不要把關閉格式約束視為已解決語意正確性。

## 4. Bionic 的使用方式與已確認範圍

**LM Studio Bionic 是獨立的新 app**，官方確認它能執行本地模型，也能與 LM Studio 並用。本次查到的明確 API server 啟動步驟屬於 LM Studio，不能直接把其 Developer 頁面或 1234 port 說成 Bionic 一定具備的功能。[Bionic 官方介紹](https://lmstudio.ai/docs/bionic)

Bionic 下載與使用本地模型的官方步驟為：

1. 開啟 **Settings → Local Models → Explore**。
2. 搜尋模型，依裝置適配提示選擇格式與量化版本並下載。
3. 到 **Local Models → Library** 查看已下載模型。
4. 在 session 的模型選擇器選擇 **Local** 模型。

以上步驟可在官方文件確認。[下載本地模型](https://lmstudio.ai/docs/bionic/models/download-local-models)、[選擇 Local／Cloud／Remote](https://lmstudio.ai/docs/bionic/models)

原始碼版的正式接法優先使用 LM Studio API；Portable 版使用內附模型服務。若使用者的 Bionic 版本確實提供同一台電腦上的 OpenAI-compatible HTTP server，可填入其實際 Base URL，完成讀取模型與上述 Local 冒煙測試後再使用；這屬於**待使用者環境驗證的相容連線**。若沒有提供 server，可讓 Bionic 閱讀匯出的報告，並由 LM Studio 提供本工具需要的推論服務。

2026-09-19 的 Bionic 1.1.5 changelog 已列出 Mac 上 Qwen3.8 的 Splash 引擎支援；這不能據此保證本工具的速度或第三方 API server 可用。[官方 changelog](https://lmstudio.ai/changelog)

## 5. 離線準備與正式驗收

Windows Portable 程式 ZIP 包含 Python 執行環境、套件、CPU 模型服務及模型下載器；模型分開取得。程式與模型準備好後，日常啟動、抽取、初篩、比對、覆核與匯出均在單機執行。更新程式 ZIP 時保留 `models`，不必重新下載 GGUF。此方式與自行安裝 LM Studio 的離線準備不同。

LM Studio 官方說明：下載模型及執行引擎後，本地推論與 API server 可離線運作；模型搜尋、下載與更新仍需要網路。部署時也要事先安裝本工具所需的 Python 依賴。[Offline Operation](https://lmstudio.ai/docs/app/offline)

使用真實公司文件前，建議以 3–5 組代表文件建立至少 100–200 條人工標準答案，涵蓋單位換算、上下限、額定／最大值、條件與例外、規範版本、跨頁表格，以及未提供證據。以下是本專案建議的試行門檻，**不是本版本已達成的測試成績**：

- 分別量測條文抽取召回率、差異召回率、錯誤判為符合的數量、引用有效率。
- 差異召回率以至少 95% 作為試行目標；樣本中的強制或安全要求不可錯判為符合。
- 每個結論均須有有效來源，或清楚標示證據不足。
- 所有結果需經人工確認；保存改判、意見及確認時間。
- 模型、量化、prompt、執行引擎或解析器改變後，用相同案例重新驗收。

文件符合度參考分數只協助排序。仍須檢查關鍵不符合、未知、未確認與不適用條件；不應以高分代替逐條確認或認證。v0.3 保留未解析原文並支援人工拆分獨立項目，仍不能保證模型已發現複合條件中的所有差異。舊版 `/classic` 的文件符合度分數不代表新版相關性或風險。

## 6. v0.3 新流程對模型的要求

同一個模型依序執行獨立項目抽取、文件相關性／適用性初篩與逐項比對，需能穩定輸出受約束 JSON。抽取會要求參數、值、單位、條件、例外、測試方法及精確原文引用；不存在的引文會被拒絕，未涵蓋文字保留待處理。

請分別測試三個階段，不能只看 `/models` 成功或舊版簡單比較成功：

1. 以同段兩個規格要求驗證能否拆成獨立項目並保留條件。
2. 以「相關但適用條件未知」與「相似名稱但範圍不同」測試初篩，所有文件都應留給人工選取。
3. 以額定／峰值、單位換算、例外條件、證據缺少測試逐項判讀，並核對來源。

關鍵性未知會增加全量查核；較弱模型若留下大量未知或無效輸出，即使單次速度快，也未必能減少整體工時。用實際文件量測抽取完整性、相關性召回、差異漏判及覆核時間，再決定部署模型。

新的項目抽取提示與 JSON 可能比舊版回覆長；若回覆被截斷，應檢查 `max_tokens`、context、單次 timeout 及模型能力，不能直接略掉未完成項目。改變模型或設定後，以相同人工基準重新驗收。
