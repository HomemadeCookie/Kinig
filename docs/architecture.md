# Kinig Lo-Fi MVP — Architecture & Project Structure

> Living document. Last updated: 2026-10-26. Edit freely as decisions change.

**Demo target:** Lo-Fi MVP demo on October 29, venture presentation on November 5

---

## 1. Overview

Kinig records a meeting, transcribes it, and generates notes **without using the internet or the cloud**.

For the Lo-Fi MVP, the work is split between two devices on a private Wi-Fi network:

| Device | Role |
|---|---|
| **Kinig Pod (ESP32)** | Captures audio, handles the button/LED, shows the QR code on the LCD |
| **Edge Hub (laptop)** | Receives audio, runs speech-to-text + summarizer, deletes audio, hosts the notes page |

> **Pitch wording:** "The pod captures audio, and processing happens on a local edge device. No cloud, no internet." Fully on-device processing is the production version.

---

## 2. System Architecture

```
┌──────────── Kinig Pod (ESP32) ────────────┐        ┌──────────── Edge Hub (laptop) ────────────┐
│ Button → state machine → LED              │        │ FastAPI server                            │
│ I2S mic → 16 kHz PCM chunks ──── HTTP ────┼──────▶ │  └ audio buffer → Whisper → summarizer    │
│ LCD ← shows QR ◀── session URL ───────────┼─────── │  └ delete audio, save notes (SQLite)      │
└───────────────────────────────────────────┘        │ Notes web page  ◀── phone scans QR        │
          all on a private Wi-Fi network, no internet                                           │
                                                     └───────────────────────────────────────────┘
```

### Network
- **Primary:** a pocket router with no SIM/internet. The pod, hub, and phones all join it.
- **Fallback:** the ESP32's own soft AP (weaker with many devices connected).
- Hub has a fixed IP on the network (e.g. `192.168.4.2`). Set it in `firmware/include/config.h`.

---

## 3. Hardware

| Component | Part | Notes |
|---|---|---|
| Microcontroller | ESP32 / ESP32-S3 | S3 with PSRAM preferred if available |
| Microphone | I2S MEMS mic (e.g. INMP441) | Omnidirectional |
| Display | Small LCD | Shows status + QR code |
| Controls | Push button + LED + resistor | Start/stop, status |
| Power | TBD | |
| Enclosure | 3D-printed case | |

> TODO: Update this table with what was actually purchased. Make sure the BOM slide in the deck matches.

### Pin map
| Signal | ESP32 pin |
|---|---|
| Mic SCK | TBD |
| Mic WS | TBD |
| Mic SD | TBD |
| LCD (SPI/I2C) | TBD |
| Button | TBD |
| LED | TBD |

---

## 4. Pod (ESP32) Design

### State machine
```
IDLE ──button──▶ RECORDING ──button──▶ PROCESSING ──notes ready──▶ SHOW_QR ──button/timeout──▶ IDLE
```

| State | LED | LCD |
|---|---|---|
| IDLE | Off / slow pulse | "Press to start" |
| RECORDING | Solid | "Recording…" (or QR in live mode) |
| PROCESSING | Blinking | "Processing…" |
| SHOW_QR | Off | QR code to notes |
| ERROR (upload failing) | Fast blink | Error message |

### Dual-core task split
- **Core 1:** reads the I2S mic continuously and fills audio buffers.
- **Core 0:** takes full buffers from a FreeRTOS queue and POSTs them to the hub.
- Recording never pauses for the network.
- Failed chunks are kept and retried. The LED switches to the error pattern.

---

## 5. Pod ↔ Hub API

All communication is HTTP over the private Wi-Fi network.

| Call | Purpose | Returns |
|---|---|---|
| `POST /sessions` | Start a meeting | `session_id`, `token` |
| `POST /sessions/{id}/audio?seq=N` | Upload one audio chunk | `200 OK` immediately |
| `POST /sessions/{id}/stop` | End the meeting | Notes URL |
| `GET /s/{id}?t={token}` | Notes page (phone) | HTML |
| `GET /s/{id}/transcript?t={token}` | Live transcript text (polled by page) | JSON |

**Notes URL format:** `http://<hub-ip>:8000/s/{id}?t={token}`. The pod renders it as a QR code.

