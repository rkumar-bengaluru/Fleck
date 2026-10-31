# Fleck user guide

This guide takes you from a clean Mac to a first real meeting with Fleck. Follow the steps in order. Every command runs from the Fleck folder (the repository root) unless a step says otherwise.

What you get:
- **The web app** at http://localhost:5173: your calendar, briefs, meetings, notes, chat and the library.
- **Fleck Helper**, a menu-bar app. It listens to the call, finds the client's questions, and shows answers in a floating panel.

## 1. Install the prerequisites and fill in `.env`

You need an Apple Silicon Mac with macOS 26.

1. Install these:
   - **Docker Desktop**, from docker.com. Start it once so the `docker` command works.
   - **uv**: `brew install uv`.
   - **Ollama**, from ollama.com or `brew install ollama`.
2. Pull the pinned models:
   ```
   make ensure-ollama
   ```
   It starts Ollama if needed, pulls `qwen3:4b-q4_K_M`, `qwen3-embedding:0.6b` and `qwen3-embedding:4b`, and checks each digest. Each line must start with `ok`. A `MISMATCH` line means the model on the registry changed; stop and tell whoever maintains Fleck.
3. Make your `.env`:
   ```
   cp .env.example .env
   ```
   Then edit `.env`:
   - `FLECK_SECRET_KEY`: required. Make one with `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` and paste it in. The api refuses to start without it.
   - `ANTHROPIC_API_KEY`: your Claude API key. Fleck uses Claude for the summary, the brief's questions, chat, and the live "General knowledge" answers.
   - `FLECK_ALLOWED_DOMAINS`: who may sign in. For the simulated Google in step 4, set `FLECK_ALLOWED_DOMAINS=northbeam.test`.
   - `TZ`: your time zone, for example `TZ=Asia/Kolkata`.
   - Leave `FLECK_GOOGLE_MODE=gsim` for now. Step 4b switches to real Google.
   - `FLECK_LIVE_FALLBACK=claude` sends only the client's question text to Claude when the library has no answer. Set it to `off` to keep the live path fully on your Mac.

`.env` holds secrets. Never commit it.

## 2. Start the stack and check it's healthy

```
make up
```

The first run builds the images and takes a few minutes. It returns when every service is up.

Check it:
```
curl -s localhost:8000/api/health
```
You should see `"status":"ok"` and `"ok"` for postgres, minio, weaviate and ollama. If ollama says otherwise, run `make ensure-ollama` again.

Open http://localhost:5173. You should see the **Sign in with Google** page.

The services and their ports (all on `localhost` only):

| Service | Port |
|---|---|
| web | 5173 |
| api | 8000 |
| weaviate | 8080, 50051 |
| gsim (simulated Google) | 8090 |
| postgres | 5432 |
| minio | 9000, 9001 |
| ollama (on the Mac, not in Docker) | 11434 |

Keep these ports on this Mac. Don't expose the api to a network: pairing codes have no attempt limit.

To stop the stack: `make down`. To wipe every meeting, document and account and start again: `make reset`, then `make up`.

## 3. Install the helper, grant permissions, pair it

### 3a. Build the app

```
make helper-app
```

This makes a local signing certificate the first time (macOS may ask for your login password), builds `dist/Fleck Helper.app` and signs it. The same certificate keeps macOS permissions across rebuilds.

Give the app your Claude key once. It reads it from `.env` and saves it in your login keychain:
```
uv run --package fleck-helper fleck-helper set-claude-key --from-env .env
```
It prints `Saved the Claude key to your login keychain.`

### 3b. Sign in to the web app first

Pairing needs a signed-in web app. Do step 4a now (simulated Google), then come back.

### 3c. Pair the helper

1. In the web app, open **Settings** and press **Get a pairing code**.
2. Run the command it shows, for example:
   ```
   uv run --package fleck-helper fleck-helper pair ABCD-1234
   ```
   It prints `Paired.` The code works once, for 10 minutes.
3. Check it:
   ```
   uv run --package fleck-helper fleck-helper status
   ```
   You should see `api http://localhost:8000: reachable` and `paired: yes`.

### 3d. Start the app and grant permissions

```
open "dist/Fleck Helper.app"
```

