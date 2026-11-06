# AegisQA Security and Execution Threat Model

## Purpose and security decision

This document defines the minimum security boundary required before Phase 1 implements project-scoped browser execution. It applies the MVP in `docs/product/mvp-definition.md` and the P0.5 readiness audit. It is a design and acceptance contract, not a claim that these controls already exist.

**Security decision:** the first product MVP may execute only anonymous, organization-controlled, dedicated test environments registered by a project administrator. It must not execute arbitrary URLs, private/intranet services, production applications, credentialed flows, or user-provided browser sessions. Until the planned API, authorization, isolated worker, and egress controls are implemented and verified, execution remains limited to Phase 0 local fixtures and controlled experiments.

## Status legend

- **Implemented prototype**: present in Phase 0 source and focused tests, but not a product boundary.
- **Required Phase 1 control**: must be designed, implemented, and tested before project-scoped execution.
- **Deferred**: intentionally excluded from the first MVP.

## 1. Trust boundaries and assets

### Trust boundaries

```text
Authenticated user
  -> API authorization boundary
  -> project-scoped persistence and artifact boundary
  -> signed/minimal job boundary
  -> isolated browser worker boundary
  -> egress/DNS enforcement boundary
  -> approved external test target

Optional model provider (deferred by default)
  <- sanitized, policy-derived planner packet only
```

No component may treat content crossing a boundary as trusted merely because it originated from a registered target, browser page, artifact, or model provider.

### Assets and protection goals

| Asset | Protection goal | Current status |
| --- | --- | --- |
| User identity and project membership | Authenticate identity; authorize every project-scoped operation. | Required Phase 1 control. |
| Target registration and policy | Prevent one project from executing another project’s target or expanding scope through page content. | Local `ActionPolicy` prototype only. |
| Test definition and assertion | Preserve approved objective/policy/version and prevent unapproved modification. | Required Phase 1 control. |
| Execution and attempt history | Immutable lineage, tenant isolation, idempotency, and auditability. | Required Phase 1 control. |
| Browser worker | Prevent target code from reaching host, peer projects, internal networks, secrets, or durable storage. | Required Phase 1 control. |
| Credentials, cookies, sessions | Do not collect, persist, or expose them in the initial MVP. | Phase 0 avoids common fields; product support is deferred. |
| Evidence and reports | Preserve sanitized facts, provenance, and integrity while limiting access and retention. | Typed sanitization/digest prototype implemented. |
| Screenshots/traces/downloads | Avoid visual/content data disclosure. | Disabled for product MVP; Phase 0 artifacts are controlled local-only evidence. |
| Gemini/API credentials | Keep provider credentials out of logs and browser targets. | Environment-based prototype only; provider use is deferred by default. |

## 2. Authentication, authorization, and tenant isolation

### Initial MVP role model

The initial role model is intentionally small:

| Role | Allowed actions | Explicitly not allowed |
| --- | --- | --- |
| Project administrator | Create project, manage membership, approve/revoke targets, approve policy-scope expansion, view all project reports. | Access another project; approve credentials or public targets in the initial MVP. |
| Test operator | Create/edit an approved-scope test definition, request an execution or manual retry, view project evidence/reports. | Register/expand target scope, manage membership, enable provider integration. |
| Reviewer | Read project test definitions, execution history, sanitized evidence, and reports. | Change policy, execute/retry, or access raw artifacts. |

An authenticated request carries an identity and is authorized against an explicit project membership before the API loads or mutates a project resource. There is no implicit “current project,” URL-only authorization, global reviewer, or cross-project search.

### Tenant isolation rules

Required Phase 1 controls:

1. Every durable entity has a non-null `project_id`: target, test definition/version, execution, attempt, evidence, finding, report, artifact metadata, and audit event.
2. Every query is project-scoped before lookup and every service method receives an authorization context; an object ID alone is never authority.
3. A worker job includes immutable project/execution/attempt identifiers and a policy version. The worker rejects mismatched ownership and cannot choose another project’s artifact prefix.
4. Artifact metadata and storage paths are derived server-side from project/execution/attempt IDs; callers cannot supply filesystem paths or object-store keys.
5. Browser contexts, profiles, temporary directories, and outbound policies are fresh per attempt and destroyed after it ends.
6. Authorization denials return safe categorical errors and are audit logged without leaking target URLs, evidence, or membership data.

The first MVP is a single organization/team deployment model, but the project boundary must be implemented as if projects are mutually untrusted. This avoids a future multi-project migration that would expose prior data.

## 3. Permitted targets and execution authorization

### Initial permitted target class

The sole remotely reachable class is an **organization-controlled, dedicated, anonymous test environment** meeting all of these requirements:

