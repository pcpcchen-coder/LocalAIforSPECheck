# Windows Portable 維護與重建

本頁給維護人員。一般同仁請直接使用 [Windows 免安裝版操作指南](WINDOWS_PORTABLE.md)，不需要執行下列指令。

## GitHub 自動建置與發佈

工作流程是 [portable-windows.yml](../.github/workflows/portable-windows.yml)。修改 `portable/`、`spec_check/`、相關測試或打包腳本並推送到 `main` 時會啟動；也可以在 GitHub 的 **Actions → Windows portable release → Run workflow** 選擇 `main`。

工作流程使用 Windows runner 的建置工具準備套件，依序執行：

1. 以 `portable/requirements.lock.txt` 約束版本，執行完整軟體測試。
2. 下載固定版本的 Python embedded、llama.cpp 及授權，逐一驗證 SHA-256。模型另行取得供成品測試，不放進 ZIP。
3. 將 Python wheels、CPU 引擎、application-local Microsoft CRT、程式、模型下載器與說明封裝為程式 ZIP；驗證成品中沒有 GGUF。
4. 解壓到含繁體中文與空白的路徑，限制 PATH；透過 `Download_model.bat --source` 匯入測試用 GGUF，再使用內附 Python 執行真實 CPU 模型檢查。驗證引文、覆核、匯出、重啟保存與批次啟停；實際通過項目以該次 smoke 報告為準。
5. 所有必要檢查成功後，建立 commit 專屬 Release 並設為 latest。

Release 包含 `LocalAIforSPECheck-Windows-x64.zip`、`SHA256SUMS.txt` 及 `portable-smoke-report.json`。失敗的成品不會公開成為 Release。不要將模型或完整執行環境提交到 Git 歷史，也不要把工作用 `data/` 放入發佈包。

發佈工作需要儲存庫的 `contents: write` 權限，由該工作專用的 `GITHUB_TOKEN` 提供；一般測試仍是唯讀。公開檔案採 commit 專屬 tag，已公開的同名版本不由重跑流程覆寫。

## 自行在 Windows 重建

建置機需要 x64 Python 3.13、網路，以及 Visual Studio 2022 的 x64 VC143 可散布 CRT 目錄。這些只存在於建置機，使用者電腦不需要安裝。建議直接使用上述 GitHub runner；不要從個人電腦的 System32 複製 DLL。

在原始碼根目錄執行：

```powershell
python -m pip install -r requirements-dev.txt -c portable/requirements.lock.txt
python -m pytest -q
python scripts/build_windows_portable.py --output-dir dist --cache-dir .portable-cache
python scripts/smoke_windows_portable.py --archive dist/LocalAIforSPECheck-Windows-x64.zip --model-source .portable-cache/Qwen3.5-2B-Q6_K.gguf --report dist/portable-smoke-report.json
```

建置程式只接受相符的 Windows x64 Python 主／次版本。**v0.3 預設成品不含模型，不必傳 `--skip-model`**；舊參數僅為相容，不能讓模型重新進入 ZIP。上面的 smoke 指令假設已將固定 GGUF 放到指定位置；也可改為其他實際路徑，檔案須符合 build-lock。一般使用者首次另外下載模型，此後更新程式沿用既有模型。

## 更新元件

- `portable/build-lock.json`：固定 Python、引擎、GGUF 與原授權的下載來源、版本及 SHA-256；模型採固定 Hugging Face revision。
- `portable/requirements.lock.txt`：包含 Windows 條件相依的完整 runtime 套件清單。建置時檢查 wheel 的相依宣告，禁止靜默漏裝套件。
- `portable/download_model.py` 與 `Download_model.bat`：以固定 URL、大小與 SHA-256 下載；已有正確模型則沿用，支援 `--source` 離線驗證複製，同名不符檔案不覆蓋。
- `models/README.md`：儲存庫內的獨立模型下載入口；不可改成隨 ZIP 重複附權重。
- `portable/launcher.py`：管理本資料夾的程序鎖、模型服務、網頁服務、停止握手及模型指紋。
- `scripts/build_windows_portable.py`：白名單收集程式，排除使用者資料，產生每檔 SHA-256 的 `MANIFEST.json` 與第三方聲明。
- `scripts/smoke_windows_portable.py`：對真正的 ZIP 成品驗收，不能用固定示範模式取代真實推論。

變更模型、引擎或依賴時，一併核對授權、實際引擎旗標、記憶體與 JSON 輸出支援，更新 [元件來源文件](PORTABLE_COMPONENTS.md)，再重新建置及驗收。模型品質另外依 [EVALUATION.md](EVALUATION.md) 評估；一個合成案例的整合成功不代表正式規範已達簽核品質。

Windows runner 已預裝許多系統元件。限制 PATH、內附依賴與 DLL 檢查可降低外部依賴風險，仍不等同所有企業映像、舊 CPU 或端點管制政策的實機驗收。

## v0.3 發佈驗收要點

- ZIP 中任何路徑均不得含 GGUF；但需含下載器、模型說明與可寫入且含說明的模型目錄。
- 無模型啟動應清楚提示取得方式，不能偷偷開始下載數 GB 檔案。
- 固定模型首次下載、已存在模型、檢查碼錯誤、離線匯入及自選模型不被覆蓋均需驗證。
- 重用 v0.2 的 `data` 及 `models` 後，舊 `/classic` 工作仍可讀；新流程需另外抽取確認。
- 成品煙霧測試與 300 份合成標準測試各自報告，不將合成資料或成功啟動當成真實文件準確率。
