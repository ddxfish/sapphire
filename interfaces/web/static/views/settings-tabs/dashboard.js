// settings-tabs/dashboard.js - Dashboard with system controls, update checker, and token metrics
import * as ui from '../../ui.js';

let updateStatus = null;

export default {
    id: 'dashboard',
    name: 'Dashboard',
    icon: '\uD83C\uDFE0',
    description: 'System status, updates, and controls',

    render(ctx) {
        return `
            <div class="dashboard-grid">
                <div class="dash-card">
                    <h4>System</h4>
                    <div class="dash-version" id="dash-version">v${window.__appVersion || '?'} <span class="text-muted" id="dash-branch"></span></div>
                    <div class="dash-controls">
                        <button class="btn-primary btn-sm" id="dash-restart">Restart</button>
                        <button class="btn-sm danger" id="dash-shutdown">Shutdown</button>
                    </div>
                </div>
                <div class="dash-card">
                    <h4>Updates</h4>
                    <div class="dash-update-status" id="dash-update-status">
                        <span class="text-muted">Checking...</span>
                    </div>
                    <div class="dash-update-actions" id="dash-update-actions"></div>
                </div>
                <div class="dash-card dash-card-wide">
                    <div class="dash-card-header">
                        <h4>Token Metrics <span class="text-muted" style="font-size:var(--font-xs);font-weight:normal">(30 days)</span></h4>
                        <label class="metrics-toggle" id="metrics-toggle">
                            <input type="checkbox" id="metrics-enabled-cb">
                            <span class="toggle-track"></span>
                            <span class="toggle-label">Track</span>
                        </label>
                    </div>
                    <div id="dash-metrics" class="dash-metrics">
                        <span class="text-muted">Loading...</span>
                    </div>
                </div>
            </div>
        `;
    },

    attachListeners(ctx, el) {
        // Restart
        el.querySelector('#dash-restart')?.addEventListener('click', async () => {
            if (!confirm('Restart Sapphire?')) return;
            try {
                await fetch('/api/system/restart', { method: 'POST' });
                ui.showToast('Restarting...', 'success');
                setTimeout(() => pollForRestart(), 2000);
            } catch { ui.showToast('Restart failed', 'error'); }
        });

        // Shutdown
        el.querySelector('#dash-shutdown')?.addEventListener('click', async () => {
            if (!confirm('Shut down Sapphire? You will need to restart it manually.')) return;
            try {
                await fetch('/api/system/shutdown', { method: 'POST' });
                ui.showToast('Shutting down...', 'success');
            } catch { ui.showToast('Shutdown failed', 'error'); }
        });

        checkForUpdate(el);
        loadMetrics(el);
    }
};


// =============================================================================
// UPDATE CHECKER
// =============================================================================

async function checkForUpdate(el) {
    const statusEl = el.querySelector('#dash-update-status');
    const actionsEl = el.querySelector('#dash-update-actions');
    if (!statusEl || !actionsEl) return;

    try {
        const res = await fetch('/api/system/update-check');
        if (!res.ok) throw new Error('Check failed');
        updateStatus = await res.json();

        // Show branch name in System card
        const branchEl = el.querySelector('#dash-branch');
        if (branchEl && updateStatus.branch) {
            const tag = updateStatus.is_fork ? `${updateStatus.branch} · fork` : updateStatus.branch;
            branchEl.textContent = `· ${tag}`;
        }

        if (updateStatus.available) {
            statusEl.innerHTML = `
                <span class="dash-update-badge">v${updateStatus.latest} available</span>
                <span class="text-muted" style="font-size:var(--font-xs)">Current: v${updateStatus.current}</span>
            `;

            if (updateStatus.is_fork) {
                // Fork: show upstream version, link to releases, no update button
                actionsEl.innerHTML = `<p class="text-muted" style="font-size:var(--font-xs);margin:0">Upstream update — get it from <a href="https://github.com/ddxfish/sapphire/releases" target="_blank">Sapphire releases</a></p>`;
            } else if (updateStatus.docker || updateStatus.managed) {
                actionsEl.innerHTML = `<p class="text-muted" style="font-size:var(--font-xs);margin:0">Update via: <code>docker compose pull && docker compose up -d</code></p>`;
            } else if (updateStatus.has_git) {
                actionsEl.innerHTML = `<button class="btn-primary btn-sm" id="dash-do-update">Update Now</button>`;
                actionsEl.querySelector('#dash-do-update')?.addEventListener('click', () => doUpdate(el));
            } else {
                actionsEl.innerHTML = `<p class="text-muted" style="font-size:var(--font-xs);margin:0">Download the latest release from <a href="https://github.com/ddxfish/sapphire/releases" target="_blank">GitHub</a></p>`;
            }

            window.dispatchEvent(new CustomEvent('update-available', { detail: updateStatus }));
        } else {
            statusEl.innerHTML = `<span class="text-muted">\u2713 Up to date (v${updateStatus.current})</span>`;
            actionsEl.innerHTML = `<button class="btn-sm" id="dash-recheck" style="margin-top:6px">Check Again</button>`;
            actionsEl.querySelector('#dash-recheck')?.addEventListener('click', () => {
                statusEl.innerHTML = '<span class="text-muted">Checking...</span>';
                actionsEl.innerHTML = '';
                checkForUpdate(el);
            });
        }
    } catch (e) {
        statusEl.innerHTML = `<span class="text-muted">Could not check for updates</span>`;
    }
}

