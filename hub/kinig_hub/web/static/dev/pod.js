// Dev pod in the browser. Chunk size is the chunk_ms from POST /sessions.
(function () {
  var session = null;
  var seq = 0;
  var audioContext = null;
  var worklet = null;
  var stream = null;
  var chunker = null;
  var startedAt = 0;
  var timerId = 0;
  var statusId = 0;
  var recording = false;

  var startBtn = document.getElementById("start");
  var stopBtn = document.getElementById("stop");
  var fileInput = document.getElementById("file");
  var liveBox = document.getElementById("live");

  startBtn.addEventListener("click", onStart);
  stopBtn.addEventListener("click", onStop);

  function modeName() {
    return liveBox.checked ? "live" : "end";
  }

  function setState(name) {
    document.getElementById("state").textContent = name;
  }

  function banner() {
    var who = session && session.client === "browser-phone" ? "Phone" : "Laptop";
    var transport = location.protocol === "https:" ? "HTTPS" : "HTTP";
    var chunk = session ? session.chunk_ms : "—";
    document.getElementById("banner").textContent = who + " · " + transport + " · chunk " + chunk + " ms";
  }

  async function onStart() {
    startBtn.disabled = true;
    if (fileInput.files && fileInput.files[0]) {
      await uploadFile(fileInput.files[0]);
      return;
    }
    session = await createSession();
    banner();
    if (session.mode === "live") showQr();
    await startMic();
    recording = true;
    stopBtn.disabled = false;
    setState("recording");
    startedAt = Date.now();
    timerId = setInterval(tick, 500);
    statusId = setInterval(refreshStatus, 1000);
  }

  async function onStop() {
    stopBtn.disabled = true;
    recording = false;
    if (chunker) chunker.flush();
    await stopMic();
    clearInterval(timerId);
    if (!session) return;
    setState("processing");
    await fetch("/sessions/" + session.session_id + "/stop", { method: "POST" });
    showQr();
    await waitUntilDone();
    clearInterval(statusId);
    startBtn.disabled = false;
  }

  async function createSession() {
    var response = await fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client: "browser", mode: modeName() }),
    });
    if (!response.ok) throw new Error("could not start session");
    return response.json();
  }

  async function startMic() {
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
    var source = audioContext.createMediaStreamSource(stream);
    source.connect(worklet);
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

  function showQr() {
    var img = document.getElementById("qr");
    var link = document.getElementById("notes-link");
    img.src = "/dev/qr/" + session.session_id + ".svg?t=" + encodeURIComponent(session.token);
    img.hidden = false;
    link.href = session.notes_url;
    link.hidden = false;
  }

  async function refreshStatus() {
    if (!session) return;
    var response = await fetch(
      "/dev/status/" + session.session_id + "?t=" + encodeURIComponent(session.token)
    );
    if (!response.ok) return;
    var data = await response.json();
    document.getElementById("chunks").textContent = String(data.chunks);
    document.getElementById("gaps").textContent = data.gaps && data.gaps.length ? data.gaps.join(", ") : "none";
    document.getElementById("lag").textContent = data.lag_seconds == null ? "—" : data.lag_seconds.toFixed(2) + " s";
    document.getElementById("transcriber").textContent = data.transcriber || "—";
    document.getElementById("summarizer").textContent = data.summarizer || "—";
    if (data.status) setState(data.status);
  }

  async function waitUntilDone() {
    for (var i = 0; i < 120; i++) {
      await refreshStatus();
      var state = document.getElementById("state").textContent;
      if (state === "done" || state === "error") return;
      await new Promise(function (resolve) { setTimeout(resolve, 1000); });
    }
  }

  async function uploadFile(file) {
    setState("processing");
    var body = new FormData();
    body.append("file", file);
    body.append("mode", modeName());
    var response = await fetch("/dev/upload", { method: "POST", body: body });
    if (!response.ok) {
      setState("error");
      startBtn.disabled = false;
      return;
    }
    session = await response.json();
    banner();
    showQr();
    statusId = setInterval(refreshStatus, 1000);
    await waitUntilDone();
    clearInterval(statusId);
    startBtn.disabled = false;
  }

  function tick() {
    var seconds = Math.floor((Date.now() - startedAt) / 1000);
    var mm = String(Math.floor(seconds / 60)).padStart(2, "0");
    var ss = String(seconds % 60).padStart(2, "0");
    document.getElementById("timer").textContent = mm + ":" + ss;
  }

  banner();
})();
