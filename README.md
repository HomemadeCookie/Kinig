# Kinig

Kinig is an offline meeting pod. A small recorder sends audio to a laptop on the same Wi-Fi. The laptop transcribes it, writes notes, deletes the audio, and shows the notes behind a QR code.

## Demo flow

1. Start the hub on the laptop (`bash scripts/run_hub.sh`) and open `http://127.0.0.1:8000/host`.
2. Power the pod. It joins Wi-Fi, finds the hub by mDNS, and shows a 4-digit code.
3. On `/host`, check the code matches and press **Approve pod**.
4. Press **Start recording**. The pod streams audio and shows a join QR.
5. People scan the QR, type a name, and wait. Approve them on `/host`.
6. Press **Stop recording**. Approved phones open the notes when they are ready.
7. Press **Done, back to ready** for the next meeting.

No ESP32 at hand? `python scripts/fake_esp32.py --file meeting.wav` (or `--mic`) runs the same steps from a terminal.

To see the web UI with no hardware and no models: `python scripts/mock_demo.py` (fake pods, fake viewers, canned notes, port 8080). Add `--auto` to watch the whole flow play by itself on a loop.

Pairing and approval show the flow. They are not security: there is no key exchange, and everything is plain HTTP on the local Wi-Fi.

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

Other devices need inbound TCP **8000**, and UDP **5353** so the pod can find the hub by mDNS. Open **8443** only while you are testing the phone recorder.

Fedora:

```bash
sudo firewall-cmd --add-port=8000/tcp --add-service=mdns
```

Ubuntu, if ufw is on:

```bash
sudo ufw allow 8000/tcp
```

Windows: allow Python through the firewall when Windows asks, or allow inbound TCP 8000 for the private network. If the pod still says "No hub", Windows is blocking mDNS: set `KINIG_HUB_HOST` in `firmware/kinig_pod/config.h` to the IP the hub prints.

## 5. Run it

Hub:

```bash
bash scripts/run_hub.sh
```

Windows: `hub\.venv\Scripts\python -m kinig_hub.main`

The log line `Notes and QR codes use http://...` is the address phones use. `Host console: http://127.0.0.1:8000/host` is where Start, Stop, pairing, and approvals live. `/` redirects there.

Fake ESP32 (no board), from a second terminal:

```bash
python scripts/fake_esp32.py --file path/to/meeting.wav
python scripts/fake_esp32.py --mic
```

It pairs, waits for Start on `/host`, streams, and prints the join QR, exactly like the pod.

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

`firmware/kinig_pod/kinig_pod.ino` is the pod: ESP32 Dev Module, INMP441 mic, SSD1306 128x64 OLED. Wiring is in `hardware/wiring.md`. Create `firmware/kinig_pod/secrets.h` (gitignored) with `#define KINIG_WIFI_SSID "..."` and `#define KINIG_WIFI_PASSWORD "..."`. The hub address is found by mDNS.

One command (Linux), with the pod plugged in by USB:

```bash
bash scripts/flash_pod.sh
```

It installs PlatformIO if needed, asks for the Wi-Fi (or copies a saved one with `--from-nm "Name"`), finds the port, and flashes. `--monitor` opens the serial log after.

Arduino IDE:

1. Boards Manager: install **esp32 by Espressif Systems**, version 3.x.
2. Library Manager: install **Adafruit SSD1306** (it pulls in Adafruit GFX).
3. Open `firmware/kinig_pod/kinig_pod.ino`, pick **ESP32 Dev Module** and the port, Upload.
4. Serial Monitor at 115200 shows each step.

PlatformIO, same source:

```bash
cd firmware
pio run -t upload
pio device monitor
```

The pod compiles with Arduino-ESP32 3.3 (pioarduino 55.03.312-1). The I2S and OLED setup is the one from the tested `KinigMicOLED_Fixed.ino`. The full pod has not yet run on the board end to end.

## 9. What is stored

See `docs/privacy.md` and `docs/audio-format.md`. Audio is dropped after transcription. The database keeps text. `debug_save_audio` stays false.
