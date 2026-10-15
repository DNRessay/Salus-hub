const API = window.API_URL;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rand = (c) => "R" + (c / 100).toFixed(2);
const TZ = "Africa/Johannesburg";
const when = (iso) => new Date(iso).toLocaleString("en-ZA", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TZ });
const hhmm = (iso) => new Date(iso).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
const todaySA = () => new Date().toLocaleDateString("en-CA", { timeZone: TZ });
const addDays = (ymd, n) => { const d = new Date(ymd + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const STATUS = { online: "Online", dropin: "Taking drop-ins", resting: "Resting" };
const stars = (n) => `<span class="stars">${"★".repeat(Math.round(n))}${"☆".repeat(5 - Math.round(n))}</span>`;

let token = localStorage.getItem("token");
let me = null;
let timer = null;

async function api(path, opts = {}) {
  const headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  let body;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  const r = await fetch(API + path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = Array.isArray(d.detail) ? d.detail.map((x) => (x.loc || []).slice(-1) + ": " + x.msg).join(", ") : d.detail;
    throw new Error(msg || "Request failed (" + r.status + ")");
  }
  return d;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.style.display = "block"; setTimeout(() => (t.style.display = "none"), 3800); }
const needLogin = () => `<div class="card">Please <a href="#/login">log in</a> or <a href="#/register">create an account</a> to continue.</div>`;
const statusBadge = (s) => `<span class="meta"><span class="dot ${esc(s)}"></span>${STATUS[s] || esc(s)}</span>`;
const rating = (d) => d.review_count ? `${stars(d.avg_rating)} <span class="meta">${d.avg_rating} (${d.review_count})</span>` : '<span class="meta">No reviews yet</span>';

// ---------- views ----------
async function home(p) {
  const qs = new URLSearchParams();
  for (const k of ["q", "city", "specialty", "status"]) if (p.get(k)) qs.set(k, p.get(k));
  if (p.get("max")) qs.set("max_fee_cents", Math.round(+p.get("max") * 100));
  const [docs, specs] = await Promise.all([api("/doctors?" + qs), api("/specialties")]);
  return `<h1>Find a doctor. Book. Pay from your wallet.</h1>
  <form class="card filters" data-form="search">
    <input name="q" placeholder="Name or practice" value="${esc(p.get("q"))}">
    <input name="city" placeholder="City / suburb" value="${esc(p.get("city"))}">
    <select name="specialty"><option value="">Any specialty</option>${specs.map((s) => `<option ${p.get("specialty") === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <select name="status"><option value="">Any availability</option><option value="online" ${p.get("status") === "online" ? "selected" : ""}>Online now</option><option value="dropin" ${p.get("status") === "dropin" ? "selected" : ""}>Taking drop-ins</option></select>
    <input name="max" type="number" min="0" placeholder="Max fee (R)" value="${esc(p.get("max"))}">
    <button>Search</button></form>
  ${docs.map((d) => `<a class="card row" href="#/d/${d.id}" style="color:inherit;text-decoration:none">
    <div class="grow"><strong>${esc(d.name)}</strong> <span class="pill ok">✔ HPCSA verified</span>
      <div class="meta">${esc(d.profession)} · ${esc(d.specialty)} · ${esc(d.city)}</div>
      <div>${rating(d)} · ${statusBadge(d.status)}</div>
      <div class="meta">${d.next_slot ? "Next available: <b>" + when(d.next_slot) + "</b>" : "No open slots this week"}</div></div>
    <div class="price">${rand(d.fee_cents)}<div class="meta">${d.slot_minutes} min</div></div></a>`).join("") || "<p class='meta'>No doctors match yet.</p>"}`;
}

async function doctorView(id, p) {
  const d = await api("/doctors/" + id);
  const day = p.get("day") || todaySA();
  const r = d.ratings;
  return `<div class="card"><div class="between"><h1 style="margin:0">${esc(d.name)}</h1><div class="price">${rand(d.fee_cents)}</div></div>
    <div class="meta">${esc(d.profession)} · ${esc(d.specialty)} · <span class="pill ok">✔ HPCSA verified</span></div>
    <p class="meta">${esc(d.address)}, ${esc(d.city)} · ${d.slot_minutes}-minute consultations</p>
    <p>${statusBadge(d.status)}</p>${d.bio ? `<p>${esc(d.bio)}</p>` : ""}
    ${r.n ? `<p>${stars(r.avg)} <b>${r.avg}</b> from ${r.n} verified visits<br><span class="meta">Wait time ${r.wait ?? "–"} · Clinic ${r.clinic ?? "–"} · Communication ${r.comm ?? "–"}</span></p>` : ""}</div>
  <div class="card"><h2>Book a time</h2>
    <input type="date" id="day" min="${todaySA()}" max="${addDays(todaySA(), 60)}" value="${esc(day)}" data-did="${d.id}">
    <div id="slots" class="meta">Loading times…</div>
    <div id="confirm"></div></div>
  <div class="card"><h2>Patient reviews</h2>${d.reviews.map((x) => `<div style="border-top:1px solid var(--line);padding:8px 0">${stars(x.rating)} <span class="meta">${esc(x.patient)} · ${new Date(x.created_at).toLocaleDateString("en-ZA")}</span>
    <div class="meta">Wait ${x.wait_rating ?? "–"} · Clinic ${x.clinic_rating ?? "–"} · Communication ${x.comm_rating ?? "–"}</div>${x.comment ? `<div>${esc(x.comment)}</div>` : ""}</div>`).join("") || "<p class='meta'>No reviews yet.</p>"}</div>`;
}

async function loadSlots() {
  const inp = $("#day"); if (!inp) return;
  const box = $("#slots");
  try {
    const s = await api(`/doctors/${inp.dataset.did}/slots?day=${inp.value}`);
    const sel = $("#confirm").dataset.slot;
    box.className = "";
    box.innerHTML = s.slots.length
      ? `<div class="slots">${s.slots.map((x) => `<button data-act="pick" data-slot="${x}" data-fee="${s.fee_cents}" class="${sel === x ? "sel" : ""}">${hhmm(x)}</button>`).join("")}</div><div class="meta">Live availability, refreshed automatically. Times are South African time.</div>`
      : `<p class="meta">${s.status === "resting" && inp.value === todaySA() ? "This doctor is resting and not taking same-day bookings." : "No open slots on this date."}</p>`;
  } catch (e) { box.textContent = e.message; }
}

function confirmPanel(slot, fee) {
  const c = $("#confirm"); c.dataset.slot = slot;
  if (!me) { c.innerHTML = needLogin(); return; }
  const short = me.balance_cents < fee;
  c.innerHTML = `<div class="card" style="background:var(--accent2)"><b>${when(slot)}</b> · ${rand(fee)}
    <div class="meta">Wallet balance: ${rand(me.balance_cents)}${short ? ` · <a href="#/wallet">Top up ${rand(fee - me.balance_cents)} more</a>` : ""}</div>
    <textarea id="reason" placeholder="Reason for visit (optional, shared only with this doctor)" maxlength="500"></textarea>
    <button data-act="book" data-slot="${slot}" ${short ? "disabled" : ""}>Pay ${rand(fee)} &amp; book</button>
    <div class="meta">Free cancellation until 2 hours before. After that 50% is refunded.</div></div>`;
}

async function appointments() {
  if (!me) return needLogin();
  const list = await api("/appointments");
  return `<h1>My appointments</h1>${list.map((a) => `<div class="card"><div class="between"><div><b>${esc(a.doctor)}</b> <span class="meta">${esc(a.specialty)}</span><br>${when(a.starts_at)}</div>
    <span class="pill ${a.status === "booked" ? "ok" : a.status === "cancelled" ? "bad" : ""}">${a.status}</span></div>
    <div class="meta">${esc(a.address)}, ${esc(a.city)} · ${rand(a.fee_cents)}${a.reason ? " · " + esc(a.reason) : ""}</div>
    ${a.status === "booked" ? `<p><button class="danger" data-act="cancel" data-i="${a.id}">Cancel</button></p>` : ""}
    ${a.status === "completed" && !a.reviewed ? `<form data-form="review" data-i="${a.id}"><h2>Rate your visit</h2>
      <div class="filters"><label>Overall<select name="rating">${[5, 4, 3, 2, 1].map((n) => `<option>${n}</option>`).join("")}</select></label>
      <label>Wait time<select name="wait_rating">${[5, 4, 3, 2, 1].map((n) => `<option>${n}</option>`).join("")}</select></label>
      <label>Clinic<select name="clinic_rating">${[5, 4, 3, 2, 1].map((n) => `<option>${n}</option>`).join("")}</select></label>
      <label>Communication<select name="comm_rating">${[5, 4, 3, 2, 1].map((n) => `<option>${n}</option>`).join("")}</select></label></div>
      <textarea name="comment" placeholder="Comments (optional)"></textarea><button>Submit review</button></form>` : ""}</div>`).join("") || "<p class='meta'>No appointments yet.</p>"}`;
}

async function walletView() {
  if (!me) return needLogin();
  const w = await api("/wallet");
  return `<h1>Wallet</h1><div class="card"><div class="meta">Balance</div><div class="big">${rand(w.balance_cents)}</div>
    <h2>Top up by card or instant EFT</h2><div>${[10000, 25000, 50000, 100000].map((c) => `<button class="ghost" data-act="topup" data-c="${c}">${rand(c)}</button> `).join("")}</div>
    <form data-form="topup" style="margin-top:10px"><input name="amount" type="number" min="20" max="5000" step="1" placeholder="Other amount (R20 - R5000)" required><button>Top up</button></form>
    ${w.dev_topup ? `<p><button class="ghost" data-act="devtopup">Add R500 test credit</button></p>` : ""}</div>
  <div class="card"><h2>History</h2><ul class="clean">${w.transactions.map((t) => `<li class="between"><span>${esc(t.note || t.kind)}<br><span class="meta">${new Date(t.created_at).toLocaleString("en-ZA", { timeZone: TZ })}</span></span><b class="${t.amount_cents > 0 ? "pos" : "neg"}">${t.amount_cents > 0 ? "+" : ""}${rand(t.amount_cents)}</b></li>`).join("") || "<li class='meta'>No transactions</li>"}</ul></div>`;
}

const profileForm = (d = {}) => `
  <input name="name" placeholder="Name on practice (e.g. Dr A Naidoo)" required minlength="3" value="${esc(d.name)}">
  <input name="profession" placeholder="Profession (General Practitioner, Psychologist…)" required value="${esc(d.profession)}">
  <input name="specialty" placeholder="Specialty (e.g. General practice, Dermatology)" required value="${esc(d.specialty)}">
  <input name="hpcsa_number" placeholder="HPCSA registration number" required value="${esc(d.hpcsa_number)}">
  <input name="city" placeholder="City / suburb" required value="${esc(d.city)}">
  <input name="address" placeholder="Practice address" required minlength="5" value="${esc(d.address)}">
  <input name="fee" type="number" min="50" step="1" placeholder="Consultation fee (R)" required value="${d.fee_cents ? d.fee_cents / 100 : ""}">
  <select name="slot_minutes">${[15, 20, 30, 45, 60].map((m) => `<option ${d.slot_minutes === m ? "selected" : ""} value="${m}">${m}-minute slots</option>`).join("")}</select>
  <textarea name="bio" placeholder="Short bio (optional)">${esc(d.bio)}</textarea>`;

async function applyView() {
  if (!me) return needLogin();
  if (me.doctor_id) { location.hash = "#/doctor"; return ""; }
  return `<h1>List your practice</h1><p class="meta">We check your HPCSA registration before you appear in search. Patients pay from their wallet; you receive the fee less ${"10"}% platform fee after each completed consultation.</p>
  <form class="card" data-form="apply">${profileForm()}<button>Submit for verification</button></form>`;
}

const schedRow = (r = { weekday: 0, start: "08:00", end: "17:00" }) => `<div class="sched"><select class="wd">${DAYS.map((n, i) => `<option value="${i}" ${r.weekday === i ? "selected" : ""}>${n}</option>`).join("")}</select>
  <input class="st" type="time" value="${r.start}"><input class="en" type="time" value="${r.end}"><button class="danger" data-act="rmrow" type="button">✕</button></div>`;

async function doctorDash() {
  if (!me) return needLogin();
  if (!me.doctor_id) return `<div class="card">You don't have a practice profile yet. <a href="#/apply">List your practice</a></div>`;
  const [d, appts] = await Promise.all([api("/doctor/me"), api("/doctor/appointments")]);
  return `<h1>${esc(d.name)}</h1>
  <div class="card"><div class="between"><div>${d.verified ? '<span class="pill ok">✔ Verified and listed</span>' : '<span class="pill bad">Awaiting HPCSA verification, not listed yet</span>'}</div>
    <div class="meta">Earnings wallet: <b>${rand(me.balance_cents)}</b></div></div>
    <h2>Live status</h2><div>${["online", "dropin", "resting"].map((s) => `<button class="${d.status === s ? "" : "ghost"}" data-act="status" data-s="${s}">${STATUS[s]}</button> `).join("")}</div>
    <p class="meta">Resting stops same-day bookings. Drop-ins shows a badge to patients nearby.</p></div>
  <div class="card"><h2>Weekly schedule</h2><div id="sched">${d.availability.map(schedRow).join("")}</div>
    <button class="ghost" data-act="addrow" type="button">+ Add hours</button> <button data-act="savesched">Save schedule</button></div>
  <div class="card"><h2>Appointments</h2><ul class="clean">${appts.map((a) => `<li class="between"><span><b>${esc(a.patient)}</b>${a.phone ? " · " + esc(a.phone) : ""}<br>${when(a.starts_at)} · ${rand(a.fee_cents)}${a.reason ? `<br><span class="meta">${esc(a.reason)}</span>` : ""}</span>
    <span>${a.status === "booked" ? `<button data-act="complete" data-i="${a.id}" ${new Date(a.starts_at) > new Date() ? "disabled" : ""}>Complete</button> <button class="danger" data-act="cancel" data-i="${a.id}">Cancel</button>` : `<span class="pill">${a.status}</span>`}</span></li>`).join("") || "<li class='meta'>No appointments</li>"}</ul></div>
  <form class="card" data-form="docprofile"><h2>Practice details</h2>${profileForm(d)}<button>Save details</button><p class="meta">Changing your HPCSA number triggers re-verification.</p></form>`;
}

async function adminView() {
  if (!me || !me.is_admin) return `<div class="card">Admins only.</div>`;
  const rows = await api("/admin/doctors?pending=true");
  return `<h1>Pending verification</h1>${rows.map((d) => `<div class="card"><b>${esc(d.name)}</b> · ${esc(d.profession)} · ${esc(d.specialty)}<div class="meta">HPCSA: <b>${esc(d.hpcsa_number)}</b> · ${esc(d.city)} · ${esc(d.email)}</div>
    <p class="meta">Check the number on the HPCSA iRegister before approving.</p>
    <button data-act="verify" data-i="${d.id}" data-v="1">Approve</button> <button class="danger" data-act="verify" data-i="${d.id}" data-v="0">Reject</button></div>`).join("") || "<p class='meta'>Nothing pending.</p>"}`;
}

async function notifView() {
  if (!me) return needLogin();
  const n = await api("/notifications");
  api("/notifications/read", { method: "POST" }).then(refreshBell).catch(() => {});
  return `<h1>Notifications</h1><div class="card"><ul class="clean">${n.items.map((x) => `<li>${x.read ? "" : "● "}${x.link ? `<a href="${esc(x.link)}">${esc(x.body)}</a>` : esc(x.body)}<br><span class="meta">${new Date(x.created_at).toLocaleString("en-ZA", { timeZone: TZ })}</span></li>`).join("") || "<li class='meta'>Nothing yet</li>"}</ul></div>`;
}

const authForm = (kind) => `<h1>${kind === "login" ? "Log in" : "Create your account"}</h1><form class="card" data-form="${kind}">
  ${kind === "register" ? '<input name="full_name" placeholder="Full name" required minlength="2"><input name="phone" type="tel" placeholder="Cell number">' : ""}
  <input name="email" type="email" placeholder="Email" required><input name="password" type="password" placeholder="Password (8+ characters)" minlength="8" required>
  <button>${kind === "login" ? "Log in" : "Sign up"}</button></form>`;

// ---------- router ----------
async function render() {
  clearInterval(timer);
  const [path, qs] = location.hash.slice(1).split("?");
  const p = new URLSearchParams(qs || "");
  const s = (path || "/").split("/").filter(Boolean);
  const routes = {
    "": () => home(p), d: () => doctorView(s[1], p), appointments, wallet: walletView, apply: applyView, doctor: doctorDash,
    admin: adminView, notifications: notifView, login: () => authForm("login"), register: () => authForm("register"),
  };
  $("#app").innerHTML = '<p class="meta">Loading…</p>';
  try {
    const html = await (routes[s[0] || ""] || routes[""])();
    if (html) $("#app").innerHTML = html;
    if (s[0] === "d") { loadSlots(); timer = setInterval(loadSlots, 30000); }
  } catch (e) { $("#app").innerHTML = `<div class="card">${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}

async function refreshBell() {
  if (!me) return;
  try { const n = await api("/notifications"); const b = $("#bell"); if (b) b.textContent = n.unread ? `🔔 ${n.unread}` : "🔔"; } catch {}
}
function renderNav() {
  $("#nav").innerHTML = `<a href="#/">Find a doctor</a>` + (me
    ? `<a href="#/appointments">Appointments</a><a href="#/doctor">${me.doctor_id ? "My practice" : "For doctors"}</a>${me.is_admin ? '<a href="#/admin">Admin</a>' : ""}
       <span style="flex:1"></span><a href="#/wallet"><b>${rand(me.balance_cents)}</b></a><a id="bell" href="#/notifications">🔔</a><a href="#" data-act="logout">Log out</a>`
    : `<a href="#/apply">For doctors</a><span style="flex:1"></span><a href="#/login">Log in</a><a class="btn" href="#/register">Sign up</a>`);
  refreshBell();
}

// ---------- events ----------
const rands = (v) => Math.round(parseFloat(v) * 100);
const doctorBody = (d) => ({ name: d.name, profession: d.profession, specialty: d.specialty, hpcsa_number: d.hpcsa_number, city: d.city,
  address: d.address, bio: d.bio || null, fee_cents: rands(d.fee), slot_minutes: +d.slot_minutes });
const forms = {
  async login(d) { token = (await api("/auth/login", { json: d })).token; await boot("#/"); },
  async register(d) { token = (await api("/auth/register", { json: d })).token; await boot("#/"); },
  async search(d) { const q = new URLSearchParams(); for (const [k, v] of Object.entries(d)) if (v) q.set(k, v); location.hash = "#/?" + q; },
  async review(d, f) { await api(`/appointments/${f.dataset.i}/review`, { json: { rating: +d.rating, wait_rating: +d.wait_rating, clinic_rating: +d.clinic_rating, comm_rating: +d.comm_rating, comment: d.comment || null } }); toast("Thanks for your review"); render(); },
  async topup(d) { await startTopup(rands(d.amount)); },
  async apply(d) { await api("/doctor/apply", { json: doctorBody(d) }); toast("Submitted. We'll verify your HPCSA registration."); await boot("#/doctor"); },
  async docprofile(d) { const r = await api("/doctor/profile", { method: "PUT", json: doctorBody(d) }); toast(r.reverify ? "Saved. HPCSA change needs re-verification." : "Saved"); render(); },
};
async function startTopup(cents) { const r = await api("/wallet/topup", { json: { amount_cents: cents } }); location.href = r.url; }

const acts = {
  logout() { token = null; localStorage.removeItem("token"); me = null; boot("#/"); },
  pick(el) { document.querySelectorAll("#slots button").forEach((b) => b.classList.remove("sel")); el.classList.add("sel"); confirmPanel(el.dataset.slot, +el.dataset.fee); },
  async book(el) {
    el.disabled = true;
    try {
      const did = $("#day").dataset.did;
      await api("/appointments", { json: { doctor_id: +did, starts_at: el.dataset.slot, reason: $("#reason").value || null } });
      toast("Booked! You'll find it under Appointments."); await boot("#/appointments");
    } catch (e) { el.disabled = false; toast(e.message); loadSlots(); }
  },
  async cancel(el) {
    if (!confirm("Cancel this appointment?")) return;
    const r = await api(`/appointments/${el.dataset.i}/cancel`, { method: "POST" });
    toast(me.doctor_id && location.hash.startsWith("#/doctor") ? "Cancelled" : `Cancelled. Refunded ${rand(r.refunded_cents)}`); await boot();
  },
  async complete(el) { const r = await api(`/appointments/${el.dataset.i}/complete`, { method: "POST" }); toast(`Completed. ${rand(r.earned_cents)} added to your earnings`); await boot(); },
  async status(el) { await api("/doctor/status", { method: "PATCH", json: { status: el.dataset.s } }); render(); },
  addrow() { $("#sched").insertAdjacentHTML("beforeend", schedRow()); },
  rmrow(el) { el.closest(".sched").remove(); },
  async savesched() {
    const rows = [...document.querySelectorAll("#sched .sched")].map((r) => ({ weekday: +r.querySelector(".wd").value, start: r.querySelector(".st").value, end: r.querySelector(".en").value }));
    await api("/doctor/availability", { method: "PUT", json: rows }); toast("Schedule saved");
  },
  async topup(el) { await startTopup(+el.dataset.c); },
  async devtopup() { await api("/wallet/dev-topup", { json: { amount_cents: 50000 } }); await boot(); },
  async verify(el) { await api(`/admin/doctors/${el.dataset.i}/verify`, { json: { verified: el.dataset.v === "1" } }); render(); },
};

document.addEventListener("submit", async (e) => {
  const f = e.target.closest("form[data-form]"); if (!f) return;
  e.preventDefault();
  const btn = f.querySelector("button"); if (btn) btn.disabled = true;
  try { await forms[f.dataset.form](Object.fromEntries(new FormData(f)), f); } catch (err) { toast(err.message); }
  if (btn) btn.disabled = false;
});
document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-act]"); if (!el) return;
  e.preventDefault();
  try { await acts[el.dataset.act](el); } catch (err) { toast(err.message); }
});
document.addEventListener("change", (e) => { if (e.target.id === "day") { $("#confirm").dataset.slot = ""; $("#confirm").innerHTML = ""; loadSlots(); } });
window.addEventListener("hashchange", render);
setInterval(refreshBell, 30000);

async function boot(go) {
  if (token) {
    try { me = await api("/me"); localStorage.setItem("token", token); }
    catch { token = null; me = null; localStorage.removeItem("token"); }
  }
  renderNav();
  if (go && location.hash !== go) location.hash = go; else render();
}

// Paystack sends the user back to the site root with ?reference=... after paying
(async function init() {
  const ref = new URLSearchParams(location.search).get("reference");
  if (ref && token) {
    history.replaceState(null, "", location.pathname + "#/wallet");
    try { const r = await api("/wallet/verify/" + encodeURIComponent(ref)); toast(r.status === "paid" ? "Top-up successful" : "Payment not confirmed yet. It will appear shortly."); } catch (e) { toast(e.message); }
  }
  boot();
})();
