#!/usr/bin/env python3
"""selfcompact: lets a Claude Code desktop session compact itself, at a moment it chooses.

A session can't run /compact on itself, and compacting its transcript file does nothing
while the session's process runs, because that process keeps the conversation in memory.
But the desktop app reloads a session from its transcript whenever a message arrives and
the session's process is gone. So:

  1. During its turn, the session runs `request`. That records its session id, its process
     id, optional focus instructions for the summary and, optionally, what to do next.
  2. When the turn ends, the Stop hook (`hook-stop`) sees the request and starts `worker`
     outside the session's process tree, then returns.
  3. The worker waits for the transcript to go quiet, backs it up and runs the bundled
     Claude Code CLI headless on it: `claude -p --resume <id> "/compact <focus>"`. That is
     the real built-in compaction, written into the session's own transcript. Then it stops
     the session's process and everything it started, except background commands the
     session asked to keep (`request --keep`).
  4. The app resumes the session from the transcript (at once if it is on screen, otherwise
     on the next message), and the session continues from the compaction summary. The
     desktop view keeps showing the full history.
  5. Optionally, a relay session sends the session a message so it carries on by itself:
     the session hands the relay its job just before its turn ends, and the relay runs
     `relay`, which waits for the worker and prints the message to send.

Sessions don't watch their own context: `hook-context` (PostToolUse and UserPromptSubmit)
tells a session once when its context passes each checkpoint (250k, 500k, 650k, 800k and
900k tokens by default), and the self-compact skill says what each one means. `off` silences
that for a session whose user wants to compact by hand.

State lives in ~/.claude/selfcompact/: config.json names the relay session (and can
override the checkpoints), off/ holds the switched-off sessions, and sessions/<session id>/
holds each session's request, job, result, checkpoint state, handoff document, transcript
backups (archive/) and hook.log. Nothing is written inside project folders. The hooks are
registered in the user settings, so this works in every project. The earlier route
(summarize yourself, then clear) is in legacy/selfclear.py.

Usage:
  python selfcompact.py handoff-path                           (where to write the handoff)
  python selfcompact.py request [--handoff PATH] [--focus TEXT] [--then TEXT] [--keep TASK_ID ...]
  python selfcompact.py cancel
  python selfcompact.py off | on                 (switch self-compaction off or on for this session)
  python selfcompact.py status [--session ID]
  python selfcompact.py hook-context             (PostToolUse/UserPromptSubmit hook: checkpoints)
  python selfcompact.py hook-stop                (Stop hook; reads hook JSON on stdin)
  python selfcompact.py hook-session-start       (SessionStart hook; reports results)
  python selfcompact.py worker --session ID      (started by hook-stop)
  python selfcompact.py relay --session ID --request STAMP   (run by the relay session)
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
SCRIPT = Path(__file__).resolve()
HOME = CONFIG_DIR / "selfcompact"
REQUEST_MAX_AGE = dt.timedelta(minutes=30)  # an older request is stale; the Stop hook drops it
JOB_MAX_AGE = dt.timedelta(minutes=30)      # a worker running longer than this has died
QUIET_SECONDS = 5           # transcript unchanged this long = the turn has finished writing
QUIET_MAX_WAIT = 120        # seconds; give up if it never goes quiet
COMPACT_TIMEOUT = 1500      # seconds for the headless /compact; a long summary takes minutes
RELAY_WAIT = 100            # seconds one `relay` call waits; fits a default 2-minute Bash timeout
RELAY_GIVE_UP = dt.timedelta(minutes=30)    # the relay stops waiting on a request this old
# The session's own variables would make the CLI behave as the desktop's child process.
STRIP_ENV = ("CLAUDE", "ANTHROPIC")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CHECKPOINTS = [250_000, 500_000, 650_000, 800_000, 900_000]  # tokens; config.json "checkpoints" overrides
CHECKPOINT_ADVICE = {
    250_000: "Compact now only at a hard pivot: the previous task is fully wrapped up and the user has "
             "said you are moving on to a new subject. Otherwise carry on.",
    500_000: "For an ordinary task, look for a good place to wrap up the current task, write the handoff "
             "and compact. For a token-heavy task (the user had you read a large file, or you have read a "
             "lot of code that matters for this task), carry on and wait for the 650k note.",
    650_000: "For a token-heavy task, start looking for a good place to wrap up, write the handoff and "
             "compact. Only if the user explicitly had you read a massive file in full, to reason from it "
             "rather than search it, may you wait for the 800k note.",
    800_000: "Whatever the task, start wrapping up now: reach a clean stopping point, write the handoff "
             "and compact.",
    900_000: "Compact at the very next stopping point. At about 970k tokens Claude Code compacts "
             "automatically, without your handoff.",
}
TAIL_CHUNK = 1 << 20        # bytes read at a time from the end of a transcript
TAIL_LIMIT = 64 << 20       # never read further back than this


def state_dir(session_id):
    if not session_id or not re.fullmatch(r"[\w-]+", session_id):
        sys.exit(f"selfcompact: not a session id: {session_id!r}")
    return HOME / "sessions" / session_id


def session_arg(args):
    """The session a command is about: --session, or the session running the command."""
    return args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(stamp):
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def log(sdir, record):
    if sdir.exists():  # only log for sessions that have used selfcompact
        with open(sdir / "hook.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": utcnow().isoformat(timespec="seconds"), **record}) + "\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    """Write via a temporary file, so the relay never reads a half-written file."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            return os.replace(tmp, path)
        except PermissionError:  # Windows: another process has the file open for a moment
            if attempt == 9:
                raise
            time.sleep(0.1)