**Access control:** the random token in the QR is the Lo-Fi version of host-controlled access. Only someone who scans the QR can open the notes.

### Audio format
- Raw PCM, **16 kHz, 16-bit, mono** (what Whisper expects)
- ~32 KB per second, ~1.9 MB per minute
- Sent as `Content-Type: application/octet-stream`
- `seq` number on each chunk so the hub can detect gaps or reordering
- Hub wraps the collected bytes in a WAV header before transcription

### What the bytes are
- **PCM waveform**, not frequency data: 16,000 loudness samples per second.
- Each sample is a signed 16-bit integer (−32,768 to 32,767), little-endian.
- A chunk has **no header**; the format is fixed by contract (`docs/audio-format.md`).
- Frequency analysis (spectrogram) happens inside Whisper, not on the ESP32.

### Where audio handling lives
| Step | Location |
|---|---|
| I2S capture; 32→16-bit shift (volume) | `firmware/src/audio.cpp`, `mic_shift` from `pod.toml` |
| Chunk reordering by `seq`, gap detection, windowing | `hub/kinig_hub/audio/buffer.py` |
| Bytes → float32 array; WAV header | `hub/kinig_hub/audio/format.py` |
| Test-mode `.wav` recording (off by default) | `hub/kinig_hub/audio/debug_dump.py` → `hub/debug_audio/` |

- Model adapters receive a ready float32 array and never deal with raw bytes.
- `debug_save_audio = false` for the real demo; keeping audio contradicts the privacy claim.

### Chunk size and memory limits
- The practical limit is ESP32 RAM: `http.POST(data, len)` needs the whole body in memory.
- Plain ESP32: ~300 KB heap; largest single block often ~100 KB. Check with `ESP.getMaxAllocHeap()`.
- **Use 0.5–1 s chunks (16–32 KB).** The queue holds several buffers at once (e.g. 4 × 32 KB = 128 KB).
- Reuse the HTTP connection (keep-alive) to cut per-request delay.
- For large files (SD fallback), stream from the card with `http.sendRequest("POST", &file, file.size())`.

### Tunable parameters (no reflashing needed)
Settings live in `hub/config/pod.toml` and are sent to the pod in the `POST /sessions` response.

```toml
chunk_ms = 1000       # audio per POST
buffer_count = 4      # buffers in the ESP32 queue
window_s = 8          # hub transcription window (separate knob from chunk size)
mic_shift = 14        # 32→16-bit shift; higher = quieter
debug_save_audio = false  # hub saves session .wav files when true (testing only)
```

- `firmware/src/remote_config.cpp` clamps values to a safe range: `chunk_ms × 32 bytes × buffer_count` must fit within `ESP.getMaxAllocHeap()`.
- If the hub is unreachable or a value is unsafe, the pod uses the defaults in `config.h`.
- Buffers are allocated at session start, so changes apply to the **next** recording.
- `GET/PUT /config` on the hub lets you view or change settings live.
- `hub/kinig_hub/metrics.py` logs each session's settings plus upload latency, missing `seq` numbers, retries, and transcription lag to `experiments/results.csv`.

### Transport alternatives
| Method | Verdict |
|---|---|
| **HTTP POST chunks** | **Current choice.** Simplest, easy to test with `curl` |
| WebSocket | Possible upgrade for smoother live streaming |
| Record to SD, upload at stop | Fallback if Wi-Fi is unreliable (needs SD module) |
| UDP | Not used. Packets can be silently lost |

---

## 6. Hub Design

### Core rule
**The audio endpoint never waits for Whisper.** It stores the chunk and returns `200 OK` in milliseconds. Transcription happens in a background worker.

### Processing flow
```
ESP32 ──POST chunk──▶ [Receiver]  appends to session audio buffer, returns 200 instantly
                           │
                           ▼  when ~5–10 s of new audio (or a pause) is ready
                      [Job queue]
                           │
                           ▼
                   [Transcription worker]  runs Whisper, appends text to transcript
                           │
Phone ──GET (poll)──▶ [Notes page]  reads whatever transcript exists so far
```

**On stop:** finish remaining jobs → summarize full transcript → delete audio → mark done.

