"""A stand-in for VS Code used by the end-to-end tests.

It implements the driver's backend interface on top of the editor model, plus
the few workbench features a session uses: Quick Open (Ctrl+P), Go to Line
(Ctrl+G), the clipboard and Save.
"""
from ghostwriter.editor import VirtualEditor


class FakeVSCode:
    def __init__(self, editors: dict):
        self.editors = editors            # name -> VirtualEditor
        self.active = next(iter(editors)) if editors else None
        self.mode = "editor"
        self.entry = ""
        self.clipboard = ""
        self.saved: list[str] = []
        self.opened: list[str] = []

    @property
    def ed(self) -> VirtualEditor:
        return self.editors[self.active]

    def type_char(self, ch):
        if self.mode == "editor":
            self.ed.type_char(ch)
        else:
            self.entry += ch

    def key(self, name):
        if self.mode == "quickopen":
            if name == "enter":
                assert self.entry in self.editors, f"Quick Open: no file {self.entry!r}"
                self.active = self.entry
                self.opened.append(self.entry)
                self.mode = "editor"
            elif name == "escape":
                self.mode = "editor"
            return
        if self.mode == "goto":
            if name == "enter":
                self.ed.goto_line(int(self.entry))
            self.mode = "editor"
            return
        if name == "ctrl+p":
            self.mode, self.entry = "quickopen", ""
        elif name == "ctrl+g":
            self.mode, self.entry = "goto", ""
        elif name == "ctrl+c":
            if self.ed.has_selection():
                self.clipboard = self.ed.selected_text()
        elif name == "ctrl+v":
            self.ed.paste(self.clipboard)
        elif name == "ctrl+s":
            self.saved.append(self.active)
        else:
            self.ed.key(name)

    def set_clipboard(self, text):
        self.clipboard = text

    def get_clipboard(self):
        return self.clipboard

    def check_failsafe(self):
        pass
