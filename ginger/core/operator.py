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
            name="Addable",
            methods=[
                FuncSig(
                    name="add",
                    params=[
                        Param("self", TypeRef("Self")),
                        Param("other", TypeRef("Self")),
                    ],
                    ret=TypeRef("Self"),
                )
            ],
        ),
        ImplDecl(
            typ=TypeRef("Int"),
            guarantee="Addable",
            methods=[ImplMethod(name="add", builtin="core.int.add")],
        ),
        ImplDecl(
            typ=TypeRef("Float"),
            guarantee="Addable",
            methods=[ImplMethod(name="add", builtin="core.float.add")],
        ),
        SigDecl(
            name="add",
            params=[
                TypeRef("T"),
                TypeRef("T"),
                ],
            ret=TypeRef("T"),
            requires=[RequireGuarantees(type_var="T", guarantee_name="Addable")],
            failures=[],
            attrs=[],
        ),

        GuaranteeDecl(
            name="Subtractable",
            methods=[
                FuncSig(
                    name="sub",
                    params=[
                        Param("self", TypeRef("Self")),
                        Param("other", TypeRef("Self")),
                    ],
                    ret=TypeRef("Self"),
                )
            ],
        ),
        ImplDecl(
            typ=TypeRef("Int"),
            guarantee="Subtractable",
            methods=[ImplMethod(name="sub", builtin="core.int.sub")],
        ),
        ImplDecl(
            typ=TypeRef("Float"),
            guarantee="Subtractable",
            methods=[ImplMethod(name="sub", builtin="core.float.sub")],
        ),
        SigDecl(
            name="sub",
            params=[
                TypeRef("T"),
                TypeRef("T"),
                ],
            ret=TypeRef("T"),
            requires=[RequireGuarantees(type_var="T", guarantee_name="Subtractable")],
            failures=[],
            attrs=[],
        ),

        GuaranteeDecl(
            name="Multipliable",
            methods=[
                FuncSig(
                    name="mul",
                    params=[
                        Param("self", TypeRef("Self")),
                        Param("other", TypeRef("Self")),
                    ],
                    ret=TypeRef("Self"),
                )
            ],
        ),
        ImplDecl(
            typ=TypeRef("Int"),
            guarantee="Multipliable",
            methods=[ImplMethod(name="mul", builtin="core.int.mul")],
        ),
        ImplDecl(
            typ=TypeRef("Float"),
            guarantee="Multipliable",
            methods=[ImplMethod(name="mul", builtin="core.float.mul")],
        ),
        SigDecl(
            name="mul",
            params=[
                TypeRef("T"),
                TypeRef("T"),
                ],
            ret=TypeRef("T"),
            requires=[RequireGuarantees(type_var="T", guarantee_name="Multipliable")],
            failures=[],
            attrs=[],
        ),
        SigDecl(
            name="div",
            params=[TypeRef("Float"), TypeRef("Float")],
            ret=TypeRef("Float"),
            requires=[],
            failures=[],
            attrs=[],
            builtin="core.float.div",
        ),
        SigDecl(
            name="toFloat",
            params=[TypeRef("Int")],
            ret=TypeRef("Float"),
            requires=[],
            failures=[],
            attrs=[],
            builtin="core.int.toFloat",
        ),
        GuaranteeDecl(
            name="Negatable",
            methods=[
                FuncSig(
                    name="neg",
                    params=[Param("self", TypeRef("Self"))],
                    ret=TypeRef("Self"),
                )
            ],
        ),
        ImplDecl(
            typ=TypeRef("Int"),
            guarantee="Negatable",
            methods=[ImplMethod(name="neg", builtin="core.int.neg")],
        ),
        ImplDecl(
            typ=TypeRef("Float"),
            guarantee="Negatable",
            methods=[ImplMethod(name="neg", builtin="core.float.neg")],
        ),
        SigDecl(
            name="neg",
            params=[TypeRef("T")],
            ret=TypeRef("T"),
            requires=[RequireGuarantees(type_var="T", guarantee_name="Negatable")],
            failures=[],
            attrs=[],
        ),

        # --- Ordering / cmp ---
        GuaranteeDecl(
            name="Ord",
            methods=[
                FuncSig(
                    name="cmp",
                    params=[
                        Param("self", typ=TypeRef("Self")),
                        Param("other", typ=TypeRef("Self")),
                    ],
                    ret=TypeRef("Ordering"),
                )
            ],
        ),
        ImplDecl(
            typ=TypeRef("Int"),
            guarantee="Ord",
            methods=[ImplMethod(name="cmp", builtin="core.int.cmp")],

        ),
        ImplDecl(
            typ=TypeRef("Float"),
            guarantee="Ord",
            methods=[ImplMethod(name="cmp", builtin="core.float.cmp")],
        ),
        SigDecl(
            name="cmp",
            params=[TypeRef("T"), TypeRef("T")],
            ret=TypeRef("Ordering"),
            requires=[RequireGuarantees(type_var="T", guarantee_name="Ord")],
            attrs=[],
        ),

    ]