"""
Shopify Public Lead Finder

Reads public store URLs from input_urls.txt, visits publicly
accessible pages, and writes found business details to output/leads.csv.

This tool does not use paid APIs, proxies, or CAPTCHA bypassing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from crawler import collect_lead
from utils import normalize_url

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "input_urls.txt"
OUTPUT_DIR = ROOT / "output"
OUTPUT_FILE = OUTPUT_DIR / "leads.csv"

CSV_COLUMNS = [
    "store_name",
    "website",
    "shopify_status",
    "email",
    "contact_page",
    "about_page",
    "instagram",
    "facebook",
    "tiktok",
    "linkedin",
    "country",
    "status",
]


def read_input_urls(path: Path) -> list[str]:
    """Read, normalize, and de-duplicate URLs from the input file."""
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {path.name}. Add your store URLs to that file."
        )

    unique_urls: list[str] = []
    seen: set[str] = set()

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        normalized = normalize_url(line)
        if not normalized:
            print(f"Skipping invalid URL on line {line_number}: {raw_line}")
            continue
        if normalized in seen:
            continue

        seen.add(normalized)
        unique_urls.append(normalized)

    return unique_urls


def print_progress(index: int, total: int, result: dict[str, str]) -> None:
    """Show a short beginner-friendly status for one website."""
    website = result.get("website", "")
    shopify = result.get("shopify_status", "UNKNOWN")
    email = result.get("email", "")
    status = result.get("status", "error").replace("_", " ").upper()

    print(f"[{index}/{total}] Checking {website}")
    print(f"Shopify: {shopify}")
    if email:
        print(f"Email: {email}")
    print(f"Status: {status}")
    print()


def save_leads(rows: list[dict[str, str]], path: Path) -> None:
    """Write one CSV row per website."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=CSV_COLUMNS)
    frame.to_csv(path, index=False, encoding="utf-8")


def main() -> None:
    urls = read_input_urls(INPUT_FILE)
    if not urls:
        print("No URLs found in input_urls.txt.")
        print("Add one public website per line, then run: python main.py")
        save_leads([], OUTPUT_FILE)
        return

    print(f"Loaded {len(urls)} unique website(s) from {INPUT_FILE.name}.")
    print("Only public pages will be requested. robots.txt is respected.\n")

    rows: list[dict[str, str]] = []
    total = len(urls)

    for index, url in enumerate(urls, start=1):
        try:
            result = collect_lead(url)
        except Exception as exc:
            # One broken website must never stop the whole run.
            result = {
                "store_name": "",
                "website": url,
                "shopify_status": "UNKNOWN",
                "email": "",
                "contact_page": "",
                "about_page": "",
                "instagram": "",
                "facebook": "",
                "tiktok": "",
                "linkedin": "",
                "country": "",
                "status": "error",
            }
            print(f"Unexpected error for {url}: {exc}")

        rows.append(result)
        print_progress(index, total, result)

    save_leads(rows, OUTPUT_FILE)
    print(f"Finished. Saved {len(rows)} row(s) to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
