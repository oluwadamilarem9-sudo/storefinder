import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "leads.db"
if not db.exists():
    print("DB missing", db)
    raise SystemExit(0)

conn = sqlite3.connect(db)
c = conn.cursor()
for t in ("processed_domains", "leads", "rejected_candidates"):
    try:
        r = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception as e:
        r = f"ERROR: {e}"
    print(f"{t}: {r}")
conn.close()
