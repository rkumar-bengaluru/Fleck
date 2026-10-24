# Fleck — Full Feature Specification

> Fleck = Bot-free AI notepad for meetings. A privacy-first, local system-audio capturer + hybrid notes (your rough notes + AI transcript) + instant post-meeting polish.

This doc lists everything required to build Fleck as a complete product.

---

## 1. Core Philosophy & Positioning

- **Notepad first, transcriber second**: User's own notes are primary (black text), AI enhancement is secondary (grey text)
- **Bot-free capture**: No bot joins Zoom / Meet / Teams — preserves professionalism, no "who is this bot?" moment
- **Privacy-first**: No audio/video stored long-term, only transcript
- **Stay present**: Minimal UI, fades into background, designed for people in back-to-back meetings

---

## 2. Capture Engine (The Hard Part)

### 2.1 System Audio Capture
- [ ] macOS system audio capture (CoreAudio / ScreenCaptureKit) — requires screen recording permission
- [ ] Windows system audio capture (WASAPI loopback) — Windows 10/11 support
- [ ] Mix both mic input + system output into single stream
- [ ] Local processing flag: transcribe locally first if possible
- [ ] No bot SDK, no conferencing bot APIs
- [ ] Auto-start / auto-stop based on calendar
- [ ] One-click ad-hoc capture for unplanned calls (Slack huddle -> Meet)
- [ ] Background running, low CPU/memory footprint
- [ ] Handle device changes mid-call (headphones, speaker switch)

### 2.2 Calendar Intelligence
- [ ] Google Calendar OAuth + sync
- [ ] Outlook Calendar OAuth + sync
- [ ] Auto-detect meeting start from calendar event
- [ ] Pull attendees, title, description, meeting link
- [ ] Pre-meeting notification / ready state
- [ ] Handle recurring meetings, all-day blocks exclusion

### 2.3 Multi-Context Capture
- [ ] Desktop conferencing: Zoom, Google Meet, Microsoft Teams, Slack Huddles, Webex
- [ ] VoIP / phone calls via system audio
- [ ] In-person meetings: iPhone mic on table mode
- [ ] Mobile outbound phone call capture
- [ ] Walking 1:1s, coffee chats

---

## 3. Transcription & Intelligence

### 3.1 Transcription
- [ ] Real-time streaming transcription (on-device or cloud fallback)
- [ ] Speaker diarization (Speaker A/B/C) with high accuracy in 3+ person calls
- [ ] Speaker identification / naming from calendar attendees
- [ ] Voice print learning for frequent contacts
- [ ] Multi-language detection + transcription on all plans
- [ ] 90-95% accuracy target in clear audio
- [ ] Punctuation, capitalization, number handling (critical for pricing, timelines)
- [ ] Timestamp alignment

### 3.2 Post-Meeting Enhancement (Hybrid Notes)
- [ ] Merge user typed notes during meeting (keywords, bullets) + full transcript
- [ ] Use LLM (GPT-4o, Claude 3.5/4) to turn rough notes into clean structured summary
- [ ] Output sections: Summary, Key Points, Decisions, Action Items, Key Quotes, Open Questions
- [ ] Preserve user voice — don't overwrite user's phrasing
- [ ] Distinguish visual: user notes in black, AI inferred in grey
- [ ] Under 15 seconds generation for 1-hour call
- [ ] Optional encrypted audio retention (7-day auto-delete) for verification — opt-in

### 3.3 Templates / Recipes
- [ ] Pre-built templates: Sales Call, Board Meeting, 1:1, Standup, Customer Interview, Product Review, Investor Update, User Research
- [ ] Custom Recipes: User-defined prompt that formats output (e.g., "Coach Me", "Write a Brief / PRD", "MEDDIC for sales")
- [ ] Recipe marketplace / sharing
- [ ] Apply recipe retroactively after meeting

---

## 4. AI Chat & Memory Layer

- [ ] Ask about single meeting: "What did Sarah say about timeline?"
- [ ] Cross-meeting search: "Find all mentions of Acme pricing"
- [ ] Natural language query over transcript library
- [ ] Citation back to meeting / timestamp
- [ ] Personal memory API — context layer for other AI tools
- [ ] Briefs: Overnight pre-meeting brief for external calls built from past notes + Gmail + web research

---

## 5. Platforms

- [ ] **macOS app** (primary): Native, menu bar presence, auto-launch
- [ ] **Windows app**: Feature parity with Mac, stable on Win 10/11
- [ ] **iOS app**: Meeting notes, phone call transcription, in-person capture
- [ ] **Android app**: Launch on day 1 — critical for global teams
- [ ] **Apple Watch app**: Tap to start note
- [ ] **Web app**: View, search, share, manage library, settings
- [ ] **Vision Pro / AR**: Optional for solo thinking sessions

---

## 6. Collaboration — Spaces

- [ ] Shared folders / Spaces for team (organize by project/client/department)
- [ ] Share note via link (view-only / editable)
- [ ] Attendee auto-share (share notes with meeting attendees)
- [ ] Private vs shared toggle
- [ ] Team activity feed
- [ ] Comments on notes
- [ ] Role-based access (viewer, editor, admin)

---

## 7. Integrations & API

