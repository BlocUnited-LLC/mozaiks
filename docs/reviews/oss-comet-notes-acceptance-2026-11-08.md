# Comet Notes OSS Acceptance

## Result and scope

One generated, authenticated app passed its business acceptance sequence on
2026-11-08. Comet Notes was generated through the Studio browser with real
model calls, corrected through one normal Workbench refinement, accepted, and
downloaded through the authenticated artifact endpoint. Its exact exported
bytes ran in a separate local container and database with real OIDC logins.
No generated source was manually patched to obtain this result.

This qualifies the artifact and source below. It does not qualify every
capability pack, unattended repeatability, the hosted App Zero product, or a
release. The original generation's Workbench continuation remained incomplete;
the accepted export and standalone app were tested separately through their
existing authorized endpoints.

## Exact identities

| Item | Value |
| --- | --- |
| OSS source | `92354ee80e3facde7a7e27333028d618f7a29b86` |
| Source tree | `e16a4f6815290badbccd4ae9b83444daca06636b` |
| Runtime image | `sha256:37dd82dc2e72d75228912b60b4ca501dceb3f8280aab70229a77783d9b389908` |
| App | `draft-app-6106c684` |
| Registry | `appreg_2968b5574b5f48f8a4cdc690ed990ecf` |
| Accepted artifact | `av_60089f1ad8bc4a79927bc292` |
| Parent artifact | `av_a319871ee5754dfea022d9b4` |
| Refinement session | `rs_c062353a7c004907b12c3ed5` |
| Export SHA-256 | `50f0dbc0d23c27b5a69e689da2f602c9fde943dc0d141306f6f3210db77c398c` |
| Artifact content SHA-256 | `98aeda63249577b4ca9c81518b30e38a7d2ac537ec853483efbe94b73b33a042` |

The export contains 26 files, one notes module, and two custom React routes:
`/notes` and `/notes/:noteId`. It has one declared role, `member`, with six
approved action grants. It has no app administrator role, subscription, or
AI workflow. Those omissions were approved build choices, not tested features.

The accepted child changed the route authorization response from `authorized`
to the existing canonical `allowed` field in the service and output schema.
The normal proposed-scope, confirmed-scope, coding-worker, validation, and
Accept actions produced this child. The parent became stale. Neither artifact
was activated or promoted.

## What passed

- Authenticated export: 17 checks passed. The stored manifest, downloaded ZIP,
  staged files, source identity, and image identity matched.
- Contained runtime: 23 checks passed, including six role-holder/non-holder
  action pairs. Completion and scoped cleanup were verified.
- Three real browser OIDC/PKCE logins: two distinct members and one roleless
  user. Issuer, subject, app identity, audiences, ID/access-token roles, and
  browser-session identity were checked. Credentials and raw tokens are absent
  from the evidence receipts.
- Member A created a note, opened its detail route, edited it, and reloaded the
  saved values. Member B created a separate note.
- Server paging returned 20 plus two distinct records, with a stable first
  page. The rendered Next control reached page two and became disabled.
  Body-text search returned the expected owned note; the other member's search
  returned zero matches.
- Eight cross-owner get, route-authorization, update, and delete requests
  returned 404. Seven roleless requests covering all six actions plus search
  returned 403. The rendered denial screens exposed no protected note body or
  Edit control.
- Forged `user_id` on create/update returned 403 at the token/context identity
  boundary. Forged `note_id` on create returned 400. Full member lists before
  and after the denial probes were identical, covering all 23 records.
- The final visible Delete confirmation returned 200. Reload showed 21 owned
  notes; fetching the deleted note returned 404, and the other member's full
  list was unchanged.

These are a sequence of explicitly linked runs, not one clean first attempt.
Three earlier business runs stopped on the existing shared 60-request/minute
rate limit: twice while seeding, once at deletion after detail navigation.
Their successful writes were retained and their failed receipts were preserved.
Each continuation verified the exact source, artifact, identities, successful
seed history, and retained inventory before proceeding. The final deletion-only
run passed five fresh checks in 78.37 seconds with zero 429 responses. No rate
limit was disabled, no failed request was silently retried, and no test record
was removed through database access.

Local Keycloak setup was explicit operator configuration. The dedicated public
client used PKCE S256, the app's exact callback, and the existing `basic` scope
to obtain the access-token subject. Three fresh task-owned users carried the
declared member/roleless roles. This setup is not evidence that arbitrary
identity-provider configuration works automatically.

## Evidence ledger

Receipts are retained under `.logs/read-e2e/` in the isolated
`oss-proof-20261107` checkout. They are local evidence, not public CI artifacts.
The checkout remains detached at the tested source; integration of PR #1014
uses a separate worktree. The verified 27-file canonical refinement output
(26 workspace files plus its ZIP) remains at its recorded paths.

| Receipt relative to `.logs/read-e2e/` | SHA-256 |
| --- | --- |
| `acceptance-av_60089f1ad8bc4a79927bc292-export-smoke.json` | `a07f8348ea746921bd2735c2e2ed595b206a8b53ee6c366545d34cbc96356950` |
| `final-export-independent-review.json` | `f96e7e450dbc0f14f474cadc8049226e9025b31c93cc2348b24a34c5804f1812` |
| `business-2026-11-08T00-24-31-099Z/receipt.json` | `b892adc193486874ae999bddfd9e2ab78917143b2c35bd0fc6d79525d2381493` |
| `business-continuation-2026-11-08T00-28-56-566Z/receipt.json` | `f459af399e4acbc0c4792dfb224fd1c9774ab04ced1cecd566e17240db61ae21` |
| `business-rate-aware-2026-11-08T00-40-11-191Z/receipt.json` | `c07f4e296715b4ee6295722a41498b64b5f0e7e4909b296f79920dd08434cf8d` |
| `business-rate-aware-independent-review.json` | `eaa3bc581652b93c236bf7207c65e09ed0f5046bd6cfb62d2da424980224c935` |
| `business-delete-only-2026-11-08T00-48-25-998Z/receipt.json` | `870803e6061165b32d0ec7be5bb21ece7f723c2e9aeb001d6f4cc4ac73ef325a` |
| `business-final-independent-review.json` | `f2991fed907c98c64c1cb3a9a3145774aec5c7045c7b4290982fff4e9d1618e0` |

Required CI for the original PR head passed on attempt one:
[run 42421939969](https://github.com/BlocUnited-LLC/mozaiks/actions/runs/42421939969),
26,635 passed, 220 skipped, one xfailed, 85% coverage. CI tested synthetic merge
`60c9eac3fec8b5677f1d962fb0877051abc3dcc2`, tree
`2b7099c225f6575b97c89c354fc3628d96046d5f`, combining main `178d6ed5` with
the pinned PR head. That tree differs from the live proof source above.

## Remaining limits

- The old AppGenerator Workbench did not hand the refined child to a new review
  session or complete its original waiter. That separate fix must preserve
  immutable run/build bindings and prevent exporting the superseded parent.
- Interactive Studio preview, activation, promotion, and an exact OSS/App Zero
  commit pair were not qualified by this standalone run.
- The contained observer explicitly left `event_rejection` unverified. Custom
  React pages were verified in the browser; a zero declarative-page count must
  not be read as a missing UI or as schema-page coverage.
- No admin-role, cross-member administration, mobile, load, deployment, billing,
  or all-pack acceptance is claimed.
- A separate ThemeCapture journey rejected malformed model JSON. Successful
  generation here does not establish unattended repeatability or justify
  relaxing structured-output validation.
- No release, tag, public deployment, or paid infrastructure was created.
