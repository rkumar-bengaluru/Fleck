# Meeting Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start recording also records the Zoom/Meet window as video. The video is saved with the meeting, and the meeting page plays it, or the audio alone, in sync with the transcript.

**Architecture:**
- The helper runs a second, video-only ScreenCaptureKit stream on the locked call window and writes 60 s H.264 MP4 pieces with AVAssetWriter.
- The pieces are uploaded by a separate transport queue to `PUT /api/meetings/{id}/video/{start_ms}`.
- An `assemble_video` job places the pieces on the audio timeline, muxes the meeting audio and stores `recordings/{id}.mp4`.
- The web meeting page shows a `<video>` with a **Video | Audio only** switch driving the existing timeline.

**Tech Stack:** PyObjC (ScreenCaptureKit, AVFoundation, CoreMedia), FastAPI + SQLAlchemy + Alembic, MinIO, ffmpeg, React + Vitest + Playwright.

**Spec:** `docs/superpowers/specs/2026-11-01-meeting-video-design.md` (decisions 165–170 in `docs/superpowers/decisions.md`).

## Global Constraints

- **Branch:** work on `feat/video`. Commit and push at every task close. Run a secret scan before each push (no `.env`, `clients/jf/` or key-like strings).
- **Question detection:** never touch it (`helper/fleck_helper/classify.py`, the gate, the thresholds).
- **System audio:** never share or restart the system-audio `SCStream`. Video is a separate stream on its own threads.
- **A video failure never stops or delays audio.** The meeting goes on as audio only, and the helper logs it once.
- **Window capture only** (`SCContentFilter initWithDesktopIndependentWindow:`). Never fall back to capturing the whole display.
- **Window match:**
  - an on-screen Zoom meeting window (owner `us.zoom.xos`, title containing "Zoom Meeting" or "Meeting"), or an on-screen Chrome window (owner `com.google.Chrome`) whose title starts with "Meet";
  - never a title starting with "Fleck";
  - no other fallback;
  - re-check every 2 s until a match, lock on the first one, never re-choose.
- **Format:** 10 fps, fit within 1280×720 keeping the aspect ratio, H.264. Pieces are 60 s long. The final file is H.264 + AAC with `-movflags +faststart`.
- **Timing:** `start_ms` is measured from meeting time 0 (`_Recording.t0_wall`), so a late lock and a resumed meeting stay in sync. The sync tolerance is 0.5 s.
- **Menu lines while recording:**
  - "Video: waiting for call window"
  - "Video: <window title>"
  - "Video: none (audio only)" (after a failure or the window closing)
- **Video never goes to Claude,** nor into chat or the summary.
- **Never write to the real login keychain in tests.**

## Review Focus

1. **Record is pressed before the call window exists.** Video locks later, and playback is still in sync. Test: `test_late_lock_offsets_start_ms` (Task 3).
2. **The locked window closes mid-call, or Chrome quits.** Video stops and audio carries on with no crash. Test: `test_window_gone_ends_video_keeps_audio` (Task 4).
3. **A slow network with large pieces.** Live segment and answer events are not held behind a video upload. Test: `test_video_upload_does_not_block_events` (Task 4).
4. **Stop while a piece is half written.** The partial piece is finished and uploaded, and a piece arriving after `assemble_video` ran requeues only that job. Tests: `test_stop_finishes_partial_piece` (Task 3) and `test_late_piece_requeues_only_assemble_video` (Task 1).
5. **A meeting with no video.** The meeting page, the list and the API behave exactly as before. Tests: `test_meeting_without_video_unchanged` (Task 5, web) and `test_get_video_404_without_video` (Task 2).

---

### Task 1: API: store video pieces

**Files:**
- Modify: `api/fleck_api/models.py` (add `VideoChunk` next to `AudioChunk` at :140, and `video_key: Mapped[str | None]` on `Meeting` next to `recording_key` at :124)
- Create: `api/alembic/versions/0011_meeting_video.py`
- Modify: `api/fleck_api/meetings.py` (`put_video` next to `put_audio` at :133; `has_video` in `meeting_dict` at :29)
- Test: `api/tests/test_meetings.py`

