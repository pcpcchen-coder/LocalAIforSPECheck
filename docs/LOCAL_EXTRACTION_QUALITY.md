# 小模型萃取：Prompt、保護機制與驗收

適用：v0.3.3 原子萃取流程；引擎版本 2.1。產品與本機規範萃取共用。

## 結論與本次檢查

**舊 Prompt 不能單憑文字設計就宣稱在 E4B、2B 或 Bonsai 等模型上有足夠品質。新版也需要實測。** 本次確認程式缺口並補上防護，沒有在使用者 GPU 上執行 E4B／12B／Bonsai 準確率評測。

| 舊版缺口 | 本次修改 | 仍須人工確認 |
|---|---|---|
| 多條規則濃縮在短段落，沒有示範 | 五步整理流程、完整欄位定義、三組正反例 | 模型是否遵守，不能由 Prompt 長度推論 |
| 關閉 structured output 時缺乏完整文字格式說明 | Prompt 明列全部 12 欄、字串型別、枚舉與 JSON 根結構 | 後端是否支援 JSON schema；不支援可關閉後測試 |
| 引文存在，但欄位可以捏造／換算／改變等號 | value/unit/operator/conditions/exceptions/test_method 逐欄驗證為自身 quote 的連續原文；不符則清空該欄、整項 unresolved | 字串存在不代表參數對應正確 |
| 引用整段掩蓋漏掉另一個數值 | 檢查原文數值／編號是否出現在有引文支持的欄位中；缺少則新增保留整段的 unresolved | 相同數值的不同要求、純文字要求仍可能漏掉 |
| 條件、例外被省略 | 偵測常見條件／例外詞而對應欄位為空時轉 unresolved | 啟發式不是完整語法分析，可能多報或漏報 |
| 非數值功能被當背景 | 擴充「須、禁止、支援、required、prohibited」等背景防漏條件 | 模型把要求寫成其他形式仍可能漏判 |
| 缺乏本機原子萃取探針 | 16 個合成案例、22 個預期項目；相同案例可重複測不同模型 | 合成資料不是獨立人工真值，也不是正式驗收集 |

本次**不更動** ChatGPT 外部匯入格式或既有歷史資料，也不自動重新萃取已確認文件。新增保護僅用於本地 `extract_block`；外部成果仍走原本的匯入驗證與人工確認流程。

## 實際使用的完整 Prompt

唯一來源：[LOCAL_ATOMIC_EXTRACTION_PROMPT.md](../spec_check/prompts/LOCAL_ATOMIC_EXTRACTION_PROMPT.md)。程式啟動時讀取這個檔案，Portable 也包含相同檔案；不是另外提供一份未接入系統的範本。

系統訊息使用整份 Prompt，使用者訊息由系統填入：

```json
{"data_only":true,"role":"standard","source":{"block_id":"B00001","location":"第1頁","text":"待萃取原文"}}
```

產品文件用 `role=product`。Prompt 本身不是可匯入的 `.standard.json`，也不是外部完整文件萃取包。一般同仁不需手動貼 Prompt：更新程式後，重新執行需要評估的文件萃取即可。

萃取階段的 operator 保留「大於」「不超過」或原符號；**不自行補 `=`、不做單位換算**。單位等價判斷留到後續比對。分散的條件若要放在同一字串欄，複製涵蓋條件的連續片段；不能自行串接數段原文。不可靠的表格、跨頁斷句及缺少必要引用一律待確認。

## 小模型操作方式

1. 在 LM Studio 載入選定模型並啟動本機 API，確認平台連線正常。Bionic 須確實提供相容 API。
2. 平台仍逐來源區塊處理。一般檔案匯入的區塊上限為 1,400 字元，這是**文字切段**；模型再將區塊拆為原子要求。切段不保證依完整條款邊界，跨頁上下文缺失須處理。
3. 可從 runtime context 8192 tokens、單一請求、平台 max_tokens 4096 開始測試。這是試驗起點，不是保證容納任何區塊或思考輸出；平台的 `context_chars` 是字元數，不等同 runtime token 上限，也不控制原文匯入切段。
4. 若輸出被截斷、逾時或 JSON 錯誤，原文會保留為 unresolved；依實際情況提高輸出／上下文上限或換模型。沒有無限自動重試，也不把截斷答案當成成功拆完。
5. 先核對待確認項目，再抽查已解析項目。新版較保守，待確認數可能增加；這不是新發現同等數量的不合格產品。
6. 重跑會建立新的抽取版本、清除該文件的確認狀態；既有分析快照不會自動變更。先保存報告，再重新萃取與確認，之後建立新分析。

平台 API 會傳送 temperature 與 max_tokens；runtime UI 的同名預設可能被覆蓋。本工具尚未新增各模型專用 thinking/top_p/top_k 設定，請記錄 runtime 設定並保持比較一致。若 runtime 將思考文字混入最終 JSON，應先調整 runtime 的回應格式；系統不會隨意刪除一段文字後假裝驗證成功。

## 執行相同案例比較模型

