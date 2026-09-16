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
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )

    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    ontology_client = client.ontology()

    print(f"Token: {credentials.bearer_token()}")

    try:
        # 2. List ontologies (name → id)
        listed = ontology_client.list(organisation_id=organisation_id)
        print(f"Found {listed.page.total} ontology(ies)")
        for item in listed.items:
            print(f"  - [{item.kind}/{item.status}] {item.display_name or item.name} ({item.id})")

        ontology_id = listed.items[0].id if listed.items else "b8a45108-9952-4202-88d6-5cb1fadea23d"
        # Agent path: typed JSON snapshot (entities, relations, properties)
        print(f"\nExporting ontology '{ontology_id}' as a typed snapshot...")
        snapshot = ontology_client.export_snapshot(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
        )
        print(
            f"{len(snapshot.entities)} entities, {len(snapshot.relations)} relations, {len(snapshot.properties)} properties"
        )
        for entity in list(snapshot.entities.values())[:5]:
            print(f"  [{entity.entity_type}] {entity.label} ({entity.id})")
        for relation in list(snapshot.relations.values())[:5]:
            print(f"  {relation.source_id} -{relation.label}-> {relation.target_id} ({relation.relation_type})")

        # Export as Turtle/OWL
        print(f"\nExporting ontology '{ontology_id}' as Turtle...")
        turtle_export = ontology_client.export(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
            export_format="turtle",
        )
        print(f"Turtle export ({len(turtle_export)} chars):")
        print(turtle_export[:500])

        # Unified package (this ontology + imported upper ontologies, one Turtle file)
        print(f"\nExporting ontology '{ontology_id}' as a package...")
        package_export = ontology_client.export(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
            export_format="package",
        )
        print(f"package export ({len(package_export)} chars):")
        print(package_export[:500])

        # Zip of separate Turtle files — binary, so export_bytes
        print(f"\nExporting ontology '{ontology_id}' as package-zip...")
        zip_export = ontology_client.export_bytes(
            ontology_id=ontology_id,
            organisation_id=organisation_id,
            export_format="package-zip",
        )
        print(f"package-zip export ({len(zip_export)} bytes)")

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
            print(
                f"  - [{mapping.mapping_type}] {mapping.entity_label or mapping.entity_id} -> {mapping.datasource_name or mapping.datasource_id} (status: {mapping.status})"
            )

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
