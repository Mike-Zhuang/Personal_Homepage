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

    function createDefaultContactSettings() {
        return {
            contactPlaceholderMode: true,
            smtpHost: "",
            smtpPort: 465,
            smtpUseSsl: true,
            smtpUseStarttls: false,
            smtpUser: "",
            smtpPassConfigured: false,
            mailFrom: "",
            mailTo: "",
            mailSubjectPrefix: "[Personal Homepage]"
        };
    }

    var state = {
        apiKey: "",
        connected: false,
        busy: false,
        sections: [],
        activeSection: "",
        sourceFile: "",
        contentObject: {},
        contentText: "{}",
        editorMode: "form",
        backups: [],
        messageFilter: "all",
        messageTotal: 0,
        messages: [],
        activeMessageId: "",
        activeMessageDetail: null,
        contactSettings: createDefaultContactSettings(),
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

    function deepClone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function pathToString(path) {
        return path.map(String).join(".");
    }

    function parsePath(text) {
        if (!text) {
            return [];
        }

        return text
            .split(".")
            .filter(function (segment) {
                return segment.length > 0;
            })
            .map(function (segment) {
                return /^\d+$/.test(segment) ? Number(segment) : segment;
            });
    }

    function getAtPath(root, path) {
        var cursor = root;

        for (var index = 0; index < path.length; index += 1) {
            if (cursor == null) {
                return undefined;
            }
            cursor = cursor[path[index]];
        }

        return cursor;
    }

    function setAtPath(root, path, nextValue) {
        if (path.length === 0) {
            return nextValue;
        }

        var nextRoot = deepClone(root);
        var cursor = nextRoot;

        for (var index = 0; index < path.length - 1; index += 1) {
            cursor = cursor[path[index]];
        }

        cursor[path[path.length - 1]] = nextValue;
        return nextRoot;
    }

    function defaultValueFromSample(sample) {
        if (Array.isArray(sample)) {
            return [];
        }

        if (isObject(sample)) {
            return {};
        }

        if (typeof sample === "number") {
            return 0;
        }

        if (typeof sample === "boolean") {
            return false;
        }

        return "";
    }

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
            state.contentObject = {};
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
        state.contentObject = deepClone(contentResult.content || {});
        state.contentText = JSON.stringify(state.contentObject, null, 2);
        state.backups = Array.isArray(backupResult.backups) ? backupResult.backups : [];
    }

    async function loadMessages(nextFilter) {
        var targetFilter = nextFilter || state.messageFilter || "all";
        var result = await requestAdmin(
            "/messages?status=" + encodeURIComponent(targetFilter) + "&limit=120"
        );

        state.messageFilter = targetFilter;
        state.messages = Array.isArray(result.messages) ? result.messages : [];
        state.messageTotal = Number(result.total || 0);

        if (state.messages.length === 0) {
            state.activeMessageId = "";
            state.activeMessageDetail = null;
            return;
        }

        if (!state.activeMessageId || !state.messages.some(function (item) { return item.id === state.activeMessageId; })) {
            state.activeMessageId = state.messages[0].id;
        }

        var detailResult = await requestAdmin("/messages/" + encodeURIComponent(state.activeMessageId));
        state.activeMessageDetail = detailResult.message || null;
    }

    async function selectMessage(messageId) {
        if (!messageId) {
            return;
        }

        setBusy(true);
        try {
            var detailResult = await requestAdmin("/messages/" + encodeURIComponent(messageId));
            state.activeMessageId = messageId;
            state.activeMessageDetail = detailResult.message || null;
            setStatus("Loaded message " + messageId + ".");
        } catch (error) {
            setError(error.message || "Unable to load message detail.");
        } finally {
            setBusy(false);
        }
    }

    async function refreshMessages(nextFilter) {
        setBusy(true);
        try {
            await loadMessages(nextFilter || state.messageFilter || "all");
            setStatus("Messages refreshed.");
        } catch (error) {
            setError(error.message || "Unable to refresh messages.");
        } finally {
            setBusy(false);
        }
    }

    async function markMessageProcessed() {
        if (!state.activeMessageId) {
            return;
        }

        setBusy(true);
        try {
            await requestAdmin("/messages/" + encodeURIComponent(state.activeMessageId) + "/process", {
                method: "POST"
            });

            await loadMessages(state.messageFilter);
            setStatus("Message marked as processed.");
        } catch (error) {
            setError(error.message || "Unable to update message status.");
        } finally {
            setBusy(false);
        }
    }

    async function loadContactSettings() {
        var result = await requestAdmin("/contact-settings");
        state.contactSettings = Object.assign(createDefaultContactSettings(), result || {});
    }

    function readContactSettingsForm() {
        var smtpHost = document.getElementById("smtp-host-input");
        var smtpPort = document.getElementById("smtp-port-input");
        var smtpUseSsl = document.getElementById("smtp-use-ssl-input");
        var smtpUseStarttls = document.getElementById("smtp-use-starttls-input");
        var smtpUser = document.getElementById("smtp-user-input");
        var smtpPass = document.getElementById("smtp-pass-input");
        var mailFrom = document.getElementById("mail-from-input");
        var mailTo = document.getElementById("mail-to-input");
        var subjectPrefix = document.getElementById("mail-subject-prefix-input");
        var placeholderMode = document.getElementById("contact-placeholder-mode-input");

        var parsedPort = Number(smtpPort ? smtpPort.value : 465);
        var safePort = Number.isFinite(parsedPort) ? parsedPort : 465;

        return {
            contactPlaceholderMode: placeholderMode ? Boolean(placeholderMode.checked) : true,
            smtpHost: smtpHost ? smtpHost.value.trim() : "",
            smtpPort: safePort,
            smtpUseSsl: smtpUseSsl ? Boolean(smtpUseSsl.checked) : true,
            smtpUseStarttls: smtpUseStarttls ? Boolean(smtpUseStarttls.checked) : false,
            smtpUser: smtpUser ? smtpUser.value.trim() : "",
            smtpPass: smtpPass ? smtpPass.value : "",
            mailFrom: mailFrom ? mailFrom.value.trim() : "",
            mailTo: mailTo ? mailTo.value.trim() : "",
            mailSubjectPrefix: subjectPrefix ? subjectPrefix.value.trim() : "[Personal Homepage]"
        };
    }

    async function saveContactSettings() {
        var payload = readContactSettingsForm();

        if (payload.smtpPort < 1 || payload.smtpPort > 65535) {
            setError("SMTP port must be between 1 and 65535.");
            return;
        }

        // SMTPS 和 STARTTLS 不建议同时开启，优先保留 SMTPS。
        if (payload.smtpUseSsl) {
            payload.smtpUseStarttls = false;
        }

        if (!payload.smtpPass) {
            delete payload.smtpPass;
        }

        setBusy(true);
        try {
            var result = await requestAdmin("/contact-settings", {
                method: "PUT",
                body: JSON.stringify(payload)
            });

            state.contactSettings = Object.assign(createDefaultContactSettings(), result || {});
            var passInput = document.getElementById("smtp-pass-input");
            if (passInput) {
                passInput.value = "";
            }
            setStatus("SMTP settings saved.");
        } catch (error) {
            setError(error.message || "Unable to save SMTP settings.");
        } finally {
            setBusy(false);
        }
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
            await loadMessages("all");
            await loadContactSettings();
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
        state.contentObject = {};
        state.contentText = "{}";
        state.editorMode = "form";
        state.backups = [];
        state.messageFilter = "all";
        state.messageTotal = 0;
        state.messages = [];
        state.activeMessageId = "";
        state.activeMessageDetail = null;
        state.contactSettings = createDefaultContactSettings();
        state.publish = {
            status: "idle",
            startedAt: null,
            finishedAt: null,
            lastError: null,
            lastOutput: null
        };
        setStatus("Logged out. This page does not persist password.");
    }

    function syncJsonEditorToObject() {
        var editor = document.getElementById("json-editor");
        var nextText = editor ? editor.value : state.contentText;
        var parsed;

        try {
            parsed = JSON.parse(nextText);
        } catch (error) {
            setError("JSON parse error: " + error.message);
            return false;
        }

        state.contentObject = deepClone(parsed);
        state.contentText = JSON.stringify(parsed, null, 2);
        return true;
    }

    function switchEditorMode(nextMode) {
        if (nextMode !== "form" && nextMode !== "json") {
            return;
        }

        if (nextMode === state.editorMode) {
            return;
        }

        if (nextMode === "form" && state.editorMode === "json") {
            if (!syncJsonEditorToObject()) {
                return;
            }
        }

        if (nextMode === "json") {
            state.contentText = JSON.stringify(state.contentObject || {}, null, 2);
        }

        state.editorMode = nextMode;
        render();
    }

    function updateFieldFromElement(element) {
        var rawPath = element.getAttribute("data-field-path") || "";
        var rawType = element.getAttribute("data-field-type") || "string";
        var path = parsePath(rawPath);
        var nextValue;

        if (rawType === "boolean") {
            nextValue = Boolean(element.checked);
        } else if (rawType === "number") {
            var parsed = Number(element.value);
            nextValue = Number.isFinite(parsed) ? parsed : 0;
        } else {
            nextValue = element.value;
        }

        state.contentObject = setAtPath(state.contentObject, path, nextValue);
        state.contentText = JSON.stringify(state.contentObject, null, 2);
    }

    function addArrayItem(pathText) {
        var path = parsePath(pathText);
        var arrayValue = getAtPath(state.contentObject, path);

        if (!Array.isArray(arrayValue)) {
            return;
        }

        var sample = arrayValue.length > 0 ? arrayValue[arrayValue.length - 1] : "";
        var nextItems = arrayValue.slice();
        nextItems.push(defaultValueFromSample(sample));

        state.contentObject = setAtPath(state.contentObject, path, nextItems);
        state.contentText = JSON.stringify(state.contentObject, null, 2);
        render();
    }

    function removeArrayItem(pathText, indexText) {
        var path = parsePath(pathText);
        var index = Number(indexText);
        var arrayValue = getAtPath(state.contentObject, path);

        if (!Array.isArray(arrayValue)) {
            return;
        }

        if (!Number.isInteger(index) || index < 0 || index >= arrayValue.length) {
            return;
        }

        var nextItems = arrayValue.slice();
        nextItems.splice(index, 1);
        state.contentObject = setAtPath(state.contentObject, path, nextItems);
        state.contentText = JSON.stringify(state.contentObject, null, 2);
        render();
    }

    async function saveContent() {
        if (!state.activeSection) {
            return;
        }

        if (state.editorMode === "json" && !syncJsonEditorToObject()) {
            return;
        }

        var payloadObject = deepClone(state.contentObject);
        state.contentText = JSON.stringify(payloadObject, null, 2);
        setBusy(true);

        try {
            var result = await requestAdmin("/content/" + encodeURIComponent(state.activeSection), {
                method: "PUT",
                body: JSON.stringify({ content: payloadObject })
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

        var messagesRefreshButton = document.getElementById("messages-refresh-button");
        if (messagesRefreshButton) {
            messagesRefreshButton.addEventListener("click", function () {
                void refreshMessages();
            });
        }

        var smtpSaveButton = document.getElementById("smtp-save-button");
        if (smtpSaveButton) {
            smtpSaveButton.addEventListener("click", function () {
                void saveContactSettings();
            });
        }

        var smtpSslInput = document.getElementById("smtp-use-ssl-input");
        var smtpStarttlsInput = document.getElementById("smtp-use-starttls-input");
        if (smtpSslInput && smtpStarttlsInput) {
            smtpSslInput.addEventListener("change", function () {
                if (smtpSslInput.checked) {
                    smtpStarttlsInput.checked = false;
                }
            });

            smtpStarttlsInput.addEventListener("change", function () {
                if (smtpStarttlsInput.checked) {
                    smtpSslInput.checked = false;
                }
            });
        }

        var formModeButton = document.getElementById("editor-mode-form");
        if (formModeButton) {
            formModeButton.addEventListener("click", function () {
                switchEditorMode("form");
            });
        }

        var jsonModeButton = document.getElementById("editor-mode-json");
        if (jsonModeButton) {
            jsonModeButton.addEventListener("click", function () {
                switchEditorMode("json");
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

        var fieldInputs = document.querySelectorAll("[data-field-path]");
        fieldInputs.forEach(function (input) {
            input.addEventListener("input", function () {
                updateFieldFromElement(input);
            });

            input.addEventListener("change", function () {
                updateFieldFromElement(input);
            });
        });

        var arrayAddButtons = document.querySelectorAll("[data-array-add]");
        arrayAddButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var pathText = button.getAttribute("data-array-add") || "";
                addArrayItem(pathText);
            });
        });

        var arrayRemoveButtons = document.querySelectorAll("[data-array-remove]");
        arrayRemoveButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var pathText = button.getAttribute("data-array-remove") || "";
                var indexText = button.getAttribute("data-array-index") || "";
                removeArrayItem(pathText, indexText);
            });
        });

        var jsonEditor = document.getElementById("json-editor");
        if (jsonEditor) {
            jsonEditor.addEventListener("input", function () {
                state.contentText = jsonEditor.value;
            });
        }

        var apiKeyInput = document.getElementById("api-key-input");
        if (apiKeyInput) {
            apiKeyInput.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    void connectWithKey();
                }
            });
        }

        var messageFilterButtons = document.querySelectorAll("[data-message-filter]");
        messageFilterButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var filter = button.getAttribute("data-message-filter") || "all";
                void refreshMessages(filter);
            });
        });

        var messageButtons = document.querySelectorAll("[data-message-id]");
        messageButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var messageId = button.getAttribute("data-message-id") || "";
                void selectMessage(messageId);
            });
        });

        var processButton = document.getElementById("message-process-button");
        if (processButton) {
            processButton.addEventListener("click", function () {
                void markMessageProcessed();
            });
        }
    }

    function renderPrimitiveNode(label, value, path) {
        var rawPath = pathToString(path);
        var escapedPath = escapeHtml(rawPath);
        var escapedLabel = escapeHtml(label);
        var disabledAttr = state.busy ? "disabled" : "";

        if (typeof value === "boolean") {
            return ""
                + "<label class=\"field checkbox-field\">"
                + "<span class=\"field-label\">" + escapedLabel + "</span>"
                + "<input type=\"checkbox\" data-field-path=\"" + escapedPath + "\" data-field-type=\"boolean\" "
                + (value ? "checked " : "")
                + disabledAttr
                + ">"
                + "</label>";
        }

        if (typeof value === "number") {
            return ""
                + "<label class=\"field\">"
                + "<span class=\"field-label\">" + escapedLabel + "</span>"
                + "<input type=\"number\" data-field-path=\"" + escapedPath + "\" data-field-type=\"number\" value=\""
                + escapeHtml(String(value))
                + "\" "
                + disabledAttr
                + ">"
                + "</label>";
        }

        var normalized = value == null ? "" : String(value);
        var useTextarea = normalized.length > 120 || normalized.indexOf("\n") >= 0;

        if (useTextarea) {
            return ""
                + "<label class=\"field\">"
                + "<span class=\"field-label\">" + escapedLabel + "</span>"
                + "<textarea data-field-path=\"" + escapedPath + "\" data-field-type=\"string\" "
                + disabledAttr
                + ">"
                + escapeHtml(normalized)
                + "</textarea>"
                + "</label>";
        }

        return ""
            + "<label class=\"field\">"
            + "<span class=\"field-label\">" + escapedLabel + "</span>"
            + "<input type=\"text\" data-field-path=\"" + escapedPath + "\" data-field-type=\"string\" value=\""
            + escapeHtml(normalized)
            + "\" "
            + disabledAttr
            + ">"
            + "</label>";
    }

    function renderFormNode(label, value, path, depth) {
        var depthLevel = Math.min(depth, 4);
        var disabledAttr = state.busy ? "disabled" : "";

        if (Array.isArray(value)) {
            var arrayPath = pathToString(path);
            var itemsHtml = value.map(function (item, index) {
                return ""
                    + "<div class=\"array-item\">"
                    + "<div class=\"array-item-header\">"
                    + "<span>Item " + String(index + 1) + "</span>"
                    + "<button class=\"danger\" type=\"button\" data-array-remove=\"" + escapeHtml(arrayPath) + "\" data-array-index=\"" + String(index) + "\" " + disabledAttr + ">Remove</button>"
                    + "</div>"
                    + renderFormNode(label + "[" + String(index) + "]", item, path.concat(index), depth + 1)
                    + "</div>";
            }).join("");

            if (!itemsHtml) {
                itemsHtml = "<p class=\"admin-subtitle\">Array is empty.</p>";
            }

            return ""
                + "<section class=\"node depth-" + String(depthLevel) + "\">"
                + "<div class=\"node-title-row\">"
                + "<h4>" + escapeHtml(label) + "</h4>"
                + "<button class=\"secondary\" type=\"button\" data-array-add=\"" + escapeHtml(arrayPath) + "\" " + disabledAttr + ">Add Item</button>"
                + "</div>"
                + "<div class=\"array-list\">" + itemsHtml + "</div>"
                + "</section>";
        }

        if (isObject(value)) {
            var keys = Object.keys(value);
            var childrenHtml = keys.length === 0
                ? "<p class=\"admin-subtitle\">Object is empty.</p>"
                : keys.map(function (key) {
                    return renderFormNode(key, value[key], path.concat(key), depth + 1);
                }).join("");

            return ""
                + "<section class=\"node depth-" + String(depthLevel) + "\">"
                + "<div class=\"node-title-row\"><h4>" + escapeHtml(label) + "</h4></div>"
                + "<div class=\"node-children\">" + childrenHtml + "</div>"
                + "</section>";
        }

        return renderPrimitiveNode(label, value, path);
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

    function getPublishStatusClass(status) {
        if (status === "running") {
            return "running";
        }

        if (status === "success") {
            return "success";
        }

        if (status === "failed") {
            return "failed";
        }

        return "idle";
    }

    function renderPublishStatusBlock() {
        var status = state.publish.status || "idle";
        var statusClass = getPublishStatusClass(status);

        return ""
            + "<section class=\"publish-status-block " + statusClass + "\">"
            + "<div class=\"publish-status-head\">"
            + "<span class=\"publish-status-label\">Publish</span>"
            + "<span class=\"publish-status-chip " + statusClass + "\">" + escapeHtml(status) + "</span>"
            + "</div>"
            + "<div class=\"publish-status-row\">"
            + "<span>Started: " + escapeHtml(formatTime(state.publish.startedAt)) + "</span>"
            + "<span>Finished: " + escapeHtml(formatTime(state.publish.finishedAt)) + "</span>"
            + "</div>"
            + "</section>";
    }

    function renderPublishLogsBlock() {
        var blocks = [];

        if (state.publish.lastError) {
            blocks.push(
                ""
                + "<section class=\"publish-log-block error\">"
                + "<h3 class=\"publish-log-title\">Error</h3>"
                + "<pre class=\"mono-output mono-scrollable\">" + escapeHtml(state.publish.lastError) + "</pre>"
                + "</section>"
            );
        }

        if (state.publish.lastOutput) {
            blocks.push(
                ""
                + "<section class=\"publish-log-block\">"
                + "<h3 class=\"publish-log-title\">Output</h3>"
                + "<pre class=\"mono-output mono-scrollable\">" + escapeHtml(state.publish.lastOutput) + "</pre>"
                + "</section>"
            );
        }

        if (blocks.length === 0) {
            return "<p class=\"admin-subtitle publish-empty\">No publish logs yet.</p>";
        }

        return "<div class=\"publish-logs\">" + blocks.join("") + "</div>";
    }

    function renderMessageStatusChip(status) {
        var normalized = String(status || "new");
        var statusClass = normalized === "processed" ? "processed" : "new";
        return "<span class=\"message-chip " + statusClass + "\">" + escapeHtml(normalized) + "</span>";
    }

    function renderContactSettingsBlock() {
        var settings = Object.assign(createDefaultContactSettings(), state.contactSettings || {});
        var passConfiguredText = settings.smtpPassConfigured ? "Configured" : "Not configured";

        return ""
            + "<section class=\"smtp-panel\">"
            + "<div class=\"smtp-panel-head\">"
            + "<h3 class=\"panel-subhead\">SMTP Settings</h3>"
            + "<span class=\"smtp-pass-indicator\">Password: " + escapeHtml(passConfiguredText) + "</span>"
            + "</div>"
            + "<p class=\"admin-subtitle\">You can update mail delivery settings directly here. Leave password blank to keep current secret.</p>"
            + "<div class=\"smtp-grid\">"
            + "<label class=\"field\"><span class=\"field-label\">SMTP Host</span><input id=\"smtp-host-input\" type=\"text\" value=\"" + escapeHtml(settings.smtpHost || "") + "\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"field\"><span class=\"field-label\">SMTP Port</span><input id=\"smtp-port-input\" type=\"number\" min=\"1\" max=\"65535\" value=\"" + escapeHtml(String(settings.smtpPort || 465)) + "\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"field\"><span class=\"field-label\">SMTP User</span><input id=\"smtp-user-input\" type=\"text\" value=\"" + escapeHtml(settings.smtpUser || "") + "\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"field\"><span class=\"field-label\">SMTP Password</span><input id=\"smtp-pass-input\" type=\"password\" autocomplete=\"new-password\" placeholder=\"Leave blank to keep\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"field\"><span class=\"field-label\">Mail From</span><input id=\"mail-from-input\" type=\"text\" value=\"" + escapeHtml(settings.mailFrom || "") + "\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"field\"><span class=\"field-label\">Mail To</span><input id=\"mail-to-input\" type=\"text\" value=\"" + escapeHtml(settings.mailTo || "") + "\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"field smtp-span-2\"><span class=\"field-label\">Subject Prefix</span><input id=\"mail-subject-prefix-input\" type=\"text\" value=\"" + escapeHtml(settings.mailSubjectPrefix || "") + "\" " + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"checkbox-field\"><span class=\"field-label\">Use SSL (465)</span><input id=\"smtp-use-ssl-input\" type=\"checkbox\" " + (settings.smtpUseSsl ? "checked " : "") + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"checkbox-field\"><span class=\"field-label\">Use STARTTLS</span><input id=\"smtp-use-starttls-input\" type=\"checkbox\" " + (settings.smtpUseStarttls ? "checked " : "") + (state.busy ? "disabled" : "") + "></label>"
            + "<label class=\"checkbox-field smtp-span-2\"><span class=\"field-label\">Placeholder Mode (disable real sending)</span><input id=\"contact-placeholder-mode-input\" type=\"checkbox\" " + (settings.contactPlaceholderMode ? "checked " : "") + (state.busy ? "disabled" : "") + "></label>"
            + "</div>"
            + "<div class=\"smtp-actions\">"
            + "<button id=\"smtp-save-button\" class=\"secondary\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Save SMTP Settings</button>"
            + "</div>"
            + "</section>";
    }

    function renderMessagesBlock() {
        var filter = state.messageFilter || "all";
        var filterButtons = [
            { key: "all", label: "All" },
            { key: "new", label: "New" },
            { key: "processed", label: "Processed" }
        ].map(function (item) {
            var klass = filter === item.key ? "active" : "ghost";
            return ""
                + "<button type=\"button\" class=\"" + klass + "\" data-message-filter=\"" + escapeHtml(item.key) + "\" "
                + (state.busy ? "disabled" : "")
                + ">"
                + escapeHtml(item.label)
                + "</button>";
        }).join("");

        var listHtml = "";
        if (state.messages.length === 0) {
            listHtml = "<p class=\"admin-subtitle\">No messages in this filter.</p>";
        } else {
            listHtml = state.messages.map(function (item) {
                var activeClass = item.id === state.activeMessageId ? "active" : "";
                return ""
                    + "<button type=\"button\" class=\"message-list-item " + activeClass + "\" data-message-id=\"" + escapeHtml(item.id) + "\" "
                    + (state.busy ? "disabled" : "")
                    + ">"
                    + "<div class=\"message-list-head\">"
                    + "<strong>" + escapeHtml(formatTime(item.createdAt)) + "</strong>"
                    + renderMessageStatusChip(item.status)
                    + "</div>"
                    + "<p>" + escapeHtml(item.preview || "-") + "</p>"
                    + "<p class=\"message-list-meta\">"
                    + "Reply: " + escapeHtml(item.wantReply ? "Yes" : "No")
                    + " | "
                    + "Email: " + escapeHtml(item.email || "-")
                    + "</p>"
                    + "</button>";
            }).join("");
        }

        var detailHtml = "<p class=\"admin-subtitle\">Select a message to view details.</p>";
        var processButtonHtml = "";

        if (state.activeMessageDetail) {
            detailHtml = ""
                + "<div class=\"message-detail-grid\">"
                + "<p><strong>ID:</strong> " + escapeHtml(state.activeMessageDetail.id || "-") + "</p>"
                + "<p><strong>Time:</strong> " + escapeHtml(formatTime(state.activeMessageDetail.createdAt)) + "</p>"
                + "<p><strong>Name:</strong> " + escapeHtml(state.activeMessageDetail.name || "-") + "</p>"
                + "<p><strong>Email:</strong> " + escapeHtml(state.activeMessageDetail.email || "-") + "</p>"
                + "<p><strong>Phone:</strong> " + escapeHtml(state.activeMessageDetail.phone || "-") + "</p>"
                + "<p><strong>Want Reply:</strong> " + escapeHtml(state.activeMessageDetail.wantReply ? "Yes" : "No") + "</p>"
                + "<p><strong>Status:</strong> " + escapeHtml(state.activeMessageDetail.status || "new") + "</p>"
                + "<p><strong>Processed:</strong> " + escapeHtml(formatTime(state.activeMessageDetail.processedAt)) + "</p>"
                + "<p><strong>IP Hash:</strong> " + escapeHtml(state.activeMessageDetail.ipHash || "-") + "</p>"
                + "</div>"
                + "<h4 class=\"panel-subhead message-content-title\">Message</h4>"
                + "<pre class=\"mono-output mono-scrollable\">" + escapeHtml(state.activeMessageDetail.content || "") + "</pre>";

            if (state.activeMessageDetail.status !== "processed") {
                processButtonHtml = ""
                    + "<button id=\"message-process-button\" class=\"secondary\" type=\"button\" "
                    + (state.busy ? "disabled" : "")
                    + ">Mark as processed</button>";
            }
        }

        return ""
            + "<section class=\"message-panel\">"
            + "<div class=\"message-panel-head\">"
            + "<h3 class=\"panel-subhead\">Messages (" + escapeHtml(String(state.messageTotal || 0)) + ")</h3>"
            + "<button id=\"messages-refresh-button\" class=\"ghost\" type=\"button\" " + (state.busy ? "disabled" : "") + ">Refresh</button>"
            + "</div>"
            + "<div class=\"message-filter-row\">" + filterButtons + "</div>"
            + "<div class=\"message-layout\">"
            + "<div class=\"message-list\">" + listHtml + "</div>"
            + "<div class=\"message-detail\">"
            + processButtonHtml
            + detailHtml
            + "</div>"
            + "</div>"
            + "</section>";
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

        var publishStatusBlock = renderPublishStatusBlock();
        var publishLogsBlock = renderPublishLogsBlock();
        var messagesBlock = renderMessagesBlock();
        var contactSettingsBlock = renderContactSettingsBlock();

        var editorBody = "";
        if (state.editorMode === "json") {
            editorBody = ""
                + "<label class=\"field\" for=\"json-editor\">"
                + "<span class=\"field-label\">Section JSON</span>"
                + "<textarea id=\"json-editor\" class=\"json-editor\" spellcheck=\"false\" " + (state.busy ? "disabled" : "") + "></textarea>"
                + "</label>";
        } else {
            editorBody = ""
                + "<div class=\"field\">"
                + "<span class=\"field-label\">Section Fields</span>"
                + "<div class=\"form-editor-root\">"
                + renderFormNode(state.activeSection || "section", state.contentObject || {}, [], 0)
                + "</div>"
                + "</div>";
        }

        root.innerHTML = ""
            + "<div class=\"admin-shell\">"
            + "<header class=\"admin-header\">"
            + "<h1 class=\"admin-title\">Personal Homepage Admin</h1>"
            + "<p class=\"admin-subtitle\">Stable mode: plain JavaScript, no external CDN dependencies. Nested fields for homepage V2 are supported directly in the editor.</p>"
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
            + "</div>"
            + "<div class=\"editor-mode-toggle\">"
            + "<button id=\"editor-mode-form\" type=\"button\" class=\"" + (state.editorMode === "form" ? "active" : "ghost") + "\" " + (state.busy ? "disabled" : "") + ">Field Editor</button>"
            + "<button id=\"editor-mode-json\" type=\"button\" class=\"" + (state.editorMode === "json" ? "active" : "ghost") + "\" " + (state.busy ? "disabled" : "") + ">Raw JSON</button>"
            + "</div>"
            + publishStatusBlock
            + publishLogsBlock
            + messagesBlock
            + contactSettingsBlock
            + editorBody
            + "</main>"
            + "</div>"
            + "</div>";

        if (state.editorMode === "json") {
            var editor = document.getElementById("json-editor");
            if (editor) {
                editor.value = state.contentText || "";
            }
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