First, macOS asks whether Fleck Helper may use the `ai.fleck.helper` items in your login keychain. It asks once for the Claude key and once for the pairing token. Type your login password and press **Always Allow** each time. **Fleck** appears in the menu bar only after you answer. It asks again after each `set-claude-key` or `pair`, because the command line wrote those items, not the app.

Then macOS asks for permissions the first time the app needs them:
- **Microphone**: allow it.
- **Screen & System Audio Recording**: this is how Fleck hears the call. If macOS doesn't ask, open System Settings > Privacy & Security > Screen & System Audio Recording and turn on Fleck Helper.
- **Notifications**: allow them. Fleck reminds you 5 minutes before a meeting.
- **Accessibility** (optional): System Settings > Privacy & Security > Accessibility, turn on Fleck Helper. Without it, the ⌥⌘F shortcut works only while Fleck's menu is open.

Open the **Fleck** menu. You should see **Connected as** and your email. In the web app, Today shows **Desktop app connected** within a minute.

If you paired after opening the app, the menu says **Not paired: run fleck-helper pair <code>**. Choose **Quit Fleck** and open the app again.

## 4. Sign in

### 4a. Simulated Google (gsim)

1. Open http://localhost:5173 and press **Sign in with Google**.
2. The simulator shows **Choose an account**. Press **rupak@northbeam.test**.
3. Press **Allow**.

You land on **Today**, with meetings from the simulated calendar. One is **Presales: demo prospect**.

`outsider@example.test` is refused with "That Google account isn't allowed to use Fleck." That is the allow-list working.

### 4b. Real Google

Do step 6 first if you want the demo meeting: `make seed-demo` signs in through the simulator.

**Google Cloud setup (once):**
1. Go to https://console.cloud.google.com and create a project (or pick one).
2. Enable the **Google Calendar API** for the project (APIs & Services > Library).
3. Set up the OAuth consent screen (Google Auth Platform, or APIs & Services > OAuth consent screen):
   - App name: Fleck. Add your support email.
   - Audience: **Internal** if your team shares a Google Workspace, else **External**. For External in testing mode, add each team member as a test user.
   - Data access (scopes): `openid`, `email`, `profile` and `https://www.googleapis.com/auth/calendar.readonly`. Fleck only reads the calendar.
4. Create an OAuth client: Credentials (or Clients) > Create > OAuth client ID.
   - Application type: **Web application**.
   - Authorized redirect URI: `http://localhost:8000/api/auth/callback`
5. Copy the client ID and the client secret.

**Switch Fleck to real Google:**
1. In `.env` set:
   ```
   GOOGLE_CLIENT_ID=<your client id>
   GOOGLE_CLIENT_SECRET=<your client secret>
   FLECK_GOOGLE_MODE=google
   FLECK_ALLOWED_DOMAINS=<your firm's email domain>
   ```
   Use `FLECK_ALLOWED_EMAILS=a@x.com,b@y.com` for single people outside that domain.
2. Run `make up`. Compose recreates the api and worker with the new settings.
3. Open http://localhost:5173, sign out in **Settings** if you were signed in, then **Sign in with Google** with your real account.
4. Today now shows your real calendar. Pair the helper again (step 3c) for the new account, then **Quit Fleck** and open the app again.

To go back to the simulator, set `FLECK_GOOGLE_MODE=gsim` and run `make up`.

## 5. Add material to the library

### The demo library

```
make seed-library
```

This indexes everything under `clients/`: 15 documents (3 firm, 6 reference, 6 client). Running it again skips unchanged files. Open **Library**: each row goes from **Waiting to index** to **Indexing, 32 of 120 passages** (for example) to **Indexed**.

### Your own documents

1. Open **Library** and press **Add documents**.
2. **Files**: PDF, DOCX, PPTX, XLSX, Markdown or text, up to 50 MB each.
3. **Kind of document**:
   - **Our past work**: work your firm did for a client. Pick the **Client**, or **New client…** and type its **New client name**.
   - **Firm**: rate card, capabilities, FAQ. No client.
   - **Reference**: third-party examples, such as public contracts. Fleck quotes their terms but never presents them as your work.
4. Press **Upload**. The dialog closes and the new rows appear at once.

Prices shown live come only from documents. Put your rates in a **Firm** document, because a past client's price is never quoted as a price for a new client.

The library is shared with everyone on the team. **Used in answers** counts how often the live answers cited each document.

## 6. Replay the test conversation and read the result

```
make seed-demo
```

