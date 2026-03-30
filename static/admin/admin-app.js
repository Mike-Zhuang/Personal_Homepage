(function () {
    "use strict";

    function resolveApiPrefix() {
        if (typeof window === "undefined") {
            return "/api/admin";
        }

        var hostname = window.location.hostname;
        var isLocalHost = hostname === "127.0.0.1" || hostname === "localhost";
        var isHugoDevPort = window.location.port === "1313";

        if (isLocalHost && isHugoDevPort) {
            return "http://127.0.0.1:8000/api/admin";
        }

        return "/api/admin";
    }

    var API_PREFIX = resolveApiPrefix();
    var POLL_TIMER = null;

    var state = {
        apiKey: "",
        connected: false,
        busy: false,
        sections: [],
        activeSection: "",
        sourceFile: "",
        contentText: "",
        backups: [],
        statusText: "Please enter ADMIN_API_KEY to connect.",
        errorText: "",
        publish: {
            status: "idle",
            startedAt: null,
            finishedAt: null,
            lastError: null,
            lastOutput: null
        }
    };

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatTime(value) {
        if (!value) {
            return "-";
        }

        try {
            return new Date(value).toLocaleString();
        } catch (_error) {
            return String(value);
        }
    }

    function setBusy(nextBusy) {
        state.busy = Boolean(nextBusy);
        render();
    }

    function setStatus(message) {
        state.statusText = message;
        state.errorText = "";
        render();
    }

    function setError(message) {
        state.errorText = message;
        render();
    }

    function stopPublishPolling() {
        if (POLL_TIMER !== null) {
            window.clearInterval(POLL_TIMER);
            POLL_TIMER = null;
        }
    }

    function startPublishPolling() {
        stopPublishPolling();

        if (state.publish.status !== "running") {
            return;
        }

        POLL_TIMER = window.setInterval(function () {
            void refreshPublishStatus();
        }, 2200);
    }

    async function requestAdmin(path, options) {
        var requestOptions = options || {};
        var headers = Object.assign({}, requestOptions.headers || {});

        if (state.apiKey) {
            headers["X-Admin-API-Key"] = state.apiKey;
        }

        if (requestOptions.body) {
            headers["Content-Type"] = "application/json";
        }

        var response = await fetch(API_PREFIX + path, Object.assign({}, requestOptions, { headers: headers }));
        var text = await response.text();
        var payload = {};

        if (text) {
            try {
                payload = JSON.parse(text);
            } catch (_error) {
                payload = { detail: text };
            }
        }

        if (!response.ok) {
            throw new Error(payload.detail || ("Request failed: " + response.status));
        }

        return payload;
    }

    async function loadSections() {
        var result = await requestAdmin("/sections");
        state.sections = Array.isArray(result.sections) ? result.sections : [];

        if (state.sections.length === 0) {
            state.activeSection = "";
            state.sourceFile = "";
            state.contentText = "{}";
            state.backups = [];
            return;
        }

        if (!state.activeSection || !state.sections.some(function (item) { return item.key === state.activeSection; })) {
            state.activeSection = state.sections[0].key;
        }

        await loadSection(state.activeSection);
    }

    async function loadSection(section) {
        var target = section || state.activeSection;
        if (!target) {
            return;
        }

        var contentResult = await requestAdmin("/content/" + encodeURIComponent(target));
        var backupResult = await requestAdmin("/backups/" + encodeURIComponent(target));

        state.activeSection = target;
        state.sourceFile = contentResult.sourceFile || "";
        state.contentText = JSON.stringify(contentResult.content || {}, null, 2);
        state.backups = Array.isArray(backupResult.backups) ? backupResult.backups : [];
    }

    async function connectWithKey() {
        var input = document.getElementById("api-key-input");
        var nextKey = input ? input.value.trim() : "";

        if (!nextKey) {
            setError("ADMIN_API_KEY is required.");
            return;
        }

        state.apiKey = nextKey;
        setBusy(true);

        try {
            await loadSections();
            state.connected = true;
            setStatus("Connected.");
        } catch (error) {
            state.apiKey = "";
            state.connected = false;
            setError(error.message || "Failed to connect.");
        } finally {
            setBusy(false);
        }
    }

    function logout() {
        stopPublishPolling();
        state.apiKey = "";
        state.connected = false;
        state.sections = [];
        state.activeSection = "";
        state.sourceFile = "";
        state.contentText = "";
        state.backups = [];
        state.publish = {
            status: "idle",
            startedAt: null,
            finishedAt: null,
            lastError: null,
            lastOutput: null
        };
        setStatus("Logged out. This page does not persist password.");
    }

    async function saveContent() {
        if (!state.activeSection) {
            return;
        }

        var editor = document.getElementById("json-editor");
        var nextText = editor ? editor.value : state.contentText;
        var parsed;

        try {
            parsed = JSON.parse(nextText);
        } catch (error) {
            setError("JSON parse error: " + error.message);
            return;
        }

        state.contentText = JSON.stringify(parsed, null, 2);
        setBusy(true);

        try {
            var result = await requestAdmin("/content/" + encodeURIComponent(state.activeSection), {
                method: "PUT",
                body: JSON.stringify({ content: parsed })
            });

            await loadSection(state.activeSection);
            var publishText = result.publishStatus ? (" Publish: " + result.publishStatus + ".") : "";
            setStatus("Saved at " + formatTime(result.updatedAt) + "." + publishText);
            startPublishPolling();
        } catch (error) {
            setError(error.message || "Save failed.");
        } finally {
            setBusy(false);
        }
    }

    async function publishNow() {
        setBusy(true);

        try {
            state.publish = await requestAdmin("/publish", { method: "POST" });
            setStatus("Publish status: " + state.publish.status + ".");
            startPublishPolling();
        } catch (error) {
            setError(error.message || "Publish trigger failed.");
        } finally {
            setBusy(false);
        }
    }

    async function refreshPublishStatus() {
        try {
            state.publish = await requestAdmin("/publish/status");
            render();

            if (state.publish.status !== "running") {
                stopPublishPolling();
            }
        } catch (_error) {
            // ignore transient poll errors
        }
    }

    async function rollbackTo(name) {
        if (!state.activeSection || !name) {
            return;
        }

        var ok = window.confirm("Rollback " + state.activeSection + " using " + name + "?");
        if (!ok) {
            return;
        }

        setBusy(true);

        try {
            var encoded = encodeURIComponent(name);
            var result = await requestAdmin("/rollback/" + encodeURIComponent(state.activeSection) + "/" + encoded, {
                method: "POST"
            });

            await loadSection(state.activeSection);
            setStatus("Rollback completed at " + formatTime(result.updatedAt) + ".");
            startPublishPolling();
        } catch (error) {
            setError(error.message || "Rollback failed.");
        } finally {
            setBusy(false);
        }
    }

    async function selectSection(section) {
        if (!section || section === state.activeSection) {
            return;
        }

        setBusy(true);

        try {
            await loadSection(section);
            setStatus("Loaded " + section + ".");
        } catch (error) {
            setError(error.message || "Unable to load section.");
        } finally {
            setBusy(false);
        }
    }

    async function reloadActiveSection() {
        if (!state.activeSection) {
            return;
        }

        setBusy(true);

        try {
            await loadSection(state.activeSection);
            setStatus("Reloaded " + state.activeSection + ".");
        } catch (error) {
            setError(error.message || "Unable to reload section.");
        } finally {
            setBusy(false);
        }
    }

    function bindEvents() {
        var connectButton = document.getElementById("connect-button");
        if (connectButton) {
            connectButton.addEventListener("click", function () {
                void connectWithKey();
            });
        }

        var logoutButton = document.getElementById("logout-button");
        if (logoutButton) {
            logoutButton.addEventListener("click", logout);
        }

        var saveButton = document.getElementById("save-button");
        if (saveButton) {
            saveButton.addEventListener("click", function () {
                void saveContent();
            });
        }

        var publishButton = document.getElementById("publish-button");
        if (publishButton) {
            publishButton.addEventListener("click", function () {
                void publishNow();
            });
        }

        var refreshButton = document.getElementById("refresh-button");
        if (refreshButton) {
            refreshButton.addEventListener("click", function () {
                void reloadActiveSection();
            });
        }

        var pollButton = document.getElementById("publish-refresh-button");
        if (pollButton) {
            pollButton.addEventListener("click", function () {
                void refreshPublishStatus();
            });
        }

        var sectionButtons = document.querySelectorAll("[data-section-key]");
        sectionButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var key = button.getAttribute("data-section-key") || "";
                void selectSection(key);
            });
        });

        var rollbackButtons = document.querySelectorAll("[data-backup-name]");
        rollbackButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var backupName = button.getAttribute("data-backup-name") || "";
                void rollbackTo(backupName);
            });
        });

        var apiKeyInput = document.getElementById("api-key-input");
        if (apiKeyInput) {
            apiKeyInput.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    void connectWithKey();
                }
            });
        }
    }

    function renderAuthView(root) {
        root.innerHTML = ""
            + "<div class=\"auth-wrap\">"
            + "<section class=\"auth-card\">"
            + "<h1 class=\"admin-title\">Personal Homepage Admin</h1>"
            + "<p class=\"admin-subtitle\">Enter ADMIN_API_KEY to connect. Password is never stored in browser storage.</p>"
            + "<label class=\"field\" for=\"api-key-input\">"
            + "<span class=\"field-label\">ADMIN_API_KEY</span>"
            + "<input id=\"api-key-input\" type=\"password\" autocomplete=\"off\" spellcheck=\"false\" placeholder=\"Paste your key\" "
            + (state.busy ? "disabled" : "")
            + ">"
            + "</label>"
            + "<div class=\"auth-actions\">"
            + "<button id=\"connect-button\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Connect</button>"
            + "</div>"
            + "<div class=\"status-line " + (state.errorText ? "error" : "") + "\">"
            + escapeHtml(state.errorText || state.statusText)
            + "</div>"
            + "</section>"
            + "</div>";
    }

    function renderAdminView(root) {
        var sectionsHtml = state.sections.map(function (item) {
            var activeClass = item.key === state.activeSection ? "active" : "ghost";
            return ""
                + "<button type=\"button\" class=\"" + activeClass + "\" data-section-key=\"" + escapeHtml(item.key) + "\" "
                + (state.busy ? "disabled" : "")
                + ">"
                + escapeHtml(item.key)
                + "</button>";
        }).join("");

        var backupsHtml = state.backups.length === 0
            ? "<p class=\"admin-subtitle\">No backups yet.</p>"
            : state.backups.map(function (item) {
                return ""
                    + "<div class=\"backup-item\">"
                    + "<div><strong>" + escapeHtml(item.name) + "</strong></div>"
                    + "<div class=\"backup-meta\">" + escapeHtml(formatTime(item.createdAt)) + " | " + escapeHtml(item.sizeBytes) + " bytes</div>"
                    + "<button class=\"danger\" type=\"button\" data-backup-name=\"" + escapeHtml(item.name) + "\" "
                    + (state.busy ? "disabled" : "")
                    + ">Rollback</button>"
                    + "</div>";
            }).join("");

        var publishError = state.publish.lastError
            ? "<div class=\"status-line error\">" + escapeHtml(state.publish.lastError) + "</div>"
            : "";

        var publishOutput = state.publish.lastOutput
            ? "<div class=\"status-line\"><pre class=\"mono-output\">" + escapeHtml(state.publish.lastOutput) + "</pre></div>"
            : "";

        root.innerHTML = ""
            + "<div class=\"admin-shell\">"
            + "<header class=\"admin-header\">"
            + "<h1 class=\"admin-title\">Personal Homepage Admin</h1>"
            + "<p class=\"admin-subtitle\">Stable mode: plain JavaScript, no external CDN dependencies.</p>"
            + "<div class=\"top-actions\">"
            + "<button id=\"refresh-button\" class=\"secondary\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Reload Section</button>"
            + "<button id=\"save-button\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Save</button>"
            + "<button id=\"publish-button\" class=\"secondary\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Publish</button>"
            + "<button id=\"publish-refresh-button\" class=\"ghost\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Refresh Publish</button>"
            + "<button id=\"logout-button\" class=\"danger\" type=\"button\">Logout</button>"
            + "</div>"
            + "<div class=\"status-line " + (state.errorText ? "error" : "") + "\">"
            + escapeHtml(state.errorText || state.statusText)
            + "</div>"
            + "</header>"
            + "<div class=\"admin-layout\">"
            + "<aside class=\"panel\">"
            + "<h2>Sections</h2>"
            + "<div class=\"section-list\">" + sectionsHtml + "</div>"
            + "<h3 class=\"panel-subhead\">Backups</h3>"
            + "<div class=\"backup-list\">" + backupsHtml + "</div>"
            + "</aside>"
            + "<main class=\"panel\">"
            + "<div class=\"editor-toolbar\">"
            + "<h2>Editor</h2>"
            + "<span class=\"source-file\">Source: " + escapeHtml(state.sourceFile || "-") + "</span>"
            + "<span class=\"source-file\">Publish: " + escapeHtml(state.publish.status || "idle")
            + " | Started: " + escapeHtml(formatTime(state.publish.startedAt))
            + " | Finished: " + escapeHtml(formatTime(state.publish.finishedAt)) + "</span>"
            + "</div>"
            + publishError
            + publishOutput
            + "<label class=\"field\" for=\"json-editor\">"
            + "<span class=\"field-label\">Section JSON</span>"
            + "<textarea id=\"json-editor\" class=\"json-editor\" spellcheck=\"false\" " + (state.busy ? "disabled" : "") + "></textarea>"
            + "</label>"
            + "</main>"
            + "</div>"
            + "</div>";

        var editor = document.getElementById("json-editor");
        if (editor) {
            editor.value = state.contentText || "";
        }
    }

    function render() {
        var root = document.getElementById("admin-root");
        if (!root) {
            return;
        }

        if (!state.connected) {
            renderAuthView(root);
        } else {
            renderAdminView(root);
        }

        bindEvents();
    }

    function showFatalError(error) {
        var root = document.getElementById("admin-root");
        if (!root) {
            return;
        }

        var message = error && error.message ? error.message : String(error || "Unknown error");
        root.innerHTML = ""
            + "<div class=\"auth-wrap\">"
            + "<section class=\"auth-card\">"
            + "<h1 class=\"admin-title\">Admin UI Crash</h1>"
            + "<p class=\"admin-subtitle\">Unexpected error occurred while rendering admin page.</p>"
            + "<div class=\"status-line error\">" + escapeHtml(message) + "</div>"
            + "</section>"
            + "</div>";
    }

    try {
        render();
    } catch (error) {
        showFatalError(error);
    }
})();
