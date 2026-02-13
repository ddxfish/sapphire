// settings-tabs/wakeword.js - Wake word detection settings
export default {
    id: 'wakeword',
    name: 'Wakeword',
    icon: '\uD83C\uDFB5',
    description: 'Wake word detection model and threshold',
    essentialKeys: ['WAKE_WORD_ENABLED', 'WAKEWORD_MODEL', 'WAKEWORD_THRESHOLD'],
    advancedKeys: ['WAKEWORD_FRAMEWORK', 'CHUNK_SIZE', 'BUFFER_DURATION', 'WAKE_TONE_DURATION', 'WAKE_TONE_FREQUENCY'],

    render(ctx) {
        return ctx.renderFields(this.essentialKeys) +
               ctx.renderAccordion('wakeword-adv', this.advancedKeys);
    }
};
