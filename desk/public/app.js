// Posting Desk: the posts of one episode (opened by its private link ?e=...), with shared "Posted" ticks.
// With the admin key it shows every episode and lets you add, remove and share them. Data comes from /api (Netlify Blobs).
const NAMES = { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube Shorts", linkedin: "LinkedIn" };
const ORDER = ["tiktok", "instagram", "youtube", "linkedin"];
const UPLOAD = { tiktok: "https://www.tiktok.com/tiktokstudio/upload", instagram: "https://business.facebook.com/latest/composer",
                 youtube: "https://studio.youtube.com", linkedin: "https://www.linkedin.com/feed/?shareActive=true" };
const MAX = 19900000;            // a file is streamed back whole, and the server streams at most 20 MB
const PART = 3 * 1024 * 1024;    // upload size per request (the server takes up to about 4.5 MB)
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const local = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } },
  set(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} },
};
const LINK = (new URLSearchParams(location.search).get("e") || "").replace(/[^A-Za-z0-9_-]/g, "");
const linkFor = share => location.origin + "/?e=" + encodeURIComponent(share);
const S = { episodes: [], ticks: {}, filter: local.get("desk.filter") === "done" ? "done" : "todo", ep: "", loaded: false, err: "",
            name: local.get("desk.name"), key: local.get("desk.key"), busy: false };

