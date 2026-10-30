# Manual checks for rupak.k

## Task 2b: helper permissions (system audio, notification action)

1. `make helper-app`
2. `open -W -n "dist/Fleck Helper.app" --args --probe --out /tmp/probe.json`
3. Allow Microphone if asked. Allow Screen Recording for "Fleck Helper" when asked (or System Settings > Privacy & Security > Screen & System Audio Recording > enable Fleck Helper, then re-run). Allow notifications (System Settings > Notifications > Fleck Helper).
4. When the "Fleck probe" notification appears, click **Record** (within 20 s of it posting).
5. `cat /tmp/probe.json`: expect `menubar` true, `mic_rms` > 0, `system_rms` > 0.01 (afplay tone plays for 3 s; keep volume up), `notification_authorized` true, `action_received` true.
6. Optional: rebuild (`make helper-app`) and re-run; permissions should persist without new prompts.
7. Optional: trust the cert so `security find-identity -v -p codesigning` lists it: `security add-trusted-cert -r trustRoot -p codeSign -k ~/Library/Keychains/login.keychain-db` on the cert (needs your login password). Signing works without it.
If a denied entry is stuck: `tccutil reset ScreenCapture ai.fleck.helper`.
