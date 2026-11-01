import argparse
from pathlib import Path
import sys
from ginger.pipeline import run

def report_result(result) -> int:

    for event in result.unresolved_events:
        print(
            f"Unresolved failure: {event.failure_id.value}; "
            f"event: #{event.event_id}; "
            f"origin: {event.origin}; call: {event.call_id}",
            file=sys.stderr,
        )

    if result.contract_violation is not None:
        print(result.contract_violation, file=sys.stderr)
        return 1

    if result.value_contract_violation is not None:
        print(result.value_contract_violation, file=sys.stderr)
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ginger")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run a Ginger source file",
    )
    run_parser.add_argument("file", type=Path)

    args = parser.parse_args()

    if args.command == "run":

        path = args.file

        if not path.is_file() and path.parent == Path("."):
            path = Path(__file__).parent / "scripts" / path.name

        try:
            src = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ginger: {exc}", file=sys.stderr)
            return 1

        return report_result(run(src))

    return 1


if __name__ == "__main__":
    raise SystemExit(main())