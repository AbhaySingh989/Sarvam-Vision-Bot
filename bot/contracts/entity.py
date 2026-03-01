from pydantic import BaseModel, Field

class EntityResultRow(BaseModel):
    entity: str = Field(description="The name of the entity being extracted.")
    value: str = Field(description="The extracted value of the entity from the text. If not found, use 'Not found'.")
    source_snippet: str = Field(description="The exact snippet of text where the value was found. If not found, leave blank.")
    page_number: str = Field(description="The page number where the entity was found.")

class EntityReport(BaseModel):
    extracted_entities: list[EntityResultRow] = Field(description="List of extracted entities.")