由負責選型／驗收的工程同仁操作即可，一般使用者繼續使用網頁介面。探針只連本機 loopback，直接呼叫與平台相同的萃取函式；不更改資料庫、不上傳文件。

在 **v0.3.3 Portable 解壓縮資料夾**開啟 PowerShell（使用內附 Python，不需安裝）：

```powershell
.\runtime\python.exe -m spec_check.extraction_benchmark --model "實際模型ID" --label "E4B-it-Q6_K.gguf；填入runtime版本與context設定" --repeat 3 --output .\reports\e4b-q6-run1.json
```

先在 LM Studio 的模型服務確認實際 ID，不要直接假設下載檔名等於 API ID。換成 12B／Bonsai 後更換 `--model`、`--label` 和輸出檔名再執行。已有報告不會被覆蓋。

原始碼版本：

```powershell
.venv\Scripts\python.exe scripts\evaluate_extraction.py --model "實際模型ID" --label "GGUF檔名與runtime版本" --repeat 3 --output reports\model-run1.json
```

macOS/Linux 使用 `.venv/bin/python`。可用參數：`--base-url http://127.0.0.1:1234/v1`、`--max-tokens 4096`、`--temperature 0.1`、`--timeout 180`、`--no-structured-output`、`--cases 自訂案例.json`。CLI 不提供需認證服務的 API key 選項；若本機服務要求認證，先用平台介面完成測試，勿將 key 放在 URL。

`temperature=0.1` 是固定比較條件，不宣稱是所有模型最佳參數。需要比較其他 sampling 設定時分開跑並保存報告。執行中顯示目前輪次、案例與等待狀態；若中斷，這次完整報告尚未產生，請重新執行。

### 報告怎麼看

| 欄位 | 意義 |
|---|---|
| `exact_fixture_recall` | 預期項目中，指定欄位精確命中的比例；含預期 unresolved |
| `resolved_fixture_recall` | 僅計預期可解析要求；模型全部回 unresolved 不能得到高分 |
| `missing_expected_indices` | 未命中預期項目的 0 起始索引；缺項仍留在分母 |
| `extra_prediction_indices` | 未配對輸出；須看是否正確額外項目、重複、誤抽或防護提示 |
| `records` | 每次原文、預期欄位、萃取結果、警告、配對及時間 |
| `extraction_prompt_sha256` / `fixture_sha256` | 固定 Prompt 與案例版本以重現比較 |
| `model_label` | 操作者自行填寫的量化與 runtime 資訊，不是程式查證的模型身分 |

每個輸出最多配對一個預期項目，避免「一個複合項目」同時滿足多條要求。欄位同義改寫可能被保守算成未命中；原子切法不同也需要人工檢查。不能只看一個百分比，還要核對 **AC/DC、參數對應、表格欄位、禁止條件、錯誤額外項目及原文遺漏**。`coverage=complete` 仍然只代表文字引用保留，不是語意品質通過。

合成案例是開發者編寫的回歸探針，部分模式已出現在 Prompt 示範中，**不能當成未見資料的獨立測試**。不要用模型自己的輸出自動修改答案來提高命中率。CLI 成功結束只表示報告已保存，不代表驗收通過。

## 正式採用的試行門檻

由兩位工程／品質同仁先依原始文件標註至少 100–200 條獨立要求，包含相同數值不同物理量、AC/DC、嚴格／含等號、工作／儲存、OR 選項、否定、表格註腳、跨章引用及無數值功能。另保留未用來修改 Prompt 的文件做測試。

建議先約定：原子要求召回率 ≥95%、關鍵條件遺漏或改寫為 0、數值／單位／運算關係錯誤為 0、無依據新增要求為 0、引用有效率 100%；重跑三次並記錄人工修訂時間。這是**待達成的試行門檻**，不是本次已測得的成績；樣本零錯誤也不是全域零風險。

抽取驗收後，還須讓同一模型用相同的獨立人工比對答案，分別測「人工整理項目」「ChatGPT 規範＋本機產品」「全本機萃取」三條路徑。萃取好不保證相關性初篩、條件適用及最終差異判讀都好。現有 `/classic` 的 `evaluate_gold.py` 仍是舊版區塊比對評估，不能把本頁的原子項目 ID 直接代入。

## 本次工程驗證與限制

新增故障注入回歸測試涵蓋：整段引用但漏數值、捏造六種欄位、漏條件／例外、非數值要求誤標背景、關閉 schema 時多餘欄位、未解析不冒充符合、評測一對一配對、報告保留與禁止遠端模型 URL，以及 Portable 內含 Prompt／探針案例。

這些是程式契約測試，不能當作 E4B／12B／Bonsai 的實測成績。Windows 發佈仍須通過既有 CI、Portable 組裝及真模型流程 smoke 後才提供新 ZIP；smoke 使用固定入門模型，也不是上述模型的品質驗收。

2026-09-23 本機 Python 3.12 回歸結果：320 tests passed、48 subtests passed；1 項 Windows Job Objects 測試因作業系統不同跳過。語法編譯及評測 CLI 說明檢查通過；沒有連接使用者的本機模型。
