#!/usr/bin/env python3

import os
from agency_sdk.client import CredentialsSupplier, AgencyClient
from agency_sdk.domain import (
    CreatePromptCommand, UpdatePromptCommand, PublishPromptCommand,
    PromptPayload, PromptContent, MessageContent, MessageRole, ContentType, SearchRequest, Pagination
)

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
    prompts_client = client.prompts()
    
    print(f"Token: {credentials.bearer_token()}")
    #
    # # 2. Search for prompts
    # search_request = SearchRequest(
    #     organisation=2,
    #     tags=["new"],
    #     pagination=Pagination(page=0,size=10),
    # )
    #
    # search_response = prompts_client.search(search_request)
    # print(f"Search Response Body: {search_response}")
    #
    #
    # # 2. Create a prompt
    # create_command = CreatePromptCommand(
    #     organisation=2,
    #     payload=PromptPayload(
    #         name="Entity extraction for lawrence",
    #         description="Sample prompt",
    #         tags=["test", "tags", "lawrence"],
    #         content=PromptContent(
    #             message=MessageContent(
    #                 role=MessageRole.SYSTEM,
    #                 content_type=ContentType.TEXT,
    #                 content="Friendly llm {{guideline}}",
    #                 parameters={
    #                     "param": {
    #                         "guideline": "string"
    #                     }
    #                 }
    #             )
    #         )
    #     )
    # )
    #
    # create_response = prompts_client.create(create_command)
    # print(f"Create Response Body: {create_response}")
    #
    # # 3. List prompts
    # list_response = prompts_client.list(organisation_id=2)
    # print(f"List Response Body: {list_response}")
    #
    # 4. Get a specific prompt
    prompt_id = "4a793103-44f0-47c8-b06f-fbdc8f3a94ae"
    #
    # # 5. Update the prompt
    # update_command = UpdatePromptCommand(
    #     organisation=2,
    #     payload=PromptPayload(
    #         name="prompt2",
    #         description="Sample prompt2",
    #         tags=["test", "tags", "new"],
    #         metadata={"test": "header"},
    #         content=PromptContent(
    #             message=MessageContent(
    #                 role=MessageRole.SYSTEM,
    #                 content_type=ContentType.TEXT,
    #                 content="Friendly llm2",
    #                 parameters={
    #                     "param": {
    #                         "type": "string"
    #                     }
    #                 }
    #             )
    #         )
    #     )
    # )
    #
    #
    # update_response = prompts_client.update(prompt_id, update_command)
    # print(f"Update Response Body: {update_response}")


    get_response = prompts_client.get(
        prompt_id=prompt_id,
        organisation_id=2,
        version="latest"
    )
    print(f"Get Response Body: {get_response}")

    # # 6. Publish the prompt
    # publish_command = PublishPromptCommand(organisation=2, payload={})
    # publish_response = prompts_client.publish(prompt_id, publish_command)
    # print(f"Publish Response Body: {publish_response}")


if __name__ == "__main__":
    main()