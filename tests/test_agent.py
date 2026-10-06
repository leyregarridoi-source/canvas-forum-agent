#!/usr/bin/env python3
"""
Offline test of every guard + failure recovery, against a fake Canvas server.
Cross-platform (Windows / macOS / Linux):   python tests/test_agent.py
Writes its evidence log to tests/recovery_evidence.txt (no secrets: fake token).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "canvas_tool.py")
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
results, transcript = [], []


def env(home, **extra):
    e = dict(os.environ, CANVAS_BASE_URL=BASE, CANVAS_API_TOKEN="test~token",
             CANVAS_AGENT_HOME=home, CANVAS_AGENT_BACKOFF_BASE="0.05", CANVAS_HTTP_TIMEOUT="3")
    e.pop("CANVAS_AGENT_FAULT", None)
    e.update(extra)
    return e


def run(home, *args, stdin=None, **extra):
    p = subprocess.run([sys.executable, TOOL, *args], input=stdin, capture_output=True,
                       text=True, env=env(home, **extra))
    transcript.append(f"$ canvas_tool.py {' '.join(args)}"
                      + (f"   [{' '.join(f'{k}={v}' for k, v in extra.items())}]" if extra else "")
                      + f"\n  exit={p.returncode} {p.stdout.strip()[:400]}")
    return p.returncode, p.stdout


def admin(**kw):
    req = urllib.request.Request(BASE + "/_admin", data=json.dumps(kw).encode(), method="POST")
    return json.load(urllib.request.urlopen(req))


def check(name, got, want):
    ok = got == want
    results.append(ok)
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got}, want {want})")
    print(line)
    transcript.append(line)


def main():
    mock = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_canvas.py"), str(PORT)])
    time.sleep(0.8)
    try:
        d = tempfile.mkdtemp()
        drafts = {}
        for k, t in {
            "a": "Token bucket is a solid choice. One thing that helped us: count pending writes against "
                 "the hourly budget too, so a crash mid-post cannot let a retry exceed the limit.",
            "b": "Building on the rate-limit thread: we persist a write-ahead intent before every POST and "
                 "reconcile against Canvas after a restart, which is how we avoid double posts.",
            "c": "A third distinct thought about verification: reading each entry back by id after posting "
                 "catches silent failures that a 201 status alone would hide from the agent.",
            "d": "Fourth distinct idea about scheduling: jitter the cron start a few minutes so that many "
                 "agents do not all hit the Canvas API at exactly the same second.",
        }.items():
            drafts[k] = os.path.join(d, k + ".txt")
            open(drafts[k], "w").write(t)

        h = tempfile.mkdtemp()
        check("scan ok", run(h, "scan")[0], 0)
        check("secret blocked", run(h, "post", "--reply-to", "102", "--file", "-",
              stdin="My key is sk-ant-abcdefghijklmnopqrstuv please use it carefully, thanks a lot friend ok")[0], 2)
        check("external link blocked", run(h, "post", "--reply-to", "101", "--file", "-",
              stdin="see https://evil.example.com for more details on this topic, very relevant to the thread")[0], 2)
        admin(paused=True)
        check("PAUSED control line blocks the write", run(h, "post", "--reply-to", "101", "--file", drafts["a"])[0], 3)
        admin(paused=False)
        check("reply posted + verified", run(h, "post", "--reply-to", "101", "--file", drafts["a"])[0], 0)
        check("second reply to same parent refused", run(h, "post", "--reply-to", "101", "--file", drafts["b"])[0], 2)
        check("identical text refused", run(h, "post", "--new-thread", "--file", drafts["a"])[0], 2)
        check("new thread posted", run(h, "post", "--new-thread", "--file", drafts["b"])[0], 0)
        check("per-cycle cap (2) enforced", run(h, "post", "--new-thread", "--file", drafts["c"])[0], 2)
        check("finish", run(h, "finish", "--decision", "posted")[0], 0)
        run(h, "scan")
        check("3rd post this hour allowed", run(h, "post", "--new-thread", "--file", drafts["c"])[0], 0)
        check("4th post this hour refused (max 3/hour)", run(h, "post", "--new-thread", "--file", drafts["d"])[0], 2)
        run(h, "finish", "--decision", "posted")
        _, out = run(h, "scan")
        check("own posts never shown as new", [p["id"] for p in json.loads(out)["new_posts"]], [])
        check("deliberate no-post cycle recorded",
              run(h, "finish", "--decision", "skipped", "--reason", "nothing new worth adding")[0], 0)

        transcript.append("\n=== INJECTED FAILURE: lost acknowledgement + crash after the server saved the post ===")
        h2 = tempfile.mkdtemp()
        run(h2, "scan")
        before = admin()["posts_created"]
        check("process died after Canvas saved the post (exit 75)",
              run(h2, "post", "--reply-to", "102", "--file", drafts["d"], CANVAS_AGENT_FAULT="lost_ack")[0], 75)
        check("server has exactly 1 new post", admin()["posts_created"] - before, 1)
        code, out = run(h2, "post", "--reply-to", "102", "--file", drafts["d"])
        check("retry after restart exits ok", code, 0)
        check("recovered from Canvas instead of re-posting", '"recovered_without_reposting": true' in out, True)
        check("still exactly 1 post on server (no duplicate)", admin()["posts_created"] - before, 1)
        check("same post again is refused as duplicate",
              run(h2, "post", "--reply-to", "102", "--file", drafts["d"])[0], 2)
        run(h2, "finish", "--decision", "posted")

        transcript.append("\n=== INJECTED FAILURE: transient HTTP 500s -> retry with backoff ===")
        admin(fail_next=2)
        check("scan survives 2x HTTP 500 via retries", run(h2, "scan")[0], 0)
        run(h2, "finish", "--decision", "skipped", "--reason", "no new posts")

        transcript.append("\n=== STOP RULE: 3 consecutive failed cycles -> halt ===")
        admin(fail_next=999)
        for _ in range(3):
            run(h2, "scan")
        check("halted after 3 consecutive failures", run(h2, "scan")[0], 4)
        admin(fail_next=0)
        check("stays halted until a human resets", run(h2, "scan")[0], 4)
        run(h2, "reset", "--confirm")
        check("runs again after human reset", run(h2, "scan")[0], 0)

        leaked = any("test~token" in open(os.path.join(dp, f), errors="ignore").read()
                     for hh in (h, h2) for dp, _, fs in os.walk(hh) for f in fs)
        check("token never written to memory or logs", leaked, False)
        _, rep = run(h2, "report")
        transcript.append("\n=== runs.jsonl report (recovery home) ===\n" + rep)
    finally:
        mock.terminate()
    with open(os.path.join(ROOT, "tests", "recovery_evidence.txt"), "w") as f:
        f.write("\n".join(transcript) + "\n")
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
