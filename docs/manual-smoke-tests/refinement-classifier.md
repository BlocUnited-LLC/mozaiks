# Refinement Classifier Smoke

This manual smoke validates the live `LLMChangeClassifier` against neutral
refinement requests, then sends the resulting classifications through the
deterministic route and impact pipeline. It does not execute refinement
workflows and does not write generated app files.

## Run

```powershell
python scripts/smoke_refinement_classifier.py
```

For machine-readable output:

```powershell
python scripts/smoke_refinement_classifier.py --json
```

To save a replay fixture:

```powershell
python scripts/smoke_refinement_classifier.py --save-fixture
```

The fixture path is:

```text
tests/fixtures/refinement_classifier_smoke_output.json
```

## Required Configuration

The script uses the `classifier` LLM profile from
`factory_app/app/config/ai.json`. Provide an LLM provider through the normal
runtime configuration, usually:

```powershell
$env:OPENAI_API_KEY="..."
```

If the provider is stored in the configured runtime database or secret backend,
that can be used instead. `CAPABILITY_LLM_API_BASE` may point at an
OpenAI-compatible endpoint. Do not change model settings for this smoke.

If no usable provider key is available, the script exits with a clear error and
does not save a fixture.

## Requests

The smoke uses five neutral cases:

- UI / ExperienceSpec: `Change the dashboard experience to highlight reports first.`
- Module/backend: `Add an archive_project action to the projects module API.`
- External integration: `Change the analytics_provider connector sync behavior.`
- Data model migration: `Add a required project phase field and migrate existing project records.`
- Hosted capability facade: `Change hosted analytics dashboard display.`

## Output Shape

Each case reports:

- request text
- classifier output: `change_class`, rationale, confidence, signals
- selected `workflow_id` and `workflow_sequence`
- affected declarative families
- affected bundle paths
- scope summary
- LLM profile used

The script validates that change classes are one of
`patch`, `design`, `feature`, or `core`; the selected route exists; expected
neutral path families are present; secret-bearing paths are not emitted; and no
proprietary example terms appear in the output.

## Fixture Replay

Run:

```powershell
python -m pytest tests/test_live_refinement_classifier_smoke.py -q
```

The live LLM test is skipped. If
`tests/fixtures/refinement_classifier_smoke_output.json` exists, pytest replays
and validates the fixture without calling the LLM. If the fixture is absent, the
replay tests skip.

## Failure Meaning

- Invalid `change_class`: the classifier output drifted from the control-plane
  contract.
- Missing route or sequence: `control_plane.yaml` and
  `extension_registry.json` are out of alignment.
- Missing path family: deterministic impact derivation no longer recognizes the
  neutral refinement surface.
- Secret path emitted: impact scoping leaked credential-bearing files and must
  be fixed before using live refinement.
- Missing destructive warning: data-model migration risk is not being surfaced.

The smoke is intentionally limited to classification plus route and impact
derivation. It does not run AppGenerator, does not rewrite workflows, and does
not mutate generated app output.