const media = id => "/media/" + encodeURIComponent(id);
const slug = s => String(s).toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "episode";
const newId = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^a-zA-Z0-9]/g, "");
const tickId = (ep, clip, pl) => `${ep}~${clip}~${pl}`;
function todayIn(tz) {
  try { return new Intl.DateTimeFormat("en-CA", { timeZone: tz || "Europe/London", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
  catch (e) { return new Date().toISOString().slice(0, 10); }
}
const niceDay = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
const tzLabel = tz => !tz || tz === "Europe/London" ? "UK time" : tz.replace(/_/g, " ");

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (S.key) headers["x-admin-key"] = S.key;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  let body = null; try { body = await res.json(); } catch (e) {}
  if (!res.ok) { const err = new Error((body && body.error) || `The server said ${res.status}.`); err.status = res.status; throw err; }
  return body;
}

async function load() {
  if (!LINK && !S.key) { S.episodes = []; S.ticks = {}; S.loaded = true; S.err = ""; render(); return; }
  try {
    const st = await api("/api/state" + (LINK ? "?e=" + encodeURIComponent(LINK) : ""));
    S.all = st.episodes || [];
    S.episodes = (LINK ? S.all.filter(e => !st.admin || e.share === LINK) : S.all).sort((a, b) => String(a.posts?.[0]?.when || "").localeCompare(String(b.posts?.[0]?.when || "")));
    S.ticks = st.ticks || {};
    S.err = "";
  } catch (e) {
    S.err = e.status === 404 ? "This link doesn't open an episode any more. Ask whoever sent it for a new one."
          : e.status === 403 ? "The admin key isn't right any more. Press Lock, then unlock again."
          : "The posts couldn't load. Check your connection and reload.";
    if (e.status === 404) S.episodes = [];
  }
  S.loaded = true;
  if (!S.busy) { render(); renderAdmin(); }
}

// ---------- the list
function allPosts() {
  const out = [];
  for (const e of S.episodes) for (const p of e.posts || []) {
    const pls = ORDER.filter(pl => p.platforms && p.platforms[pl]);
    const done = pls.filter(pl => S.ticks[tickId(e.id, p.clip, pl)]);
    const day = String(p.when).slice(0, 10), today = todayIn(e.tz);
    const state = pls.length && done.length === pls.length ? "done" : day < today ? "overdue" : day === today ? "today" : "later";
    out.push({ e, p, pls, done, day, state });
  }
  return out.sort((a, b) => String(a.p.when).localeCompare(String(b.p.when)) || a.e.title.localeCompare(b.e.title));
}

function render() {
  const one = LINK && S.episodes.length === 1 ? S.episodes[0] : null;
  $("h1").textContent = one ? one.title : "Posting Desk";
  document.title = one ? one.title + " · Posting Desk" : "Posting Desk";
  $("f-ep").hidden = !!LINK || !S.key;
  $("admin-toggle").hidden = !!LINK && !S.key;
  if (!LINK && !S.key) {
    $("stats").innerHTML = ""; $("help").hidden = true;
    $("f-todo").parentElement.hidden = true;
    $("me-btn").hidden = true;
    $("list").dataset.sig = "";
    $("list").innerHTML = `<div class="empty"><b>Open the link you were sent</b>Each episode has its own link. Ask whoever sent you here for it.</div>`;
    return;
  }
  $("help").hidden = false; $("me-btn").hidden = false; $("f-todo").parentElement.hidden = false;
  const posts = allPosts();
  const todo = posts.filter(x => x.state !== "done");
  const over = todo.filter(x => x.state === "overdue").length, today = todo.filter(x => x.state === "today").length;
  $("stats").innerHTML = !S.loaded ? `<span class="chip">Loading the posts…</span>` : [
    over ? `<span class="chip warn">${over} overdue</span>` : "",
    `<span class="chip today">${today} to post today</span>`,
    `<span class="chip">${todo.length - over - today} coming up</span>`,
    `<span class="chip ok">${posts.length - todo.length} posted</span>`,
    S.err ? `<span class="chip warn">${esc(S.err)}</span>` : "",
  ].join("");
  $("me-btn").textContent = S.name ? `Posting as ${S.name}` : "Your name";

  const sel = $("f-ep"), cur = S.ep;
  sel.innerHTML = `<option value="">All episodes</option>` + S.episodes.map(e => `<option value="${esc(e.id)}">${esc(e.title)}</option>`).join("");
  sel.value = S.episodes.some(e => e.id === cur) ? cur : "";
  for (const f of ["todo", "done"]) $("f-" + f).setAttribute("aria-pressed", String(S.filter === f));

  let shown = posts.filter(x => !S.ep || x.e.id === S.ep);
  if (S.filter === "todo") shown = shown.filter(x => x.state !== "done");
  if (S.filter === "done") shown = shown.filter(x => x.state === "done").reverse();

  const list = $("list");
  if (!S.loaded) { list.innerHTML = `<div class="empty"><b>Loading the posts</b>This takes a moment.</div>`; return; }
  if (!S.episodes.length) {
    list.dataset.sig = "";
    list.innerHTML = LINK ? `<div class="empty"><b>This link doesn't open an episode</b>It may have been replaced. Ask whoever sent it for a new one.</div>`
                          : `<div class="empty"><b>No episodes yet</b>Add an episode zip under Manage episodes.</div>`;
    return;
  }
  if (!shown.length) {
    list.innerHTML = `<div class="empty"><b>${S.filter === "todo" ? "Everything's posted" : "Nothing posted yet"}</b>${S.filter === "todo" ? "New clips appear here when the next episode is added." : "Ticked posts show up here."}</div>`;
    return;
  }
  // keep a playing video playing: only rebuild when something visible changed
  const sig = JSON.stringify([S.filter, S.ep, shown.map(x => [x.e.id, x.p.clip, x.p.when, x.e.addedAt, x.pls.map(pl => S.ticks[tickId(x.e.id, x.p.clip, pl)] || 0)])]);
  if (sig === list.dataset.sig) return;
  list.dataset.sig = sig;
  const frag = document.createDocumentFragment();
  let lastDay = "";
  for (const x of shown) {
    if (x.day !== lastDay) {
      const h = document.createElement("div"); h.className = "day";
      h.textContent = (x.day === todayIn(x.e.tz) ? "Today, " : "") + niceDay(x.day);
      frag.appendChild(h); lastDay = x.day;
    }
    frag.appendChild(postCard(x));
  }
  list.replaceChildren(frag);
}

function postCard({ e, p, pls, done, state }) {
  const d = document.createElement("article");
  d.className = "post is-" + state;
  const label = { overdue: "Overdue", today: "Today", done: "Posted", later: "Coming up" }[state];
  d.innerHTML = `<div class="post-head">
      <span class="when">${esc(String(p.when).slice(11, 16))}</span>
      <span class="state ${state}">${label}</span>
      <span class="title"><b>Clip ${esc(p.clip)}: ${esc(p.title || "")}</b><br><span class="ep">${LINK ? "" : esc(e.title) + " · "}${esc(tzLabel(e.tz))} · ${done.length} of ${pls.length} platforms done</span></span>
    </div><div class="tiles"></div>`;
  const tiles = d.querySelector(".tiles");
  for (const pl of pls) tiles.appendChild(tile(e, p, pl));
  return d;
}

function tile(e, p, pl) {
  const x = p.platforms[pl], id = tickId(e.id, p.clip, pl), t = S.ticks[id];
  const r = document.createElement("div");
  r.className = "tile" + (t ? " is-done" : "");
  r.dataset.shape = x.shape || "";
  const tid = "t-" + id.replace(/[^a-zA-Z0-9_-]/g, "_");
  const base = `${String(p.when).slice(0, 10)} clip${p.clip}`;
  const vname = `${base} ${x.shape || pl}.mp4`, cname = `${base} cover ${x.shape || pl}.png`;
  const when = t && t.at ? new Date(t.at).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  r.innerHTML = `<div class="tile-head"><span class="pname">${esc(NAMES[pl] || pl)}</span><span class="shape">${esc(x.shape || "")}</span>
      <label class="tick" for="${tid}"><input type="checkbox" id="${tid}" ${t ? "checked" : ""}> Posted</label></div>
    ${x.video ? `<video controls preload="none" playsinline src="${media(x.video)}"${x.cover ? ` poster="${media(x.cover)}"` : ""}></video>` : `<div class="missing">${esc(x.missing || "This video isn't on the desk. Post it from the episode zip.")}</div>`}
    ${pl === "youtube" && x.title ? `<div class="yt">${esc(x.title)}</div>` : ""}
    <div class="cap">${esc(x.caption || "")}</div>
    <button class="more" hidden>Show all</button>
    <div class="actions">
      ${pl === "youtube" && x.title ? `<button class="c-title">Copy title</button>` : ""}
      <button class="c-cap">Copy caption</button>
      ${x.video ? `<a class="btn" href="${media(x.video)}?download=${encodeURIComponent(vname)}" download="${esc(vname)}">Save video</a>` : ""}
      ${x.cover ? `<a class="btn" href="${media(x.cover)}?download=${encodeURIComponent(cname)}" download="${esc(cname)}">Save cover</a>` : ""}
      <a class="btn" href="${esc(UPLOAD[pl] || "#")}" target="_blank" rel="noopener">Open ${esc(NAMES[pl] || pl)} ↗</a>
    </div>
    ${t ? `<div class="who">Posted by ${esc(t.name || "someone")}${when ? ", " + esc(when) : ""}</div>` : ""}`;
  const cap = r.querySelector(".cap"), more = r.querySelector(".more");
  requestAnimationFrame(() => { if (cap.scrollHeight > cap.clientHeight + 2) more.hidden = false; });
  more.onclick = () => { cap.classList.toggle("open"); more.textContent = cap.classList.contains("open") ? "Show less" : "Show all"; };
  r.querySelector(".c-cap").onclick = ev => copy(ev.currentTarget, x.caption || "");
  const ct = r.querySelector(".c-title"); if (ct) ct.onclick = ev => copy(ev.currentTarget, x.title || "");
  r.querySelector("input").onchange = ev => setTick(ev.currentTarget, e.id, p.clip, pl);
  return r;
}

async function copy(btn, text) {
  let ok = true;
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const t = document.createElement("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select(); try { ok = document.execCommand("copy"); } catch (e2) { ok = false; } t.remove();
  }
  flash(btn, ok ? "Copied" : "Select and copy it by hand");
}
function flash(btn, msg, ms = 1400) {
  if (!btn.dataset.label) btn.dataset.label = btn.textContent;
  btn.textContent = msg; btn.classList.add("done-msg");
  clearTimeout(btn._t); btn._t = setTimeout(() => { btn.textContent = btn.dataset.label; btn.classList.remove("done-msg"); }, ms);
}

async function setTick(input, ep, clip, pl) {
  const on = input.checked;
  if (on && !S.name) { input.checked = false; S.pending = { ep, clip, pl }; showMe(true); return; }
  input.disabled = true;
  try {
    await api("/api/tick", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ e: LINK, ep, clip, pl, name: S.name, done: on }) });
    if (on) S.ticks[tickId(ep, clip, pl)] = { name: S.name, at: new Date().toISOString() };
    else delete S.ticks[tickId(ep, clip, pl)];
    render();
  } catch (e) { input.checked = !on; S.err = "That tick didn't save. Try again."; render(); }
  input.disabled = false;
}

