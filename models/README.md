# 獨立模型下載

**v0.3 起，Windows Portable ZIP 不包含模型。模型下載一次即可重複使用，更新程式不必重新下載。**

## 建議操作

1. 下載並完整解壓縮 [Windows Portable 最新 ZIP](https://github.com/pcpcchen-coder/LocalAIforSPECheck/releases/latest)。
2. 雙擊根目錄的 **Download_model.bat**。視窗會顯示已下載大小、百分比、速度及 SHA-256 校驗結果。
3. 看到「模型已就緒」後，雙擊 **Start.bat**。

下載程式只取得公開模型，不讀取或傳送產品、標準文件。首次下載需要網路；完成後比對可在單機離線執行，不需要安裝 Python、LM Studio 或系統服務。

## 模型直連與固定版本

**[下載 Qwen3.5-2B-Q6_K.gguf（約 1.56 GB）](https://huggingface.co/lmstudio-community/Qwen3.5-2B-GGUF/resolve/bb84e11355a036e28f080c7793fa6d22b7c4e344/Qwen3.5-2B-Q6_K.gguf)**

| 項目 | 固定內容 |
|---|---|
| 模型 | Qwen3.5-2B |
| GGUF 量化 | Q6_K，lmstudio-community 提供 |
| 檔名 | `Qwen3.5-2B-Q6_K.gguf` |
| 大小 | 1,556,390,368 bytes |
| SHA-256 | `49e219c54fe4e936b078a994cdb10254a6ae24fc022834989d81240172a520f8` |
| GGUF revision | `bb84e11355a036e28f080c7793fa6d22b7c4e344` |
| 模型授權 | Apache-2.0；請參閱 [原模型授權](https://huggingface.co/Qwen/Qwen3.5-2B/blob/15852e8c16360a2fea060d615a32b45270f8a8fc/LICENSE) |

上述 URL 固定於模型儲存庫 revision，與本工具每次的程式 release 分開。機器可讀的相同設定保存在 [portable/build-lock.json](https://github.com/pcpcchen-coder/LocalAIforSPECheck/blob/main/portable/build-lock.json)。模型不提交至 Git，也不放入程式 ZIP。

## 手動下載、無外網電腦與舊版升級

- **下載程式無法連線**：可用瀏覽器從上方直連下載。在可連外的電腦下載後，將完整 GGUF 複製到 Portable 的 `models` 資料夾，維持上述檔名。再執行 `Download_model.bat`，它會直接校驗既有模型，通過後不會發出下載請求。下載未完成或校驗不符的檔案不能使用；請先移出並重新下載。
- **從 v0.2 升級**：先執行舊版 `Stop.bat`，備份舊資料夾，把新版解壓到另一個資料夾；將舊版 `data` 和 `models` 內容複製至新版對應目錄。程式、引擎與 Python 使用新版。詳細步驟見 [Portable 升級指南](../docs/WINDOWS_PORTABLE.md#更新備份與搬移)。
- **自訂模型**：把相容 llama.cpp 的 GGUF 放入 `models`，執行 `Choose_model.bat` 選擇。`Download_model.bat` 只處理上述入門模型，不改動已選的自訂模型，也不覆蓋其他模型。
- **既有同名模型校驗失敗**：程式會保留原檔並停止，不會覆蓋。請先將該檔移出或改名，確認後再重試。
- **取消或斷線**：正常取消或失敗會移除 `.part` 暫存；如果直接關閉視窗留下 `.part`，下次執行會從頭重新下載。暫存不會當成可用模型。請保留足夠空間存放模型（約 1.56 GB）。

若需要透過命令列將下載檔複製並校驗到 `models`，可在 Portable 根目錄執行：

```bat
Download_model.bat --source "D:\Downloads\Qwen3.5-2B-Q6_K.gguf"
```

這個 2B 模型供入門與流程測試。正式文件的判定品質需用人工標註案例驗收；模型大小不是比對正確率的保證。
