# Changelog

## 0.1.0 - 2026-11-06

First release.

- Tools: `pdf_info`, `read_pdf`, `search_pdf`, `extract_tables`, `merge_pdfs`, `split_pdf`,
  `extract_pages`, `rotate_pages`, `extract_images`.
- Pure-Python dependencies only (pypdf, pdfplumber): no system packages.
- Token-budgeted reading with a "continue with pages=..." hint; scanned pages are reported
  instead of returned empty.
- `--dir` allow-list for all reads and writes (default: current folder); no overwrite unless
  `overwrite=true`; passwords are never logged or echoed.
