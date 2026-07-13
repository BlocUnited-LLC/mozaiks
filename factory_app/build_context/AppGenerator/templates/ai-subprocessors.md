# AI Subprocessors and Data Use Disclosure

<!-- TODO: Replace all [PLACEHOLDER] values before publishing this document. -->
<!-- This file was scaffolded by Mozaiks AppGenerator because your app uses    -->
<!-- AI-powered features. You must complete and review it before launch.        -->

**Last updated:** [DATE]  
**App name:** [APP_NAME]  
**App operator:** [COMPANY_NAME]

---

## Overview

[APP_NAME] uses AI-powered features to provide [BRIEF_DESCRIPTION_OF_AI_FEATURES].
These features are powered by one or more third-party AI subprocessors listed below.
This document describes what data each subprocessor receives, how long they retain it,
and your rights as a user.

---

## AI Subprocessors

| Subprocessor | Purpose | Data sent | Trains on user data? | Retention |
|---|---|---|---|---|
| [PROVIDER_NAME] | [PURPOSE] | [DATA_TYPES, e.g. "message text, user role"] | [Yes / No — link to provider policy] | [RETENTION_PERIOD, e.g. "30 days"] |

<!-- Add one row per AI provider your app sends user data to.          -->
<!-- Typical providers include LLM API providers (OpenAI, Anthropic,   -->
<!-- Cohere, etc.), embedding services, and speech/image providers.    -->
<!-- Check each provider's data processing agreement (DPA) for        -->
<!-- accurate retention and training-opt-out information.              -->

---

## What Data Is Sent to AI Subprocessors

When you use AI-powered features, the following types of data may be transmitted
to the subprocessors listed above:

- [DATA_TYPE_1, e.g. "Your submitted text or messages"]
- [DATA_TYPE_2, e.g. "File or document content you choose to share"]
- [DATA_TYPE_3, e.g. "Your account role (e.g. admin, member) used for context"]

The following data is **never** sent to AI subprocessors:

- [EXCLUDED_DATA_1, e.g. "Payment card or financial account details"]
- [EXCLUDED_DATA_2, e.g. "Passwords or authentication tokens"]
- [EXCLUDED_DATA_3 — list any data categories you explicitly exclude]

---

## Model Training

<!-- Review each provider's current DPA. Policies change.             -->
<!-- Do not leave this section blank — regulators and users will ask. -->

[APP_NAME] [does / does not] share data with subprocessors that use it for model
training. Specifically:

- **[PROVIDER_NAME]:** [Does / Does not] use submitted data to train models.
  [Optional: You can opt out by [OPT_OUT_MECHANISM] or by contacting [CONTACT].]

---

## User Rights

Under GDPR, CCPA, and similar regulations you have the right to:

- **Access** the personal data we hold about you.
- **Delete** your account and associated data, including data sent to AI subprocessors
  where the provider's retention window allows deletion on request.
- **Object** to AI-powered processing. You may [DESCRIBE_OPT_OUT_PATH, e.g.
  "disable AI features in your account settings under Settings → AI Features"].
- **Port** a copy of your data in a machine-readable format.

To exercise any of these rights, contact us at [DATA_RIGHTS_EMAIL_OR_FORM_URL].

We will respond within [RESPONSE_TIMEFRAME, e.g. "30 days"] in line with
applicable law.

---

## Consent and Notice

<!-- Describe when and how users are informed that AI features process  -->
<!-- their data. This may be at signup, at first AI feature use, or    -->
<!-- through a banner/modal. Choose what applies to your app.          -->

Users are informed of AI data processing at [POINT_OF_NOTICE, e.g. "account
sign-up through our Terms of Service and Privacy Policy, and again at first use
of an AI-powered feature through an in-product notice"].

[If applicable: We obtain explicit opt-in consent before processing [SENSITIVE_DATA_CATEGORY]
through AI features.]

---

## Data Retention

[APP_NAME] retains AI-interaction logs (prompt inputs, outputs, session metadata)
for [YOUR_APP_RETENTION_PERIOD, e.g. "90 days"] for debugging and quality purposes.
After that period, logs are deleted from our systems. Subprocessor retention windows
are listed in the table above; we cannot guarantee earlier deletion by subprocessors
once data is transmitted.

---

## Security

All data sent to AI subprocessors is transmitted over encrypted channels (TLS 1.2+).
[Add any additional controls relevant to your app, e.g. VPC peering, no-logging API
tier, enterprise DPA.]

---

## Changes to This Document

We will update this document when we add, remove, or change AI subprocessors.
Significant changes will be communicated to registered users by [NOTIFICATION_METHOD,
e.g. "email at least 30 days before taking effect"].

---

## Contact

For questions about this disclosure or to exercise your rights, contact:

**[COMPANY_NAME]**  
[CONTACT_EMAIL]  
[OPTIONAL: Data Protection Officer: DPO_NAME — dpo@[DOMAIN]]  
[OPTIONAL: EU Representative: EU_REP_NAME — [ADDRESS]]
