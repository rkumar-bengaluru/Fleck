# Manual checks for rupak.k

## Task 18: before hand-over (spec §9), first

Claude cannot hold a real call, so these are the checks only a person can do (decision 124). Build first: `make helper-app`, then `fleck-helper set-claude-key --from-env .env` once (or pair again from the repo folder), and start the app with `open "dist/Fleck Helper.app"`. Logs: `~/Library/Logs/Fleck/helper.log`.

1. **Real Google Meet call on speakers (browser).** Join a Meet with a second device or colleague as the client, no headset. Have the client ask two or three questions from `fixtures/conversation/reference.json` (q_rate, q_start, q_general). Expect: each shows **Thinking…** within about a second of the client finishing, then an answer with its label; your own voice never triggers; the web transcript has no You line that repeats the client's words. Then share your **full screen** in Meet and look at the shared view on the other device: the answer panel must not appear in it. Note any echoed You lines and the time to first words.
2. **Real Zoom call on speakers (desktop app).** Same as 1 in the Zoom app, with a full-screen share. Zoom's own echo cancellation differs from the browser's: note any client words that reach the You stream.
3. **Notification Record.** Put an event with a Meet link 6 to 10 minutes ahead in the calendar (gsim or Google), wait for the "Starts in 5 min." notification, press **Record**: recording starts for that event (menu shows **Recording 00:0x** and the event title). Repeat with a tap on the notification's body (not the button): it also starts recording.
4. **⌥⌘F anywhere.** The global shortcut needs Accessibility permission for Fleck Helper (System Settings > Privacy & Security > Accessibility). Without it ⌥⌘F works only while Fleck's menu is open; **Hide answer panel** in the menu always works.
5. **Permissions after a rebuild.** After `make helper-app`, Microphone, Screen & System Audio Recording and Notifications should still be granted (same bundle id and certificate).
6. **Resume.** While recording, force-quit Fleck Helper (Activity Monitor), reopen it and choose **Start recording now**: it asks to resume the meeting; **Resume** keeps one meeting with one transcript, **Start new** sends the old one to processing.
7. **Sleep.** While recording, `pmset -g assertions` lists "Fleck is recording a meeting" (PreventUserIdleSystemSleep); after Stop it is gone.


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
