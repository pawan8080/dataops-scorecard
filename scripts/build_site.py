"""Wrap the dashboard page in a full HTML document for GitHub Pages.

    python scripts/build_site.py --out site

dashboard/index.html is written as a page body (so it can also be published as a
Claude artifact); this adds the doctype and head that a static host needs.
"""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    body = (ROOT / "dashboard" / "index.html").read_text()
    html = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )
    (out / "index.html").write_text(html)
    metrics = ROOT / "dashboard" / "data" / "metrics.json"
    if metrics.exists():
        shutil.copy(metrics, out / "data" / "metrics.json")
    print(f"Built {out / 'index.html'}")


if __name__ == "__main__":
    main()