// ---------- your name
function showMe(force) {
  const p = $("me-panel");
  p.hidden = force ? false : !p.hidden;
  if (!p.hidden) { $("me-name").value = S.name; $("me-name").focus(); p.scrollIntoView({ block: "nearest" }); }
}
$("me-btn").onclick = () => showMe(false);
$("me-form").onsubmit = ev => {
  ev.preventDefault();
  S.name = $("me-name").value.trim().slice(0, 60); local.set("desk.name", S.name);
  $("me-panel").hidden = true; render();
  // finish the tick that asked for the name
  const t = S.pending; S.pending = null;
  if (t && S.name) {
    const box = [...document.querySelectorAll(".tile input[type=checkbox]")].find(i => i.id === "t-" + tickId(t.ep, t.clip, t.pl).replace(/[^a-zA-Z0-9_-]/g, "_"));
    if (box && !box.checked) { box.checked = true; setTick(box, t.ep, t.clip, t.pl); }
  }
};

// ---------- admin: unlock, add and remove episodes
$("admin-toggle").onclick = () => {
  if (S.key) { $("admin").hidden = !$("admin").hidden; return; }
  $("key-panel").hidden = !$("key-panel").hidden;
  if (!$("key-panel").hidden) $("key").focus();
};
$("key-form").onsubmit = async ev => {
  ev.preventDefault();
  S.key = $("key").value.trim();
  try { await api("/api/admin"); local.set("desk.key", S.key); $("key-panel").hidden = true; $("admin").hidden = false; $("key").value = ""; $("key-note").textContent = ""; load(); }
  catch (e) { S.key = ""; $("key-note").textContent = e.status === 403 ? "That admin key isn't right." : e.message; $("key-note").className = "note err"; }
};
$("lock").onclick = () => { S.key = ""; local.set("desk.key", ""); $("admin").hidden = true; load(); };
$("add").onclick = () => $("zip").click();
$("zip").onchange = ev => { const f = ev.target.files[0]; if (f) addZip(f); };

