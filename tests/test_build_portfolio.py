"""build_portfolio.py 的自動化測試。全部使用假的 HTTP Session，不需要真實 Token。"""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_portfolio as bp  # noqa: E402

FAKE_TOKEN = "github_pat_TESTONLY0123456789abcdefghijklmnopqrstuvwxyz"
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
API = "https://api.github.com/repos"


# ---------------------------------------------------------------------------
# 測試輔助工具
# ---------------------------------------------------------------------------

def make_png(color: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (64, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_response(status: int, body: bytes | str | dict = b"") -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    if isinstance(body, dict):
        body = json.dumps(body)
    resp._content = body.encode("utf-8") if isinstance(body, str) else body
    return resp


class FakeSession:
    """以 URL 對應假回應；未登記的 URL 一律回傳 404，並記錄所有請求。"""

    def __init__(self, routes: dict[str, object] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        value = self.routes.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return make_response(404, {"message": "Not Found"})
        return value


def add_repo(routes: dict, owner: str, repo: str, *, intro=None, cover=None, private=True, branch="main"):
    base = f"{API}/{owner}/{repo}"
    routes[base] = make_response(200, {"full_name": f"{owner}/{repo}", "private": private})
    if intro is not None:
        routes[f"{base}/contents/intro.md?ref={branch}"] = (
            intro if isinstance(intro, requests.Response) else make_response(200, intro)
        )
    if cover is not None:
        routes[f"{base}/contents/cover.png?ref={branch}"] = (
            cover if isinstance(cover, (requests.Response, Exception)) else make_response(200, cover)
        )
    return routes


@pytest.fixture
def workspace(tmp_path: Path):
    """建立獨立的專案目錄（使用真實的範本、CSS 與預設封面）。"""

    def _make(projects: list[dict] | dict | str):
        projects_file = tmp_path / "projects.json"
        if isinstance(projects, str):
            projects_file.write_text(projects, encoding="utf-8")
        else:
            data = projects if isinstance(projects, dict) else {"projects": projects}
            projects_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return projects_file

    return _make


def run_build(projects_file: Path, tmp_path: Path, session=None, token: str | None = FAKE_TOKEN):
    out = tmp_path / "site"
    cards = bp.build(
        projects_file=projects_file,
        output_dir=out,
        template_file=ROOT / "templates" / "index.html",
        assets_dir=ROOT / "assets",
        token=token,
        session=session,
        now=NOW,
    )
    return cards, out


def all_output_text(site: Path) -> str:
    return "\n".join(
        p.read_bytes().decode("utf-8", errors="replace") for p in site.rglob("*") if p.is_file()
    )


PROJECT_A = {"owner": "alice", "repo": "secret-tool", "branch": "main", "title": "Python 自動化工具"}
PROJECT_B = {"owner": "acme-org", "repo": "web.app", "branch": "develop", "title": "Web 平台"}


# ---------------------------------------------------------------------------
# 1. 空專案清單
# ---------------------------------------------------------------------------

def test_empty_project_list_builds_without_token(workspace, tmp_path, monkeypatch):
    monkeypatch.delenv(bp.TOKEN_ENV, raising=False)
    session = FakeSession()
    cards, site = run_build(workspace([]), tmp_path, session=session, token=None)

    assert cards == []
    assert session.calls == []  # 完全不呼叫 API
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "作品整理中" in index
    assert 'href="assets/style.css"' in index
    assert (site / "assets" / "style.css").is_file()
    assert Image.open(site / "assets" / "cover.png").format == "PNG"


def test_main_with_repository_projects_json_succeeds(tmp_path, monkeypatch):
    """實際的 projects.json（初始為空）可直接建置成功。"""
    monkeypatch.delenv(bp.TOKEN_ENV, raising=False)
    data = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
    if data["projects"]:
        pytest.skip("projects.json 已有專案，需要 Token 才能建置")
    assert bp.main(["--output", str(tmp_path / "site")]) == 0
    assert (tmp_path / "site" / "index.html").is_file()


def test_projects_without_token_fail_with_setup_hint(workspace, tmp_path):
    with pytest.raises(bp.PortfolioError) as exc:
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(), token=None)
    msg = str(exc.value)
    assert "PRIVATE_REPOS_TOKEN" in msg
    assert "Settings → Secrets and variables → Actions" in msg


# ---------------------------------------------------------------------------
# 2 & 3. 封面處理
# ---------------------------------------------------------------------------

def test_uses_project_cover_when_present(workspace, tmp_path):
    cover = make_png((10, 200, 10))
    routes = add_repo({}, "alice", "secret-tool", intro="# 標題\n\n介紹內容", cover=cover)
    cards, site = run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))

    assert cards[0].cover_path == "assets/covers/project-01.png"
    out_cover = site / "assets" / "covers" / "project-01.png"
    with Image.open(out_cover) as img:
        assert img.format == "PNG"
        assert img.getpixel((0, 0)) == (10, 200, 10)
    assert 'src="assets/covers/project-01.png"' in (site / "index.html").read_text(encoding="utf-8")