async function doUpdate(el) {
    const actionsEl = el.querySelector('#dash-update-actions');
    if (!actionsEl) return;

    const btn = actionsEl.querySelector('#dash-do-update');
    if (btn) { btn.disabled = true; btn.textContent = 'Updating...'; }

    try {
        const res = await fetch('/api/system/update', { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Update failed');
        }
        ui.showToast('Updated! Restarting...', 'success');

        const statusEl = el.querySelector('#dash-update-status');
        if (statusEl) statusEl.innerHTML = '<span class="text-muted">Restarting with new version...</span>';
        actionsEl.innerHTML = '';

        setTimeout(() => pollForRestart(), 2000);
    } catch (e) {
        ui.showToast(e.message, 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Update Now'; }
    }
}

function pollForRestart() {
    let attempts = 0;
    const poll = async () => {
        attempts++;
        try {
            const res = await fetch('/api/health');
            if (res.ok) { window.location.reload(); return; }
        } catch {}
        if (attempts < 30) setTimeout(poll, 1000);
    };
    poll();
}


// =============================================================================
// TOKEN METRICS
// =============================================================================

const fmt = n => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
};

async function loadMetrics(el) {
    const metricsEl = el.querySelector('#dash-metrics');
    const cb = el.querySelector('#metrics-enabled-cb');
    if (!metricsEl) return;

    // Load toggle state
    try {
        const toggleRes = await fetch('/api/metrics/enabled');
        if (toggleRes.ok) {
            const { enabled } = await toggleRes.json();
            if (cb) cb.checked = enabled;
        }
    } catch {}

    // Wire toggle
    if (cb) {
        cb.addEventListener('change', async () => {
            try {
                await fetch('/api/metrics/enabled', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: cb.checked })
                });
                loadMetricsData(metricsEl, cb.checked);
            } catch { cb.checked = !cb.checked; }
        });
    }

    loadMetricsData(metricsEl, cb?.checked !== false);
}

async function loadMetricsData(el, enabled) {
    if (!enabled) {
        el.innerHTML = '<span class="text-muted">Metrics tracking is off. Per-message stats still show in chat.</span>';
        return;
    }

    try {
        const [sumRes, brkRes, dailyRes] = await Promise.all([
            fetch('/api/metrics/summary?days=30'),
            fetch('/api/metrics/breakdown?days=30'),
            fetch('/api/metrics/daily?days=30')
        ]);

        if (!sumRes.ok || !brkRes.ok || !dailyRes.ok) throw new Error('Metrics fetch failed');

        const summary = await sumRes.json();
        const breakdown = await brkRes.json();
        const daily = await dailyRes.json();

        renderMetrics(el, summary, breakdown.models || [], daily.daily || []);
    } catch (e) {
        el.innerHTML = '<span class="text-muted">No metrics data yet — send some messages to start collecting</span>';
    }
}

function renderMetrics(el, s, models, daily) {
    if (!s.total_calls) {
        el.innerHTML = '<span class="text-muted">No data yet — metrics start recording from this version</span>';
        return;
    }

    const cacheRate = s.total_prompt > 0 && s.total_cache_read > 0
        ? Math.round((s.total_cache_read / s.total_prompt) * 100) : null;

    el.innerHTML = `
        <div class="metrics-stats">
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_calls)}</div>
                <div class="metric-label">LLM Calls</div>
            </div>
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_tokens)}</div>
                <div class="metric-label">Total Tokens</div>
            </div>
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_prompt)}</div>
                <div class="metric-label">Input</div>
            </div>
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_completion)}</div>
                <div class="metric-label">Output</div>
            </div>
            ${s.total_thinking > 0 ? `
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_thinking)}</div>
                <div class="metric-label">Thinking</div>
            </div>` : ''}
            ${cacheRate !== null ? `
            <div class="metric-item">
                <div class="metric-value">${cacheRate}%</div>
                <div class="metric-label">Cache Hit</div>
            </div>` : ''}
        </div>
        <div class="metrics-charts">
            <div class="metrics-chart-container">
                <div class="chart-title">Daily Usage</div>
                <div id="chart-daily" class="chart-area"></div>
            </div>
            <div class="metrics-chart-container">
                <div class="chart-title">Models</div>
                <div id="chart-models" class="chart-area"></div>
            </div>
        </div>
    `;

    renderDailyChart(el.querySelector('#chart-daily'), daily);
    renderModelChart(el.querySelector('#chart-models'), models);
}