It takes about 8 minutes, most of it a real-time replay. It:
- runs `make seed-library`;
- signs in as rupak@northbeam.test and syncs the simulated calendar;
- creates the client **Harbourline Legal** and links it to **Presales: demo prospect**;
- builds that meeting's brief;
- plays `fixtures/conversation/conversation.wav` through the real helper pipeline into the meeting, stops it, and waits until it's ready.

If the demo meeting is already there, it says so and stops.

Then, in the web app:
1. **Today**: the **Presales: demo prospect** card shows **Harbourline Legal** under **Client**. Press **Open brief** to see the brief: **What we know going in**, **Likely questions**, and **Where our material is thin**.
2. **Meetings** > **Presales: demo prospect**. The **Notes** tab shows:
   - **Where Fleck helped**: the 8 questions, each with the answer Fleck showed, its source, and a marker such as **Used, auto** or **Not used, auto**. Press a marker to flip it; it then reads **by hand**, and a hand-set value always wins.
   - **Notes**: the summary. **Your words** are kept word for word; grey text is **Added by Claude**.
3. Press **Play recording**. The marks on the timeline are the moments Fleck answered; press one to jump there.
4. **Transcript** tab: every line, **You** and **Client**, with a time you can press to play from.
5. **Chat** tab: type a question in **Ask about this meeting**, for example "What did they say about where documents can live?", and press **Ask**. Each source is a link: a meeting time opens the transcript at that line, and a document opens it in the library.

What the answer labels mean (the panel and the web app use the same ones):
- **Our material** + document title: written from your library, with the source.
- **Reference contract** + "Not our work: <title>": from a reference document, quoted for its terms.
- **General knowledge** + "Not from our material": Claude answered from the question alone. It never contains prices or claims about your past work.
- **Not in our material**: nothing in the library answers it, or a figure failed the check. Fleck shows no guess. A price question with no backing document always ends here.
- **Thinking…**: Fleck is working on it.
- **Late**: see "Late answers" below.

## 7. Record a first real meeting

### Before the meeting

1. **Link the meeting to a client.** On **Today**, use the **Client** dropdown on the meeting's card. Fleck may offer **Suggested: <client>** from the guests' email domain, or **New client: <name>**. Linking a client gives a fuller brief.
2. **Read the brief.** Press **Open brief**.
   - With a client, Fleck builds the brief by itself: when you link the client, and again 30 minutes before the meeting.
   - With no client, Fleck builds it only when you press **Refresh brief**. It uses the event's title and description.
   - Before anything goes to Claude, Fleck replaces the attendees' names with "someone" and client names with "a client", and drops emails, links and long numbers. Claude only proposes the questions. The answers come from your library, on your Mac.
   - **Refresh brief** builds it again at any time.

### Start recording

Any of these starts a recording:
- the 5-minute notification: press **Record** (or click the notification);
- **Record** on the meeting's card on **Today**, or on the brief page;
- the **Fleck** menu: the meeting under **Today**, or **Start recording now** for a call that isn't in your calendar.

**Record** in the web app needs the helper running. If it isn't, you see "Desktop app not connected".

Join the call as usual, on speakers or a headset. No bot joins.

### During the meeting

- The menu-bar title shows **● Fleck** and the recording time.
- The answer panel appears at the top centre of your screen. It reads **Fleck is listening** until the first question.
- When the client asks a question, the panel shows **Client asked at m:ss**, then **Thinking…**, then a 2–3 line answer and its label. Read it; you never need to click or switch windows.
- ◀ ▶ step through earlier answers. A new answer always jumps to the newest.
- **Hide ⌥⌘F** (or ⌥⌘F, or **Hide answer panel** in the menu) hides the panel. **Show answer panel** brings it back.
- The web page for the meeting has **My notes** (saved as you type) and the **Live transcript**. Answers show there too.
- The panel is kept out of screen shares. If it ever shows up in your shared screen, share a single window instead of the whole screen.

**Late answers.** If your Mac is busy, Fleck may first fail to classify a client's line in time. It then checks the line again for up to 30 seconds. If the line was a question after all, the answer arrives with a **Late** tag. It answers an earlier question, not the one just asked.

**A meeting with no calendar event.** A recording started with **Start recording now** has no client. Pick one in the **Client** dropdown at the top of the meeting page, during or after the meeting. Only existing clients are listed; make new ones on Today or when you upload a document.

