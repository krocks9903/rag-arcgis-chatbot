"""One-off: split monolithic index.html into assets/, styles.css, app.js."""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "frontend"
HTML = ROOT / "index.html"
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)


def _decode_b64(data: str) -> bytes:
    data = re.sub(r"\s+", "", data)
    pad = (-len(data)) % 4
    if pad:
        data += "=" * pad
    return base64.b64decode(data)


def extract_base64_images(text: str) -> str:
    """Replace data:image/png;base64,... with external asset files."""

    def repl_url(match: re.Match[str]) -> str:
        b64 = match.group(1)
        raw = _decode_b64(b64)
        name = hashlib.md5(raw).hexdigest()[:12]
        path = ASSETS / f"img-{name}.png"
        if not path.exists():
            path.write_bytes(raw)
        return f"url('assets/img-{name}.png')"

    text = re.sub(
        r"url\(\s*['\"]?data:image/png;base64,([A-Za-z0-9+/=\s]+?)['\"]?\s*\)",
        repl_url,
        text,
        flags=re.DOTALL,
    )

    def img_repl(match: re.Match[str]) -> str:
        b64 = match.group(1)
        raw = _decode_b64(b64)
        name = hashlib.md5(raw).hexdigest()[:12]
        path = ASSETS / f"logo-{name}.png"
        if not path.exists():
            path.write_bytes(raw)
        return f'src="assets/logo-{name}.png"'

    text = re.sub(
        r'src="data:image/png;base64,([A-Za-z0-9+/=\s]+?)"',
        img_repl,
        text,
        flags=re.DOTALL,
    )
    return text


def main() -> None:
    html = HTML.read_text(encoding="utf-8")

    style_m = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    css = style_m.group(1).strip() if style_m else ""
    css = extract_base64_images(css)

    body_m = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL)
    body = body_m.group(1).strip() if body_m else ""
    body = extract_base64_images(body)

    scripts = list(re.finditer(r"<script>(.*?)</script>", html, re.DOTALL))
    if not scripts:
        raise SystemExit("No script blocks found")
    js = scripts[-1].group(1).strip()

    # Remove inline script from body (keep esri require block in app.js)
    body = re.sub(r"<script>.*?</script>\s*", "", body, flags=re.DOTALL).strip()

    (ROOT / "styles.css").write_text(css + "\n", encoding="utf-8")

    api_header = """// API base: set window.API_BASE before this script, or use same-origin / local dev.
const API_BASE = (typeof window !== "undefined" && window.API_BASE)
  ? window.API_BASE.replace(/\\/$/, "")
  : (window.location.port === "8000" || window.location.hostname === "localhost"
      ? "http://localhost:8000"
      : window.location.origin);

"""
    (ROOT / "app.js").write_text(api_header + js + "\n", encoding="utf-8")

    head_pre = html.split("<style>", 1)[0]
    title_m = re.search(r"<title>(.*?)</title>", head_pre)
    title = title_m.group(1) if title_m else "Engage Estero"

    new_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <link rel="stylesheet" href="https://js.arcgis.com/4.30/esri/themes/light/main.css" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
{body}
  <script src="https://js.arcgis.com/4.30/"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked@4.3.0/marked.min.js"></script>
  <script src="app.js"></script>
</body>
</html>
"""
    HTML.write_text(new_html, encoding="utf-8")
    print(f"Wrote styles.css ({len(css)} bytes), app.js ({len(js)} bytes), index.html ({len(new_html)} bytes)")
    print(f"Assets: {list(ASSETS.glob('*'))}")


if __name__ == "__main__":
    main()
