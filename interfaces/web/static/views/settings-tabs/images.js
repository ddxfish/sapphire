// settings-tabs/images.js - Images: what she sees and for how long (image upgrade,
// 2026-09-10). Both keys are core (web image search lives in functions/, the
// vision window in history) — plugin-owned image knobs (sd-server etc.) stay in
// their plugin tabs. IMAGE_UPLOAD_MAX_WIDTH moved here from the LLM tab; it
// still lives in the `llm` defaults section (sections are only grouping).
export default {
    id: 'images',
    name: 'Images',
    icon: '\u{1F5BC}\uFE0F',
    description: 'Image memory, web image search, and upload size',
    keys: ['IMAGE_MEMORY_TURNS', 'WEB_IMAGES_SAFESEARCH', 'IMAGE_UPLOAD_MAX_WIDTH'],

    render(ctx) {
        return `
            <div class="settings-group">
                <h3>\u{1F5BC}\uFE0F Images</h3>
                <p class="text-muted" style="font-size:var(--font-sm);line-height:1.5">
                    Every image in a chat stays visible to you forever. The model sees an
                    image the turn it arrives, and again for the next few turns (below).
                    Anything older she can re-view by its handle (<code>img:&hellip;</code>).
                </p>
            </div>
            ${ctx.renderFields(this.keys)}
        `;
    },
};
