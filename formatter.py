"""
Turns clipboard HTML into a list of (text, (bold, italic, underline)) runs,
so the typer can reproduce formatting with Ctrl+B / Ctrl+I / Ctrl+U.
Pure Python, no third-party packages.
"""
import re
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "head", "title"}
VOID = {"br", "img", "hr", "meta", "link", "input", "col", "wbr", "area", "base", "source"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK = HEADINGS | {"p", "div", "li", "tr", "blockquote", "pre", "section", "article",
                    "header", "footer", "ul", "ol", "table", "dl", "dt", "dd", "figure"}
PLAIN = (False, False, False)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ops = []      # [text, (b, i, u)]
        self.stack = []    # (tag, b, i, u)
        self.skip = 0
        self.pre = 0
        self.lists = []    # [tag, counter]
        self.last = ""

    def cur(self):
        return self.stack[-1][1:] if self.stack else PLAIN

    def _emit(self, text, fmt):
        if not text:
            return
        self.ops.append([text, fmt])
        self.last = text[-1]

    def _newline(self):
        if self.last in ("", "\n"):
            return
        if self.ops and self.ops[-1][0].endswith(" "):
            self.ops[-1][0] = self.ops[-1][0].rstrip(" ")
        self._emit("\n", self.cur())

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        style = (dict(attrs).get("style") or "").lower()
        b, i, u = self.cur()
        if tag in ("b", "strong") or tag in HEADINGS:
            b = True
        if tag in ("i", "em", "cite", "dfn", "var"):
            i = True
        if tag in ("u", "ins"):
            u = True
        m = re.search(r"font-weight\s*:\s*([a-z0-9]+)", style)
        if m:
            v = m.group(1)
            b = v in ("bold", "bolder") or (v.isdigit() and int(v) >= 600)
        m = re.search(r"font-style\s*:\s*([a-z]+)", style)
        if m:
            i = m.group(1) in ("italic", "oblique")
        m = re.search(r"text-decoration(?:-line)?\s*:\s*([^;]+)", style)
        if m:
            u = "underline" in m.group(1)

        if tag == "br":
            self._emit("\n", self.cur())
            return
        if tag in VOID:
            return
        if tag in ("ul", "ol"):
            self._newline()
            self.lists.append([tag, 0])
        elif tag in BLOCK:
            self._newline()
        if tag == "pre":
            self.pre += 1
        self.stack.append((tag, b, i, u))
        if tag == "li":
            if self.lists:
                lst = self.lists[-1]
                lst[1] += 1
                prefix = "\u2022 " if lst[0] == "ul" else f"{lst[1]}. "
            else:
                prefix = "\u2022 "
            self._emit(prefix, PLAIN)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                del self.stack[idx:]
                break
        else:
            return
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()
        if tag in BLOCK:
            self._newline()

    def handle_data(self, data):
        if self.skip:
            return
        data = data.replace("\xa0", " ")
        if self.pre:
            data = data.replace("\r\n", "\n")
        else:
            data = re.sub(r"\s+", " ", data)
            if self.last in ("", "\n"):
                data = data.lstrip()
        self._emit(data, self.cur())


def html_to_ops(fragment: str):
    p = _Parser()
    p.feed(fragment)
    p.close()
    ops = [(t, f) for t, f in p.ops]
    while ops:                       # trim trailing blank space
        t = ops[-1][0].rstrip(" \n")
        if t:
            ops[-1] = (t, ops[-1][1])
            break
        ops.pop()
    merged = []
    for t, f in ops:
        if merged and merged[-1][1] == f:
            merged[-1] = (merged[-1][0] + t, f)
        else:
            merged.append((t, f))
    return merged


def has_formatting(ops) -> bool:
    return any(any(f) for _, f in ops)


def describe(ops) -> str:
    names = []
    for idx, name in enumerate(("bold", "italic", "underline")):
        if any(f[idx] for _, f in ops):
            names.append(name)
    return ", ".join(names)


def extract_fragment(raw: bytes) -> str:
    """Pull the HTML fragment out of the Windows 'HTML Format' clipboard data."""
    m1 = re.search(rb"StartFragment:(\d+)", raw)
    m2 = re.search(rb"EndFragment:(\d+)", raw)
    if m1 and m2:
        a, b = int(m1.group(1)), int(m2.group(1))
        if 0 <= a < b <= len(raw):
            return raw[a:b].decode("utf-8", "replace")
    s = raw.decode("utf-8", "replace")
    m = re.search(r"<!--StartFragment-->(.*)<!--EndFragment-->", s, re.S)
    return m.group(1) if m else s
