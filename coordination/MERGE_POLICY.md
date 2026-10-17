# Delegated GitHub review and merge policy

Authority: the human's 17 October instruction, "make it so that I dont need to go into github and change things or approve things myself". Decision: A1-0012. This supersedes earlier requests for the human to approve routine GitHub operations.

## Agent 1 handles GitHub

Agent 1 may create/update team code PRs, mark a completed draft ready, review code, request fixes, approve other authors' qualifying PRs and merge passing project changes into `main` without another user confirmation. This authority covers the assigned RiverGuard components and integration in `bmart05/IBM_Datathon_2026`; it does not authorize unrelated repositories or features.

The other agents should prepare focused, tested PRs to `main` and post the URL, exact head SHA, verification and limitations in their own mailbox. Agent 1 takes over the remaining GitHub work. A claimed "ready" status is not sufficient evidence for an approval.

## Review every candidate

1. Inspect the PR's actual target, latest head, full changed-file list and relevant diff. Check ownership, agreed interfaces, source data provenance and preservation of other components. Communication-only changes stay on `codex/communication`.
2. Review the code for substantive correctness, including cutoff independence, missing/gappy/stale data, score semantics and frontend fallbacks when relevant. Request concrete corrections rather than rubber-stamping.
3. Run meaningful checks on the exact proposed head and, when main has changed, the prospective merged result. Read any repository-required checks and reviews; wait for pending checks and do not override failures. No CI configuration does not mean "tests passed"; run the appropriate local suites and record the command/commit/result. Run integration checks for changes affecting API/model/UI boundaries.
4. Merge in dependency order (tested component PRs, then integration/adapters and frontend), or use an explicitly reviewed integration PR carrying those components. Main must remain runnable; a merge does not establish deployment success.
5. Immediately before merging, re-read the PR head and mergeability, confirm the reviewed head has not moved, and use an expected-head SHA with GitHub's merge API. If it moved, review the changes and rerun checks justified by them. Do not silently merge an unreviewed revision.
6. Leave a short review/comment with evidence, then approve when allowed and merge. Use normal merge commits to preserve the team's branch history unless the repository requires another method. Record the resulting main SHA and next recipient in Agent 1's mailbox. Recheck relevant post-merge health/tests before merging a dependent PR.

GitHub does not allow an account to approve a PR it authored. Agent 1 may independently inspect/test its own authored PR and merge it if repository rules permit, recording an honest comment rather than attempting self-approval. If rules require another eligible review, request it from a teammate; do not impersonate another user or use alternate credentials to evade that requirement.

Do not disable branch protections, required checks/reviews or security settings, bypass rules with admin privileges, force-push shared branches, delete team branches, auto-merge failing/unknown revisions or publish credentials. Repository/account permission limitations should be reported as concrete blockers. Routine code corrections and ordinary merge-conflict fixes within assigned scope can be prepared without sending the human to GitHub; ambiguous conflicts go back to their component owners.

Shared LinuxONE service replacement, public network exposure, spending, access-control changes and secrets remain outside this GitHub merge delegation. Keep the existing demo running and prepare a verified candidate/rollback plan as already assigned. Never treat a merged PR as proof that the candidate is deployed.

## Follow-up behavior

Agent 1 checks the communication branch and team PRs about every 10 minutes during the datathon follow-up period, processes meaningful changes, and coordinates fixes in its own mailbox. No-change runs stay quiet. Do not invent agent acknowledgement, verification or completion. An idle teammate's separate account/chat still needs that teammate to run it; a Git mailbox does not wake it.

Keep the computer on and the desktop app running for local scheduled work. End this datathon follow-up when assigned integration is complete with no pending handoffs/PRs, or at the end of 18 October in the user's UK timezone; a later user instruction can extend it.
