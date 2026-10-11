"""Render the LinkedIn clip (1080x1350, 30 fps MP4) from metrics.json.

    pip install playwright && playwright install chromium
    python video/render.py --metrics dashboard/data/metrics.json --out video/dataops-scorecard.mp4

Each frame is drawn by calling setTime(t) in video/scene.html and taking a
screenshot, so the output is identical every time for the same metrics.
Requires ffmpeg on PATH. Re-render before posting so the clip shows real numbers.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def font_css(fonts_dir: Path | None) -> str:
    """Optional @font-face rules for rendering offline (e.g. from @fontsource packages)."""
    if not fonts_dir:
        return ""
    faces = [
        ("Archivo", 700, "archivo/files/archivo-latin-700-normal.woff2"),
        ("Archivo", 800, "archivo/files/archivo-latin-800-normal.woff2"),
        ("JetBrains Mono", 400, "jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2"),
        ("JetBrains Mono", 500, "jetbrains-mono/files/jetbrains-mono-latin-500-normal.woff2"),
        ("Source Sans 3", 400, "source-sans-3/files/source-sans-3-latin-400-normal.woff2"),
        ("Source Sans 3", 600, "source-sans-3/files/source-sans-3-latin-600-normal.woff2"),
    ]
    rules = []
    for family, weight, rel in faces:
        path = fonts_dir / rel
        if path.exists():
            rules.append(f"@font-face{{font-family:'{family}';font-weight:{weight};"
                         f"src:url('{path.as_uri()}') format('woff2');}}")
    return "\n".join(rules)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", default="dashboard/data/metrics.json")
    ap.add_argument("--out", default="video/dataops-scorecard.mp4")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--site-url", default="pawan8080.github.io/dataops-scorecard")
    ap.add_argument("--fonts-dir", type=Path, help="folder containing @fontsource packages")
    ap.add_argument("--poster", help="also save a still frame at this time (seconds)", type=float, default=24.5)
    args = ap.parse_args()

    metrics = json.loads(Path(args.metrics).read_text())
    frames = Path(tempfile.mkdtemp(prefix="clip-"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        page.add_init_script(f"window.METRICS = {json.dumps(metrics)}; window.SITE_URL = {json.dumps(args.site_url)};")
        page.goto((HERE / "scene.html").as_uri())
        css = font_css(args.fonts_dir)
        if css:
            page.add_style_tag(content=css)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(300)
        duration = page.evaluate("window.DURATION")
        total = int(duration * args.fps)
        for i in range(total):
            page.evaluate(f"setTime({i / args.fps})")
            page.screenshot(path=str(frames / f"f{i:05d}.png"))
        if args.poster is not None:
            page.evaluate(f"setTime({args.poster})")
            page.screenshot(path=str(out.with_suffix(".png")))
        browser.close()

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", str(frames / "f%05d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "slow", "-movflags", "+faststart",
        str(out),
    ], check=True)
    shutil.rmtree(frames)
    print(f"Wrote {out} ({total} frames, {duration}s)")


if __name__ == "__main__":
    main()
