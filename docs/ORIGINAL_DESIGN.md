# Historical design notes

These are the author's historical observations from the original Windows setup. Timings and desktop behavior have not been revalidated against the current Claude release. Installation paths and real session state are excluded.

# selfcompact

Lets a Claude Code **desktop** session compact itself with the real built-in `/compact`, at a moment the session chooses, without the user typing anything. The user's view keeps the full history; only the model's context is compacted. It's installed for every project: the skill is in `~/.claude/skills/self-compact/`, the hooks are in `~/.claude/settings.json`, and both run `selfcompact.py` from this folder.

Every desktop session self-compacts unless the user switches it off (`selfcompact.py off`). Sessions don't watch their own context. A hook tells each one, once per checkpoint, when its context passes 250k, 500k, 650k, 800k and 900k tokens, and the skill says what each checkpoint means for the kind of task at hand:

- **250k:** only at a hard pivot.
- **500k:** ordinary tasks.
- **650k:** token-heavy tasks.
- **800k:** only when the user had it read a massive file in full.
- **900k:** last call before auto-compaction.

Before compacting, a session writes a handoff document. It covers goals, state, sidetracks and background work, plus which files to **re-read** (context) and which only to **consult if needed** (reference). After compacting, the session re-reads the context files instead of trusting the summary's version of them.

## How it works

Claude can't type `/compact`, and compacting a session's transcript file does nothing while the session's process runs, because the process keeps the conversation in memory. But the desktop app runs each session as a separate `claude.exe` process, and when a message arrives for a session whose process is gone, it resumes the session from its transcript. So:

0. **Checkpoints.** The `hook-context` hook runs after every tool call (PostToolUse) and before every user message is processed (UserPromptSubmit). It reads the context size of the session's last API call from the end of its transcript; hooks aren't told the context size. When that passes a checkpoint it hasn't announced yet, it adds a `selfcompact checkpoint:` note to the session's context.
   - **When it stays quiet:** in subagents (the hook input has `agent_id`), in the relay session, outside the desktop app, and in switched-off sessions.
   - **Resetting:** after a compaction, when the context drops back below a checkpoint, that checkpoint can fire again.
   - **Overriding:** `config.json` can set different `"checkpoints"`.
