# JF Studio 作品集（JFSttudioPortfolio）

以 GitHub Pages 發布的卡片式個人作品集網站。網站內容由 Python 在 GitHub Actions 中自動產生：
讀取 Actions Secret `PROJECTS_JSON` 登記的**私人** Repository，僅擷取其根目錄的 `cover.png` 與 `intro.md`，
轉換成靜態 HTML 後部署。網站不需要後端、資料庫或前端 Token，也不會提供任何通往私人 Repository 的連結。

實際的專案清單（owner、repo、branch）**只存放在 Secret 中**，不會出現在本公開 Repository 的任何檔案裡。

---

## 目錄

1. [架構概覽](#架構概覽)
2. [檔案結構與用途](#檔案結構與用途)
3. [安全性與公開範圍（請務必閱讀）](#安全性與公開範圍請務必閱讀)
4. [初次設定 GitHub Pages](#初次設定-github-pages)
5. [建立 Fine-grained PAT](#建立-fine-grained-pat)
6. [設定 PRIVATE_REPOS_TOKEN Secret](#設定-private_repos_token-secret)
7. [設定 PROJECTS_JSON：新增、修改、移除展示專案](#設定-projects_json新增修改移除展示專案)
8. [私人 Repository 需要放置的檔案](#私人-repository-需要放置的檔案)
9. [封面預設圖規則](#封面預設圖規則)
10. [自動觸發與手動部署](#自動觸發與手動部署)
11. [本機開發與測試](#本機開發與測試)
12. [常見錯誤與排查](#常見錯誤與排查)

---

## 架構概覽

```
push / 手動執行 / 每週排程 ──▶ GitHub Actions
                                   │
                                   ├─ 安裝依賴、執行 pytest（不提供任何 Secret）
                                   ├─ build_portfolio.py（僅此步驟提供 PROJECTS_JSON、PRIVATE_REPOS_TOKEN）
                                   │    ├─ 驗證 PROJECTS_JSON
                                   │    ├─ GitHub REST API（Fine-grained PAT，Contents: Read-only）
                                   │    │    ├─ GET /repos/{owner}/{repo}            → 確認為 Private
                                   │    │    ├─ GET .../contents/intro.md?ref=分支    → 必要
                                   │    │    └─ GET .../contents/cover.png?ref=分支   → 選擇性
                                   │    ├─ Markdown → HTML → 白名單清理（nh3）
                                   │    ├─ PNG 驗證與重新編碼（Pillow）
                                   │    └─ 輸出 site/（先建到暫存目錄，成功後才取代）
                                   ├─ Configure Pages → Upload Pages Artifact
                                   └─ Deploy Pages（僅在 build 成功後執行）
```

| 技術 | 用途 |
| --- | --- |
| GitHub Pages | 發布靜態網站（Source：GitHub Actions） |
| HTML / CSS | 響應式卡片 UI（桌面 3 欄、平板 2 欄、手機 1 欄） |
| Python | 讀取清單、擷取展示資料、產生網站 |
| GitHub Actions | 測試、建置、部署 |
| GitHub REST API | 只讀取已登記私人 Repository 根目錄的 `cover.png`、`intro.md` |

使用的官方 Actions：`actions/checkout@v7`、`actions/setup-python@v7`、`actions/configure-pages@v6`、
`actions/upload-pages-artifact@v5`、`actions/deploy-pages@v5`。

## 檔案結構與用途

| 路徑 | 用途 |
| --- | --- |
| `.github/workflows/deploy.yml` | 自動建置與部署流程 |
| `scripts/build_portfolio.py` | 網站產生器（驗證、API 擷取、Markdown 清理、輸出） |
| `scripts/generate_default_cover.py` | 重新產生預設封面 `assets/cover.png`（一般不需執行） |
| `tests/test_build_portfolio.py` | 自動化測試（全部使用 Mock，不需要真實 Token） |
| `templates/index.html` | 網頁範本（網站標題、說明、SEO metadata 在此修改） |
| `assets/style.css` | 網站樣式 |
| `assets/cover.png` | 預設封面（1200 × 750 PNG） |
| `projects.example.json` | 專案清單格式範例（僅含佔位資料；實際清單存放於 Secret `PROJECTS_JSON`） |
| `requirements.txt` | Python 依賴（固定版本） |
| `.gitignore` | 排除 `site/`、快取、`.env` 與 `projects.json`（避免誤提交實際清單） |
| `site/` | 建置產物（自動產生，不提交進 Git） |

`templates/index.html` 中的 `$content`、`$project_count`、`$year`、`$updated` 是由程式填入的佔位符，
請勿刪除；若要在範本中顯示 `$` 符號，請寫成 `$$`。

## 安全性與公開範圍（請務必閱讀）

- **私人 Repository 的 owner / repo / branch 只存放於 Secret `PROJECTS_JSON`。** 本 Repository 為 Public，
  請勿在任何檔案、Issue 或 commit 訊息中寫入實際清單；`projects.json` 已列入 `.gitignore`。
- GitHub Actions 的執行日誌在公開 Repository 中**任何人都看得到**。建置程式的日誌與錯誤訊息只以
  「專案 #序號「公開標題」」識別專案，不輸出 owner、repo、branch 或 API 網址；驗證錯誤也不回顯欄位值。
- **只有 `cover.png` 與 `intro.md` 會被發布。** 這兩個檔案的內容一旦部署，即成為任何人都能看到的公開資訊，
  請只放允許公開的內容。私人 Repository 的其他檔案（原始碼、設定、其他圖片）一律不會被讀取。
- 程式只允許讀取固定的兩個根目錄檔案；專案清單不接受 `path`、`url` 等任何其他欄位。
- 不 clone、不下載整個 Repository、不遞迴掃描目錄。
- 讀取前會確認 Repository 的 `private` 屬性為 `true`，公開 Repository 會被拒絕。
- `intro.md` 會轉為 HTML 後以白名單清理：只保留段落、標題、清單、粗體、斜體、刪除線、程式碼與引用。
  `<script>`、任意 HTML、事件處理器、`style`、圖片及所有連結都會被移除（連結只保留文字）。
- `intro.md` 中指向已登記私人 Repository 的網址，以及疑似 GitHub Token 的字串，會被替換成「[已移除私人連結]」/「[已移除]」。
- `cover.png` 會驗證為有效 PNG，並重新編碼以移除中繼資料（例如文字區塊）。網站中的封面檔名使用序號（`project-01.png`），不含 Repository 名稱。
- 建置最後會掃描輸出檔案，若發現 Token、私人 Repository 網址或 `owner/repo` 字串則中止建置。
- `intro.md` 開頭若有與卡片標題相同的一級標題，會自動省略以免重複顯示。
- 網頁設定 Content-Security-Policy，只允許載入同網站的 CSS 與圖片，不允許任何 JavaScript。
- Token 只在 Actions 的建置步驟以環境變數提供，不會寫入任何檔案、HTML 或日誌；部署使用 GitHub 官方的 OIDC（`id-token: write`），不使用 PAT。
- Workflow 不會在 Pull Request 觸發，因此 Secret 不會暴露給 Pull Request。

## 初次設定 GitHub Pages

1. 開啟本 Repository 的 **Settings → Pages**。
2. **Build and deployment → Source** 選擇 **GitHub Actions**。
3. （建議）在 **Settings → Environments → github-pages** 確認部署分支限制包含 `main`（預設即是）。
4. 到 **Actions** 頁面選擇 **Build and deploy portfolio** → **Run workflow** 執行第一次部署。
5. 部署完成後，網站網址為：`https://joshfelix76.github.io/JFSttudioPortfolio/`
   （也會顯示在 Actions 執行結果的 deploy 步驟中）。

> 網站所有資源都使用相對路徑，因此可正常運作於 `/JFSttudioPortfolio/` 子路徑，也相容於自訂網域。

## 建立 Fine-grained PAT

> 只有當 `PROJECTS_JSON` 中有專案時才需要 Token。空清單可直接部署。

1. GitHub 右上角頭像 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
   → **Generate new token**。
2. **Token name**：例如 `portfolio-readonly`；**Expiration**：建議設定到期日（例如 90 天）並定期更新。
3. **Resource owner**：選擇私人 Repository 所屬的帳號或組織
   （組織的 Repository 可能需要組織管理員核准 Fine-grained PAT）。
4. **Repository access**：選擇 **Only select repositories**，只勾選要展示的私人 Repository。
5. **Repository permissions**：只設定 **Contents → Read-only**
   （**Metadata → Read-only** 會自動包含，為必要權限）。其他權限一律保持 **No access**。
6. 產生後立即複製 Token。**不要**把 Token 貼到程式碼、Issue、聊天視窗、終端機參數或任何檔案中。

若展示的私人 Repository 分屬不同 owner（例如個人帳號與組織），單一 Fine-grained PAT 只能屬於一個 Resource owner，
請將所有要展示的 Repository 放在同一個 owner 之下。

## 設定 PRIVATE_REPOS_TOKEN Secret

1. 開啟本 Repository 的 **Settings → Secrets and variables → Actions**。
2. 點選 **New repository secret**。
3. **Name** 輸入 `PRIVATE_REPOS_TOKEN`，**Secret** 貼上剛才的 Fine-grained PAT，按 **Add secret**。
4. Token 到期或更換時，於同一頁面編輯此 Secret 即可，不需修改程式。

## 設定 PROJECTS_JSON：新增、修改、移除展示專案

展示清單存放在 Actions Secret `PROJECTS_JSON`，格式與 `projects.example.json` 相同：

```json
{
  "projects": [
    {
      "owner": "your-github-username",
      "repo": "your-private-repo",
      "branch": "main",
      "title": "公開顯示的專案標題"
    }
  ]
}
```

GitHub 不會顯示既有 Secret 的內容，因此每次修改都要**提供完整清單**覆寫。建議在本機（本 Repository 以外的資料夾）
保存清單檔，修改後以標準輸入更新 Secret，避免內容出現在命令列參數或歷史紀錄：

```bash
gh secret set PROJECTS_JSON --repo joshFelix76/JFSttudioPortfolio < 路徑/projects.local.json
```

也可以在 **Settings → Secrets and variables → Actions → PROJECTS_JSON → Update** 貼上完整 JSON。
**更新 Secret 不會自動觸發部署**，請接著到 Actions 手動執行 workflow。

沒有任何要展示的專案時，請設定為 `{"projects": []}`；`PROJECTS_JSON` 未設定或為空白時建置會失敗，
以免誤將網站部署成空白。

| 欄位 | 必要 | 說明 |
| --- | --- | --- |
| `owner` | 是 | 私人 Repository 所屬帳號或組織 |
| `repo` | 是 | 私人 Repository 名稱（不含 `.git`） |
| `branch` | 否 | 讀取的分支，預設 `main` |
| `title` | 是 | 卡片顯示名稱（最多 100 字元） |

- **新增**：在 `projects` 陣列中加入一筆資料，並確認 PAT 已授權該 Repository。
- **修改**：直接修改 `title` 或 `branch`。
- **移除**：刪除該筆資料。下一次部署後卡片即從網站上消失。
- 卡片顯示順序即為陣列順序。
- 以下情況會被拒絕並顯示錯誤：JSON 格式錯誤、缺少必要欄位、未知欄位（例如 `path`、`url`）、
  非字串值、無效的 owner / repo / branch 名稱、同一 Repository 重複登記（不分大小寫）。
  錯誤訊息只指出位置（例如 `projects[3].repo`），不顯示欄位內容。

更新 Secret 後，到 Actions 手動執行 workflow 即可部署。

## 私人 Repository 需要放置的檔案

在每個要展示的私人 Repository **根目錄**、`branch` 所指定的分支放置：

| 檔案 | 必要 | 說明 |
| --- | --- | --- |
| `intro.md` | **必要** | 專案介紹，UTF-8 編碼，最大 100 KB。缺少或無法讀取時建置會失敗 |
| `cover.png` | 選擇性 | 專案封面，必須是有效 PNG，最大 5 MB，建議 1200 × 750（16:10） |

`intro.md` 支援的格式：段落、標題（會自動降階以符合卡片階層）、項目／編號清單、**粗體**、*斜體*、
行內程式碼、程式碼區塊、引用、分隔線。
圖片、連結、HTML 標籤與 JavaScript 不會顯示（連結只保留文字）。
介紹文字過長時，卡片內會出現捲軸，建議控制在數段之內。

## 封面預設圖規則

1. 私人 Repository 根目錄有 `cover.png` → 使用該封面。
2. API 明確回傳 **HTTP 404**（檔案不存在）→ 使用本 Repository 的 `assets/cover.png`。
3. **HTTP 401、403、429、5xx、網路錯誤**等其他狀況 → **建置失敗**，不會用預設封面掩蓋權限或 API 問題。
4. 成功取得但內容不是有效 PNG（例如副檔名改成 `.png` 的 ICO 圖示）→ **不發布該檔案**，改用預設封面，
   並在 Actions 日誌顯示警告，提醒更換為真正的 PNG。

若要更換預設封面，直接替換 `assets/cover.png`（建議 1200 × 750 PNG），
或修改並執行 `python scripts/generate_default_cover.py`。

## 自動觸發與手動部署

**自動觸發**：

- push 到 `main` 且變更了 `scripts/`、`templates/`、`assets/`、`requirements.txt` 或 workflow 檔案。
- 每週一 02:00（UTC）排程重建，同步私人 Repository 中更新過的 `intro.md` / `cover.png`
  （GitHub 會在公開 Repository 連續 60 天沒有活動時停用排程，屆時到 Actions 頁面重新啟用即可）。

私人 Repository 本身的 commit、`README.md` 等文件變更、以及 Pull Request 都**不會**觸發；
Pull Request 不會取得任何 Secret。

**手動部署**：以下情況請到 **Actions → Build and deploy portfolio → Run workflow** 手動執行：

- 更新了 `PROJECTS_JSON` 或 `PRIVATE_REPOS_TOKEN`（Secret 更新不會觸發 workflow）。
- 私人 Repository 的 `intro.md` 或 `cover.png` 有更新，想立刻同步到網站。

流程中任何步驟（測試、驗證、API 讀取、安全檢查）失敗時，deploy job 不會執行，網站維持上一個成功版本。

## 本機開發與測試

```bash
python -m pip install -r requirements.txt
```

```bash
python -m pytest -q
```

```bash
python scripts/build_portfolio.py --projects projects.example.json
```

建置結果在 `site/`，可用 `python -m http.server --directory site` 預覽。
專案清單可用 `--projects` 指定檔案，或以環境變數 `PROJECTS_JSON` 提供；清單為空時不需要 Token。
若要在本機讀取私人 Repository，請將實際清單放在本 Repository **以外**的位置，並在**自己的終端機**以環境變數設定
`PRIVATE_REPOS_TOKEN`（不要寫進任何檔案或提交到 Git，`.env` 與 `projects.json` 已被 `.gitignore` 排除）。

## 常見錯誤與排查

| 錯誤訊息（節錄） | 原因 | 處理方式 |
| --- | --- | --- |
| `未提供專案清單：... PROJECTS_JSON` | Secret `PROJECTS_JSON` 未設定、名稱打錯或為空白 | 依「設定 PROJECTS_JSON」設定（沒有專案時設為 `{"projects": []}`） |
| `PROJECTS_JSON 不是有效的 JSON（第 X 行第 Y 欄）` | Secret 內容格式錯誤 | 在本機修正清單檔後重新 `gh secret set` |
| `已登記 N 個專案，但未設定 PRIVATE_REPOS_TOKEN` | Secret 未設定或名稱打錯 | 到 Settings → Secrets and variables → Actions 新增 `PRIVATE_REPOS_TOKEN` |
| `HTTP 401，Token 無效或已過期` | Token 過期、被撤銷或貼錯 | 重新產生 Fine-grained PAT 並更新 Secret |
| `HTTP 403，權限不足或觸發 API 速率限制` | Token 沒有 Contents: Read-only、組織尚未核准 PAT，或速率限制 | 檢查 Token 權限、組織的 PAT 核准設定，或稍後再執行 |
| `HTTP 404，Repository 不存在，或 Token 未被授權存取` | owner/repo 打錯，或 PAT 的 Repository access 未勾選此 Repository | 確認名稱，並編輯 PAT 加入該 Repository |
| `HTTP 429` | API 請求過多 | 稍後重新執行 |
| `不是 Private Repository` | 登記的 Repository 為公開 | 本作品集僅展示私人 Repository，請移除該筆資料 |
| `根目錄缺少必要的 intro.md` | 指定分支的根目錄沒有 `intro.md`（注意大小寫） | 新增檔案，或確認 `branch` 設定正確 |
| `intro.md 內容為空` | 檔案為空，或內容全部被安全清理移除 | 以 Markdown 撰寫文字內容 |
| `intro.md 必須是 UTF-8 編碼` | 檔案為 Big5 等其他編碼 | 以 UTF-8 重新存檔 |
| 警告 `cover.png 不是有效的 PNG 檔案，已改用預設封面` | 副檔名是 .png 但實際為 ICO、JPG 等格式或已損毀 | 重新匯出為真正的 PNG |
| `檔案超過大小上限` | `cover.png` > 5 MB 或 `intro.md` > 100 KB | 壓縮圖片或精簡內容 |
| `不支援的欄位` / `不是有效的 ... 名稱` / `重複登記` | `PROJECTS_JSON` 內容格式錯誤 | 依訊息指出的位置修正，僅允許 owner、repo、branch、title |
| Actions 的 Configure Pages 或 Deploy 失敗 | Pages 尚未啟用，或 Source 不是 GitHub Actions | 依「初次設定 GitHub Pages」設定 |
| 更新 Secret 後網站沒有變化 | Secret 更新不會觸發 workflow | 手動執行 workflow |
