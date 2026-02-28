# Plan V2: Multi-Module Telegram Doc Intelligence (Comparison + Entity Extraction + Existing OCR)

## 1. Purpose
This document defines the complete scope, functional behavior, technical architecture, implementation sequence, and acceptance criteria for the next release of the Telegram bot.

This release adds two new modules:
1. Document Comparison
2. Entity Extraction

It retains and protects the current module:
3. Complete Text Extraction (existing MVP behavior must stay intact)

This plan is written for direct execution by a coding agent and is intentionally specific to reduce ambiguity.

---

## 2. Business Goals and Outcomes

## 2.1 Primary Business Goals
1. Expand from single-flow OCR assistant to a multi-workflow document intelligence assistant.
2. Enable users to compare two documents and receive spreadsheet-ready, audit-friendly change details.
3. Enable users to extract requested or AI-suggested entities into a spreadsheet for downstream analysis.
4. Preserve the current UX quality so existing users do not perceive a regression.

## 2.2 User Value by Module
1. Complete Text Extraction: fast OCR access and current TL;DR, key points, action items, ask flow.
2. Document Comparison: legal, compliance, procurement, and policy users can track differences with page-level evidence.
3. Entity Extraction: analysts can quickly structure semi-structured documents into tabular entity outputs.

## 2.3 Success Metrics (Release-Level)
1. Functional completion rate >= 95% across all module flows in smoke tests.
2. No regressions in existing OCR module behavior.
3. Spreadsheet output generated for comparison and entity runs in one file per run.
4. User-visible progress indicators present throughout all long-running steps.

---

## 3. Confirmed Scope (User Approved)
1. Document Comparison output includes only changed rows.
2. Semantic summary must be generated per changed row (exactly 3 points each).
3. One output spreadsheet file per run.
4. Spreadsheet formatting requirements are mandatory:
   1. Constant column widths
   2. Header color
   3. Freeze top row
5. Do not remove existing UI/UX elements (progress updates, elapsed counters, emojis, action prompts).
6. Do not remove current fallback, retry, and error handling; retain and upgrade where appropriate.

---

## 4. Non-Regression Contract (Must Preserve Existing Behavior)

The following current behaviors are release blockers if broken:

1. Upload support and checks:
   1. PDF/PNG/JPG/JPEG only
   2. Size limits and MIME/extension validation
2. Vision reliability:
   1. Retry strategy per upload strategy
   2. Retry-after handling for transient backend errors
   3. Poll with status progress updates and elapsed/queued timing
3. Chat reliability:
   1. Prompt-too-long backoff and retry
   2. Structured, user-readable error handling
4. UX:
   1. Progress/status message updates with emojis
   2. Post-action prompt (`What next?`)
   3. Inline action keyboard behavior
5. Safety:
   1. Sensitive log redaction stays enabled
   2. No token leakage via transport logs

---

## 5. Context7-Based Library/Module Guidance (Latest References)

The following references were retrieved from Context7 and should guide implementation choices:

1. `python-telegram-bot`:
   1. Library ID: `/python-telegram-bot/python-telegram-bot` (version docs available for `v22.5`)
   2. Relevant patterns: `Application`, async handlers, `ConversationHandler`, state-based flows, callback/query routing.
2. `openpyxl`:
   1. Library ID: `/websites/openpyxl_readthedocs_io_en_stable`
   2. Relevant patterns: header styling (`Font`, `PatternFill`, `Alignment`), fixed widths (`column_dimensions`), freeze rows (`freeze_panes`), workbook save/export.
3. `pydantic`:
   1. Library ID: `/pydantic/pydantic` (v2 style APIs)
   2. Relevant patterns: strict validation (`ConfigDict(strict=True)`), `model_validate_json`, field constraints/aliases, robust serialization contracts.

## 5.1 Versioning Strategy for This Release
1. Keep Telegram runtime stable for low-risk migration (current app already runs with `python-telegram-bot` 21.x).
2. Adopt Context7 patterns compatible with current version where possible.
3. Add:
   1. `openpyxl` for XLSX generation/styling.
   2. `pydantic` v2 for strict model output validation.
4. Optional post-release hardening: upgrade Telegram library to latest major once module release stabilizes.

---

## 6. Target Product Behavior (Functional Spec)

