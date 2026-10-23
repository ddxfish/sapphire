// AudioWorklet processor for browser conversation mode (v3).
// Batches mic samples off the audio thread and posts Float32Array blocks to
// the main thread, which downsamples to 16k int16 and ships them over the WS.
// Outputs stay silent (zeroed) — we connect to destination only so the graph
// keeps pulling; echo cancellation happens in getUserMedia, not here.
const BATCH_SAMPLES = 2048; // ~43ms @48k per post — small enough for snappy VAD

class ConvCaptureProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._bufs = [];
        this._len = 0;
    }
    process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (ch && ch.length) {
            this._bufs.push(new Float32Array(ch)); // copy — the input buffer is reused
            this._len += ch.length;
            if (this._len >= BATCH_SAMPLES) {
                const out = new Float32Array(this._len);
                let o = 0;
                for (const b of this._bufs) { out.set(b, o); o += b.length; }
                this.port.postMessage(out, [out.buffer]);
                this._bufs = [];
                this._len = 0;
            }
        }
        return true;
    }
}

registerProcessor('conv-capture', ConvCaptureProcessor);
