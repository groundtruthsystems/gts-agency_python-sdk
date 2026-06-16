#!/usr/bin/env python3

import os
from agency_sdk.client import CredentialsSupplier, AgencyClient
from agency_sdk.delegates.rules_dto import ExecuteRequest


def main():
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))

    # 1. Set up authentication
    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "api-key-2c018ce7-ffc2-481e-81ef-a65e07c31b61"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "Ye4wESpTea2L7HPbiywCbaoPpFceUx2Y"),
    )

    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    rules_client = client.rules()

    try:
        # 2. List available rules
        print("Listing rules...")
        rules_result = rules_client.list(organisation_id=organisation_id)
        print(f"Found {rules_result.page.total} rule(s)")
        for rule in rules_result.items:
            print(f"  - {rule.name} (id={rule.id}, status={rule.active_version_status})")

        if not rules_result.items:
            print("No rules found.")
            return

        # 3. Get rule detail
        rule_id = rules_result.items[0].id
        print(f"\nGetting detail for rule '{rule_id}'...")
        detail = rules_client.get(rule_id=rule_id, organisation_id=organisation_id)
        print(f"  Name: {detail.name}")
        print(f"  Version: {detail.version.version} (status={detail.version.status})")
        print(f"  Versions: {len(detail.versions)}")

        # 4. Execute the rule
        print(f"\nExecuting rule '{rule_id}'...")
        request = ExecuteRequest(
            organisation=organisation_id,
            context={"key": "value"},  # Update with actual input context
            trace=True,
        )
        result = rules_client.execute(rule_id=rule_id, request=request)
        print(f"  Execution ID: {result.execution_id}")
        print(f"  Duration: {result.duration_ms}ms")
        print(f"  Result: {result.result}")

        # 5. List execution history
        print(f"\nListing executions for rule '{rule_id}'...")
        executions = rules_client.list_executions(rule_id=rule_id, organisation_id=organisation_id)
        print(f"Found {executions.page.total} execution(s)")
        for execution in executions.items:
            print(f"  - {execution.id} status={execution.status} duration={execution.duration_ms}ms")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