## 6.1 Entry Experience
1. User enters bot (`/start`).
2. Bot shows module selector first:
   1. Complete Text Extraction
   2. Document Comparison
   3. Entity Extraction
3. User picks one module.
4. Bot transitions into module-specific guided flow.

## 6.2 Module A: Complete Text Extraction (Existing)
1. Keep existing flow exactly:
   1. Upload file
   2. OCR extraction
   3. Actions: Complete OCR, TL;DR, Key Points, Action Items, Ask Question
2. Any refactor done for shared architecture must preserve visible behavior and existing outputs.

## 6.3 Module B: Document Comparison

## 6.3.1 User Flow
1. User chooses `Document Comparison`.
2. Bot asks for Document A upload.
3. Bot confirms A received and asks for Document B upload.
4. Bot confirms B received and asks comparison level:
   1. High level
   2. Section level
   3. Subsection level
   4. Line level
5. Bot processes both documents and returns one `.xlsx` file.

## 6.3.2 Output Columns (Required)
1. `header_hierarchy`
2. `doc_a_text`
3. `doc_a_page`
4. `doc_b_text`
5. `doc_b_page`
6. `what_changed`
7. `change_summary_3_points`

## 6.3.3 Output Rules
1. Include changed rows only.
2. `change_summary_3_points` must be semantic, per changed row, exactly 3 points.
3. `doc_a_page`/`doc_b_page` must be filled where available; fallback to nearest known page; final fallback `Unknown`.
4. `header_hierarchy` must represent best-known heading path; fallback `Document > Unstructured`.

## 6.4 Module C: Entity Extraction

## 6.4.1 User Flow
1. User chooses `Entity Extraction`.
2. Bot asks user to upload one document/image.
3. Bot asks extraction mode:
   1. Provide entities manually (semicolon-separated)
   2. Let AI decide
4. If manual mode:
   1. User sends entity list, example: `company name; product name; year; price`
5. If AI mode:
   1. Bot infers candidate entities from document text using Sarvam Chat.
   2. Bot confirms inferred entities in chat before extraction (recommended guardrail).
6. Bot returns one `.xlsx` file.

## 6.4.2 Output Table (Required)
Required minimum columns:
1. `entity`
2. `value`
3. `source_snippet`
4. `page_number`

Recommended additional columns:
1. `normalized_entity`
2. `confidence`
3. `notes`

## 6.4.3 Output Rules
1. Each row is one extracted entity-value evidence unit.
2. If entity not found, include row with value `Not found in document`.
3. Page number fallback: nearest page or `Unknown`.

---

## 7. UX and Conversational Design Requirements

## 7.1 Mandatory UX Retention
1. Keep emoji-rich status text during long operations.
2. Keep elapsed-time style progress where processing is asynchronous.
3. Keep user informed at each stage transition (download, OCR, parse, compare/extract, export).
4. Keep existing "What next?" affordance where appropriate.

## 7.2 UX Upgrades for New Modules
1. Add stage progress with clear prefixes:
   1. Comparison:
      1. `📥 Receiving Document A`
      2. `📥 Receiving Document B`
      3. `🧾 OCR A in progress`
      4. `🧾 OCR B in progress`
      5. `🧠 Aligning sections`
      6. `🔍 Detecting changes`
      7. `📝 Generating semantic summaries`
      8. `📊 Building spreadsheet`
   2. Entity extraction:
      1. `📥 Receiving document`
      2. `🧾 Extracting text`
      3. `🧠 Determining entity set` (AI mode)
      4. `🔎 Extracting entity values`
      5. `📊 Building spreadsheet`
2. Continue using safe status edit fallback for Telegram message edit failures.
3. Add `/cancel` in multi-step flows to safely reset module state.

---

## 8. Technical Architecture

## 8.1 Design Principles
1. Incremental change with minimal breakage to existing MVP.
2. Shared core services for OCR/chat/retries/error handling.
3. Explicit workflow state machine per module.
4. Structured model I/O contracts for predictable parsing.

## 8.2 Proposed Components
1. `workflow_router`:
   1. Handles module selection and transition to specific flow handlers.
2. `session_state`:
   1. Extends current per-chat session to include module context and pending inputs.
3. `ocr_pipeline`:
   1. Reuses existing Vision flow.
   2. Enhances extraction output to preserve page-level references.
