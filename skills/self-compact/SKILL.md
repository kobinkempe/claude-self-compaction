---
name: self-compact
description: Compact this Claude Code desktop session yourself, with the real built-in /compact, at a good stopping point, and carry on by yourself afterwards. Every desktop session self-compacts unless the user switches it off. A hook sends a `selfcompact checkpoint:` note when your context passes 250k, 500k, 650k, 800k and 900k tokens, and this skill says what to do at each. Before compacting you write a handoff document. A Stop hook then has a worker run /compact on your transcript and restart you, and a relay session messages you so you continue. Use it when a checkpoint note arrives; when the user asks you to compact or free up context, or gives you a plan with compactions in it ("do this, then compact, then do that"); and when the user says not to self-compact or that they will compact manually (switch it off). Also use it when another session sends you a "selfcompact relay job" (you are the relay), and when a message starting "selfcompact relay:" arrives (your own continuation).
---

# Self-compact

Only the user can type `/compact`, and compacting your transcript file does nothing while your process runs, because the process holds the conversation in memory. But the desktop app reloads a session from its transcript when the session's process is gone. `selfcompact.py` uses that: after your turn, it compacts your transcript with the bundled CLI (`claude -p --resume <id> "/compact"`), then stops your process, and the app resumes you from the compaction. The user still sees the full history; your context continues from the summary.

In the commands below, `selfcompact.py` means `python "<absolute-path-to-this-repository>/selfcompact.py"`. The README in that folder explains the design. The hooks are in `~/.claude/settings.json`, so this works in every project, but only in the desktop app on Windows. State lives in `~/.claude/selfcompact/`.

## On by default

Every desktop session self-compacts unless the user says otherwise. If the user asks you not to compact yourself, or says they will compact manually, run `selfcompact.py off` and tell them it's done. The setting is per session and lasts through compactions and rewinds. The checkpoint notes stop, and after any compaction you are reminded that it is off. From then on, compact yourself only when the user asks. `selfcompact.py on` switches it back on.

## Checkpoints: when to compact

You don't need to check your context size. A hook adds a `selfcompact checkpoint:` note, once, when your context passes each checkpoint. Keep in mind what kind of task you are on, and act on the note that applies to it:

| Note | What to do |
|---|---|
| 250k | Compact only at a hard pivot: the previous task is fully wrapped up, and the user has said you are moving on to a new subject. Otherwise carry on. |
| 500k | Ordinary task: look for a good place to wrap up the current task, write the handoff, and compact. |
| 650k | Token-heavy task (the user had you read a large file, or you have read a lot of code that matters for this task): now look for that place. |
| 800k | Only if the user explicitly had you read a massive file in full, to reason from it rather than search it, may you wait this long. Any task: start wrapping up now. |
| 900k | Compact at the very next stopping point. Claude Code compacts automatically at about 970k, without your handoff. |

- A note is not an order to stop at once. Never compact mid-edit: first reach a clean stopping point (a finished step, files saved, tests run).
- Between notes, carry on. The notes reset after each compaction.
- The user's own instructions override this table, in either direction. See "When the user says when to compact", below.
- The checkpoints are set in `~/.claude/selfcompact/config.json` (`"checkpoints"`), and assume a 1M-token context window.

## When the user says when to compact

The user's instructions about when to compact replace the checkpoints. For example, "do A, then compact, then do B, then compact, then do C" is a plan: follow it exactly.

