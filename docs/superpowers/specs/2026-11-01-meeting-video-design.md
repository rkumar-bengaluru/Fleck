# Meeting video: design

Status: approved by rupak.k's stand-in (decisions 165–170, marked proxy; rupak.k may overturn). Builds on v0.1.0 (`main`). Replaces item 1 of `fleck-mvp2.md`.

## 1. Intent

After a client call, rupak.k wants to watch themselves back and judge **how convincing they looked** when they answered: his expression and the client's, as the call showed them. The audio and the transcript already say *what* was said; the video shows *how*.

So Fleck records the meeting window as it appears on screen (Google Meet's tiles, a shared screen, whatever is on it) alongside the audio it already records, saves it with the meeting, and the meeting page plays it in sync with the transcript and the "Fleck helped" markers.

## 2. Goals

1. **One button.** The existing **Start recording now** (menu bar, web app Record, the notification's **Record**) starts audio and video together. **Stop** ends both. No new button, no new step. (rupak.k)
2. **Record the call window as it appears.** The Chrome window with the active tab (Meet), or the Zoom meeting window, including anything shown in it: tiles, a screen share, slides. If the user switches tab, the video shows that tab. (rupak.k: "active chrome tab will do, and if that tab is sharing a screen it captures what it shows".)
3. **Saved with the meeting.** The video goes up in pieces during the call, through the same outbox the audio uses, so a crash or a network drop loses at most the piece in progress. After Stop, the api assembles one video file per meeting.
4. **Watched on the meeting page, audio or video.** A meeting with video shows a video player (with the meeting's sound) above the existing recording timeline; the user can switch to **Audio only**. The transcript's time links, the helped markers and the chat's citations seek whichever is shown. A meeting without video looks exactly as today. (rupak.k: "list audio as well as video for viewing".)

## 3. Non-goals (this round)

- A separate camera feed of the user. The call window already shows both faces. (rupak.k chose the window.)
- Any analysis of the video (expressions, eye contact, scoring). Watching is done by a person.
- Hiding Fleck's own live page if the user switches the recorded Chrome window to it: it appears in the video. Accepted: the video is for the team, and clients never see it *(rupak.k proxy, decision 166)*.
- An on/off setting for video. Every recording records video. *(Claude's call; easy to add later.)*
- Consent indicators beyond macOS's own screen-recording indicator, retention periods, per-user viewing rights. These were open questions in `fleck-mvp2.md`; they stay open for a later round. *(Claude's call to defer; flag to rupak.k.)*
- Video in the replay/test path (`fleck-helper replay`), in chat, in the summary or in Claude prompts. Video never goes to Claude.
- Higher frame rate for shared screens.

## 4. Anti-goals (failure even if it works)

- **Video makes live help worse.** The audio pipeline, question detection and the latency gates (decision 112: library first words p95 ≤ 3 s, full ≤ 6 s, Claude ≤ 5 s) must not regress. Video capture runs on its own ScreenCaptureKit stream and its own threads. It must not share or restart the system-audio stream, and must not hold the GIL for long periods (encode in AVFoundation/VideoToolbox, never per-frame Python work beyond handing the buffer on).
- **A video failure stops the meeting.** If no window is found, the window closes, permission is missing, or encoding fails, the meeting records audio only and says so once (helper log + menu line). Audio is never stopped or delayed by video.
- **The Fleck answer panel appears in the video.** Window capture records only the chosen window, so it can't; keep it that way (never fall back to whole-display capture).
- **Video out of sync with the transcript.** A transcript time link must land within 0.5 s of the moment in the video.

## 5. Constraints

- macOS only, same helper app, same Screen Recording permission it already has (no new permission prompt, no Chrome extension). Signed with the existing local certificate.
- Storage in the existing MinIO bucket `fleck-recordings`; served by the api with range requests like the audio (`GET /api/meetings/{id}/recording`).
- Ports, stack and services unchanged. No new dependency unless AVFoundation/ffmpeg (already used) can't do it.
- Never touch question detection (memory rule).
- Commit and push at every task close; work on a new branch from `main` (e.g. `feat/video`).

## 6. Decided how (rupak.k's choices and Claude's calls)

**Capture (helper).** *(Claude's call.)*
- A second `SCStream` with `SCContentFilter(desktopIndependentWindow:)` on the chosen window, video only (`capturesAudio` false). The existing system-audio stream is untouched.
- **Choosing the window** *(rupak.k proxy, decision 165)*: at Start and every ~2 s after, look for
  1. an on-screen Zoom meeting window (owner `us.zoom.xos`, the meeting window, not the Zoom home window), or
  2. an on-screen Chrome window (owner `com.google.Chrome`) whose title starts with "Meet" (Chrome titles a window by its active tab).
  Never a window whose title is Fleck's own, and no "frontmost Chrome" fallback: no video beats a video of the inbox. Lock on the first match and keep it for the whole meeting. While waiting the menu shows "Video: waiting for call window"; if nothing matched by Stop, the meeting is audio only and the log says so once. If the locked window closes, video ends there; audio carries on.
  Video can start late (Record pressed before joining): `start_ms` is always measured from the meeting's audio start.
  Not covered this round: Teams, Webex and other web calls; a Meet tab Chrome titles differently (another language, a renamed tab). The "Video:" menu line is how the user notices.
- **Frame rate and size:** 10 fps, scaled to fit 1280×720 keeping the aspect ratio; hardware H.264 via `AVAssetWriter`. Expected ~150–300 MB/hour; the builder measures and records the real figure.
- **Pieces:** a new MP4 file every 60 s (`AVAssetWriter` finished and restarted), each stored in the outbox folder (same resend rules) but uploaded by its own queue, so live segment and answer events never wait behind a video upload *(Claude's call, decision 168)*; uploaded as `PUT /api/meetings/{id}/video/{seq}`. Each piece records the host-clock time of its first frame relative to the meeting's audio start (the same clock the audio uses: `CMSampleBufferGetPresentationTimeStamp`), sent as a header or query parameter, so the api can place it.
- The menu shows "Video: <window title>" or "Video: none (audio only)" while recording.

**Storage and assembly (api, worker).** *(Claude's call.)*
- New table `video_chunks(meeting_id, seq, blob_key, start_ms)`, mirroring `audio_chunks`; keys `meetings/{id}/video/{seq:06d}.mp4`. Same auth rule as `put_audio` (the recording device's user only), a size cap per piece.
- A separate `assemble_video` job *(Claude's call, decision 167)*, queued after `process_meeting` finishes and again when a late video piece arrives; video rows never requeue `process_meeting`, so the summary is not rebuilt and the meeting goes `ready` without waiting for the video. It places the pieces on the audio timeline by `start_ms` (gaps and a late start become black frames), muxes the meeting's stereo audio in, and writes `recordings/{id}.mp4` (H.264 + AAC) with `+faststart`. `meetings.video_key` holds it. A video assembly failure leaves the meeting `ready` with audio only and logs why; it never fails the meeting.
- `GET /api/meetings/{id}/video` serves it with the same range logic as the audio route (shared helper, not a copy).
- A video piece that arrives after the job ran requeues only `assemble_video` (normal: up to 60 s of video is still uploading at Stop).
- After a helper crash and resume (same meeting, `offset_ms` in `menubar.py`), video pieces continue the numbering and timeline; they never overwrite earlier pieces.

**Meeting page (web).** *(Claude's call.)*
- If the meeting has video: a `<video>` player above the recording timeline, and a two-way switch **Video | Audio only** (remembered per browser). The timeline, markers, transcript links and chat citations drive whichever element is shown (both are `HTMLMediaElement`; `RecordingTimeline` already takes the media element).
- If not: today's page, unchanged.
- The meeting list marks meetings that have video (a small "Video" label), so the user can find them.

## 7. Testing

- Helper unit tests with fakes, as `test_audio.py` does: the window match (Zoom, Meet, never Fleck's own, no fallback), a window that appears at 20 s locking late with the right offset, the 60 s piece rollover, video uploads not blocking events, start offsets, and that a video failure leaves audio running.
- Api tests: `PUT .../video/{seq}` auth and caps; `assemble_video` with two pieces and a gap produces one file of the right length with audio; the range route; a failed assembly leaves the meeting ready.
- Web tests: the switch, a transcript link seeking the video, a meeting without video unchanged.
- On the Mac (signed app): record a real Chrome window playing a video for 3 minutes; check the file plays, faces are readable, sync within 0.5 s at start and end, size per hour. Re-run `scripts/measure_e2e.py --runs 3` with video capture on a Chrome window during the replay and confirm the decision 112 gates still pass.

## 8. Left to the builder

Exact SCK/AVAssetWriter settings (bitrate, keyframe interval), the piece upload format details, the migration, component layout and copy, how the start offset is sent, and how the Mac check drives a window.
