(function () {
    var revealElements = document.querySelectorAll(".reveal");

    if ("IntersectionObserver" in window) {
        var revealObserver = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting) {
                        return;
                    }

                    var index = Number(entry.target.getAttribute("data-reveal-index") || "0");
                    entry.target.style.transitionDelay = String(index * 45) + "ms";
                    entry.target.classList.add("in-view");
                    revealObserver.unobserve(entry.target);
                });
            },
            { threshold: 0.22 }
        );

        revealElements.forEach(function (element) {
            revealObserver.observe(element);
        });
    } else {
        revealElements.forEach(function (element) {
            element.classList.add("in-view");
        });
    }

    var form = document.querySelector("[data-contact-form]");
    var statusNode = document.querySelector("[data-form-status]");

    if (!form || !statusNode) {
        return;
    }

    var submitButton = form.querySelector('button[type="submit"]');
    var runtimeConfig = window.__SITE_CONFIG__ || {};
    var baseUrl = (runtimeConfig.apiBaseUrl || "").replace(/\/$/, "");
    var endpoint = baseUrl + "/api/contact";

    form.addEventListener("submit", async function (event) {
        event.preventDefault();

        var formData = new FormData(form);
        var payload = {
            name: String(formData.get("name") || "").trim(),
            email: String(formData.get("email") || "").trim(),
            message: String(formData.get("message") || "").trim()
        };

        if (!payload.name || !payload.email || !payload.message) {
            statusNode.textContent = "Please complete all required fields.";
            return;
        }

        if (submitButton) {
            submitButton.disabled = true;
        }

        statusNode.textContent = "Sending...";

        try {
            var response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            var result = {};
            try {
                result = await response.json();
            } catch (jsonError) {
                result = {};
            }

            if (!response.ok) {
                throw new Error(result.detail || "Submission failed. Please try again.");
            }

            statusNode.textContent = result.message || "Message received.";
            form.reset();
        } catch (error) {
            statusNode.textContent = error.message || "Unable to send message right now.";
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
            }
        }
    });
})();
