# Windows Portable 元件、來源與授權

查證日期：2026-09-21。本文件記錄 Windows x64 Portable 使用的固定上游元件，供重建、來源追溯與內部審查。最終 ZIP 的實際內容與雜湊，以該次發行隨附的 manifest 為準。**v0.3 起模型獨立下載，下面的模型記錄是相容入門模型，並非 ZIP 內含檔案。**

## 固定版本

| 元件 | 固定版本／來源 | 原始檔案大小 |
| --- | --- | ---: |
| CPython Windows embedded x64 | Python 3.13.15 | 11,009,825 bytes |
| llama.cpp Windows CPU x64 | `b10964`，對應 stable `v0.4.1` | 18,427,629 bytes |
| 獨立下載文字模型 | LM Studio Community `Qwen3.5-2B-Q6_K.gguf` | 1,556,390,368 bytes |

llama.cpp 的原始碼 commit 為 `b29c606e28a01b1bc8c1351026a0fa6e616bf6c4`；量化模型儲存庫 revision 為 `bb84e11355a036e28f080c7793fa6d22b7c4e344`。

模型來源是 [LM Studio Community 量化版本](https://huggingface.co/lmstudio-community/Qwen3.5-2B-GGUF/tree/bb84e11355a036e28f080c7793fa6d22b7c4e344)，原始模型由 [Qwen](https://huggingface.co/Qwen/Qwen3.5-2B) 發布。Portable 沒有重包 LM Studio 桌面應用程式，也不需要使用者安裝 LM Studio。此 GGUF 僅用於文字推論；未附影像投影模型，不代表包含 OCR。

### 下載與 SHA-256

| 元件 | 固定下載位置 | SHA-256 |
| --- | --- | --- |
| Python embedded | [python-3.13.15-embed-amd64.zip](https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip) | `d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf` |
| llama.cpp CPU | [llama-b10964-bin-win-cpu-x64.zip](https://github.com/ggml-org/llama.cpp/releases/download/b10964/llama-b10964-bin-win-cpu-x64.zip) | `917f39c076402c421224824607397af20f53625a60defc20e8dd22446bf4c5d7` |
| Qwen3.5-2B Q6_K | [Qwen3.5-2B-Q6_K.gguf](https://huggingface.co/lmstudio-community/Qwen3.5-2B-GGUF/resolve/bb84e11355a036e28f080c7793fa6d22b7c4e344/Qwen3.5-2B-Q6_K.gguf) | `49e219c54fe4e936b078a994cdb10254a6ae24fc022834989d81240172a520f8` |

Python 與 llama.cpp ZIP 的 SHA-256 已實際下載核對；模型固定 SHA-256 與大小記錄於 build-lock；使用者下載器及成品測試匯入時，都會對完整模型再次核對。llama.cpp CPU release asset ID 為 `563672535`。[Python 發行資訊](https://www.python.org/downloads/release/python-31315/)、[llama.cpp 固定版本](https://github.com/ggml-org/llama.cpp/releases/tag/b10964)

程式 ZIP 不含模型；使用者從 [獨立模型入口](../models/README.md) 取得 GGUF，更新程式時沿用原檔。模型不提交至 Git 歷史，也不重複放入 Release ZIP。GitHub Releases 的單一 asset 必須小於 2 GiB，建置流程仍檢查最終 ZIP 大小。[GitHub 官方限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)

## 免安裝所需 DLL

Python embedded 本身是供應用程式攜帶的私有執行環境，第三方 Python 套件應在建置階段一起準備；使用者端不需要執行 pip。[Python embedded 官方文件](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)

實際檢查上述 ZIP 與 PE imports：

- Python ZIP 包含 `vcruntime140.dll`、`vcruntime140_1.dll`，沒有 `MSVCP140.dll`。
- llama.cpp CPU ZIP 包含 `libomp.dll`、`llama-server-impl.dll`、`llama-common.dll`、`llama.dll`、`ggml*.dll`、`mtmd.dll` 等，但沒有 Microsoft Visual C++ CRT DLL。
- llama.cpp executable／DLL 仍引用 `MSVCP140.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll`。只打包上游 CPU ZIP，不能保證在未安裝 VC Redistributable 的電腦啟動。

因此 Portable 建置必須從建置機的 Visual Studio `VC/Redist/MSVC/<version>/x64/Microsoft.VC*.CRT` 放入未修改的可散布 CRT DLL，讓 executable 從自己資料夾載入。保留需要的完整相依鏈，不從 `System32` 任意擷取 DLL。Microsoft 支援 application-local 部署，可讓未安裝 C++ runtime 套件的目標機器直接執行。[Microsoft 部署說明](https://learn.microsoft.com/en-us/cpp/windows/walkthrough-deploying-a-visual-cpp-application-to-an-application-local-folder?view=msvc-170)

Windows 10／11 已內建 Universal CRT；本包以這兩代 x64 Windows 為目標，不需另外執行 UCRT 或 VC Redistributable 安裝程式。[Microsoft UCRT 說明](https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment?view=msvc-170)

## CPU 與記憶體

目標平台為 Windows 10／11、Intel／AMD x64 CPU。第一版使用 CPU 推論，不需要安裝 CUDA 或其他 GPU runtime。

**AVX2 是效能建議，不是這個固定 CPU ZIP 的硬性最低需求。** 上游 `b10964` Windows release 啟用 `GGML_NATIVE=OFF`、`GGML_BACKEND_DL=ON`、`GGML_CPU_ALL_VARIANTS=ON`；原始碼與 ZIP 都包含不要求 AVX 的 `x64` backend，以及 SSE4.2、Sandy Bridge、Haswell 等 backend。應保留完整 CPU DLL 集合，由上游選擇適合的版本，不應由啟動器僅因缺少 AVX2 就拒絕執行。[固定 release 建置設定](https://github.com/ggml-org/llama.cpp/blob/b10964/.github/workflows/release.yml)、[CPU 變體定義](https://github.com/ggml-org/llama.cpp/blob/b10964/ggml/src/CMakeLists.txt)

上面的相容性判斷來自建置設定與檔案檢查，尚未在各世代舊 CPU 上逐一實測。實際速度還取決於記憶體頻寬、核心數、context 長度與文件規模。

模型檔 1.56 GB 不等於整個程式只需要 1.56 GB RAM；推論 context、KV cache、模型暫存、文件解析與瀏覽器都會增加用量。建議以 16 GB RAM 的辦公電腦作為首輪驗收環境，再依實測決定較低記憶體設備是否可用。這是部署建議，並非上游保證的最低規格。

## llama-server 啟動旗標查證

下列旗標均已在固定版本的 [common/arg.cpp](https://github.com/ggml-org/llama.cpp/blob/b10964/common/arg.cpp) 與 [server 文件](https://github.com/ggml-org/llama.cpp/blob/b10964/tools/server/README.md) 核對：

| 旗標 | `b10964` 狀態與用途 |
| --- | --- |
| `--reasoning off` | 支援。設定 reasoning 關閉，並將 chat template 的 `enable_thinking` 設為 `false`；Portable 應優先用此參數。 |
| `--chat-template-kwargs` 搭配 `{"enable_thinking":false}` | 仍接受 JSON，但用它設定 `enable_thinking` 已 deprecated，應改用上列旗標。 |
| `--api-key-file <檔案>` | 支援。每行一個 API key；以 `#` 開頭的行視為註解。避免把 key 直接放到命令列參數。 |
| `--host 127.0.0.1` | 綁定本機 loopback，用於單機推論。 |
| `--model <本地 GGUF>` | 明確載入使用者已獨立準備的本地模型；離線使用不應依賴 `--hf-repo` 自動下載。 |

以上為元件能力與封裝原則；實際 port、context 長度、API key 檔案位置由本專案啟動器管理。關閉 thinking 有助於降低等待時間與維持結構化輸出流程，但不是準確率保證。

## 必須隨包保留的授權與來源

| 元件／檔案 | 授權／固定來源 | SHA-256 |
| --- | --- | --- |
| CPython `LICENSE.txt` | 保留 Python embedded ZIP 中的原檔，含 Python 與相關第三方聲明 | `62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120` |
| llama.cpp `LICENSE` | [固定 commit 的 MIT 原文](https://raw.githubusercontent.com/ggml-org/llama.cpp/b29c606e28a01b1bc8c1351026a0fa6e616bf6c4/LICENSE) | `94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d` |
| LLVM OpenMP | 保留 llama CPU ZIP 中的 `LICENSE-LLVM-OpenMP` 原檔 | `fdad1758a9e1f9d5a81e18879b3406772115edc92c24bfa36b70c654f325e8e4` |
| Qwen 原始模型 `LICENSE` | [官方固定 revision 的 Apache-2.0 原文](https://huggingface.co/Qwen/Qwen3.5-2B/raw/15852e8c16360a2fea060d615a32b45270f8a8fc/LICENSE) | `bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a` |
| GGUF 量化來源 `README.md` | [LM Studio Community 固定 revision 原文](https://huggingface.co/lmstudio-community/Qwen3.5-2B-GGUF/raw/bb84e11355a036e28f080c7793fa6d22b7c4e344/README.md) | `1f8b5a17378b64facd960d3fb8fbd2683d134c5787955ec8eaa5099ee366e532` |
| Microsoft CRT | 依建置機 Visual Studio 授權與 [官方可散布清單](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution) 保留未修改檔案及來源記錄 | 依建置機版本，記錄於發行 manifest |
| Python 第三方套件 | 保留每個 wheel／distribution 隨附的授權資訊，並記錄固定版本 | 依建置輸出記錄 |

Qwen 授權文字的固定 revision 與 GGUF 儲存庫 revision 是兩個不同的儲存庫，不應混用。上表授權檔 SHA-256 均以直接下載的原始 bytes 計算；不要在核對前改變換行格式。

## 模型定位與驗收

Qwen3.5-2B Q6_K 是獨立下載的入門模型。使用者先取得程式 ZIP 與此 GGUF，往後只更新程式，不重抓已存在模型。它能用於初步比對與流程驗證；條文例外、測試條件、否定句、表格跨頁、數值與單位推理，仍可能出現漏判或誤判。

免安裝驗收與比對準確率驗收需分開記錄：前者確認本機無 Python／LM Studio／VC installer 也能運作，後者使用代表性文件與人工標準答案量測。較大 GGUF 可另行準備並改用，但仍需重新做內容品質驗收，不應把「成功啟動」當作「所有條文判斷正確」。
