"""產生 JF Studio 作品集靜態網站。

流程：
1. 從環境變數 ``PROJECTS_JSON``（GitHub Actions Secret）讀取並驗證專案清單；
   本機測試時可改用 ``--projects`` 指定公開 Repository 以外的檔案。
2. 透過 GitHub REST API，僅讀取每個已登記私人 Repository 根目錄的
   ``cover.png``（選擇性）與 ``intro.md``（必要）。
3. 將 Markdown 轉為 HTML 並以白名單清理，產生 ``site/`` 靜態網站。

安全原則：
- 只允許程式內固定的檔案路徑（ALLOWED_REMOTE_FILES），不接受任何外部指定路徑或 URL。
- Token 與專案清單只從環境變數讀取，不寫入任何檔案或日誌。
- 日誌與錯誤訊息只以「專案 #序號」與公開標題識別專案，不輸出 owner / repo / branch。
- 輸出的網站不包含私人 Repository 的名稱、連結、Token 或其他檔案。
"""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Protocol
from urllib.parse import quote

import markdown
import nh3
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE_FILE = ROOT / "templates" / "index.html"
DEFAULT_ASSETS_DIR = ROOT / "assets"
DEFAULT_OUTPUT_DIR = ROOT / "site"

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
REQUEST_TIMEOUT = 20
RETRY_DELAYS = (2, 5)  # 暫時性錯誤的重試等待秒數
TOKEN_ENV = "PRIVATE_REPOS_TOKEN"
PROJECTS_ENV = "PROJECTS_JSON"
SECRETS_HINT = "GitHub Repository 的 Settings → Secrets and variables → Actions"
DEFAULT_BRANCH = "main"

COVER_FILE = "cover.png"
INTRO_FILE = "intro.md"
# 唯一允許從私人 Repository 讀取的檔案（僅限根目錄）。
ALLOWED_REMOTE_FILES = frozenset({COVER_FILE, INTRO_FILE})

MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_INTRO_BYTES = 100 * 1024
MAX_TITLE_LENGTH = 100

PROJECT_KEYS_REQUIRED = {"owner", "repo", "title"}
PROJECT_KEYS_ALLOWED = PROJECT_KEYS_REQUIRED | {"branch"}

OWNER_RE = re.compile(r"^(?!-)(?!.*--)[A-Za-z0-9-]{1,39}(?<!-)$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "del",
    "code", "pre", "blockquote",
}
# 連同內容一起移除的標籤（其餘不允許的標籤只移除標籤、保留純文字）。
CLEAN_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math"}

TOKEN_PATTERN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
REDACTED_LINK = "[已移除私人連結]"
REDACTED_TOKEN = "[已移除]"


class PortfolioError(Exception):
    """建置過程中可預期的錯誤，訊息會直接顯示給使用者。"""


@dataclass(frozen=True)
class Project:
    owner: str
    repo: str
    branch: str
    title: str
    index: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def label(self) -> str:
        """可公開顯示於日誌的識別：只含序號與公開標題，不含 owner / repo。"""
        return f"專案 #{self.index}「{self.title}」"


# ---------------------------------------------------------------------------
# 專案清單驗證（錯誤訊息不回顯任何欄位值，避免私人 Repository 名稱進入公開日誌）
# ---------------------------------------------------------------------------

def load_projects(path: Path) -> list[Project]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise PortfolioError(f"找不到專案清單檔案：{path.name}") from None
    return parse_projects_text(raw, path.name)


def parse_projects_text(raw: str, source: str) -> list[Project]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PortfolioError(
            f"{source} 不是有效的 JSON（第 {exc.lineno} 行第 {exc.colno} 欄）：{exc.msg}"
        ) from None
    return parse_projects(data)


