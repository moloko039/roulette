"""Тестовый сервер (staging): раздача клиента Telegram Mini App, подмена API_URL и CSP, лента тестирования."""
import logging
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from fastapi import Request, Response

logger = logging.getLogger("depnaya")
_warned_app_env = False

ALLOWED_DIRS = {"css", "js", "fonts", "games", "skins"}

ALLOWED_EXTENSIONS = {
    ".html", ".css", ".js", ".json", ".webp", ".png", ".svg", ".woff2", ".woff", ".ttf"
}

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webp": "image/webp",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}

STAGING_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Robots-Tag": "noindex",
}

RIBBON_CSS = """\
.staging-ribbon {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 99999;
  font-size: 10px;
  line-height: 14px;
  padding: 2px 6px;
  background-color: rgba(220, 38, 38, 0.75);
  color: #ffffff;
  pointer-events: none;
  font-family: sans-serif;
  text-transform: uppercase;
  font-weight: 700;
  letter-spacing: 0.5px;
  border-bottom-right-radius: 4px;
}
"""

API_URL_RE = re.compile(r"const API_URL = '[^']*';")


def is_staging(env) -> bool:
    """Проверяет, включён ли тестовый режим (APP_ENV == 'staging')."""
    global _warned_app_env
    app_env = env.get("APP_ENV")
    if not app_env:
        return False
    if app_env == "staging":
        return True
    if not _warned_app_env:
        _warned_app_env = True
        logger.warning("Неизвестное значение APP_ENV=%r, боевой режим", app_env)
    return False


def find_client_dir(env) -> Path:
    """Находит директорию клиента: CLIENT_DIR или папка-родитель bot/."""
    client_dir_str = env.get("CLIENT_DIR")
    if client_dir_str:
        client_dir = Path(client_dir_str).resolve()
    else:
        # Папка-родитель bot/
        client_dir = Path(__file__).resolve().parent.parent
    if not (client_dir / "index.html").is_file():
        raise SystemExit(f"Не найден index.html в папке клиента: {client_dir}")
    return client_dir


def patch_config_js(content: str) -> str:
    """Заменяет API_URL на пустую строку для работы на одном origin."""
    matches = API_URL_RE.findall(content)
    if len(matches) != 1:
        raise SystemExit(
            f"В js/00-config.js строка const API_URL = '...' найдена {len(matches)} раз (ожидалось ровно 1)"
        )
    return API_URL_RE.sub("const API_URL = '';", content, count=1)


def patch_index_html(html_text: str) -> str:
    """Добавляет 'self' в connect-src CSP и вставляет ленту staging."""
    if not re.search(r"\bconnect-src\b", html_text):
        raise SystemExit("В index.html в CSP отсутствует директива connect-src")
    if "</head>" not in html_text or "</body>" not in html_text:
        raise SystemExit("В index.html отсутствуют теги </head> или </body>")

    def add_self(match):
        prefix = match.group(1)
        rest = match.group(2) or ""
        if "'self'" in rest.split():
            return f"{prefix}{rest}"
        return f"{prefix} 'self' {rest.lstrip()}"

    html_text = re.sub(r"(\bconnect-src\b)(\s+[^;\"]*)?", add_self, html_text, count=1)
    html_text = html_text.replace("</head>", '  <link rel="stylesheet" href="/_staging/ribbon.css">\n</head>', 1)
    html_text = html_text.replace("</body>", '  <div class="staging-ribbon" role="note">ТЕСТОВЫЙ СЕРВЕР</div>\n</body>', 1)
    return html_text


def validate_client(client_dir: Path):
    """Проверяет при старте наличие и корректность файлов клиента для подмены."""
    index_file = client_dir / "index.html"
    if not index_file.is_file():
        raise SystemExit(f"Не найден index.html в папке клиента: {client_dir}")
    index_content = index_file.read_text(encoding="utf-8")
    patch_index_html(index_content)

    config_file = client_dir / "js" / "00-config.js"
    if not config_file.is_file():
        raise SystemExit(f"Не найден js/00-config.js в папке клиента: {client_dir}")
    config_content = config_file.read_text(encoding="utf-8")
    patch_config_js(config_content)


