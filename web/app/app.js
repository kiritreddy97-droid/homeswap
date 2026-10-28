// HomeSwap web client: no framework, no build step. All user-supplied text goes through
// textContent (via h()), never innerHTML, so listings and reviews cannot inject markup.
"use strict";

const TOKEN_KEY = "homeswap_token";
let me = null;
let balance = null;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

const store = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } },
};

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = store.get();
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && token) { logout(); }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = Array.isArray(d) ? d.map((e) => (e.loc || []).slice(1).join(".") + ": " + e.msg).join("; ") : d;
    throw new Error(res.status === 429 ? "Too many requests, wait a moment." : msg || "Request failed (" + res.status + ")");
  }
  return data;
}

let toastTimer;
function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ""), 3500);
}

// Runs an async action, shows errors as a toast, re-renders on success.
function act(fn, okMsg, { rerender = true } = {}) {
  return async (ev) => {
    const btn = ev && ev.currentTarget;
    if (btn) btn.disabled = true;
    try {
      await fn(ev);
      if (okMsg) toast(okMsg);
      if (rerender) await render();
    } catch (e) {
      toast(e.message, true);
      if (btn) btn.disabled = false;
    }
  };
}

function field(label, input) {
  return h("div", {}, h("label", {}, label), input);
}

function logout() {
  store.set(null);
  me = null;
  balance = null;
  location.hash = "#/login";
}

// ---------- views ----------

function loginView() {
  const view = h("div", {}, h("h1", {}, "Log in or sign up"));
  const box = h("div", { class: "card" });
  view.append(box);
  let identifier = "";

  function stepContact() {
    const input = h("input", { type: "text", autocomplete: "username", placeholder: "you@example.com or 98765 43210", required: true });
    box.replaceChildren(
      h("p", { class: "muted" }, "We send a 6-digit code to your email or phone. No password needed."),
      field("Email or phone", input),
      h("p", {}, h("button", {
        onclick: act(async () => {
          identifier = input.value.trim();
          await api("POST", "/auth/otp/request", { identifier });
          stepCode();
        }, null, { rerender: false }),
      }, "Send code")),
    );
    input.focus();
  }

  function stepCode() {
    const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, placeholder: "123456" });
    const name = h("input", { type: "text", autocomplete: "name", placeholder: "Optional, for new accounts" });
    box.replaceChildren(
      h("p", {}, "Code sent to ", h("strong", {}, identifier), "."),
      h("p", { class: "muted" }, "In dev/staging the code is printed in the server log."),
      field("6-digit code", code),
      field("Your name", name),
      h("p", { class: "row" },
        h("button", {
          onclick: async (ev) => {
            ev.currentTarget.disabled = true;
            try {
              const r = await api("POST", "/auth/otp/verify", { identifier, code: code.value.trim(), name: name.value });
              store.set(r.access_token);
              me = r.user;
              if (location.hash === "#/homes") await render();
              else location.hash = "#/homes";
            } catch (e) { toast(e.message, true); ev.currentTarget.disabled = false; }
          },
        }, "Log in"),
        h("button", { class: "ghost", onclick: stepContact }, "Use a different contact")),
    );
    code.focus();
  }

  stepContact();
  return view;
}

async function homesView() {
  const city = h("input", { type: "search", placeholder: "City, e.g. Goa" });
  const list = h("div");
  async function load() {
    const q = city.value.trim() ? "?city=" + encodeURIComponent(city.value.trim()) : "";
    const homes = await api("GET", "/homes" + q);
    list.replaceChildren(...(homes.length ? homes.map(homeCard) : [h("p", { class: "muted" }, "No verified homes found.")]));
  }
  city.addEventListener("input", () => { clearTimeout(city._t); city._t = setTimeout(() => load().catch((e) => toast(e.message, true)), 250); });
  await load();
  return h("div", {}, h("h1", {}, "Browse homes"), h("div", { class: "row" }, city), list,
    me && !me.id_verified ? h("p", { class: "muted" }, "Your identity is not verified yet. You can browse, but staying requires verification.") : null);
}