**Interfaces:**
- Produces:
  - `VideoChunk(id, meeting_id, start_ms: int, blob_key: str)`, unique on `(meeting_id, start_ms)`, table `video_chunks`.
  - `Meeting.video_key`.
  - `PUT /api/meetings/{meeting_id}/video/{start_ms}`: body is `video/mp4` bytes; the response is 204. It stores `meetings/{id}/video/{start_ms:09d}.mp4`. Same device auth and ownership rule as `put_audio`. The body cap is `MAX_VIDEO_CHUNK = 64 * 2**20`. `start_ms` must be `0 <= start_ms <= 4*3600*1000`, otherwise 400.
  - When a new row is inserted and the meeting status is `ready` or `failed`, call `enqueue_once(s, "assemble_video", {"meeting_id": id})`. While `recording` or `processing`, do nothing: `process_meeting` enqueues it, as in Task 2.
  - Video rows are **not** added to `processing._row_count`.
  - `meeting_dict` gains `"has_video": m.video_key is not None`.

- [ ] **Step 1: Write the failing tests** in `api/tests/test_meetings.py`, following the existing `put_audio` tests' fixtures:
  - `test_put_video_stores_piece`: PUT 3 bytes to `/video/61000` gives 204. A `video_chunks` row exists with `start_ms == 61000` and `blob_key == "meetings/{id}/video/000061000.mp4"`.
  - `test_put_video_other_users_meeting_403`.
  - `test_put_video_empty_or_out_of_range_400`: an empty body gives 400; `start_ms = 4*3600*1000 + 1` gives 400.
  - `test_put_video_resend_is_idempotent`: the same `start_ms` twice leaves one row.
  - `test_late_piece_requeues_only_assemble_video`: the meeting is `ready`. A PUT enqueues one `assemble_video` job and no `process_meeting` job.
  - `test_meeting_dict_has_video`: `False`, then `True` after `video_key` is set.
- [ ] **Step 2: Run them and see them fail.** `uv run --all-packages pytest api/tests/test_meetings.py -k video -v`. Expected: FAIL (404 route / no table).
- [ ] **Step 3: Implement** the model, the migration (`down_revision` = the 0010 revision id), `put_video` and `has_video`.
- [ ] **Step 4: Run them and see them pass.** Same command gives PASS; then `uv run --all-packages pytest api -q` stays all green.
- [ ] **Step 5: Commit and push.** `feat(api): store meeting video pieces (spec 2026-11-01 §6)`.

### Task 2: API: assemble and serve the video

**Files:**
- Modify: `api/fleck_api/processing.py`:
  - `assemble_video` and the `@job("assemble_video")` next to `assemble_recording` at :47;
  - enqueue it at the end of `process_meeting` at :222;
  - pull out a shared range-stream helper from `get_recording` at :235;
  - add `get_video`.
- Test: `api/tests/test_processing.py`

**Interfaces:**
- Consumes: `VideoChunk`, `Meeting.video_key`, `Meeting.recording_key` (Task 1, existing).
- Produces:
  - `assemble_video(s: Session, meeting_id: int) -> None`: sets `Meeting.video_key = f"recordings/{meeting_id}.mp4"`.
  - Job kind `"assemble_video"`, payload `{"meeting_id": int}`. It is idempotent and re-runnable. Any exception is logged as a warning and leaves `video_key` unchanged and the meeting status untouched.
  - `GET /api/meetings/{meeting_id}/video`: `video/mp4`, single-range support identical to `/recording`. 404 `"no video"` without `video_key`.
  - `_stream_object(request, key: str, media_type: str) -> StreamingResponse`: the shared helper both routes use.
- **Algorithm (not obvious from the signature):**
  - Download the pieces sorted by `start_ms`.
  - Build an ffmpeg concat list in which each gap before a piece is a black clip of `gap_ms` (generated with `-f lavfi color=black:s=<first piece's WxH>:r=10`). A first piece with `start_ms > 0` gets a leading black clip of `start_ms`.
  - Re-encode to H.264 at 10 fps, scaled and padded to the first piece's size.
  - Mux the meeting's `recording_key` audio (AAC copy) with `-shortest` off, so the file is as long as the longer of the two.
  - Use `-movflags +faststart` and a 30-minute timeout.
  - If there's no `recording_key`, write the video without audio.
  - Overlapping pieces (a resend after a resume) are trimmed so start times never go backwards.

- [ ] **Step 1: Write the failing tests** (make tiny MP4 pieces with ffmpeg `testsrc` at 10 fps, as the existing assembly tests make WAVs):
  - `test_assemble_video_places_pieces_with_gap`: two 2 s pieces at `start_ms` 1000 and 5000, plus an 8 s stereo audio recording. Output duration (ffprobe) is 8.0 ± 0.2 s. The file has one video stream and one audio stream, and `video_key == "recordings/{id}.mp4"`.
  - `test_assemble_video_failure_leaves_meeting_ready`: a corrupt piece leaves the meeting `ready`, `video_key` None, and the job doesn't raise.
  - `test_process_meeting_enqueues_assemble_video`: after `process_meeting` reaches `ready` on a meeting with one video row, one `assemble_video` job is queued. With no video rows, none.
  - `test_get_video_range`: `Range: bytes=0-9` gives 206, 10 bytes, `Content-Range`, `video/mp4`.
  - `test_get_video_404_without_video`.
  - The existing `/recording` range tests still pass, unchanged.
