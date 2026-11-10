# Model Configuration

Mozaiks OSS defaults to Google `gemini-3.8-flash` for workflow execution and
the shipped refinement profiles. The base package includes the Google SDK;
`mozaiks[gemini]` remains a supported installation alias. Existing operator configuration and stored model
records remain authoritative; upgrading does not rewrite them.

## Local OSS setup

Install Mozaiks and configure the workspace `.env`:

```sh
python -m pip install mozaiks
```

```dotenv
LLM_PRIMARY_API_TYPE=google
LLM_PRIMARY_MODEL=gemini-3.8-flash
GEMINI_API_KEY=your-key
```

Create a key in [Google AI Studio](https://aistudio.google.com/api-keys).
`GOOGLE_API_KEY` is also supported. Keep credential values out of tracked files.

As verified on November 10, 2026, Google's stable `gemini-3.8-flash` model
supports structured outputs and function calling, with no announced shutdown
date. Its Developer API offers free input/output quota; a key on a paid project
uses that project's billing terms. Free quota is not an unlimited service:
request and token limits depend on the project and are shown in AI Studio.
See Google's [model reference](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash),
[pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash),
[lifecycle](https://ai.google.dev/gemini-api/docs/deprecations), and
[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

Google may use unpaid-service inputs and outputs to improve its products,
including human review. Use non-sensitive examples with the free tier.
Google's terms require paid services for API clients made available to users in
the EEA, Switzerland, or UK. Check the current [API terms](https://ai.google.dev/gemini-api/terms)
before sending private source code or user data. A free tier in an app's own
subscription plans does not make its provider API requests free.

## Workflow and general assistant requests

The environment selects the runtime provider and model with
`LLM_PRIMARY_API_TYPE` and `LLM_PRIMARY_MODEL`. The latter takes precedence over
the `DEFAULT_LLM_MODEL` model override. App-specific model records in
MongoDB can override the environment. The `llm.provider` and `llm.model` fields
written to `app/config/ai.json` by `onboard` are display metadata, not runtime
model selectors.

OpenAI remains available with `LLM_PRIMARY_API_TYPE=openai`, an explicit
`LLM_PRIMARY_MODEL`, and `OPENAI_API_KEY`. Supported OpenAI models can opt into
`LLM_USE_RESPONSES_API=true` and `LLM_REASONING_EFFORT=low`; these settings are
provider-specific and should be omitted for Google. For Anthropic, install
`mozaiks[anthropic]`, select `anthropic`, and set `ANTHROPIC_API_KEY` with an
explicit model. Provider/model capabilities must match the workflow's tools and
structured-output requirements.

## Refinement requests

`app/config/refinement_policy.yaml` owns named `llm_profiles`. The shipped
`classifier`, `impact_analyzer`, `architecture`, and `codegen` profiles use
`api_type: google` and `model: gemini-3.8-flash`. To change refinement models,
edit these profiles as well as the workflow environment. Existing workspace
profiles are preserved by onboarding.

An explicit provider/model profile owns its request settings. Set a native
output limit such as `max_output_tokens` in that profile when needed; the
workflow environment's `LLM_MAX_TOKENS` is not inherited by a complete profile.

Classification, impact planning, and structured patch generation use these
profiles. The `architecture` profile is advisory workflow context; the workflow
runner still selects its model through the workflow runtime configuration.
ACP coding adapters run a separate coding agent with separate configuration;
the shipped ACP provider remains disabled. Choosing a model does not install or
enable Codex or Claude Code.
