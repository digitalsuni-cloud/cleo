## Data Integrity & Fallbacks

- **Never** add fake data, dummy numbers, or hardcoded service details as a fallback mechanism when real data is unavailable.
- If a data source, API, or query fails or returns empty results, the application should gracefully surface that emptiness or failure to the user (e.g. "No data available") rather than masking it with mocked, hallucinated, or placeholder data.
