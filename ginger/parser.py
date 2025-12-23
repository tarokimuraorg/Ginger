from typing import List, Optional, Tuple
from .tokenizer import Token, tokenize
from .ast import (
    Program, TopLevel,
    TypeRef, Param, FuncSig,
    GuaranteeDecl, TypeGroupDecl, RegisterDecl,
    ImplDecl, ImplMethod,
    RequireClause, RequireIn, RequireGuarantees,
    FuncDecl, VarDecl,
    Expr, CallExpr, IdentExpr, IntLit, FloatLit,
    Arg, PosArg, NamedArg,
    ExprStmt, TryStmt, CatchStmt,
)

class Parser:

    def __init__(self, toks: List[Token]):
        self.toks = toks
        self.i = 0

    def skip_newlines(self) -> None:
        while self.match("NEWLINE"):
            self.i += 1

    def cur(self) -> Token:
        return self.toks[self.i]

    def match(self, kind: str, text: Optional[str] = None) -> bool:
        t = self.toks[self.i]
        if t.kind != kind:
            return False
        if text is not None and t.text != text:
            return False
        return True

    def eat(self, kind: str, text: Optional[str] = None) -> Token:
        t = self.cur()
        if not self.match(kind, text):
            exp = f"{kind}('{text}')" if text else kind
            raise SyntaxError(f"Expected {exp} but got {t.kind}('{t.text}') at {t.pos}")
        self.i += 1
        return t

    # ---- program ----

    def parse_program(self) -> Program:
        items: List[TopLevel] = []
        while True:
            # 空行はここで全部捨てる(toplevelに入る前)
            while self.match("NEWLINE"):
                self.i += 1

            if self.match("EOF"):
                break

            items.append(self.parse_toplevel())

        return Program(items)

    def parse_toplevel(self) -> TopLevel:
        
        if self.match("EOF"):
            # parse_program の while not EOF に戻る設定
            return ExprStmt(expr=IntLit(0))
        
        # --- catalog/decl ---
        if self.match("KW", "guarantee"):
            return self.parse_guarantee()
        if self.match("KW", "typegroup"):
            return self.parse_typegroup()
        if self.match("KW", "register"):
            return self.parse_register()
        if self.match("KW", "impl"):
            return self.parse_impl()
        if self.match("KW", "func"):
            return self.parse_func()
        
        """
        try print(1) 
        catch PrintErr try print(0) 
        catch breath()
        
        のような構文を禁止する 
        """
        
        # --- try/catch (toplevel statement) ---
        if self.match("KW", "try"):

            self.i += 1
            expr = self.parse_expr()
            return TryStmt(expr=expr)
        
        if self.match("KW", "catch"):

            self.i += 1
            
            if not self.match("IDENT"):
                raise SyntaxError("expected failure name after catch")
            
            failure_name = self.toks[self.i].text
            self.i += 1

            handler_tokens = []

            while not self.match("NEWLINE") and not self.match("EOF"):
                handler_tokens.append(self.toks[self.i])
                self.i += 1
            
            if self.match("NEWLINE"):
                self.i += 1

            if not handler_tokens:
                raise SyntaxError("catch must have a handler expression on the same line")
            
            # ネスト禁止：handlerの先頭が try/catch ならアウト
            if handler_tokens[0].kind == "KW" and handler_tokens[0].text in ("try", "catch"):
                raise SyntaxError("nested try/catch is forbidden in catch body")
            
            sub = Parser(handler_tokens + [Token("EOF", "", handler_tokens[-1].pos)])
            expr = sub.parse_expr()

            return CatchStmt(failure_name=failure_name, expr=expr)
        
        # --- ExprStmt ---
        if self.match("IDENT") and self.toks[self.i + 1].kind == "SYM" and self.toks[self.i + 1].text == "(":
            expr = self.parse_expr()
            return ExprStmt(expr=expr)
        
        return self.parse_var_decl()

    # ---- shared ----

    def parse_type(self) -> TypeRef:
        return TypeRef(self.eat("IDENT").text)

    def parse_params(self) -> List[Param]:
        params: List[Param] = []
        if self.match("SYM", ")"):
            return params
        while True:
            pname = self.eat("IDENT").text
            self.eat("SYM", ":")
            ptype = self.parse_type()
            params.append(Param(pname, ptype))
            if self.match("SYM", ","):
                self.eat("SYM", ",")
                continue
            break
        return params

    # ---- guarantee ----

    def parse_guarantee(self) -> GuaranteeDecl:
        self.eat("KW", "guarantee")
        name = self.eat("IDENT").text
        self.eat("SYM", "{")
        methods: List[FuncSig] = []

        while True:
            self.skip_newlines()
            if self.match("SYM", "}"):
                break
            methods.append(self.parse_method_sig())

        self.eat("SYM", "}")
        return GuaranteeDecl(name=name, methods=methods)

    def parse_method_sig(self) -> FuncSig:
        # add(self: Self, other: Self) -> Self
        fname = self.eat("IDENT").text
        self.eat("SYM", "(")
        params = self.parse_params()
        self.eat("SYM", ")")
        self.eat("SYM", "->")
        ret = self.parse_type()
        return FuncSig(name=fname, params=params, ret=ret)

    # ---- typegroup ----

    def parse_typegroup(self) -> TypeGroupDecl:
        # typegroup Number = Int | Float
        self.eat("KW", "typegroup")
        name = self.eat("IDENT").text
        self.eat("SYM", "=")
        members: List[TypeRef] = [self.parse_type()]
        while self.match("SYM", "|"):
            self.eat("SYM", "|")
            members.append(self.parse_type())
        return TypeGroupDecl(name=name, members=members)

    # ---- register ----

    def parse_register(self) -> RegisterDecl:
        # register Int guarantees Addable
        self.eat("KW", "register")
        typ = self.parse_type()
        self.eat("KW", "guarantees")
        gname = self.eat("IDENT").text
        return RegisterDecl(typ=typ, guarantee=gname)

    # ---- impl ----

    def parse_impl(self) -> ImplDecl:
        # impl Int guarantees Addable { add = builtin core.int.add }
        self.eat("KW", "impl")
        typ = self.parse_type()
        self.eat("KW", "guarantees")
        gname = self.eat("IDENT").text

        self.eat("SYM", "{")
        methods: List[ImplMethod] = []

        while True:
            self.skip_newlines()
            if self.match("SYM", "}"):
                break

            mname = self.eat("IDENT").text
            self.eat("SYM", "=")
            self.eat("KW", "builtin")
            bname = self.eat("IDENT").text
            methods.append(ImplMethod(name=mname, builtin=bname))

        self.eat("SYM", "}")
        return ImplDecl(typ=typ, guarantee=gname, methods=methods)

    # ---- func ----

    def parse_func(self) -> FuncDecl:
        # func add(a: T, b: T) -> T
        #   require T in Number
        #   require T guarantees Addable
        self.eat("KW", "func")
        name = self.eat("IDENT").text
        self.eat("SYM", "(")
        params = self.parse_params()
        self.eat("SYM", ")")
        self.eat("SYM", "->")
        ret = self.parse_type()

        requires: List[RequireClause] = []
        failure: TypeRef | None = None

        while True:

            self.skip_newlines()

            if self.match("KW", "require"):
                requires.append(self.parse_require_clause())
                continue

            if self.match("KW", "failure"):

                self.eat("KW", "failure")

                if failure is not None:
                    raise SyntaxError("duplicate failure")
                failure = self.parse_type()

                continue

            break

        if failure is None:
            raise SyntaxError("missing failure")

        return FuncDecl(name=name, params=params, ret=ret, requires=requires, failure=failure)

    def parse_require_clause(self) -> RequireClause:
        self.eat("KW", "require")
        tvar = self.eat("IDENT").text

        if self.match("KW", "in"):
            self.eat("KW", "in")
            group = self.eat("IDENT").text
            return RequireIn(type_var=tvar, group_name=group)

        if self.match("KW", "guarantees"):
            self.eat("KW", "guarantees")
            gname = self.eat("IDENT").text
            return RequireGuarantees(type_var=tvar, guarantee_name=gname)

        t = self.cur()
        raise SyntaxError(
            f"Expected 'in' or 'guarantees' after require, got {t.kind}('{t.text}') at {t.pos}"
        )

    # ---- var decl ----

    def parse_var_decl(self) -> VarDecl:
        # Float x = add(1.3, 1.2)
        typ = self.parse_type()
        name = self.eat("IDENT").text
        self.eat("SYM", "=")
        expr = self.parse_expr()
        return VarDecl(typ=typ, name=name, expr=expr)

    # ---- expressions ----

    def parse_expr(self) -> Expr:
        if self.match("IDENT"):
            ident = self.eat("IDENT").text
            if self.match("SYM", "("):
                self.eat("SYM", "(")
                args, style = self.parse_args()
                self.eat("SYM", ")")
                return CallExpr(callee=ident, args=args, arg_style=style)
            return IdentExpr(ident)

        if self.match("INT"):
            return IntLit(int(self.eat("INT").text))
        if self.match("FLOAT"):
            return FloatLit(float(self.eat("FLOAT").text))

        t = self.cur()
        raise SyntaxError(f"Unexpected token {t.kind}('{t.text}') at {t.pos} in expression")

    def parse_args(self) -> Tuple[List[Arg], str]:
        # empty ok
        if self.match("SYM", ")"):
            return [], "pos"

        args: List[Arg] = []
        style: Optional[str] = None  # "pos" | "named"

        while True:
            # named iff IDENT ':' at arg start
            if (
                self.match("IDENT")
                and self.toks[self.i + 1].kind == "SYM"
                and self.toks[self.i + 1].text == ":"
            ):
                if style is None:
                    style = "named"
                elif style != "named":
                    raise SyntaxError("Cannot mix positional and named arguments")

                name = self.eat("IDENT").text
                self.eat("SYM", ":")
                expr = self.parse_expr()
                args.append(NamedArg(name=name, expr=expr))
            else:
                if style is None:
                    style = "pos"
                elif style != "pos":
                    raise SyntaxError("Cannot mix positional and named arguments")

                expr = self.parse_expr()
                args.append(PosArg(expr=expr))

            if self.match("SYM", ","):
                self.eat("SYM", ",")
                continue
            break

        return args, style or "pos"


def parse(src: str) -> Program:
    return Parser(tokenize(src)).parse_program()