- [ ] **Step 2: Run them and see them fail.** `uv run --all-packages pytest api/tests/test_processing.py -k "video" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified above.
- [ ] **Step 4: Run them and see them pass.** Same command gives PASS; `uv run --all-packages pytest api -q` is all green.
- [ ] **Step 5: Commit and push.** `feat(api): assemble meeting video as its own job and serve it (decision 167)`.

### Task 3: Helper: window match and video capture

**Files:**
- Create: `helper/fleck_helper/video.py`
- Test: `helper/tests/unit/test_video.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Win: window_id: int; owner: str; title: str; on_screen: bool`.
  - `pick_window(wins: list[Win]) -> Win | None`: the Global Constraints match rules. Zoom first, then Meet; the first on-screen match in list order.
  - `class VideoRecorder`, with:
    - `__init__(self, out_dir: Path, t0_wall: Callable[[], float | None], on_piece: Callable[[int, Path], None], on_status: Callable[[str], None], list_windows: Callable[[], list[Win]] = sck_windows, stream_factory=sck_window_stream, poll_s: float = 2.0, piece_s: float = 60.0)`.
    - `start() -> None`: starts a daemon thread that polls `list_windows()` every `poll_s` until `pick_window` matches or `stop()` is called. Calls `on_status("Video: waiting for call window")` once, then `on_status(f"Video: {title}")` on lock.
    - `stop() -> None`: finishes the open piece and hands it to `on_piece`, then stops the stream. It returns within 5 s.
    - `on_piece(start_ms, path)` is called once per finished piece. `start_ms = round((first_frame_wall - t0_wall()) * 1000)`, clamped at 0. If `t0_wall()` is still None at the first frame, wait for it (audio sets it within the first second).
    - Any exception in capture or encoding calls `on_status("Video: none (audio only)")`, logs once and ends video. It never raises out of the thread.
  - `sck_windows() -> list[Win]`: from `SCShareableContent` windows (`windowID`, `owningApplication().bundleIdentifier()`, `title()`, `isOnScreen()`).
  - `sck_window_stream(window_id: int, on_frame: Callable[[object, float], None], on_stopped: Callable[[], None])`: returns an object with `.stop()`. It runs a video-only SCStream, `minimumFrameInterval` 1/10 s, sized to fit 1280×720, and calls `on_frame(sample_buffer, wall_time)`. `wall_time` is converted from the buffer's host-clock PTS.
  - The writer (private): AVAssetWriter with H.264 and `AVVideoAverageBitRateKey` ~1.5 Mbit/s, a new file every `piece_s` of frame time, written to `out_dir/piece-{start_ms:09d}.mp4`.
  - The tests drive `VideoRecorder` with a fake `stream_factory` that calls `on_frame` with fake buffers. Factor the writer so it can be replaced too (constructor arg `writer_factory`); the fake writer records frames and "finishes" files.

- [ ] **Step 1: Write the failing tests:**
  - `test_pick_window_prefers_zoom_then_meet`: from `[Chrome "Inbox", Chrome "Meet - abc-defg-hij", Zoom "Zoom Meeting"]`, it picks Zoom. Without Zoom, it picks Meet.
  - `test_pick_window_never_fleck_or_fallback`: from `[Chrome "Fleck: Meeting", Chrome "Gmail"]`, it returns None. Off-screen Meet is None.
  - `test_late_lock_offsets_start_ms`: with fake `list_windows`, there's no match on the first 10 polls (`poll_s=0.01`), then Meet. `t0_wall` = T, and the first fake frame wall = T + 20.0. `on_piece` gets `start_ms == 20000`. `on_status` calls are exactly `["Video: waiting for call window", "Video: Meet - abc-defg-hij"]`.
  - `test_pieces_roll_every_piece_s`: `piece_s=1.0`; 25 frames at 10 fps give 3 pieces with `start_ms` 0, 1000 and 2000 (relative), in order.
  - `test_stop_finishes_partial_piece`: 5 frames, then `stop()` gives one piece delivered, and the stream's `stop` is called.
  - `test_capture_error_reports_audio_only`: the fake stream raises in `on_frame` gives `on_status("Video: none (audio only)")`; `stop()` still returns.
- [ ] **Step 2: Run them and see them fail.** `uv run --all-packages pytest helper/tests/unit/test_video.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `video.py`. PyObjC imports go inside the `sck_*` functions and the real writer, as `audio.py` does, so the unit tests import without frameworks.
- [ ] **Step 4: Run them and see them pass.** Same command gives PASS.
- [ ] **Step 5: Commit and push.** `feat(helper): record the call window as 60 s video pieces (decisions 165, 170)`.

