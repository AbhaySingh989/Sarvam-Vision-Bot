import json
import logging
from bot.clients.sarvam_chat import SarvamChatClient
from bot.contracts.entity import EntityReport, EntityResultRow

ENTITY_PROMPT_TEMPLATE = """
You are an expert entity extraction AI.
Extract the requested entities from the text.
Output ONLY valid JSON matching this schema:

{schema}

TEXT TO EXTRACT FROM:
{text}

REQUESTED ENTITIES (If empty, infer key entities yourself):
{entities}
"""

async def run_extraction(
    chat_client: SarvamChatClient,
    model: str,
    text: str,
    entities: list[str],
) -> EntityReport:
    """
    Executes entity extraction using the chat model.
    """
    schema = EntityReport.model_json_schema()

    system_prompt = "You are an entity extraction assistant. Ensure the output strictly conforms to the JSON schema."
    user_prompt = ENTITY_PROMPT_TEMPLATE.format(
        schema=json.dumps(schema, indent=2),
        text=text,
        entities=", ".join(entities) if entities else "None specified. Please infer the most important 5-10 entities."
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response_text = await chat_client.complete(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )

            # Clean up markdown
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            elif response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            response_text = response_text.strip()

            # Parse with Pydantic
            report = EntityReport.model_validate_json(response_text)
            return report

        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed to parse extraction JSON: {e}")
            if attempt == max_retries - 1:
                logging.error("Failed to generate valid extraction after all retries.")
                return EntityReport(extracted_entities=[
                    EntityResultRow(
                        entity="Error",
                        value="Failed to extract",
                        source_snippet=str(e)[:100],
                        page_number="N/A"
                    )
                ])