### Stop and read the notes

- Press **Stop** on the meeting page, or **Stop recording** in the menu.
- The page shows **Processing the recording…**, then the notes. A 3-minute call took about 30 seconds.
- Read **Where Fleck helped** and the **Used** / **Not used** markers. Fleck marks an answer **Used** when what you said in the 15 seconds after it appeared is close to it. It measures topic, not correctness, so check the markers and flip any that are wrong.
- **Claude cost** at the top is what this meeting's Claude calls cost.

### Chat

- **Chat** on a meeting answers about that meeting, plus the library.
- **Ask across meetings** answers from every recorded meeting and the library.
- Answers cite meetings, times and documents. A sentence whose figures are not in the sources it cites is left out, and the answer ends "A sentence was left out: its figures are not in the sources it cites." So an answer can be shorter than you expect.

## 8. Troubleshooting

Logs:
- the helper: `~/Library/Logs/Fleck/helper.log`
- the stack: `docker compose -f deploy/docker-compose.yml logs api worker`
- the helper's state: `uv run --package fleck-helper fleck-helper status`

**Permissions**
- No client words in the transcript: Screen & System Audio Recording is off for Fleck Helper. Turn it on in System Settings > Privacy & Security, then quit and reopen the app.
- A denied permission that won't come back: `tccutil reset ScreenCapture ai.fleck.helper`, then open the app again.
- ⌥⌘F does nothing: grant Accessibility (step 3d), or use **Hide answer panel** in the menu.
- Permissions lost after `make helper-app`: they normally stay, because the bundle id and certificate don't change. If not, grant them again.

**A password pop-up from macOS (the keychain)**
- After `set-claude-key` or `pair`, macOS asks for your login password when Fleck Helper reads the `ai.fleck.helper` item in your keychain. The command line saved that item, so macOS checks that the app may read it too.
- Type your login password and press **Always Allow**. macOS then stops asking, until the next `set-claude-key` or `pair`.
- If you press **Deny**, the app still runs. For the Claude key it means only that the live fallback has no key: the menu shows **⚠ No Claude key: general answers are off**, and questions the library can't answer show **Not in our material**. For the pairing token, the menu says **Not paired: run fleck-helper pair <code>**. macOS may ask again later; press **Always Allow** to stop it.

**Models not loaded**
- The menu shows **⚠ Question detection degraded**: the question model didn't answer in time, so stricter rules decide and most questions get no answer. Check Ollama: `curl -s localhost:11434/api/tags`. Run `make ensure-ollama`. Then stop and start the recording.
- The menu shows **⚠ No Claude key: general answers are off**: run `uv run --package fleck-helper fleck-helper set-claude-key --from-env .env`.
- `/api/health` shows ollama not ok: run `make ensure-ollama`.

**No audio**
- The transcript shows a **Fleck** line such as "Audio from the call was interrupted (1:02–1:09)": capture stopped and restarted. Fleck carries on.
- Nothing at all: check that the menu shows **Recording**, and that **Connected as** is there. If it says **Not connected to Fleck**, the stack is down: run `make up`.
- "Desktop app not connected" on **Record**: open `dist/Fleck Helper.app`, and pair it if the menu says **Not paired**.

**Slow answers**
- Answers are written by Ollama on your Mac, one request at a time. Another app using the same Ollama makes Fleck wait. Close it during calls.
- Fleck already holds back its own background work (indexing, briefs) while a meeting records.
- Heavy swap slows everything. Close big apps during a call.

**Sign-in**
- "That Google account isn't allowed to use Fleck.": add the domain to `FLECK_ALLOWED_DOMAINS` or the address to `FLECK_ALLOWED_EMAILS`, then `make up`.
- The api won't start: `FLECK_SECRET_KEY` is empty, `change-me` or too short. With `FLECK_GOOGLE_MODE=google`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` must also be set.
- "Calendar not synced. Sign in again": your Google sign-in expired. Press **Sign in again**.

**Pairing**
- "That code is wrong or has expired. Get a new one in Settings.": press **Get a pairing code** again and use the new code within 10 minutes.

**A meeting failed**
- The page shows **Processing failed:** and a reason. Press **Try again**.

**Start over**
- `make reset` wipes every meeting, document and account, and the helper's pairing with them. Then `make up`, `make seed-library`, sign in again, and pair the helper again (step 3c).
