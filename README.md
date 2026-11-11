# Version 3: batch upload and public research

The local browser app now includes **Preview & export merged leads**. Select one or more saved batches, preview the combined table, search any lead or evidence field, and download CSV or a native Excel `.xlsx` workbook. Exports contain every matching row across all preview pages, including source files, batch IDs, published contact evidence, and review statuses. Rows merge when name, role and company match (ignoring case and extra spaces); different roles and partial/full names stay separate. The current batch's CSV editor values are included when you refresh or export. Use research again to save reviewed edits to the batch; previewing and exporting do not save editor changes. CSV exports protect against spreadsheet formulas; Excel cells are stored as text. The ZIP download remains available for the full research output.

Start with **QUICK_START.md** for the new local browser app, 60-file import, OCR, free search providers and performance results. The older CLI instructions remain below.

# Xalms Public Lead Research

## Version 2: published email extraction and domain checks

The new `source_audit.py` reads selected public HTML pages and PDF directories, extracts literal email addresses, saves nearby text and PDF page numbers, and checks email-domain DNS. It works independently of Brave and needs no search API key. It does not discover new source URLs automatically: add approved URLs to `sources.csv`, using the original search tool or your browser to find them. Six sources from our research are included. No email source has been confirmed for Christina; the sample does not fabricate a seventh URL.

```bash
python -m pip install -r requirements.txt
python source_audit.py sources.csv --out email-results
```

Open `email-results/email_report.html` and `contacts.csv`. `sources.csv` in that output folder records every fetch attempt, including blocked pages, failures and successful pages without extractable emails. The output `audit.json` provides structured results. Existing evidence outputs are overwritten in the selected output folder, so use a fresh folder for each dated audit. No approved-review data is modified.

Input manifest columns: `company`, `person` (hint only), `source_url`, `source_date` (manually recorded), `allowed_email_domains` (semicolon-separated, optional), `local_path` (optional local HTML/PDF copy). Local copies must be inside the manifest folder; they are explicitly labelled as local copies, not live fetches. A blocked PDF can be downloaded normally in your browser and supplied this way. Do not put login pages or private files in the source list. The tool fetches only your selected URLs, without crawling child links or bypassing access controls.

All extracted addresses start with **person_association=NEEDS_REVIEW**, even when a person's name is in the input. A general company inbox is never automatically assigned to an executive. All mailboxes remain **NOT_CHECKED**. An email in a page may be stale, an example, or refer to a different person: inspect its context before approving it. Gmail and similar addresses are retained for manual business-context review, as with Manuela's accelerator listing. Restrict allowed domains to avoid collecting unrelated addresses from large directories; an empty filter keeps all literal addresses for review.

DNS statuses: `MX_PRESENT` indicates configured mail routing; `DOMAIN_NOT_FOUND` indicates NXDOMAIN; `NULL_MX_NO_MAIL` indicates explicit rejection of email; `IMPLICIT_MX_POSSIBLE` indicates no MX but an A/AAAA fallback; `NO_MAIL_ROUTE_FOUND` indicates no routing records; `UNKNOWN` indicates an inconclusive lookup. No MX alone is not treated as proof of invalidity. DNS uses Google's public JSON resolver with Cloudflare fallback, sending domain names only. DNS is checked afresh per run and cached per domain within that run. Use `--no-dns` for extraction only. DNS does not establish catch-all status, mailbox existence, deliverability or ownership.

HTML extraction reads visible text and mailto links, removes zero-width formatting characters, and excludes scripts/styles. It does not run JavaScript or perform OCR. PDF extraction requires pypdf and works on selectable text; scanned PDFs may produce no emails. Fetches are limited to public HTTP(S) hosts on standard ports and 20 MiB per source; PDFs are limited to 500 pages. Requests have timeouts and failures are reported, not treated as evidence that no address exists. Only run with source URLs you trust; this is a local CLI, not a hardened untrusted multi-user fetch service.

The included `live-email-results` folder contains the actual run from November 11, 2026. In that run, the script extracted Glenn's address, Randy's address on PDF page 22, Manuela's accelerator contact and Ursula's address on PDF page 4. DNS marked Ursula's domain DOMAIN_NOT_FOUND. All person associations remain NEEDS_REVIEW and mailboxes NOT_CHECKED, even for these familiar examples.