### Task 4: Helper: upload queue and recorder wiring

**Files:**
- Modify: `helper/fleck_helper/transport.py` (add `upload_video` and a second daemon thread for `.vchunk` items; `_items` keeps only `.evt` and `.chunk`)
- Modify: `helper/fleck_helper/menubar.py` (in `Recorder._record` at :318, start a `VideoRecorder`; stop it in `stop()` before the `after` thread joins; add the menu line at :450)
- Test: `helper/tests/unit/test_transport.py` and `helper/tests/unit/test_menubar.py` (or whichever files hold the existing Transport and Recorder tests; extend those)

**Interfaces:**
- Consumes: `VideoRecorder`, `Win` (Task 3); `PUT /api/meetings/{id}/video/{start_ms}` (Task 1).
- Produces:
  - `Transport.upload_video(meeting_id: str, start_ms: int, path: Path) -> None`: writes `<ns>.vchunk` into the outbox. Its own thread PUTs them oldest-first with `Content-Type: video/mp4` and `timeout=120`. It has the same retry, drop and unlink rules as `_upload` (5xx/401/429 retry with backoff; other 4xx drop with a warning; the file is deleted on success). It never waits on websocket acks, and events never wait on it.
  - `Recorder.video_status: str | None`, shown as a menu line under the title while recording.
  - `Recorder` gains a `video_factory` constructor parameter (default builds `VideoRecorder`), so tests inject a fake.
  - Video pieces live in `self.chunks / mid / "video"`. Because the key is `start_ms`, a resumed meeting never overwrites earlier pieces (no state-file change).

- [ ] **Step 1: Write the failing tests:**
  - `test_video_upload_does_not_block_events` (transport): a fake HTTP PUT that blocks for 2 s on `.vchunk`. An event queued after it is sent on the websocket within 0.5 s.
  - `test_video_upload_retries_then_deletes` (transport): 503, then 204, gives two PUTs to `/api/meetings/7/video/61000`; the file and the item are gone.
  - `test_record_starts_and_stops_video` (recorder, with the existing fakes): Start calls the fake video `start()`; Stop calls `stop()`; pieces reach `transport.upload_video(mid, start_ms, path)`.
  - `test_window_gone_ends_video_keeps_audio`: the fake video reports `"Video: none (audio only)"` mid-meeting. The audio pipeline still yields segments, `video_status` shows that line, and Stop still posts the stop.
  - `test_video_factory_error_does_not_stop_recording`: `video_factory` raises. The recording proceeds, and `video_status == "Video: none (audio only)"`.
