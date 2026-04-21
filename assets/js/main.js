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

        form.addEventListener("submit", function (event) {
            event.preventDefault();

            var content = contentInput ? contentInput.value.trim() : "";
            if (!content) {
                setContactStatus(statusElement, "Message content is required.", "error");
                return;
            }

            var payload = {
                content: content,
                name: nameInput ? nameInput.value.trim() : "",
                email: emailInput ? emailInput.value.trim() : "",
                phone: phoneInput ? phoneInput.value.trim() : "",
                wantReply: wantReplyInput ? Boolean(wantReplyInput.checked) : false,
                website: websiteInput ? websiteInput.value.trim() : ""
            };

            if (submitButton) {
                submitButton.disabled = true;
            }
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
                    if (submitButton) {
                        submitButton.disabled = false;
                    }
                });
        });
    }

    initContactForm();
})();