4. `comparison_engine`:
   1. Segmenter by granularity.
   2. Alignment + diff classifier.
   3. Semantic summary orchestrator.
5. `entity_engine`:
   1. Manual or AI-derived schema selection.
   2. Value extraction and evidence mapping.
6. `excel_exporter`:
   1. Styled workbook builder with fixed widths and freeze panes.
7. `contracts`:
   1. `pydantic` models for all chat JSON outputs.

## 8.3 Data Model Additions
1. `ModuleType` enum:
   1. `complete_extraction`
   2. `document_comparison`
   3. `entity_extraction`
2. `WorkflowState` enum with explicit states for each module.
3. `DocumentArtifact`:
   1. `document_name`
   2. `job_id`
   3. `full_text`
   4. `pages: list[OCRPage]`
4. `OCRPage`:
   1. `page_number`
   2. `text`
5. `ComparisonRow` and `EntityRow` schemas.

---

## 9. Comparison Engine Specification

## 9.1 Segmentation Strategy by Comparison Level
1. High level:
   1. Segment by top headings when available.
   2. Fallback to large paragraph blocks.
2. Section level:
   1. Segment by heading depth 1.
3. Subsection level:
   1. Segment by heading depth 2/3.
   2. If headings absent, fallback to paragraph blocks.
4. Line level:
   1. Segment each non-empty normalized line.

## 9.2 Header Hierarchy Resolution
1. Parse markdown heading markers where present.
2. Secondary fallback to numbering patterns (`1.`, `1.1`, etc.).
3. Carry forward nearest active hierarchy to child segments.
4. If unavailable, use `Document > Unstructured`.

## 9.3 Alignment and Change Detection
1. Remove exact matches early via normalized hash.
2. Pair remaining segments using similarity scoring.
3. Change classification:
   1. `ADDED`: only in doc B
   2. `REMOVED`: only in doc A
   3. `MODIFIED`: matched pair with meaningful delta
4. Generate `what_changed`:
   1. Rule-based concise description (add/remove/modify + key token deltas).

## 9.4 Semantic Summary Generation (Per Changed Row)
1. Batch changed rows for LLM processing with stable `row_id`.
2. Prompt contract: return strict JSON with exactly 3 summary points per row.
3. Validate with `pydantic`; retry invalid outputs.
4. If retries exhausted:
   1. fallback summary template with deterministic phrasing,
   2. still output 3 points.

## 9.5 Performance Controls
1. Dynamic batch sizing for row summarization to avoid token overflow.
2. Reuse prompt-too-long backoff strategy from current chat flow.
3. Cap max text per segment for summarization context.

---

## 10. Entity Extraction Engine Specification

## 10.1 Entity Input Modes
1. Manual mode:
   1. Parse semicolon-separated entities.
   2. Trim/normalize duplicates and blanks.
2. AI mode:
   1. Ask model for candidate entities based on document type/content.
   2. Validate candidate list schema with `pydantic`.
   3. Confirm candidate entities in chat before extraction (recommended).

## 10.2 Extraction Logic
1. Use OCR page-wise text when available.
2. Chunk text to manageable units.
3. Query model with:
   1. Target entities
   2. Chunk text
   3. Required JSON output shape
4. Aggregate results across chunks.
5. Dedupe rows using `(entity, value, page_number, normalized_snippet)`.
6. Fill not-found rows for missing target entities.

## 10.3 Quality Controls
1. Enforce strict output schema with `pydantic`.
2. Reject low-quality model responses lacking evidence.
3. Keep snippet length bounded and human-readable.

---

## 11. Spreadsheet Generation Specification (openpyxl)

## 11.1 Common Formatting Standard
1. Sheet title:
   1. Comparison: `Comparison_Results`
   2. Entity: `Entity_Results`
2. Header style:
   1. Bold white font
   2. Solid header fill color (e.g., `1F4E78`)
   3. Centered text alignment
3. Freeze top row:
   1. `freeze_panes = "A2"`
4. Constant column widths:
   1. Apply fixed width map per column (no autosize).
5. Data cell style:
   1. Wrap text on long columns
   2. Top vertical alignment
6. Optional polish:
   1. Auto-filter on header row
   2. Zebra row fill for readability

