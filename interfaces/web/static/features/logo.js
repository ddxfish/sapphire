// features/logo.js - Sapphire gem logo tinting (navrail + favicon)
//
// The gem is monochrome: six facets sharing one hue at six lightness steps.
// Tinting = take the tint color's hue + saturation, keep each facet's
// lightness — the faceted depth survives in any hue.
//
// Effective color = chat/persona trim (via applyTrimColor hook) falling back
// to the instance-level ICON_COLOR setting, falling back to stock blues.
// Geometry here mirrors static/logo.svg and the inline navrail gem in
// index.html — keep all three in sync if the mark ever changes.

const VIEWBOX = '793.73 373.73 332.54 332.54';

const FACETS = [
    { hex: '#3289d9', l: 52.4, d: 'M960,373.73a28.75,28.75,0,0,0-14.39,3.85L826.54,446.33a28.68,28.68,0,0,0-10.49,10.46L960,539.9Z' },
    { hex: '#036ec3', l: 38.8, d: 'M816.05,456.79a28.67,28.67,0,0,0-3.9,14.47V608.74A28.71,28.71,0,0,0,816,623.06L960,539.9Z' },
    { hex: '#046dc0', l: 38.4, d: 'M1107.85,471.26a28.67,28.67,0,0,0-3.9-14.47L960,539.9l144,83.16a28.71,28.71,0,0,0,3.81-14.32Z' },
    { hex: '#62a2e5', l: 64.1, d: 'M1104,456.79a28.68,28.68,0,0,0-10.49-10.46L974.39,377.58A28.75,28.75,0,0,0,960,373.73V539.9Z' },
    { hex: '#004594', l: 29.0, d: 'M960,706.27a28.75,28.75,0,0,0,14.39-3.85l119.07-68.75A28.72,28.72,0,0,0,1104,623.06L960,539.9Z' },
    { hex: '#015cb5', l: 35.7, d: 'M816,623.06a28.72,28.72,0,0,0,10.58,10.61l119.07,68.75A28.75,28.75,0,0,0,960,706.27V539.9Z' },
];

let _instanceColor = '';

function _valid(c) {
    return typeof c === 'string' && /^#[0-9a-f]{6}$/i.test(c) ? c : '';
}

function _hexToHueSat(hex) {
    const r = parseInt(hex.slice(1, 3), 16) / 255;
    const g = parseInt(hex.slice(3, 5), 16) / 255;
    const b = parseInt(hex.slice(5, 7), 16) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    const l = (max + min) / 2;
    if (d === 0) return [0, 0];
    const s = d / (1 - Math.abs(2 * l - 1));
    let h;
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    return [Math.round(((h * 60) + 360) % 360), Math.round(s * 100)];
}

function _facetFills(color) {
    if (!color) return FACETS.map(f => f.hex);
    const [h, s] = _hexToHueSat(color);
    return FACETS.map(f => `hsl(${h}, ${s}%, ${f.l}%)`);
}

export function gemSvg(color) {
    const fills = _facetFills(_valid(color));
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${VIEWBOX}">` +
        FACETS.map((f, i) => `<path fill="${fills[i]}" d="${f.d}"/>`).join('') +
        '</svg>';
}

export function setInstanceColor(color) {
    _instanceColor = _valid(color);
}

// Repaint navrail gem + favicon. Called from applyTrimColor with the active
// trim ('' when none) — falls back to the instance color, then stock blues.
export function applyLogoTint(trimColor) {
    const color = _valid(trimColor) || _instanceColor;

    const nav = document.querySelector('.nav-logo svg');
    if (nav) {
        const fills = _facetFills(color);
        nav.querySelectorAll('path').forEach((p, i) => p.setAttribute('fill', fills[i]));
    }

    const link = document.getElementById('favicon-svg');
    if (link) link.href = 'data:image/svg+xml,' + encodeURIComponent(gemSvg(color));
}
