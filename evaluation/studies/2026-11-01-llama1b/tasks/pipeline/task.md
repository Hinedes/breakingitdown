# Task: sales report pipeline

Build a small standard-library-only sales report from `sample_sales.csv`. Keep the processing in four modules with these exact public interfaces:

- `sales_loader.py`: `load_sales(path)` reads CSV rows and returns dictionaries with `date` (ISO `YYYY-MM-DD` string), `sku` (string), `quantity` (integer), and `unit_price_cents` (integer).
- `sales_filter.py`: `filter_sales(rows, start_date, end_date)` keeps rows whose dates are inclusively within the two ISO date strings and whose quantity is positive.
- `sales_summary.py`: `summarize_sales(rows)` returns dictionaries with `sku`, summed `units`, and summed integer `revenue_cents`; sort by SKU ascending.
- `sales_pipeline.py`: `build_sales_report(path, start_date, end_date)` calls the three helpers and returns the summary list.

Add `tests/test_sales_pipeline.py` with at least one collected `test_` function. Do not change `sample_sales.csv` or files outside this project. Use integer cents, not floating-point currency.
