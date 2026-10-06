"""Render a reviewable ASL definition and unapproved execution input locally."""
import argparse
import json
from pathlib import Path

from bda_pipeline.orchestration import ROWS, render_definition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    definition = render_definition(Path(__file__).resolve().parents[1], args.run_id)
    if args.output.exists():
        raise ValueError("Use a fresh preparation directory")
    args.output.mkdir(parents=True)
    (args.output / "definition.json").write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
    request = {"run_id": args.run_id, "expected_rows": ROWS, "execution_approved": False}
    (args.output / "execution-input.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "prepared-not-deployed-not-executed", "output": str(args.output)}))


if __name__ == "__main__":
    main()
