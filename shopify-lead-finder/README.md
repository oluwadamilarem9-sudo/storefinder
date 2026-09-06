# Shopify Lead Finder

A **free, local Python tool** that automatically discovers candidate Shopify websites from public sources, checks whether they look like Shopify stores, and saves publicly displayed business contact details.

Open the web app:

```bash
streamlit run app.py
```

Or run one terminal cycle:

```bash
python main.py
```

You do **not** paste store URLs.

---

## 1. What every file does

| File | Role |
|---|---|
| `app.py` | Streamlit web interface. This is the main way to use the tool. |
| `pipeline.py` | Shared discovery cycle used by the web app and the terminal. |
| `main.py` | Optional terminal entry point, including `--continuous` mode. |
| `config.py` | Local settings: delays, timeouts, cycle size, output paths. |
| `discovery/domain_discovery.py` | `discover_candidates()` — asks every source for domains. |
| `discovery/discovery_sources.py` | Primary and optional secondary public sources. Add a new source here. |
| `discovery/candidate_manager.py` | Normalizes domains, removes duplicates, and filters demo/test shops. |
| `discovery/freshness.py` | Scores how much public evidence suggests a recent/new site. |
| `detection/shopify_detector.py` | Reads public HTML/headers and returns `SHOPIFY`, `NOT_SHOPIFY`, or `UNKNOWN`. |
| `contact/public_contact_finder.py` | Visits public contact/about pages and extracts displayed emails and social links. |
| `database/database.py` | Local SQLite memory (`leads.db`) and CSV export. |
| `utils/http.py` | Polite HTTP requests, timeouts, and error handling. |
| `utils/robots.py` | Checks `robots.txt` before requesting a page. |
| `utils/normalization.py` | Turns messy URLs into one domain and filters fake emails. |
| `output/leads.csv` | Spreadsheet export of useful Shopify records. |
| `requirements.txt` | Free third-party Python packages. |

Discovery is separate from detection. To add another public source later, create one class in `discovery_sources.py` and append it to `PRIMARY_SOURCES` or `SECONDARY_SOURCES`. You do not need to change `shopify_detector.py`, `public_contact_finder.py`, or `database.py`.

---

## 2. How to install dependencies

Open Cursor, then open this folder:

`C:\Users\Deski\OneDrive\Desktop\storefinder\shopify-lead-finder`

In the terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `python` is not recognized, use `py` instead.

---

## 3. How to run the tool

**Web app (recommended):**

```powershell
streamlit run app.py
```

Your browser should open to `http://localhost:8501`. Click **Run discovery cycle**. You do not paste store URLs.

**Do not deploy this app to Vercel.** Vercel hosts static sites and short serverless functions. This is a Streamlit Python app, so Vercel shows `404: NOT_FOUND`.

