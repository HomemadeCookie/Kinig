// Turn microphone floats into the hub's PCM chunks.
// chunk length comes from the session response. This file does not choose it.
(function () {
  var OUTPUT_RATE = 16000;

  function downsample(input, inputRate) {
    if (inputRate === OUTPUT_RATE) return input;
    var ratio = inputRate / OUTPUT_RATE;
    var outLen = Math.floor(input.length / ratio);
    var out = new Float32Array(outLen);
    for (var i = 0; i < outLen; i++) {
      var start = Math.floor(i * ratio);
      var end = Math.min(input.length, Math.floor((i + 1) * ratio));
      var sum = 0;
      var count = end - start;
      for (var j = start; j < end; j++) sum += input[j];
      out[i] = count ? sum / count : 0;
    }
    return out;
  }

  function floatToInt16(samples) {
    var pcm = new Int16Array(samples.length);
    for (var i = 0; i < samples.length; i++) {
      var s = samples[i];
      if (s > 1) s = 1;
      if (s < -1) s = -1;
      pcm[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
    }
    return pcm;
  }

  function Chunker(chunkMs, onChunk) {
    this.samplesPerChunk = Math.round((chunkMs * OUTPUT_RATE) / 1000);
    this.onChunk = onChunk;
    this.pending = new Int16Array(0);
  }

  Chunker.prototype.push = function (floatSamples, inputRate) {
    var next = floatToInt16(downsample(floatSamples, inputRate));
    var merged = new Int16Array(this.pending.length + next.length);
    merged.set(this.pending, 0);
    merged.set(next, this.pending.length);
    var offset = 0;
    while (merged.length - offset >= this.samplesPerChunk) {
      var slice = merged.slice(offset, offset + this.samplesPerChunk);
      this.onChunk(slice.buffer);
      offset += this.samplesPerChunk;
    }
    this.pending = merged.slice(offset);
  };

  Chunker.prototype.flush = function () {
    if (!this.pending.length) return;
    var padded = new Int16Array(this.samplesPerChunk);
    padded.set(this.pending);
    this.onChunk(padded.buffer);
    this.pending = new Int16Array(0);
  };

  window.KinigPcm = { Chunker: Chunker, OUTPUT_RATE: OUTPUT_RATE };
})();
