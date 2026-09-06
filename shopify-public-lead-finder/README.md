# Shopify Public Lead Finder

A **free, local Python tool** that checks publicly discovered Shopify store URLs and saves publicly visible business details into a CSV file.

This app only reads information that is already shown on public web pages. It does **not** use paid APIs, paid scraping services, proxies, or CAPTCHA bypassing.

---

## What it does

For each URL in `input_urls.txt`, the program:

1. Checks whether the website appears to be a Shopify store.
2. Visits the public homepage.
3. Looks for public contact / about pages on the same website.
4. Extracts emails that are actually written on those pages.
5. Saves store name, website, social links, and other public fields to `output/leads.csv`.

It never guesses missing emails or private data.

---

## 1. Install Python

1. Open [https://www.python.org/downloads/](https://www.python.org/downloads/).
2. Download **Python 3.10 or newer**.
3. Run the installer.
4. On Windows, tick **Add python.exe to PATH**.
5. Check it worked. Open a terminal and run:

```bash
python --version
```

You should see something like `Python 3.12.x`.

On some computers the command is `py` instead of `python`:

```bash
py --version
```

---

## 2. Open the project in Cursor

1. Open Cursor.
2. Choose **File → Open Folder**.
3. Select the `shopify-public-lead-finder` folder (or the parent `storefinder` folder).
4. Open the terminal inside Cursor with **Terminal → New Terminal**.

---

## 3. Create a virtual environment

A virtual environment keeps this project's packages separate from other Python projects.

In Cursor's terminal, move into the project folder first:

```bash
cd shopify-public-lead-finder
```

If Cursor already opened that folder, you can skip the `cd` command.

Then create and activate the environment.

**Windows (PowerShell):**

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once, then try again:

```bash
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

When it is active, your prompt usually starts with `(.venv)`.

---

## 4. Install requirements

With the virtual environment active:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

This installs only free, local packages:

- `requests`
- `beautifulsoup4`
- `pandas`

No API keys are needed.

---

## 5. Add URLs

Open `input_urls.txt` and put **one public website per line**.

```text
https://example-store.com
https://another-store.com
https://third-store.com
```

Notes:

- Lines starting with `#` are comments and are ignored.
- Duplicate URLs are removed automatically.
- `example.com`, `https://example.com`, and `https://example.com/` are treated as the same website.

---

## 6. Run the program

From the project folder, with the virtual environment active:

```bash
python main.py
```

You will see progress like this:

```text
[1/100] Checking https://example.com
Shopify: YES
Email: hello@example.com
Status: SUCCESS

[2/100] Checking https://example2.com
Shopify: NO
Status: NOT SHOPIFY
```

---

## 7. Where the CSV appears

Results are saved to:

```text
output/leads.csv
```

You can open that file in Excel, Google Sheets, or any spreadsheet app.

---

## 8. What each CSV column means

| Column | Meaning |
|---|---|
| `store_name` | Public store or page name, if shown. |
| `website` | Normalized website URL. |
| `shopify_status` | `YES`, `NO`, or `UNKNOWN`. |
| `email` | First public email found on the pages visited. |
| `contact_page` | Public contact page URL, if found. |
| `about_page` | Public about page URL, if found. |
| `instagram` | Public Instagram link, if shown. |
| `facebook` | Public Facebook link, if shown. |
| `tiktok` | Public TikTok link, if shown. |
| `linkedin` | Public LinkedIn link, if shown. |
| `country` | Country only if the page clearly publishes one. |
| `status` | How that website finished. |

Possible `status` values:

- `success` — public email was found
- `not_shopify` — the public page did not look like Shopify
- `shopify_detected` — the site looks like Shopify, but no public email was shown
- `no_public_email` — the site was checked, Shopify was unclear, and no public email was shown
- `blocked_or_unavailable` — the website blocked or refused the request
- `timeout` — the website did not answer in time
- `error` — another request or parsing problem

---

## 9. Limitations

- This tool only reads **public** pages. It cannot see hidden, login-protected, or admin data.
- Many Shopify stores do not publish an email on the homepage or contact page.
- Shopify detection is based on public HTML/headers. If the clues are weak, the result is `UNKNOWN`.
- Missing fields are left blank. The app does not guess emails, countries, or social profiles.
- Some websites block automated requests. Those rows are marked `blocked_or_unavailable`.
- The crawler stays on the same domain, uses delays, and checks `robots.txt` when possible.
- It does not bypass CAPTCHAs, use proxies, or rotate identities to evade website security.
- Please use this only on websites you are allowed to collect public business information from, and follow local laws and each site's terms.

---

## Project files

```text
shopify-public-lead-finder/
├── main.py
├── crawler.py
├── extractor.py
├── shopify_detector.py
├── utils.py
├── requirements.txt
├── input_urls.txt
├── output/
│   └── leads.csv
└── README.md
```