## 11.2 Comparison Workbook Column Widths (Proposed Constants)
1. `A header_hierarchy`: 35
2. `B doc_a_text`: 60
3. `C doc_a_page`: 14
4. `D doc_b_text`: 60
5. `E doc_b_page`: 14
6. `F what_changed`: 34
7. `G change_summary_3_points`: 68

## 11.3 Entity Workbook Column Widths (Proposed Constants)
1. `A entity`: 28
2. `B value`: 36
3. `C source_snippet`: 72
4. `D page_number`: 14
5. `E normalized_entity` (if present): 28
6. `F confidence` (if present): 14
7. `G notes` (if present): 32

---

## 12. Reliability, Fallback, and Error Handling (Retain and Upgrade)

## 12.1 Vision Pipeline
1. Preserve existing retries and retry-after parsing.
2. Preserve transient error detection and backoff bounds.
3. Preserve periodic status emission and elapsed timer behavior.
4. Upgrade:
   1. Separate status context for Doc A and Doc B in comparison flow.
   2. Recoverable error messages should guide next action.

## 12.2 Chat Pipeline
1. Preserve prompt-too-long detection/backoff.
2. Add structured JSON parsing retries:
   1. Retry on invalid JSON
   2. Retry on schema mismatch
3. Add deterministic fallback outputs when model formatting fails.

## 12.3 Workflow Errors
1. Explicit invalid-state guardrails:
   1. Missing doc B in comparison
   2. Missing entity list in manual entity mode
2. User-facing messages must be actionable and concise.
3. `/cancel` should reset state safely from any step.

## 12.4 Logging and Security
1. Keep current sensitive redaction filter.
2. Do not log raw full OCR text at INFO level.
3. Add structured logs for workflow transitions and failure points.

---

## 13. Chronological Implementation Plan

## Phase 0: Baseline Lock and Safety Net
1. Snapshot current behavior with smoke checklist.
2. Add/refresh tests that assert existing OCR module behavior.
3. Define release branch and rollback checkpoints.

Deliverables:
1. Baseline test checklist.
2. Non-regression guardrails documented in code comments/tests.

## Phase 1: Core Workflow Infrastructure
1. Introduce module selector UI and routing.
2. Extend session model for multi-step workflow state.
3. Add `/cancel` command and unified state reset helper.
4. Keep existing feature buttons for complete extraction path.

Deliverables:
1. Module router.
2. State transition map.
3. Reset/cancel behavior.

## Phase 2: OCR Artifact and Page Mapping Foundation
1. Extend OCR parse pipeline to retain page-level artifacts where possible.
2. Keep `full_text` output compatibility for existing features.
3. Add fallback page mapping if upstream output is not page-labeled.

Deliverables:
1. `DocumentArtifact` structure with `pages`.
2. Backward-compatible use in existing extraction path.

## Phase 3: Document Comparison Module
1. Build comparison workflow prompts and handlers:
   1. Receive A
   2. Receive B
   3. Receive level
2. Implement segmentation and hierarchy parsing.
3. Implement alignment and changed-row generator.
4. Implement per-row semantic summary generation with strict schema.
5. Build XLSX export with required formatting.
6. Send single spreadsheet file in chat and post-run next action prompt.

Deliverables:
1. End-to-end comparison flow.
2. Formatted comparison workbook.

## Phase 4: Entity Extraction Module
1. Build workflow prompts and handlers:
   1. Receive document
   2. Receive mode
   3. Receive entities (manual) or infer entities (AI mode)
2. Implement extraction pipeline with strict JSON schema validation.
3. Build XLSX export with required formatting.
4. Send single spreadsheet file in chat and post-run next action prompt.

Deliverables:
1. End-to-end entity extraction flow.
2. Formatted entity workbook.

## Phase 5: Reliability and UX Enhancement Pass
1. Ensure all long operations emit progress statuses and elapsed counters.
2. Ensure retry/fallback paths are wired in all new model calls.
3. Harden error messages and invalid-state recovery.
4. Confirm emoji-rich status style consistency.

Deliverables:
1. Non-regression UX parity.
2. Upgraded fallback and retry coverage.

## Phase 6: QA, Release Readiness, and Deployment
1. Add/expand tests:
   1. unit tests
   2. workflow tests
   3. export formatting tests
