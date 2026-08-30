// surface/chat-mode.js — chat mode's markup for the Surface.
// Moved verbatim from templates/index.html (Phase 1, tmp/chat-surface-plan.md);
// eagerly imported by main.js and rendered BEFORE initElements()/bindChatDom()
// cache element refs. Behavior lives in views/chat.js (lazy), NOT here.
//
// The data-acc ids on the static accordions intentionally equal the keys the
// old chat.js persistence computed from header text ("core:<title>"), so
// users' saved open/closed states survive the migration byte-for-byte.

export const chatMode = {
    id: 'chat',
    accordionNs: 'chat-sidebar',

    mainPane: `
                    <div id="chatbg">
                        <div id="motion-layer" aria-hidden="true"></div>
                        <div id="chatbg-overlay">
                            <div id="chat-container"></div>
                        </div>
                    </div>`,

    formArea: `
                        <div id="context-bar"></div>
                        <div id="image-preview-area" style="display: none;"></div>
                        <form id="chat-form">
                            <!-- Mic split: #mic-btn keeps every gesture handler; the caret is a
                                 SIBLING (never a child) and opens this device's audio flyout —
                                 volume + Conversation mode. Composer = device runtime, sidebar =
                                 chat settings (tmp/chat-sidebar-composer-plan.md). -->
                            <div class="mic-split" id="mic-split">
                                <button type="button" id="mic-btn" title="Hold to record">&#x1F3A4;</button>
                                <button type="button" id="mic-menu-btn" class="mic-caret" title="Volume &amp; conversation mode">&#x25BE;</button>
                                <div class="mic-flyout" id="mic-flyout">
                                    <div class="mic-flyout-row">
                                        <button type="button" id="mute-btn" class="vol-btn" title="Mute">&#x1F50A;</button>
                                        <input type="range" id="volume-slider" min="0" max="100" value="100" title="Volume">
                                    </div>
                                    <div class="mic-flyout-label">Conversation</div>
                                    <div class="conv-seg" id="conv-seg">
                                        <button type="button" data-conv="off" class="active" title="Conversation mode off">Off</button>
                                        <button type="button" data-conv="local" title="Conversation mode on the server's mic">Local mic</button>
                                        <button type="button" data-conv="browser" title="Conversation mode using this browser's mic">Browser mic</button>
                                    </div>
                                </div>
                            </div>
                            <div class="textarea-wrapper">
                                <button type="button" id="image-upload-btn" title="Attach image (or paste/drop)">&#x1F4CE;</button>
                                <textarea id="prompt-input" placeholder="Type message... (paste or drop images)" rows="1"></textarea>
                            </div>
                            <input type="file" id="image-upload-input" accept="image/*,.py,.txt,.md,.js,.ts,.json,.yaml,.yml,.toml,.ini,.cfg,.conf,.sh,.bash,.html,.css,.xml,.csv,.log,.env,.rs,.go,.java,.c,.cpp,.h" multiple style="display: none;">
                            <div class="send-btn-wrapper">
                                <button type="submit" id="send-btn"><span class="btn-txt">Send</span><span class="btn-ico" aria-hidden="true">&#x27A4;</span></button>
                                <button type="button" id="stop-btn" style="display: none;"><span class="btn-txt">Stop</span><span class="btn-ico" aria-hidden="true">&#x23F9;</span></button>
                                <span id="llm-indicator"></span>
                            </div>
                        </form>
                        <div id="prompt-display"></div>
                        <div id="functions-display"></div>`,

    sidebarHeader: `
                        <!-- Row 1: appearance + chat name + collapse (always visible) -->
                        <div class="sb-chat-header">
                            <button type="button" id="sb-appearance-btn" class="sb-accent-circle sb-appearance-btn" title="Appearance &#8212; accent color, scene, motion">&#x1F3A8;</button>
                            <div class="sb-chat-picker" id="sb-chat-picker">
                                <button class="sb-chat-picker-btn" id="sb-chat-picker-btn">
                                    <span id="sb-chat-name">Chat</span>
                                    <span class="sb-chat-arrow">&#x25BE;</span>
                                </button>
                                <div class="sb-chat-picker-dropdown" id="sb-chat-picker-dropdown"></div>
                            </div>
                            <button type="button" id="chat-sidebar-toggle" class="sb-icon-btn sb-collapse-btn" title="Hide sidebar">&#x25B6;</button>
                        </div>
                        <!-- Row 2: chat actions (always visible) -->
                        <div class="sb-chat-actions">
                            <button type="button" id="sb-new-chat" class="sb-icon-btn" title="New Chat">+</button>
                            <button type="button" id="sb-privacy-eye" class="sb-icon-btn" title="Private chat &#8212; local models and tools only">&#x1F441;&#xFE0E;</button>
                            <button type="button" id="sb-delete-chat" class="sb-icon-btn sb-icon-danger" title="Delete Chat">&#x1F5D1;</button>
                            <button type="button" id="clear-chat-btn" class="sb-icon-btn" title="Clear Chat">&#x2715;</button>
                            <div class="sb-kebab kebab-menu" id="chat-menu">
                                <button type="button" class="sb-icon-btn kebab-btn" title="More">&#x22EE;</button>
                                <div class="kebab-dropdown">
                                    <button id="import-chat-btn">Import</button>
                                    <button id="export-chat-btn">Export</button>
                                </div>
                            </div>
                        </div>

                        <!-- Faces strip: click = load that persona's bundle onto this chat.
                             The fields below are the live values (2026-08-29: the old
                             Persona/Settings tabs were two views of one form). -->
                        <div class="sb-persona-wrap">
                            <div class="sb-persona-grid" id="sb-persona-grid"></div>
                            <button type="button" class="sb-btn-sm sb-personas-goto" id="sb-personas-goto" title="All personas">&#x2197;</button>
                        </div>`,

    sidebarBody: `
                        <div class="sidebar-section">
                            <div class="sb-field">
                                <label for="sb-prompt">prompt</label>
                                <div class="sb-field-row">
                                    <select id="sb-prompt"><option value="sapphire">Sapphire</option></select>
                                    <button type="button" class="sb-btn-sm sb-goto-view" data-view="prompts" data-select="sb-prompt" title="Open in Prompts">&#x2197;</button>
                                </div>
                            </div>
                            <div class="sb-field">
                                <label for="sb-toolset">toolset</label>
                                <div class="sb-field-row">
                                    <select id="sb-toolset"><option value="all">All</option></select>
                                    <button type="button" class="sb-btn-sm sb-goto-view" data-view="toolsets" data-select="sb-toolset" title="Open in Toolsets">&#x2197;</button>
                                </div>
                            </div>
                            <div class="sb-field">
                                <label for="sb-spice-set">spice set</label>
                                <div class="sb-field-row">
                                    <select id="sb-spice-set"><option value="default">default</option></select>
                                    <button type="button" class="sb-btn-sm sb-goto-view" data-view="spices" data-select="sb-spice-set" title="Open in Spices">&#x2197;</button>
                                </div>
                            </div>
                            <div class="sb-field">
                                <label for="sb-llm-primary">provider</label>
                                <div class="sb-field-row">
                                    <select id="sb-llm-primary"><option value="auto">Auto</option></select>
                                    <button type="button" class="sb-btn-sm sb-goto-view" data-view="settings" data-tab="llm" title="Open in Settings &gt; LLM">&#x2197;</button>
                                </div>
                            </div>
                            <div class="sb-field" id="sb-model-group" style="display:none">
                                <label for="sb-llm-model">model</label>
                                <select id="sb-llm-model"></select>
                            </div>
                            <div class="sb-field" id="sb-model-custom-group" style="display:none">
                                <label for="sb-llm-model-custom">model</label>
                                <input type="text" id="sb-llm-model-custom" placeholder="Model name">
                            </div>
                        </div>

                        <!-- Quick toggles (per-chat) -->
                        <div class="sb-toggles">
                            <button type="button" class="sb-toggle active" id="sb-spice-toggle" data-active="true">Spice &middot; 3</button>
                            <button type="button" class="sb-toggle" id="sb-datetime-toggle" data-active="false">Date/Time</button>
                        </div>

                        <!-- Mind Scopes Accordion — populated by shared/scope-dropdowns.js
                             from /api/init scope_declarations. Each editor that needs
                             scope dropdowns (sidebar, persona, trigger editor) uses the
                             same renderer; adding a new scope means editing one manifest. -->
                        <div class="sidebar-section sidebar-accordion" data-acc="core:&#x1F9E0; Mind">
                            <div class="sidebar-accordion-header">
                                <span class="accordion-arrow">&#x25B6;</span>
                                <span>&#x1F9E0; Mind</span>
                            </div>
                            <div class="sidebar-accordion-content" style="display:none">
                                <div id="sb-scope-dropdowns"></div>
                            </div>
                        </div>

                        <!-- Documents (RAG) Accordion -->
                        <div class="sidebar-section sidebar-accordion" data-acc="core:&#x1F4CE; Documents">
                            <div class="sidebar-accordion-header">
                                <span class="accordion-arrow">&#x25B6;</span>
                                <span>&#x1F4CE; Documents</span>
                                <span id="sb-doc-count" class="sb-badge" style="display:none">0</span>
                            </div>
                            <div class="sidebar-accordion-content" style="display:none">
                                <div class="sb-field">
                                    <label for="sb-rag-context">context</label>
                                    <select id="sb-rag-context">
                                        <option value="off">Off</option>
                                        <option value="light">Light</option>
                                        <option value="normal" selected>Normal</option>
                                        <option value="heavy">Heavy</option>
                                    </select>
                                </div>
                                <div id="sb-doc-list"></div>
                                <label class="sb-upload-btn">
                                    Upload file
                                    <input type="file" id="sb-doc-upload" hidden>
                                </label>
                            </div>
                        </div>

                        <!-- TTS (Voice) Accordion -->
                        <div class="sidebar-section sidebar-accordion" data-acc="core:TTS (Voice)">
                            <div class="sidebar-accordion-header">
                                <span class="accordion-arrow">&#x25B6;</span>
                                <span>TTS (Voice)</span>
                            </div>
                            <div class="sidebar-accordion-content" style="display:none">
                                <div class="sb-field">
                                    <label for="sb-voice">voice</label>
                                    <select id="sb-voice"></select>
                                </div>
                                <div class="sb-field sb-field-stack">
                                    <label>Pitch: <span id="sb-pitch-val">0.98</span></label>
                                    <input type="range" id="sb-pitch" min="0.5" max="1.5" step="0.02" value="0.98">
                                </div>
                                <div class="sb-field sb-field-stack">
                                    <label>Speed: <span id="sb-speed-val">1.3</span></label>
                                    <input type="range" id="sb-speed" min="0.5" max="2.5" step="0.1" value="1.3">
                                </div>
                            </div>
                        </div>

                        <!-- System Prompt Accordion -->
                        <div class="sidebar-section sidebar-accordion" data-acc="core:System Prompt">
                            <div class="sidebar-accordion-header">
                                <span class="accordion-arrow">&#x25B6;</span>
                                <span>System Prompt</span>
                            </div>
                            <div class="sidebar-accordion-content" style="display:none">
                                <div class="sb-field">
                                    <label for="sb-spice-turns">spice turns</label>
                                    <input type="number" id="sb-spice-turns" min="1" max="20" value="3">
                                </div>
                                <div class="sb-field sb-field-stack">
                                    <label for="sb-custom-context">Custom Context</label>
                                    <textarea id="sb-custom-context" rows="3" placeholder="Injected into system prompt..."></textarea>
                                </div>
                                <div class="sb-field sb-field-stack">
                                    <label for="sb-ghost-context">Ghost Message</label>
                                    <textarea id="sb-ghost-context" rows="3" placeholder="Injected as a per-turn ghost message (empty = none)..."></textarea>
                                </div>
                            </div>
                        </div>

                        <!-- Plugin-registered accordions (injected by JS) -->
                        <div id="sb-plugin-accordions"></div>
`,
};
