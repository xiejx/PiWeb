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

    // ---- theme ---------------------------------------------------------------
    //
    // Three modes: light, dark and system. "system" follows
    // prefers-color-scheme and keeps following it while the page is open — the
    // old behaviour could only be light or dark, and a first visit silently got
    // whichever the OS preferred without saying so.
    //
    // The choice lives in localStorage under "piweb-theme"; a missing or
    // unknown value means system. The inline script in the page head repeats
    // this resolution before the first paint, so a dark panel never flashes
    // white on load.

    const THEME_KEY = "piweb-theme";
    const THEME_MODES = ["light", "dark", "system"];
    const THEME_ICONS = { light: "bi-sun", dark: "bi-moon-stars", system: "bi-circle-half" };
    const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

    function currentTheme() {
        try {
            const saved = localStorage.getItem(THEME_KEY);
            return THEME_MODES.indexOf(saved) >= 0 ? saved : "system";
        } catch (e) {
            return "system";
        }
    }

    function themeIsDark(mode) {
        if (mode === "light") return false;
        if (mode === "dark") return true;
        return darkQuery.matches;
    }

    function applyTheme(mode) {
        const theme = THEME_MODES.indexOf(mode) >= 0 ? mode : "system";
        const dark = themeIsDark(theme);
        const root = document.documentElement;
        root.setAttribute("data-bs-theme", dark ? "dark" : "light");
        root.setAttribute("data-theme-mode", theme);

        document.querySelectorAll("[data-theme-icon]").forEach(function (icon) {
            // Keep spacing helpers such as me-1; only the icon name changes.
            const extra = Array.from(icon.classList).filter(function (name) {
                return name !== "bi" && name.indexOf("bi-") !== 0;
            });
            icon.className = ["bi", THEME_ICONS[theme]].concat(extra).join(" ");
        });
        document.querySelectorAll("[data-theme-set]").forEach(function (item) {
            const active = item.getAttribute("data-theme-set") === theme;
            item.classList.toggle("active", active);
            item.setAttribute("aria-current", active ? "true" : "false");
        });

        document.dispatchEvent(new CustomEvent("piweb:theme", {
            detail: { dark: dark, mode: theme }
        }));
    }

    function setTheme(mode) {
        const theme = THEME_MODES.indexOf(mode) >= 0 ? mode : "system";
        try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
        applyTheme(theme);
    }

    // ---- confirmation dialog -------------------------------------------------
    //
    // A Bootstrap modal instead of window.confirm(): it matches the panel, it
    // works in dark mode, and on a phone the native dialog is easy to dismiss
    // by accident. Resolves to true only when the confirm button is pressed.
    //
    // The dialog markup lives in base.html; when it is missing (a page that
    // does not extend the layout) we fall back to the native prompt.

    let confirmModal = null;
    let confirmResolve = null;

    function settleConfirm(answer) {
        const resolve = confirmResolve;
        confirmResolve = null;
        if (resolve) resolve(answer);
    }

    function confirmDialog(message, options) {
        options = options || {};
        const el = document.getElementById("piwebConfirmModal");
        if (!el || !window.bootstrap || !window.bootstrap.Modal) {
            return Promise.resolve(window.confirm(message));
        }

        // Two questions cannot wait at the same time: the older one is declined.
        settleConfirm(false);

        const title = el.querySelector("#piwebConfirmTitle span");
        const body = el.querySelector("#piwebConfirmBody");
        const ok = el.querySelector("#piwebConfirmOk");
        if (title) title.textContent = options.title || el.dataset.defaultTitle || title.textContent;
        if (body) body.textContent = message || "";
        if (ok) {
            ok.textContent = options.ok || el.dataset.defaultOk || ok.textContent;
            ok.className = "btn " + (options.okClass || "btn-danger");
        }

        return new Promise(function (resolve) {
            confirmResolve = resolve;
            if (!confirmModal) {
                confirmModal = new bootstrap.Modal(el);
                // Escape, the close button and Cancel all hide the dialog
                // without calling the OK handler, so the answer defaults to no.
                el.addEventListener("hidden.bs.modal", function () { settleConfirm(false); });
                el.querySelector("#piwebConfirmOk").addEventListener("click", function () {
                    settleConfirm(true);
                    confirmModal.hide();
                });
            }
            confirmModal.show();
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        applyTheme(currentTheme());

        // Theme picker: the same three options appear in the navbar dropdown
        // and on the login card.
        document.body.addEventListener("click", function (event) {
            const pick = event.target.closest("[data-theme-set]");
            if (!pick) return;
            event.preventDefault();
            setTheme(pick.getAttribute("data-theme-set"));
        });

        // While "system" is selected the panel has to follow the OS when it
        // switches at sunset, not only on the next page load.
        const onSchemeChange = function () {
            if (currentTheme() === "system") applyTheme("system");
        };
        if (typeof darkQuery.addEventListener === "function") {
            darkQuery.addEventListener("change", onSchemeChange);
        } else if (typeof darkQuery.addListener === "function") {
            darkQuery.addListener(onSchemeChange);  // Safari < 14
        }

        // Ask before a destructive link or button.
        //
        // Forms put data-confirm on the <form> element, whose question belongs
        // to the submit event: matching clicks here fired it whenever a field
        // inside the form was clicked.
        document.body.addEventListener("click", async function (event) {
            const target = event.target.closest("[data-confirm]");
            if (!target || target.tagName === "FORM") return;

            // Re-entry after the operator agreed: let the click through.
            if (target.dataset.confirmed === "1") {
                delete target.dataset.confirmed;
                return;
            }

            event.preventDefault();
            event.stopPropagation();
            if (!(await confirmDialog(target.getAttribute("data-confirm")))) return;
            const again = target.getAttribute("data-confirm-again");
            if (again && !(await confirmDialog(again))) return;

            target.dataset.confirmed = "1";
            target.click();
        });

        // Confirmation for <form data-confirm>, including Enter-to-submit.
        document.body.addEventListener("submit", async function (event) {
            const form = event.target;
            if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) return;
            if (form.dataset.confirmed === "1") {
                delete form.dataset.confirmed;
                return;
            }
            event.preventDefault();
            if (!(await confirmDialog(form.getAttribute("data-confirm")))) return;
            const again = form.getAttribute("data-confirm-again");
            if (again && !(await confirmDialog(again))) return;
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

    window.PiWeb = { toast, api, confirm: confirmDialog, csrf: CSRF };
})();
