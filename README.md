# Agency Python SDK

This is the python client library for gts agency. 

## Configuration

The examples in this repository use the following environment variables for authentication and configuration:

- `AGENCY_AUTH_URL`: The OAuth2 token endpoint URL.
- `AGENCY_API_URL`: The base URL for the Agency API.
- `AGENCY_ORG_ID`: The organization ID to use for requests.
- `AGENCY_CLIENT_ID`: Your OAuth2 client ID.
- `AGENCY_CLIENT_SECRET`: Your OAuth2 client secret.

## Example Usage

You can set these variables in your shell before running the examples:

```bash
export AGENCY_AUTH_URL="http://localhost:8080/realms/agency/protocol/openid-connect/token"
export AGENCY_API_URL="http://localhost:13001"
export AGENCY_ORG_ID="2"
export AGENCY_CLIENT_ID="your-client-id"
export AGENCY_CLIENT_SECRET="your-client-secret"

python examples/quick_clone_dataset.py
```

