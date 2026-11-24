import unittest
from unittest.mock import patch

from ginger.ast import ImplDecl, Program, TypeDecl, TypeRef
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols


class TypeDeclarationTests(unittest.TestCase):
    def test_catalog_and_compilation_scope(self):
        standard = {'Int', 'Int64', 'Float', 'Bool', 'Ordering', 'Slope', 'Unit', 'Thunk'}
        symbols = build_symbols(parse('type Bee'))
        self.assertEqual(set(symbols.types), standard | {'Bee'})
        self.assertEqual(symbols.types['Bee'], TypeDecl('Bee'))
        self.assertEqual(symbols.types['Thunk'].special, 'thunk')
        self.assertTrue(all(symbols.types[name].catalog for name in standard))
        self.assertNotIn('Bee', build_symbols(Program([])).types)

    def test_runtime_and_forward_reference(self):
        source = 'sig identity(Bee) -> Bee {}\ntype Bee'
        self.assertEqual(run(source), run(''))
        symbols = build_symbols(parse(source))
        self.assertNotIn('Bee', symbols.type_guarantees)
        self.assertFalse(any('Bee' in members for members in symbols.typegroups.values()))

    def test_declaration_syntax(self):
        for source in ['type Box[T]', 'type Bee = Int', 'type Bee {}', 'func f() { type Bee }']:
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                parse(source)

    def test_duplicate_and_reserved_names(self):
        for source, diagnostic in [
            ('type Bee\ntype Bee', "duplicate type 'Bee'"),
            ('type Int', "duplicate type 'Int'"),
            ('type Thunk', "duplicate type 'Thunk'"),
            ('type T', 'reserved type variable name'),
            ('type U', 'reserved type variable name'),
            ('type Self', 'reserved type name'),
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, diagnostic):
                compile(source)

    def test_all_type_positions(self):
        for source in [
            'var x: Missing = 1', 'sig f(Missing) -> Unit {}', 'sig f() -> Missing {}',
            'func f(x: Missing) {}', 'func f() { var x: Missing = 1 }',
            'var y: Int = resolve add(1,2) { IntegerOverflow { let x: Missing = 1\nreturn 0 } }',
            'guarantee G { op(x: Missing) -> Self }', 'guarantee G { op(x: Self) -> Missing }',
            'type Box\nsig f(Box[Float[Missing]]) -> Unit {}',
            'sigtype DeferredMissing() -> Missing { failure { Never } }\nsig f() -> Thunk[DeferredMissing] {}',
            'failureset Fs(T) { IOErr }\nsig f() -> Unit { failure { Fs(Missing) } }',
            'sigtype DeferredIntFsMissing() -> Int { failure { Fs(Missing) } }\nfailureset Fs(T) { IOErr }\nsig f() -> Thunk[DeferredIntFsMissing] {}',
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, "unknown type 'Missing'"):
                compile(source)

    def test_internal_declarations(self):
        impl = ImplDecl(TypeRef('Missing'), 'Marker', [])
        with self.assertRaisesRegex(TypecheckError, "unknown type 'Missing'"):
            build_symbols(Program([impl]))
        with patch('ginger.symbols_builder.prelude_items', return_value=[impl]):
            with self.assertRaisesRegex(TypecheckError, "unknown type 'Missing'"):
                build_symbols(Program([]))

    def test_typegroup_conflicts(self):
        for source in ['type Bee\ntypegroup Bee {}', 'typegroup Bee {}\ntype Bee',
                       'typegroup Int {}', 'type BoundedArithmetic']:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'type/typegroup name conflict'):
                compile(source)

    def test_other_namespaces(self):
        compile('type Bee\nguarantee Bee { op(self: Self) -> Self }\n'
                'failureset Bee { IOErr }\nsig Bee() -> Unit {}\nfunc Bee() {}\nlet Bee: Int = 1')

    def test_membership(self):
        for source in ['typegroup G { Bee }', 'register G { Bee }\ntypegroup G {}']:
            symbols = build_symbols(parse(source + '\ntype Bee'))
            self.assertEqual(symbols.typegroups['G'], {'Bee'})
            for prefix in ['', 'typegroup Bee {}\n']:
                with self.subTest(source=source, prefix=prefix), self.assertRaisesRegex(TypecheckError, "unknown type 'Bee'"):
                    compile(prefix + source)
        with self.assertRaisesRegex(TypecheckError, 'duplicate register'):
            compile('type Bee\ntypegroup G { Bee }\nregister G { Bee }')
        with self.assertRaisesRegex(TypecheckError, "unknown type 'Box'"):
            compile('typegroup Box {}\ntypegroup G {}\nregister G { Box[Int] }')

    def test_ordinary_type_arguments(self):
        compile('type Box\nsig f(Box, Box[Int], Box[Int, Float], Float[Int]) -> Unit {}')

    def test_failure_names_in_type_positions(self):
        for name in ['Never', 'IOErr', 'Fs']:
            with self.subTest(name=name), self.assertRaisesRegex(TypecheckError, f"unknown type '{name}'"):
                compile(f'failureset Fs {{ IOErr }}\nsig f() -> {name} {{}}')
        symbols = build_symbols(parse('type Never\nsig f() -> Never { failure { Never } }'))
        self.assertEqual(symbols.sig_failures['f'], frozenset())
