#!/usr/bin/env python3

import os
from agency_sdk.client import CredentialsSupplier, AgencyClient
from agency_sdk.delegates.ontology_dto import QueryFilter


def main():
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))

    # 1. Set up authentication
    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )

    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    ontology_client = client.ontology()

    # 2. Query entity data
    ontology_id = "092d0864-c9e3-494a-a3fa-0cc68ea88096"
    entity_id = "entity_1772944994810_5msfgczpk"

    try:
        # Unfiltered query with defaults
        print(f"Querying entity '{entity_id}' (no filters)...")
        result = ontology_client.query_entity(
            ontology_id=ontology_id,
            entity_id=entity_id,
            organisation_id=organisation_id,
        )
        print(f"Total rows: {result.page.total}")
        print(f"Mapping: {result.mapping.datasource_name} ({result.mapping.status})")
        if result.mapping.generated_query:
            print(f"Query: {result.mapping.generated_query[:200]}")
        for item in result.items[:5]:
            print(f"  {item}")

        # Filtered query example (uncomment and adjust property names to match your entity)
        # print(f"\nQuerying with filters...")
        # filtered = ontology_client.query_entity(
        #     ontology_id=ontology_id,
        #     entity_id=entity_id,
        #     organisation_id=organisation_id,
        #     filters=[
        #         QueryFilter(property="state", operator="in", value=["Virginia", "California"]),
        #         QueryFilter(property="age", operator="gte", value=18),
        #     ],
        #     page=0,
        #     size=50,
        # )
        # print(f"Filtered results: {filtered.page.total}")
        # for item in filtered.items[:5]:
        #     print(f"  {item}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
