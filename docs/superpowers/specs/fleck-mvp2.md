# Fleck MVP 2: scope parked from MVP 1

Items rupak.k asked for during the MVP 1 build that are deliberately out of MVP 1. Each gets brainstormed and specced properly before MVP 2 planning; this file only records the intent and what is known so far.

## 1. Meeting video recording (asked 2026-10-30)

**Intent:** record the meeting's video as well as its audio, store it with the meeting, and play it back on the after-meeting page.

**What is known:**
- Capture: the helper already runs ScreenCaptureKit for call audio, and Screen Recording permission is already granted, so the same stream can carry video.
- Scope of capture: the meeting app's window (Zoom, Teams, Meet), not the whole screen.
- Encoding: hardware HEVC/H.264 via VideoToolbox. Frame rate 5–10 fps is enough for slides and faces.
- Storage: chunked upload during the meeting to the same MinIO bucket as the audio recording. About 150–300 MB per hour at a low frame rate, 0.5–1 GB per hour at 1080p 30 fps.
- Playback: the after-meeting page plays the video next to the transcript, with the "used it" markers on the timeline.
- Setting: optional, "Record meeting video" or "Record audio only".

**Open questions for the MVP 2 brainstorm:**
- Consent: a visible "recording video" indicator, and how it fits the firm's recording-consent practice.
- Retention period and who can view the video.
- Muxing audio and video after the meeting, versus storing them separately.
- Whether to capture the presenter's shared screen at a higher frame rate.
