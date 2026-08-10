# Architectural Invariants

These rules protect the public framework boundary while allowing rapid AI-assisted development. Change them only through an ADR.

## 1. Public Framework Contracts Stay Provider-Neutral

Protects: portability, self-hosting, and customer sovereignty.

Current enforcement: app/module/page/data/secret/deployment contracts are provider-neutral, and hosted provider mechanics are outside generated app artifacts.

Incomplete enforcement: new provider executors can still be introduced by normal source edits.

Automated guardrail: governance CI flags provider-specific production mutation code unless the change records publication review.

## 2. Generated Artifacts Must Not Contain Raw Secrets

Protects: generated-app safety and customer credential ownership.

Current enforcement: generated secret contracts are names-first and `security/secrets.yaml` is declarative.

Incomplete enforcement: prompts, templates, or samples can accidentally include real-looking credentials.

Automated guardrail: governance CI and secret scanning reject obvious raw secrets in generated or public artifact surfaces.

## 3. Agents Produce Candidates; Deterministic Code Validates and Promotes

Protects: artifact validity, auditability, and production safety.

Current enforcement: generated apps are staged under `generated/`, validators inspect bundle shape and safety, and promotion is explicit.

Incomplete enforcement: not every workflow output is equally tied to a public validation facade.

Automated guardrail: tests should cover validators for every public artifact family.

## 4. Public Schemas and Contracts Are Classified and Versioned

Protects: stable interoperability and cross-repository dogfooding.

Current enforcement: several runtime and extension contracts already carry typed models or explicit schema versions.

Incomplete enforcement: new YAML or JSON contract families can still appear without versioning.

Automated guardrail: governance CI flags schema-looking public contract files that do not expose a version or classification field.

## 5. Generic App Intelligence Can Be OSS; Multi-App Learned Intelligence Requires Review

Protects: OSS usefulness without accidentally publishing commercial operating knowledge.

Current enforcement: baseline generation, discovery, refinement, and build-context contracts are public framework capabilities.

Incomplete enforcement: future eval-derived optimizers, customer-derived repair logic, and cross-customer learned routing could be committed like normal prompt work.

Automated guardrail: workflow, prompt, refinement, and intelligence surfaces produce a governance review notice in CI.

## 6. Authority Bypass Semantics Must Not Expand Casually

Protects: permission boundaries while production authority is designed separately.

Current enforcement: existing `granted_permissions=None` call sites are limited and tested.

Incomplete enforcement: a new caller could copy the pattern and obtain trusted internal execution by omission.

Automated guardrail: governance CI rejects new `granted_permissions=None` source locations outside the explicit allowlist.

## 7. Mozaiks App Dogfoods Public Framework Contracts

Protects: OSS credibility and prevents private forks of generic framework behavior.

Current enforcement: public contracts now exist for Studio scope resolution, host composition, platform extension bundles, generated-app validation, and app-local module dispatch.

Incomplete enforcement: future commercial needs may still reach into private framework internals if no stable seam exists.

Automated guardrail: PR review must identify when a change adds or changes a public contract, operator extension, or private internal dependency.

## 8. Operator Capabilities Are Explicitly Separated

Protects: the public framework from hosted-only production authority and commercial operations.

Current enforcement: provider execution, payments, DNS, production credentials, and cross-customer intelligence are not required by generated app bundles.

Incomplete enforcement: a useful hosted feature can be accidentally committed as generic framework behavior.

Automated guardrail: publication review is required before adding provider-specific production execution, payment economics, marketplace ranking, or production operational intelligence to MIT source.
