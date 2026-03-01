import json
import logging
from bot.clients.sarvam_chat import SarvamChatClient
from bot.contracts.comparison import DiffReport, DiffRow

COMPARISON_PROMPT_TEMPLATE = """
You are an expert document comparison AI. Compare Document A and Document B.
Analyze changes at the {level} level.
Output ONLY valid JSON matching this schema:

{schema}

DOCUMENT A:
{doc_a}

DOCUMENT B:
{doc_b}
"""

async def run_comparison(
    chat_client: SarvamChatClient,
    model: str,
    doc_a_text: str,
    doc_b_text: str,
    level: str,
) -> DiffReport:
    """
    Executes the document comparison by calling the AI model.
    """
    # Truncate texts conceptually or rely on model max tokens.
    # We will enforce the JSON schema.
    schema = DiffReport.model_json_schema()

    system_prompt = "You are a precise document comparison assistant."
    user_prompt = COMPARISON_PROMPT_TEMPLATE.format(
        level=level,
        schema=json.dumps(schema, indent=2),
        doc_a=doc_a_text,
        doc_b=doc_b_text
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response_text = await chat_client.complete(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )

            # Clean up response text if it's wrapped in markdown code blocks
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            elif response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            response_text = response_text.strip()

            # Parse with Pydantic
            report = DiffReport.model_validate_json(response_text)

            # Validate constraints
            for row in report.changed_rows:
                points = [p.strip() for p in row.change_summary_3_points.split('\n') if p.strip()]
                # Enforce exactly 3 points formatting if possible
                if len(points) != 3:
                    # Best effort formatting
                    row.change_summary_3_points = "1. " + " ".join(points) + "\n2. \n3. "

            return report

        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed to parse comparison JSON: {e}")
            if attempt == max_retries - 1:
                # Provide fallback empty report instead of crashing the workflow completely
                logging.error("Failed to generate valid comparison after all retries.")
                return DiffReport(changed_rows=[
                    DiffRow(
                        header_hierarchy="Error",
                        doc_a_text="",
                        doc_a_page="N/A",
                        doc_b_text="",
                        doc_b_page="N/A",
                        what_changed="Comparison Failed",
                        change_summary_3_points="1. Model failed to generate valid output.\n2. Please retry with a smaller text segment.\n3. Or contact support."
                    )
                ])
