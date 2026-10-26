# Kinig

Kinig is an offline meeting pod. A small recorder sends audio to a laptop on the same Wi-Fi. The laptop transcribes it, writes notes, deletes the audio, and shows the notes behind a QR code.

The shipped model settings fit an 8 GB laptop: Whisper `base`, and Ollama `llama3.2:1b` with a rule-based fallback if Ollama does not answer.

## What you need

- Python 3.11 or newer
- [Ollama](https://ollama.com/download) for summaries
- PortAudio if you want `scripts/fake_pod.py --mic` or the browser is not enough
- ffmpeg if you need to convert a phone recording to WAV

The hub does not download models while it is running. Setup downloads them once.

## 1. System packages

### Fedora

```bash
sudo dnf install python3 python3-pip python3-devel gcc portaudio portaudio-devel ffmpeg
```

Install Ollama from the Ollama site, then start it (`ollama serve` if it is not already a service).

### Ubuntu

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-dev gcc portaudio19-dev ffmpeg
```

Install Ollama from the Ollama site.

### Windows

Install Python 3.11 or newer from python.org and tick "Add python to PATH". Install Ollama from the Ollama site. The `sounddevice` wheel includes PortAudio, so a separate PortAudio install is usually unnecessary. ffmpeg is optional and comes from the ffmpeg website. `scripts/setup.sh` is for Linux. Use the Windows commands in the next section.

## 2. Python setup

From the repo root.

### Linux

```bash
bash scripts/setup.sh
```

That creates `hub/.venv`, runs `pip install -e "hub[dev]"` (the same install as `pip install -e ".[dev]"` from the `hub` directory), and runs `scripts/download_models.py`. If a step fails, the script prints the command that fixes it.

The same steps by hand, from the repo root:

```bash
python3 -m venv hub/.venv
source hub/.venv/bin/activate
python -m pip install -U pip
python -m pip install -e "hub[dev]"
python scripts/download_models.py
```

### Windows

```bat
py -3.11 -m venv hub\.venv
hub\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e "hub[dev]"
python scripts\download_models.py
```

`download_models.py` saves Whisper under `hub/model_files/` and tells you if the Ollama model is missing. Pull it with the printed command, for example `ollama pull llama3.2:1b`.

## 3. Address other devices use

`hub/config/hub.toml` ships with:

```toml
public_host = "auto"
```

On startup the hub prints the LAN address it detected. QR codes and notes links use `http://<that-address>:8000`. A phone cannot open `localhost` or `127.0.0.1`.

To override detection, set the laptop's address on the pocket router:

```toml
public_host = "192.168.4.2"
```

Use the address this laptop actually has. The number above is only an example.

## 4. Firewall

Other devices need inbound TCP **8000**. Open **8443** only while you are testing the phone recorder.

Fedora:

```bash
sudo firewall-cmd --add-port=8000/tcp
```

Ubuntu, if ufw is on:

```bash
sudo ufw allow 8000/tcp
```

Windows: allow Python through the firewall when Windows asks, or allow inbound TCP 8000 for the private network.

## 5. Run it

Hub:

```bash
bash scripts/run_hub.sh
```

Windows: `hub\.venv\Scripts\python -m kinig_hub.main`

The log line `Notes and QR codes use http://...` is the address to put in a phone browser or in the ESP32 config.

Fake pod (no ESP32), from a second terminal, with the venv active:

```bash
python scripts/fake_pod.py --file path/to/meeting.wav
python scripts/fake_pod.py --file path/to/meeting.wav --realtime
python scripts/fake_pod.py --mic
```

`--mic` reads the laptop microphone. It is in the tree and has not been tried on hardware in this repo's test log. It needs PortAudio. Press Ctrl+C to stop.

To make a WAV the fake pod can send without resampling:

```bash
ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 meeting.wav
```

### Dev page on this laptop

In `hub/config/hub.toml` set `dev_mode = true`, restart the hub, and open `http://127.0.0.1:8000/dev`.

Start uses the microphone. If you pick a file first, that file is uploaded instead. The chunk length on the banner is the `chunk_ms` from the hub, not a number baked into the page.

Change the next recording's chunk size in `hub/config/pod.toml`, or:

```bash
curl -X PUT http://127.0.0.1:8000/config -H 'Content-Type: application/json' -d '{"pod":{"chunk_ms":500}}'
```

The next Start uses 500 ms chunks. Turn `dev_mode` back to `false` before a demo. `bash scripts/demo_check.sh` fails if it is still on.

### Phone recorder

Phones need HTTPS for the microphone. Notes stay on HTTP.

```bash
bash scripts/make_certs.sh
```

Set `dev_mode = true` and `dev_https = true`, restart the hub, install the mkcert CA on the phone (the script prints the steps), and open `https://<laptop-ip>:8443/dev`. The session is tagged `browser-phone` when the phone is not the laptop itself.

## 6. Stronger laptop

Edit `hub/config/models.toml`:

```toml
# under [transcriber.options]
model = "small"

# under [summarizer.options]
model = "llama3.2:3b"
```

Run `python scripts/download_models.py` again, then `ollama pull llama3.2:3b`, and restart the hub. `PUT /config` can change the same keys; the worker loads the new choice between jobs. A Whisper size that was never downloaded still has to be fetched by `download_models.py` first.

## 7. Tests

```bash
hub/.venv/bin/python -m pytest -q hub/tests
```

Windows: `hub\.venv\Scripts\python -m pytest -q hub\tests`

## 8. Firmware

`firmware/` is the ESP32 sketch. Pins in `firmware/include/config.h` are `-1` until the board is wired. See `hardware/wiring.md`.

```bash
cd firmware
pio run
```

That compile does not mean the pod has been tried on a board.

## 9. What is stored

See `docs/privacy.md` and `docs/audio-format.md`. Audio is dropped after transcription. The database keeps text. `debug_save_audio` stays false.
