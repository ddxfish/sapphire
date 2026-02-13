// settings-tabs/tts.js - Text-to-speech settings
export default {
    id: 'tts',
    name: 'TTS',
    icon: '\uD83D\uDD0A',
    description: 'Text-to-speech configuration',
    essentialKeys: ['TTS_ENABLED'],
    advancedKeys: ['TTS_SERVER_HOST', 'TTS_SERVER_PORT', 'TTS_PRIMARY_SERVER', 'TTS_FALLBACK_SERVER', 'TTS_FALLBACK_TIMEOUT'],

    render(ctx) {
        return ctx.renderFields(this.essentialKeys) +
               ctx.renderAccordion('tts-adv', this.advancedKeys);
    }
};
