import React, { useEffect, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);

function resolveApiPrefix() {
    if (typeof window === "undefined") {
        return "/api/admin";
    }

    const hostname = window.location.hostname;
    const isLocalHost = hostname === "127.0.0.1" || hostname === "localhost";
    const isHugoDevPort = window.location.port === "1313";

    // Hugo dev server does not proxy /api by default, so local admin should call FastAPI directly.
    if (isLocalHost && isHugoDevPort) {
        return "http://127.0.0.1:8000/api/admin";
    }

    return "/api/admin";
}

const API_PREFIX = resolveApiPrefix();

function deepClone(value) {
    if (typeof structuredClone === "function") {
        return structuredClone(value);
    }
    return JSON.parse(JSON.stringify(value));
}

function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function pathToString(path) {
    return path.map(String).join(".");
}

function setAtPath(root, path, nextValue) {
    if (path.length === 0) {
        return nextValue;
    }

    const nextRoot = deepClone(root);
    let cursor = nextRoot;

    for (let index = 0; index < path.length - 1; index += 1) {
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

function formatTime(value) {
    if (!value) {
        return "-";
    }

    try {
        return new Date(value).toLocaleString();
    } catch {
        return value;
    }
}

async function requestAdmin(path, options = {}, apiKey = "") {
    const headers = {
        ...(options.headers || {})
    };

    if (apiKey) {
        headers["X-Admin-API-Key"] = apiKey;
    }

    if (options.body) {
        headers["Content-Type"] = "application/json";
    }

    const response = await fetch(`${API_PREFIX}${path}`, {
        ...options,
        headers
    });

    const text = await response.text();
    let result = {};

    if (text) {
        try {
            result = JSON.parse(text);
        } catch {
            result = { detail: text };
        }
    }

    if (!response.ok) {
        throw new Error(result.detail || `Request failed: ${response.status}`);
    }

    return result;
}

function PrimitiveEditor({ label, value, path, onChange }) {
    const key = pathToString(path);

    if (typeof value === "boolean") {
        return html`
            <label className="field" key=${key}>
                <span className="field-label">${label}</span>
                <input
                    type="checkbox"
                    checked=${Boolean(value)}
                    onChange=${(event) => onChange(path, event.target.checked)}
                />
            </label>
        `;
    }

    if (typeof value === "number") {
        return html`
            <label className="field" key=${key}>
                <span className="field-label">${label}</span>
                <input
                    type="number"
                    value=${Number.isFinite(value) ? String(value) : "0"}
                    onInput=${(event) => {
                const textValue = event.target.value;
                const nextValue = textValue === "" ? 0 : Number(textValue);
                onChange(path, Number.isFinite(nextValue) ? nextValue : 0);
            }}
                />
            </label>
        `;
    }

    const normalized = value == null ? "" : String(value);
    const useTextarea = normalized.length > 120 || normalized.includes("\n");

    if (useTextarea) {
        return html`
            <label className="field" key=${key}>
                <span className="field-label">${label}</span>
                <textarea
                    value=${normalized}
                    onInput=${(event) => onChange(path, event.target.value)}
                ></textarea>
            </label>
        `;
    }

    return html`
        <label className="field" key=${key}>
            <span className="field-label">${label}</span>
            <input
                type="text"
                value=${normalized}
                onInput=${(event) => onChange(path, event.target.value)}
            />
        </label>
    `;
}

function EditorNode({ label, value, path, onChange, depth = 0 }) {
    const depthLevel = Math.min(depth, 4);

    if (Array.isArray(value)) {
        return html`
            <section className=${`node depth-${depthLevel}`}>
                <div className="node-title-row">
                    <h4>${label}</h4>
                    <button
                        className="secondary"
                        type="button"
                        onClick=${() => {
                const sample = value.length > 0 ? value[value.length - 1] : "";
                const nextItems = [...value, defaultValueFromSample(sample)];
                onChange(path, nextItems);
            }}
                    >
                        Add Item
                    </button>
                </div>

                <div className="array-list">
                    ${value.map((item, index) => html`
                        <div className="array-item" key=${pathToString([...path, index])}>
                            <div className="array-item-header">
                                <span>${`Item ${index + 1}`}</span>
                                <button
                                    className="danger"
                                    type="button"
                                    onClick=${() => {
                    const nextItems = [...value];
                    nextItems.splice(index, 1);
                    onChange(path, nextItems);
                }}
                                >
                                    Remove
                                </button>
                            </div>
                            <${EditorNode}
                                label=${Array.isArray(item) || isObject(item) ? "entry" : `${label}[${index}]`}
                                value=${item}
                                path=${[...path, index]}
                                onChange=${onChange}
                                depth=${depth + 1}
                            />
                        </div>
                    `)}
                </div>
            </section>
        `;
    }

    if (isObject(value)) {
        return html`
            <section className=${`node depth-${depthLevel}`}>
                <div className="node-title-row">
                    <h4>${label}</h4>
                </div>
                <div className="node-children">
                    ${Object.entries(value).map(([childKey, childValue]) => html`
                        <${EditorNode}
                            key=${pathToString([...path, childKey])}
                            label=${childKey}
                            value=${childValue}
                            path=${[...path, childKey]}
                            onChange=${onChange}
                            depth=${depth + 1}
                        />
                    `)}
                </div>
            </section>
        `;
    }

    return html`<${PrimitiveEditor} label=${label} value=${value} path=${path} onChange=${onChange} />`;
}

function AdminApp() {
    const [apiKeyInput, setApiKeyInput] = useState(() => sessionStorage.getItem("adminApiKey") || "");
    const [apiKey, setApiKey] = useState(() => sessionStorage.getItem("adminApiKey") || "");
    const [sections, setSections] = useState([]);
    const [activeSection, setActiveSection] = useState("");
    const [content, setContent] = useState(null);
    const [sourceFile, setSourceFile] = useState("");
    const [backups, setBackups] = useState([]);
    const [isBusy, setIsBusy] = useState(false);
    const [statusMessage, setStatusMessage] = useState("Ready.");
    const [errorMessage, setErrorMessage] = useState("");
    const [publishState, setPublishState] = useState({ status: "idle" });

    async function loadSections(nextApiKey) {
        const resolvedKey = nextApiKey ?? apiKey;
        setIsBusy(true);
        setErrorMessage("");

        try {
            const result = await requestAdmin("/sections", {}, resolvedKey);
            const nextSections = result.sections || [];
            setSections(nextSections);

            if (nextSections.length > 0) {
                const target = activeSection || nextSections[0].key;
                setActiveSection(target);
                await loadSection(target, resolvedKey);
            }

            setStatusMessage("Connected.");
        } catch (error) {
            setErrorMessage(error.message || "Unable to load sections.");
        } finally {
            setIsBusy(false);
        }
    }

    async function loadSection(section, nextApiKey) {
        const resolvedKey = nextApiKey ?? apiKey;
        setIsBusy(true);
        setErrorMessage("");

        try {
            const contentResult = await requestAdmin(`/content/${section}`, {}, resolvedKey);
            const backupResult = await requestAdmin(`/backups/${section}`, {}, resolvedKey);

            setContent(contentResult.content);
            setSourceFile(contentResult.sourceFile || "");
            setBackups(backupResult.backups || []);
            setStatusMessage(`Loaded ${section}.`);
        } catch (error) {
            setErrorMessage(error.message || "Unable to load section content.");
        } finally {
            setIsBusy(false);
        }
    }

    async function connect() {
        const nextKey = apiKeyInput.trim();

        if (nextKey) {
            sessionStorage.setItem("adminApiKey", nextKey);
        } else {
            sessionStorage.removeItem("adminApiKey");
        }

        setApiKey(nextKey);
        await loadSections(nextKey);
    }

    async function saveSection() {
        if (!activeSection || !content) {
            return;
        }

        setIsBusy(true);
        setErrorMessage("");

        try {
            const result = await requestAdmin(
                `/content/${activeSection}`,
                {
                    method: "PUT",
                    body: JSON.stringify({ content })
                },
                apiKey
            );

            await loadSection(activeSection, apiKey);
            setStatusMessage(`Saved at ${formatTime(result.updatedAt)}.${result.publishStatus ? ` Publish: ${result.publishStatus}.` : ""}`);
        } catch (error) {
            setErrorMessage(error.message || "Save failed.");
        } finally {
            setIsBusy(false);
        }
    }

    async function triggerPublish() {
        setIsBusy(true);
        setErrorMessage("");

        try {
            const result = await requestAdmin("/publish", { method: "POST" }, apiKey);
            setPublishState(result);
            setStatusMessage(`Publish status: ${result.status}.`);
        } catch (error) {
            setErrorMessage(error.message || "Publish trigger failed.");
        } finally {
            setIsBusy(false);
        }
    }

    async function refreshPublishStatus() {
        try {
            const result = await requestAdmin("/publish/status", {}, apiKey);
            setPublishState(result);
        } catch {
            // ignore transient polling errors
        }
    }

    async function rollbackTo(backupName) {
        if (!activeSection || !backupName) {
            return;
        }

        const ok = window.confirm(`Rollback ${activeSection} using ${backupName}?`);
        if (!ok) {
            return;
        }

        setIsBusy(true);
        setErrorMessage("");

        try {
            const encoded = encodeURIComponent(backupName);
            const result = await requestAdmin(`/rollback/${activeSection}/${encoded}`, { method: "POST" }, apiKey);
            await loadSection(activeSection, apiKey);
            setStatusMessage(`Rollback completed at ${formatTime(result.updatedAt)}.`);
        } catch (error) {
            setErrorMessage(error.message || "Rollback failed.");
        } finally {
            setIsBusy(false);
        }
    }

    function handleValueChange(path, nextValue) {
        setContent((previous) => setAtPath(previous, path, nextValue));
    }

    useEffect(() => {
        if (publishState.status !== "running") {
            return undefined;
        }

        const timer = window.setInterval(() => {
            void refreshPublishStatus();
        }, 2200);

        return () => window.clearInterval(timer);
    }, [publishState.status, apiKey]);

    useEffect(() => {
        void loadSections(apiKey);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return html`
        <div className="admin-shell">
            <header className="admin-header">
                <h1 className="admin-title">Personal Homepage Admin</h1>
                <p className="admin-subtitle">Edit data sections, save changes, and trigger publish from one place.</p>
                <div className="top-actions">
                    <input
                        type="password"
                        placeholder="Optional API key (X-Admin-API-Key)"
                        value=${apiKeyInput}
                        onInput=${(event) => setApiKeyInput(event.target.value)}
                    />
                    <button type="button" onClick=${connect} disabled=${isBusy}>Connect</button>
                    <button
                        className="secondary"
                        type="button"
                        onClick=${() => activeSection && loadSection(activeSection, apiKey)}
                        disabled=${isBusy || !activeSection}
                    >
                        Reload Section
                    </button>
                    <button type="button" onClick=${saveSection} disabled=${isBusy || !content}>Save</button>
                    <button className="secondary" type="button" onClick=${triggerPublish} disabled=${isBusy}>Publish</button>
                </div>
                <div className=${`status-line ${errorMessage ? "error" : ""}`}>
                    ${errorMessage || statusMessage}
                </div>
            </header>

            <div className="admin-layout">
                <aside className="panel">
                    <h2>Sections</h2>
                    <div className="section-list">
                        ${sections.map((item) => html`
                            <button
                                key=${item.key}
                                className=${item.key === activeSection ? "active" : "ghost"}
                                type="button"
                                disabled=${isBusy}
                                onClick=${() => {
            setActiveSection(item.key);
            void loadSection(item.key, apiKey);
        }}
                            >
                                ${item.key}
                            </button>
                        `)}
                    </div>

                    <h3 style=${{ marginTop: "16px" }}>Backups</h3>
                    <div className="backup-list">
                        ${backups.length === 0 ? html`<p className="admin-subtitle">No backups yet.</p>` : backups.map((item) => html`
                            <div className="backup-item" key=${item.name}>
                                <div><strong>${item.name}</strong></div>
                                <div className="backup-meta">${formatTime(item.createdAt)} | ${item.sizeBytes} bytes</div>
                                <button
                                    className="danger"
                                    type="button"
                                    disabled=${isBusy}
                                    onClick=${() => rollbackTo(item.name)}
                                >
                                    Rollback
                                </button>
                            </div>
                        `)}
                    </div>
                </aside>

                <main className="panel">
                    <div className="editor-toolbar">
                        <h2>Editor</h2>
                        <span className="source-file">${sourceFile ? `Source: ${sourceFile}` : "No section loaded."}</span>
                        <span className="source-file">
                            ${`Publish: ${publishState.status || "idle"} | Started: ${formatTime(publishState.startedAt)} | Finished: ${formatTime(publishState.finishedAt)}`}
                        </span>
                    </div>

                    ${publishState.lastError ? html`<div className="status-line error">${publishState.lastError}</div>` : null}
                    ${publishState.lastOutput ? html`<div className="status-line">${publishState.lastOutput}</div>` : null}

                    ${content ? html`
                        <${EditorNode}
                            label=${activeSection || "section"}
                            value=${content}
                            path=${[]}
                            onChange=${handleValueChange}
                        />
                    ` : html`<p className="admin-subtitle">Connect and select a section to start editing.</p>`}
                </main>
            </div>
        </div>
    `;
}

createRoot(document.getElementById("admin-root")).render(html`<${AdminApp} />`);
