(function () {
    "use strict";

    const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

    function toast(message, level) {
        const container = document.getElementById("toastContainer");
        if (!container) return;
        const el = document.createElement("div");
        el.className = "toast align-items-center border-0";
        const tone = level === "error" ? "text-bg-danger" : level === "warning" ? "text-bg-warning" : "text-bg-success";
        el.classList.add(tone);
        el.setAttribute("role", "alert");
        el.innerHTML = '<div class="d-flex"><div class="toast-body"></div>' +
            '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
        el.querySelector(".toast-body").textContent = message;
        container.appendChild(el);
        const instance = new bootstrap.Toast(el, { delay: 4000 });
        instance.show();
        el.addEventListener("hidden.bs.toast", () => el.remove());
    }

    async function api(url, options) {
        options = options || {};
        const headers = Object.assign({ "X-CSRF-Token": CSRF }, options.headers || {});
        if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {
            headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(options.body);
        }
        const response = await fetch(url, Object.assign({}, options, { headers }));
        const contentType = response.headers.get("content-type") || "";
        const payload = contentType.includes("application/json") ? await response.json() : await response.text();
        if (!response.ok) {
            const message = (payload && payload.detail) || (payload && payload.error) || response.statusText;
            const failure = new Error(message || "Request failed");
            failure.status = response.status;
            failure.payload = payload;
            throw failure;
        }
        return payload;
    }

    function applyTheme(dark) {
        document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
        try { localStorage.setItem("piweb-theme", dark ? "dark" : "light"); } catch (e) {}
        const icon = document.getElementById("themeIcon");
        if (icon) icon.className = dark ? "bi bi-sun" : "bi bi-moon-stars";
        document.dispatchEvent(new CustomEvent("piweb:theme", { detail: { dark } }));
    }

    document.addEventListener("DOMContentLoaded", function () {
        const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
        applyTheme(dark);

        const toggle = document.getElementById("themeToggle");
        if (toggle) {
            toggle.addEventListener("click", function (event) {
                event.preventDefault();
                applyTheme(document.documentElement.getAttribute("data-bs-theme") !== "dark");
            });
        }

        // Ask before a destructive link or button.
        //
        // Forms put data-confirm on the <form> element, whose question belongs
        // to the submit event: matching clicks here fired it whenever a field
        // inside the form was clicked.
        document.body.addEventListener("click", function (event) {
            const target = event.target.closest("[data-confirm]");
            if (!target || target.tagName === "FORM") return;
            if (!window.confirm(target.getAttribute("data-confirm"))) {
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            const again = target.getAttribute("data-confirm-again");
            if (again && !window.confirm(again)) {
                event.preventDefault();
                event.stopPropagation();
            }
        });

        // Confirmation for <form data-confirm>, including Enter-to-submit.
        document.body.addEventListener("submit", function (event) {
            const form = event.target;
            if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) return;
            if (form.dataset.confirmed === "1") {
                delete form.dataset.confirmed;
                return;
            }
            event.preventDefault();
            if (!window.confirm(form.getAttribute("data-confirm"))) return;
            const again = form.getAttribute("data-confirm-again");
            if (again && !window.confirm(again)) return;
            form.dataset.confirmed = "1";
            if (typeof form.requestSubmit === "function") form.requestSubmit();
            else form.submit();
        });

        // HTMX error surfacing.
        document.body.addEventListener("htmx:responseError", function (event) {
            const status = event.detail.xhr.status;
            if (status === 401) {
                window.location.href = "/login";
                return;
            }
            toast("Request failed (" + status + ")", "error");
        });
        document.body.addEventListener("htmx:sendError", function () {
            toast("Network error — is the panel reachable?", "error");
        });
    });

    document.body.addEventListener("htmx:configRequest", function (event) {
        event.detail.headers["X-CSRF-Token"] = CSRF;
    });

    window.PiWeb = { toast, api, csrf: CSRF };
})();
