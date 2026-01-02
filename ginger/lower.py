from dataclasses import replace
from typing import List

from .ast import (
    Program, TopLevel,
    ExprStmt, TryStmt, CatchStmt, VarDecl, AssignStmt,
    FuncDecl, BlockStmt, ReturnStmt,
    Expr, BinaryExpr, CallExpr, PosArg,
)

# op -> callee
OP_TO_CALEE = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
}

def lower_program(prog: Program) -> Program:
    new_items: List[TopLevel] = []
    for it in prog.items:
        new_items.append(lower_toplevel(it))
    return Program(items=new_items)

def lower_toplevel(it: TopLevel) -> TopLevel:
    
    # statements at top level
    if isinstance(it, ExprStmt):
        return ExprStmt(expr=lower_expr(it.expr))
    if isinstance(it, TryStmt):
        return TryStmt(expr=lower_expr(it.expr))
    if isinstance(it, CatchStmt):
        return CatchStmt(failure_name=it.failure_name, expr=lower_expr(it.expr))
    if isinstance(it, VarDecl):
        return VarDecl(mutable=it.mutable, typ=it.typ, name=it.name, expr=lower_expr(it.expr))
    if isinstance(it, AssignStmt):
        return AssignStmt(name=it.name, expr=lower_expr(it.expr))
    
    # func bodies
    if isinstance(it, FuncDecl):
        return FuncDecl(
            name=it.name,
            params=it.params,
            body=lower_block(it.body),
            attrs=it.attrs,
        )
    
    # catalog-ish nodes: leave as-is (GuaranteeDecl, SigDecl, ImplDecl, etc.)
    return it

def lower_block(b: BlockStmt) -> BlockStmt:
    
    out = []
    
    for st in b.stmts:
    
        if isinstance(st, ReturnStmt):
            out.append(ReturnStmt(expr=lower_expr(st.expr)))
        elif isinstance(st, ExprStmt):
            out.append(ExprStmt(expr=lower_expr(st.expr)))
        elif isinstance(st, VarDecl):
            out.append(VarDecl(mutable=st.mutable, typ=st.typ, name=st.name, expr=lower_expr(st.expr)))
        elif isinstance(st, AssignStmt):
            out.append(AssignStmt(name=st.name, expr=lower_expr(st.expr)))
        elif isinstance(st, TryStmt):
            out.append(TryStmt(expr=lower_expr(st.expr)))
        elif isinstance(st, CatchStmt):
            out.append(CatchStmt(failure_name=st.failure_name, expr=lower_expr(st.expr)))
        else:
            out.append(st)
    
    return BlockStmt(stmts=out)

def lower_expr(e: Expr) -> Expr:
    
    if isinstance(e, BinaryExpr):
        
        # 再帰的に左右もlower
        left = lower_expr(e.left)
        right = lower_expr(e.right)

        if e.op not in OP_TO_CALEE:
            raise SyntaxError(f"unknown binary operator '{e.op}'")
        
        callee = OP_TO_CALEE[e.op]

        return CallExpr(
            callee=callee,
            args=[PosArg(left), PosArg(right)],
            arg_style="pos",
        )
    
    # CallExpr の引数も再帰的に lower（ネストした演算を潰す）
    if isinstance(e, CallExpr):

        new_args = []

        for a in e.args:
            # PosArg / NamedArg どちらも中身は expr
            new_args.append(replace(a, expr=lower_expr(a.expr)))
        
        return CallExpr(callee=e.callee, args=new_args, arg_style=e.arg_style)
    
    # IdentExpr / IntLit / FloatLit はそのまま
    return e