This version does not add inferred-address generation, SMTP probing or a search-provider replacement. Those remain separate future stages. Tests cover actual generated PDF text, HTML/mailto extraction, zero-width characters, free-mail retention, domain routing outcomes, filtering, failure reporting and the older search workflow.

A reusable Python script for researching public business leads from visible company names, roles and partial names. The seven rows from your screenshot are already in `leads.csv`. The original search-link mode requires no Python packages; the new PDF audit requires pypdf. Install Python 3.10 or newer.

## Start for free (no API key)

1. Extract this ZIP.
2. Open a terminal in the extracted folder.
3. Run:

```bash
python research.py leads.csv
```

On Windows, use `py` instead of `python` if needed.

Open `results/report.html` in your browser. Click the targeted search links, inspect the original sources and record approved details in `results/review.csv`. The free mode creates search links; it does not automatically retrieve search results.

## Automatic public search

Get your own Brave Search API key at https://api-dashboard.search.brave.com/. This is a separate service from ChatGPT; provider pricing and quotas apply. Do not paste your key into a CSV, source code or public repository.

Windows PowerShell:

```powershell
$env:BRAVE_SEARCH_API_KEY = Read-Host "Brave API key"
py research.py leads.csv --auto --out automatic-results
```

macOS/Linux bash:

```bash
read -rs -p "Brave API key: " BRAVE_SEARCH_API_KEY
export BRAVE_SEARCH_API_KEY
python3 research.py leads.csv --auto --out automatic-results
```

Open `automatic-results/report.html`. Automatic mode runs four searches per lead, plus one if a domain is supplied (32 queries for this sample). A default cap of 40 queries prevents unbounded runs. Adjust with `--max-queries 80` when appropriate. Failed requests may be retried up to three times; these retries may affect your provider quota. Successful queries are cached in the output folder so reruns can resume. Delete that cache when you intentionally want fresh results. Exit code 2 means the run is incomplete; inspect the report or `run.json`.

## Files you receive

- `report.html`: readable evidence and targeted search links.
- `evidence.csv`: possible LinkedIn names and URLs, search snippets, literal email/US phone candidates, source URLs, available dates and review status.
- `searches.csv`: queries and links, also available without a key.
- `review.csv`: your approved identities, contacts and evidence. Existing review files are preserved on reruns. New input rows are not automatically merged into existing review files; use a new output folder for a new batch.
- `run.json`: completion information and failures.
- `cache/`: cached API search responses, with retrieval dates.

Use a different `--out` folder for separate batches; generated reports and evidence are overwritten in the same folder.

## Adding your own leads or screenshots

Edit `leads.csv` using a text editor or spreadsheet program, then save as CSV. Keep these columns:

```csv
company,visible_name,role,domain
Example Foods,Jane S,Founder,example.com
```

Only `company` is required. Enter the letters actually visible; do not invent the hidden portion of a name. Correct company spelling first: in your image the beverage brand is **Plift**, which changes the search completely. A domain is an optional hint you must check, not an automatically verified company website.

This version accepts CSV, not image uploads. Read the visible screenshot rows into that CSV first. It does not perform OCR or recover blurred text.

## Reviewing evidence

All automatic evidence starts as UNREVIEWED. Candidate names come from LinkedIn result titles and can include credentials or headline fragments; they are not verified full names. A result may refer to a different company or person. Open the source, connect the person to the company and confirm the role and its date. Store disagreements rather than silently replacing them. For example, Glenn is a public Plift co-founder, but the screenshot's CEO title needs separate confirmation.

In the original research.py search mode, emails and phone numbers are copied only when literally present in search snippets, and personal mailbox domains are excluded. The new source_audit.py retains free-mail addresses for business-context review. A published business email is not proof it still works or reaches that person. Dates are passed through from provider metadata and can be absent or ambiguous; retrieval dates are not role-confirmation dates. Phone extraction is limited to North American formatting.

The tool does not access Apollo, scrape LinkedIn pages, send messages, guess addresses, run mailbox verification or use ChatGPT's internal search tools. It collects search evidence; human source review completes the process. Public pages may contain more contact information than their search snippets, so empty fields do not mean the information does not exist.

## Validation

Run `python -m unittest -v`. Included checks use mocked API responses to verify evidence handling, caching, CSV/HTML safety and preservation of review files. The automatic API path has not been tested against a live paid key in this environment.

API reference: https://api-dashboard.search.brave.com/api-reference/web/search/get