def read_hook_event():
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}


def read_entries(path):
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and not entry.get("isSidechain"):
                entries.append(entry)
    return entries


def find_transcript(session_id):
    hits = list((CONFIG_DIR / "projects").glob(f"*/{session_id}.jsonl"))
    if not hits:
        sys.exit(f"selfcompact: no transcript found for session {session_id}")
    return max(hits, key=lambda p: p.stat().st_mtime)


def usage_total(entry):
    """Context size at the API call that produced this entry: everything sent in, cached or not.
    None for anything that isn't a real reply (synthetic entries carry zeroed usage)."""
    message = entry.get("message") or {}
    if entry.get("type") != "assistant" or entry.get("isSidechain") or str(message.get("model", "")).startswith("<"):
        return None
    usage = message.get("usage") or {}
    return sum(usage.get(k) or 0 for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) or None


def context_tokens(entries):
    """Context size at the last API call."""
    for e in reversed(entries):
        total = usage_total(e)
        if total:
            return total
    return None


def last_context_tokens(path):
    """context_tokens, read backwards from the end of the transcript; hooks run on every tool
    call, and a transcript can be many megabytes."""
    def total(line):
        if b'"usage"' not in line or b'"assistant"' not in line:
            return None  # most lines, skipped without parsing
        try:
            entry = json.loads(line)
        except ValueError:
            return None
        return usage_total(entry) if isinstance(entry, dict) else None

    with open(path, "rb") as fh:
        pos = end = fh.seek(0, os.SEEK_END)
        carry = b""
        while pos > 0 and end - pos < TAIL_LIMIT:
            step = min(TAIL_CHUNK, pos)
            pos -= step
            fh.seek(pos)
            lines = (fh.read(step) + carry).split(b"\n")
            carry = lines.pop(0)  # may start mid-line; completed by the next chunk back
            for line in reversed(lines):
                found = total(line)
                if found:
                    return found
        return total(carry) if pos == 0 else None


def session_model(entries):
    for e in reversed(entries):
        model = (e.get("message") or {}).get("model") if e.get("type") == "assistant" else None
        if model and not model.startswith("<"):
            return model
    return None


def project_cwd(entries, transcript):
    """The folder the session belongs to, which `claude --resume` must run in.

    A session can cd into subfolders, so take the cwd whose name matches the folder its
    transcript is filed under (~/.claude/projects/<cwd with non-alphanumerics as dashes>).
    """
    seen = [e["cwd"] for e in entries if e.get("cwd")]
    for cwd in seen:
        if re.sub(r"[^A-Za-z0-9]", "-", cwd) == transcript.parent.name:
            return cwd
    return seen[0] if seen else None


def human_prompt_after(entries, when):
    """True if the user sent a message after `when` (an ISO timestamp)."""
    limit = parse_time(when)
    for e in entries:
        if (e.get("type") == "user" and (e.get("origin") or {}).get("kind") == "human"
                and e.get("timestamp") and parse_time(e["timestamp"]) > limit):
            return True
    return False


def compaction_after(entries, when):
    """(preTokens, summary chars) of the first compaction written after `when`, or None."""
    limit = parse_time(when)
    for i, e in enumerate(entries):
        if (e.get("type") == "system" and e.get("subtype") == "compact_boundary"
                and e.get("timestamp") and parse_time(e["timestamp"]) > limit):
            chars = 0
            for later in entries[i + 1:]:
                if later.get("isCompactSummary"):
                    content = later["message"]["content"]
                    chars = len(content if isinstance(content, str) else
                                "".join(b.get("text", "") for b in content if isinstance(b, dict)))
                    break
            return (e.get("compactMetadata") or {}).get("preTokens"), chars
    return None


def process_info(pid):
    """(name, command line) of a running process, or None if there is none."""
    ps = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
          "if ($p) { $p.Name; $p.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, errors="replace", timeout=60,
                         creationflags=NO_WINDOW)
    lines = out.stdout.splitlines()
    return (lines[0].strip(), "\n".join(lines[1:])) if lines else None


def is_session_process(info):
    """The desktop runs each session as claude.exe speaking stream-json; nothing else matches."""
    return bool(info) and info[0].lower() == "claude.exe" and "stream-json" in info[1]