def parse_projects(data: Any) -> list[Project]:
    if not isinstance(data, dict) or set(data) != {"projects"}:
        raise PortfolioError('專案清單最外層必須是只包含 "projects" 欄位的物件，例如 {"projects": []}')
    items = data["projects"]
    if not isinstance(items, list):
        raise PortfolioError('"projects" 必須是陣列')

    projects: list[Project] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        where = f"projects[{index}]"
        if not isinstance(item, dict):
            raise PortfolioError(f"{where} 必須是物件")

        missing = sorted(PROJECT_KEYS_REQUIRED - item.keys())
        if missing:
            raise PortfolioError(f"{where} 缺少必要欄位：{', '.join(missing)}")
        unknown = sorted(item.keys() - PROJECT_KEYS_ALLOWED)
        if unknown:
            raise PortfolioError(
                f"{where} 含有不支援的欄位：{', '.join(unknown)}（僅允許 owner、repo、branch、title）"
            )

        for key in PROJECT_KEYS_ALLOWED & item.keys():
            if not isinstance(item[key], str):
                raise PortfolioError(f"{where}.{key} 必須是字串")

        owner = item["owner"].strip()
        repo = item["repo"].strip()
        branch = item.get("branch", DEFAULT_BRANCH).strip() or DEFAULT_BRANCH
        title = item["title"].strip()

        if not OWNER_RE.match(owner):
            raise PortfolioError(f"{where}.owner 不是有效的 GitHub 帳號或組織名稱")
        if not REPO_RE.match(repo) or repo in {".", ".."} or repo.lower().endswith(".git"):
            raise PortfolioError(f"{where}.repo 不是有效的 Repository 名稱")
        if not is_valid_branch(branch):
            raise PortfolioError(f"{where}.branch 不是有效的分支名稱")
        if not title:
            raise PortfolioError(f"{where}.title 不可為空白")
        if len(title) > MAX_TITLE_LENGTH:
            raise PortfolioError(f"{where}.title 長度不可超過 {MAX_TITLE_LENGTH} 個字元")

        key = f"{owner}/{repo}".lower()
        if key in seen:
            raise PortfolioError(f"{where} 與 projects[{seen[key]}] 重複登記同一個 Repository")
        seen[key] = index
        projects.append(Project(owner=owner, repo=repo, branch=branch, title=title, index=index))
    return projects


def is_valid_branch(branch: str) -> bool:
    return bool(
        BRANCH_RE.match(branch)
        and ".." not in branch
        and "//" not in branch
        and not branch.startswith(("/", "-", "."))
        and not branch.endswith(("/", ".", ".lock"))
    )


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

class HttpSession(Protocol):
    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


class GitHubClient:
    """僅能讀取私人 Repository 根目錄固定檔案的最小化 API 用戶端。"""

    def __init__(self, token: str, session: HttpSession | None = None) -> None:
        self._token = token
        self._session = session or requests.Session()

    def _get(self, url: str, accept: str, what: str) -> requests.Response:
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "JFSttudioPortfolio-builder",
        }
        # 僅對暫時性錯誤（5xx、網路中斷）重試；401/403/404/429 等立即回傳由呼叫端判斷。
        for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
            try:
                resp = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=False)
            except requests.RequestException as exc:
                if delay is None:
                    raise PortfolioError(f"{what}：網路連線失敗（{type(exc).__name__}）") from None
                reason = type(exc).__name__
            else:
                if resp.status_code < 500 or delay is None:
                    return resp
                reason = f"HTTP {resp.status_code}"
            print(f"  {what}：暫時性錯誤（{reason}），{delay} 秒後重試（第 {attempt} 次）")
            time.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _repo_path(project: Project) -> str:
        return f"{API_BASE}/repos/{quote(project.owner, safe='')}/{quote(project.repo, safe='')}"

    def ensure_private(self, project: Project) -> None:
        what = f"{project.label} 讀取 Repository 資訊"
        resp = self._get(self._repo_path(project), "application/vnd.github+json", what)
        if resp.status_code == 404:
            raise PortfolioError(
                f"{what} 失敗：HTTP 404，Repository 不存在，或 PRIVATE_REPOS_TOKEN 未被授權存取此 Repository"
            )
        if resp.status_code != 200:
            raise PortfolioError(f"{what} 失敗：{describe_http_error(resp)}")
        try:
            info = resp.json()
        except ValueError:
            raise PortfolioError(f"{what} 失敗：API 回傳的內容不是有效的 JSON") from None
        if info.get("private") is not True:
            raise PortfolioError(
                f"{project.label} 不是 Private Repository。本作品集只展示已登記的私人 Repository，請確認 {PROJECTS_ENV} 設定"
            )

    def fetch_file(self, project: Project, filename: str, max_bytes: int) -> bytes | None:
        """讀取根目錄檔案。檔案不存在（HTTP 404）時回傳 None，其他錯誤一律拋出例外。"""
        if filename not in ALLOWED_REMOTE_FILES:
            raise PortfolioError(f"拒絕讀取未核准的檔案：{filename!r}")
        what = f"{project.label} 讀取 {filename}"
        url = f"{self._repo_path(project)}/contents/{filename}?ref={quote(project.branch, safe='')}"
        resp = self._get(url, "application/vnd.github.raw+json", what)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise PortfolioError(f"{what} 失敗：{describe_http_error(resp)}")
        content = resp.content
        if len(content) > max_bytes:
            raise PortfolioError(f"{what} 失敗：檔案超過大小上限 {max_bytes // 1024} KB")
        return content


