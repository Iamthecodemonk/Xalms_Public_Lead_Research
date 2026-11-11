# Validation - November 11, 2026

- 17 automated tests passed, including the real local HTTP upload/extract/download/delete flow.
- A controlled SearXNG-compatible HTTP endpoint tested JSON parsing, query encoding, caching and budget enforcement. No real SearXNG server was deployed here.
- DuckDuckGo smoke queries returned no results in this environment; live search availability is unproven. Errors remain explicit and manual search links are exported.
- Live HTML/PDF email extraction from the earlier version remains tested against six public sources (see live-email-results).
- A generated scanned PDF was successfully OCRed into a name, role and company.
- The Apollo screenshot yielded seven rows, but partial names and logo/clipped text still need human review. The screenshot itself is not included in this package.
- Final 60-file offline benchmark: 67.947 seconds, 78.99 MiB peak Python/child RSS, 60 rows, 50 exact name/company matches, 0 file failures. This is a small synthetic workload, not a production throughput promise. OCR errors are recorded for correction rather than silently promoted to verified identities.
- Frontend JavaScript syntax checked with Node.

Runtime application metrics are saved per batch. Search latency and source availability vary independently of OCR time. No emails were sent or SMTP mailbox probes performed.

Full browser automation could not launch because this execution environment blocks Chromium sockets. The HTTP workflow tests passed and JavaScript syntax passed; visual browser QA remains outstanding.
