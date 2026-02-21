#!/usr/bin/env python3

import os
from agency_sdk.client import CredentialsSupplier, AgencyClient


def main():
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))

    # 1. Set up authentication
    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret")
    )

    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    ontology_client = client.ontology()

    print(f"Token: {credentials.bearer_token()}")

    # 2. Export an ontology
    ontology_id = "b8a45108-9952-4202-88d6-5cb1fadea23d"  # Update this with the correct ontology ID

    try:
        # Export as JSON (default)
        print(f"\nExporting ontology '{ontology_id}' as JSON...")
        json_export = ontology_client.export(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
        )
        print(f"JSON export ({len(json_export)} chars):")
        print(json_export[:500])

        # Export as Turtle/OWL
        print(f"\nExporting ontology '{ontology_id}' as Turtle...")
        turtle_export = ontology_client.export(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
            export_format="turtle",
        )
        print(f"Turtle export ({len(turtle_export)} chars):")
        print(turtle_export[:500])


        # Export as Turtle/OWL
        print(f"\nExporting ontology '{ontology_id}' as ison...")
        ison_export = ontology_client.export(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
            export_format="ison",
        )
        print(f"ison export ({len(ison_export)} chars):")
        print(ison_export[:500])

        # Export a specific branch/version
        # print(f"\nExporting ontology '{ontology_id}' from branch 'dev'...")
        # branch_export = ontology_client.export(
        #     ontology_id=ontology_id,
        #     organisation_id=organisation_id,
        #     branch="dev",
        #     version="1.0.0",
        # )
        # print(f"Branch export ({len(branch_export)} chars):")
        # print(branch_export[:500])

        # 3. List mappings for the ontology
        print(f"\nListing mappings for ontology '{ontology_id}'...")
        mappings_result = ontology_client.list_mappings(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
        )
        print(f"Found {mappings_result.page.total} mapping(s)")
        for mapping in mappings_result.items:
            print(f"  - [{mapping.mapping_type}] {mapping.entity_label or mapping.entity_id} -> {mapping.datasource_name or mapping.datasource_id} (status: {mapping.status})")

            # Get mapping detail
            detail = ontology_client.get_mapping(
                ontology_id=ontology_id,
                mapping_id=mapping.id,
                organisation_id=organisation_id,
            )
            if detail.rdbms:
                print(f"    Table: {detail.rdbms.table_schema or ''}.{detail.rdbms.table_name}")
            if detail.column_mappings:
                for col in detail.column_mappings:
                    print(f"      {col.property_name} -> {col.column_name}")
            if detail.generated_query:
                print(f"    Query: {detail.generated_query[:200]}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
