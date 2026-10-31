// handlers/command-menu.js - the composer's list of slash commands: /compa, then
// Tab or Enter fills it in. Enter on a name already whole falls through and runs it.
import * as api from '../api.js';
import { getBoundChat } from '../core/bound-chat.js';

let _commands = null;   // read when a line starts with /, so it's never a stale list
let _shown = [];
let _pick = 0;
let _menu = null;

const hide = () => {
    _shown = [];
    if (_menu) _menu.style.display = 'none';
};

function fill(input, cmd) {
    input.value = `/${cmd.name}${cmd.args ? ' ' : ''}`;
    hide();
    input.focus();
    input.dispatchEvent(new Event('input'));
}

function paint(input) {
    if (!_menu) {
        _menu = document.createElement('div');
        _menu.id = 'command-menu';
        // mousedown, not click: the box must not lose focus first
        _menu.addEventListener('mousedown', e => {
            const row = e.target.closest('[data-i]');
            if (!row) return;
            e.preventDefault();
            fill(input, _shown[+row.dataset.i]);
        });
        input.closest('.textarea-wrapper').appendChild(_menu);
    }
    _menu.replaceChildren(..._shown.map((c, i) => {
        const row = document.createElement('div');
        row.className = 'command-menu-row' + (i === _pick ? ' picked' : '');
        row.dataset.i = i;
        const name = document.createElement('b');
        name.textContent = `/${c.name}${c.args ? ' ' + c.args : ''}`;
        const what = document.createElement('span');
        what.textContent = c.description || '';
        row.append(name, what);
        return row;
    }));
    _menu.style.display = '';
}

// Every input event. A chat room's composer has its own Enter and no commands yet.
export function onCommandInput(input) {
    const m = /^\/([\w-]*)$/.exec(input.value);
    if (!m || getBoundChat()) {
        if (!input.value.startsWith('/')) _commands = null;
        hide();
        return;
    }
    if (!_commands) {
        _commands = [];
        api.listCommands().then(r => {
            _commands = r.commands || [];
            onCommandInput(input);
        }).catch(() => { _commands = null; });   // typed commands still run
        return;
    }
    const prefix = m[1].toLowerCase();
    _shown = _commands.filter(c => c.name.startsWith(prefix));
    // the one whole name left: nothing to fill, Enter runs it
    if (_shown.length === 1 && _shown[0].name === prefix) _shown = [];
    _pick = 0;
    if (_shown.length) paint(input); else hide();
}

// True when the menu used the key.
export function onCommandKey(e) {
    if (!_shown.length || e.isComposing) return false;
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (step) {
        _pick = (_pick + step + _shown.length) % _shown.length;
        paint(e.target);
    } else if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        fill(e.target, _shown[_pick]);
    } else if (e.key === 'Escape') {
        hide();
    } else {
        return false;
    }
    e.preventDefault();
    return true;
}
