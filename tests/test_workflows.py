import pytest
from bot.state import AppState, ChatSession, WorkflowModule
from bot.workflows.comparison import run_comparison
from bot.workflows.entity import run_extraction
from bot.contracts.comparison import DiffReport
from bot.contracts.entity import EntityReport

class MockChatClient:
    def __init__(self, response_text):
        self.response_text = response_text
        self.call_count = 0

    async def complete(self, model, system_prompt, user_prompt, temperature=0.2):
        self.call_count += 1
        return self.response_text

@pytest.mark.asyncio
async def test_comparison_workflow_valid_json():
    mock_json = """
    {
        "changed_rows": [
            {
                "header_hierarchy": "1. Test",
                "doc_a_text": "A",
                "doc_a_page": "1",
                "doc_b_text": "B",
                "doc_b_page": "1",
                "what_changed": "Modified",
                "change_summary_3_points": "1. Point 1\\n2. Point 2\\n3. Point 3"
            }
        ]
    }
    """
    client = MockChatClient(mock_json)

    report = await run_comparison(
        chat_client=client,
        model="sarvam-m",
        doc_a_text="Doc A",
        doc_b_text="Doc B",
        level="high"
    )

    assert isinstance(report, DiffReport)
    assert len(report.changed_rows) == 1
    assert report.changed_rows[0].header_hierarchy == "1. Test"
    assert client.call_count == 1

@pytest.mark.asyncio
async def test_extraction_workflow_fallback_on_invalid_json():
    mock_json = "This is not valid json."
    client = MockChatClient(mock_json)

    report = await run_extraction(
        chat_client=client,
        model="sarvam-m",
        text="Sample text",
        entities=["Invoice"]
    )

    assert isinstance(report, EntityReport)
    assert len(report.extracted_entities) == 1
    assert report.extracted_entities[0].entity == "Error"
    # Should retry up to 3 times before failing
    assert client.call_count == 3