Use [Streamlit Community Cloud](https://share.streamlit.io/) instead:

1. Sign in with GitHub.
2. Click **New app**.
3. Repository: `oluwadamilarem9-sudo/storefinder`
4. Branch: `main`
5. Main file path: `shopify-lead-finder/app.py`
6. Click **Deploy**.

Render.com also works if you connect the same GitHub repo. The repo includes `render.yaml` for that.

**Terminal (optional):**

```powershell
python main.py
```

Repeat automatically (default wait: 1 hour):

```powershell
python main.py --continuous
```

Press `Ctrl+C` to stop continuous mode. Saved leads stay in `leads.db` and `output/leads.csv`.

Change delays, cycle size, and the wait time in `config.py`:

- `REQUEST_DELAY`
- `REQUEST_TIMEOUT`
- `MAX_DOMAINS_PER_CYCLE`
- `DISCOVERY_INTERVAL`
- `OUTPUT_FILE`
- `REJECTED_FILE`
- `DATABASE_FILE`
- `MIN_FRESHNESS_LEVEL` (default `MEDIUM`)
- `ENABLE_SECONDARY_SOURCES` (default `False`)

---

## 4. How automatic discovery works

```text
PUBLIC SOURCES
      ↓
AUTOMATIC STORE DISCOVERY
      ↓
SHOPIFY DETECTION
      ↓
NEW/RECENT STORE FILTER
      ↓
PUBLIC BUSINESS CONTACT DISCOVERY
      ↓
DEDUPLICATION
      ↓
CSV DATABASE
```

1. The program asks each public source for candidate hostnames.
2. Domains are normalized so `example.com`, `https://example.com`, and `https://example.com/` are the same site.
3. Domains already checked recently are skipped.
4. The public homepage is requested. If the site blocks, times out, or denies robots.txt, it is skipped.
5. Public HTML/headers are checked for Shopify clues.
6. Non-Shopify sites are discarded.
7. Shopify sites are checked for public contact/about pages on the same domain.
8. Only emails actually written on those pages are saved.
9. Freshness is scored from public evidence. The day this tool found a site is not a launch date.
10. HIGH and MEDIUM records go to `output/leads.csv`. LOW and UNKNOWN records go to `output/rejected_candidates.csv`.

The tool never opens Shopify admin, never logs in, and never guesses emails.

---

## 5. What sources are being used

**Primary sources** look for hostnames that recently appeared in public certificate or scan data. None of them can guarantee a store launched that day.

| Source | Why it is useful | What it does *not* prove |
|---|---|---|
| **crt.sh recent certificates** | New Shopify hostnames usually get a public TLS certificate quickly. The recent feed is better than dumping every old shop. | A certificate date can be a renewal. It is not a launch date. |
| **urlscan.io recent public scans** | A newly visible storefront is often submitted to this public scanner. | The scan date is when urlscan saw the page, not when the shop opened. |
| **Cert Spotter issuances** | Another free public view of newly issued `myshopify.com` certificates. Skipped if the API blocks anonymous use. | Same limit as crt.sh: issuance is not a launch. |

After a store is confirmed as Shopify, the tool also reads:

- the **earliest public certificate** for that hostname
- **RDAP domain registration** for custom domains, when the registry publishes it
- public **launch wording** on the homepage, without inventing a date

**Secondary sources are off by default.** Hacker News, Wayback Machine, and Common Crawl mostly surface existing or famous stores. An archive or discussion date is never treated as a launch date. Set `ENABLE_SECONDARY_SOURCES = True` in `config.py` only if you want those extra mentions, knowing they will usually be LOW freshness.

Search engines such as Google or Bing are **not** scraped.

If a source is down, slow, or blocks automated requests, it is skipped. The tool will not fall back to Hacker News to fill the list.

---

## 6. Limitations

- This is not a complete list of every Shopify store. It only sees hostnames that appear in the public sources above.
- Many stores use a custom domain. Those are kept only when a public `myshopify.com` page redirects to that domain, or when a public source already listed it.
- Many Shopify stores do not publish an email. Those rows stay in the database, but `public_email` is blank.
- Shopify detection can be `UNKNOWN` when the clues are weak. The tool does not pretend to be certain.
- Freshness is a **score**. The tool never claims a store launched on a specific date unless a public source actually published that date.
- `leads.csv` only keeps HIGH and MEDIUM records. LOW/UNKNOWN rows go to `rejected_candidates.csv`.
- Demo, test, example, staging, and similar shops are filtered out.
- Websites may block the crawler. Those sites are skipped.
- Country is saved only when a page clearly publishes one.
- Use this only for public business information you are allowed to collect, and follow local laws and each site’s terms.

---

## 7. Which parts are completely free

Everything in this project is free and local:

- Python
- `requests`, `BeautifulSoup4`, `pandas`
- SQLite (`leads.db`)
- crt.sh Certificate Transparency
- urlscan.io public search, when it allows unauthenticated use
- Cert Spotter public issuances, when it allows unauthenticated use
- Public RDAP domain dates, when a registry publishes them
- Public storefront HTML

No API key is required.

---

## 8. What could require a paid service later, only if you scale

You do **not** need these now. They would only help if you later want more coverage or higher volume:

- A paid store-intelligence API (BuiltWith, Similarweb, store databases)
- A commercial domain-discovery API
- Paid WHOIS/domain-age APIs, if RDAP is not enough
- A proxy or CAPTCHA service — this project will not add those
- Paid scraping platforms

The architecture is ready for extra **public** sources. A paid source would be a new class in `discovery_sources.py`, not a rewrite of detection, contact finding, or the database.

---

## Safety rules the program follows

- Public pages only
- No Shopify admin or private registration data
- No CAPTCHA bypass
- No login bypass
- No proxy rotation
- No email guessing
- `robots.txt` is checked where practical
- Requests are delayed
- One failed website never stops the run

---

## CSV columns

`output/leads.csv` contains:

`store_name`, `domain`, `public_email`, `shopify_status`, `freshness_level`, `freshness_score`, `freshness_evidence`, `country`, `contact_page`, `about_page`, `instagram`, `facebook`, `tiktok`, `linkedin`, `discovery_source`, `source_url`, `first_seen`, `last_checked`

Candidates that do not meet `MIN_FRESHNESS_LEVEL` are written to `output/rejected_candidates.csv`.
