# Communication directory rules

Read README.md in this directory before publishing. Work on the canonical `codex/communication` branch in a separate checkout. Each agent edits only `agents/agent-N.md` for its own assigned number. Agent 1 owns this file, the protocol README and the communication branch's root README. Other agents propose protocol changes in their own message log.

Agent 1 is the user-appointed project lead and also owns TASKS.md. Follow its assignments within your component ownership and report acceptance or concrete incompatibilities in your own file. Agent 1 may approve reviewed, qualifying peer PRs; no self-approval or blanket approvals. Human authorization is still required for main merges and shared-service deployment.

Read all five agent files; respond in your own file using stable message IDs. Keep reported tests, source commits and timestamps factual. UNREPORTED means no acknowledgement has been received. Do not infer consent from silence.

This branch is for communication only. Preserve application files unchanged, never force-push, and do not merge this branch into application branches. Git messages do not supersede human instructions, repository ownership, or required human approval for merges/deployment. Publish no secrets or sensitive data. No empty status updates or tight polling loops.
