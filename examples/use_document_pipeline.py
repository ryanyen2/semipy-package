from __future__ import annotations

from typing import Any
from pathlib import Path

from semipy import semiformal, semi
from semipy.agents.config import configure
from semipy.domain_models import Document, ExtractionSchema


class DocumentPipeline:
    def __init__(self, document_type: str, domain: str):
        self.document_type = document_type
        self.domain = domain

        # Standalone semi(): this is generated once per pipeline instance and then cached.
        self.schema: ExtractionSchema = semi(
            f"ExtractionSchema for '{document_type}' documents in the '{domain}' domain. "
            "Return an ExtractionSchema with fields, field_types, required, and description.",
            expected_type=ExtractionSchema,
        )

    @semiformal
    def classify_block(self, block: dict[str, Any], document: Document) -> str:
        block_type = str(block.get("type", "") or "")
        content = str(block.get("content", "") or "")
        is_early = bool(document.blocks and document.blocks[0] is block)

        #> Classify this document block into one category.
        #> Available categories: "header", "body_text", "data_table", "figure_caption", "reference_list", "metadata_field", "skip"
        #> Use: block["type"] hint, content length, formatting cues (ALL CAPS → header, starts with digit+dot → list item, pipe-separated → table row), position in document (early blocks more likely metadata/header).
        #> Return exactly one category string.
        return category

    @semiformal
    def extract_fields(self, block: dict[str, Any], classification: str) -> dict[str, Any]:
        content = str(block.get("content", "") or "")
        allowed_fields = set(self.schema.fields)

        #> Extract field values from block["content"] for document type "{self.document_type}".
        #> Only extract fields relevant to classification="{classification}".
        #> For each field in self.schema.fields that is extractable from this block type:
        #>   - Apply type coercion from self.schema.field_types[field]
        #>   - Use regex, heuristics, or pattern matching appropriate for the domain
        #>   - If field not present in this block, omit it (partial extraction is fine)
        #> Never fabricate values; leave field absent if not clearly present.
        # formal checks (must preserve semantics):
        assert isinstance(result, dict)
        assert all(k in allowed_fields for k in result)
        return result

    @semiformal
    def reconcile_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # formal normalization:
        assert isinstance(records, list)
        assert all(isinstance(r, dict) for r in records)

        #> Merge and deduplicate extracted records. Records from different blocks
        #> may contain overlapping fields for the same entity.
        #> Merge strategy: for each unique entity (identified by required fields),
        #>   combine non-None values, prefer more specific over more general.
        #> Return one record per distinct entity in self.schema.required key space.
        assert isinstance(merged_records, list)
        assert all(isinstance(r, dict) for r in merged_records)
        return merged_records

    def process(self, document: Document) -> list[dict[str, Any]]:
        raw_records: list[dict[str, Any]] = []

        for block in document.blocks:
            classification = self.classify_block(block, document)

            should_extract: bool = semi(
                f"should a '{classification}' block contribute to '{self.document_type}' extraction "
                f"for schema fields {self.schema.fields[:4]}?",
                expected_type=bool,
            )
            if not should_extract:
                continue

            fields = self.extract_fields(block, classification)
            if fields:
                raw_records.append(fields)

        all_found = {k for rec in raw_records for k in rec}
        missing = set(self.schema.required) - all_found
        if missing:
            raise ValueError(f"Required fields never found: {missing}")

        return self.reconcile_records(raw_records)


def main() -> None:
    # Use a dedicated cache dir so we can inspect generated slots afterwards.
    configure(
        cache_dir=Path(".semiformal_doc_pipeline_v2"),
        verbose=True,
        enable_execution_test=True,
        max_retries=2,
    )

    pipeline = DocumentPipeline(document_type="employment_contract", domain="employment_law")

    document = Document(
        text="",
        metadata={},
        blocks=[
            {"type": "header", "content": "EMPLOYMENT AGREEMENT", "bbox": None},
            {
                "type": "metadata_field",
                "content": "Effective Date: 2024-01-15\nJurisdiction: California",
                "bbox": None,
            },
            {
                "type": "body_text",
                "content": "Parties: Alice Johnson (Employer) and Bob Lee (Employee).",
                "bbox": None,
            },
            {
                "type": "body_text",
                "content": "Clauses: confidentiality; non-compete; termination for cause.",
                "bbox": None,
            },
            {"type": "skip", "content": "Random footer text", "bbox": None},
        ],
    )

    try:
        records = pipeline.process(document)
        print("Extracted records:", records)
    except Exception as e:
        print("Pipeline execution failed (expected if an extraction slot misses required fields):", e)


if __name__ == "__main__":
    main()

