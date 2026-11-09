// Shared staff session. API authorization remains authoritative on every request.
const Staff = (() => {
  const KEY = "boardcast.staff";
  let session = {token: null, devUser: null};
  let epoch = 0;
  function staleSession() { const error = new Error('The account changed.'); error.stale = true; return error; }
  try { session = JSON.parse(sessionStorage.getItem(KEY)) || session; } catch (_) {}
  function persist() { try { sessionStorage.setItem(KEY, JSON.stringify(session)); } catch (_) {} }
  function signOut(message = "") {
    epoch++;
    session = {token: null, devUser: null};
    try { sessionStorage.removeItem(KEY); } catch (_) {}
    document.getElementById("app")?.setAttribute("hidden", "");
    document.getElementById("signin")?.removeAttribute("hidden");
    document.getElementById("signout")?.setAttribute("hidden", "");
    const who = document.getElementById("who"); if (who) who.textContent = "Staff sign-in";
    const error = document.getElementById("signin-error");
    if (error) { error.textContent = message; error.hidden = !message; }
    const submit = document.querySelector('#devform button'); if (submit) submit.disabled = false;
    window.dispatchEvent(new Event("boardcast:signout"));
  }
  async function api(path, opts = {}) {
    const requestEpoch = epoch;
    const h = new Headers(opts.headers || {});
    if (!(opts.body instanceof FormData)) h.set("Content-Type", "application/json");
    if (session.token) h.set("Authorization", "Bearer " + session.token);
    if (session.devUser) h.set("X-Dev-User", session.devUser);
    let r;
    try { r = await fetch(path, {...opts, headers: h}); }
    catch (error) { if (requestEpoch !== epoch) throw staleSession(); throw error; }
    const body = await r.json().catch(() => ({}));
    if (requestEpoch !== epoch) throw staleSession();
    if (r.status === 401) signOut("Your session expired. Sign in again to continue.");
    if (!r.ok) {
      const detail = typeof body.detail === "string" ? body.detail :
        Array.isArray(body.detail) ? body.detail.map(e => e.msg).join("; ") : "Something went wrong. Please try again.";
      throw new Error(detail);
    }
    return body;
  }
  function el(tag, attrs = {}, ...children) {
    const n = document.createElement(tag);
    for (const [k,v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const child of children) if (child != null) n.append(child);
    return n;
  }
  function safeLink(value) {
    try { const u = new URL(value); return u.protocol === "https:" && !u.username && !u.password ? u.href : null; }
    catch (_) { return null; }
  }
  function init(onReady) {
    let starting = null;
    async function start() {
      const loginEpoch = epoch;
      if (starting === loginEpoch) return;
      starting = loginEpoch;
      const submit = document.querySelector("#devform button"); if (submit) submit.disabled = true;
      try {
        const me = await api("/admin/api/me");
        if (loginEpoch !== epoch) return;
        persist();
        document.getElementById("signin").hidden = true;
        document.getElementById("signin-error").hidden = true;
        document.getElementById("who").textContent = me.email + " · " + (me.role === "student_affairs" ? "Student Affairs" : "Club rep");
        document.getElementById("signout")?.removeAttribute("hidden");
        await onReady(me);
      } catch (e) {
        if (e.stale || loginEpoch !== epoch) return;
        const error = document.getElementById("signin-error"); error.textContent = e.message; error.hidden = false;
      } finally { if (loginEpoch === epoch) { starting = null; if (submit) submit.disabled = false; } }
    }
    window.onSignIn = resp => { epoch++; session = {token:resp.credential, devUser:null}; start(); };
    document.getElementById("devform")?.addEventListener("submit", ev => {
      ev.preventDefault(); epoch++; session = {token:null, devUser:document.getElementById("devemail").value.trim()}; start();
    });
    document.getElementById("signout")?.addEventListener("click", () => signOut());
    if (session.token || session.devUser) start();
  }
  return {api, el, init, signOut, safeLink};
})();