def process_table():
    """Every running process: {pid: {"ppid", "name", "cmd", "created" (ticks)}}. Empty on failure."""
    ps = ("Get-CimInstance Win32_Process | Select-Object @{n='pid';e={$_.ProcessId}}, "
          "@{n='ppid';e={$_.ParentProcessId}}, @{n='name';e={$_.Name}}, @{n='cmd';e={$_.CommandLine}}, "
          "@{n='created';e={[string]$_.CreationDate.Ticks}} | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, errors="replace", timeout=60,
                             creationflags=NO_WINDOW)
        rows = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return {r["pid"]: {"ppid": r["ppid"], "name": r["name"] or "", "cmd": r["cmd"] or "",
                       "created": int(r["created"] or 0)} for r in rows}


def child_processes(table, pid):
    """The processes pid started itself. A child is never older than its parent; an older
    process that names pid as its parent had a parent that exited and left the pid for reuse."""
    me = table.get(pid)
    return {p: info for p, info in table.items()
            if me and info["ppid"] == pid and p != pid and info["created"] >= me["created"]}


def squash(text):
    return " ".join(re.sub(r"[\"'\\]", "", text).split())


def is_task_process(task_id, command, cmdline):
    """Whether cmdline is the process Claude Code started for this background task. A PowerShell
    task's names its output file; a Bash task's holds the command, quoted, as `eval '<command>' < /dev/null`."""
    return (f"\\tasks\\{task_id}.output" in cmdline
            or bool(command) and f"eval {squash(command)} < /dev/null" in squash(cmdline))


def kept_processes(job, children):
    """The session's child processes that run the background tasks it asked to keep.
    Returns ({task id: [pids]}, [why each other requested task can't be kept])."""
    tasks = {t.get("id"): t for t in (job.get("running_at_stop") or {}).get("background tasks", [])
             if isinstance(t, dict)}
    kept, problems = {}, []
    for task_id in job.get("keep") or []:
        task = tasks.get(task_id)
        if not task:
            problems.append(f"{task_id}: it was not running when your turn ended")
        elif task.get("type") == "subagent" or not task.get("command"):
            problems.append(f"{task_id}: only shell commands can be kept; a {task.get('type') or 'task'} "
                            "runs inside the session's process")
        else:
            pids = [p for p, info in children.items() if is_task_process(task_id, task["command"], info["cmd"])]
            if pids:
                kept[task_id] = pids
            else:
                problems.append(f"{task_id}: its process was not found (it may have just finished)")
    return kept, problems


def stop_session(pid, children, keep):
    """Stop the session's process, then each process it started (children) with everything
    those started, except the pids in keep. The session's process goes first, so it can't
    react to its background tasks ending."""
    kill = lambda *a: subprocess.run(["taskkill", "/PID", *a, "/F"], capture_output=True, creationflags=NO_WINDOW)
    if not children:  # no process list to work from: the whole tree at once
        return kill(str(pid), "/T")
    kill(str(pid))
    now = process_table()
    for child, info in children.items():
        if child not in keep and now.get(child, {}).get("created") == info["created"]:
            kill(str(child), "/T")


