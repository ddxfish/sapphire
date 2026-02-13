// settings-tabs/tools.js - Function calling settings
export default {
    id: 'tools',
    name: 'Tools',
    icon: '\uD83D\uDD27',
    description: 'Function calling and tool settings',
    essentialKeys: ['FUNCTIONS_ENABLED', 'MAX_TOOL_ITERATIONS', 'MAX_PARALLEL_TOOLS'],
    advancedKeys: ['DEBUG_TOOL_CALLING'],

    render(ctx) {
        return ctx.renderFields(this.essentialKeys) +
               ctx.renderAccordion('tools-adv', this.advancedKeys);
    }
};