- It is registered by a project administrator as an exact HTTPS origin (scheme, hostname, and permitted port) with bounded allowed path prefixes.
- Its hostname resolves to publicly routable addresses at registration and immediately before every connection.
- It has no production data, authentication requirement, payment flow, or user-supplied secrets.
- It is intended for automated testing by the owning organization and has an accountable owner.
- It is reachable only through the worker’s enforced allowlisted egress path.

Developer local mode may use `file://` fixtures and loopback-only controlled scenarios, but local mode is not a shared or hosted product execution path.

### Explicitly prohibited in the first MVP

- IP-literal targets and arbitrary user-provided URLs.
- `file:`, `data:`, `blob:`, `javascript:`, `ftp:`, `ws:`, and custom schemes as navigation targets.
- Loopback, link-local, private, carrier-grade NAT, multicast, reserved, metadata-service, and IPv6 local/private address ranges from hosted workers.
- Internal DNS suffixes, unresolvable names, targets requiring VPN/intranet routing, and non-default ports unless explicitly approved by a later security decision.
- Redirects to a different scheme, hostname, port, or disallowed path.
- Browser downloads, uploads, popups, permission grants, clipboard access, device/media access, and service integrations.
- Production targets and all authenticated flows, including test accounts, cookies, tokens, MFA, and password entry.

### Human execution authorization

- The test operator must explicitly request each execution and retry.
- The system verifies that the target, test-definition version, action policy, deterministic assertion, and target-policy version remain approved at dispatch time.
- Any target scope expansion, action-policy expansion, or `fill` enablement requires project-administrator approval and generates an audit event.
- No planner, page, model, or worker may create a test, expand a policy, trigger a retry, or authorize a target.

## 4. SSRF, DNS rebinding, redirects, and egress

Application-level validation is necessary but insufficient. The worker network is the enforcement point.

### Required target onboarding validation

1. Canonicalize the submitted origin; reject credentials, query/fragment, IP literals, unsupported schemes, and malformed hosts.
2. Resolve A and AAAA records using the controlled resolver; reject any answer in denied address ranges.
3. Store the approved origin, path prefixes, target-policy version, initial DNS decision metadata, and approving administrator identity.
4. Require the target owner to confirm control through an out-of-band organization process; do not infer ownership from page content.

### Required per-request enforcement

1. Resolve immediately before navigation and verify all resolved addresses against the deny list.
2. Bind the actual outbound connection through an egress proxy/firewall that permits only the approved origin’s currently validated addresses and port. Do not rely solely on a pre-navigation DNS lookup.
3. Revalidate every HTTP redirect before following it. Cross-origin redirects fail closed and are recorded as a safe execution-policy error.
4. Deny all other outbound TCP/UDP traffic from the worker, including cloud metadata endpoints and peer-worker networks. Permit only required DNS to the controlled resolver and HTTPS to the approved target.
5. Block WebSocket, WebRTC, service-worker, and other browser-originated network paths unless a later target-policy version explicitly supports them with equivalent enforcement.

DNS rebinding is mitigated by resolving and checking at connection time, enforcing the connection through network policy, and rejecting redirect/destination changes. It is not solved by URL allowlists in `ActionPolicy` alone.

## 5. Browser worker isolation and resource limits

### Required worker boundary

Phase 0 runs Playwright in the local process. That is not a safe boundary for target-controlled JavaScript. The hosted MVP requires:

- One ephemeral, non-privileged worker sandbox per attempt; no shared browser profile, cookies, local storage, downloads, or writable project directory.
- Browser worker separate from the API, persistence, artifact service, and control plane identity.
- Read-only application image, minimal writable temporary directory, no host mounts, no Docker socket, no cloud-instance credentials, and no shell/command execution capability exposed to pages or planners.
- Browser permissions denied by default; downloads disabled; popup/new-page requests blocked unless the approved policy explicitly permits them in a later version.
- A minimal job payload containing IDs and approved policy data, not user credentials, raw artifacts, or other-project data.
- A worker identity that can write only attempt-scoped sanitized evidence/artifact metadata and can report status only for its leased attempt.

### Required limits

The deployment ADR must set and test a conservative limit profile. At minimum it must enforce:

- one browser and one context per attempt;
- maximum wall-clock duration, navigation timeout, action timeout, assertion timeout, and action budget;
- CPU, memory, process-count, disk-temp-space, artifact-size, and network-response-size limits;
- a bounded queue lease/heartbeat and forced cleanup on timeout, cancellation, or worker loss;
- global and per-project concurrency quotas.

Phase 0 demonstrates fresh contexts and bounded action/timeout controls in controlled scenarios, but it does not establish numeric safe capacity limits. Numeric values remain a deployment-ADR decision and must be benchmarked before being advertised.

## 6. Prompt injection and untrusted page content

