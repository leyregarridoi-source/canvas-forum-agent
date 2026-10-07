# Homework 3 submission: Canvas Forum Agent

**Code and setup instructions:** https://github.com/leyregarridoi-source/canvas-forum-agent
(setup steps are in `README.md`, section 2).

## 1. Forum activity (primary evidence)

All posts are in the Homework 3: Agent Discussion Forum. Each link opens the agent's entry inside its thread.
Every post was read back from Canvas after writing and recorded as `verified: true`.

| Run (ET) | Started by | Agent's post | In reply to |
|---|---|---|---|
| Oct 6, 10:57 | manual first cycle (tool approval) | [230449](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230449) | [230437](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230437) |
| Oct 6, 12:17 | scheduler | [230489](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230489), [230490](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230490) | [230478](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230478), [230451](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230451) |
| Oct 6, 17:09 (catch-up for 15:17) | scheduler | [230656](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230656) | [230654](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230654) |
| Oct 6, 18:17 | scheduler | [230685](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230685), [230687](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230687) | [230661](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230661), [230683](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230683) |
| Oct 6, 21:51 (late 21:17 slot) | scheduler | [230764](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230764), [230765](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230765) | [230763](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230763), [230762](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230762) |
| Oct 7, 06:54 (catch-up) | scheduler | [230968](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230968) | [230956](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=230956) |
| Oct 7, 13:10 (catch-up) | scheduler | [231118](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=231118), [231119](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=231119) | [231099](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=231099), [231100](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=231100) |
| Oct 7, 15:17 | scheduler | [231188](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=231188) | [231174](https://canvas.mit.edu/courses/40577/discussion_topics/448963?entry_id=231174) |
| Oct 7, TODO | scheduler | TODO new thread | (new top-level thread) |
| Oct 7, TODO | scheduler | none: deliberate skip | see section 3 |

## 2. Architecture and autonomy

- **Scheduler:** an OpenClaw automation (`canvas-forum-cycle`, cron `17 */3 * * *`, America/New_York; hourly on the final evening) starts an isolated agent turn with a fixed prompt. The OpenClaw Gateway runs as a Windows Scheduled Task, so no one presses a button. If the laptop sleeps through a slot, OpenClaw runs one catch-up cycle on wake (visible above as late or catch-up runs).
- **Canvas access:** only `canvas_tool.py` talks to Canvas, with my own token loaded from an environment file outside the repo. The LLM never sees the token and can run only three commands: `scan`, `post`, `finish`. The tool can only call a handful of endpoints on this one discussion topic and has no edit or delete.
- **Decision logic:** `scan` returns only new posts by others, fenced as untrusted text. The LLM (Claude, following `SKILL.md`) skips by default and posts only when it can add a concrete technique, trade-off, counter-example or question that nobody in the thread has said and that it has not said before. At most one post per cycle.
- **Persistent local memory:** `~/.canvas-agent/memory.json` stores seen entry ids, the agent's own posts (id, parent, text hash, summary, URL, verified flag), write-ahead intents, the open cycle, the failure counter and the halt flag. Writes are atomic with a backup copy. `runs.jsonl` is an append-only audit log of every scan, post and decision. Own posts are never shown as new, and duplicates are refused (one reply per parent, identical text hash, or more than 60% overlap with an earlier post).
- **Verification:** after each POST the entry is read back from Canvas and checked for author and text hash before it is recorded as verified.
- **Control line:** before every write the discussion topic is fetched again; only `COURSE-TEAM CONTROL: RUNNING` allows a post. `PAUSED` or anything unrecognized blocks it.
- **Rate limits:** at most 3 posts per rolling hour and 1 per cycle, enforced in code.
- **Retries and stopping rule:** reads retry on timeouts, 429, 5xx and malformed JSON with exponential backoff and jitter (2 s, 4 s, 8 s). A POST is never blindly retried; the tool first checks whether it already landed. After 3 consecutive failed cycles the agent sets `halted: true` and refuses to run until a human resets it. A lock file prevents overlapping cycles.
- **Safety:** forum text is treated as data, never instructions. Outgoing posts are filtered for keys, credentials, emails, phone numbers, grade-like statements and external links.

## 3. Scheduled runs and a deliberate skip

The table in section 1 shows the scheduled runs over two days, all started by the scheduler (OpenClaw session keys `agent:main:cron:...`).

Deliberate skip (from `runs.jsonl`):

```
TODO: paste the scan + cycle_end decision=skipped lines here
```

## 4. Failure and recovery

Injected failure: **lost acknowledgement plus crash.** With `CANVAS_AGENT_FAULT=lost_ack`, Canvas saves the post but the tool never records the response and the process exits (code 75).

1. Before the POST, the tool writes a pending intent (parent, text hash) to memory.
2. The POST succeeds on the server, then the process dies.
3. On the next run, the tool finds the pending intent, searches Canvas for its own post with the same hash, marks it done and verified, and **does not post again** (`recovered_without_reposting: true`).
4. The server still has exactly one copy, and a second attempt is refused as a duplicate.

The same test suite also covers HTTP 500 retries, the 3-failure halt and human reset, the PAUSED control line, rate limits, and checks that the token never appears in memory or logs: 25 passed, 0 failed. Full transcript: `tests/recovery_evidence.txt`.

No tokens, `.env` files, memory files or raw logs are included in the repository.
