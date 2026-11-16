"""Enforce DESIGN.md: every value in a stylesheet comes from tokens.css.

DESIGN.md holds the token tables, `tokens.css` declares them, and the stylesheets
read them. These tests fail when a table and `tokens.css` disagree, or when a
stylesheet or script sets a value the tables should supply.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "DESIGN.md"
STATIC = ROOT / "services/habit_tracker/static"
TEMPLATES = ROOT / "services/habit_tracker/templates"
TOKENS = STATIC / "tokens.css"

COMMENT = re.compile(r"/\*.*?\*/", re.S)
ROW = re.compile(r"^\|[^|]*\|\s*`(--[\w-]+)`\s*\|\s*`([^`]+)`", re.M)
DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;}]+)")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
MEDIA = re.compile(r"@media\s*([^{]+)\{")
WIDTH = re.compile(r"\(\s*(?:max|min)-width\s*:\s*([^)\s]+)\s*\)")
VARIABLE = re.compile(r"var\(\s*(--[\w-]+)")
LENGTH = re.compile(
    r"(?<![\w.-])-?\d*\.?\d+(?:px|rem|em|ch|vh|vw|dvh|svh|lvh|vmin|vmax|%)"
)
COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}(?![\w-])|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\("
)
SPACING = re.compile(
    r"^(?:padding|margin|gap|row-gap|column-gap|inset|top|right|bottom|left)"
    r"(?:-[a-z]+)*$"
)
RADIUS = re.compile(r"^border(?:-[a-z]+)*-radius$")
SCRIPT_COLOUR = re.compile(
    r"""["'`(\s:]#[0-9a-fA-F]{3,8}(?![\w-])|\b(?:rgba?|hsla?)\("""
)
SCRIPT_VARIABLE = re.compile(r"var\(\s*(--[\w-]+)")
LOCAL_VARIABLE = re.compile(
    r"""(?:setProperty\(\s*["']|style="|\$\{|["'`;{]\s*)(--[\w-]+)\s*[:"',]"""
)


def strip(css: str) -> str:
    return COMMENT.sub("", css)


def design_tokens() -> dict[str, str]:
    """Every token DESIGN.md names, with the value its table gives."""
    return {
        name: " ".join(value.split()) for name, value in ROW.findall(DESIGN.read_text())
    }


def declared(css: str) -> dict[str, str]:
    root = re.search(r":root\s*\{([^}]*)\}", strip(css))
    assert root, "tokens.css declares its tokens in :root"
    return {
        name: " ".join(value.split())
        for name, value in DECLARATION.findall(root.group(1))
    }


def breakpoints() -> set[str]:
    section = DESIGN.read_text().split("## Layout", 1)[1].split("Every page uses", 1)[0]
    found = set(re.findall(r"`(\d+px)`", section))
    assert found, "DESIGN.md names its breakpoints in backticks under Layout"
    return found


def rules(css: str) -> list[tuple[str, dict[str, str]]]:
    """Each innermost rule as (selector, {property: value})."""
    result = []
    for selector, body in RULE.findall(strip(css)):
        properties = {}
        for part in body.split(";"):
            if ":" in part:
                name, value = part.split(":", 1)
                properties[name.strip()] = " ".join(value.split())
        result.append((selector.strip(), properties))
    return result


def raw(value: str) -> bool:
    """True when a value carries a length that is not read from a token."""
    return bool(LENGTH.search(re.sub(r"var\([^)]*\)", "", value)))


def violations(css: str, known: set[str], tokens: set[str]) -> list[str]:
    """Everything in one stylesheet that DESIGN.md's Enforcement list forbids."""
    found = []
    for selector, properties in rules(css):
        for name, value in properties.items():
            where = f"{selector} {{ {name}: {value} }}"
            if SPACING.match(name) and raw(value):
                found.append(f"{where}: use a --space-* token")
            if name == "font-size" and raw(value):
                found.append(f"{where}: use a --text-* token")
            if name == "font" and raw(value):
                found.append(f"{where}: use a --text-* token for the size")
            if RADIUS.match(name) and raw(value):
                found.append(f"{where}: use a --radius-* token")
            if name == "letter-spacing" and not (
                value in {"normal", "0"} or value.startswith("var(--track-")
            ):
                found.append(f"{where}: use a --track-* token")
            if COLOUR.search(value):
                found.append(f"{where}: read the colour from a token")
            if name.startswith("--") and name in tokens and name != "--accent":
                found.append(f"{where}: only tokens.css declares {name}")
            if name == "--accent" and not re.fullmatch(r"var\(--[a-z]+\)", value):
                found.append(f"{where}: set --accent to one colour token")
            for variable in VARIABLE.findall(value):
                if variable not in known:
                    found.append(f"{where}: {variable} is not declared")
    allowed = breakpoints()
    for query in MEDIA.findall(strip(css)):
        for width in WIDTH.findall(query):
            if width not in allowed:
                found.append(f"@media {query.strip()}: {width} is not a breakpoint")
    return found


def sheets() -> dict[str, str]:
    return {
        path.name: path.read_text()
        for path in sorted(STATIC.glob("*.css"))
        if path.name != "tokens.css"
    }


def scripts() -> dict[str, str]:
    return {path.name: path.read_text() for path in sorted(STATIC.glob("*.js"))}


def known_variables() -> set[str]:
    """Tokens plus component variables a stylesheet, script or template sets."""
    names = set(declared(TOKENS.read_text()))
    for css in sheets().values():
        names.update(
            name
            for _, properties in rules(css)
            for name in properties
            if name.startswith("--")
        )
    for source in [
        *scripts().values(),
        *(p.read_text() for p in TEMPLATES.glob("*.html")),
    ]:
        names.update(LOCAL_VARIABLE.findall(source))
    return names


def test_tokens_css_declares_exactly_the_tokens_in_design_md() -> None:
    table, css = design_tokens(), declared(TOKENS.read_text())
    assert set(css) == set(table), (
        f"missing from tokens.css: {sorted(set(table) - set(css))}, "
        f"not in DESIGN.md: {sorted(set(css) - set(table))}"
    )
    differing = {
        name: (css[name], table[name]) for name in table if css[name] != table[name]
    }
    assert differing == {}, f"tokens.css differs from DESIGN.md: {differing}"