### Design decisions
- **Chunks ≠ Whisper runs.** Chunks (`chunk_ms`, ~1 s) are for transport. `buffer.py` groups them into windows (`window_s`, ~5–10 s, cut at pauses); Whisper runs once per window. Pass the previous window's text as `initial_prompt` to keep context.
- **Load the Whisper model once** at startup and share it.
- **One worker, one queue.** Whisper already uses all CPU cores, so parallel jobs only slow each other down. The queue also guarantees `finalize` runs after all pending transcriptions. Simultaneous sessions (e.g. phone + laptop) share the queue and take turns, so lag roughly doubles.
- **Keep Whisper off FastAPI's event loop.** Run it with `asyncio.to_thread` or a dedicated worker thread.
- **Session status:** `recording → transcribing → summarizing → done`. The notes page shows it.
- **Split windows at pauses** using voice activity detection (`vad_filter=True` in faster-whisper) so words aren't cut.
- **Monitor lag.** If each window takes longer to transcribe than its own length, the queue grows forever. Log queue length / lag. Fixes: smaller model, longer windows.

### AI components (modular models layer)
The pipeline never calls a specific model directly. It talks to two interfaces, and the actual backend is chosen in `hub/config/models.toml`.

```python
# hub/kinig_hub/models/base.py
class Transcriber(ABC):
    def load(self) -> None: ...
    def transcribe(self, audio: np.ndarray) -> str: ...  # float32, -1..1, 16 kHz mono (from audio/format.py)

class Summarizer(ABC):
    def load(self) -> None: ...
    def summarize(self, transcript: str) -> dict: ...  # {"summary", "key_points", "action_items"}
```

```toml
# hub/config/models.toml
[transcriber]
backend = "faster_whisper"

[transcriber.options]          # passed as-is to the adapter; each backend reads only what it understands
model = "small"                # multilingual, not "small.en", for Tagalog/Taglish
language = "tl"                # or "auto"; benchmark both
task = "transcribe"            # discourages translating Tagalog into English
initial_prompt = "Okay, so yung deadline natin is Friday, tapos i-review natin yung contract."

[summarizer]
backend = "ollama"
fallback = "rule_based"        # used if the main one fails or times out
timeout_s = 60

[summarizer.options]
model = "llama3.2:3b"          # or a SEA-LION GGUF imported into Ollama
prompt_file = "config/prompts/summary.txt"
output_language = "en"
```

| Stage | Available backends | Possible additions |
|---|---|---|
| Speech-to-text | `faster_whisper` (default), `whisper_cpp` | `transformers_whisper` (Hugging Face fine-tunes as-is), `mms` (Meta MMS, non-Whisper) |
| Summarizer | `ollama` (default), `rule_based` (fallback) | |

**Adapter defaults (as built in Phase 1):**
- Whisper adapter, if `[transcriber.options]` omits them: `device = "cpu"`, `compute_type = "int8"`, `vad_filter = true`, `language = "tl"`. `language = "auto"` lets Whisper detect. Add a key to override.
- Ollama adapter: default `host = "http://127.0.0.1:11434"`; override under `[summarizer.options]`. Errors and timeouts fall back to `rule_based`.
- `summary.txt` contains `{output_language}`, filled from `summarizer.options.output_language` (mapped to the language name, e.g. `en` → "English"). Language instructions live only in the prompt file.
- `rule_based` fallback: sentence split plus a short English/Tagalog cue list (e.g. "need to", "will", "kailangan").
- faster-whisper keeps its cache layout inside `model_files/whisper/` (e.g. `models--Systran--faster-whisper-small/`), not a single flat file. Runtime loads use `local_files_only`.

**Taglish-friendly model switching:**
- **Per-backend options:** the registry passes each `options` block straight to its adapter, so non-Whisper models aren't forced into Whisper-shaped settings.
- **Fine-tuned Whisper from Hugging Face** is usually in Transformers format. First try converting with faster-whisper's `ct2-transformers-converter` into `model_files/whisper/` (no code change); otherwise add `transformers_whisper.py`.
- **Non-Whisper speech models** (e.g. Meta MMS, which covers Tagalog) need one new adapter file; the float32 16 kHz interface already fits.
- **Summary prompt** lives in `config/prompts/summary.txt`, not in code, so each LLM can get a tuned prompt.
- **Custom Ollama models:** import a GGUF with `ollama create` and keep the Modelfile in `model_files/ollama/` for reproducibility.