def describe_http_error(resp: requests.Response) -> str:
    hints = {
        401: "Token 無效或已過期，請重新產生 Fine-grained PAT 並更新 PRIVATE_REPOS_TOKEN",
        403: "權限不足或觸發 API 速率限制，請確認 Token 已授權此 Repository 且具備 Contents: Read-only 權限",
        429: "API 請求次數過多（速率限制），請稍後再重新執行",
    }
    hint = hints.get(resp.status_code)
    if hint is None:
        hint = "GitHub 伺服器錯誤，請稍後再試" if resp.status_code >= 500 else "非預期的 API 回應"
    return f"HTTP {resp.status_code}，{hint}"


# ---------------------------------------------------------------------------
# 內容處理
# ---------------------------------------------------------------------------

def redact_private_references(text: str, projects: list[Project]) -> str:
    """移除指向已登記私人 Repository 的網址與疑似 Token 字串。"""
    for project in projects:
        owner, repo = re.escape(project.owner), re.escape(project.repo)
        pattern = re.compile(
            rf"(?:https?://|git@|ssh://git@)?(?:www\.)?(?:"
            rf"(?:api\.)?github\.com/(?:repos/)?{owner}/{repo}"
            rf"|raw\.githubusercontent\.com/{owner}/{repo}"
            rf"|{owner}\.github\.io/{repo}"
            rf"|github\.com:{owner}/{repo}"
            # 名稱必須完整結束（允許 .git 結尾或句尾標點），避免誤傷同前綴的其他 Repository。
            rf")(?:\.git)?(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])[A-Za-z0-9._~:/?#@!$&'*+,;=%-]*",
            re.IGNORECASE,
        )
        text = pattern.sub(REDACTED_LINK, text)
    return TOKEN_PATTERN.sub(REDACTED_TOKEN, text)


def render_intro(markdown_text: str, projects: list[Project], title: str | None = None) -> str:
    """Markdown → HTML → 白名單清理。結果不含連結、圖片、腳本或任何屬性。

    若介紹開頭的一級標題與卡片標題相同，會移除該標題以免重複顯示。
    """
    text = redact_private_references(markdown_text, projects)
    raw_html = markdown.markdown(text, extensions=["fenced_code", "sane_lists"], output_format="html")
    cleaned = nh3.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        clean_content_tags=CLEAN_CONTENT_TAGS,
        attributes={},
        url_schemes=set(),
        link_rel=None,
        strip_comments=True,
    ).strip()
    if title:
        leading = re.match(r"<h1>(.*?)</h1>\s*", cleaned, re.DOTALL)
        if leading and html.unescape(leading.group(1)).strip() == title.strip():
            cleaned = cleaned[leading.end():]
    # 卡片標題為 <h2>，將介紹中的標題往下調整，維持正確的標題階層。
    cleaned = re.sub(
        r"<(/?)h([1-6])>",
        lambda m: f"<{m.group(1)}h{min(int(m.group(2)) + 2, 6)}>",
        cleaned,
    )
    return cleaned.strip()