2. Run compile checks and docker build checks.
3. Execute manual UAT scenarios for all three modules.
4. Deploy via existing CI/CD path and monitor early runs.

Deliverables:
1. Test evidence.
2. Release checklist signoff.

---

## 14. Testing Strategy

## 14.1 Unit Tests
1. Entity list parser (`;` split, trim, dedupe, empty handling).
2. State machine transitions for each module.
3. Segmentation and hierarchy parser at each granularity.
4. Diff classifier and changed-row filters.
5. Pydantic schema validation for model responses.
6. Spreadsheet formatting asserts:
   1. freeze pane set
   2. expected header fill/font
   3. column widths match constants

## 14.2 Integration Tests (Mock External APIs)
1. Comparison flow happy path.
2. Comparison flow with chat JSON parse failures and retries.
3. Entity flow manual mode.
4. Entity flow AI mode.
5. Invalid input handling and `/cancel`.

## 14.3 Manual UAT Scenarios
1. Existing complete extraction flow unchanged.
2. Comparison with:
   1. minor edit
   2. section reorder
   3. large document pair
3. Entity extraction:
   1. explicit entities
   2. AI decide
4. Network/transient failure simulation for retry validation.

---

## 15. Operational and Deployment Plan

## 15.1 CI Additions
1. Keep current compile and docker build checks.
2. Add pytest step for new test suite.
3. Fail pipeline on formatting contract regressions.

## 15.2 CD and Rollback
1. Reuse existing OCI SSH deploy workflow.
2. Keep previous image tags for rollback.
3. Validate post-deploy health with smoke script:
   1. `/start`
   2. module selector visible
   3. at least one run per module.

---

## 16. Risks and Mitigations

## 16.1 Risk: Page Number Fidelity from OCR Output Variants
Mitigation:
1. Parse from structured outputs when available.
2. Fallback page inference heuristics.
3. Mark unresolved values as `Unknown` rather than guessing silently.

## 16.2 Risk: LLM JSON Drift
Mitigation:
1. Strict `pydantic` validation.
2. Retry with tighter prompt.
3. Deterministic fallback summaries/rows.

## 16.3 Risk: Token/Latency for Large Comparisons
Mitigation:
1. Batch summarization with dynamic size reduction.
2. Chunk-level processing.
3. Cap text lengths and retain useful context.

## 16.4 Risk: Regression in Existing OCR UX
Mitigation:
1. Non-regression tests before and after each phase.
2. Preserve legacy action path untouched until final integration.

---

## 17. Detailed Acceptance Criteria

## 17.1 General
1. User sees module selector on entry.
2. User can cancel any active flow with `/cancel`.
3. Progress status appears for all long-running stages.
4. No sensitive tokens/keys in logs.

## 17.2 Complete Text Extraction
1. Existing commands and outputs behave as before.
2. Existing retries/backoffs remain active.
3. Existing action keyboard and "What next?" remain available.

## 17.3 Document Comparison
1. Requires two documents and one comparison level.
2. Generates one XLSX with required columns.
3. Includes changed rows only.
4. Each changed row has exactly 3 semantic summary points.
5. Top row frozen, header colored, fixed column widths applied.

## 17.4 Entity Extraction
1. Accepts manual or AI-decide entity mode.
2. Generates one XLSX with required tabular output.
3. Includes evidence snippet and page number per row where available.
4. Top row frozen, header colored, fixed column widths applied.

---

## 18. Implementation Checklist (Agent Execution)
1. Add dependency specs (`openpyxl`, `pydantic`) and update lock/install docs.
2. Implement module selector and stateful router.
3. Preserve existing extraction flow without behavioral regressions.
4. Add OCR page artifact support and compatibility fallback.
5. Build comparison engine and comparison workbook export.
6. Build entity engine and entity workbook export.
7. Add strict model contracts and retries for structured outputs.
8. Add tests and run compile/build/test pipeline.
9. Run manual UAT and capture outputs.
10. Deploy to OCI and monitor.

---

## 19. Post-Release Enhancements (Not in Current Scope)
1. Persistent storage for sessions and run history.
2. Download link retention for generated files.
3. Advanced diff visualizations and color-highlighted changed tokens in sheet.
4. Configurable output templates per domain (legal/procurement/finance).
5. Controlled migration to latest Telegram library major if deferred.

