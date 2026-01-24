// continuity-modal.js - Main continuity panel with tabs

import * as api from './continuity-api.js';
import ContinuityEditor from './continuity-editor.js';

export default class ContinuityModal {
  constructor() {
    this.el = null;
    this.onCloseCallback = null;
    this.activeTab = 'tasks';
    this.tasks = [];
    this.timeline = [];
    this.activity = [];
    this.status = {};
  }

  async open() {
    this.render();
    document.body.appendChild(this.el);
    await this.loadData();
  }

  close() {
    if (this.el) {
      this.el.remove();
      this.el = null;
    }
    if (this.onCloseCallback) this.onCloseCallback();
  }

  async loadData() {
    try {
      const [tasks, status, timeline, activity] = await Promise.all([
        api.fetchTasks(),
        api.fetchStatus(),
        api.fetchTimeline(24),
        api.fetchActivity(20)
      ]);
      this.tasks = tasks;
      this.status = status;
      this.timeline = timeline;
      this.activity = activity;
      this.renderContent();
    } catch (e) {
      console.error('Failed to load continuity data:', e);
    }
  }

  render() {
    this.el = document.createElement('div');
    this.el.className = 'continuity-modal';
    this.el.innerHTML = `
      <div class="continuity-panel">
        <div class="continuity-header">
          <h2>
            ⏰ Continuity
            <span class="status-badge ${this.status.running ? '' : 'stopped'}">
              ${this.status.running !== false ? 'Running' : 'Stopped'}
            </span>
          </h2>
          <button class="continuity-close" data-action="close">&times;</button>
        </div>
        <div class="continuity-tabs">
          <button class="continuity-tab active" data-tab="tasks">Tasks</button>
          <button class="continuity-tab" data-tab="timeline">Timeline</button>
          <button class="continuity-tab" data-tab="activity">Activity</button>
        </div>
        <div class="continuity-content" id="continuity-content">
          <div class="continuity-empty">
            <div class="continuity-empty-icon">⏳</div>
            Loading...
          </div>
        </div>
      </div>
    `;

    this.el.addEventListener('click', (e) => this.handleClick(e));
    
    // Close on overlay click
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.close();
    });
  }

  handleClick(e) {
    const action = e.target.dataset.action;
    const tab = e.target.dataset.tab;
    const taskId = e.target.dataset.taskId;

    if (action === 'close') {
      this.close();
      return;
    }

    if (tab) {
      this.switchTab(tab);
      return;
    }

    if (action === 'add-task') {
      this.openEditor(null);
      return;
    }

    if (action === 'edit' && taskId) {
      const task = this.tasks.find(t => t.id === taskId);
      if (task) this.openEditor(task);
      return;
    }

    if (action === 'run' && taskId) {
      this.runTask(taskId);
      return;
    }

    if (action === 'delete' && taskId) {
      this.deleteTask(taskId);
      return;
    }

    if (action === 'toggle' && taskId) {
      this.toggleTask(taskId);
      return;
    }
  }

  switchTab(tab) {
    this.activeTab = tab;
    this.el.querySelectorAll('.continuity-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
    this.renderContent();
  }

  renderContent() {
    const content = this.el.querySelector('#continuity-content');
    
    // Update status badge
    const badge = this.el.querySelector('.status-badge');
    if (badge) {
      badge.className = `status-badge ${this.status.running ? '' : 'stopped'}`;
      badge.textContent = this.status.running !== false ? 'Running' : 'Stopped';
    }
    
    switch (this.activeTab) {
      case 'tasks':
        content.innerHTML = this.renderTasks();
        break;
      case 'timeline':
        content.innerHTML = this.renderTimeline();
        break;
      case 'activity':
        content.innerHTML = this.renderActivity();
        break;
    }
  }

  renderTasks() {
    if (this.tasks.length === 0) {
      return `
        <div class="continuity-empty">
          <div class="continuity-empty-icon">📋</div>
          <p>No tasks yet</p>
          <button class="continuity-add-btn" data-action="add-task">+ Create First Task</button>
        </div>
      `;
    }

    return `
      <div class="continuity-tasks">
        ${this.tasks.map(t => this.renderTaskCard(t)).join('')}
        <button class="continuity-add-btn" data-action="add-task">+ Add Task</button>
      </div>
    `;
  }

  renderTaskCard(task) {
    const lastRun = task.last_run ? this.formatTime(task.last_run) : 'Never';
    const memoryInfo = task.memory_scope && task.memory_scope !== 'none' ? `💾 ${task.memory_scope}` : '';
    return `
      <div class="continuity-task-card">
        <button class="continuity-task-toggle ${task.enabled ? 'enabled' : ''}" 
                data-action="toggle" data-task-id="${task.id}" 
                title="${task.enabled ? 'Disable' : 'Enable'}"></button>
        <div class="continuity-task-info">
          <div class="continuity-task-name">${this.escapeHtml(task.name)}</div>
          <div class="continuity-task-schedule">${this.escapeHtml(task.schedule)}</div>
          <div class="continuity-task-meta">
            ${task.chance < 100 ? `${task.chance}% chance • ` : ''}
            ${task.iterations > 1 ? `${task.iterations} iterations • ` : ''}
            ${memoryInfo ? `${memoryInfo} • ` : ''}
            Last: ${lastRun}
          </div>
        </div>
        <div class="continuity-task-actions">
          <button class="run" data-action="run" data-task-id="${task.id}" title="Run Now">▶</button>
          <button data-action="edit" data-task-id="${task.id}" title="Edit">✏️</button>
          <button class="delete" data-action="delete" data-task-id="${task.id}" title="Delete">🗑️</button>
        </div>
      </div>
    `;
  }

  renderTimeline() {
    if (this.timeline.length === 0) {
      return `
        <div class="continuity-empty">
          <div class="continuity-empty-icon">📅</div>
          <p>No scheduled tasks in the next 24 hours</p>
        </div>
      `;
    }

    return `
      <div class="continuity-timeline">
        ${this.timeline.map(t => `
          <div class="continuity-timeline-item">
            <div class="continuity-timeline-time">${this.formatTime(t.scheduled_for)}</div>
            <div class="continuity-timeline-task">${this.escapeHtml(t.task_name)}</div>
            ${t.chance < 100 ? `<div class="continuity-timeline-chance">${t.chance}% chance</div>` : ''}
          </div>
        `).join('')}
      </div>
    `;
  }

  renderActivity() {
    if (this.activity.length === 0) {
      return `
        <div class="continuity-empty">
          <div class="continuity-empty-icon">📜</div>
          <p>No activity yet</p>
        </div>
      `;
    }

    return `
      <div class="continuity-activity">
        ${this.activity.slice().reverse().map(a => `
          <div class="continuity-activity-item">
            <div class="continuity-activity-status ${a.status}"></div>
            <div class="continuity-activity-time">${this.formatTime(a.timestamp)}</div>
            <div class="continuity-activity-name">${this.escapeHtml(a.task_name)}</div>
            <div class="continuity-activity-detail">${a.status}${a.details?.reason ? ` (${a.details.reason})` : ''}</div>
          </div>
        `).join('')}
      </div>
    `;
  }

  openEditor(task) {
    const editor = new ContinuityEditor(
      task,
      () => this.loadData(),  // onSave - refresh
      null  // onClose
    );
    editor.open();
  }

  async runTask(taskId) {
    const task = this.tasks.find(t => t.id === taskId);
    if (!task) return;
    
    if (!confirm(`Run "${task.name}" now?`)) return;
    
    try {
      await api.runTask(taskId);
      // Reload after a delay to get updated last_run
      setTimeout(() => this.loadData(), 1000);
    } catch (e) {
      alert('Error: ' + e.message);
    }
  }

  async deleteTask(taskId) {
    const task = this.tasks.find(t => t.id === taskId);
    if (!task) return;
    
    if (!confirm(`Delete "${task.name}"?`)) return;
    
    try {
      await api.deleteTask(taskId);
      await this.loadData();
    } catch (e) {
      alert('Error: ' + e.message);
    }
  }

  async toggleTask(taskId) {
    const task = this.tasks.find(t => t.id === taskId);
    if (!task) return;
    
    try {
      await api.updateTask(taskId, { enabled: !task.enabled });
      await this.loadData();
    } catch (e) {
      alert('Error: ' + e.message);
    }
  }

  formatTime(isoString) {
    if (!isoString) return 'Unknown';
    try {
      const d = new Date(isoString);
      const now = new Date();
      const diff = now - d;
      
      // Today
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      
      // Yesterday
      const yesterday = new Date(now);
      yesterday.setDate(yesterday.getDate() - 1);
      if (d.toDateString() === yesterday.toDateString()) {
        return 'Yesterday ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      
      // Within a week
      if (diff < 7 * 24 * 60 * 60 * 1000) {
        return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + 
               d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
             d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoString;
    }
  }

  escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;')
                      .replace(/</g, '&lt;')
                      .replace(/>/g, '&gt;')
                      .replace(/"/g, '&quot;');
  }
}