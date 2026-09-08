// Pure-function corpus for shared/checklist-modal.js — run under node by
// tests/test_checklist_modal_js.py. No DOM: covers the filter table, row /
// list markup, and the chip bar. The DOM-bound half (wireList, checkedRows,
// applyFilter) is the owner's click-list.
globalThis.document = {
    createElement() {
        let t = '';
        return {
            set textContent(v) { t = String(v); },
            get innerHTML() {
                return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            },
        };
    },
};
globalThis.CSS = { escape: s => s };

const m = await import('../../interfaces/web/static/shared/checklist-modal.js');
let n = 0;
const ok = (cond, msg) => { if (!cond) throw new Error('FAIL: ' + msg); n++; };

// ── FILTERS: the one table every modal reads ──
ok(m.FILTERS.all.pick({}) === true && m.FILTERS.none.pick({}) === false, 'all / none');
ok(m.FILTERS.main.pick({ type: 'character' }) === true, 'main keeps story pieces');
ok(m.FILTERS.main.pick({ type: 'extras' }) === false && m.FILTERS.main.pick({ type: 'emotions' }) === false,
   'main skips the generic types');
ok(m.FILTERS.custom.pick({}) === true, 'custom keeps an unmarked row');
ok(m.FILTERS.custom.pick({ stock: '1' }) === false, 'custom drops shipped pieces');
ok(m.FILTERS.custom.pick({ pack: '1' }) === false, 'custom drops plugin pieces');
ok(m.GENERIC_TYPES.includes('extras') && m.GENERIC_TYPES.includes('emotions'), 'generic types exported');

// ── rowHTML: every marker the filters and the harvest read ──
const r = m.rowHTML({
    type: 'character', key: 'he"ro', checked: true, disabled: true, stock: true, pack: true, flag: true,
    data: { kind: 'piece', store: 'vault' }, badges: ['first', '', 'second'], title: 'a, b',
});
ok(r.includes('class="pc-row"'), 'row class');
ok(r.includes('data-type="character"') && r.includes('data-key="he&quot;ro"'), 'type/key attrs, key escaped');
ok(/\schecked>/.test(r) && r.includes(' disabled'), 'checked + disabled attrs');
ok(r.includes('data-stock="1"') && r.includes('data-pack="1"') && r.includes('data-flag="1"'), 'stock/pack/flag markers');
ok(r.includes('data-kind="piece"') && r.includes('data-store="vault"'), 'extra data attrs from data:{}');
ok(r.includes('first · second') && !r.includes('·  ·'), 'empty badges dropped, rest joined');
ok(r.includes('title="a, b"'), 'title carried');
ok(r.includes('character/he&quot;ro'), 'default label is type/key, escaped');
const plain = m.rowHTML({ type: 't', key: 'k' });
ok(!plain.includes('checked') && !plain.includes('disabled') && !plain.includes('data-stock'), 'bare row has no markers');
ok(m.rowHTML({ type: 't', key: 'k', label: '<b>x</b>' }).includes('<b>x</b>'), 'label is html when given');

// ── filtersHTML: only known names, nothing when none ──
ok(m.filtersHTML(['all', 'bogus', 'custom']).split('class="pc-preset"').length === 3, 'unknown names dropped');
ok(m.filtersHTML(['custom']).includes('data-preset="custom"') && m.filtersHTML(['custom']).includes('>Custom<'), 'chip label');
ok(m.filtersHTML([]) === '' && m.filtersHTML(['bogus']) === '', 'no chips → no bar');

// ── listHTML: flat vs sections ──
const flat = m.listHTML({ sections: [{ id: 'p', flat: true, rows: [{ type: 't', key: 'k' }] }], filters: ['all'] });
ok(!flat.includes('pc-sec-head') && flat.includes('class="pc-row"'), 'flat: rows without a section header');
ok(flat.includes('data-preset="all"') && !flat.includes('data-preset="main"'), 'flat: the filters it asked for');
ok(flat.includes('max-height:45vh'), 'default max height');

const sec = m.listHTML({
    sections: [
        { id: 'prompt:a', name: 'a', open: true, label: '<b>a</b>', rows: [{ type: 't', key: '1' }, { type: 't', key: '2' }] },
        { id: 'b', rows: [{ type: 't', key: '3' }] },
    ],
    maxHeight: '40vh',
});
ok(sec.includes('data-type="prompt:a"') && sec.includes('data-type="b"'), 'section ids');
ok(sec.includes('(2)') && sec.includes('(1)'), 'section counts');
ok(sec.includes('▾') && sec.includes('▸'), 'open and closed arrows');
ok((sec.match(/display:none;/g) || []).length === 1, 'only the closed section is hidden');
ok(sec.includes('Check/uncheck everything in a'), 'header checkbox title uses the plain name');
ok(sec.includes('<b>a</b>') && sec.includes('<b>b</b>'), 'html label when given, name otherwise');
ok(sec.includes('max-height:40vh'), 'custom max height');
ok(sec.split('data-preset=').length === 4, 'default filter bar = all/none/main');

console.log(`${n} passed`);
