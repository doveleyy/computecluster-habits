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


def test_stylesheets_take_every_value_from_the_tokens() -> None:
    names, tokens = known_variables(), set(design_tokens())
    found = [
        f"{name}: {problem}"
        for name, css in sheets().items()
        for problem in violations(css, names, tokens)
    ]
    assert found == []


def test_scripts_write_no_colour_and_read_only_declared_tokens() -> None:
    names = known_variables()
    found = []
    for name, source in scripts().items():
        found += [
            f"{name}: colour literal {m.group(0).strip()!r}"
            for m in SCRIPT_COLOUR.finditer(source)
        ]
        found += [
            f"{name}: {variable} is not declared"
            for variable in SCRIPT_VARIABLE.findall(source)
            if variable not in names
        ]
    assert found == []


def test_templates_carry_no_inline_style_and_load_tokens_first() -> None:
    for path in sorted(TEMPLATES.glob("*.html")):
        html = path.read_text()
        assert "style=" not in html and "<style" not in html, (
            f"{path.name}: inline style"
        )
        assert not re.findall(r'(?:fill|stroke|color)="#', html), (
            f"{path.name}: colour attribute"
        )
        links = re.findall(r'href="__BASE_PATH__/static/([\w-]+)\.css', html)
        assert links[0] == "tokens", (
            f"{path.name}: tokens.css loads first, not {links[0]}"
        )
        theme = re.search(r'name="theme-color" content="(#[0-9a-f]{6})"', html)
        assert theme and theme.group(1) == design_tokens()["--bg"], (
            f"{path.name}: theme-color"
        )


def test_the_checker_flags_each_kind_of_violation() -> None:
    known, tokens = (
        {"--space-1", "--text-1", "--bg", "--accent"},
        {"--space-1", "--bg", "--accent"},
    )
    cases = {
        ".a{padding:5px}": "--space-*",
        ".a{margin:0 calc(-4px)}": "--space-*",
        ".a{gap:var(--space-1) 3px}": "--space-*",
        ".a{inset:0 0 24px 0}": "--space-*",
        ".a{font-size:13px}": "--text-*",
        ".a{font-size:clamp(1px,2vw,3px)}": "--text-*",
        ".a{font:600 9px var(--mono)}": "--text-*",
        ".a{border-radius:6px}": "--radius-*",
        ".a{border-radius:50%}": "--radius-*",
        ".a{letter-spacing:.1em}": "--track-*",
        ".a{color:#fff}": "token",
        ".a{background:rgba(0,0,0,.5)}": "token",
        ".a{color:var(--nope)}": "not declared",
        ".a{--bg:#000}": "token",
        ".a{--space-1:5px}": "only tokens.css",
        ":root{--accent:red}": "--accent",
        "@media(max-width:700px){.a{top:0}}": "not a breakpoint",
        "@media(min-width:1000px){.a{top:0}}": "not a breakpoint",
    }
    for css, expected in cases.items():
        found = violations(css, known, tokens)
        assert any(expected in problem for problem in found), (css, found)
    clean = (
        ".a{padding:0 var(--space-1);margin:calc(-1 * var(--space-1)) auto;"
        "font:600 var(--text-1)/1 x;"
        "letter-spacing:normal;color:var(--accent);border-radius:var(--radius-1)}"
        "@media(max-width:840px){.a{top:0}}@media(prefers-reduced-motion:reduce){.a{top:0}}"
    )
    assert violations(clean, known | {"--radius-1"}, tokens) == []
