#!/usr/bin/env python3

import os
from pathlib import Path
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
    datasets_client = client.dataset()

    print(f"Token: {credentials.bearer_token()}")
    print("Fetching datasets...")

    # 2. List all datasets
    try:
        datasets_response = datasets_client.list(organisation_id=organisation_id, page=0, size=10)

        if not datasets_response.items:
            print("No datasets found.")
            return

        print(f"Found {len(datasets_response.items)} datasets:")
        for i, dataset in enumerate(datasets_response.items):
            print(f"  {i}: {dataset.name} (ID: {dataset.id})")

        # 3. Clone the first dataset
        first_dataset = datasets_response.items[0]
        target_path = "./build/unittest/"

        print(f"\nCloning dataset '{first_dataset.name}' (ID: {first_dataset.id}) to {target_path}")

        # Create build directory if it doesn't exist
        Path(target_path).mkdir(parents=True, exist_ok=True)

        # Clone the dataset
        datasets_client.clone_dataset(
            dataset_id=first_dataset.id, organisation_id=organisation_id, target_path=target_path, version="latest"
        )

        print(f"Successfully cloned dataset to {target_path}")

        # List the cloned files
        cloned_files = []
        for root, dirs, files in os.walk(target_path):
            for file in files:
                cloned_files.append(os.path.join(root, file))

        if cloned_files:
            print(f"\nCloned files ({len(cloned_files)}):")
            for file_path in cloned_files[:10]:  # Show first 10 files
                print(f"  {file_path}")
            if len(cloned_files) > 10:
                print(f"  ... and {len(cloned_files) - 10} more files")
        else:
            print("\nNo files were cloned.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