def normalize_png(data: bytes, what: str) -> bytes:
    """確認資料為有效 PNG，並重新編碼以移除中繼資料區塊。"""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PortfolioError(f"{what} 不是有效的 PNG 檔案（檔案簽章不符）")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format != "PNG":
                raise PortfolioError(f"{what} 不是有效的 PNG 檔案")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
    except PortfolioError:
        raise
    except Exception as exc:  # Pillow 對損毀檔案可能拋出多種例外
        raise PortfolioError(f"{what} 不是有效的 PNG 檔案（{type(exc).__name__}）") from None
    return out.getvalue()


# ---------------------------------------------------------------------------
# 網站產生
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Card:
    title: str
    cover_path: str  # 相對於 site/ 的路徑
    intro_html: str


def collect_cards(projects: list[Project], client: GitHubClient, covers_dir: Path) -> list[Card]:
    cards: list[Card] = []
    for index, project in enumerate(projects, start=1):
        print(f"處理專案 {index}/{len(projects)}：{project.title}")
        client.ensure_private(project)

        intro_bytes = client.fetch_file(project, INTRO_FILE, MAX_INTRO_BYTES)
        if intro_bytes is None:
            raise PortfolioError(f"{project.label} 的 Repository 根目錄缺少必要的 {INTRO_FILE}（指定分支上找不到）")
        try:
            intro_text = intro_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise PortfolioError(f"{project.label} 的 {INTRO_FILE} 必須是 UTF-8 編碼") from None
        intro_html = render_intro(intro_text, projects, project.title)
        if not intro_html:
            raise PortfolioError(f"{project.label} 的 {INTRO_FILE} 內容為空，無法產生卡片")

        cover_path = "assets/cover.png"
        cover_bytes = client.fetch_file(project, COVER_FILE, MAX_COVER_BYTES)
        if cover_bytes is None:
            print(f"  {COVER_FILE} 不存在（HTTP 404），使用預設封面")
        else:
            try:
                png = normalize_png(cover_bytes, f"{project.label} 的 {COVER_FILE}")
            except PortfolioError as exc:
                # 檔案內容不是 PNG（例如副檔名改成 .png 的 ICO）：不發布該檔案，改用預設封面。
                # API 與權限錯誤已在 fetch_file 中讓建置失敗，不會走到這裡。
                print(f"::warning::{exc}，已改用預設封面")
            else:
                # 以序號命名，避免在網站檔名中出現 Repository 名稱。
                filename = f"project-{index:02d}.png"
                (covers_dir / filename).write_bytes(png)
                cover_path = f"assets/covers/{filename}"

        cards.append(Card(title=project.title, cover_path=cover_path, intro_html=intro_html))
    return cards


def render_card(card: Card) -> str:
    title = html.escape(card.title)
    return f"""      <article class="card">
        <div class="card__media">
          <img src="{html.escape(card.cover_path)}" alt="{title} 專案封面" width="1200" height="750" loading="lazy" decoding="async">
        </div>
        <div class="card__body">
          <h2 class="card__title">{title}</h2>
          <div class="card__intro" tabindex="0" role="region" aria-label="{title} 專案介紹">
{card.intro_html}
          </div>
        </div>
      </article>"""


def render_page(template_text: str, cards: list[Card], now: datetime) -> str:
    if cards:
        content = '    <div class="grid">\n' + "\n".join(render_card(c) for c in cards) + "\n    </div>"
    else:
        content = """    <div class="empty">
      <h2 class="empty__title">作品整理中</h2>
      <p class="empty__text">目前尚未公開任何作品，敬請期待。</p>
    </div>"""
    try:
        return Template(template_text).substitute(
            content=content,
            project_count=str(len(cards)),
            year=str(now.year),
            updated=now.strftime("%Y-%m-%d"),
        )
    except (KeyError, ValueError) as exc:
        raise PortfolioError(f"範本 templates/index.html 含有無效的佔位符：{exc}") from None


