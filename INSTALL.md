# Explicit installation and rollback

This snapshot is prepared for source review. Nothing has been installed or changed in the author's live Claude configuration by portfolio preparation.

1. Keep the checkout at a stable absolute path. Read selfcompact.py and docs/ORIGINAL_DESIGN.md, including the process-tree stop and known race conditions.
2. Copy skills/self-compact into the intended Claude skills directory. Replace `<absolute-path-to-this-repository>` in the skill with the actual checkout path.
3. Review examples/settings.fragment.json. Replace every example script path and merge only the four intended hook entries into existing settings. Preserve unrelated hooks/settings. Save your original settings first.
4. If an auto-continuation relay is wanted, create/choose it yourself and explicitly authorize its job in that chat. Put its real ID in the local selfcompact/config.json under the chosen CLAUDE_CONFIG_DIR. Do not commit that file. The script only prints a relay job; it does not itself authorize or send cross-session messages.
5. Adjust context checkpoints to the model's actual window. The historical default thresholds are not universal model limits.
6. Exercise the workflow in a disposable desktop conversation. Verify backup, handoff, process identity, result reporting and recovery before using it for important tasks. Live compatibility was not established during this packaging task.

To roll back, restore the prior hook settings or remove only the selfcompact hook entries and skill. Preserve local state/backups until you have confirmed recovery. Do not delete a transcript or kill a process as an installation step. `off` is a per-session switch; consult the script's help and original design before executing operational commands.
