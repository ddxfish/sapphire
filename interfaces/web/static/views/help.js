/**
 * Help Encyclopedia — in-app docs browser with search.
 *
 * Deep-linkable: #help/AGENTS or #help/integrations/DISCORD
 * Other views can link here via: window._viewSelect = 'path/to/DOC'; switchView('help');
 */

let container = null;
let tree = null;
let activePath = null;
let searchTimeout = null;

/* ── marked.js-lite: minimal markdown → HTML ────────────────────────── */

function md(src) {
    if (!src) return '';
    // Extract code blocks first so later passes don't mangle them
    const codeBlocks = [];
    let html = src.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const id = `\x00CB${codeBlocks.length}\x00`;
        codeBlocks.push(`<pre class="help-code"><code>${esc(code.trimEnd())}</code></pre>`);
        return id;
    });
    html = html
        // inline code
        .replace(/`([^`]+)`/g, '<code class="help-inline-code">$1</code>')
        // tables
        .replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)*)/gm, (_, hdr, _align, body) => {
            const headers = hdr.split('|').filter(c => c.trim()).map(c => `<th>${c.trim()}</th>`).join('');
            const rows = body.trim().split('\n').map(row => {
                const cells = row.split('|').filter(c => c.trim()).map(c => `<td>${c.trim()}</td>`).join('');
                return `<tr>${cells}</tr>`;
            }).join('');
            return `<table class="help-table"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
        })
        // headings
        .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        // hr
        .replace(/^---+$/gm, '<hr>')
        // bold + italic
        .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // images (before links)
        .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img alt="$1" src="$2" class="help-img">')
        // links
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, href) => {
            if (href.endsWith('.md') && !href.startsWith('http')) {
                return `<a href="#" class="help-doc-link" data-doc="${href}">${text}</a>`;
            }
            return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
        })
        // unordered lists
        .replace(/^(\s*)[-*] (.+)$/gm, '$1<li>$2</li>')
        // ordered lists
        .replace(/^(\s*)\d+\. (.+)$/gm, '$1<li>$2</li>')
        // blockquote
        .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
        // paragraphs — wrap remaining loose lines
        .replace(/^(?!<[huplitbod]|<hr|<pre|<blockquote)(.+)$/gm, '<p>$1</p>');

    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>.*<\/li>\s*)+)/g, '<ul>$1</ul>');
    // Merge consecutive blockquotes
    html = html.replace(/<\/blockquote>\s*<blockquote>/g, '<br>');
    // Merge consecutive badge/link-only paragraphs into one line (e.g. shield.io badges)
    html = html.replace(/(<p>(?:<a [^>]*>(?:<img [^>]*>|[^<]*)<\/a>\s*)+<\/p>\s*){2,}/g, match =>
        '<p class="help-badges">' + match.replace(/<\/?p[^>]*>/g, ' ').trim() + '</p>');
    // Restore code blocks
    codeBlocks.forEach((block, i) => { html = html.replace(`\x00CB${i}\x00`, block); });
    return html;
}

function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

/* ── API ─────────────────────────────────────────────────────────────── */

async function fetchTree() {
    const r = await fetch('/api/docs', { credentials: 'same-origin' });
    return (await r.json()).tree;
}

