# Privacy

Kinig keeps the words from a meeting and deletes the audio.

- While a session is recording, PCM lives in memory in that session's buffer.
- When the transcript is saved, the buffer is cleared. The audio is not written to the database.
- SQLite (`hub/kinig.db`) stores the session id, token, client, status, transcript, summary, key points, and action items.
- `debug_save_audio` in `hub/config/pod.toml` is false. Turning it on writes a WAV under `hub/debug_audio/` for debugging. Leave it off for a demo.
- The notes page needs the token that was returned by `POST /sessions`. The token is in the QR code. A request with a missing or wrong token is rejected.
- The hub does not call a cloud transcription or summary API. Whisper runs on the laptop. The summary model is a local Ollama process, or the rule-based fallback if Ollama does not answer.
- Model weights are downloaded once, during setup, by `scripts/download_models.py`. The running hub loads them from `hub/model_files/` and does not download again.