**Code vs. weights:**
- `models/transcribers/*.py` and `models/summarizers/*.py` are **adapters** (code): they know *how* to run a kind of model.
- `model_files/` holds the **weights** (data). Large, gitignored.
- In `models.toml`, `backend` picks the adapter; `model` / `path` picks the weights. One adapter can run many models.

| Backend | Where weights live | Config needs |
|---|---|---|
| `whisper_cpp` | `model_files/whisper/ggml-*.bin` | `path` to the file |
| `faster_whisper` | `model_files/whisper/` (via `download_root`) | `model` name; downloads on first run |
| `ollama` | Ollama's own storage (not `model_files/`) | `model` name; fetch first with `ollama pull <name>` |

> Download all models **before** demo day. The pocket router has no internet. `demo_check.sh` should verify every configured model is present.

- **Swap a model:** change one line in `models.toml`.
- **Add a model:** add one file under `models/transcribers/` or `models/summarizers/` and register it in `registry.py`. No pipeline changes.
- **Same tests for every backend:** `tests/test_models.py`.
- **Metrics** record which models ran, so `experiments/results.csv` compares models as well as chunk sizes.
- Lo-Fi version of the "future model cartridges" idea from the pitch.

### Privacy behavior
- Audio exists only in temporary storage during the session.
- Audio is deleted once the transcript is complete.
- Only text (transcript + notes) is kept, in local SQLite.
- Notes are accessible only with the session token.

### Dev testing path (temporary; remove before production)
**Principle:** dev tools are only *callers*. They get audio into a normal session. They never run or choose models.

```
Phone upload ─┐
fake_pod.py  ─┼─▶ convert to the standard format ─▶ normal session ─▶ buffer ─▶ worker ─▶ models ─▶ notes page
ESP32 (real) ─┘
```

**`/dev` virtual pod** (`api/dev.py`, `templates/dev.html`, `static/dev/`)
A page that does everything the ESP32 does, including showing the QR, so testing never waits on hardware. Runs on the laptop at `localhost`, or on a phone over a dev-only HTTPS port.

```
┌───────────────── Kinig Virtual Pod (DEV) ─────────────────┐
│  Source: (•) Laptop mic   ( ) Upload file  [choose…]      │
│                                                           │
│  [ ● START ]        State: RECORDING   00:42              │
│                                                           │
│  ┌──────────┐   Live transcript preview…                  │
│  │  QR CODE │   chunks sent: 42 · gaps: 0 · lag: 3.1 s    │
│  └──────────┘   transcriber: faster_whisper/base          │
└───────────────────────────────────────────────────────────┘
```

Usage:
1. Run the hub with `dev_mode = true`.
   - **Laptop:** open `http://localhost:8000/dev`.
   - **Phone:** set `dev_https = true`, then open `https://<laptop-ip>:8443/dev` (see "Phone testing over HTTPS" below).
2. Choose a source: **device mic** (live) or **upload file** (recording, benchmark file).
3. Start / Stop like the pod's button. Same states: `IDLE → RECORDING → PROCESSING → SHOW_QR`.
4. A large QR appears on the laptop screen (at Start in live mode). Phones scan it to open the normal notes page.

How it stays a caller:
- **Device mic (laptop or phone):** the browser acts as a third pod following the same contract as the ESP32:
  1. `POST /sessions` returns session ID, token, **and current `pod.toml` settings** (same response the ESP32 gets).
  2. Captures the mic, downsamples to 16 kHz, converts to int16 mono, collects `chunk_ms` of samples, POSTs each chunk with `seq`.
  3. Change `chunk_ms` in `pod.toml` or via `PUT /config`; the next recording uses it. **Chunk size is never hardcoded in the page.**

  | File | Role | ESP32 equivalent |
  |---|---|---|
  | `static/dev/pod.js` | State machine + session API; reads `chunk_ms` from `/sessions` | `main.cpp` + `net.cpp` |
  | `static/dev/recorder-worklet.js` | AudioWorklet: captures mic off the main thread (no gaps) | Core 1 capture task |
  | `static/dev/pcm.js` | Downsample 44.1/48 kHz → 16 kHz, float → int16, build chunks | `audio.cpp` |

