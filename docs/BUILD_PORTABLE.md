# Windows Portable 維護與重建

本頁給維護人員。一般同仁請直接使用 [Windows 免安裝版操作指南](WINDOWS_PORTABLE.md)，不需要執行下列指令。

## GitHub 自動建置與發佈

工作流程是 [portable-windows.yml](../.github/workflows/portable-windows.yml)。修改 `portable/`、`spec_check/`、相關測試或打包腳本並推送到 `main` 時會啟動；也可以在 GitHub 的 **Actions → Windows portable release → Run workflow** 選擇 `main`。

工作流程使用 Windows runner 的建置工具準備套件，依序執行：

1. 以 `portable/requirements.lock.txt` 約束版本，執行完整軟體測試。
2. 下載固定版本的 Python embedded、llama.cpp、模型及授權，逐一驗證 SHA-256。
3. 將 Python wheels、CPU 引擎、application-local Microsoft CRT、模型、程式與說明封裝為單一 ZIP。
4. 解壓到含繁體中文與空白的路徑，限制 PATH，使用內附 Python 執行真實 CPU 模型比對，驗證原文引述、覆核、三種匯出、重啟保存與批次啟停。
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
python scripts/smoke_windows_portable.py --archive dist/LocalAIforSPECheck-Windows-x64.zip --report dist/portable-smoke-report.json
```

建置程式只接受相符的 Windows x64 Python 主／次版本。`--skip-model` 僅供開發，會產生檔名包含 `-no-model` 的 ZIP，不能當成使用者要求的完整離線包。

## 更新元件

- `portable/build-lock.json`：固定 Python、引擎、GGUF 與原授權的下載來源、版本及 SHA-256；模型採固定 Hugging Face revision。
- `portable/requirements.lock.txt`：包含 Windows 條件相依的完整 runtime 套件清單。建置時檢查 wheel 的相依宣告，禁止靜默漏裝套件。
- `portable/launcher.py`：管理本資料夾的程序鎖、模型服務、網頁服務、停止握手及模型指紋。
- `scripts/build_windows_portable.py`：白名單收集程式，排除使用者資料，產生每檔 SHA-256 的 `MANIFEST.json` 與第三方聲明。
- `scripts/smoke_windows_portable.py`：對真正的 ZIP 成品驗收，不能用固定示範模式取代真實推論。

變更模型、引擎或依賴時，一併核對授權、實際引擎旗標、記憶體與 JSON 輸出支援，更新 [元件來源文件](PORTABLE_COMPONENTS.md)，再重新建置及驗收。模型品質另外依 [EVALUATION.md](EVALUATION.md) 評估；一個合成案例的整合成功不代表正式規範已達簽核品質。

Windows runner 已預裝許多系統元件。限制 PATH、內附依賴與 DLL 檢查可降低外部依賴風險，仍不等同所有企業映像、舊 CPU 或端點管制政策的實機驗收。