function readKit(html) {
  const m = html.match(/const D = (\{[\s\S]*?\});\s*\n\s*const NAMES/);
  if (!m) throw new Error("This zip's kit.html doesn't have the posts in it. Make the kit again in Offcuts and try once more.");
  return JSON.parse(m[1]);
}

async function uploadFile(blob, type, onPart) {
  const id = newId(), parts = [];
  for (let off = 0, n = 0; off < blob.size; off += PART, n++) {
    const piece = blob.slice(off, off + PART);
    let tries = 0;
    for (;;) {
      try { await api(`/api/file/${id}/part/${n}`, { method: "PUT", headers: { "content-type": "application/octet-stream" }, body: piece }); break; }
      catch (e) { if (++tries >= 3 || e.status === 403) throw e; await new Promise(r => setTimeout(r, 1500 * tries)); }
    }
    parts.push(piece.size); onPart && onPart(piece.size);
  }
  await api(`/api/file/${id}/meta`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ type, size: blob.size, parts }) });
  return id;
}

async function addZip(file) {
  const up = $("up"), bar = $("bar"), note = $("upnote");
  const say = (t, err) => { note.textContent = t; note.className = "note" + (err ? " err" : ""); };
  up.hidden = false; bar.style.width = "0%"; $("add").disabled = true; S.busy = true;
  try {
    say("Opening the zip…");
    const zip = await JSZip.loadAsync(file);
    const kits = Object.keys(zip.files).filter(n => /(^|\/)kit\.html$/.test(n) && !/__MACOSX/.test(n)).sort((a, b) => a.length - b.length);
    if (!kits.length) throw new Error("There's no kit.html in this zip. Choose an episode zip from Ready to post.");
    const root = kits[0].slice(0, -"kit.html".length);
    const D = readKit(await zip.file(kits[0]).async("string"));
    const title = file.name.replace(/\.zip$/i, "") || D.episode;
    const epId = slug(title);  // the zip name carries the show, so two clients' "Episode 1" don't collide
    const shapes = D.shapes || {};

    const jobs = new Map(), byShape = {};  // zip path -> content type; one video per post and shape
    const posts = (D.posts || []).map(p => {
      const platforms = {};
      for (const pl of ORDER) {
        const given = p.files && p.files[pl];
        if (!given) continue;
        const shape = shapes[pl] || "";
        const cover = `${p.dir}/cover ${shape}.png`, desk = `${p.dir}/${shape}-desk.mp4`, key = `${p.dir}|${shape}`;
        const rel = byShape[key] || (zip.file(root + desk) ? desk : given);
        byShape[key] = rel;
        jobs.set(rel, "video/mp4");
        const hasCover = !!zip.file(root + cover);
        if (hasCover) jobs.set(cover, "image/png");
        platforms[pl] = { shape, rel, coverRel: hasCover ? cover : null,
                          caption: (p.copy || {})[pl] || "", title: pl === "youtube" ? (p.copy || {}).youtube_title || "" : "" };
      }
      return { clip: String(p.clip), title: p.title || "", when: p.when, platforms };
    });

    // sizes first, so the progress bar moves with the bytes
    const files = [];
    for (const [rel, type] of jobs) { const f = zip.file(root + rel); files.push({ rel, type, f, size: f ? f._data.uncompressedSize || 0 : 0 }); }
    const total = files.reduce((a, x) => a + x.size, 0) || 1;
    let sent = 0;
    const ids = {}, failed = {};
    for (const [i, x] of files.entries()) {
      say(`Uploading file ${i + 1} of ${files.length}…`);
      if (!x.f) { failed[x.rel] = "This video wasn't in the zip."; continue; }
      const b = new Blob([await x.f.async("blob")], { type: x.type });
      if (b.size > MAX) { failed[x.rel] = `This video is ${(b.size / 1e6).toFixed(0)} MB, too big for the desk. Post it from the episode zip.`; sent += x.size; continue; }
      try { ids[x.rel] = await uploadFile(b, x.type, n => { sent += n; bar.style.width = Math.min(100, Math.round(100 * sent / total)) + "%"; }); }
      catch (e) { if (e.status === 403) throw new Error("The admin key isn't right any more. Press Lock, then unlock again."); failed[x.rel] = "This file didn't upload. Add the episode again to retry."; }
    }
    bar.style.width = "100%";
    for (const p of posts) for (const x of Object.values(p.platforms)) {
      x.video = ids[x.rel] || null; if (!x.video) x.missing = failed[x.rel];
      x.cover = x.coverRel ? ids[x.coverRel] || null : null;
      delete x.rel; delete x.coverRel;
    }
    say("Saving the posts…");
    await api(`/api/episode/${epId}`, { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ title, episode: D.episode || title, tz: D.tz || "Europe/London", posts, files: Object.values(ids), addedAt: new Date().toISOString() }) });
    const bad = Object.keys(failed).length;
    say(`${title} is on the desk: ${posts.length} posts${bad ? `. ${bad} file${bad > 1 ? "s" : ""} couldn't be uploaded, and their posts say so` : ""}. Send its link from the list below.`, !!bad);
  } catch (err) {
    say(err && err.message ? err.message : "That zip couldn't be read. Try again.", true);
  }
  S.busy = false; $("add").disabled = false; $("zip").value = "";
  load();
}