- **Caller tag:** `POST /sessions` includes `client` (`esp32`, `browser`, `fake_pod`), recorded in metrics so `results.csv` compares chunk sizes per caller.
- **Phone vs laptop:** there is no separate phone mode in code; both load the same page. `api/sessions.py` refines `browser` by request origin: from `localhost` → `browser-laptop`; from another IP (via port 8443) → `browser-phone`. The page shows a banner like "Phone · HTTPS · chunk 1000 ms".
- **Upload file:** `audio/decode.py` converts the file to the standard format and pushes it into a normal session. Uploaded files are deleted right after decoding.
- **QR:** encodes the same notes URL the pod would show; served as SVG from `GET /dev/qr/{id}.svg` (Python `qrcode` package).
- Status panel shows chunk count, gaps, lag, and active models (read-only).

Gotchas:
- **QR must use the laptop's network IP, not `localhost`.** Set `public_host = "192.168.x.x"` in `hub.toml`; all notes URLs are built from it.
- **Browsers record at 44.1/48 kHz** and may ignore a 16 kHz request (esp. iPhone Safari), so `pcm.js` must downsample.

**Phone testing over HTTPS (dev-only, free)**
Browsers block mic access on plain `http://192.168…` addresses; only `localhost` or HTTPS is allowed. HTTPS is used **only for phone recording during dev**.
- **Two ports, same app:**
  | Port | Protocol | Used by |
  |---|---|---|
  | 8000 | HTTP | ESP32, `fake_pod.py`, laptop dev page, all notes pages |
  | 8443 | HTTPS | Phone dev page only (loaded when `dev_https = true`) |
- Notes URLs in QR codes always use HTTP on 8000; viewing notes needs no mic, so it doesn't need HTTPS.
- `scripts/make_certs.sh` uses **mkcert** (free, open source, offline) to create a cert for the laptop's IP in `hub/certs/`.
- **Install the mkcert root certificate on each test phone** once. Clicking through a "not secure" warning is unreliable for mic access, especially on iPhone.
- If the laptop's IP changes (different network), regenerate the cert.
- **Offline-safe:** QR generated on the hub; all JS kept in `static/`, no CDN.
- Only loaded when `dev_mode = true` in `hub/config/hub.toml`. **Turn off for the demo.**

**`scripts/fake_pod.py`** — sends audio through the real `/sessions` endpoints in the exact ESP32 format.
| Option | Purpose |
|---|---|
| `--file x.m4a` | Repeatable runs from a recording (use for measurements) |
| `--realtime` | Send at real speed, to simulate a meeting for live mode |
| `--mic` | Stream live from the laptop mic (`sounddevice`, 16 kHz int16 mono) |
| `--shuffle`, `--drop 5%` | Misbehave on purpose to test `seq` reordering and gap detection |
| (always) | Prints the notes QR in the terminal |

Tests: session API, chunk handling, `chunk_ms` behavior, async worker, live mode lag, finalize + audio deletion, notes page + token, Wi-Fi (run from a second laptop), metrics.
Can't test: ESP32 hardware (I2S mic, `mic_shift`, RAM limits, button/LED/LCD, ESP32 Wi-Fi).

**Model comparison is separate:** `scripts/bench_models.py` runs a WAV (default `hub/tests/sample_audio/taglish.wav`, or `--file`) through the configured models via the registry, offline (no API, no hub running). It prints transcript, summary, and timings, and appends a row to `experiments/bench_results.csv` (separate from session metrics in `results.csv`). Columns: timestamp, file, audio_seconds, transcriber_backend, transcriber_options, summarizer_backend, summarizer_options, transcribe_seconds, summarize_seconds, realtime_factor (transcribe time ÷ audio length), fallback_used. No accuracy column until a reference transcript exists; then add CER using `taglish.txt` beside the WAV. Input must be 16 kHz mono 16-bit WAV (read with the standard `wave` module); otherwise it exits with the ffmpeg conversion command.

**Model setup:** `scripts/download_models.py` (needs internet once) downloads the configured Whisper model into `model_files/whisper/` and checks the configured Ollama model, printing the `ollama pull` command if missing. A missing model at runtime is a hard error pointing to this script. Model choice for the live system comes only from `models.toml` (or `PUT /config`).

