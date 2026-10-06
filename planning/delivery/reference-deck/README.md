# Group 3 project learning reference

Start with [the visual project guide](IS459_G3_Visual_Project_Guide_v3.pptx). It contains 42 slides explaining the current implementation, with editable architecture and process diagrams, native charts, annotated dashboard views, and an operating appendix.

Evidence date: 6 October 2026. The guide uses retained repository acceptance results and screenshots. Its implementation status supersedes early proposal intentions. It is a learning reference longer than the final course presentation slot.

## Find the topic you need

| Slides | Topic |
| --- | --- |
| 1–6 | Purpose, business decisions, implementation status and dataset |
| 7–12 | AWS batch architecture, parallel branches, curation, FX and trailing features |
| 13–18 | AML ranking, chronological evaluation, explanations and treasury concentration |
| 19–20 | Using the AML and treasury dashboard views |
| 21–27 | Verification, training versus inference, caching, publication and streaming |
| 28–35 | Setup, run choices, failure recovery, costs, repository map and validation |
| 36–42 | Copyable command reference, deployment context, glossary and submission requirements |

## Files in this folder

- [Visual project guide](IS459_G3_Visual_Project_Guide_v3.pptx): the revised deck to use.
- [Editable architecture diagrams](IS459_G3_Current_Architecture.drawio): batch, parallel analysis and streaming pages. AWS icons use the verified draw.io AWS stencil library.
- [Architecture explanation](IS459_G3_Current_Architecture.md): diagram semantics, links and service purposes.
- [Detailed operating reference](Architecture_and_Operating_Reference.md): the original detailed handout, including copyable commands and additional explanations. Its section numbers refer to the original reference deck.
- [Original reference deck](IS459_G3_Architecture_and_Operating_Reference.pptx): retained for comparison.

## Quick start

Run these commands from the workspace root in PowerShell, with Python 3.11+ and AWS CLI v2 available:

```powershell
cd ".\bda-project-g3"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[cloud]"
.\scripts\aws\configure-aws.ps1
```

The project launcher assumes the existing Group 3 deployment and configured profiles. From the project directory:

```powershell
.\scripts\ingestion\ingest.ps1 -Python python -NoWait
```

This is the documented cloud run command. An uncached execution starts paid AWS work and can publish a new dashboard release. No AWS run, deployment or cleanup was performed to create the deck.

For the exact setup, supported options, fixtures and recovery procedure, use the current [ingestion runbook](../../runbooks/ingestion-runbook.md), [AWS login guide](../../runbooks/aws-login.md), and [batch runbook](../../runbooks/aws-batch-runbook.md). The [implementation runbook](../../runbooks/pipeline-optimisation-implementation.md) and [model review](../../archive/reviews/local-model-validation.md) contain the deeper technical evidence.
