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

    if (!navToggle || !nav) {
        return;
    }

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
})();