def spawn_detached(argv, cwd):
    """Start argv outside this process tree, so it outlives the session's process.

    The Stop hook is a child of the session's process, which the worker is about to stop.
    Win32_Process.Create makes the new process a child of the WMI service instead, outside
    any job object the session's tree belongs to. Returns how it was started.
    """
    quote = lambda s: "'" + s.replace("'", "''") + "'"
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
          f"@{{CommandLine={quote(subprocess.list2cmdline(argv))}; CurrentDirectory={quote(cwd)}}}; "
          '"$($r.ReturnValue) $($r.ProcessId)"')
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, errors="replace", timeout=60)
        code, _, pid = out.stdout.strip().partition(" ")
        if code == "0":
            return f"wmi pid {pid}"
        problem = (out.stdout + out.stderr).strip()[:300]
    except (OSError, subprocess.SubprocessError) as exc:
        problem = repr(exc)
    # Fallback: a detached child. It dies with the session if that runs in a kill-on-close job.
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    kwargs = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    try:
        proc = subprocess.Popen(argv, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        proc = subprocess.Popen(argv, creationflags=flags, **kwargs)
    return f"popen pid {proc.pid} (wmi failed: {problem})"


def config():
    path = HOME / "config.json"
    try:
        return read_json(path) if path.exists() else {}
    except ValueError:
        return {}


def relay_session():
    """The desktop session that sends the continuation message (config.json: relay_session)."""
    return config().get("relay_session")


def off_flags(*ids):
    """The files that switch self-compaction off. The desktop session id comes first: it
    survives a rewind, which forks the conversation under a new CLI session id."""
    return [HOME / "off" / i for i in ids if i and re.fullmatch(r"[\w-]+", i)]


def is_off(*ids):
    return any(flag.exists() for flag in off_flags(*ids))


def describe(items):
    """A short description of the background tasks or crons a Stop hook reported."""
    cut = lambda s: s if len(s) <= 80 else s[:77] + "..."
    out = []
    for item in items:
        if isinstance(item, dict):
            label = next((str(item[k]) for k in ("description", "command", "prompt", "name") if item.get(k)), "")
            ident = str(item.get("id") or "")
            out.append(cut(label) + (f" ({ident})" if ident else "") if label else ident or cut(json.dumps(item)))
        else:
            out.append(cut(str(item)))
    return "; ".join(out)


def checkpoint_note(point, tokens):
    advice = CHECKPOINT_ADVICE.get(point, "Look for a good place to wrap up, write the handoff and compact.")
    return (f"selfcompact checkpoint: your context has passed {point // 1000}k tokens (now about "
            f"{tokens // 1000}k). {advice} Never compact mid-edit, and the user's own instructions come "
            "first. See \"Checkpoints\" in the self-compact skill; this note comes once per checkpoint.")


def relay_job(request):
    """The message that hands the relay session its job."""
    command = (f'python "{SCRIPT.as_posix()}" relay --session {request["session_id"]} '
               f'--request {request["created"]}')
    return ("selfcompact relay job (see \"If you are the relay\" in the self-compact skill).\n"
            f"Run with Bash: {command}\n"
            f"It waits up to {RELAY_WAIT} seconds. If it prints WAIT, run it again. If it prints SEND, "
            "send everything after `MESSAGE: ` to the session after `TO: ` with send_message, exactly "
            "as printed. If it prints NOTHING, send nothing. Then reply in one line.")


def wait_quiet(path):
    """Wait until the file has not changed for QUIET_SECONDS. False if that never happens."""
    start = stable_since = time.time()
    last = path.stat().st_mtime
    while time.time() - start < QUIET_MAX_WAIT:
        time.sleep(1)
        mtime = path.stat().st_mtime
        if mtime != last:
            last, stable_since = mtime, time.time()
        elif time.time() - stable_since >= QUIET_SECONDS:
            return True
    return False


def cmd_request(args):
    session_id = session_arg(args)
    pid = args.pid or os.environ.get("CLAUDE_PID")
    exe = os.environ.get("CLAUDE_CODE_EXECPATH")
    if not (session_id and pid and exe):
        sys.exit("selfcompact: run this inside a Claude Code desktop session (CLAUDE_CODE_SESSION_ID, "
                 "CLAUDE_PID and CLAUDE_CODE_EXECPATH must be set)")
    if not is_session_process(process_info(pid)):
        sys.exit(f"selfcompact: process {pid} is not a running Claude Code session; not requesting")
    transcript = find_transcript(session_id)
    entries = read_entries(transcript)
    cwd = project_cwd(entries, transcript) or os.getcwd()
    sdir = state_dir(session_id)
    host = None if args.session else os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    relay = relay_session()
    if args.then and not (relay and host):
        sys.exit("selfcompact: --then needs a relay session (relay_session in "
                 f"{HOME / 'config.json'}) and a desktop session to message back; not requesting")
    if args.then and host == relay:
        sys.exit("selfcompact: this is the relay session, which can't message itself; request without --then")
    handoff = Path(args.handoff).resolve() if args.handoff else None
    if handoff and not handoff.is_file():
        sys.exit(f"selfcompact: no handoff document at {handoff}; write it first, then request")
    # Background tasks write to <temp>/claude/<project folder>/<session id>/tasks/<task id>.output.
    tasks_dir = Path(tempfile.gettempdir()) / "claude" / transcript.parent.name / session_id / "tasks"
    keep = [k for arg in args.keep or [] for k in arg.replace(",", " ").split()]
    for task_id in keep:
        if not (re.fullmatch(r"[\w-]+", task_id) and (tasks_dir / f"{task_id}.output").exists()):
            sys.exit(f"selfcompact: --keep {task_id}: this session has no background task with that id "
                     "(the id is in the tool result that started it); not requesting")
    sdir.mkdir(parents=True, exist_ok=True)
    request = {
        "created": utcnow().isoformat(),
        "session_id": session_id,
        "pid": int(pid),
        "host_session": host,
        "exe": exe,
        "cwd": cwd,
        "transcript": str(transcript),
        "model": session_model(entries),
        "focus": args.focus or "",
        "then": args.then or "",
        "handoff": str(handoff) if handoff else "",
        "keep": keep,
        "tasks_dir": str(tasks_dir),
        "context_tokens_before": context_tokens(entries),
    }
    write_json(sdir / "request.json", request)
    log(sdir, {"event": "request", "session": session_id, "pid": int(pid),
               "context_tokens": request["context_tokens_before"], "focus": bool(args.focus),
               "then": bool(args.then), "handoff": bool(handoff), "keep": keep})
    print(f"requested: compaction of session {session_id} (process {pid}, "
          f"~{request['context_tokens_before'] or 0:,} tokens) starts when this turn ends.\n"
          "`selfcompact.py cancel` withdraws the request before then.")
    if keep:
        print(f"\nKept running through the compaction: {', '.join(keep)}. After the restart you will NOT be "
              "notified when they finish, as you would be now; the resume note says how to check on them. "
              "Every other background task is stopped.")
    if args.then:
        print(f"\nAs your last action, send this message to the relay session {relay} with "
              "send_message, then end your turn. Tell the user you will continue by yourself "
              "in about a minute and they need not reply.\n\n" + relay_job(request))
    else:
        print("Tell the user to wait about a minute before sending the next message.")


def cmd_cancel(args):
    sdir = state_dir(session_arg(args))
    request = sdir / "request.json"
    if request.exists():
        request.unlink()
        log(sdir, {"event": "cancel"})
        print("request withdrawn")
    else:
        print("no pending request")


def cmd_off(args):
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    flags = off_flags(host or session_arg(args))
    if not flags:
        sys.exit("selfcompact: run this inside a Claude Code session (or pass --session)")
    flags[0].parent.mkdir(parents=True, exist_ok=True)
    write_json(flags[0], {"at": utcnow().isoformat(), "session": session_arg(args), "host": host})
    print("Self-compaction is off for this session, including after compactions and rewinds: no "
          "checkpoint notes. Don't compact yourself unless the user asks. `selfcompact.py on` undoes it.")


def cmd_on(args):
    for flag in off_flags(os.environ.get("CLAUDE_CODE_HOST_SESSION_ID"), session_arg(args)):
        flag.unlink(missing_ok=True)
    print("Self-compaction is on for this session: checkpoint notes resume at the next tool call.")


def cmd_handoff_path(args):
    sdir = state_dir(session_arg(args))
    sdir.mkdir(parents=True, exist_ok=True)
    print(sdir / "handoff.md")


def cmd_hook_context(args):
    """PostToolUse and UserPromptSubmit hook: tell the session, once, when its context passes a
    checkpoint. Runs after every tool call in every session, so the usual path is a quick exit."""
    event = read_hook_event()
    session, transcript = event.get("session_id"), event.get("transcript_path")
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    if event.get("agent_id") or not (session and transcript and host):
        return  # a subagent has its own context; outside the desktop, self-compaction can't run
    tokens = last_context_tokens(Path(transcript))
    if not tokens:
        return
    conf = config()
    reached = max((p for p in conf.get("checkpoints") or CHECKPOINTS if p <= tokens), default=0)
    sdir = state_dir(session)
    state_file = sdir / "checkpoint.json"
    announced = read_json(state_file).get("announced", 0) if state_file.exists() else 0
    if reached == announced:
        return
    if reached < announced:  # compacted since: the checkpoints above can fire again
        if reached:
            write_json(state_file, {"announced": reached, "at": utcnow().isoformat(), "tokens": tokens})
        else:
            state_file.unlink(missing_ok=True)
        return
    if host == conf.get("relay_session") or is_off(host, session):
        return  # the relay is cleared by hand; a switched-off session gets no notes
    sdir.mkdir(parents=True, exist_ok=True)
    write_json(state_file, {"announced": reached, "at": utcnow().isoformat(), "tokens": tokens})
    log(sdir, {"event": "checkpoint", "checkpoint": reached, "tokens": tokens})
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": event.get("hook_event_name") or "PostToolUse",
        "additionalContext": checkpoint_note(reached, tokens)}}))


