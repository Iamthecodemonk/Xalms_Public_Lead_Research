# Xalms Batch Lead Research - Version 3

## Start the local browser app

Install Python 3.10 or newer, then open a terminal in this extracted folder:

```bash
python -m pip install -r requirements.txt
python app.py
```

On Windows, substitute `py` for `python`. After installing the dependencies, you can also double-click `START_WINDOWS.bat`. Keep the terminal open and visit **http://127.0.0.1:8765** in your browser. This is a local application, not a hosted website. It binds to your computer only.

Install **Tesseract OCR** separately for image/scanned-PDF extraction. The `tesseract` executable must be on your PATH. On Windows, add the folder containing `tesseract.exe` to PATH; restart your terminal afterwards. Official installation guidance: https://tesseract-ocr.github.io/tessdoc/Installation.html. CSVs and simple selectable-text tables can work without OCR. Benchmark generation additionally requires `python -m pip install reportlab`.

## Use the workflow

1. Select up to 60 PDF/image/CSV files. Each file may be at most 25 MiB, and the batch at most 300 MiB. Files upload sequentially and are streamed to disk.
2. Click **Upload & extract**. Processing runs in the background. Progress and failures appear without blocking the page. Images and unparsed PDF layouts go through OCR, one page at a time.
3. Review the editable CSV rows. Correct spelling, clipped company names and OCR errors. Missing names stay blank. Use **Extracted page text** for layouts that need manual row entry. These OCR/text parsers target simple three-column name/role/company tables, including Apollo screenshots; they do not reliably interpret every document layout.
4. Select DuckDuckGo or your SearXNG server, set query/page budgets, then **Start / retry research**. It performs broad searches, looks for public LinkedIn profile candidates, tries follow-up searches for candidate names, opens accessible source pages/PDFs, extracts literal emails and checks DNS.
5. Download the results ZIP. Review `research/matches.csv`, `contacts.csv`, `evidence.csv`, `errors.csv` and `search_links.csv`. The last file works as a manual fallback if automatic search is unavailable.
6. Choose whether to delete uploaded copies. Deletion removes this batch's stored upload files, OCR text and page-extraction cache. It retains extracted lead/contact results and search evidence. It never deletes the originals you selected from your computer. Browser file selection is cleared after deletion.

Use the displayed batch ID to resume after a browser/server restart. The most recent batch ID is remembered in your browser. Restarting interrupts active work; use **Upload & extract** only for a new batch, or retry a stage on a resumed batch. Research can retry using cached successful searches. Successful file extraction is also cached by file-content hash; failed files are not cached. Click **Retry extraction** to retry failures while reusing completed files. Caches and output folders live in `batches/`. No automatic deletion occurs.

## Search providers

**DuckDuckGo:** uses the third-party `ddgs` library with its DuckDuckGo backend. No API key is required. It is not an official unlimited search API. No-results responses, throttling and provider/network blocks remain possible. Live DuckDuckGo smoke queries in this development environment returned no results. Successful search handling, iterative queries, error states and matching were tested with controlled responses. We cannot claim live search reliability in your environment until you run it there.

**SearXNG:** enter your own base URL, such as `http://127.0.0.1:8080`. JSON must be enabled. An optional local Docker configuration is in `searxng/`:

```bash
cd searxng
docker compose up -d
```

Then use `http://127.0.0.1:8080` in the app. Docker is optional and must be installed separately. This configuration binds only to localhost; do not expose it publicly unchanged. It was provided for local setup and not deployed/tested here. Hosting still uses your CPU/network and upstream engines may block or throttle searches. Change the placeholder server secret before any external hosting.

Search caches last 24 hours. Default limits are 100 actual uncached queries and 30 fetched source pages per research run. There are up to four/five initial queries per lead and up to two candidate-name follow-ups. A 60-file batch can contain far more than 60 leads: online research takes much longer than local extraction. If the budget runs out, increase it and retry. No Brave key is required for the new app. The older `research.py` CLI still offers Brave optionally.

## What is and isn't automated

This is an iterative, rule-based research workflow, not the same reasoning engine as ChatGPT. It does not reconstruct blur. Company spellings and OCR mistakes may still need your correction; a search can find a full name independently from visible company/role clues. A profile candidate is flagged UNCERTAIN_CANDIDATE, never silently approved. Several people can fit the clues. Roles can be stale. Extracted emails may belong to a company inbox, another employee, a publisher or a different company: inspect the recorded context. Publication, person association and DNS status remain separate. All mailboxes remain NOT_CHECKED; no email is sent and there is no SMTP probing or guessed-address generation.

## Performance and space

See `benchmark_results.json` for the measured 60-file run and its limitations. The final measured run took 67.947 seconds and peaked at 78.99 MiB process/child RSS. All 60 lead rows were detected; 50 of 60 synthetic name/company pairs matched exactly before correction. Numeric fixture names were among the OCR errors, so review is required even for clean tables. Fixtures are 30 unique simple one-page text PDFs and 30 unique clean table images. It measures offline extraction only, not internet searching or realistic large/scanned files. A PDF has a 300-page limit; complex files can take much longer. OCR subprocesses time out after 90 seconds per pass; a detected table may use multiple passes. Pages that cannot be parsed are retained as text for review.

The importer processes files sequentially and rasterizes one page at a time, so image-memory use does not grow by retaining all 60 full images. Roughly, extraction time grows with total text plus rasterized/OCR pixels, and output disk space grows with extracted text/rows plus uploaded bytes. Lead-deduplication memory grows with unique lead rows. Per-file text/cache storage grows with that file's pages. Network work grows with uncached queries and fetched pages up to your budgets; it has no constant time guarantee. Benchmarks report peak resident memory of Python plus child OCR processes (not total operating-system memory). The app saves `extracting-performance.json` and `researching-performance.json` per batch, with elapsed seconds, peak process/child RSS, upload bytes and output bytes. RAM measurements include the local server's baseline.

## Tests

```bash
python -m unittest -v
python benchmark_batch.py --files 60
```

Tests cover browser HTTP upload/extraction/download/deletion, duplicate rows, successful-extraction cache reuse, iterative candidate searches, HTML/PDF extraction, DNS routing statuses, ambiguous matches and error reporting. The original source audit and its successful six-source live run remain included. CSV exports are suitable for Excel; there is no native XLSX export in this version.
