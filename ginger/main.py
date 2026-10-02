from pathlib import Path
import sys
from ginger.pipeline import run


def report_result(result) -> int:
    for event in result.unresolved_events:
        print(f"Unresolved failure: {event.failure_id.value}; event: #{event.event_id}; "
              f"origin: {event.origin}; call: {event.call_id}", file=sys.stderr)
    if result.contract_violation is not None:
        print(result.contract_violation, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    root = Path(__file__).parent
    scene_id = 1
    src = (root / f"scripts/Scene_{scene_id}.ginger").read_text(encoding="utf-8")
    return report_result(run(src))


if __name__ == "__main__":
    raise SystemExit(main())
