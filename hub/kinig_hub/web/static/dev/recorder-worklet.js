// Runs off the main thread. Posts microphone floats; the page packs chunks.
class KinigRecorder extends AudioWorkletProcessor {
  process(inputs) {
    var channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      this.port.postMessage(channel);
    }
    return true;
  }
}

registerProcessor("kinig-recorder", KinigRecorder);
