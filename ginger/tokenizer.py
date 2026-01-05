from dataclasses import dataclass
from typing import List

@dataclass(frozen=True)
class Token:
    kind: str   # KW, IDENT, SYM, INT, FLOAT, EOF, NEWLINE
    text: str
    pos: int

KEYWORDS = {
    "guarantee", "typegroup", "register", "impl",
    "func", "sig", "require","failure", "return",
    "guarantees", "in",
    "builtin",
    "try", "catch",
    "let","var",
}

SYMBOLS_1 = set("{}():,=@.+-*/")  # one-char
# special: "->" and "|"

def tokenize(src: str) -> List[Token]:

    toks: List[Token] = []
    i, n = 0, len(src)

    def peek(k: int = 0) -> str:
        return src[i + k] if i + k < n else "\0"

    while i < n:
        c = src[i]

        # newline
        if c == "\n":
            toks.append(Token("NEWLINE", "\\n", i))
            i += 1
            continue

        # whitespace
        if c.isspace():
            i += 1
            continue

        # line comment //
        if c == "/" and peek(1) == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue

        # two-char symbol ->
        if c == "-" and peek(1) == ">":
            toks.append(Token("SYM", "->", i))
            i += 2
            continue

        # union pipe |
        if c == "|":
            toks.append(Token("SYM", "|", i))
            i += 1
            continue

        # one-char symbols
        if c in SYMBOLS_1:
            toks.append(Token("SYM", c, i))
            i += 1
            continue

        # number: int or float
        # rule:
        #   - default Int
        #   - if '.' appears, it must be followed by at least one digit => Float
        #   - disallow "1." (enforce "1.0" style)
        if c.isdigit():
            start = i
            while peek().isdigit():
                i += 1

            if peek() == ".":
                # enforce at least one digit after '.'
                if not peek(1).isdigit():
                    # e.g. "1." -> error (don't let it become INT + '.')
                    raise SyntaxError(
                        f"Float literal requires digits after '.' (use '{src[start:i]}.0') at {start}"
                    )

                i += 1  # consume '.'
                while peek().isdigit():
                    i += 1
                toks.append(Token("FLOAT", src[start:i], start))
                continue

            toks.append(Token("INT", src[start:i], start))
            continue

        # number: int or float
        # rule: default Int. If there's a '.', it must be followed by at least one digit => Float.
        # disallow "1." to enforce "1.0" style and avoid INT + '.' splitting.
        """if c.isdigit():
            start = i
            while peek().isdigit():
                i += 1

            # float?
            if peek() == ".":
                # "1." is not allowed (enforce 1.0, 0.1, etc.)
                if not peek(1).isdigit():
                    raise SyntaxError(
                        f"Float literal requires digits after '.' (use '{src[start:i]}.0') at {start}"
                    )

                i += 1  # consume '.'
                while peek().isdigit():
                    i += 1
                toks.append(Token("FLOAT", src[start:i], start))
                continue

            toks.append(Token("INT", src[start:i], start))
            continue"""


        # number: int or float
        """
        if c.isdigit():
            start = i
            while peek().isdigit():
                i += 1
            if peek() == "." and peek(1).isdigit():
                i += 1
                while peek().isdigit():
                    i += 1
                toks.append(Token("FLOAT", src[start:i], start))
            else:
                toks.append(Token("INT", src[start:i], start))
            continue
        """

        # identifier / keyword
        if c.isalpha() or c == "_":
            start = i
            i += 1
            while peek().isalnum() or peek() in "_":
                i += 1
            text = src[start:i]
            kind = "KW" if text in KEYWORDS else "IDENT"
            toks.append(Token(kind, text, start))
            continue

        raise SyntaxError(f"Unexpected character '{c}' at {i}")

    toks.append(Token("EOF", "", n))
    return toks