def audit_output(site_dir: Path, projects: list[Project], token: str | None) -> None:
    """最後防線：確認輸出中沒有 Token 或私人 Repository 網址。"""
    for path in site_dir.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if token and token.encode() in data:
            raise PortfolioError(f"安全檢查失敗：輸出檔案 {path.name} 含有 Token，已中止建置")
        if path.suffix in {".html", ".css"}:
            text = data.decode("utf-8", errors="replace")
            if redact_private_references(text, projects) != text:
                raise PortfolioError(f"安全檢查失敗：輸出檔案 {path.name} 含有私人 Repository 網址或疑似 Token")
            lowered = text.lower()
            if any(p.full_name.lower() in lowered for p in projects):
                raise PortfolioError(f"安全檢查失敗：輸出檔案 {path.name} 含有私人 Repository 識別資訊")


def resolve_projects(projects_json: str | None, projects_file: Path | None) -> list[Project]:
    """專案清單來源：--projects 指定的檔案優先，否則讀取 PROJECTS_JSON 環境變數。"""
    if projects_file is not None:
        return load_projects(projects_file)
    if projects_json is None or not projects_json.strip():
        raise PortfolioError(
            f"未提供專案清單：請到 {SECRETS_HINT} 設定名為 {PROJECTS_ENV} 的 Secret"
            f'（沒有要展示的專案時請設定為 {{"projects": []}}）；本機測試可改用 --projects 指定檔案'
        )
    return parse_projects_text(projects_json, PROJECTS_ENV)


def build(
    projects_json: str | None = None,
    projects_file: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    template_file: Path = DEFAULT_TEMPLATE_FILE,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    token: str | None = None,
    session: HttpSession | None = None,
    now: datetime | None = None,
) -> list[Card]:
    projects = resolve_projects(projects_json, projects_file)
    template_text = template_file.read_text(encoding="utf-8")
    now = now or datetime.now(timezone.utc)

    client: GitHubClient | None = None
    if projects:
        token = (token or "").strip()
        if not token:
            raise PortfolioError(
                f"專案清單已登記 {len(projects)} 個專案，但未設定 {TOKEN_ENV}。"
                f"請到 {SECRETS_HINT} 新增名為 {TOKEN_ENV} 的 Secret；本機執行時請以環境變數提供"
            )
        client = GitHubClient(token, session)

    # 先在暫存目錄建置，全部成功後才取代 site/，避免留下不完整的網站。
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".site-build-", dir=output_dir.parent))
    try:
        out_assets = staging / "assets"
        covers_dir = out_assets / "covers"
        covers_dir.mkdir(parents=True)
        shutil.copyfile(assets_dir / "style.css", out_assets / "style.css")
        default_cover = normalize_png((assets_dir / "cover.png").read_bytes(), "預設封面 assets/cover.png")
        (out_assets / "cover.png").write_bytes(default_cover)

        cards = collect_cards(projects, client, covers_dir) if client else []
        (staging / "index.html").write_text(render_page(template_text, cards, now), encoding="utf-8")

        audit_output(staging, projects, token)

        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.rename(output_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return cards


def main(argv: list[str] | None = None) -> int:
    # 確保中文訊息在 Windows 主控台等非 UTF-8 環境也能正常輸出。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="產生作品集靜態網站")
    parser.add_argument(
        "--projects", type=Path, default=None,
        help=f"本機測試用的專案清單 JSON 路徑（未指定時讀取環境變數 {PROJECTS_ENV}）",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="輸出目錄（預設 site/）")
    args = parser.parse_args(argv)

    try:
        cards = build(
            projects_json=os.environ.get(PROJECTS_ENV),
            projects_file=args.projects,
            output_dir=args.output,
            token=os.environ.get(TOKEN_ENV),
        )
    except PortfolioError as exc:
        print(f"建置失敗：{exc}", file=sys.stderr)
        return 1
    print(f"建置完成：共 {len(cards)} 張專案卡片，輸出至 {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
