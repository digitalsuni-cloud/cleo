# CloudHealth REST API Guide

The CloudHealth Reporting API provides endpoints for fetching programmatic data, typically returning JSON. The core endpoint for cost reporting is the OLAP Reports API.

## Cost History Endpoint
`https://chapi.cloudhealthtech.com/olap_reports/cost/history`

### Headers Required
- `Authorization: Bearer <API_KEY>`
- `Accept: application/json`

### Query Parameters
- **interval**: `monthly`, `weekly`, `daily`
- **dimensions[]**: To group by cloud, account, or region. Examples: `AWS-Regions`, `AWS-Service-Category`, `AWS-Account`, `Azure-Subscription`, `GCP-Project`.
- **measures[]**: What cost to calculate. Examples: `cost`, `cost_amortized`
- **filters[]**: To filter by time or other dimensions. Example: `time:select:2026-08`

### Example Request
```http
GET https://chapi.cloudhealthtech.com/olap_reports/cost/history?interval=monthly&dimensions[]=AWS-Service-Category&measures[]=cost
```

Note: When querying via REST API, use `httpx.get()` in your python scripts instead of `httpx.post()` since these are GET requests.