def register(app, client_dir: Path):
    """Регистрирует маршруты раздачи клиента в staging-режиме."""
    client_dir = Path(client_dir).resolve()
    validate_client(client_dir)

    @app.api_route("/_staging/ribbon.css", methods=["GET", "HEAD"])
    def staging_ribbon(request: Request):
        return Response(RIBBON_CSS, media_type="text/css; charset=utf-8", headers=STAGING_HEADERS)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    def serve_staging(request: Request, full_path: str):
        raw_path = request.scope.get("raw_path") or b""
        path_str = request.url.path
        unquoted = unquote(path_str)

        # 1. Защита от выхода из папки и некорректных путей
        if (
            b".." in raw_path
            or ".." in path_str
            or ".." in unquoted
            or b"%2e" in raw_path.lower()
            or "%2e" in path_str.lower()
            or b"//" in raw_path
            or "//" in path_str
            or "//" in unquoted
            or b"\\" in raw_path
            or "\\" in path_str
            or "\\" in unquoted
            or b"%5c" in raw_path.lower()
            or "%5c" in path_str.lower()
            or b"\0" in raw_path
            or "\0" in path_str
            or "\0" in unquoted
            or b"%00" in raw_path.lower()
            or "%00" in path_str.lower()
        ):
            return Response(status_code=404)

        # 2. Корневые разрешённые файлы
        if path_str in ("/", "/index.html"):
            index_path = client_dir / "index.html"
            if not index_path.is_file():
                return Response(status_code=404)
            patched = patch_index_html(index_path.read_text(encoding="utf-8"))
            return Response(patched, media_type="text/html; charset=utf-8", headers=STAGING_HEADERS)

        if path_str == "/privacy.html":
            target = client_dir / "privacy.html"
            if not target.is_file():
                return Response(status_code=404)
            return Response(target.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8", headers=STAGING_HEADERS)

        if path_str == "/terms.html":
            target = client_dir / "terms.html"
            if not target.is_file():
                return Response(status_code=404)
            return Response(target.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8", headers=STAGING_HEADERS)

        if path_str == "/robots.txt":
            return Response("User-agent: *\nDisallow: /\n", media_type="text/plain; charset=utf-8", headers=STAGING_HEADERS)

        # Запрет путей с закрывающим слэшем для остальных файлов
        if path_str.endswith("/"):
            return Response(status_code=404)

        # 3. Белый список директорий и расширений
        clean_path = path_str.lstrip("/")
        rel_path = PurePosixPath(clean_path)
        parts = rel_path.parts
        if not parts:
            return Response(status_code=404)

        first_dir = parts[0]
        if first_dir not in ALLOWED_DIRS:
            return Response(status_code=404)

        # Запрет dotfiles (.git, .env и т.д.)
        if any(part.startswith(".") for part in parts):
            return Response(status_code=404)

        # Проверка расширения файла
        ext = rel_path.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return Response(status_code=404)

        # 4. Проверка выхода за пределы папки и символьных ссылок наружу
        allowed_base_dir = (client_dir / first_dir).resolve()
        target_file = (client_dir / rel_path).resolve()

        try:
            if not target_file.is_relative_to(client_dir.resolve()) or not target_file.is_relative_to(allowed_base_dir):
                return Response(status_code=404)
        except (ValueError, AttributeError):
            return Response(status_code=404)

        if not target_file.is_file():
            return Response(status_code=404)

        # 5. Подмена на лету для js/00-config.js
        if rel_path == PurePosixPath("js/00-config.js"):
            config_content = target_file.read_text(encoding="utf-8")
            patched_config = patch_config_js(config_content)
            return Response(patched_config, media_type="text/javascript; charset=utf-8", headers=STAGING_HEADERS)

        # 6. Отдача статического файла
        media_type = MIME_TYPES.get(ext, "application/octet-stream")
        try:
            content_bytes = target_file.read_bytes()
        except OSError:
            return Response(status_code=404)

        return Response(content_bytes, media_type=media_type, headers=STAGING_HEADERS)