function renderAdmin() {
  const box = $("eps"), eps = S.all || S.episodes;
  if (!eps.length) { box.innerHTML = `<div class="note">No episodes on the desk yet.</div>`; return; }
  box.replaceChildren(...eps.map(e => {
    const row = document.createElement("div"); row.className = "eprow";
    const ps = e.posts || [], first = ps[0]?.when?.slice(0, 10), last = ps[ps.length - 1]?.when?.slice(0, 10);
    row.innerHTML = `<span class="name"><b>${esc(e.title)}</b><br><span class="note">${ps.length} posts${first ? `, ${esc(first)} to ${esc(last)}` : ""}</span></span>
      ${e.share ? `<button class="primary c-link">Copy link</button><a class="btn" href="${esc(linkFor(e.share))}" target="_blank" rel="noopener">Open ↗</a>` : ""}
      <button class="n-link">New link</button><button class="rm">Remove</button>`;
    const cl = row.querySelector(".c-link"); if (cl) cl.onclick = ev => copy(ev.currentTarget, linkFor(e.share));
    row.querySelector(".n-link").onclick = ev => newLink(ev.currentTarget, e);
    row.querySelector(".rm").onclick = ev => removeEpisode(ev.currentTarget, e);
    return row;
  }));
}

async function newLink(btn, e) {
  if (e.share && btn.dataset.armed !== "1") {
    btn.dataset.armed = "1"; btn.textContent = "Old link stops working. Press again";
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "New link"; }, 4000);
    return;
  }
  btn.disabled = true;
  try { const r = await api(`/api/episode/${e.id}/newlink`, { method: "POST" }); e.share = r.share; renderAdmin(); }
  catch (er) { btn.disabled = false; btn.textContent = "Couldn't make a link. Try again"; }
}

async function removeEpisode(btn, e) {
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1"; btn.textContent = "Press again to remove";
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Remove"; }, 4000);
    return;
  }
  btn.disabled = true; btn.textContent = "Removing…";
  try { await api(`/api/episode/${e.id}`, { method: "DELETE" }); load(); }
  catch (er) { btn.disabled = false; btn.textContent = "Couldn't remove it. Try again"; }
}

// ---------- filters and start
for (const f of ["todo", "done"]) $("f-" + f).onclick = () => { S.filter = f; local.set("desk.filter", f); render(); };
$("f-ep").onchange = ev => { S.ep = ev.target.value; render(); };
render();
load();
setInterval(() => { if (!document.hidden) load(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
