"""
from ginger.ast import (
    GuaranteeDecl, 
    FuncSig,
    Param,
    TypeRef,
    ImplDecl,
    ImplMethod,
    SigDecl,
    RequireGuarantees,
)

def core_items():
    return [
        GuaranteeDecl(
            name="Printable",
            methods=[
                FuncSig(
                    name="print",
                    params=[Param("self", TypeRef("Self"))],
                    ret=TypeRef("Unit"),
                )
            ],
        ),
        ImplDecl(
            typ=TypeRef("Int"),
            guarantee="Printable",
            methods=[ImplMethod(name="print", builtin="core.int.print")],
        ),
        ImplDecl(
            typ=TypeRef("Float"),
            guarantee="Printable",
            methods=[ImplMethod(name="print", builtin="core.float.print")],
        ),
        ImplDecl(
            typ=TypeRef("String"),
            guarantee="Printable",
            methods=[ImplMethod(name="print", builtin="core.string.print")],
        ),

        # --- Ordering ---
        ImplDecl(
            typ=TypeRef("Ordering"),
            guarantee="Printable",
            methods=[ImplMethod(name="print", builtin="core.ordering.print")],
        ),

        # --- sig print(T) -> Unit ---
        SigDecl(
            name="print",
            params=[TypeRef("T")],
            ret=TypeRef("Unit"),
            requires=[RequireGuarantees(type_var="T", guarantee_name="Printable")],
            failures=[],
            attrs=[],
        ),
    ]
"""