async function fetchDoc(path) {
    const r = await fetch(`/api/docs/${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!r.ok) return null;
    return (await r.json()).content;
}

async function searchDocs(query) {
    const r = await fetch(`/api/docs/search?q=${encodeURIComponent(query)}`, { credentials: 'same-origin' });
    return (await r.json()).results;
}

/* ── Rendering ───────────────────────────────────────────────────────── */

function renderSidebar() {
    const sidebar = container.querySelector('.help-sidebar-items');
    if (!sidebar || !tree) return;

    // Separate folders and files, show folders first
    const folders = tree.filter(i => i.type === 'folder');
    const files = tree.filter(i => i.type === 'file');

    let html = '';
    for (const f of files) {
        const active = activePath === f.path ? ' active' : '';
        html += `<div class="help-nav-item${active}" data-path="${f.path}">${formatName(f.name)}</div>`;
    }
    for (const folder of folders) {
        const open = folder.children.some(c => c.path === activePath) ? ' open' : '';
        html += `<details class="help-nav-folder"${open ? ' open' : ''}>`;
        html += `<summary class="help-nav-folder-name">${formatName(folder.name)}</summary>`;
        for (const c of folder.children) {
            const active = activePath === c.path ? ' active' : '';
            html += `<div class="help-nav-item help-nav-child${active}" data-path="${c.path}">${formatName(c.name)}</div>`;
        }
        html += `</details>`;
    }
    sidebar.innerHTML = html;
}

function formatName(name) {
    return name.replace(/[-_]/g, ' ').replace(/\bmd\b/gi, '').replace(/README/i, 'Overview').trim();
}

function renderContent(markdown) {
    const content = container.querySelector('.help-content');
    if (!content) return;
    content.innerHTML = `<div class="help-article">${md(markdown)}</div>`;
    content.scrollTop = 0;
}

function renderSearchResults(results) {
    const content = container.querySelector('.help-content');
    if (!content) return;

    if (!results.length) {
        content.innerHTML = '<div class="help-article"><p class="help-empty">No results found.</p></div>';
        return;
    }

    let html = '<div class="help-article"><h2>Search Results</h2>';
    for (const r of results) {
        html += `<div class="help-search-result" data-path="${r.path}">`;
        html += `<h3 class="help-search-title">${esc(r.title)}</h3>`;
        html += `<span class="help-search-path">${r.path}</span>`;
        for (const m of r.matches) {
            html += `<div class="help-search-snippet"><pre>${esc(m.snippet)}</pre></div>`;
        }
        html += `</div>`;
    }
    html += '</div>';
    content.innerHTML = html;
}

function renderWelcome() {
    const content = container.querySelector('.help-content');
    if (!content) return;
    content.innerHTML = `<div class="help-article help-welcome">
        <h1>Sapphire Encyclopedia</h1>
        <p>Browse the docs on the left or search above.</p>
        <p class="help-hint">Other parts of the app can link here directly — look for <strong>?</strong> icons.</p>
    </div>`;
}

/* ── Navigation ──────────────────────────────────────────────────────── */

async function navigateTo(path) {
    if (!path) { renderWelcome(); return; }
    // Resolve relative links — strip docs/ prefix (README style) and ../ (cross-references)
    path = path.replace(/^docs\//, '').replace(/^\.\.\//, '');
    activePath = path;
    renderSidebar();
    const content = await fetchDoc(path);
    if (content) {
        renderContent(content);
    } else {
        container.querySelector('.help-content').innerHTML =
            '<div class="help-article"><p class="help-empty">Document not found.</p></div>';
    }
}

/* ── View Module ─────────────────────────────────────────────────────── */

export default {
    init(el) {
        container = el;
        container.innerHTML = `
        <div class="two-panel help-view">
            <div class="panel-left help-sidebar">
                <div class="help-search-box">
                    <input type="text" id="help-search" placeholder="Search docs..." autocomplete="off">
                </div>
                <div class="help-sidebar-items"></div>
            </div>
            <div class="panel-right help-content"></div>
        </div>`;

        // Sidebar nav clicks
        container.addEventListener('click', e => {
            const item = e.target.closest('.help-nav-item');
            if (item) {
                const path = item.dataset.path;
                navigateTo(path);
                // Update hash for deep-linking
                history.replaceState(null, '', `#help/${path.replace('.md', '')}`);
                return;
            }
            // Search result clicks
            const result = e.target.closest('.help-search-result');
            if (result) {
                navigateTo(result.dataset.path);
                return;
            }
            // Internal doc links (markdown cross-references)
            const docLink = e.target.closest('.help-doc-link');
            if (docLink) {
                e.preventDefault();
                navigateTo(docLink.dataset.doc);
                return;
            }
        });

        // Search
        const searchInput = container.querySelector('#help-search');
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            const q = searchInput.value.trim();
            if (q.length < 2) {
                activePath = null;
                renderSidebar();
                renderWelcome();
                return;
            }
            searchTimeout = setTimeout(async () => {
                const results = await searchDocs(q);
                activePath = null;
                renderSidebar();
                renderSearchResults(results);
            }, 250);
        });

        // Esc clears search
        searchInput.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                searchInput.value = '';
                searchInput.dispatchEvent(new Event('input'));
            }
        });
    },

    async show() {
        // Load tree if not cached
        if (!tree) tree = await fetchTree();
        renderSidebar();

        // Check for deep-link: #help/AGENTS or #help/integrations/DISCORD
        const hash = location.hash.replace(/^#help\/?/, '');
        // Check for _viewSelect (other views linking here)
        const target = window._viewSelect || (hash ? `${hash}.md` : null);
        if (window._viewSelect) delete window._viewSelect;

        if (target) {
            await navigateTo(target);
        } else if (!activePath) {
            await navigateTo('_root/README.md');
        }
    },

    hide() {}
};