Page text, labels, accessibility names, browser errors, traces, screenshots, network responses, model responses, and artifact content are untrusted data.

### Existing prototype protections

- `PlaywrightBrowserTool.observe()` derives permitted elements from `ActionPolicy`; page content does not grant authority.
- Observations are bounded and omit input values, password/hidden fields, cookies, page-wide HTML, and sensitive query data.
- `GeminiPlanner` is optional, bounded, schema-constrained, and passes output through action validation; its planning rules say page content is untrusted.
- Evidence sanitization allows only bounded fields and safe error categories.

### MVP decision

Gemini planning and LLM investigation are disabled by default and not required for the first product slice. If an administrator later enables a planner, it may receive only a schema-versioned, allowlisted, sanitized packet. Its only possible output is one typed action; static policy and runtime observation checks remain authoritative. It cannot receive raw page text, raw exception messages, traces, cookies, credentials, or evaluation ground truth.

Before enabling a model for any project, implement P0.4D-style hostile-content tests covering instruction-bearing page text, labels, logs, error messages, and model output. A model may not classify a failure more confidently than deterministic evidence or initiate side effects beyond an already approved typed action.

## 7. Credential, session, and secret handling

### MVP decision: anonymous execution only

The initial product MVP has no user credentials, stored secrets, test accounts, cookies, authorization headers, OAuth flows, session import/export, password fills, or MFA support. A project test must operate against an anonymous controlled target.

This choice resolves the immediate data-classification question: the first hosted MVP accepts only internal non-sensitive test content. It does not process production personal data or secrets by design.

### Future credential gate

Credentialed testing is deferred until a separate security decision defines:

- encrypted secret storage, key management, rotation, and least-privilege access;
- ephemeral just-in-time injection into one worker attempt;
- browser-context destruction and no cookie/session persistence;
- guaranteed redaction from logs, evidence, screenshots, traces, downloads, and provider prompts;
- approval workflow, audit trail, incident response, and revocation.

Environment variables in `GeminiPlanner.from_environment()` are acceptable for the Phase 0 local experiment only. They are not the product secret-management design.

## 8. Evidence, artifact privacy, retention, and access

### MVP data policy

- Sanitized evidence JSON, deterministic findings, and execution metadata are the only retained execution data in the initial hosted MVP.
- Screenshots, Playwright traces, browser downloads, response bodies, headers, cookies, form values, raw page text, and raw exception messages are disabled and not retained.
- Evidence fields remain allowlisted, bounded, immutable after finalization, and digest-verified. SHA-256 is modification detection relative to a trusted digest, not evidence authenticity or correctness.
- Default retention for sanitized execution/evidence/report metadata is **30 days**, then deletion is scheduled and recorded. A project administrator may request earlier deletion; retention extension is out of scope for the first MVP.
- Evidence/report reads are authorized through project membership. No public artifact URLs or client-selected storage keys are permitted.

The 30-day policy is an MVP product decision, not an implemented retention mechanism. Its legal/compliance suitability must be revisited before handling regulated or personal data.

### Artifact expansion gate

Visual artifacts can expose sensitive content even when evidence JSON is sanitized. Screenshots/traces require a later design covering explicit opt-in, content classification, encryption, short retention, audited access, export restrictions, malware/download scanning, deletion verification, and provider-prompt exclusion.

## 9. Abuse prevention, audit logging, and rate limits

### Required abuse controls

- Require authenticated, project-authorized execution requests; reject unauthenticated and cross-project requests before job creation.
- Apply rate limits to login/session endpoints, target registration, test-definition changes, execution creation, retry, artifact/report reads, and provider enablement.
- Enforce per-project active-execution and queued-execution quotas; first MVP defaults to one active browser attempt per project until capacity benchmarks justify more.
- Limit target registrations and policy-scope expansions; require administrator approval and audit each decision.
- Reject oversized objectives, policies, selectors, evidence, artifacts, and job payloads using explicit server-side limits.
- Monitor safe counters for rejected targets, denied egress, policy violations, worker termination, timeout, cleanup failure, and repeated execution requests.

### Audit events

Record immutable, project-scoped audit events for: authentication/session lifecycle; membership/role changes; target approvals/revocations; test-definition/policy versions; execution/retry/cancel requests; worker lease/terminal state; artifact/evidence deletion; report access; and any provider opt-in. Events include actor ID, project ID, resource ID/version, action, timestamp, outcome, and safe reason category—never credentials, raw page content, or secret values.

Exact rate and quota values are unresolved operational configuration. Their enforcement and safe failure behavior are Phase 1 acceptance criteria, not values inferred from the local experiments.

## 10. Threat scenarios, mitigations, and residual risks

