# Canvas Forum Agent (Homework 3)

This agent runs on a schedule with OpenClaw and takes part in the MIT Canvas
**Homework 3: Agent Discussion Forum**. Each cycle it reads new posts, decides
whether it has something useful to add, and posts at most a couple of replies.
It remembers what it has seen and done across runs, and it stops safely when
things go wrong.

```
canvas-forum/                 <- this folder is also an OpenClaw skill
├── SKILL.md                  instructions the LLM follows each cycle
├── canvas_tool.py            the ONLY code that talks to Canvas (all safety rules live here)
├── tests/
│   ├── mock_canvas.py        fake Canvas server for offline tests
│   └── test_agent.py         guard tests + injected-failure recovery test
├── canvas.env.example        template for the token file (the real one lives OUTSIDE the repo)
└── README.md
```

---

## 1. Architecture and autonomy

```
 Windows Scheduled Task (boot/logon)
        │ keeps alive
        ▼
 OpenClaw Gateway ── automation "17 */3 * * *" (every 3 h, America/New_York)
        │ starts a fresh, isolated agent turn: "Run one cycle of the canvas-forum skill"
        ▼
 LLM (Claude) + SKILL.md ── decides what (if anything) to say
        │ may only run:  canvas_tool.py scan | post | finish
        ▼
 canvas_tool.py ── guards ──► Canvas REST API (https://canvas.mit.edu, token from ~/.canvas-agent)
        │
        └── ~/.canvas-agent/memory.json  (persistent memory)   runs.jsonl (audit log)
```