def cmd_hook_stop(args):
    event = read_hook_event()
    if not event.get("session_id"):
        return
    sdir = state_dir(event["session_id"])
    request_file = sdir / "request.json"
    if not request_file.exists():
        return  # the usual case, at every turn end in every session; not worth a log line
    request = read_json(request_file)
    if utcnow() - dt.datetime.fromisoformat(request["created"]) > REQUEST_MAX_AGE:
        request_file.unlink()
        log(sdir, {"event": "dispatch", "session": request["session_id"], "detail": "request was stale; dropped"})
        return
    job_file = sdir / "job.json"
    if job_file.exists() and utcnow() - dt.datetime.fromisoformat(read_json(job_file)["dispatched"]) < JOB_MAX_AGE:
        log(sdir, {"event": "dispatch", "session": request["session_id"], "detail": "a worker is still running; request kept"})
        return
    try:
        os.replace(request_file, job_file)  # an atomic claim: a second run of this hook finds nothing
    except FileNotFoundError:
        return
    request["dispatched"] = utcnow().isoformat()
    # They live in the session's process, which the worker stops; the resumed session is told.
    request["running_at_stop"] = {"background tasks": event.get("background_tasks") or [],
                                  "session crons": event.get("session_crons") or []}
    write_json(job_file, request)
    pythonw = Path(sys.executable).with_name("pythonw.exe")  # no console window for the worker
    python = str(pythonw if pythonw.exists() else sys.executable)
    how = spawn_detached([python, str(SCRIPT), "worker", "--session", request["session_id"]], request["cwd"])
    log(sdir, {"event": "dispatch", "session": request["session_id"], "detail": how})


