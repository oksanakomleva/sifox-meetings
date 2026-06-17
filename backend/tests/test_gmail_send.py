"""Unit tests for the protocol-email Markdown→HTML converter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.gmail_send import markdown_to_html


class TestMarkdownToHtml:
    def test_headings(self):
        html = markdown_to_html("## Участники\n### Подраздел")
        assert "<h2>Участники</h2>" in html
        assert "<h3>Подраздел</h3>" in html

    def test_bullets(self):
        html = markdown_to_html("- первый\n- второй")
        assert "<ul>" in html and "</ul>" in html
        assert "<li>первый</li>" in html
        assert "<li>второй</li>" in html

    def test_numbered_list(self):
        html = markdown_to_html("1. раз\n2. два")
        assert "<ol>" in html and "</ol>" in html
        assert "<li>раз</li>" in html

    def test_bold(self):
        html = markdown_to_html("Решение: **сделать**")
        assert "<strong>сделать</strong>" in html

    def test_paragraph(self):
        html = markdown_to_html("Просто текст.")
        assert "<p>Просто текст.</p>" in html

    def test_html_is_escaped(self):
        html = markdown_to_html("a < b & c > d <script>x</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_blank_line_closes_list(self):
        html = markdown_to_html("- a\n\nтекст")
        # The list must be closed before the paragraph.
        assert html.index("</ul>") < html.index("<p>текст</p>")

    def test_empty_input(self):
        # Should not raise and produces a wrapper div.
        assert "<div" in markdown_to_html("")
