"""Trusted AST fixtures for implementation/contract tests, not Ginger source.

The legacy declaration notation here is only a compact fixture serialization.
Public-source rejection is tested separately against ginger.parser/pipeline.
The production Parser and catalog remain unchanged. The compile wrapper only
substitutes this fixture reader for pipeline.parse during fixture compilation.
"""
from unittest.mock import patch

from ginger import pipeline
from ginger.parser import Parser
from ginger.tokenizer import tokenize
from ginger.pipeline import execute


class _InternalDeclarationParser(Parser):
    def reject_source_builtin(self):
        # Tests deliberately construct the same trusted AST as the catalog loader.
        pass


def parse(source):
    return _InternalDeclarationParser(tokenize(source)).parse_program()


def compile(source):
    with patch('ginger.pipeline.parse', side_effect=parse):
        return pipeline.compile(source)


def run(source):
    return pipeline.execute(compile(source))