- [ ] **Step 2: Run them and see them fail.** `uv run --all-packages pytest helper/tests/unit -k "video" -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Pass `t0_wall=lambda: src.t0_wall` from the `_Recording` in `_record`. `on_piece` calls `transport.upload_video`. `on_status` sets `video_status` and calls `on_change()`.
- [ ] **Step 4: Run them and see them pass.** Same command gives PASS. `make test` (the full unit suite, 208 helper tests plus api/core/gsim) is all green.
- [ ] **Step 5: Commit and push.** `feat(helper): video pieces upload on their own queue; Record starts video too (decision 168)`.

### Task 5: Web: watch video or audio

**Files:**
- Modify: `web/src/components/RecordingTimeline.tsx`:
  - generalise the `audio` ref to `media: RefObject<HTMLMediaElement | null>`;
  - add the prop `kind: "audio" | "video"`. For `video`, render `<video>` above the bar (`playsInline`, no native controls, `className="meeting-video"`).
- Modify: `web/src/routes/MeetingAfter.tsx`:
  - the **Video | Audio only** switch (shown only when `m.has_video`);
  - pass `src` `/api/meetings/${id}/video` or `/recording`;
  - keep the current time when switching (seek the new element to the old one's `currentTime`).
- Modify: `web/src/routes/Meetings.tsx` (a "Video" label on rows whose `has_video` is true; add `has_video: boolean` to `Row`)
- Modify: `web/src/api.ts` or the meeting type (add `has_video: boolean`), and the CSS file the player uses (`.meeting-video { width: 100%; max-height: 60vh; background: #000 }`)
- Test: `web/tests/meeting-after.test.tsx`, `web/e2e/after-meeting.spec.ts`

**Interfaces:**
- Consumes: `has_video` in the meeting dict (Task 1); `GET /api/meetings/{id}/video` (Task 2).
- Produces:
  - The switch is a `role="radiogroup"` with an `aria-label` of "Recording view" and two radios, "Video" and "Audio only".
  - The default is Video. The choice is remembered in `localStorage` key `fleck.recordingView`, with every access wrapped in try/catch.

- [ ] **Step 1: Write the failing tests** (Vitest, in the existing meeting-after test style):
  - `test shows video player and switch when has_video`: a `video` element with `src` ending `/api/meetings/1/video`, and the radios "Video" (checked) and "Audio only".
  - `test switching to Audio only keeps time`: set the video's `currentTime = 42`, click "Audio only". The `audio` element has src `/recording` and `currentTime` 42.
  - `test transcript link seeks the video`: click a transcript time link at 01:05. The video's `currentTime` is 65.
  - `test_meeting_without_video_unchanged`: `has_video` false gives no switch, no `video` element, and the audio element as before.
  - Meetings list: `test video label on rows with video`.
- [ ] **Step 2: Run them and see them fail.** `cd web && npx vitest run tests/meeting-after.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and see them pass.** Vitest is all green (`cd web && npx vitest run`), plus `npx tsc --noEmit`. Add one Playwright check to `after-meeting.spec.ts`: with a meeting given a `video_key` by the test setup (use the seeded demo meeting plus a small generated MP4 via the api's `assemble_video` on two `testsrc` pieces), the switch is visible and the video plays (`currentTime > 0` after 1 s). Run `cd web && npx playwright test after-meeting.spec.ts`.
- [ ] **Step 5: Commit and push.** `feat(web): meeting page plays the video or the audio (spec 2026-11-01 goal 4)`.

### Task 6: On the Mac: real capture, load check, docs

**Files:**
- Modify: `docs/user-guide.md` (step 7: video recording, the menu line, the switch; troubleshooting: "Video: waiting for call window"), `docs/superpowers/handover.md` (what was measured; known limits: Teams/Webex not matched, Meet titles in other languages, Fleck's page may appear, no on/off setting), `docs/superpowers/build-log.md` (a "Meeting video" section with numbers), `docs/superpowers/manual-checks.md` (a real Meet call with video)
- Test: `helper/tests/mac/test_video_capture.py` (marked like the existing mac tests, `FLECK_TARGETS=1`)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the Mac test** `test_records_chrome_meet_window`. It opens a local HTML page titled "Meet - test" in Chrome (`open -a "Google Chrome" file://...` with a page that animates a counter and plays a 1 kHz beep each second). It runs `VideoRecorder` with the real factories for 70 s, then asserts:
  - 2 pieces;
  - each is decodable by ffprobe, 10 ± 1 fps, with its longer side ≤ 1280;
  - the first `start_ms` is within 0.5 s of the expected value.
  Use `t0_wall` = the time the test starts.
- [ ] **Step 2: Run it.** `FLECK_TARGETS=1 uv run --all-packages pytest helper/tests/mac/test_video_capture.py -s`. Expected: PASS; record the MB per minute it prints in the build log.
- [ ] **Step 3: End to end in the signed app.** `make helper-app`, `make up`, open `dist/Fleck Helper.app`, with the Chrome "Meet - test" page in front. Start recording now, wait 3 min, then Stop. Then check:
  - the meeting page shows the switch;
  - the video plays;
  - the beep and the counter agree within 0.5 s at the start and the end;
  - the size per hour is extrapolated and recorded.
  Never write to the real login keychain (use the throwaway file keyring if a key is needed).
- [ ] **Step 4: Load check (anti-goal: video never slows live help).** With the "Meet - test" window open and the real `VideoRecorder` running in a background process for the whole time, run `uv run --all-packages python scripts/measure_e2e.py --runs 3`. Expected: library first words p95 ≤ 3.0 s, full ≤ 6.0 s, Claude first words ≤ 5.0 s (decision 112). Record the numbers next to the Task 22 ones in the build log.
- [ ] **Step 5: Docs.** Update the four docs above. Add a decision row for anything decided during the build, continuing from 171. Run a secret scan.
- [ ] **Step 6: Commit and push.** `docs: meeting video measured on the Mac; guide, hand-over and checks updated`.