| Threat scenario | Required mitigation | Residual risk |
| --- | --- | --- |
| User submits a metadata-service/private-IP URL | Canonical origin validation, DNS/IP deny list, egress firewall/proxy, no IP literals. | DNS/network policy defects; requires integration tests and monitoring. |
| Approved host DNS rebinds to a private address | Resolve/check at connection time; egress permits only validated addresses; deny redirects. | Dynamic CDN/address changes may produce safe execution failures. |
| Target redirects browser to a malicious host | Revalidate every redirect; cross-origin/path expansion fails closed. | Same-origin target content can still be hostile within its approved scope. |
| Malicious page instructs model to reveal data or expand actions | LLM disabled by default; sanitized policy-derived context only; schema/action validation; hostile-content testing before opt-in. | Prompt injection remains an evolving risk; no P0.4D evidence yet. |
| Page exploits browser/worker or exhausts resources | Ephemeral sandbox, non-privileged worker, network/filesystem isolation, hard resource limits, patched browser image. | Browser zero-days and sandbox escapes require patching, monitoring, and incident response. |
| Cross-project report/artifact access | Project-scoped authorization, storage prefixes, server-side lookup, no public URLs, audit logs. | Authorization implementation defects; requires negative tests. |
| Sensitive value enters evidence/log | Anonymous-only MVP; allowlisted sanitizer; safe categories; no raw artifacts. | Novel fields/third-party logs can bypass a flawed mapper; maintain tests and review. |
| Worker replay/duplicate execution | Idempotency key, attempt lineage, lease ownership, terminal immutability, audit events. | At-least-once delivery still needs safe duplicate detection and user-visible lineage. |
| Abuse causes excessive cost | Auth, quotas, one active attempt/project, bounded time/resources, provider disabled by default. | Legitimate burst demand waits; capacity policy must be measured. |
| Insider changes target/policy scope | Administrator approval, versioned policy, audit event, dispatch-time approval check. | Authorized administrators remain trusted; governance is out of scope. |

## 11. Phase 1 security acceptance criteria

Project-scoped execution cannot be enabled until all of the following are demonstrated:

1. Authentication and the administrator/operator/reviewer authorization matrix are enforced by API integration tests, including cross-project negative cases.
2. Every persisted execution-related entity and artifact metadata row has project ownership; object/path access is derived server-side and cross-project retrieval fails.
3. Target onboarding rejects prohibited schemes, IP literals, private/local/reserved resolution, unsupported ports, and unapproved path/host changes.
4. Worker egress tests prove denied access to loopback, private ranges, metadata addresses, peer services, and redirect destinations; approved target access succeeds only through the enforcement path.
5. Each attempt runs in a fresh isolated worker/browser context with no inherited cookies, local storage, downloads, host mounts, or ambient cloud credentials.
6. Hard timeout, action-budget, CPU/memory/process/disk, artifact-size, and cancellation cleanup limits are enforced and produce safe terminal states.
7. Initial hosted executions cannot enter credentials, persist sessions, upload/download files, or retain screenshots/traces; sanitizer and log tests demonstrate exclusion of sensitive fields.
8. Audit events capture authorization, target/policy, execution, and deletion activity with no secret leakage; rate/quota tests reject abuse safely.
9. Deterministic assertions and classifier evidence isolation remain unchanged; missing/contradictory evidence continues to resolve conservatively.
10. Gemini/planner use remains disabled unless separately approved after hostile-content and provider-data-flow acceptance tests.

## 12. Unresolved security decisions and blockers

| Decision | Status | Blocker impact |
| --- | --- | --- |
| Identity provider/session mechanism | Unresolved | Blocks authenticated API implementation, but not local architecture work. |
| Worker platform and exact sandbox technology | Unresolved | Blocks remote target execution; deployment ADR must select it. |
| Egress proxy/firewall implementation and resolver | Unresolved | Blocks any hosted target execution. |
| Numeric resource, quota, and rate-limit profile | Unresolved pending benchmark | Blocks capacity promises; initial conservative values must still be chosen before launch. |
| Database encryption/backups/deletion verification | Unresolved | Blocks persistence of even sanitized multi-project history until data model/hosting ADR covers it. |
| Visual artifact opt-in policy | Resolved for first MVP: disabled | Must be redesigned before screenshots/traces are product features. |
| Credentialed test support | Resolved for first MVP: prohibited | Requires a separate threat model and secret-management design. |
| LLM planner/investigation | Resolved for first MVP: disabled by default and not required | Requires P0.4D grounding/injection evaluation before opt-in. |

## Decision summary

The smallest defensible MVP security boundary is authenticated, project-scoped execution of anonymous, organization-controlled test targets only, through an isolated browser worker with enforceable egress restrictions. Phase 0’s action validation, observation bounds, evidence sanitization, and deterministic classification are retained as defense in depth; they do not substitute for authentication, tenant isolation, network enforcement, or sandboxing.