| Part | Job |
|---|---|
| ESP32 / `fake_pod.py` / `/dev` upload | **Callers.** Get audio into a session |
| Sessions + buffer + worker | **Pipeline.** Order, queue, finalize, delete |
| `models/` + `models.toml` | **Models.** The only place models are chosen and run |
| `bench_models.py` | **Evaluation.** Offline model comparison |

**Removal:** delete `api/dev.py`, `audio/decode.py`, `templates/dev.html`, `static/dev/`, `certs/`, `make_certs.sh`, and the `dev_mode` / `dev_https` flags. Nothing else depends on them.

> Keep one real Taglish practice recording in `tests/sample_audio/` as the standard benchmark file.

### Phase 2 implementation details (as built)
- `hub.toml` has `session_mode = "end"` (or `"live"`); a `POST /sessions` body can override it with `mode`.
- `POST /sessions` returns `session_id`, `token`, `notes_url`, `client`, `mode`, and current `pod.toml` settings (so live mode can show the QR before stop).
- `Transcriber.transcribe(audio, initial_prompt=None)`: the worker passes the previous window's text; `None` keeps the prompt from `models.toml`.
- Pause cuts use fixed settings in `buffer.py` (200 ms of quiet near the window end); otherwise cut at `window_s`.
- A missing `seq` is filled with `chunk_ms` of silence. A duplicate `seq` counts as a retry.
- Hub listens on `0.0.0.0`; notes URLs use `public_host` and port 8000.
- Notes page polls while status is `recording`, `transcribing`, or `summarizing`; wrong/missing token → 403.
- `experiments/results.csv` columns: timestamp, session_id, client, mode, status, chunk_ms, buffer_count, window_s, mic_shift, audio_seconds, chunks_received, missing_seqs (`|`-separated), retries, upload_ms_avg, transcribe_seconds, summarize_seconds, lag_seconds (worst window's queue-to-finish time minus its length), transcriber_backend, summarizer_backend, fallback_used.
- `fake_pod.py` also has `--live`; it resamples non-16 kHz WAVs and decodes other formats with PyAV.

### Modes
| Mode | Behavior | Priority |
|---|---|---|
| **End-of-meeting** | Transcribe + summarize after stop; QR shown at end | **Build first** |
| **Live** | Transcribe rolling windows during the meeting; QR shown at start; page polls for new text; summary at end | Upgrade if time allows (hub-only change) |

---

## 7. Directory Structure

```
kinig/
├── firmware/                  # ESP32 (PlatformIO, Arduino framework)
│   ├── platformio.ini
│   ├── include/
│   │   └── config.h           # Wi-Fi SSID, hub IP, pins, default + max chunk settings
│   └── src/
│       ├── main.cpp           # setup + state machine loop
│       ├── remote_config.cpp/.h # fetch tunable settings from hub, clamp to safe range
│       ├── audio.cpp/.h       # I2S mic capture, 32→16-bit shift; buffers sized from chunk_ms
│       ├── net.cpp/.h         # Wi-Fi + HTTP calls to the hub
│       ├── display.cpp/.h     # LCD + QR rendering
│       └── controls.cpp/.h    # button debounce, LED patterns
│
├── hub/                       # Laptop software (Python)
│   ├── pyproject.toml
│   ├── config/
│   │   ├── hub.toml           # hub settings: dev_mode, dev_https, public_host (laptop LAN IP), http_port 8000, https_port 8443
│   │   ├── pod.toml           # transport: chunk_ms, buffer_count, window_s, mic_shift
│   │   ├── models.toml        # which transcriber/summarizer + per-backend options
│   │   └── prompts/
│   │       └── summary.txt    # summarizer instructions (tunable per LLM)
│   ├── model_files/           # downloaded/converted weights (gitignored); ollama/ holds Modelfiles
│   ├── debug_audio/           # test-mode .wav recordings (gitignored)
│   ├── certs/                 # DEV: mkcert HTTPS cert + key for phone testing (gitignored)│   ├── kinig_hub/
│   │   ├── main.py            # FastAPI app entry point, starts worker
│   │   ├── audio/
│   │   │   ├── format.py      # format constants; bytes → float32; WAV header
│   │   │   ├── buffer.py      # per-session buffer: seq reorder, gap detection, windows
│   │   │   ├── decode.py      # DEV: any audio file → float32 16 kHz mono
│   │   │   └── debug_dump.py  # save .wav when debug_save_audio = true
│   │   ├── metrics.py         # per-session latency, gaps, retries, lag, models → results.csv
│   │   ├── api/
│   │   │   ├── sessions.py    # session + audio + stop endpoints (returns pod config)
│   │   │   ├── config.py      # GET/PUT /config to view or change settings live
│   │   │   └── dev.py         # DEV caller: virtual pod page, file upload, QR SVG (dev_mode only)
│   │   ├── models/            # swappable model layer
│   │   │   ├── base.py        # Transcriber + Summarizer interfaces
│   │   │   ├── registry.py    # builds backends from models.toml
│   │   │   ├── transcribers/
│   │   │   │   ├── faster_whisper.py
│   │   │   │   └── whisper_cpp.py
│   │   │   └── summarizers/
│   │   │       ├── ollama.py
│   │   │       └── rule_based.py
│   │   ├── pipeline/
│   │   │   ├── worker.py      # job queue + background worker; uses models via interfaces
│   │   │   └── cleanup.py     # delete audio after transcription
│   │   ├── storage.py         # SQLite: sessions, tokens, notes
│   │   ├── web/
│   │   │   ├── templates/     # notes page (HTML); dev.html = DEV virtual pod
│   │   │   └── static/        # all JS/CSS bundled locally (offline)
│   │   │       └── dev/       # DEV browser pod (laptop via localhost, phone via HTTPS)
│   │   │           ├── pod.js              # state machine + session API; reads chunk_ms
│   │   │           ├── recorder-worklet.js # AudioWorklet mic capture
│   │   │           └── pcm.js              # downsample to 16 kHz, int16, chunking
│   │   └── config.py
│   └── tests/
│       ├── test_models.py     # every backend must pass the same tests
│       ├── test_audio.py      # format conversion, out-of-order and missing chunks
│       └── sample_audio/      # test recordings: taglish.wav (16 kHz mono s16), later taglish.txt reference; .gitkeep
│
├── hardware/
│   ├── wiring.md              # pin map: mic, LCD, button, LED
│   ├── enclosure/             # STL / CAD files
│   └── bom.md                 # what was actually bought
│
├── experiments/
│   ├── results.csv            # one row per session (settings + pipeline metrics), written by metrics.py
│   ├── bench_results.csv      # one row per bench_models.py run (model comparison)
│   └── notes.md               # what was tried and what was learned
│
├── scripts/
│   ├── run_hub.sh             # start the hub on demo day
│   ├── demo_check.sh          # pre-demo test: network, models, mic, dev_mode off
│   ├── make_certs.sh          # DEV: mkcert cert for the laptop IP + phone install steps
│   ├── fake_pod.py            # file or laptop mic → ESP32-format chunks → real session API
│   ├── download_models.py     # one-time setup: fetch Whisper weights, check Ollama model
│   ├── bench_models.py        # offline model comparison via registry → bench_results.csv
│   └── sweep_chunks.py        # run tests across several chunk sizes
│
├── docs/
│   ├── architecture.md        # this file
│   ├── audio-format.md        # audio contract shared by firmware and hub
│   └── privacy.md             # what's stored, what's deleted, when
│
├── .gitignore                 # model_files/, debug_audio/, certs/, .venv/, __pycache__/, *.db, experiments/*.csv, *.egg-info/, .pytest_cache/
└── README.md
```

Python packages under `hub/kinig_hub/` include `__init__.py` files; `hub/pyproject.toml` installs `kinig_hub` as a package (`pip install -e ".[dev]"`) so scripts can import it.

### Ownership
| Area | Owner |
|---|---|
| `firmware/` | Carl Steven Sumadsad (Hardware) |
| `hub/api/`, `hub/web/`, `storage.py` | Lance Benedict Cabrera (Software) |
| `hub/models/`, `hub/pipeline/` | Niccolo Coronel (AI & Security) |
| `hardware/` | Joshua Miguel Pangilinan (Industrial Design) |
| `docs/`, pitch alignment | Bernard Tapiru Jr. (Product) |

---

## 8. Build Order

- [ ] Confirm purchased hardware; update §3 and the deck's BOM slide
- [ ] Hub: transcribe a pre-recorded WAV end to end (no hardware needed)
- [ ] `bench_models.py` to compare models offline on the benchmark recording
- [ ] Hub: dev virtual pod page on the laptop (mic following chunk_ms, file upload, QR)
- [ ] Phone testing: `make_certs.sh`, HTTPS port 8443, root cert installed on test phones
- [ ] `fake_pod.py` to test the full session pipeline without the ESP32
- [ ] Hub: summarizer + rule-based fallback
- [ ] Hub: session endpoints + notes page with token
- [ ] Firmware: button/LED state machine
- [ ] Firmware: I2S mic capture
- [ ] Firmware: chunked POST upload with retry
- [ ] Firmware: QR on LCD
- [ ] Full end-to-end test on the pocket router
- [ ] (Stretch) Live transcription mode
- [ ] `demo_check.sh` + rehearsal

---

## 9. Known Risks & Open Questions

| Item | Status / Notes |
|---|---|
| Taglish accuracy on small Whisper models | Use multilingual `small`+, `task="transcribe"`, Taglish `initial_prompt`; benchmark `tl` vs auto; fall back to English demo if needed. Post-MVP: fine-tune on Taglish |
| Laptop keeping up with live mode | Measure lag; choose model size accordingly |
| Wi-Fi drops during demo | Retry + error LED; SD-card fallback if needed |
| Deck says "on-device AI"; Lo-Fi uses an edge hub | Update slides / address up front |
| Power source for the pod | TBD |

---

## 10. Changelog

| Date | Change |
|---|---|
| 2026-10-26 | Initial version |
| 2026-10-26 | Made chunk size and related settings tunable from the hub; added metrics and experiments folder |
| 2026-10-26 | Added modular models layer (interfaces, registry, models.toml); removed transcribe.py / summarize.py from pipeline |
| 2026-10-26 | Added hub audio/ module, audio-format contract, mic_shift + debug_save_audio settings; Transcriber now takes a float32 array |
| 2026-10-26 | Added dev testing path (/dev upload page, decode.py, hub.toml dev_mode) and fake_pod.py |
| 2026-10-26 | Dev path reworked to callers only (feeds normal sessions); removed model overrides; added fake_pod modes and offline bench_models.py |
| 2026-10-26 | Dev page became a virtual pod: laptop mic or file, start/stop states, QR on laptop screen; added public_host; fake_pod prints QR in terminal |
| 2026-10-26 | Browser pod follows chunk_ms from /sessions; static/dev/ split (pod.js, recorder-worklet.js, pcm.js); client tag in metrics |
| 2026-10-26 | Dropped HTTPS and phone recording; dev testing is laptop-only (phones just scan the QR) |
| 2026-10-26 | Re-added HTTPS for phone dev testing only: separate port 8443 behind dev_https; ESP32 and notes pages stay on HTTP 8000 |
| 2026-10-26 | Hub tags browser sessions as browser-laptop / browser-phone by request origin; clarified chunks vs Whisper windows and single-queue behavior |
| 2026-10-26 | Taglish-ready model switching: per-backend options blocks, prompts/summary.txt, conversion path for Hugging Face fine-tunes, possible transformers_whisper / mms adapters |
| 2026-10-26 | Phase 1 built: download_models.py, bench_results.csv (columns listed), adapter defaults, {output_language} in prompt, http/https ports in hub.toml, .gitignore, installable kinig_hub package |
| 2026-10-26 | Phase 2 built: session_mode, start response fields, initial_prompt chaining, pause cuts, gap/retry rules, results.csv columns, fake_pod --live |
| 2026-10-26 | Phases 3–7 built. public_host defaults to auto (UDP connect to 192.0.2.1, no packet sent; a literal IP overrides). Shipped models are Whisper base and llama3.2:1b. GET/PUT /config edits toml lines in place; pod settings apply on the next session; a model change is loaded by the worker between jobs. /dev returns 404 while dev_mode is false. dev_https serves the same app on 8443 with hub/certs/dev.pem and dev-key.pem, and does not start the worker a second time. The LCD driver prints status and a QR on Serial until a panel is chosen. Firmware pins are -1. Until the button pin is set, s on the serial monitor is the button. Until the mic pins are set, capture sends silence. |
