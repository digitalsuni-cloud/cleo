# CloudHealth GraphQL API Guide

CloudHealth's GraphQL API provides a single endpoint for queries and mutations.

**Endpoint:** `https://apps.cloudhealthtech.com/graphql`

## Important Concept
The GraphQL schema is **dynamic and specific to the organization's data model** (tenant). Standard tables or objects available in one tenant might not be available in another without specific modules enabled.

## Common Queries
- `organizations`: Lists standard organizations.
- `channelCustomers`: Lists sub-tenants for a partner account.

## Introspection
If you ever encounter an error saying a field doesn't exist, you should run a standard GraphQL introspection query to discover the exact schema for the user's tenant:

```graphql
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    types {
      name
      description
      kind
    }
  }
}
```
If you need fields for a specific type (e.g. `FlexReport`), you can refine the introspection to only fetch that type's fields to avoid context overflow.