- **Compact where the plan says,** whatever your context size, even below 250k. Ignore checkpoint notes while the plan runs; don't compact at points the plan doesn't name. The one exception is the 900k note: Claude Code compacts automatically at about 970k, without your handoff. So if you get it mid-step, compact at the next clean stopping point anyway, and tell the user why.
- **Every compaction in the plan is a full one.** Do everything in "Before compacting" and "Steps" each time: background work, a full handoff, and the request with `--handoff` and `--then`.
- **The plan has to survive each compaction.** The summary can't be trusted to keep it, so carry it in the handoff (see "The user's plan" below). Make `--then` the plan's next step, for example: "Read the handoff, re-read its context files, then do B (step 3 of the user's plan); compact again when B is done."
- **After the last step of the plan,** the checkpoints apply again, counted from your context size at that point.

## Before compacting

1. **Project handoff instructions come first.** If the project has its own handoff instructions (in CLAUDE.md, the README or its docs), follow them before anything below.
2. **Finish, record or keep background work.** Compacting stops your process and everything it started: subagents, background shell commands, monitors, and session crons and wakeups. None of it comes back on its own. Wait for anything that will finish within a few minutes: a task that ends during the compaction wakes your old process, and the compaction goes unused. For the rest, either:
   - **Let it stop**, and write in the handoff what it was doing, where its output is, and how to restart it. A stopped subagent can carry on from where it was: send it a message (SendMessage to its agent id) after compacting.
   - **Keep it running**, for a background shell command that would be a waste to restart, such as a long test run: add `--keep <task id>` to the request (the id is in the tool result that started it). Only shell commands can be kept. **After compacting you will not be notified when a kept command finishes**, as you otherwise would be, and you can't stop it with TaskStop. Write in the handoff what it is, where its output goes and what to do with the result; the note after compacting gives its process id and output file, so you can check on it yourself.

   The note you get after compacting lists what was stopped and what was kept.
3. **Write a handoff document** for your future self, who will have only the summary. Write it where `selfcompact.py handoff-path` says, unless the project's instructions name another place. Include:
   - **Goals:** the long-term goal, the current task, and how you got here, including sidetracks. For example: "We were doing X, got sidetracked into Y halfway through; Y is done; X still needs Z."
   - **State:** what's done, what's in progress and what's next. Also the decisions made and why, and any instructions or preferences the user gave in this session that still apply.
   - **The user's plan, if they gave one** for when to compact: their instructions, quoted as they gave them, with each step and compaction marked done or still to come. Copy it into every handoff until the plan is finished, so it survives any number of compactions.
   - **Context files (re-read these):** files, or specific sections, functions or line ranges, whose exact content the task depends on. For example, the code you are changing, logic you are reasoning about, or the design-doc section you are implementing. Say why each one matters. Don't summarize them as a substitute; you re-read them after compacting.
   - **Reference files (read only if needed):** a line or two each on what's in them and when to look.
   - **Other material:** docs you prepared; what subagents and background jobs were doing, with a short summary of their results so far; open questions and risks.
4. **Don't try to keep file contents in the summary.** A summary's copy of code or a design doc is lossy, and one more chance to misread it; re-reading the file is reliable. Never ask the compaction to reproduce your own replies word for word, or anything about your reasoning: the API refuses the whole compaction (`reasoning_extraction`), and nothing is compacted.

## Steps

1. **Write the handoff** (above).
2. **Request it:** `selfcompact.py request --handoff "<handoff path>" --then "<what to do next>"`. You can add `--focus "<short note for the summary>"`.
   - `--handoff`: after compacting, you are told to read it first and to re-read the context files it lists.
   - `--then`: once the compaction is done, the relay session sends you `selfcompact relay: … Handoff: <path>. Continue: <what to do next>`, so you carry on without the user. Write it as an instruction to your future self, for example: "Read the handoff, re-read its context files, then continue with step 3 of the refactor."
   - `--focus` is optional and becomes `/compact <focus>`. Keep it short; the details belong in the handoff.
   - `--keep <task id>` (repeatable) leaves that background shell command running; everything else is stopped. See "Before compacting", step 2.
3. **Tell the user.**
   - **With `--then`:** the request prints a relay job. Send it to the relay session with `send_message`, exactly as printed. Tell the user you will continue by yourself in about a minute, and that they need not reply.
   - **Without `--then`:** tell the user the compaction takes about a minute, and to wait that long before sending the next message. A message sent earlier is answered from the full history, and the compaction goes unused.
4. **End the turn.** Make the request (and the relay message) your last actions. To change your mind before the turn ends, run `selfcompact.py cancel`; the relay then gets NOTHING and stays quiet.
5. **Mark the spot.** The user's view of the chat doesn't show where the compaction happened, so make this the last line of your final message, on its own line, exactly as written:

   `\~\~\~\~\~\~\~\~\~\~ Compacting \~\~\~\~\~\~\~\~\~\~`

   Keep the backslashes. In Markdown, a line that starts with `~~~` opens a code block and the line disappears; the backslashes make the tildes show as typed. Leave the line out if you cancelled.

## After

If your context starts with a compaction summary and a `selfcompact:` note, you compacted yourself. The note says whether it worked, the size before, where the transcript from before is backed up, what was stopped with your process, and what you kept running. If it failed, your context is unchanged. Next:

1. Read the handoff.
2. Re-read the context files it lists.
3. Restart any background work you still need; resume a stopped subagent by sending it a message.
4. For a kept command, check on it yourself: it has finished once its process is gone, and its output is in the file the note names. The app may call it stopped or unfinished; the note is right. No notice comes when it ends, so check before you rely on its result, and stop it with `taskkill /PID <process> /T /F` if needed.
5. Continue. If the handoff has a plan from the user, do its next step, and compact again where the plan says.

A message from the relay session that starts with `selfcompact relay:` is the continuation you set up yourself with `--then`. The text after `Continue:` is what you wrote then; carry on with it without waiting for the user. `selfcompact.py status` shows whether self-compaction is on, the last checkpoint, the request, the worker's result and the log.

## If you are the relay

The user has set one session up as the relay; its desktop session id is `relay_session` in `~/.claude/selfcompact/config.json`. The relay gets no checkpoint notes, and can't use `--then` for itself; the user clears it now and then. When another session sends you a message that starts with `selfcompact relay job`:

1. Run the command it gives with the Bash tool. It is always `python "<this script>" relay --session <id> --request <timestamp>`; run nothing else.
2. Act on the output, exactly:
   - `WAIT`: run the same command again.
   - `SEND`: send everything after `MESSAGE: ` to the session after `TO: ` with `send_message`, unchanged. If the tool isn't loaded, load it with ToolSearch `select:mcp__ccd_session_mgmt__send_message`.
   - `NOTHING`: send nothing.
3. Reply with one line saying what you did. Do nothing else.