def test_cover_404_falls_back_to_default(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", cover=make_response(404, {"message": "Not Found"}))
    cards, site = run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))

    assert cards[0].cover_path == "assets/cover.png"
    assert not any((site / "assets" / "covers").iterdir())
    assert 'src="assets/cover.png"' in (site / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502])
def test_cover_non_404_error_fails_build(workspace, tmp_path, status):
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", cover=make_response(status, {"message": "x"}))
    with pytest.raises(bp.PortfolioError, match=f"HTTP {status}"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))
    assert not (tmp_path / "site").exists()


def test_cover_network_error_fails_build(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", cover=requests.ConnectionError("boom"))
    with pytest.raises(bp.PortfolioError, match="網路連線失敗"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


@pytest.mark.parametrize("payload", [b"not a png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, b"GIF89a....."])
def test_invalid_cover_png_fails_build(workspace, tmp_path, payload):
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", cover=payload)
    with pytest.raises(bp.PortfolioError, match="不是有效的 PNG"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


def test_cover_metadata_is_stripped(workspace, tmp_path):
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "https://github.com/alice/secret-tool internal note")
    buf = io.BytesIO()
    Image.new("RGB", (32, 20), (1, 2, 3)).save(buf, format="PNG", pnginfo=info)
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", cover=buf.getvalue())
    _, site = run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))
    assert b"internal note" not in (site / "assets" / "covers" / "project-01.png").read_bytes()


# ---------------------------------------------------------------------------
# 4. API 錯誤
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_repo_metadata_error_fails_build(workspace, tmp_path, status):
    routes = {f"{API}/alice/secret-tool": make_response(status, {"message": "nope"})}
    with pytest.raises(bp.PortfolioError, match=f"HTTP {status}"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


def test_repo_not_accessible_fails_build(workspace, tmp_path):
    with pytest.raises(bp.PortfolioError, match="未被授權存取"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession({}))


def test_public_repository_is_rejected(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", private=False)
    session = FakeSession(routes)
    with pytest.raises(bp.PortfolioError, match="不是 Private Repository"):
        run_build(workspace([PROJECT_A]), tmp_path, session=session)
    assert len(session.calls) == 1  # 確認不是私人 Repository 後不再讀取任何檔案


@pytest.mark.parametrize("status", [401, 403, 500])
def test_intro_non_404_error_fails_build(workspace, tmp_path, status):
    routes = add_repo({}, "alice", "secret-tool", intro=make_response(status, {"message": "x"}))
    with pytest.raises(bp.PortfolioError, match=f"HTTP {status}"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


def test_failed_build_keeps_previous_site(workspace, tmp_path):
    """建置失敗時不得留下或覆蓋成不完整的網站。"""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("previous", encoding="utf-8")
    routes = add_repo({}, "alice", "secret-tool", intro="介紹", cover=make_response(403))
    with pytest.raises(bp.PortfolioError):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))
    assert (site / "index.html").read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob(".site-build-*"))


# ---------------------------------------------------------------------------
# 5. intro.md
# ---------------------------------------------------------------------------

def test_missing_intro_fails_build(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", cover=make_png())
    with pytest.raises(bp.PortfolioError, match="缺少必要的 intro.md"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


@pytest.mark.parametrize("intro", ["", "   \n\n", "<script>alert(1)</script>"])
def test_empty_intro_fails_build(workspace, tmp_path, intro):
    routes = add_repo({}, "alice", "secret-tool", intro=intro)
    with pytest.raises(bp.PortfolioError, match="內容為空"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


def test_non_utf8_intro_fails_build(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", intro=make_response(200, "介紹".encode("big5")))
    with pytest.raises(bp.PortfolioError, match="UTF-8"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


def test_oversized_intro_fails_build(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", intro="a" * (bp.MAX_INTRO_BYTES + 1))
    with pytest.raises(bp.PortfolioError, match="大小上限"):
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))


# ---------------------------------------------------------------------------
# 6. Markdown 清理
# ---------------------------------------------------------------------------

DANGEROUS_MD = """# 專案標題

一般段落含 **粗體**、*斜體* 與 `inline code`。

- 項目一
- 項目二

> 引用文字

```python
print("<b>hi</b>")
```

<script>alert('xss')</script>
<img src="https://evil.example/x.png" onerror="alert(1)">
<div onclick="alert(2)" style="color:red">div 內容</div>
<iframe src="https://evil.example"></iframe>
<a href="javascript:alert(3)">js 連結</a>

![外部圖片](https://evil.example/tracker.png)

[私人 Repo](https://github.com/alice/secret-tool) 與 <https://example.com/auto>

[參考式連結][ref]

[ref]: https://example.com/ref
"""


def test_markdown_is_sanitized():
    out = bp.render_intro(DANGEROUS_MD, [bp.Project("alice", "secret-tool", "main", "t")])
    lowered = out.lower()

    for forbidden in ["<script", "<img", "<iframe", "<a ", "<a>", "href", "src=", "onerror",
                      "onclick", "style=", "javascript:", "<div", "evil.example/x.png"]:
        assert forbidden not in lowered, forbidden
    assert "alert('xss')" not in out
    assert "github.com/alice/secret-tool" not in lowered

    # 允許的安全格式仍然保留（標題降階為 h3）
    for allowed in ["<h3>專案標題</h3>", "<strong>粗體</strong>", "<em>斜體</em>", "<code>inline code</code>",
                    "<ul>", "<li>項目一</li>", "<blockquote>", "<pre><code>"]:
        assert allowed in out, allowed
    assert "&lt;b&gt;hi&lt;/b&gt;" in out  # 程式碼區塊內的 HTML 被轉義
    assert "js 連結" in out and "私人 Repo" in out  # 連結文字保留為純文字
    assert bp.REDACTED_LINK not in out or "secret-tool" not in out


def test_private_repo_urls_and_tokens_redacted_from_text():
    projects = [bp.Project("alice", "secret-tool", "main", "t"), bp.Project("acme-org", "web.app", "main", "w")]
    md = (
        "見 https://github.com/alice/secret-tool/blob/main/app.py ，"
        "或 github.com/ACME-ORG/web.app 以及 git@github.com:alice/secret-tool.git，"
        "raw.githubusercontent.com/alice/secret-tool/main/x，"
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789。另見 github.com/alice/secret-tool.\n"
        "公開專案 https://github.com/alice/secret-tool-public 不受影響。"
    )
    out = bp.render_intro(md, projects)
    assert "alice/secret-tool/" not in out
    assert "ACME-ORG/web.app" not in out
    assert "ghp_" not in out
    assert out.count(bp.REDACTED_LINK) == 5
    assert "git@" not in out
    assert "secret-tool-public" in out


def test_rendered_card_contains_no_links(workspace, tmp_path):
    routes = add_repo({}, "alice", "secret-tool", intro=DANGEROUS_MD, cover=make_png())
    _, site = run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "<a " not in index.replace('<a class="skip-link" href="#main">', "")
    assert "<script" not in index.lower()
    assert "evil.example" not in index or "evil.example/x.png" not in index


def test_title_is_html_escaped(workspace, tmp_path):
    project = dict(PROJECT_A, title='<img src=x onerror=alert(1)> & "工具"')
    routes = add_repo({}, "alice", "secret-tool", intro="介紹")
    _, site = run_build(workspace([project]), tmp_path, session=FakeSession(routes))
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "<img src=x" not in index
    assert "&lt;img src=x onerror=alert(1)&gt; &amp; &quot;工具&quot;" in index


# ---------------------------------------------------------------------------
# 7. 多個專案
# ---------------------------------------------------------------------------

def test_multiple_projects_generate_distinct_cards(workspace, tmp_path):
    routes: dict = {}
    add_repo(routes, "alice", "secret-tool", intro="Alice 專案介紹", cover=make_png((255, 0, 0)))
    add_repo(routes, "acme-org", "web.app", intro="ACME 專案介紹", cover=make_response(404), branch="develop")
    project_c = {"owner": "bob", "repo": "Data_Pipeline", "title": "資料管線"}  # 未指定 branch → main
    add_repo(routes, "bob", "Data_Pipeline", intro="Bob 專案介紹", cover=make_png((0, 0, 255)))

    session = FakeSession(routes)
    cards, site = run_build(workspace([PROJECT_A, PROJECT_B, project_c]), tmp_path, session=session)

    assert [c.title for c in cards] == ["Python 自動化工具", "Web 平台", "資料管線"]
    assert [c.cover_path for c in cards] == [
        "assets/covers/project-01.png",
        "assets/cover.png",
        "assets/covers/project-03.png",
    ]
    with Image.open(site / "assets/covers/project-01.png") as img:
        assert img.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(site / "assets/covers/project-03.png") as img:
        assert img.getpixel((0, 0)) == (0, 0, 255)

    index = (site / "index.html").read_text(encoding="utf-8")
    assert index.count('<article class="card">') == 3
    for text in ["Alice 專案介紹", "ACME 專案介紹", "Bob 專案介紹", "共 3 個專案"]:
        assert text in index
    # 所有資源都使用相對路徑，支援 GitHub Pages 專案型網址
    assert 'src="/' not in index and 'href="/' not in index

    # develop 分支有被使用
    assert any("ref=develop" in url for url, _ in session.calls)


def test_only_allowed_files_are_requested(workspace, tmp_path):
    routes: dict = {}
    add_repo(routes, "alice", "secret-tool", intro="介紹", cover=make_png())
    add_repo(routes, "acme-org", "web.app", intro="介紹", branch="develop")
    session = FakeSession(routes)
    run_build(workspace([PROJECT_A, PROJECT_B]), tmp_path, session=session)

    allowed = set()
    for owner, repo, branch in [("alice", "secret-tool", "main"), ("acme-org", "web.app", "develop")]:
        base = f"{API}/{owner}/{repo}"
        allowed |= {base, f"{base}/contents/intro.md?ref={branch}", f"{base}/contents/cover.png?ref={branch}"}
    requested = {url for url, _ in session.calls}
    assert requested <= allowed
    for _, kwargs in session.calls:
        assert kwargs["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == bp.REQUEST_TIMEOUT


def test_fetch_file_rejects_unapproved_paths():
    client = bp.GitHubClient(FAKE_TOKEN, FakeSession())
    project = bp.Project("alice", "secret-tool", "main", "t")
    for path in ["main.py", "src/cover.png", "../intro.md", ".env", "docs/intro.md"]:
        with pytest.raises(bp.PortfolioError, match="未核准"):
            client.fetch_file(project, path, 1024)


# ---------------------------------------------------------------------------
# 8. projects.json 驗證
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "不是有效的 JSON"),
        ("[]", "最外層"),
        ('{"projects": {}}', "必須是陣列"),
        ('{"projects": [], "extra": 1}', "最外層"),
        ('{"projects": ["x"]}', "必須是物件"),
        ('{"projects": [{"owner": "a", "repo": "b"}]}', "缺少必要欄位：title"),
        ('{"projects": [{"repo": "b", "title": "t"}]}', "缺少必要欄位：owner"),
        ('{"projects": [{"owner": "a", "repo": "b", "title": "t", "path": "src/x.py"}]}', "不支援的欄位：path"),
        ('{"projects": [{"owner": "a", "repo": "b", "title": "t", "url": "https://x"}]}', "不支援的欄位：url"),
        ('{"projects": [{"owner": 1, "repo": "b", "title": "t"}]}', "必須是字串"),
        ('{"projects": [{"owner": "a", "repo": "b", "title": "   "}]}', "不可為空白"),
    ],
)
def test_invalid_projects_json_rejected(workspace, tmp_path, content, message):
    with pytest.raises(bp.PortfolioError, match=message):
        run_build(workspace(content), tmp_path, session=FakeSession())


def test_missing_projects_file(tmp_path):
    with pytest.raises(bp.PortfolioError, match="找不到專案清單"):
        run_build(tmp_path / "nope.json", tmp_path, session=FakeSession())


def test_duplicate_projects_rejected(workspace, tmp_path):
    dup = dict(PROJECT_A, owner="Alice", repo="SECRET-TOOL", title="另一個名稱")
    with pytest.raises(bp.PortfolioError, match="重複登記"):
        run_build(workspace([PROJECT_A, dup]), tmp_path, session=FakeSession())


@pytest.mark.parametrize(
    "field, value",
    [
        ("owner", "-alice"),
        ("owner", "alice-"),
        ("owner", "al--ice"),
        ("owner", "a" * 40),
        ("owner", "alice/evil"),
        ("owner", "https://github.com/alice"),
        ("owner", ""),
        ("repo", "../etc"),
        ("repo", ".."),
        ("repo", "repo/contents"),
        ("repo", "repo?x=1"),
        ("repo", "repo.git"),
        ("repo", "a" * 101),
        ("branch", "../main"),
        ("branch", "main?x=1"),
        ("branch", "-main"),
        ("branch", "feature..x"),
        ("branch", "main.lock"),
        ("title", "x" * 101),
    ],
)
def test_invalid_identifiers_rejected(workspace, tmp_path, field, value):
    project = dict(PROJECT_A, **{field: value})
    session = FakeSession()
    with pytest.raises(bp.PortfolioError):
        run_build(workspace([project]), tmp_path, session=session)
    assert session.calls == []  # 驗證失敗時不得送出任何 API 請求


def test_valid_identifiers_accepted():
    projects = bp.parse_projects({"projects": [
        {"owner": "a", "repo": "b", "title": "t"},
        {"owner": "Org-123", "repo": "my_repo.v2-final", "branch": "release/1.0", "title": "t2"},
    ]})
    assert projects[0].branch == "main"
    assert projects[1].branch == "release/1.0"


# ---------------------------------------------------------------------------
# 9. 輸出不含私人資訊
# ---------------------------------------------------------------------------

def test_output_contains_no_private_data(workspace, tmp_path):
    routes: dict = {}
    add_repo(routes, "alice", "secret-tool", intro="介紹 [原始碼](https://github.com/alice/secret-tool)", cover=make_png())
    add_repo(routes, "acme-org", "web.app", intro="**ACME**", branch="develop")
    # 即使 API 端存在其他檔案，也不會被讀取
    routes[f"{API}/alice/secret-tool/contents/main.py?ref=main"] = make_response(200, "SECRET_SOURCE_CODE = 1")
    routes[f"{API}/alice/secret-tool/contents/other.png?ref=main"] = make_response(200, make_png())

    _, site = run_build(workspace([PROJECT_A, PROJECT_B]), tmp_path, session=FakeSession(routes))
    text = all_output_text(site)
    lowered = text.lower()

    assert FAKE_TOKEN not in text
    assert "SECRET_SOURCE_CODE" not in text
    assert "github.com" not in lowered
    assert "api.github.com" not in lowered
    assert "secret-tool" not in lowered  # 檔名與內容皆不出現 Repository 名稱
    assert "acme-org" not in lowered

    files = sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())
    assert files == [
        "assets/cover.png",
        "assets/covers/project-01.png",
        "assets/style.css",
        "index.html",
    ]


def test_token_leak_in_output_is_detected(tmp_path):
    (tmp_path / "index.html").write_text(f"oops {FAKE_TOKEN}", encoding="utf-8")
    with pytest.raises(bp.PortfolioError, match="含有 Token"):
        bp.audit_output(tmp_path, [], FAKE_TOKEN)


def test_error_messages_do_not_contain_token(workspace, tmp_path):
    routes = {f"{API}/alice/secret-tool": make_response(401, {"message": "Bad credentials"})}
    with pytest.raises(bp.PortfolioError) as exc:
        run_build(workspace([PROJECT_A]), tmp_path, session=FakeSession(routes))
    assert FAKE_TOKEN not in str(exc.value)


def test_main_reports_error_and_exit_code(workspace, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(bp.TOKEN_ENV, raising=False)
    projects_file = workspace([PROJECT_A])
    code = bp.main(["--projects", str(projects_file), "--output", str(tmp_path / "site")])
    assert code == 1
    assert "PRIVATE_REPOS_TOKEN" in capsys.readouterr().err


def test_default_cover_asset_is_valid_png():
    with Image.open(ROOT / "assets" / "cover.png") as img:
        assert img.format == "PNG"
        assert img.size == (1200, 750)
