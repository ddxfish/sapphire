// settings-tabs/stt.js - Speech-to-text / Whisper settings
export default {
    id: 'stt',
    name: 'STT',
    icon: '\uD83C\uDFA4',
    description: 'Speech-to-text engine and voice detection',
    essentialKeys: ['STT_ENABLED', 'STT_MODEL_SIZE', 'RECORDER_BACKGROUND_PERCENTILE', 'RECORDER_SILENCE_DURATION', 'RECORDER_MAX_SECONDS'],
    advancedKeys: [
        'STT_HOST', 'STT_SERVER_PORT',
        'FASTER_WHISPER_DEVICE', 'FASTER_WHISPER_CUDA_DEVICE', 'FASTER_WHISPER_COMPUTE_TYPE',
        'FASTER_WHISPER_BEAM_SIZE', 'FASTER_WHISPER_NUM_WORKERS', 'FASTER_WHISPER_VAD_FILTER',
        'RECORDER_SILENCE_THRESHOLD', 'RECORDER_SPEECH_DURATION', 'RECORDER_BEEP_WAIT_TIME'
    ],

    render(ctx) {
        return ctx.renderFields(this.essentialKeys) +
               ctx.renderAccordion('stt-adv', this.advancedKeys);
    }
};
