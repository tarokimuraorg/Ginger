"""Limited static contracts; runtime boundaries continue to use FailureSet."""

from dataclasses import dataclass

from .failure_spec import FailureId, FailureSet, EMPTY_FAILURES, union_failures
from ginger.errors import TypecheckError


@dataclass(frozen=True)
class ConditionalFailure:
    failure_id: FailureId
    type_var: str
    guarantee_name: str


@dataclass(frozen=True)
class FailureContract:
    unconditional: FailureSet = EMPTY_FAILURES
    conditional: frozenset[ConditionalFailure] = frozenset()

    def potential_failures(self) -> FailureSet:
        return union_failures(self.unconditional, (c.failure_id for c in self.conditional))

    def concrete_failures(self) -> FailureSet:
        if self.conditional:
            raise TypecheckError("cannot instantiate failure contract without concrete type bindings")
        return self.unconditional


def union_contracts(*contracts: FailureContract) -> FailureContract:
    unconditional = union_failures(*(c.unconditional for c in contracts))
    conditional = frozenset(clause for c in contracts for clause in c.conditional
                            if clause.failure_id not in unconditional)
    return FailureContract(unconditional, conditional)


def substitute_contract(contract: FailureContract, bindings, type_guarantees) -> FailureContract:
    unconditional = set(contract.unconditional)
    conditional = set()
    for clause in contract.conditional:
        typ = bindings.get(clause.type_var)
        if typ is None:
            raise TypecheckError(f"cannot determine type variable '{clause.type_var}' in failure condition")
        if len(typ.name) == 1 and typ.name.isalpha() and typ.name.isupper():
            conditional.add(ConditionalFailure(clause.failure_id, typ.name, clause.guarantee_name))
        elif clause.guarantee_name in type_guarantees.get(typ.name, set()):
            unconditional.add(clause.failure_id)
    return union_contracts(FailureContract(frozenset(unconditional), frozenset(conditional)))


def uncovered_contract(inferred: FailureContract, declared: FailureContract) -> FailureContract:
    """Only identical clauses or an unconditional FailureId cover a condition."""
    return FailureContract(
        inferred.unconditional - declared.unconditional,
        frozenset(c for c in inferred.conditional
                  if c.failure_id not in declared.unconditional and c not in declared.conditional),
    )
