"""The editor model must react to keys the way VS Code does."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import re
import unittest

from ghostwriter.editor import EditorSettings, VirtualEditor


def run(lang, keys, **settings):
    ed = VirtualEditor(lang, EditorSettings(**settings))
    names = "enter|tab|backspace|delete|escape|left|right|up|down|home|end"
    for token in re.findall(rf"<(?:shift\+|ctrl\+)?(?:{names})>|.", keys, re.S):
        if len(token) > 1:
            ed.key(token[1:-1])
        else:
            ed.type_char(token)
    return ed


class AutoClosing(unittest.TestCase):
    def test_bracket_is_closed_and_typed_over(self):
        ed = run("python", "print(")
        self.assertEqual((ed.text(), ed.col), ("print()", 6))
        ed.type_char(")")
        self.assertEqual((ed.text(), ed.col), ("print()", 7))

    def test_quote_not_closed_after_word_or_inside_string(self):
        self.assertEqual(run("python", "don't").text(), "don't")
        self.assertEqual(run("python", 'x = "it\'s"').text(), 'x = "it\'s"')

    def test_quote_closed_inside_fstring_expression(self):
        self.assertEqual(run("python", "f\"{'").text(), "f\"{''}\"")

    def test_not_closed_before_a_word(self):
        ed = run("javascript", "foo<home>(")
        self.assertEqual(ed.text(), "(foo")

    def test_never(self):
        self.assertEqual(run("python", "f(", auto_closing_brackets="never").text(), "f(")

    def test_backspace_removes_the_pair(self):
        self.assertEqual(run("javascript", "a(<backspace>").text(), "a")

    def test_typing_over_stops_after_a_line_break(self):
        self.assertEqual(run("python", "x = [1<enter>]").text(), "x = [1\n]]")

    def test_jsdoc_pair(self):
        self.assertEqual(run("javascript", "/**").text(), "/** */")

    def test_html_comment_pair(self):
        self.assertEqual(run("html", "<!--").text(), "<!---->")

    def test_triple_quote_is_not_closed(self):
        self.assertEqual(run("python", '"""').text(), '"""')


class Enter(unittest.TestCase):
    def test_python_block(self):
        ed = run("python", "def f():<enter>")
        self.assertEqual((ed.text(), ed.col), ("def f():\n    ", 4))

    def test_python_match_is_not_an_indent_keyword(self):
        self.assertEqual(run("python", "match x:<enter>").text(), "match x:\n")

    def test_between_brackets(self):
        ed = run("javascript", "f() {<enter>")
        self.assertEqual((ed.text(), ed.pos), ("f() {\n    \n}", (1, 4)))

    def test_keep_mode_only_copies_indentation(self):
        self.assertEqual(run("python", "    if x:<enter>", auto_indent="keep").text(), "    if x:\n    ")

    def test_none(self):
        self.assertEqual(run("python", "    x<enter>", auto_indent="none").text(), "    x\n")

    def test_jsdoc_continuation(self):
        ed = run("javascript", "/**<enter>a<enter>b")
        self.assertEqual(ed.text(), "/**\n * a\n * b\n */")

    def test_js_case(self):
        self.assertEqual(run("javascript", "case 1:<enter>x").text(), "case 1:\n    x")

    def test_js_single_statement_if_under_full(self):
        self.assertEqual(run("javascript", "if (a)<enter>b();<enter>c").text(), "if (a)\n    b();\nc")
        self.assertEqual(run("javascript", "if (a)<enter>b", auto_indent="advanced").text(), "if (a)\nb")

    def test_html_tag(self):
        self.assertEqual(run("html", "<div><enter>x").text(), "<div>\n    x")
        self.assertEqual(run("html", "<br><enter>x").text(), "<br>\nx")

    def test_blank_line_whitespace_is_trimmed(self):
        ed = run("python", "if x:<enter>a<enter><enter>b")
        self.assertEqual(ed.text(), "if x:\n    a\n\n    b")

    def test_tabs(self):
        self.assertEqual(run("python", "if x:<enter>a", insert_spaces=False).text(), "if x:\n\ta")


class Editing(unittest.TestCase):
    def test_backspace_uses_tab_stops_in_indentation(self):
        ed = run("python", "if x:<enter>if y:<enter><backspace>")
        self.assertEqual(ed.line, "    ")
        ed.key("backspace")
        self.assertEqual(ed.line, "")

    def test_electric_closing_bracket(self):
        ed = run("python", "x = [<delete><enter>1,<enter>]", auto_closing_brackets="never")
        self.assertEqual(ed.text(), "x = [\n    1,\n]")

    def test_selection_and_replace(self):
        ed = run("python", "total = 1<shift+left><backspace>2")
        self.assertEqual(ed.text(), "total = 2")
        ed = run("python", "    abc<home><shift+end>")
        self.assertEqual(ed.selected_text(), "abc")

    def test_smart_home(self):
        ed = run("python", "    abc<home>")
        self.assertEqual(ed.col, 4)
        ed.key("home")
        self.assertEqual(ed.col, 0)

    def test_vertical_movement_remembers_the_column(self):
        ed = run("python", "abcdef<enter>x<enter>abcdef<up><up>")
        self.assertEqual(ed.pos, (0, 6))

    def test_tab_on_blank_line_jumps_to_the_expected_indent(self):
        ed = run("python", "if x:<enter><backspace><tab>")
        self.assertEqual(ed.line, "    ")


if __name__ == "__main__":
    unittest.main()
