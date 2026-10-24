---
name: self-compact
description: Choose a clean compaction boundary in a Windows Claude Desktop Code session, preserve a durable handoff, compact with the signed-in CLI, and continue through an explicitly authorized relay. Use for selfcompact checkpoint notes, compaction requests, relay jobs, or /self-compact configure. First-time setup must explain and confirm the policy, both Claude logins, and relay setup before enabling compaction.

---

# Self-compact

Only the user can type `/compact`, and compacting your transcript file does nothing while your process runs, because the process holds the conversation in memory. But the desktop app reloads a session from its transcript when the session's process is gone. `selfcompact.py` uses that: after your turn, it compacts your transcript with the bundled CLI (`claude -p --resume <id> "/compact"`), then stops your process, and the app resumes you from the compaction. The user still sees the full history; your context continues from the summary.

In the commands below, `selfcompact.py` means `python "<absolute-path-to-this-repository>/selfcompact.py"`. The README in that folder explains the design. The hooks are in `~/.claude/settings.json`, so this works in every project, but only in the desktop app on Windows. State lives in `~/.claude/selfcompact/`.

## On by default

After the installer has confirmed setup and enabled this integration, desktop sessions self-compact unless the user says otherwise. An installation marked `enabled: false` must finish the setup wizard first. If the user asks you not to compact yourself, or says they will compact manually, run `selfcompact.py off` and tell them it's done. The setting is per session and lasts through compactions and rewinds. The checkpoint notes stop, and after any compaction you are reminded that it is off. From then on, compact yourself only when the user asks. `selfcompact.py on` switches it back on.

## Setup and configuration come first

If the user says `configure`, asks to change thresholds or handoff rules, or no completed setup exists in `~/.claude/selfcompact/config.json`, explain the installed/default policy and help them run:

`python "<absolute-path-to-this-repository>/install.py" --configure`

The guided wizard asks what they want, validates the checkpoints, and writes both `config.json` and the actual installed `SKILL.md`. Do not edit only one of them. Skills contain instructions; persistent settings here are this project's JSON configuration, not built-in Claude skill parameters. Ask the user for their intended model window instead of guessing. Keep the clean-stop and handoff requirements.

Before enabling, explicitly explain: sign in to **Claude Desktop AND the Claude Code CLI**. Desktop login alone does not authenticate the headless compaction worker. Run `claude auth login` (or `claude` then `/login`) and `claude auth status` in the same local profile; verify the bundled CLI used by Desktop can authenticate too. GitHub CLI authentication is not required to compact.

Automatic continuation also requires a **separate user-authorized Desktop relay chat** with Bash and cross-session messaging. A terminal-only session or an ordinary subagent does not provide that relay. The installer writes `~/.claude/selfcompact/RELAY_SETUP.txt` as a prompt the user can paste into their chosen relay. The relay must report its desktop HOST session ID, which the user enters in the wizard. Installation cannot verify messaging support or grant authorization. If setup is deferred, the new installation stays disabled and must not request compaction.

## Checkpoints: when to compact

The hook reads the local JSON policy and announces each reached checkpoint once. A checkpoint is an invitation to finish a coherent step, not an instruction to interrupt an edit. Save files, finish applicable checks, account for background work and write the handoff before every compaction. Checkpoints reset after compaction.

<!-- POLICY:START -->
## Default compaction policy

These are the historical defaults for a **1,000,000-token context**. Choose lower thresholds for smaller models in the installer.

| Note | What to do |
|---|---|
| 250,000 | Only compact at a hard pivot: the previous task is complete and the user has moved to a new subject. Otherwise continue. |
| 500,000 | Ordinary task: finish a coherent step, write the handoff, then compact at a clean stopping point. |
| 650,000 | Token-heavy task: begin looking for that clean stopping point. |
| 800,000 | Even for explicitly requested massive full-file readings, start wrapping up now. |
| 900,000 | Compact at the next clean stop, leaving headroom before the actual model limit. |

Never compact mid-edit. The user's explicit compaction plan takes precedence while leaving headroom for the model's actual limit. Automatic compaction thresholds vary by model and version.
<!-- POLICY:END -->

## When the user says when to compact

The user's instructions about when to compact replace the checkpoints. For example, "do A, then compact, then do B, then compact, then do C" is a plan: follow it exactly.

- **Compact where the plan says,** whatever your context size, even below the first configured checkpoint. Ignore checkpoint notes while the plan runs; don't compact at points the plan doesn't name. The exception is the final configured safety checkpoint: if the model is approaching its actual automatic limit, compact at the next clean stopping point and tell the user why. Never assume an automatic limit of 970k on every model.
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
# Installation and rollback

Use the downloadable Windows package or run `python install.py` from the repository. Read <absolute-path-to-this-repository>/INSTALL.md for the full installation guide. The wizard explains defaults, asks for changes and login/relay confirmations, backs up settings, and merges only this integration's four hooks. The installed copy lives under `~/.claude/selfcompact/tool/`; this skill is in `~/.claude/skills/self-compact/`. `CLAUDE_CONFIG_DIR` selects a different profile.

Re-run the installed wizard with `--configure` to change policy or finish relay setup. It updates the hook's runtime policy and this installed skill together. Existing unrelated settings and configuration keys survive. The wizard does not sign in, compact a live chat, stop processes, create an agent, or send messages.

Test the workflow in a disposable Desktop Code conversation before relying on it. This is a custom Windows integration based on observed Desktop behavior; compatibility with every current version is not established by the offline installer tests. Onboarding enables it based on the user's confirmations, not an automated compatibility probe.

To roll back, remove only the four hook commands referencing this install's `selfcompact.py` and the installed self-compact skill, or restore the matching settings/skill backup if no unrelated changes occurred since. Backups live in `~/.claude/selfcompact/install-backups/`. Preserve session state and transcript archives for recovery. No transcript deletion or process stop is needed to uninstall. `off` is a per-session switch, not an uninstall.