def cmd_worker(args):
    sdir = state_dir(args.session)
    job_file = sdir / "job.json"
    job = read_json(job_file)
    started = time.time()
    result_file = sdir / "result.json"
    result = {"session": job["session_id"], "ok": False, "requested": job["created"],
              "host_session": job.get("host_session"), "then": job.get("then") or "",
              "handoff": job.get("handoff") or ""}

    def save(detail, **extra):
        result.update(detail=detail, finished=utcnow().isoformat(),
                      seconds=round(time.time() - started), **extra)
        # A session restarted mid-run may already have reported this result; keep that.
        prior = read_json(result_file) if result_file.exists() else {}
        result["announced"] = prior.get("requested") == result["requested"] and prior.get("announced", False)
        write_json(result_file, result)

    def finish(detail, **extra):
        save(detail, **extra)
        (sdir / "archive").mkdir(exist_ok=True)
        job_file.replace(sdir / "archive" / f"{utcnow():%Y%m%d-%H%M%S}-job.json")
        log(sdir, {"event": "worker", **{k: v for k, v in result.items() if k not in ("announced", "then")}})

    try:
        transcript = Path(job["transcript"])
        if not wait_quiet(transcript):
            return finish("the transcript never went quiet; not compacting")
        if human_prompt_after(read_entries(transcript), job["dispatched"]):
            return finish("the user sent a message first; not compacting")
        info = process_info(job["pid"])
        if info and not is_session_process(info):
            return finish(f"process {job['pid']} is no longer the session's; not compacting")
        backup = sdir / "archive" / f"{utcnow():%Y%m%d-%H%M%S}-{job['session_id'][:8]}-before.jsonl"
        backup.parent.mkdir(exist_ok=True)
        shutil.copy2(transcript, backup)
        result["backup"] = str(backup)
        if result["handoff"] and Path(result["handoff"]).is_file():  # a later handoff may overwrite it
            shutil.copy2(result["handoff"], backup.with_name(f"{backup.name[:15]}-handoff.md"))

        # Compact while the session's process is still running, idle. Stopping it first fails
        # for a session that is on screen: the app restarts it within a second, and the new
        # process loads the transcript before the compaction is in it.
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(STRIP_ENV)}
        command = [job["exe"], "-p", "--resume", job["session_id"], "--output-format", "json"]
        if job.get("model"):
            command += ["--model", job["model"]]
        command.append(f"/compact {job.get('focus') or ''}".strip())
        run = subprocess.run(command, cwd=job["cwd"], env=env, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=COMPACT_TIMEOUT, creationflags=NO_WINDOW)
        try:  # what the compaction itself cost, as the CLI reports it
            out = json.loads(run.stdout)
            result["cli"] = {k: out.get(k) for k in ("total_cost_usd", "duration_ms", "modelUsage")}
        except ValueError:
            pass
        entries = read_entries(transcript)
        compacted = compaction_after(entries, job["dispatched"])
        if not compacted:
            return finish(f"the CLI wrote no compaction (exit {run.returncode}): "
                          f"{(run.stdout[-300:] + run.stderr[-300:]).strip()}")
        sizes = dict(tokens_before=compacted[0], summary_chars=compacted[1])
        if human_prompt_after(entries, job["dispatched"]):
            # The running process answered it and chained it to the old history, so the
            # compaction sits on a dead branch and is never loaded.
            return finish("the user sent a message during the compaction; it was written but not used", **sizes)
        if not info:
            return finish("compacted (the session's process had already exited)", ok=True, **sizes)
        # Background work goes with the session's process, unless the session asked to keep a
        # shell command: that keeps running, but no session hears when it ends.
        children = child_processes(process_table(), job["pid"])
        kept, not_kept = kept_processes(job, children)
        running = {kind: [t for t in items if not (isinstance(t, dict) and t.get("id") in kept)]
                   for kind, items in (job.get("running_at_stop") or {}).items()}
        if any(running.values()):
            sizes["stopped"] = {kind: describe(items) for kind, items in running.items() if items}
            resumable = [t["id"] for t in running.get("background tasks", [])
                         if isinstance(t, dict) and t.get("type") == "subagent" and t.get("id")]
            if resumable:
                sizes["resumable"] = resumable
        if kept:
            tasks = {t.get("id"): t for t in job["running_at_stop"]["background tasks"] if isinstance(t, dict)}
            output = lambda i: Path(job.get("tasks_dir") or ".") / f"{i}.output"
            sizes["kept"] = [{"id": i, "description": describe([{"description": tasks[i].get("description")
                                                                 or tasks[i].get("command")}]),
                              "pids": pids, "output": str(output(i)) if output(i).exists() else ""}
                             for i, pids in kept.items()]
        if not_kept:
            sizes["not_kept"] = not_kept
        save("compacted", ok=True, **sizes)  # before the stop: a restarted session reads it at startup
        stop_session(job["pid"], children, {p for pids in kept.values() for p in pids})
        for _ in range(15):
            time.sleep(1)
            if not process_info(job["pid"]):
                return finish("compacted", ok=True, **sizes)
        finish(f"compacted, but process {job['pid']} could not be stopped; the compaction loads "
               "when the session next restarts, unless a message comes first", **sizes)
    except Exception as exc:  # the worker has no console; the result file is its only report
        finish(f"error: {exc!r}")