// =============================================================================
// SVG CHARTS
// =============================================================================

function renderDailyChart(el, daily) {
    if (!el || daily.length < 2) {
        if (el) el.innerHTML = '<span class="text-muted" style="font-size:var(--font-xs)">Need 2+ days of data</span>';
        return;
    }

    const W = 540, H = 120, PAD_L = 40, PAD_R = 8, PAD_T = 8, PAD_B = 20;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;

    const maxTokens = Math.max(...daily.map(d => d.tokens)) || 1;
    const points = daily.map((d, i) => {
        const x = PAD_L + (i / (daily.length - 1)) * chartW;
        const y = PAD_T + chartH - (d.tokens / maxTokens) * chartH;
        return { x, y, ...d };
    });

    const polyline = points.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

    // Fill area under line
    const areaPoints = `${PAD_L},${PAD_T + chartH} ${polyline} ${points[points.length - 1].x.toFixed(1)},${PAD_T + chartH}`;

    // Y-axis labels (0, mid, max)
    const yMid = fmt(Math.round(maxTokens / 2));
    const yMax = fmt(maxTokens);

    // X-axis labels (first and last date)
    const firstDate = daily[0].date.slice(5); // MM-DD
    const lastDate = daily[daily.length - 1].date.slice(5);

    // Tooltip dots
    const dots = points.map(p =>
        `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" class="chart-dot">
            <title>${p.date}: ${fmt(p.tokens)} tokens, ${p.calls} calls</title>
        </circle>`
    ).join('');

    el.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" class="chart-svg">
            <!-- Grid lines -->
            <line x1="${PAD_L}" y1="${PAD_T}" x2="${PAD_L + chartW}" y2="${PAD_T}" class="chart-grid"/>
            <line x1="${PAD_L}" y1="${PAD_T + chartH / 2}" x2="${PAD_L + chartW}" y2="${PAD_T + chartH / 2}" class="chart-grid"/>
            <line x1="${PAD_L}" y1="${PAD_T + chartH}" x2="${PAD_L + chartW}" y2="${PAD_T + chartH}" class="chart-grid"/>

            <!-- Y labels -->
            <text x="${PAD_L - 4}" y="${PAD_T + 4}" class="chart-label" text-anchor="end">${yMax}</text>
            <text x="${PAD_L - 4}" y="${PAD_T + chartH / 2 + 3}" class="chart-label" text-anchor="end">${yMid}</text>
            <text x="${PAD_L - 4}" y="${PAD_T + chartH + 3}" class="chart-label" text-anchor="end">0</text>

            <!-- X labels -->
            <text x="${PAD_L}" y="${H - 2}" class="chart-label">${firstDate}</text>
            <text x="${PAD_L + chartW}" y="${H - 2}" class="chart-label" text-anchor="end">${lastDate}</text>

            <!-- Area fill -->
            <polygon points="${areaPoints}" class="chart-area-fill"/>

            <!-- Line -->
            <polyline points="${polyline}" class="chart-line"/>

            <!-- Dots -->
            ${dots}
        </svg>
    `;
}

function renderModelChart(el, models) {
    if (!el || !models.length) {
        if (el) el.innerHTML = '<span class="text-muted" style="font-size:var(--font-xs)">No model data yet</span>';
        return;
    }

    const top = models.slice(0, 5);
    const maxTotal = Math.max(...top.map(m => m.total)) || 1;

    const BAR_H = 18, GAP = 6, LABEL_W = 100, BAR_AREA = 370, PAD_R = 70;
    const W = LABEL_W + BAR_AREA + PAD_R;
    const H = top.length * (BAR_H + GAP) + GAP;

    const bars = top.map((m, i) => {
        const y = GAP + i * (BAR_H + GAP);
        const barW = Math.max(2, (m.total / maxTotal) * BAR_AREA);
        const label = m.model.length > 14 ? m.model.slice(0, 13) + '\u2026' : m.model;
        const cacheInfo = m.cache_read > 0 && m.prompt > 0
            ? ` \u00B7 cache ${Math.round((m.cache_read / m.prompt) * 100)}%` : '';

        return `
            <text x="${LABEL_W - 4}" y="${y + BAR_H / 2 + 4}" class="chart-label" text-anchor="end">${label}</text>
            <rect x="${LABEL_W}" y="${y}" width="${barW.toFixed(1)}" height="${BAR_H}" class="chart-bar" rx="2">
                <title>${m.model}: ${fmt(m.total)} tokens, ${m.calls} calls${cacheInfo}</title>
            </rect>
            <text x="${LABEL_W + barW + 4}" y="${y + BAR_H / 2 + 4}" class="chart-label">${fmt(m.total)}${cacheInfo}</text>
        `;
    }).join('');

    el.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" class="chart-svg">
            ${bars}
        </svg>
    `;
}
