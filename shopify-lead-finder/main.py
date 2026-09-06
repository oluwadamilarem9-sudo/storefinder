"""
Shopify Public Lead Finder — terminal entry point.

Web interface:

    streamlit run app.py

Terminal cycle:

    python main.py
    python main.py --continuous
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DISCOVERY_INTERVAL
from pipeline import run_cycle


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic Shopify public lead finder")
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Repeat discovery on DISCOVERY_INTERVAL seconds.",
    )
    args = parser.parse_args()

    run_cycle()
    if not args.continuous:
        return

    print()
    print(f"Continuous mode: waiting {DISCOVERY_INTERVAL} seconds before the next cycle.")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(DISCOVERY_INTERVAL)
            print()
            run_cycle()
    except KeyboardInterrupt:
        print("\nStopped. Already-saved leads stay in leads.db and output/leads.csv.")


if __name__ == "__main__":
    main()
