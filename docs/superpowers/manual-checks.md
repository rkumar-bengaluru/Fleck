# Manual checks for rupak.k

## Task 2b: helper permissions (system audio, notification action)

1. `make helper-app`
2. `open -W -n "dist/Fleck Helper.app" --args --probe --out /tmp/probe.json`
3. Allow Microphone if asked. Allow Screen Recording for "Fleck Helper" when asked (or System Settings > Privacy & Security > Screen & System Audio Recording > enable Fleck Helper, then re-run). Allow notifications (System Settings > Notifications > Fleck Helper).
4. When the "Fleck probe" notification appears, click **Record** (within 20 s of it posting).
5. `cat /tmp/probe.json`: expect `menubar` true, `mic_rms` > 0, `system_rms` > 0.01 (afplay tone plays for 3 s; keep volume up), `notification_authorized` true, `action_received` true.
6. Optional: rebuild (`make helper-app`) and re-run; permissions should persist without new prompts.
7. Optional: trust the cert so `security find-identity -v -p codesigning` lists it (needs your login password): `security find-certificate -c "Fleck Local Signing" -p > /tmp/fleck.pem && security add-trusted-cert -r trustRoot -p codeSign -k ~/Library/Keychains/login.keychain-db /tmp/fleck.pem`. Signing works without it.
If a denied entry is stuck: `tccutil reset ScreenCapture ai.fleck.helper`.

## Task 11: live capture under the signed bundle

Mic capture was checked from a terminal; system audio needs Screen Recording, which only the signed app has. Once `fleck-helper run` exists (Task 12+), from `dist/Fleck Helper.app`:
1. Record for a minute while a video plays: Client frames carry the video's audio, You frames your voice, and the helper's own sounds are absent.
2. Stop the capture mid-recording (System Settings > Privacy & Security > Screen & System Audio Recording, toggle Fleck Helper off and on, or stop sharing from the menu-bar privacy indicator): the transcript shows one gap marker and Client audio resumes.
3. Connect AirPods (or another headset) mid-recording: expect a short "you" gap marker, then your speech is transcribed again from the new mic.
4. Leave the call silent for 10 s: expect no gap marker (missing buffers are treated as silence; only a stopped stream marks a gap). While recording, log for each ScreenCaptureKit buffer its delivery delay (arrival `now()` minus PTS minus duration) and the PTS offset from the host clock; expect delays well under 1 s and offsets near 0. A log line "client timestamps are … off the host clock" or "late audio dropped" means `BAD_STAMP_S`/`MAX_STALL_S` need a look. Also log, for each client question, the time the Final was emitted minus its `end_ms`; expect under 0.5 s on a quiet call (if SCK sends no buffers in silence, the slot wait adds up to 1.0 s).
5. System notification sounds (Mail, Slack, the Fleck notification) are played by macOS, not the helper, so `excludesCurrentProcessAudio` does not remove them; they reach the Client stream. Expect no transcript line for them (the segmenter discards under 300 ms of speech); note any that get through.
