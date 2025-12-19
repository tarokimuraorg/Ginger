from .ast import GuaranteeDecl, FuncSig, Param, TypeRef

CORE_GUARANTEES_DECLS = {
    "Printable": GuaranteeDecl(
        name="Printable",
        methods=[
            FuncSig(
                name="print",
                params=[Param(name="self", typ=TypeRef("Self"))],
                ret=TypeRef("Unit"),
            )
        ],
    ),
}