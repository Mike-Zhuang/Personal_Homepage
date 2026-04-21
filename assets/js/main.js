(function () {
    "use strict";

    var revealElements = document.querySelectorAll(".reveal");

    if ("IntersectionObserver" in window) {
        var revealObserver = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting) {
                        return;
                    }

                    var index = Number(entry.target.getAttribute("data-reveal-index") || "0");
                    entry.target.style.transitionDelay = String(index * 40) + "ms";
                    entry.target.classList.add("in-view");
                    revealObserver.unobserve(entry.target);
                });
            },
            { threshold: 0.18 }
        );

        revealElements.forEach(function (element) {
            revealObserver.observe(element);
        });
    } else {
        revealElements.forEach(function (element) {
            element.classList.add("in-view");
        });
    }

    var body = document.body;
    var navToggle = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector("[data-nav]");
    var navBackdrop = document.querySelector("[data-nav-backdrop]");
    var navLinks = document.querySelectorAll("[data-nav-link]");

    function setNavOpen(nextOpen) {
        var isOpen = Boolean(nextOpen);

        body.setAttribute("data-nav-open", isOpen ? "true" : "false");

        if (navToggle) {
            navToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        }

        if (nav) {
            nav.setAttribute("data-open", isOpen ? "true" : "false");
        }
    }

    if (navToggle && nav) {
        setNavOpen(false);

        navToggle.addEventListener("click", function () {
            var expanded = navToggle.getAttribute("aria-expanded") === "true";
            setNavOpen(!expanded);
        });

        if (navBackdrop) {
            navBackdrop.addEventListener("click", function () {
                setNavOpen(false);
            });
        }

        navLinks.forEach(function (link) {
            link.addEventListener("click", function () {
                setNavOpen(false);
            });
        });

        window.addEventListener("resize", function () {
            if (window.innerWidth > 834) {
                setNavOpen(false);
            }
        });
    }

    function resolveApiBase() {
        var configuredBase = body ? String(body.getAttribute("data-api-base") || "").trim() : "";
        if (configuredBase) {
            return configuredBase.replace(/\/$/, "");
        }

        var hostname = window.location.hostname;
        var isLocalHost = hostname === "127.0.0.1" || hostname === "localhost";
        var isHugoDevPort = window.location.port === "1313";

        if (isLocalHost && isHugoDevPort) {
            return "http://127.0.0.1:8000";
        }

        return "";
    }

    function buildApiUrl(path) {
        var normalizedPath = path.charAt(0) === "/" ? path : ("/" + path);
        return resolveApiBase() + normalizedPath;
    }

    function setContactStatus(statusElement, message, level) {
        if (!statusElement) {
            return;
        }

        statusElement.textContent = message || "";
        statusElement.classList.remove("success", "error");

        if (level === "success" || level === "error") {
            statusElement.classList.add(level);
        }
    }

    function readErrorDetail(responsePayload, fallbackText) {
        if (!responsePayload) {
            return fallbackText;
        }

        if (typeof responsePayload.detail === "string" && responsePayload.detail) {
            return responsePayload.detail;
        }

        if (typeof responsePayload.message === "string" && responsePayload.message) {
            return responsePayload.message;
        }

        return fallbackText;
    }

    function initContactForm() {
        var form = document.getElementById("contact-form");
        if (!form) {
            return;
        }

        var contentInput = document.getElementById("contact-content");
        var nameInput = document.getElementById("contact-name");
        var emailInput = document.getElementById("contact-email");
        var phoneInput = document.getElementById("contact-phone");
        var wantReplyInput = document.getElementById("contact-want-reply");
        var websiteInput = document.getElementById("contact-website");
        var submitButton = document.getElementById("contact-submit");
        var statusElement = document.getElementById("contact-form-status");
        var pendingRequest = false;

        function looksLikeEmail(value) {
            if (!value || value.length > 254 || value.indexOf("..") >= 0) {
                return false;
            }

            return /^[A-Za-z0-9](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$/.test(value);
        }

        function looksLikePhone(value) {
            if (!value) {
                return false;
            }

            var compact = value.replace(/[\s()\-]+/g, "");
            var digitCount = (compact.match(/\d/g) || []).length;

            if ((!compact.startsWith("+") && /\D/.test(compact)) || digitCount < 6 || digitCount > 20) {
                return false;
            }

            return /^\+?[0-9][0-9()\-\s]{5,31}$/.test(value);
        }

        function hashText(value) {
            var hash = 2166136261;
            for (var index = 0; index < value.length; index += 1) {
                hash ^= value.charCodeAt(index);
                hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
            }

            return (hash >>> 0).toString(16);
        }

        function buildClientMeta() {
            var timezone = "";
            var connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection || null;
            try {
                timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
            } catch (_error) {
                timezone = "";
            }

            var languageList = Array.isArray(navigator.languages) ? navigator.languages.join(", ") : "";
            var fingerprintSource = [
                timezone,
                navigator.language || "",
                languageList,
                String(window.screen && window.screen.width || 0) + "x" + String(window.screen && window.screen.height || 0),
                String(window.innerWidth || 0) + "x" + String(window.innerHeight || 0),
                navigator.platform || "",
                String(navigator.maxTouchPoints || 0),
                String(navigator.hardwareConcurrency || 0),
                String(navigator.deviceMemory || 0)
            ].join("|");

            return {
                timezone: timezone,
                language: navigator.language || "",
                languages: languageList,
                networkType: connection && connection.effectiveType ? String(connection.effectiveType) : "",
                connectionType: connection && connection.type ? String(connection.type) : "",
                downlink: connection && typeof connection.downlink === "number" ? String(connection.downlink) : "",
                rtt: connection && typeof connection.rtt === "number" ? String(connection.rtt) : "",
                onlineStatus: typeof navigator.onLine === "boolean" ? (navigator.onLine ? "online" : "offline") : "",
                screenResolution: String(window.screen && window.screen.width || 0) + "x" + String(window.screen && window.screen.height || 0),
                viewportSize: String(window.innerWidth || 0) + "x" + String(window.innerHeight || 0),
                refererPath: window.location.pathname || "",
                pageUrl: window.location.href || "",
                referrer: document.referrer || "",
                platform: navigator.platform || "",
                cookieEnabled: Boolean(navigator.cookieEnabled),
                touchPoints: navigator.maxTouchPoints || 0,
                hardwareConcurrency: navigator.hardwareConcurrency || 0,
                deviceMemory: navigator.deviceMemory || 0,
                colorScheme: window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
                fingerprint: hashText(fingerprintSource)
            };
        }

        form.addEventListener("submit", function (event) {
            event.preventDefault();

            if (pendingRequest) {
                setContactStatus(statusElement, "Message is already sending. Please wait.", "error");
                return;
            }

            var content = contentInput ? contentInput.value.trim() : "";
            if (!content) {
                setContactStatus(statusElement, "Message content is required.", "error");
                return;
            }

            if (content.length > 2000) {
                setContactStatus(statusElement, "Message must be 2000 characters or fewer.", "error");
                return;
            }

            var nameValue = nameInput ? nameInput.value.trim() : "";
            var emailValue = emailInput ? emailInput.value.trim() : "";
            var phoneValue = phoneInput ? phoneInput.value.trim() : "";
            var wantReply = wantReplyInput ? Boolean(wantReplyInput.checked) : false;

            if (nameValue && nameValue.length > 80) {
                setContactStatus(statusElement, "Name must be 80 characters or fewer.", "error");
                return;
            }

            if (emailValue && !looksLikeEmail(emailValue)) {
                setContactStatus(statusElement, "Email format is invalid.", "error");
                return;
            }

            if (phoneValue && !looksLikePhone(phoneValue)) {
                setContactStatus(statusElement, "Phone format is invalid.", "error");
                return;
            }

            if (wantReply && !emailValue && !phoneValue) {
                setContactStatus(statusElement, "Email or phone is required if you want a reply.", "error");
                return;
            }

            var payload = {
                content: content,
                name: nameValue,
                email: emailValue,
                phone: phoneValue,
                wantReply: wantReply,
                website: websiteInput ? websiteInput.value.trim() : "",
                clientMeta: buildClientMeta()
            };

            if (submitButton) {
                submitButton.disabled = true;
            }
            pendingRequest = true;
            setContactStatus(statusElement, "Sending...", "");

            fetch(buildApiUrl("/api/contact"), {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            })
                .then(function (response) {
                    return response.text().then(function (text) {
                        var responsePayload = {};
                        if (text) {
                            try {
                                responsePayload = JSON.parse(text);
                            } catch (_error) {
                                responsePayload = { detail: text };
                            }
                        }

                        if (!response.ok) {
                            throw new Error(readErrorDetail(responsePayload, "Unable to send message right now."));
                        }

                        return responsePayload;
                    });
                })
                .then(function (_payload) {
                    if (contentInput) {
                        contentInput.value = "";
                    }
                    if (nameInput) {
                        nameInput.value = "";
                    }
                    if (emailInput) {
                        emailInput.value = "";
                    }
                    if (phoneInput) {
                        phoneInput.value = "";
                    }
                    if (websiteInput) {
                        websiteInput.value = "";
                    }
                    if (wantReplyInput) {
                        wantReplyInput.checked = false;
                    }

                    setContactStatus(statusElement, "Message sent. Thank you.", "success");
                })
                .catch(function (error) {
                    setContactStatus(statusElement, error.message || "Unable to send message right now.", "error");
                })
                .finally(function () {
                    pendingRequest = false;
                    if (submitButton) {
                        submitButton.disabled = false;
                    }
                });
        });
    }

    initContactForm();
})();
