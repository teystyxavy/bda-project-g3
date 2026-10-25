"""Render a reviewable ASL definition and unapproved execution input locally.

Where this sits in the pipeline
-------------------------------
Part of the *orchestration* stack (``infra/orchestration``): a Step Functions
state machine that runs the full-data frozen-model Glue scoring job and the
Athena corridor SQL (BQ2) over the curated Parquet. Before anything is
deployed, this script renders the Amazon States Language (ASL) definition with
``bda_pipeline.control.orchestration.render_definition`` (which fills the template with
the run's S3 paths, Glue job and SQL) so a reviewer can read exactly what the
state machine will do.

Writes (to a fresh ``--output`` folder)
---------------------------------------
* ``definition.json`` - the rendered ASL definition.
* ``execution-input.json`` - the input a later execution would receive:
  ``run_id``, the expected row count (``ROWS`` = 5,078,345, the HI-Small
  transaction count) and ``execution_approved: false`` to make clear that
  nothing has been approved or started.

No AWS calls are made.

Usage
-----
    python scripts/aws/prepare-orchestration.py --run-id full-001 \
        --output output/orchestration-full-001
"""
import argparse
import json
from pathlib import Path

from bda_pipeline.control.orchestration import ROWS, render_definition


def main():
    """Render the ASL definition for ``--run-id`` and write the two JSON files.

    Raises:
        ValueError: if ``--output`` already exists (previous reviews are kept).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # parents[2] = repository root, where the ASL template and SQL files live.
    definition = render_definition(Path(__file__).resolve().parents[2], args.run_id)
    if args.output.exists():
        raise ValueError("Use a fresh preparation directory")
    args.output.mkdir(parents=True)
    (args.output / "definition.json").write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
    # The workflow can compare expected_rows with what it actually scores.
    request = {"run_id": args.run_id, "expected_rows": ROWS, "execution_approved": False}
    (args.output / "execution-input.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "prepared-not-deployed-not-executed", "output": str(args.output)}))


if __name__ == "__main__":
    main()
