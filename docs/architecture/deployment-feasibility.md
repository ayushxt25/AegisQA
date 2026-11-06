# AegisQA deployment feasibility

## Decision and scope

This is a documentation-only deployment feasibility assessment for the deliberately narrow Phase 1 MVP in `docs/product/mvp-definition.md`. It preserves the P0.7 rule: **remote browser execution remains disabled until per-attempt isolation, controlled DNS, and SSRF-resistant egress are implemented and demonstrated.** It does not authorize deployment, add infrastructure, or claim production readiness.

**Recommendation: CONDITIONAL GO for local development and hosted control-plane prototyping; NO-GO for hosted browser execution today.** Docker Compose is a practical local topology. A hosted FastAPI/PostgreSQL control plane is feasible, but no low-cost managed platform reviewed here independently establishes the full browser-worker security boundary. AWS ECS/Fargate plus RDS is the leading hosted candidate to validate, not an approved remote-execution deployment.

## 1. What has and has not been validated

| Area | Status | Evidence / implication |
| --- | --- | --- |
| Local Python Playwright Chromium | Verified controlled experiment | P0.1 and P0.4C records; this is not container, worker, or public-target validation. |
| Local controlled browser suite | Verified in the user's Windows/Chromium environment | P0.4C.8: 75/75 tests, six controlled scenarios. It is not a capacity, multi-tenant, or network-isolation benchmark. |
| FastAPI, PostgreSQL, SQLAlchemy, Alembic | Planned only | Defined in `backend-execution-design.md`; no application implementation or deployment exists. |
| Docker Compose / Linux Playwright container | Feasible by provider documentation; untested in this repository | Requires a pinned Playwright image, compose configuration, and local acceptance run. |
| Hosted API/database | Feasible in principle | Provider capabilities are documented below; selection/configuration are unimplemented. |
| Hosted browser-worker isolation and egress control | Not verified | It remains a Phase 1 P0 gate, even where a platform can run a container. |

## 2. Source register and verification status

All provider facts and prices below were checked on **2026-11-06** against the linked official pages. Prices are USD and region/configuration dependent; they are planning inputs, not quotes. Provider plans and limits can change before implementation.

