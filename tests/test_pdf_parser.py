from __future__ import annotations

import unittest

import fitz

from claimtrace.exceptions import PDFParsingError
from claimtrace.models import SourceKind
from claimtrace.pdf_parser import build_source_packet, parse_pdf


def _make_searchable_pdf() -> bytes:
    document = fitz.open()

    page_one = document.new_page()
    page_one.insert_text((72, 72), "A synthetic biomedical study")
    page_one.insert_text((72, 120), "Abstract")
    page_one.insert_text(
        (72, 155),
        "Treatment reduced the endpoint relative to vehicle (n = 12; p = 0.01).",
    )

    page_two = document.new_page()
    page_two.insert_text((72, 72), "Methods")
    page_two.insert_text(
        (72, 120),
        "Animals were randomized to vehicle or treatment and assessed by a blinded reader.",
    )

    page_three = document.new_page()
    page_three.insert_text(
        (72, 72),
        "Figure 1. Treatment response over time. Points show mean and error bars.",
    )
    page_three.insert_text(
        (72, 130),
        "Table 1. Prespecified primary and secondary endpoint summaries.",
    )

    payload = document.tobytes()
    document.close()
    return payload


class PDFParserTests(unittest.TestCase):
    def test_preserves_page_numbers_and_source_ids(self) -> None:
        parsed = parse_pdf(_make_searchable_pdf(), "example.pdf")

        self.assertEqual(parsed.page_count, 3)
        self.assertEqual(parsed.filename, "example.pdf")
        self.assertTrue(parsed.sha256)
        self.assertEqual(
            {source.page_number for source in parsed.sources},
            {1, 2, 3},
        )
        self.assertTrue(all(source.source_id.startswith("p") for source in parsed.sources))
        self.assertEqual(len(parsed.sources), len(parsed.source_index))

    def test_detects_figure_table_method_and_statistical_sources(self) -> None:
        parsed = parse_pdf(_make_searchable_pdf())
        kinds = {source.kind for source in parsed.sources}

        self.assertIn(SourceKind.FIGURE, kinds)
        self.assertIn(SourceKind.TABLE, kinds)
        self.assertIn(SourceKind.METHOD, kinds)
        self.assertIn(SourceKind.STATISTICAL_RESULT, kinds)

    def test_source_packet_contains_page_and_exact_excerpt(self) -> None:
        parsed = parse_pdf(_make_searchable_pdf())
        packet = build_source_packet(parsed)
        first = parsed.sources[0]

        self.assertIn(first.source_id, packet)
        self.assertIn(f"page={first.page_number}", packet)
        self.assertIn(first.excerpt, packet)

    def test_rejects_non_pdf_bytes(self) -> None:
        with self.assertRaises(PDFParsingError):
            parse_pdf(b"not a pdf")

    def test_rejects_image_only_pdf_without_ocr(self) -> None:
        document = fitz.open()
        document.new_page()
        payload = document.tobytes()
        document.close()

        with self.assertRaisesRegex(PDFParsingError, "No extractable text"):
            parse_pdf(payload)


if __name__ == "__main__":
    unittest.main()

