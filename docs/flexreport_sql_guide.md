# CloudHealth FlexReports SQL Guide

FlexReports allow you to query billing data using standard SQL syntax with some specific table and column names depending on the cloud provider.

## Basic Structure
All FlexReports SQL queries must select from a dataset and typically group by time intervals.

Example for AWS Cost:
```sql
SELECT 
    timeInterval_Month AS Month, 
    SUM(ActualCostInReportingCurrency) AS Total_Cost, 
    SUM(Quantity) AS Total_Quantity
FROM aws_billing_data
GROUP BY timeInterval_Month
```

## GraphQL Execution
To execute a FlexReport via GraphQL, you must use the `createFlexReportAsync` or equivalent mutation depending on your tenant's schema, passing the `sqlStatement`.

Example:
```graphql
mutation {
  createFlexReport(input: {
    name: "Cost Report",
    sqlStatement: "SELECT timeInterval_Month, SUM(ActualCostInReportingCurrency) as cost FROM aws_billing_data GROUP BY timeInterval_Month"
  }) {
    id
    status
  }
}
```
*Note: The exact mutation name (e.g., queryDataSource, createFlexReport) and table names (aws_billing_data, azure_ea_data) vary by tenant. Always list standard datasources first to verify table names.*
