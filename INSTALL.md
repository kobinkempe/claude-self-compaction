# Install Claude Self-Compaction (Windows)

This is the original custom **Claude Desktop Code on Windows** integration. A skill file alone cannot give a terminal session the Desktop process/restart and cross-chat messaging capabilities this workflow uses.

## 1. Download and open the wizard

[Download the installer ZIP](https://kobin-ai-portfolio.kobinkempe.chatgpt.site/downloads/claude-self-compaction-windows.zip), extract it, then double-click **INSTALL.cmd**. Alternatively run `python install.py` from the extracted folder. Python 3.10+ is required. A download does not run code or install itself.

## 2. Sign in to BOTH Claude surfaces

- Sign in to **Claude Desktop**, and use its Code mode for the working and relay chats.
- Sign in to the **Claude Code CLI** separately: run `claude auth login`, or start `claude` and type `/login`. Run `claude auth status` to check the CLI account. Verify that the bundled CLI used by Desktop can use this local login too. Use the same local user/config profile; a WSL or remote login is not evidence that the Windows bundled CLI is signed in.
- Desktop login alone is insufficient: a detached CLI process performs the actual compaction. The installer never handles login credentials. GitHub CLI login is only useful if you choose to clone the private repository with `gh`; the downloadable package and compaction do not require it.

## 3. Review or change the policy

The wizard explains the historical **1M-token** defaults before applying anything:

| Context | Behavior |
|---|---|
| 250K | Compact only at a hard pivot after a completed task and a user-directed subject change. |
| 500K | Ordinary task: finish a coherent step and compact at a clean stop. |
| 650K | Token-heavy work: begin looking for a clean stop. |
| 800K | Start wrapping up even when reasoning from a massive full-file reading. |
| 900K | Compact at the next clean stop, leaving headroom before the model's actual limit. |

Always save files, finish applicable checks, record background work and write a durable handoff. Never interrupt an edit just because a checkpoint arrives. The user's explicit plan takes precedence while leaving headroom. Automatic compaction thresholds vary by model and version.

Enter your model's real context window. The wizard proposes proportionally lower checkpoints for smaller windows; you can customize each threshold and its decision rule, plus additional handoff preferences. These are configurable project settings, not universal recommended model limits.

Both **config.json** and the actual installed **SKILL.md** are generated from your choices. The hooks read the same thresholds and rules. Run the installed `install.py --configure` wizard to change them later (or ask Claude to help you run `/self-compact configure`). Editing only the skill or only the JSON can make them disagree.

## 4. Set up a relay for automatic continuation

Create a **separate Claude Desktop Code chat**, explicitly authorize it to serve as your compaction relay, and give it the installed skill's **If you are the relay** instructions. The chat needs Bash and the Desktop cross-session `send_message` tool. An ordinary subagent or terminal-only chat is not a substitute; the messaging integration is not bundled here.

Ask that chat for its **desktop host session ID**, then enter that ID in the wizard. A CLI transcript ID is a different identifier. The installer generates `~/.claude/selfcompact/RELAY_SETUP.txt` with an authorization prompt and the exact local script path. If you want that prompt first, complete installation with no relay ID: compaction remains **disabled**. Paste the prompt into your relay chat, then rerun the wizard with `--configure` and enter the ID.

The wizard asks you to confirm both logins and relay authorization/capability before enabling the integration. It does not send messages, launch a chat, grant permissions or verify the relay automatically. Keep the relay available during long-running work; clear it manually as needed.

## 5. Apply and verify

The final prompt lists the policy and describes the file/settings changes. On approval, the wizard:

1. Copies the tool to `~/.claude/selfcompact/tool/` and installs `~/.claude/skills/self-compact/SKILL.md`.
2. Backs up existing settings, local config and the installed skill under `selfcompact/install-backups/`.
3. Merges the four PostToolUse, UserPromptSubmit, Stop and SessionStart hooks into `~/.claude/settings.json`, retaining unrelated settings/hooks. Re-running does not duplicate its hooks.
4. Saves your thresholds, rules, preferences, relay ID and enabled state in `~/.claude/selfcompact/config.json`.

`CLAUDE_CONFIG_DIR` selects another profile; run the wizard for the same profile Desktop uses. Restart your Desktop Code sessions to load the hooks. Test in a **disposable** session: verify backup, process identity, compaction result, handoff re-reading and relay continuation. A successful installer test does not prove live compatibility. The actual worker compacts before stopping the verified session process and its children; read the skill's background-task instructions.

The package never runs compaction or stops a process during installation. It is Windows-only and based on observed Desktop/CLI behavior, rather than an official Claude feature. No macOS/Linux/terminal-only support is claimed.

## Change settings or roll back

Run `python "<your-config-dir>/selfcompact/tool/install.py" --configure` to change the policy or relay setup. The wizard preserves unrelated config keys and backs up files it replaces. Do not publish local config, transcripts, hook logs or relay state.

To uninstall, remove only the four commands referencing this install's `selfcompact.py` from settings and remove its skill folder. Restore a backup only if you have not made unrelated changes since it was saved. Keep transcript archives and session state until recovery is confirmed. `selfcompact.py off` disables an individual session; it does not uninstall the hooks.

## Official references

- [Claude Code skills and personal skill folders](https://code.claude.com/docs/en/skills)
- [Claude Code authentication](https://code.claude.com/docs/en/authentication)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)

These references document the underlying features, not endorsement of this custom Desktop workflow.