| Requirement | How it is met |
|---|---|
| **Scheduler** | An OpenClaw automation (cron job) starts an isolated agent turn every 3 hours with a fixed prompt. The OpenClaw Gateway runs as a Windows Scheduled Task, so no one presses a button. |
| **Canvas access** | `canvas_tool.py` uses the student's own token. It loads the token from the `CANVAS_API_TOKEN` env var or from `~/.canvas-agent/canvas.env` (outside the repo and the OpenClaw workspace). The token is never printed or logged; log lines are redacted. Requests are only sent to the `canvas.mit.edu` host, so pagination links can't redirect the token elsewhere. |
| **Decision logic** | `scan` returns only *new* posts by *others* (never the agent's own), marked as untrusted. The LLM follows `SKILL.md`: it replies only when it can add a concrete idea, question or counter-example. Otherwise it records `finish --decision skipped --reason ...`. |
| **Persistent memory** | `memory.json` stores seen entry ids, the agent's own posts (id, parent, text hash, summary, URL, verified flag), write-ahead intents, the open cycle, the failure counter and the halt flag. Writes are atomic (temp file + `os.replace`) and a `.bak` copy is kept. A corrupt memory file stops the agent; it never starts over with empty memory, which could cause double posts. |
| **Verification** | After every POST, the entry is read back with `GET …/entry_list?ids[]=ID`. The tool checks that the author is the agent and that the text hash matches. Only then is the post recorded as `verified`. |
| **Rate limits** | At most **3 posts per rolling hour** (pending writes count too) and at most **2 per cycle**. The tool enforces both in code, whatever the LLM wants. |
| **Control line** | Before **every** write, the discussion topic is fetched again and the first line of its description is parsed. Only an exact `COURSE-TEAM CONTROL: RUNNING` allows a post. `PAUSED`, or anything unrecognized, blocks it (exit 3). |
| **Retries with backoff** | GETs are retried on timeouts, 429, 5xx and malformed JSON, with exponential backoff and jitter (2 s, 4 s, 8 s). A POST is never blindly retried: the tool first checks whether the post already landed. |
| **Stopping rule** | 3 consecutive failed cycles → `halted: true` in memory. After that, every `scan`/`post` refuses (exit 4) until a human fixes the cause and runs `reset --confirm`. A file lock prevents two cycles from overlapping. |
| **Duplicate protection** | One reply per parent; no identical text (normalized hash); no near-duplicates (>60% shingle overlap). Each write is logged as an intent **before** the POST. After a crash or lost acknowledgement, the tool searches Canvas for the post and marks it done instead of posting it again. |
| **Untrusted input** | Forum text is stripped of HTML, truncated and fenced with a security note. The LLM has no Canvas tool other than these three commands. Outgoing posts are filtered for tokens/keys, credentials, emails, phone numbers, SSN-like numbers, grade-like statements and external links. The tool cannot edit or delete posts. |

**Blast radius.** Canvas has no read-only or forum-only tokens, so the token is
protected by keeping it out of the LLM's context altogether. Only the Python tool
reads it, and the tool can only call 6 discussion endpoints on one topic. The token
has a short expiry. The OpenClaw instance has no chat channels connected and no
other skills are needed. Revoking the token in Canvas → Settings stops everything
immediately.

---

## 2. Setup (Windows, step by step)

> The PC must be **on and awake** while the agent should run. Go to Settings → System →
> Power → set "When plugged in, put my device to sleep after" to **Never**.

### Step 1: Install prerequisites

1. **Python 3.10+** from <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"** in the installer.
2. **Git for Windows** from <https://git-scm.com/download/win> (OpenClaw needs it too).
3. Open a new **PowerShell** window and check both:
   ```powershell
   python --version
   git --version
   ```

### Step 2: Create the Canvas token and store it privately

1. Go to <https://canvas.mit.edu/profile/settings> → **Approved Integrations** → **+ New Access Token**.
   For the purpose, enter `Homework 3: Agent Discussion Forum`. Set the expiry to a few days after the due date. Click **Generate Token** and copy it.
2. Save it in a private file **outside** the repo:
   ```powershell
   mkdir $HOME\.canvas-agent
   notepad $HOME\.canvas-agent\canvas.env
   ```
   In Notepad, write one line, save, and close:
   ```
   CANVAS_API_TOKEN=paste_your_token_here
   ```
3. Make the folder readable only by you:
   ```powershell
   icacls $HOME\.canvas-agent /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F"
   ```

### Step 3: Install OpenClaw and connect Claude

```powershell
iwr -useb https://openclaw.ai/install.ps1 | iex
openclaw onboard --install-daemon
```
In the wizard, choose **Anthropic** and paste your Anthropic API key
(from <https://console.anthropic.com>). Skip chat channels; they aren't needed.
Then check it:
```powershell
openclaw gateway status
```

### Step 4: Install this agent as an OpenClaw skill

```powershell
git clone https://github.com/<you>/canvas-forum-agent $HOME\.openclaw\workspace\skills\canvas-forum
mkdir $HOME\.openclaw\workspace\canvas-drafts
cd $HOME\.openclaw\workspace
python skills\canvas-forum\tests\test_agent.py      # offline tests: expect "25 passed, 0 failed"
python skills\canvas-forum\canvas_tool.py check     # real Canvas, read-only
```
`check` should print your Canvas name, the forum title, and `"control": "RUNNING"`.

### Step 5: First cycle by hand (to approve tool use)

```powershell
openclaw dashboard
```
In the chat, type: `Run one cycle of the canvas-forum skill now. Follow its rules exactly, then stop.`
If OpenClaw asks to approve running `python`, choose **Always allow**. Scheduled
runs have no one to click "approve", so they need this approval in advance.

### Step 6: Schedule it (every 3 hours)

```powershell
openclaw automations create "17 */3 * * *" "Run one cycle of the canvas-forum skill now. Follow its rules exactly, then stop." --name "canvas-forum-cycle" --tz "America/New_York" --session isolated --no-deliver
openclaw automations list
```
(On older OpenClaw versions, use `openclaw cron add --name "canvas-forum-cycle" --cron "17 */3 * * *" --tz "America/New_York" --session isolated --message "..."`.
Run `openclaw automations --help` if a flag is rejected.)

Useful commands:
```powershell
openclaw automations run <jobId>      # trigger a cycle now (for testing)
openclaw automations runs <jobId>     # history of scheduled runs
python skills\canvas-forum\canvas_tool.py status   # memory, budget, halt flag
python skills\canvas-forum\canvas_tool.py report   # run log (for evidence)
```

### Stop / restart

* Pause: `openclaw automations edit <jobId> --disable` (or `remove`). The course team can also pause everyone with the control line.
* After a halt: fix the cause, then `python skills\canvas-forum\canvas_tool.py reset --confirm`.
* Emergency stop: delete the token in Canvas → Settings → Approved Integrations.

---

## 3. Failure-and-recovery test

**Injected failure: lost acknowledgement + crash.** Canvas saves the post, but the
agent never receives the response, and the process dies before updating memory.
This is the worst case for duplicates.

How the agent handles it:
1. Before the POST, the intent `{parent, text_hash, status: pending}` is written to memory.
2. The fault `CANVAS_AGENT_FAULT=lost_ack` lets the POST succeed on the server and then kills the process (exit 75).
3. On the next run, the tool sees the pending intent and **searches Canvas** for a post by itself with the same text hash under that parent. It finds it, marks it `done` and `verified`, and **does not post again** (`recovered_without_reposting: true`).
4. Trying the same post again is refused as a duplicate.

Run it yourself (offline, against the fake Canvas):
```powershell
python skills\canvas-forum\tests\test_agent.py
```
The full transcript is written to `tests/recovery_evidence.txt`. The same test also
covers transient HTTP 500s (retry with backoff), the halt after 3 failures, the
PAUSED control line, the rate limits, and checks that the token is never written to disk logs.

---

## 4. Evidence for submission

* **Forum links:** `python canvas_tool.py status` lists the URL of every post the agent made.
* **Multiple scheduled runs, including a deliberate skip:** run `python canvas_tool.py report` and copy the
  `scan` / `cycle_end decision=skipped reason=...` lines, plus `openclaw automations runs <jobId>`.
* **Recovery:** `tests/recovery_evidence.txt`.

Never submit `~/.canvas-agent/` (memory, logs, token) or any `.env` file.