function homeCard(home) {
  const start = h("input", { type: "date", required: true });
  const end = h("input", { type: "date", required: true });
  const reviews = h("div");
  return h("div", { class: "card" },
    h("div", { class: "row spread" }, h("strong", {}, home.title), h("span", { class: "pill ok" }, home.points_per_night + " pts / night")),
    h("div", { class: "muted" }, home.city + " · " + home.bedrooms + " bedroom" + (home.bedrooms === 1 ? "" : "s") + " · up to " + home.max_guests + " guests"),
    h("div", { class: "row" }, field("Check in", start), field("Check out", end),
      h("button", {
        onclick: act(async () => {
          const r = await api("POST", "/swaps", { home_id: home.id, start: start.value, end: end.value });
          toast("Requested for " + r.points_cost + " points. The host will review it.");
          location.hash = "#/trips";
        }),
      }, "Request stay")),
    h("div", {}, h("button", {
      class: "ghost",
      onclick: async (ev) => {
        try {
          const r = await api("GET", "/homes/" + home.id + "/reviews");
          ev.currentTarget.textContent = r.count ? "Reviews: " + r.average_rating.toFixed(1) + " / 5 (" + r.count + ")" : "No reviews yet";
          reviews.replaceChildren(...r.reviews.map((x) => h("p", { class: "muted" }, "★".repeat(x.rating) + (x.comment ? " " + x.comment : ""))));
        } catch (e) { toast(e.message, true); }
      },
    }, "Show reviews"), reviews),
  );
}

async function myHomesView() {
  const homes = await api("GET", "/me/homes");
  const f = { city: h("input", { required: true }), title: h("input", { required: true }),
    bedrooms: h("input", { type: "number", min: 1, value: 1 }), guests: h("input", { type: "number", min: 1, value: 2 }),
    pts: h("input", { type: "number", min: 1, value: 100 }) };
  return h("div", {},
    h("h1", {}, "My homes"),
    ...(homes.length ? homes.map((x) => h("div", { class: "card row spread" },
      h("div", {}, h("strong", {}, x.title), h("div", { class: "muted" }, x.city + " · " + x.points_per_night + " pts/night")),
      h("span", { class: "pill" + (x.property_verified ? " ok" : "") }, x.property_verified ? "verified" : "awaiting verification"))) : [h("p", { class: "muted" }, "You have not listed a home yet.")]),
    h("h2", {}, "List a home"),
    h("div", { class: "card" },
      h("p", { class: "muted" }, "An admin verifies each home before it appears in search. Verified homes earn a one-time 200-point listing bonus."),
      h("div", { class: "row" }, field("City", f.city), field("Title", f.title)),
      h("div", { class: "row" }, field("Bedrooms", f.bedrooms), field("Max guests", f.guests), field("Points per night", f.pts)),
      h("p", {}, h("button", {
        onclick: act(() => api("POST", "/homes", {
          city: f.city.value, title: f.title.value, bedrooms: +f.bedrooms.value, max_guests: +f.guests.value, points_per_night: +f.pts.value,
        }), "Home submitted for verification"),
      }, "Submit home"))),
  );
}

function tripActions(s) {
  const isHost = s.host_id === me.id;
  const btn = (label, path, cls, ok) => h("button", { class: cls || "", onclick: act(() => api("POST", path), ok) }, label);
  const out = [];
  if (isHost && s.status === "requested") out.push(btn("Approve", `/swaps/${s.id}/approve`, "", "Approved. Points transferred."));
  if (s.status === "requested" || s.status === "approved") out.push(btn("Cancel", `/swaps/${s.id}/cancel`, "danger", "Cancelled"));
  if (s.status === "approved" && s.end <= new Date().toISOString().slice(0, 10)) out.push(btn("Mark completed", `/swaps/${s.id}/complete`, "ghost"));
  if (s.status === "completed") {
    out.push(reviewForm(s));
    if (isHost) out.push(claimForm(s));
  }
  return out;
}

function reviewForm(s) {
  const rating = h("select", {}, ...[5, 4, 3, 2, 1].map((n) => h("option", { value: n }, n + " ★")));
  const comment = h("input", { type: "text", placeholder: "Comment (optional)", maxlength: 2000 });
  return h("div", { class: "row" }, rating, comment, h("button", {
    class: "ghost",
    onclick: act(() => api("POST", `/swaps/${s.id}/reviews`, { rating: +rating.value, comment: comment.value }), "Review posted"),
  }, "Leave review"));
}

function claimForm(s) {
  const desc = h("input", { type: "text", placeholder: "Describe the damage" });
  const amt = h("input", { type: "number", min: 0, placeholder: "Amount (₹)" });
  return h("div", { class: "row" }, desc, amt, h("button", {
    class: "danger",
    onclick: act(() => api("POST", `/swaps/${s.id}/damage-claim`, { description: desc.value, amount: +amt.value }), "Damage claim filed"),
  }, "File damage claim"));
}

