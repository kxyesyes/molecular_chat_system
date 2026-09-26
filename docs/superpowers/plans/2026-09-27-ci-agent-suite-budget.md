# Core Agent regression capacity amendment

## Evidence, scope and delegated decision

The user delegates recommended choices and scoped, reviewed merges through
package 8. This independent CI-only prerequisite changes neither application
deadlines nor scientific acceptance. Baseline main is
82322c3d0098d44082e732b41084c71b4d73c230.

PR93 a84aa921b20de2417c830b2717bba7f77e54952c run36268575293 failed:
core job108478025405 executed20:11:29Z to20:21:29Z and exited124.
Collection succeeded:10742 original =10518 core +224 Web. Bounded log inspection
shows passing cases through94%, including successive admission cases until
20:21:28.878Z. This proves active progress up to termination, not that remaining
tests pass or why all tests run slower on that worker. Other seven execution/
static checks passed; the aggregate gate correctly failed.

Correction to an earlier handoff interpretation: PR90 increased combined Agent
capacity to1200s through two600s partitions; it never changed either command to
1200s. Its original document is correct and must remain as historical evidence.

Options: retry unchanged (no new evidence; rejected), add another partition
(more selection/coverage machinery), or transparently increase only the core
suite capacity. Choose the last option under delegated recommendation authority.
Core command_timeout becomes1200; Web stays600, other rows180/600, collection
180-total/60-child and job30min unchanged. Combined Agent capacity becomes1800s.
This explicitly relaxes the aggregate core-suite wall-clock guard, NOT any
individual correctness deadline, production timeout or performance claim.

## Allowlist and implementation plan

Only .github/workflows/quality.yml, tests/test_quality_workflow_contract.py and
this document. Deployment contracts require row count/selections, not600 for
every row, and remain unchanged. All matrix selections, diagnostic options,
offline flags, dependencies and nine-check merge gate remain unchanged.

1. Update the two existing exact budget assertions: agent1200, all other rows
   unchanged. Add a focused contract that execution plus collection fits the
   existing job budget with at least300s reserved for setup/teardown; preserve
   fail-fast=false and complete collection proof. Run actual RED before YAML.
2. Change only agent command_timeout from600 to1200. Run the full workflow and
   deployment contract modules through the approved isolated launcher; no raw
   pytest/app probes. Keep the sole local scientific execution slot, poll actual
   handles, retain all failed runs and platform skips.
3. Independent SOURCE review followed by fresh QUALITY and identical focused
   repeat. Exact-file stage/commit, draft PR, actual9checks plus collection proof,
   fully paginated reviews/threads, SHA-guarded squash and landed tree equality.
4. Align PR93 to verified landing, inspect unchanged six scientific blobs, rerun
   exact-head CI. Do not retry its old failed run or erase the failure.

Focused targets (authorized launcher only):
tests/test_quality_workflow_contract.py tests/test_deployment_assets.py.
Real scientific/model/UI acceptance and P7/P8 remain incomplete.

## Actual TDD evidence

Locke independently reviewed the written design and approved its bounded scope;
required exact30min job assertion is included. The parent holds the sole local
science slot for these two runs; no other local project execution overlapped.
Approved launcher is byte-equivalent to the PR93 integration copy except REPO;
SHA256 B7B2A93F66751EBE12EB8B2E5AA610B94BF6039319F45D7994AC8A5B915F5863.

RED15050/1b2746:3failed32passed2skipped7warnings24.61s, process/runner exit1.
All three failures were real600-versus1200 budget assertions; no setup failure.
GREEN47832/bb3fd4:35passed2skipped7warnings29.51s, process/runner exit0.
Skips: Linux GNU watchdog and unavailable Windows native sendmsg; both remain
required on Linux CI. Warnings:3SWIG +4FastAPI deprecated lifespan hooks.
Production change is exactly one YAML scalar; no src/ or deployment test edits.
Workflow SHA25665230B2EFF1732E7E656DABA4B055461C985C65B34BE572137BA687FD0061655;
contract SHA256CA40534CA84A4B3931DE27BEA8C65816F40267D695FEA5DD98B47E65CA40978A.
Parent diff check passes. SOURCE and fresh QUALITY/final CI remain required.
Both executions terminal; parent scientific slot released for reviewer repeat.

## Independent release evidence

Locke SOURCE approved the frozen implementation, no findings. Fresh Mendel
QUALITY independently executed the exact same two modules once:14555/0c0b28,
35passed2skipped7warnings21.48s, runner/process exit0. All20 captured hashes
unchanged; same platform skips and warning categories, no edits or retries.
Reviewer released the slot. Local dual-review gate satisfied; actual Linux
collection, platform-only tests and all9CI checks still required before merge.
