from typing import List, Optional, Tuple
from .tokenizer import Token, tokenize
from .ast import (
    Program, TopLevel,
    TypeRef, Param, FuncSig,
    GuaranteeDecl, TypeGroupDecl, RegisterDecl,
    ImplDecl, ImplMethod,
    RequireClause, RequireIn, RequireGuarantees,
    SigDecl, FuncDecl, VarDecl,AssignStmt,BinaryExpr,
    BlockStmt, ReturnStmt, Stmt,
    Expr, CallExpr, IdentExpr, IntLit, FloatLit,
    Arg, PosArg, NamedArg,
    ExprStmt, TryStmt, CatchStmt,
)

# 演算子の優先順位
_PRECEDENCE = {
    "+": 10,
    "-": 10,
    "*": 20,
    "/": 20,
}

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
    
    def parse_attrs(self) -> List[str]:
        """
        Parse attribute lines preceding a func declaration.
        
        Syntax:
            @attr.<name>
        
        Returns:
            ["io", "handled",...] (the <name> part only)
        """     
        attrs: List[str] = []
        
        while self.match("SYM", "@"):

            # Catalog以外で@attrの付与を禁止する
            """
            if getattr(self, "origin", "unknown") != "catalog":
                t = self.cur()
                raise SyntaxError(
                    f"@attr is only allowed in Catalog (got '@' at {t.pos})"
                )
            """
            
            self.eat("SYM", "@")

            # must be: attr . NAME
            ns = self.eat("IDENT").text

            if ns != "attr":
                raise SyntaxError(f"unknown attribute namespace '@{ns}' (did you mean @attr.<name>?)")
            
            # @attr.<name>の'.'がない場合：落とす
            if not self.match("SYM", "."):
                t = self.cur()
                raise SyntaxError(
                    f"expected '.' after '@attr' (use @attr.<name>), got {t.kind}('{t.text}') at {t.pos}"
                )

            self.eat("SYM", ".")

            if not self.match("IDENT"):
                t = self.cur()
                raise SyntaxError(
                    f"expected attribute name after '@attr.', got {t.kind}('{t.text}') at {t.pos}"
                )
            
            aname = self.eat("IDENT").text
            attrs.append(aname)
            self.skip_newlines()

        return attrs

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
        
        # 先頭の @attr を回収
        attrs = self.parse_attrs()
        
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
            return self.parse_func(attrs=attrs)
        if self.match("KW", "sig"):
            return self.parse_sig(attrs=attrs)
        
        # attrs があるのに func でない場合：エラー
        if attrs:
            raise SyntaxError("attributes must precede a sig or func declaration")
        
        # --- let/var (toplevel statement) ---
        if self.match("KW", "let"):
            return self.parse_let_var_decl(mutable=False)
        if self.match("KW", "var"):
            return self.parse_let_var_decl(mutable=True)
        
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
        
        # --- AssignStmt ---
        if (self.match("IDENT") and self.toks[self.i + 1].kind == "SYM" and self.toks[self.i + 1].text == "="):
            return self.parse_assign_stmt()
        
        # --- ExprStmt ---
        if self.match("IDENT") and self.toks[self.i + 1].kind == "SYM" and self.toks[self.i + 1].text == "(":
            expr = self.parse_expr()
            return ExprStmt(expr=expr)
        
        t = self.cur()
        raise SyntaxError(f"Unexpected toplevel token {t.kind}('{t.text}') at {t.pos}")
    

    # ---- shared ----
    def parse_dotted_name(self) -> str:
        # IDENT ('.' IDENT)*
        name = self.eat("IDENT").text
        while self.match("SYM", "."):
            self.eat("SYM", ".")
            name += "." + self.eat("IDENT").text
        return name

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
    
    def parse_sig_param_types(self) -> List[TypeRef]:
        
        tys: List[TypeRef] = []

        if self.match("SYM", ")"):
            return tys
        
        while True:

            tys.append(self.parse_type())   # IDENT -> TypeRef

            if self.match("SYM", ","):
                self.eat("SYM", ",")
                continue

            break

        return tys

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
            bname = self.parse_dotted_name()
            methods.append(ImplMethod(name=mname, builtin=bname))

        self.eat("SYM", "}")
        return ImplDecl(typ=typ, guarantee=gname, methods=methods)
    

    # ---- sig ----

    def parse_sig(self, attrs: Optional[List[str]] = None) -> SigDecl:

        self.eat("KW", "sig")
        name = self.eat("IDENT").text
        self.eat("SYM", "(")
        params = self.parse_sig_param_types()
        self.eat("SYM", ")")
        self.eat("SYM", "->")
        ret = self.parse_type()

        requires: List[RequireClause] = []
        failures: list[str] = []
        builtin: str | None = None

        self.eat("SYM", "{")

        while True:

            self.skip_newlines()

            if self.match("SYM", "}"):
                break

            if self.match("KW", "require"):
                requires.append(self.parse_require_clause())
                continue

            if self.match("KW", "failure"):

                self.eat("KW", "failure")
                f = self.parse_type().name

                if f == "Never":
                    if failures:
                        raise SyntaxError("cannot combine 'Never' with other failures")
                    # failures は空のまま
                else:
                    if f in failures:
                        raise SyntaxError(f"duplicate failure '{f}'")
                    failures.append(f)
                continue

            if self.match("KW", "builtin"):

                self.eat("KW", "builtin")

                if builtin is not None:
                    raise SyntaxError("duplicate builtin")
                
                builtin = self.parse_dotted_name()
                continue

            t = self.cur()
            raise SyntaxError(f"Unexpected token in sig body {t.kind}('{t.text}') at {t.pos}")

        self.eat("SYM", "}")

        return SigDecl(
            name=name,
            params=params,
            ret=ret,
            requires=requires,
            failures=failures,
            attrs=list(attrs or []),
            builtin=builtin,
        )

    # ---- func ----

    def parse_func(self, attrs: Optional[List[str]] = None) -> FuncDecl:
        # func add(a: Int, b: Int) { return a + b }
        self.eat("KW", "func")
        name = self.eat("IDENT").text
        self.eat("SYM", "(")
        params = self.parse_params()
        self.eat("SYM", ")")
        self.skip_newlines()
        body = self.parse_block()

        return FuncDecl(
            name=name, 
            params=params,
            body=body, 
            attrs=list(attrs or []),
            )

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

    # ---- let/var decl ----
    def parse_let_var_decl(self, mutable: bool) -> VarDecl:

        if mutable:
            self.eat("KW", "var")
        else:
            self.eat("KW", "let")

        name = self.eat("IDENT").text
        self.eat("SYM", ":")
        typ = self.parse_type()
        self.eat("SYM", "=")
        expr = self.parse_expr()

        return VarDecl(mutable=mutable, typ=typ, name=name, expr=expr)

    def parse_assign_stmt(self) -> AssignStmt:
        name = self.eat("IDENT").text
        self.eat("SYM", "=")
        expr = self.parse_expr()
        return AssignStmt(name=name, expr=expr)
    
    def parse_block(self) -> BlockStmt:

        self.eat("SYM", "{")
        stmts: List[Stmt] = []

        while True:

            self.skip_newlines()

            if self.match("SYM", "}"):
                break

            # return <expr>
            if self.match("KW", "return"):
                self.eat("KW", "return")
                expr = self.parse_expr()
                stmts.append(ReturnStmt(expr=expr))
                self.skip_newlines()
                continue

            # 今は最低限、式分だけ許可
            expr = self.parse_expr()
            stmts.append(ExprStmt(expr=expr))
            self.skip_newlines()

        self.eat("SYM", "}")

        return BlockStmt(stmts=stmts)
    

    # ---- expressions ----

    def _is_op(self) -> bool:
        return self.match("SYM") and self.cur().text in _PRECEDENCE

    def parse_expr(self) -> Expr:
        # 演算子式は必ず '(' から始まる
        if self.match("SYM", "("):
            return self.parse_paren_infix_expr()
        
        # それ以外は「operand（原子）」しか許さない
        expr = self.parse_operand()

        # operand の直後に演算子が見えたらルール違反
        if self._is_op():
            t = self.cur()
            raise SyntaxError(
                f"infix operator '{t.text}' is only allowed inside '(...)' at {t.pos}"
            )
        return expr

    def parse_paren_infix_expr(self) -> Expr:
        # '(' <infix-expr> ')' だが、ただの(operand)は禁止
        lpar = self.eat("SYM", "(")

        expr, saw_op = self.parse_infix(min_prec=0)

        self.eat("SYM", ")")

        if not saw_op:
            # (1) や (div(1,2)) を禁止
            raise SyntaxError(
                f"parentheses are only for infix expressions; "
                f"remove '(...)' or write an operator inside at {lpar.pos}"
            )
        return expr

    def parse_infix(self, min_prec: int) -> tuple[Expr, bool]:
        # left operand
        left = self.parse_operand()
        saw_op = False

        while self._is_op():
            op = self.cur().text
            prec = _PRECEDENCE[op]
            if prec < min_prec:
                break

            self.eat("SYM", op)
            right, right_saw = self.parse_infix(min_prec=prec + 1)

            saw_op = True or saw_op
            saw_op = saw_op or right_saw

            left = BinaryExpr(op=op, left=left, right=right)

        return left, saw_op

    def parse_operand(self) -> Expr:
        
        # unary '-' はどこでも禁止（neg(x)に固定）
        if self.match("SYM", "-"):
            t = self.cur()
            raise SyntaxError(f"unary '-' is forbidden; use neg(x) at {t.pos}")

        # infix-group は operand 扱いできる（入れ子OK）
        if self.match("SYM", "("):
            return self.parse_paren_infix_expr()

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


    def parse_primary(self) -> Expr:

        if self.match("SYM", "-"):
            
            t = self.cur()

            def tok(i):
                if 0 <= i < len(self.toks):
                    return self.toks[i]
                return None
            
            t_m1 = tok(self.i - 1)
            t_m2 = tok(self.i - 2)

            if (
                t_m1 is not None and t_m1.kind == "SYM" and t_m1.text == "(" and
                t_m2 is not None and t_m2.kind == "IDENT" and t_m2.text == "neg"
            ):
                raise SyntaxError(
                    f"unary '-' is forbidden inside neg(...); write neg(neg(x)) instead at {t.pos}"
                )
            
            raise SyntaxError(
                f"unary '-' is forbidden; use neg(x) at {t.pos}"
            )

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