async function tripsView() {
  const swaps = await api("GET", "/me/swaps");
  return h("div", {}, h("h1", {}, "My stays"),
    ...(swaps.length ? swaps.reverse().map((s) => h("div", { class: "card" },
      h("div", { class: "row spread" }, h("strong", {}, s.home_title + ", " + s.city), h("span", { class: "pill" }, s.status)),
      h("div", { class: "muted" }, s.start + " → " + s.end + " · " + s.points_cost + " pts · " + (s.host_id === me.id ? "you are hosting" : "you are the guest")),
      h("div", { class: "row" }, ...tripActions(s)))) : [h("p", { class: "muted" }, "No stays yet. Browse homes to request one.")]));
}

async function adminView() {
  const [users, homes, claims] = await Promise.all([
    api("GET", "/admin/users?id_verified=false"), api("GET", "/admin/homes?verified=false"), api("GET", "/admin/damage-claims?status=open"),
  ]);
  const empty = (t) => h("p", { class: "muted" }, t);
  return h("div", {}, h("h1", {}, "Admin"),
    h("h2", {}, "Users awaiting ID verification"),
    ...(users.length ? users.map((u) => h("div", { class: "card row spread" },
      h("span", {}, "#" + u.id + " " + (u.name || "(no name)") + " · " + (u.email || u.phone)),
      h("button", { onclick: act(() => api("POST", `/admin/users/${u.id}/verify`), "User verified") }, "Verify ID"))) : [empty("None.")]),
    h("h2", {}, "Homes awaiting verification"),
    ...(homes.length ? homes.map((x) => h("div", { class: "card row spread" },
      h("span", {}, "#" + x.id + " " + x.title + ", " + x.city + " (owner #" + x.owner_id + ")"),
      h("button", { onclick: act(() => api("POST", `/admin/homes/${x.id}/verify`), "Home verified") }, "Verify home"))) : [empty("None.")]),
    h("h2", {}, "Open damage claims"),
    ...(claims.length ? claims.map(claimCard) : [empty("None.")]));
}

function claimCard(c) {
  const awarded = h("input", { type: "number", min: 0, max: c.amount_claimed, value: c.amount_claimed });
  const note = h("input", { type: "text", placeholder: "Resolution note" });
  const resolve = (approve) => act(() => api("POST", `/admin/damage-claims/${c.id}/resolve`,
    { approve, amount_awarded: approve ? +awarded.value : null, note: note.value }), approve ? "Claim approved" : "Claim rejected");
  return h("div", { class: "card" },
    h("div", {}, h("strong", {}, "Claim #" + c.id), " on stay #" + c.swap_id + " · ₹" + c.amount_claimed),
    h("p", {}, c.description),
    h("div", { class: "row" }, field("Award (₹)", awarded), field("Note", note)),
    h("div", { class: "row" }, h("button", { onclick: resolve(true) }, "Approve"), h("button", { class: "danger", onclick: resolve(false) }, "Reject")));
}

// ---------- shell ----------

const ROUTES = {
  "#/login": { view: loginView, public: true },
  "#/homes": { view: homesView, label: "Browse" },
  "#/trips": { view: tripsView, label: "My stays" },
  "#/my-homes": { view: myHomesView, label: "My homes" },
  "#/admin": { view: adminView, label: "Admin", admin: true },
};

function renderNav(route) {
  const nav = document.getElementById("nav");
  if (!me) { nav.replaceChildren(); return; }
  const links = Object.entries(ROUTES).filter(([, r]) => r.label && (!r.admin || me.role === "admin"))
    .map(([hash, r]) => h("a", { href: hash, class: hash === route ? "on" : "" }, r.label));
  nav.replaceChildren(...links, h("span", { class: "grow" }),
    h("span", { class: "pill ok", title: "Points balance" }, (balance ?? 0) + " pts"),
    h("span", { class: "muted" }, me.name || me.email || me.phone),
    h("button", { class: "ghost", onclick: logout }, "Log out"));
}

async function render() {
  const route = ROUTES[location.hash] ? location.hash : "#/homes";
  const def = ROUTES[route];
  const target = document.getElementById("view");
  if (!def.public && !me) { location.hash = "#/login"; return; }
  if (def.admin && me.role !== "admin") { location.hash = "#/homes"; return; }
  try {
    if (me) balance = (await api("GET", "/me/balance")).points;
    target.replaceChildren(await def.view());
  } catch (e) {
    target.replaceChildren(h("p", { class: "err" }, e.message));
  }
  renderNav(route);
}

async function boot() {
  if (store.get()) {
    try { me = await api("GET", "/me"); } catch { me = null; }
  }
  await render();
}

window.addEventListener("hashchange", () => render().catch((e) => toast(e.message, true)));
boot();
