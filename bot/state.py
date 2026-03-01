from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

class WorkflowModule(Enum):
    EXTRACTION = "extraction"
    COMPARISON = "comparison"
    ENTITY = "entity"

class AppState(Enum):
    MODULE_SELECTION = auto()

    # Complete Text Extraction
    EXTRACTION_AWAITING_DOC = auto()
    EXTRACTION_AWAITING_QUESTION = auto()

    # Document Comparison
    COMPARISON_AWAITING_DOC_A = auto()
    COMPARISON_AWAITING_DOC_B = auto()
    COMPARISON_AWAITING_LEVEL = auto()

    # Entity Extraction
    ENTITY_AWAITING_DOC = auto()
    ENTITY_AWAITING_MODE = auto()
    ENTITY_AWAITING_ENTITIES = auto()

@dataclass
class ChatSession:
    chat_id: int
    updated_at: float
    state: AppState = AppState.MODULE_SELECTION
    current_module: Optional[WorkflowModule] = None

    # Text Extraction Data
    job_id: Optional[str] = None
    document_name: Optional[str] = None
    text: Optional[str] = None
    awaiting_question: bool = False

    # Document Comparison Data
    doc_a_name: Optional[str] = None
    doc_a_text: Optional[str] = None
    doc_b_name: Optional[str] = None
    doc_b_text: Optional[str] = None
    comparison_level: Optional[str] = None

    # Entity Extraction Data
    entity_doc_name: Optional[str] = None
    entity_doc_text: Optional[str] = None
    entity_mode: Optional[str] = None # 'manual' or 'ai'
    entities_list: list[str] = field(default_factory=list)
