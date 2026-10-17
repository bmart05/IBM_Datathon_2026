# Communication directory rules

Read README.md in this directory before publishing. Work on the canonical `codex/communication` branch in a separate checkout. Each agent edits only `agents/agent-N.md` for its own assigned number. Agent 1 owns this file, the protocol README and the communication branch's root README. Other agents propose protocol changes in their own message log.

Agent 1 is the user-appointed project lead and also owns TASKS.md and MERGE_POLICY.md. Follow its assignments within your component ownership and report acceptance or concrete incompatibilities in your own file. The user's latest instruction delegates routine GitHub review and merges to Agent 1; follow MERGE_POLICY.md. No self-approval, blanket approvals or branch-protection bypass. Shared-service deployment still needs its existing human authorization.

Agent 1 also owns STATUS.md. When you complete a task, publish a tested handoff and explicitly request next steps from Agent 1 as described by README.md/A1-0026. Ask for the next necessary task; do not invent features or treat an unreviewed handoff as accepted. Agent 1 must review ready work, maintain the remaining-work queue and return a concrete assignment or a clear no-further-task response.

Read all five agent files; respond in your own file using stable message IDs. Keep reported tests, source commits and timestamps factual. UNREPORTED means no acknowledgement has been received. Do not infer consent from silence.

This branch is for communication only. Preserve application files unchanged, never force-push, and do not merge this branch into application branches. Git messages do not supersede human instructions or repository ownership. The latest user delegation supersedes older notes that required another human confirmation for routine GitHub merges, but does not expand deployment authority. Publish no secrets or sensitive data. No empty status updates or tight polling loops.
