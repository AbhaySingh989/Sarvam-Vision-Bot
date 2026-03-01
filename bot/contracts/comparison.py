from pydantic import BaseModel, Field

class DiffRow(BaseModel):
    header_hierarchy: str = Field(description="The hierarchy of headings this change falls under.")
    doc_a_text: str = Field(description="The text as it appeared in Document A.")
    doc_a_page: str = Field(description="The page number or section where it appeared in Doc A.")
    doc_b_text: str = Field(description="The text as it appeared in Document B.")
    doc_b_page: str = Field(description="The page number or section where it appeared in Doc B.")
    what_changed: str = Field(description="Short description of the change type (e.g. Added, Removed, Modified).")
    change_summary_3_points: str = Field(description="Exactly 3 semantic bullet points summarizing the change.")

class DiffReport(BaseModel):
    changed_rows: list[DiffRow] = Field(description="List of all changed rows.")