def cmd_hook_session_start(args):
    event = read_hook_event()
    source, session = event.get("source"), event.get("session_id")
    if not session:
        return
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")  # unset in the worker's headless CLI run
    off = None
    if host and source in ("compact", "resume") and is_off(host, session):
        # The user compacts this session by hand; a compaction must not lose that.
        off = ("selfcompact: self-compaction is switched off for this session, because the user asked. You get "
               "no checkpoint notes; don't compact yourself unless the user asks. If they want it back on, "
               "run `selfcompact.py on`.")
    sdir = state_dir(session)
    if not sdir.exists():  # the usual case: a session that has never compacted itself
        if off:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": off}}))
        return
    note = None
    result_file = sdir / "result.json"
    if source == "resume" and host and result_file.exists():
        result = read_json(result_file)
        if result.get("session") == session and not result.get("announced"):
            finished = result["finished"][:16].replace("T", " ")
            if result["ok"]:
                note = (f"selfcompact: you compacted yourself at {finished} UTC. Your conversation history "
                        f"is now the built-in compaction summary (context before: ~{result.get('tokens_before') or 0:,} "
                        f"tokens). The transcript from before is backed up at {result['backup']}.")
                if result.get("handoff"):
                    note += (f" Read your handoff document first: {result['handoff']}. Then re-read the files it "
                             "lists as context; don't rely on the summary for them.")
                stopped = result.get("stopped") or {}
                if stopped:
                    note += (" These were still running when your process was stopped, and were stopped with it: "
                             + "; ".join(f"{kind}: {text}" for kind, text in stopped.items())
                             + ". Restart any you still need; your handoff should say how.")
                if result.get("resumable"):
                    note += (" A stopped subagent carries on from where it was if you send it a message "
                             f"(SendMessage to {', '.join(result['resumable'])}).")
                kept = result.get("kept") or []
                if kept:
                    note += (" Left running, as you asked: "
                             + "; ".join(f"{k['description']} (task {k['id']}: process "
                                         + ", ".join(map(str, k["pids"]))
                                         + (f", output in {k['output']}" if k.get("output") else "") + ")"
                                         for k in kept)
                             + ". You will NOT be notified when they finish, as you would have been before the "
                             "compaction, and the app may report them as stopped or unfinished; they are not. Check "
                             "them yourself: a task has finished once its process is gone, and its output is in "
                             "its file. To stop one: `taskkill /PID <process> /T /F`.")
                if result.get("not_kept"):
                    note += (" You asked to keep these running, but they were not kept: "
                             + "; ".join(result["not_kept"]) + ".")
            else:
                note = (f"selfcompact: the compaction you requested did not happen ({result['detail']}). "
                        "Your context is unchanged.")
            if result.get("then"):
                note += (" Before compacting you asked the relay session to send you a continuation. A message "
                         "from it that starts with `selfcompact relay:` is that continuation: the text after "
                         "`Continue:` is what you wrote then. Carry on with it without waiting for the user.")
            result["announced"] = True
            write_json(result_file, result)
    log(sdir, {"event": "SessionStart", "source": source, "session": session, "host": host,
               "reported": bool(note)})
    note = " ".join(n for n in (note, off) if n)
    if note:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": note}}))