1. During its turn, Claude writes its handoff document (`selfcompact.py handoff-path` gives the default place) and runs `python selfcompact.py request --handoff <path> [--focus "..."] [--then "..."] [--keep <task id>]`. That writes `~/.claude/selfcompact/sessions/<session id>/request.json`: session ID, process ID (`CLAUDE_PID`), the bundled CLI's path (`CLAUDE_CODE_EXECPATH`), the model, the handoff path and the optional instructions. `--keep` names background shell commands to leave running (see step 3); the request checks that each id has an output file in the session's tasks folder.
2. When the turn ends, the **Stop hook** (`hook-stop`) finds the request and starts `worker` through WMI (`Win32_Process.Create`), so the worker is not part of the session's process tree, then returns. It also records the background tasks and session crons the hook input lists as still running.
3. The **worker** waits until the transcript has been unchanged for 5 seconds. It backs off if the user has sent a message since, and checks that the process is still the session's `claude.exe`. It copies the transcript to `archive/`, then, with the session's process **still running, idle**, runs:
   `claude.exe -p --resume <session> --model <model> --output-format json "/compact <focus>"`
   with the session's `CLAUDE*`/`ANTHROPIC*` variables removed. That appends the standard compaction (marker, summary, re-attached files) to the session's own transcript. Only then does it write `result.json` (including the compaction's own cost, tokens and time as the CLI reports them) and stop the session's process, then each process it started, with everything those started (`taskkill /T`). The session's process goes first, so it can't react to its background tasks ending. That ends everything that ran in the session: subagents, monitors, session crons and background shell commands. MCP servers aren't children of the session's process, so nothing leaks.
   - **Except kept commands:** a shell command named with `request --keep` keeps running. The worker finds its process among the session's child processes: a Bash task's command line carries the command as `eval '<command>'`, and a PowerShell task's names its output file. The command then runs on, but with no parent: nothing tells the resumed session when it ends.
4. The app reloads the session from the transcript, which now ends in the compaction. For a session on screen this sometimes happens within a second; otherwise it happens on the next message, or when the user opens the session. The **SessionStart hook** tells the resumed session:
   - whether its compaction succeeded;
   - to read its handoff first and re-read the context files it lists;
   - what was still running when its process stopped, and that a stopped subagent carries on if it is sent a message;
   - which commands were kept running, with their process ids and output files, and that it won't be notified when they finish;
   - any `--keep` that couldn't be honoured, and why (a subagent, or a task that had already ended).

   In a switched-off session, it says so after every compaction, including the user's own `/compact`.
5. **Optional auto-continue.** With `request --then "<what to do next>"`, the request prints a relay job, and Claude sends it to the **relay session** (`~/.claude/selfcompact/config.json`; currently "Relay agent for self-compaction", on Haiku). One relay serves every project. The relay runs `selfcompact.py relay`, which waits for the worker to finish and prints what to send. The relay then messages the compacted session: `selfcompact relay: your compaction finished (~N tokens before). Continue: <what to do next>`. That message wakes the session if its process is stopped and starts a turn, so Claude carries on from the summary without the user.
   - The relay sends nothing if the user messaged the session during the compaction, or if the request was withdrawn.
   - If the compaction failed, the relay still sends the continuation, saying so.
   - The skill tells both sides what to do, and the SessionStart note reminds the compacted session that the relay's message is its own plan.

**Why compact first, then stop:** the app restarts a session's process as soon as it dies if that session is on screen ("exited while idle… restarting it" in `%LOCALAPPDATA%\Claude\Logs\main.log`), and starts it whenever the user clicks into a session. The first self-test stopped the process first: the app restarted it within a second, loaded the transcript before the compaction was written, and the compaction ended up stranded on a dead branch.

A test session compacted in 18 seconds from request to result. A live self-compaction of a 214k-token session took 43 seconds and left it at 78k (Messages 11k; the rest is fixed overhead). The first auto-continue test compacted a 159k-token session in 42 seconds. Once the relay agreed to run (see Known limits), it took 10 seconds from starting the job to delivering the continuation. The second auto-continue test needed nothing from the user: a 101k-token session compacted in 36 seconds, the continuation arrived 48 seconds after the request, and the session resumed at 81k (Messages 13k). The first test after the global install, with the new relay, also needed nothing from the user: a 254k-token session compacted in 35 seconds, the relay ran its job three seconds after receiving it, the continuation arrived 60 seconds after the request, and the session resumed at 85k (Messages 17k).

## Files

| Path | What it is |
|---|---|
| `selfcompact.py` (this folder) | `handoff-path`, `request`, `cancel`, `off`, `on`, `status`, `relay`, `hook-context`, `hook-stop`, `hook-session-start`, `worker` |
| `~/.claude/settings.json` | Registers the hooks for every session: PostToolUse and UserPromptSubmit (`hook-context`), Stop, SessionStart |
| `~/.claude/skills/self-compact/SKILL.md` | The skill: the off switch, what each checkpoint means, following the user's own plan for when to compact (it replaces the checkpoints and is carried in every handoff), the handoff document, background work (and `--keep`), how to compact yourself, what to do afterwards, and what the relay does |
| `~/.claude/selfcompact/config.json` | Names the relay session; can override `"checkpoints"` |
| `~/.claude/selfcompact/off/<id>` | Switched-off sessions, by desktop session ID, which survives a rewind (a rewind forks the conversation under a new CLI session ID) |
| `~/.claude/selfcompact/sessions/<id>/` | One session's state: `checkpoint.json` (last checkpoint announced), `handoff.md` (the default place for its handoff), `request.json`, `job.json` (worker running), `result.json`, `relayed.json`, `archive/` (transcript backups, handoff copies, old jobs), `hook.log` |
| `~/.claude/selfcompact/hook-errors.log` | Created only if a hook fails; the hooks never show errors in sessions |
| `~/.claude/selfcompact/old-project-state/` | State from before the global install, when it lived in this folder's `.claude/compaction/` |
| `legacy/` | The earlier route: Claude summarizes itself, then clears the session (see below) |

## Safeguards

- Each session has its own state folder, so sessions in the same project can't pick up each other's requests. The Stop hook acts only on a request less than 30 minutes old, and only when no worker is already running for that session.
- Nothing is written inside project folders, so transcript backups can't end up in a git repo.
- In a session that has never compacted itself, the SessionStart and Stop hooks check for its state folder and exit, in about 0.2 seconds. `hook-context` runs after every tool call in every session. It reads only the end of the transcript, and on a 2.4 MB transcript it takes about 0.13 seconds. It writes nothing until a checkpoint is passed.
- The worker stops only a process that is `claude.exe` with `stream-json` on its command line.
- It does nothing if the user sent a message after the hook ran.
- It backs up the transcript before compacting.
- If anything fails, the result says why, and the session's context stays as it was.

## Known limits

- **A message sent during the ~20–60 seconds of compaction** is answered by the still-running session from its full history. The compaction is then left unused on a dead branch, and the worker reports that. The skill tells Claude to ask the user to wait a minute.
- **Untested: a background task that ends during the compaction.** The session's process is still running then, and a task ending normally starts a new turn so the session can react. That turn would answer from the full history, like a user message would, leaving the compaction unused. The worker only checks for messages from the user, so it would still report success. The skill tells Claude to wait for anything about to finish before compacting.
- **The stop can look like a crash.** The worker force-stops the process, so it exits with code 1. The app sometimes restarts it silently; other times it shows a "Claude Code crashed — Try again" card. Over five self-compactions of one session, the app restarted it silently at 20:55, showed the card at 21:03, then restarted it silently at 21:24, 21:39 and 22:15 (the last on a newer app version). That fits a limit of one silent restart every so often (somewhere between 8 and 15 minutes), but it isn't confirmed. Either clicking "Try again" or sending a message resumes the session from the compaction.
- **The compaction shows up late and out of order.** The desktop draws the conversation from its own copy. When it next reloads the session from the transcript, it appends the rows the CLI wrote (a `/compact <focus>` bubble and a "Compacted session · saved N tokens" divider) at the bottom, below messages that came after them. In the first relay test, where the app restarted the process silently, no compaction rows showed up at all. That's why the skill ends the turn with a "Compacting" line, so the user can see where it happened.
- **Messages from other sessions can't run slash commands.** `send_message` wraps the text in a `<cross-session-message>` block marked as a peer message, so `/compact` sent that way is just text to the model. Such a message does wake a stopped session and start a turn.
- Needs the bundled CLI to be logged in on its own: run it once in a terminal with `/login`. In PowerShell: `& "<CLAUDE_CODE_EXECPATH>"`.
- Works only in the desktop app on Windows. In a terminal `claude` session, `request` refuses, because the process isn't the desktop's.
- The skill and the hooks point at `selfcompact.py` in this folder. If the folder moves, update the paths in `~/.claude/settings.json` and the skill.
- **Auto-continue needs the relay session to exist** and to run in a permission mode that lets it run the script and `send_message` without asking (auto mode does). Each relay job adds a few thousand tokens to the relay's context; clear it now and then.
- **The relay must know its job before the first one arrives.** A relay job is a message from another session asking it to run a script and message a third session, and Claude refuses that unless the user has said otherwise. In the first relay test, Test session's process had started before CLAUDE.md had its "If you are the relay" section, so it never saw it. It refused, and ran the job five minutes later when the user told it directly to run relay jobs. The relay's instructions are now in the global skill, so any session can be the relay, but still have the user tell it once, in its own chat, to run relay jobs. After that, the second relay test ran with no input from the user. A late run is fine: the relay still sends for up to 30 minutes, unless the user has messaged the compacted session in the meantime.
- **Why a relay, and not a background task or a scheduled prompt:** both live inside the session's own process, which the worker stops. The process the app restarts knows nothing about them.

## Findings that shaped the design

- **From Git Bash, `"/compact"` gets rewritten** to `C:/Program Files/Git/compact` and sent as an ordinary message. The worker calls the CLI directly, with no shell; to test by hand, set `MSYS_NO_PATHCONV=1` or use PowerShell.
- **Headless `/compact` works:** `-p --resume <id> "/compact"` exits after about 20–30 seconds with `num_turns: 0` and an empty result. Without `--fork-session`, it writes to the same file under the same session ID.
- **When the desktop (re)starts a session's process:**
  - immediately after it dies, if the session is on screen;
  - when the user clicks into the session;
  - when a message arrives.
  A background session's stopped process stays stopped until one of those. The app also has a 30-minute idle timeout for sessions not on screen. Its current log is `%LOCALAPPDATA%\Claude\Logs\main.log`; the one under the Packages folder stopped in August.
- **SessionStart sources** seen: `startup` (new session, or a self-clear), `resume` (desktop reload, and the worker's headless CLI run), `compact` (during the CLI's compaction), `fork` (`--fork-session`).
- **What a compaction keeps**, from the five compactions of one session:
  - **Summary:** written by the session's own model (the worker passes `--model`), not Haiku. A Haiku session gets a Haiku summary.
  - **Verbatim tail:** only the session's last assistant message, kept word for word.
  - **Re-attached files:** up to five recently read files, in full. One of about 28 KB came back only as a pointer saying it was too large to include. Files read through a shell command rather than the Read tool aren't tracked.
  - **Reasoning:** none. The transcript stores almost all reasoning as an encrypted signature, with no readable text (141 of 153 blocks), and the summary is plain text.
  - `compactMetadata` on the boundary entry records `preTokens`, `postTokens` and which messages were kept.
- **What focus instructions can change** (2026-10-14; tests on copies of a 271k-token session made with `--fork-session`; costs are list prices from the CLI's report):

  | Instruction | Result | Summary | Context after | Time | Cost |
  |---|---|---|---|---|---|
  | none | compacted: the user's messages mostly kept word for word (7 of 9), the assistant's replies 0%, file text 0% | 10.9k chars | 12.9k tokens | 41 s | $0.22 |
  | keep the three files in full | letter and resume 100% word for word; all 42 PDF pages kept as text, with repeated panel text merged (about half verbatim by 6-word runs); images lost | 62.5k chars | 25.8k tokens | 174 s | $0.58 |
  | keep the user's messages and the assistant's replies word for word | refused by the API: `reasoning_extraction`, nothing written | | | 5–7 s | $0.06–0.14 |

  Three attempts that asked for the assistant's replies word for word were all refused; one of them also asked for a codeword from the reasoning. Asking for files is fine. `scratchpad/compare_summary.py` in session 216adc83 measured the coverage.
- **What happens to background work** (2026-10-14, one self-compaction with the worker stopping only the session's own process, not its tree):
  - **A background shell command survives** as an orphan: a 5-minute timer kept writing its heartbeats through the kill and finished, and its "finished" line still reached its old output file. But the app had told the resumed session it was stopped, and no notice came when it finished.
  - **A subagent dies with the process**, because it runs inside it; it was killed in the middle of a pause. A message to its id (SendMessage) resumed it from its own transcript: it wrote two more poems and counted all six.
  - Both kept working during the 44 seconds of compaction, while the session sat idle.
  - The app logged "torn down with 3 live task(s) — settling as stopped": the timer, the subagent, and the subagent's own command in progress.

  Hence the default: stop everything (an orphan nobody hears back from is easy to forget, and a server or endless loop would run until killed by hand), with `--keep` for a command worth not restarting, such as a long test run.
- **Compaction only shrinks the Messages category of context.** System tools, MCP tools and skills (about 60k tokens here) are fixed overhead.

## The earlier route (legacy/)

Claude wrote its own summary, `selfclear.py stage` packed it with the user's messages verbatim, Claude cleared its own session (`clear_session("self")`), and a SessionStart hook injected the pack. It worked: context went from 172k to 74k tokens. But the clear starts a new session, so the old messages vanish from the user's view. That's why it was replaced.

## Checkpoint limits

- The checkpoints are absolute token counts that assume a 1M-token window. A session with a smaller window would be compacted automatically before the later checkpoints.
- A checkpoint note can arrive one tool call late, because the hook reads the context size from the last API call, before the latest tool result was added.
- After `~/.claude/settings.json` changes, the running sessions picked up the new hooks from about the second tool call on.

## Not built yet

- A way to stop the process without the app calling it a crash.