| Source | Facts used | Verification status |
| --- | --- | --- |
| [Playwright Python Docker](https://playwright.dev/python/docs/docker) | Pinned browser images are available; root disables Chromium sandbox; an unprivileged user plus seccomp is recommended for untrusted browsing; image is explicitly described as testing/development-oriented. | Official; current page checked. |
| [Docker Desktop WSL 2 backend](https://docs.docker.com/desktop/features/wsl/) | WSL 2 is supported and uses a full Linux kernel; Docker notes Hyper-V as a stricter isolation alternative. | Official; current page checked. |
| [AWS Fargate task networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html) and [Fargate constraints](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html) | Fargate gives each task an ENI in `awsvpc`; custom DNS servers and `disableNetworking` are unsupported. | Official; current pages checked. |
| [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/) | Linux/x86 US East example rates: $0.000011244 per vCPU-second and $0.000001235 per GB-second; 1-minute minimum; 20 GB ephemeral storage included. | Official; current page checked. |
| [Render compute plans](https://render.com/docs/compute-plans), [background workers](https://render.com/docs/background-workers), and [pricing example](https://render.com/articles/production-rails-hosting-guide) | Long-running background workers exist; a 1 CPU/2 GB worker plan exists; the cited current Render example lists $7 small web, $25 1c/2g, and $6 small Postgres. | Official provider documentation/article; use as indicative only. |
| [Railway pricing](https://docs.railway.com/pricing) and [plan limits](https://docs.railway.com/pricing/plans) | Hobby is $5/month minimum including $5 usage; RAM $10/GB-month, CPU $20/vCPU-month, egress $0.05/GB; free plan is $1 monthly resource credit with 0.5 GB RAM/1 vCPU per service limit. | Official; current pages checked. |
| [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract), [service timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout), and [Cloud Run pricing](https://cloud.google.com/run) | Jobs exit at completion and receive SIGTERM/SIGKILL at timeout; services can scale to zero and request timeout does not itself terminate a continuing container; free allowance is limited and usage-based pricing applies. | Official; current pages checked. |

## 3. Recommended local development topology

```text
Windows host
  -> Docker Desktop (WSL 2 for ordinary development; Hyper-V preferred for hostile-page isolation tests)
       -> compose network: api (FastAPI, planned) <-> postgres (planned)
       -> separate worker container (planned) -> one non-root Chromium process/context per attempt
       -> local fixture / explicitly controlled test target only
```

### Feasibility and operating rules

- Use Docker Compose only for development and controlled test targets. It is the smallest reproducible way to keep FastAPI, PostgreSQL, and the browser worker separate without adding Redis, Celery, or another queue. PostgreSQL remains the queue/system of record defined by P0.8.
- Prefer a Linux Playwright image pinned to the repository's Playwright version. Playwright documents that browser binaries cannot be mixed freely across versions. Do not use Alpine for this image because Playwright documents musl-based Alpine as unsupported.
- Run the browser as an unprivileged user with the documented seccomp profile; do not accept a root browser merely to simplify setup. Avoid `--cap-add=SYS_ADMIN` except a narrowly documented local debugging case, never for the proposed secure worker profile.
- Give the worker its own read-only source mount, bounded writable temporary directory, no Docker socket, no host-network mode, and no shared browser profile. Compose network segmentation is helpful for development, but it is not sufficient evidence of production SSRF resistance.
- On Windows, WSL 2 is convenient and is Docker Desktop's normal Linux backend. Its integration intentionally permits host/WSL interoperability. Use Hyper-V or Enhanced Container Isolation when testing hostile-target containment, and keep the repository and artifacts outside broad host mounts. Windows/WSL2 local execution is therefore a development environment, not a tenant-isolation claim.
- The first Compose acceptance run should use only the fixture server or `127.0.0.1` controlled HTTP scenario. Any non-local target remains disabled until the P0.7 controls are proven.

## 4. Hosted deployment options

### Compared combinations

| Combination | Practical strengths | Material limitations | Security conclusion | Indicative monthly cost |
| --- | --- | --- | --- | --- |
| **A. AWS ECS/Fargate + RDS PostgreSQL + ECR/VPC** | Separate API and worker/task deployment, task-level ENI/security-group controls, explicit stop capability, managed PostgreSQL. A per-attempt Fargate task is a plausible isolation unit. | Fargate does not support custom DNS server or `disableNetworking`; security groups are IP/port controls, not a complete hostname/rebinding policy. Secure DNS/egress and logs/NAT can dominate cost. The P0.8 PostgreSQL polling worker needs a task-launch design before per-attempt tasking is valid. | **Candidate for a security proof of concept only.** Do not enable external targets until DNS/egress and forced-stop tests pass. | Approximately **$70–$150/month** for a small always-on API, small RDS instance, modest logs/storage, and low worker use; secure egress components can materially exceed this. See assumptions below. |
| **B. Google Cloud Run service/jobs + Cloud SQL PostgreSQL** | A Cloud Run Job is naturally bounded and exits; provider sends SIGTERM then SIGKILL at job timeout. API can scale to zero; jobs provide a potential per-attempt boundary. | Cloud Run service request timeout can end the client request while container work continues; jobs still need a dispatcher/queue integration. No reviewed documentation establishes controlled DNS/FQDN allowlisting or browser-specific hostile-page isolation. Cloud SQL pricing requires region and instance choices. | **Technically plausible but not compatible with the P0.8 worker model without design work.** Not approved for remote browsing. | **$30–$120+/month**, dominated by Cloud SQL and always-available control-plane needs; browser-job compute is usage-based. This range is intentionally not a quote. |
| **C. Render web service + Render Postgres + background worker** | Very small operational footprint; documented continuously running background workers; simple developer/demo deployment. | A worker may initiate outbound requests, and the reviewed docs do not establish per-attempt sandbox destruction, controlled DNS, FQDN egress enforcement, or a browser-worker security boundary. Small plans may be memory constrained for Chromium. | **Development/demo control plane only; reject for approved remote browser execution.** | About **$38/month before bandwidth/workspace charges** using Render's cited $7 small API, $25 1c/2g worker, and $6 small Postgres example. |
| **D. Railway API + PostgreSQL service + long-running worker** | Straightforward containers and consumption pricing; hobby plan is accessible for a prototype. | Free is only $1 resource credit; continuously running 1 vCPU/2 GB browser capacity is roughly $40/month before API/database/egress. Reviewed docs do not establish DNS/egress controls or per-attempt browser isolation. | **Development/demo only; reject for remote browser execution.** | Roughly **$55+/month** before PostgreSQL and egress for an always-on 0.5 vCPU/0.5 GB API plus 1 vCPU/2 GB worker, including the $5 Hobby minimum; database raises this. |

### Recommended hosted topology, when its gates are met

The recommended **candidate** is AWS: public FastAPI behind HTTPS, private PostgreSQL, and an isolated Fargate worker/task boundary in a dedicated VPC. The API has no browser process; the database accepts only API/worker security groups. A worker has no inbound public endpoint. A worker sandbox receives only immutable job/version data and can write only through the project-scoped repository boundary.

```text
User -> HTTPS API (FastAPI) -> PostgreSQL queue/system of record
                                  |
                                  v
                    worker launcher / isolated one-attempt Fargate task
                                  |
                    approved DNS resolution + enforced egress policy
                                  |
                       anonymous, controlled test target only
```

This is not yet deployable because the following design proof is missing: how the PostgreSQL queue's worker launcher creates and supervises one task per attempt without permitting task credentials, arbitrary egress, or automatic retries. If that proof is not accepted, retain a hosted API/database only and run browser evaluation locally against fixtures.

### Provider limit details that must influence selection

- **AWS Fargate:** a Linux/x86 task can be sized from 0.25 vCPU/0.5 GB upward; the checked pricing page includes 20 GB ephemeral storage and bills from image download to task termination with a one-minute minimum. Each task has its own ENI, but Fargate does not support `dnsServers` or `disableNetworking`; this is a direct limitation for this threat model, not a configuration omission. RDS PostgreSQL maximum connections, storage, backups, and availability depend on the selected DB instance class and parameter group, so no connection count is assumed here. The selected RDS configuration must reserve connections for the API, worker, migrations, and administrative recovery, and use a bounded application pool.
- **Cloud Run:** a Job can be a bounded browser unit, but its writable filesystem is memory-backed and is lost on stop; temporary browser data therefore consumes the configured memory limit. A service can scale to zero and has a default 300-second request timeout (up to 3,600 seconds), but ending the request does not necessarily stop continued container work. Cold start, image-pull time, job quota, Cloud SQL connection limits, storage, and network-egress cost must be measured in the chosen region; none is assumed safe or free.
- **Render:** background workers are continuous and have no inbound URL. The documented 0.5 CPU/512 MB smallest worker is not accepted as a Chromium baseline; begin any non-security test at the documented 1 CPU/2 GB plan. Render's cited small Postgres example lists 100 connections, but this must be verified against the exact plan before configuring SQLAlchemy pooling. Its low operational friction does not solve browser sandbox, DNS, or egress enforcement.
- **Railway:** its Free plan is explicitly only $1 of monthly resource credit, not a durable free worker. Free services are capped at 0.5 GB RAM/1 vCPU; Hobby allows larger services but bills CPU, memory, volume storage, and egress by usage. The official plan pages reviewed do not establish a PostgreSQL connection limit or browser-specific container guarantee for this use case; treat both as pre-deployment verification items rather than inferred capabilities.

### Conservative fallback

If a task-isolation and egress proof cannot be funded, keep all browser work local in Docker Desktop/WSL2 or a dedicated organization-controlled development machine, and host **no browser worker**. A hosted control plane may store only sanitized metadata once the API/authentication/persistence work exists, but it must not dispatch remote targets. This is preferable to selecting an inexpensive worker service that violates the MVP security boundary.

## 5. Cost assumptions and billing risks

The estimates above assume a single region, one small team, one concurrent browser attempt, sanitized metadata only, no screenshots/traces/downloads, and low target traffic. They exclude identity-provider charges, domain/DNS, observability retention, support, taxes, backups beyond provider defaults, and engineering time.

- AWS calculation anchor: the checked Fargate page's US East Linux/x86 rates make an always-on 0.5 vCPU/1 GB API approximately $18/month in Fargate compute, and an always-on 1 vCPU/2 GB worker approximately $36/month, before RDS, NAT, egress, logging, or storage. Each task has a one-minute minimum; image pulls count toward task duration. RDS and secure-egress choices require a region-specific calculator quote.
- AWS billing risk: a NAT gateway, inspection service, traffic logs, container image transfer, and idle persistent worker increase a small compute estimate rapidly. Do not use Fargate Spot for the authoritative worker lifecycle until interruption semantics are evaluated.
- Render billing risk: worker/API are continuously billed by selected plan; a 512 MB worker is not an accepted Chromium capacity baseline. The quoted article's prices are a current example, not an SLA or future-price guarantee.
- Railway billing risk: the $5 Hobby subscription is a minimum commitment, not an unlimited free tier. CPU, memory, and egress are metered; reaching zero account credit stops workloads according to its billing documentation.
- Cloud Run billing risk: scale-to-zero may reduce idle API cost, but a queue poller needs CPU while idle and a browser job may require warm-up/image pulls. Cloud SQL normally sets the baseline. A service request timeout must not be treated as browser termination.

## 6. Initial conservative defaults

These are planned deployment defaults, not measured capacity limits. Change them only after a pinned-environment benchmark and security test record.

| Control | Initial default | Reason |
| --- | --- | --- |
| Concurrent browser executions | 1 globally | Simplifies containment, forced termination, and evidence review while browser capacity is unbenchmarked. |
| Queue capacity | 25 queued jobs per project; 100 globally | Bounds storage and abuse before rate/capacity data exists. Reject excess requests safely rather than silently dropping them. |
| Lease duration | 90 seconds | Enough for a 20-second heartbeat margin while surfacing dead workers promptly. |
| Heartbeat frequency | 20 seconds | Four missed intervals leave a bounded detection window; conditional fencing remains mandatory. |
| Maximum execution deadline | 120 seconds, non-extendable | Enforced independently of the lease and API request lifecycle. |
| Cleanup grace | 15 seconds | Worker receives stop signal, then supervisor terminates browser/process group and revokes sandbox egress where supported. |
| Browser action/navigation timeout | 15 seconds each | Keeps one attempt bounded; assertion timeout is included in the 120-second deadline. |
| Evidence string / collection / mapping bounds | 256 characters / 20 items / 16 entries | Matches current `failure_intelligence/contracts.py` sanitizer bounds. |
| Evidence nesting / serialized record limit | depth 3 / 4,096 bytes per finalized record | Matches existing deterministic evidence contracts; reject, do not truncate into authority. |
| Raw artifacts | Disabled | Screenshots, traces, cookies, page HTML, response bodies, and downloads remain outside the initial hosted MVP. |

## 7. Security feasibility checklist

| Required control | Feasible approach | Status / blocker |
| --- | --- | --- |
| Non-root browser | Dedicated Linux user and documented Playwright seccomp profile in a pinned image. | Feasible locally; must be tested in selected runtime. |
| Per-attempt isolation | Fresh Fargate/Cloud Run Job/isolated container rather than only a fresh Playwright context. | Architecture choice unresolved; Phase 0 fresh contexts are not enough. |
| Controlled DNS | Resolve approved hostname through a controlled resolver, re-check every resolved address against a deny list before connect, and bind egress to the verified result. | Required; Fargate's lack of custom DNS support makes this a design blocker for that platform as configured. |
| SSRF-resistant egress | Default deny; deny loopback/link-local/RFC1918/ULA and cloud metadata ranges; enforce destination/port at network boundary, validate redirects, and log safe categorical denials. | Required; security groups alone are insufficient. |
| Network isolation | No host networking; no Docker socket; worker has no public inbound listener; API and database in distinct security groups/networks. | Local design feasible; hosted implementation/test missing. |
| Forced termination | Worker detects fencing failure; external supervisor invokes runtime stop; runtime must kill browser child after the 15-second grace. | Required; Cloud Run Jobs document kill behavior, but integration is unimplemented. |
| Sandbox cleanup | Ephemeral writable space, no reusable profile/cookies, delete task/container after terminal result, and audit cleanup outcome. | Required; no implementation. |
| Evidence/artifact controls | Persist only contract-validated metadata in PostgreSQL; no raw artifacts in initial MVP; apply retention/deletion audit path. | Contract prototype exists; durable storage controls missing. |

## 8. Deployment acceptance tests

The remote worker remains disabled until all applicable tests pass in the selected pinned platform/runtime:

1. **Compose baseline:** Start planned API, PostgreSQL, and worker as separate services; run P0.4C F01–F06 against only controlled fixtures; record image, Chromium, Python, and host versions.
2. **Non-root Chromium:** Prove the browser UID is non-root and Chromium sandbox starts with the selected seccomp profile; fail deployment if a root fallback is required.
3. **Per-attempt lifecycle:** Run two attempts and demonstrate fresh browser/user-data directories, no cookie/profile persistence, bounded temporary files, and removal after each attempt.
4. **Lease-loss stop:** Deliberately stop heartbeats. Verify no stale database write succeeds, browser process is terminated by the deadline/grace, and no network connection survives after termination.
5. **Deadline/cancellation:** Verify the independent 120-second deadline and cancellation both kill browser work even if an API request has already returned or the worker loop is stuck.
6. **Egress/SSRF:** Attempt loopback, RFC1918, link-local, cloud metadata, IPv6 local, redirect-to-private, and DNS-rebinding targets. Verify all are denied at the enforced network boundary and the evidence/audit event has no sensitive content.
7. **Tenant isolation:** Attempt cross-project job, evidence, artifact, and database-record access; verify composite database constraints and API authorization reject each case.
8. **Finalization failure:** Induce invalid evidence and database unavailability. Verify no success or authoritative finding appears, invalid payload is not stored, browser stops, and recovery does not create a replacement attempt.
9. **Capacity/billing soak:** With concurrency fixed at one, run a declared number of bounded attempts, measure memory/CPU/image-pull/queue delay and provider bill, then decide whether limits may change.

## 9. GO / NO-GO decision

| Decision | Scope | Rationale |
| --- | --- | --- |
| **GO** | Local Compose planning and Phase 1 API/PostgreSQL implementation | Fits the approved FastAPI + PostgreSQL architecture and has no new queue infrastructure requirement. |
| **CONDITIONAL GO** | Hosted API/database control-plane prototype | Only after identity, project authorization, database constraints, backup/retention, and cost-account controls are selected and tested. |
| **NO-GO** | Hosted browser execution against any remote target | No selected provider/configuration has yet demonstrated the required browser isolation, controlled DNS, SSRF-resistant egress, and forced-termination contract. |

## Open decisions and next sequence

1. Decide whether Phase 1's first browser worker is local-only or whether to fund an AWS isolation/egress proof of concept.
2. If AWS is selected, specify the task-launcher authority, VPC DNS/egress enforcement mechanism, endpoint allowlist format, logging retention, and an actual regional cost estimate before writing deployment code.
3. Implement the durable API/database state machine and local Compose topology, then run the acceptance tests that do not require remote egress.
4. Only after the isolation proof passes, enable one anonymous organization-controlled remote target and repeat the controlled evaluation with pinned versions.

No provider free tier is selected for secure remote browser work. Free or low-cost plans are suitable only for disposable API/database experimentation and must not be treated as evidence of a secure worker boundary.