def cmd_relay(args):
    """Run by the relay session: wait for the request's worker to finish, then say what to send.

    Prints WAIT (still running; run again), SEND with TO and MESSAGE lines, or NOTHING.
    """
    sdir = state_dir(args.session)
    load = lambda name: read_json(sdir / name) if (sdir / name).exists() else {}
    deadline = time.time() + args.wait
    while True:
        # Read in the order the files change hands (request -> job -> result), so a request
        # that moves on between two reads still shows up in the later one.
        pending, job, result = load("request.json"), load("job.json"), load("result.json")
        if result.get("requested") == args.request and job.get("created") != args.request:
            break  # the worker has finished
        if args.request not in (pending.get("created"), job.get("created")):
            return print("NOTHING: the request was withdrawn or replaced before the compaction ran.")
        if utcnow() - parse_time(args.request) > RELAY_GIVE_UP:
            return print("NOTHING: the compaction never ran; giving up.")
        if time.time() > deadline:
            return print("WAIT: the compaction has not finished yet. Run the same command again.")
        time.sleep(2)
    if load("relayed.json").get("requested") == args.request:
        return print("NOTHING: the message for this compaction was already handed out.")
    detail, to = result.get("detail", ""), result.get("host_session")
    if detail.startswith("the user sent a message"):
        return print(f"NOTHING: {detail}. The user is already talking to that session.")
    if not to:
        return print("NOTHING: the request did not record which desktop session to message.")
    if result.get("ok"):
        message = f"selfcompact relay: your compaction finished (~{result.get('tokens_before') or 0:,} tokens before)."
    else:
        message = f"selfcompact relay: your compaction did not happen ({detail}). Your context is unchanged."
    if result.get("ok") and result.get("handoff"):
        message += f" Handoff: {result['handoff']}."
    if result.get("then"):
        message += f" Continue: {result['then']}"
    write_json(sdir / "relayed.json", {"requested": args.request, "at": utcnow().isoformat()})
    log(sdir, {"event": "relay", "session": result.get("session"), "to": to, "ok": result.get("ok")})
    print(f"SEND\nTO: {to}\nMESSAGE: {message}")


def cmd_status(args):
    session = session_arg(args)
    if not session:  # outside a session: the one that used selfcompact last
        dirs = sorted((HOME / "sessions").glob("*"), key=lambda p: p.stat().st_mtime) if HOME.exists() else []
        if not dirs:
            return print(f"nothing in {HOME / 'sessions'} yet")
        session = dirs[-1].name
    sdir = state_dir(session)
    print(f"session {session} ({sdir})")
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") if not args.session else None
    print("self-compaction: " + ("OFF (selfcompact.py on switches it back on)" if is_off(host, session) else "on"))
    for name in ("checkpoint.json", "request.json", "job.json", "result.json"):
        path = sdir / name
        if path.exists():
            print(f"{name}: {path.read_text(encoding='utf-8').strip()}")
    log_file = sdir / "hook.log"
    if log_file.exists():
        print("recent log:")
        for line in log_file.read_text(encoding="utf-8").splitlines()[-8:]:
            print("  " + line)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request", help="compact this session when the current turn ends")
    request.add_argument("--focus", help="instructions for the summary, passed as /compact <focus>")
    request.add_argument("--pid", help="its process id (default: $CLAUDE_PID)")
    request.add_argument("--then", help="what to do after the compaction; the relay session sends it back")
    request.add_argument("--handoff", help="the handoff document the resumed session reads first")
    request.add_argument("--keep", action="append", metavar="TASK_ID",
                         help="a background shell command to leave running (repeatable); all others are stopped")
    relay = sub.add_parser("relay", help="relay session: wait for the worker, then print the message to send")
    relay.add_argument("--request", required=True, help="the request's `created` timestamp")
    relay.add_argument("--wait", type=int, default=RELAY_WAIT, help="seconds to wait before printing WAIT")
    sub.add_parser("cancel", help="withdraw a pending request")
    sub.add_parser("off", help="switch self-compaction off for this session (no checkpoint notes)")
    sub.add_parser("on", help="switch self-compaction back on for this session")
    sub.add_parser("handoff-path", help="print where to write this session's handoff document")
    sub.add_parser("status", help="show request, job, result and recent log lines")
    sub.add_parser("hook-context", help="PostToolUse/UserPromptSubmit hook: note a passed checkpoint")
    sub.add_parser("hook-stop", help="Stop hook: hand a pending request to the worker")
    sub.add_parser("hook-session-start", help="SessionStart hook: log, and report the last result")
    sub.add_parser("worker", help="stop the session's process and compact its transcript")
    for p in sub.choices.values():
        p.add_argument("--session", help="session id (default: $CLAUDE_CODE_SESSION_ID)")
    args = parser.parse_args()
    if args.command in ("worker", "relay") and not args.session:
        parser.error(f"{args.command} needs --session")
    command = {"request": cmd_request, "cancel": cmd_cancel, "off": cmd_off, "on": cmd_on,
               "handoff-path": cmd_handoff_path, "status": cmd_status, "hook-context": cmd_hook_context,
               "hook-stop": cmd_hook_stop, "hook-session-start": cmd_hook_session_start,
               "worker": cmd_worker, "relay": cmd_relay}[args.command]
    if not args.command.startswith("hook-"):
        return command(args)
    # The hooks run at every tool call, session start and turn end in every project: never fail loudly.
    try:
        command(args)
    except (Exception, SystemExit) as exc:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(HOME / "hook-errors.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": utcnow().isoformat(timespec="seconds"),
                                 "hook": args.command, "error": repr(exc)}) + "\n")


if __name__ == "__main__":
    main()