### 7.1 Productivity
- [ ] Notion: Push notes to Notion page/database
- [ ] Slack: Push notes to channel/DM, post-meeting summary
- [ ] Zapier: General automation bridge

### 7.2 CRM
- [ ] HubSpot
- [ ] Attio
- [ ] Affinity
- [ ] Salesforce
- [ ] Auto-log meeting notes to CRM contact/company/deal

### 7.3 Email & Tasks — Key Differentiator
- [ ] Gmail: Read context for Briefs + auto-draft follow-up emails after meeting
- [ ] Gmail / Outlook send follow-up draft
- [ ] Native task integrations (not via Zapier): Asana, Jira, Linear, ClickUp
- [ ] Action Items Inbox: Central place to track tasks across meetings

### 7.4 AI Ecosystem — MCP Server & API
- [ ] MCP server for Claude, ChatGPT, Cursor, Lovable, Replit, Bolt, v0
- [ ] Personal API: fetch your meetings, transcripts, summaries
- [ ] Enterprise API: org-wide access, admin controls over API sharing
- [ ] Webhooks for meeting completed
- [ ] One-click import from other notetakers

---

## 8. Library & Search

- [ ] Unlimited meeting history (free plan = 30-day visibility lock, then hidden not deleted)
- [ ] Full-text search across all notes + transcripts
- [ ] Semantic search (vector embeddings)
- [ ] Filters: by attendee, date, template, company, Space
- [ ] CSV export (even for locked history)
- [ ] Markdown export
- [ ] Global keyboard shortcut to open
- [ ] Morning brief: one digest across meetings, email, calendar

---

## 9. Security, Privacy & Compliance

- [ ] SOC 2 Type II compliance
- [ ] GDPR-compliant processing
- [ ] Local audio processing option
- [ ] No audio/video retention by default (opt-in only)
- [ ] Auto-deletion policy (org-wide setting for Enterprise)
- [ ] Model training opt-out OFF by default (privacy-first)
- [ ] SSO (SAML/OIDC) for Enterprise
- [ ] Admin dashboard: usage analytics, member management, billing
- [ ] Centralized billing
- [ ] Disclosure helper: reminds user to inform participants
- [ ] Contribute option to climate / open-source — brand differentiation

---

## 10. Pricing & Packaging

| Tier | Price | Includes |
|------|-------|----------|
| Basic Free | $0 | Unlimited notes, AI chat, templates, multi-language, 30-day history visibility, shared folders |
| Pro | $14/user/mo | Unlimited history, advanced AI models, CRM integrations, Notion/Slack/Zapier, MCP/API, centralized billing, Briefs, Gmail drafts |
| Enterprise | $35/user/mo | Everything in Pro + SSO, admin controls for sharing/API, org-wide auto-deletion, priority support, usage analytics |

Consider: Launch with transparent limits published on pricing page and homepage.

---

## 11. Fleck Differentiators to Build In Day 1

1.  **Optional audio verification**: Encrypted, 7-day auto-delete playback to verify numbers/dates — solves #1 complaint of bot-free tools.
2.  **Best-in-class speaker ID**: Voice prints + calendar mapping, accurate in 3+ person calls.
3.  **Native task manager**: Direct Asana/Jira/Linear/ClickUp — no Zapier middleman.
4.  **Cross-meeting analytics**: Trends, talk-time, coaching, action item completion rate.
5.  **Android on day 1**: Don't repeat the Apple-only launch mistake.
6.  **Privacy by default**: No training on user data unless explicitly opted in.
7.  **Multi-language done right**: Major European languages + accurate number handling.

---

## 12. Tech Stack Suggestions

- **Desktop**: Tauri (Rust) or Electron + native audio modules for low footprint
- **Transcription**: Deepgram / AssemblyAI / Whisper large-v3 + local whisper.cpp fallback for offline
- **LLM**: GPT-4o + Claude 3.5 Sonnet for enhancement, structured output with function calling
- **Vector DB**: Pinecone / Qdrant for semantic cross-meeting search
- **Mobile**: React Native or native Swift/Kotlin
- **Backend**: Postgres + S3 for transcripts, Auth0 for SSO, Stripe for billing
- **Realtime**: WebSockets for live transcription streaming

---

## 13. MVP Build Order (0-6 months)

**Phase 1 (MVP - Month 0-2):** 
System audio capture Mac + Windows + calendar sync + real-time transcription + hybrid notes (black/grey UI) + summary <15s + pre-built templates

**Phase 2 (Month 2-4):** 
iOS + Android, shared folders / Spaces, Notion/Slack push, AI chat, search & library

**Phase 3 (Month 4-5):** 
Recipes, Briefs, CRM integrations, Gmail drafts, native task integrations

**Phase 4 (Month 5-6):** 
MCP server, Personal + Enterprise API, SSO, admin dashboard, webhooks, analytics

---

## 14. Brand Notes for Fleck

- **Name vibe**: Fleck = small spot, speck of insight — the thing you jot down that becomes the full memory
- **Tagline ideas**: "Notes that remember for you." / "Your memory, enhanced." / "Stay present. Fleck remembers."
- **Design**: Minimal, restrained, Apple Notes-like — no clutter, no aggressive onboarding
- **Tone**: Tool that fades into background, not another dashboard to manage

---

Ready to build.
