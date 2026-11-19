// One-screen demo. Start: laptop mic -> live session, transcript polls in.
// Stop: the hub finishes the transcript and summarizes, then action items show.
// Chunk size is the chunk_ms from POST /sessions, as with the ESP32 pod.
(function () {
  var session = null;
  var seq = 0;
  var audioContext = null;
  var worklet = null;
  var stream = null;
  var chunker = null;
  var recording = false;
  var startedAt = 0;
  var timerId = 0;
  var pollId = 0;

  var startBtn = document.getElementById("start");
  var stopBtn = document.getElementById("stop");
  var stateEl = document.getElementById("state");
  var timerEl = document.getElementById("timer");
  var transcriptEl = document.getElementById("transcript");
  var actionsEl = document.getElementById("actions");
  var errorEl = document.getElementById("error");

  startBtn.addEventListener("click", function () { onStart().catch(fail); });
  stopBtn.addEventListener("click", function () { onStop().catch(fail); });

  var LABELS = {
    recording: "Listening",
    transcribing: "Finishing transcript",
    summarizing: "Writing action items",
    done: "Done",
    error: "Error",
  };

  function setState(name) {
    stateEl.textContent = LABELS[name] || name;
  }

  function fail(err) {
    errorEl.textContent = String(err && err.message ? err.message : err);
    recording = false;
    clearInterval(timerId);
    clearInterval(pollId);
    stopMic();
    setState("error");
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }

  async function onStart() {
    startBtn.disabled = true;
    errorEl.textContent = "";
    transcriptEl.textContent = "";
    showActions(null);
    seq = 0;
    var response = await fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client: "browser", mode: "live" }),
    });
    if (!response.ok) throw new Error("Could not start a session (" + response.status + ")");
    session = await response.json();
    await startMic();
    recording = true;
    stopBtn.disabled = false;
    setState("recording");
    startedAt = Date.now();
    tick();
    timerId = setInterval(tick, 500);
    pollId = setInterval(poll, 1000);
  }

  async function onStop() {
    stopBtn.disabled = true;
    recording = false;
    if (chunker) chunker.flush();
    await stopMic();
    clearInterval(timerId);
    setState("transcribing");
    var response = await fetch("/sessions/" + session.session_id + "/stop", { method: "POST" });
    if (!response.ok) throw new Error("Stop failed (" + response.status + ")");
    // poll keeps running until the hub reports done or error.
  }

  async function startMic() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("No mic access. Open this page as http://localhost:8000/demo on the hub laptop.");
    }
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext();
    await audioContext.audioWorklet.addModule("/static/dev/recorder-worklet.js");
    worklet = new AudioWorkletNode(audioContext, "kinig-recorder");
    chunker = new window.KinigPcm.Chunker(session.chunk_ms, sendChunk);
    var rate = audioContext.sampleRate;
    worklet.port.onmessage = function (event) {
      if (!recording) return;
      chunker.push(event.data, rate);
    };
    audioContext.createMediaStreamSource(stream).connect(worklet);
  }

  async function stopMic() {
    if (worklet) worklet.disconnect();
    if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
    if (audioContext) await audioContext.close();
    worklet = null;
    stream = null;
    audioContext = null;
  }

  function sendChunk(buffer) {
    var current = seq;
    seq += 1;
    fetch("/sessions/" + session.session_id + "/audio?seq=" + current, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: buffer,
    });
  }

  async function poll() {
    if (!session) return;
    var response = await fetch(
      "/s/" + session.session_id + "/transcript?t=" + encodeURIComponent(session.token)
    );
    if (!response.ok) return;
    var data = await response.json();
    if (data.transcript) {
      var atBottom = transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 40;
      transcriptEl.textContent = data.transcript;
      if (atBottom) transcriptEl.scrollTop = transcriptEl.scrollHeight;
    }
    if (!recording) setState(data.status);
    if (data.status === "done" || data.status === "error") {
      clearInterval(pollId);
      if (data.error) errorEl.textContent = data.error;
      if (!data.transcript) transcriptEl.textContent = "(Nothing was heard.)";
      showActions(data.action_items || []);
      startBtn.disabled = false;
    }
  }

  function showActions(items) {
    actionsEl.textContent = "";
    var li;
    if (items === null) {
      li = document.createElement("li");
      li.className = "hint";
      li.textContent = "Appear here after you press Stop.";
      actionsEl.appendChild(li);
      return;
    }
    if (!items.length) {
      li = document.createElement("li");
      li.className = "hint";
      li.textContent = "No action items found.";
      actionsEl.appendChild(li);
      return;
    }
    items.forEach(function (item) {
      li = document.createElement("li");
      li.textContent = String(item);
      actionsEl.appendChild(li);
    });
  }

  function tick() {
    var seconds = Math.floor((Date.now() - startedAt) / 1000);
    var mm = String(Math.floor(seconds / 60)).padStart(2, "0");
    var ss = String(seconds % 60).padStart(2, "0");
    timerEl.textContent = mm + ":" + ss;
  }
})();
