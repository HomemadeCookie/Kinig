# Roadmap and differentiation

Recorded 2026-10-29, after the first working pod and hub run.

## Next

### Test accuracy on short paragraphs

- Record a small set of short paragraphs (English, Tagalog, Taglish), each with a typed reference transcript. Put them under `hub/tests/sample_audio/` (gitignored audio) with the references next to them.
- Score word error rate per model with `scripts/bench_models.py`. Compare Whisper `base` and `small`, and `language = "tl"` against `"auto"`.
- Record the pod's own mic too, not only clean files. The INMP441 in the enclosure is the real input.

### Try a Taglish model

- Look for Whisper fine-tunes on Filipino and Taglish speech. `docs/architecture.md` describes the Hugging Face conversion path. The transcriber is chosen in `models.toml`, so a new model is a config change plus a download.
- Benchmark it against `base` and `small` on the same paragraphs before switching the default.

### Open issues from the 2026-10-29 run

- Phones on the pod's Wi-Fi may send requests over mobile data because Kinig-Pod has no internet. Tell people to tap "Stay connected" or turn off mobile data. A captive-portal style redirect on the pod could fix this properly.
- The Windows scripts (`scripts/setup.bat`, `run_hub.bat`) have not been run on Windows yet.
- The full end-to-end run on the real board, from pairing through notes on a phone, is not yet confirmed.

## Differentiation (for the patent discussion)

- **Modular architecture.** The pod captures audio and the hub runs the models. Transcriber and summarizer are adapters chosen in `models.toml`. Any part can be swapped without changing the rest.
- **Models as hardware.** The model is a component you install, like a part. A model pack is loaded onto the hub, and a classroom can get a better model without new pods.
- **The model itself (later).** A localized model trained for Filipino classrooms and Taglish.
- **Localized and air-gapped.** No cloud calls. Audio and transcripts never leave the room. The pod runs its own Wi-Fi with no internet connection.
- **Modularity and repairability.** Off-the-shelf ESP32, INMP441, and SSD1306 parts in a 3D-printed pod. Any part can be replaced, and the firmware is one open sketch.
