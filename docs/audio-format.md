# Audio format

Every pod and the hub use the same bytes.

| Field | Value |
|---|---|
| Sample rate | 16000 Hz |
| Channels | 1 (mono) |
| Sample type | signed 16-bit, little-endian |
| Header | none |
| One HTTP POST | one chunk |
| `seq` | starts at 0 |

A 1 second chunk is 32000 bytes. The chunk length is `chunk_ms` from `POST /sessions`, which is copied from `hub/config/pod.toml`. Pods do not choose that number themselves.

The hub converts those bytes to a float32 NumPy array in the range −1 to 1 before a transcriber sees them. Transcribers do not read files or raw bytes.

## WAV files

`scripts/fake_pod.py` and the dev page upload accept a WAV that already matches this contract. Anything else (m4a, mp3, a WAV at another rate) is resampled by `hub/kinig_hub/audio/decode.py`.

To make a contract WAV from a phone recording:

```bash
ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 taglish.wav
```

Put a benchmark file at `hub/tests/sample_audio/taglish.wav` when you have one. That path is gitignored as audio you record yourself; the folder is kept by `.gitkeep`.
