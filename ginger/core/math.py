from ginger.ast import (
    GuaranteeDecl, 
    FuncSig,
    Param,
    TypeRef,
    ImplDecl,
    ImplMethod,
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
    ]