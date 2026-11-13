// Posting Desk API: episodes, shared "Posted" ticks and the clip files, all in Netlify Blobs.
//   GET    /api/state?e=<link>            one episode (a client's private link) and its ticks; everything for the admin
//   POST   /api/tick                      {e (the link) or admin key, ep, clip, pl, name, done}
//   POST   /api/episode/:id/newlink       a fresh private link (the old one stops working)   admin
//   GET    /api/admin                     checks the admin key (header x-admin-key)
//   PUT    /api/file/:id/meta             {type, size, parts}      admin
//   PUT    /api/file/:id/part/:n          raw bytes (< 4 MB)       admin
//   PUT    /api/episode/:id               the episode's posts      admin
//   DELETE /api/episode/:id               episode, its files, its ticks   admin
//   GET    /media/:id[?download=name]     a stored file, with Range support for video
import { getStore } from "@netlify/blobs"
import type { Config, Context } from "@netlify/functions"

const store = () => getStore({ name: "desk", consistency: "strong" })
const SAFE = /^[A-Za-z0-9_-]{1,80}$/
const json = (data: unknown, status = 200) => Response.json(data, { status, headers: { "cache-control": "no-store" } })
const isAdmin = (req: Request) => {
  const key = Netlify.env.get("ADMIN_KEY") || "", got = req.headers.get("x-admin-key") || ""
  if (!key || got.length !== key.length) return false
  let diff = 0  // compare every character, so the time taken doesn't hint at the key
  for (let i = 0; i < key.length; i++) diff |= key.charCodeAt(i) ^ got.charCodeAt(i)
  return diff === 0
}
// a tick is one key, so listing the ticks reads them all at once and two people can't overwrite each other
const b64 = (s: string) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
const unb64 = (s: string) => { try { return decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/")))) } catch { return "" } }

async function ticksFor(prefix: string) {
  const s = store()
  const t: Record<string, { name: string; at: string }> = {}
  for (const b of (await s.list({ prefix })).blobs) {
    const [ep, clip, pl, name, at] = b.key.slice(5).split("~")
    t[`${ep}~${clip}~${pl}`] = { name: unb64(name), at: unb64(at) }
  }
  return t
}

async function state(req: Request) {
  const s = store()
  if (isAdmin(req)) {
    const eps = await s.list({ prefix: "ep/" })
    const episodes = (await Promise.all(eps.blobs.map(b => s.get(b.key, { type: "json" })))).filter(Boolean)
    return json({ admin: true, episodes, ticks: await ticksFor("tick/") })
  }
  const ep = await episodeForLink(new URL(req.url).searchParams.get("e"))
  if (!ep) return json({ error: "This link doesn't open an episode. Ask whoever sent it for a new one." }, 404)
  const { share, files, ...shown } = ep as Record<string, unknown>
  return json({ admin: false, episodes: [shown], ticks: await ticksFor(`tick/${ep.id}~`) })
}

async function episodeForLink(link: string | null) {
  if (!link || !/^[A-Za-z0-9_-]{16,64}$/.test(link)) return null
  const s = store()
  const id = await s.get(`share/${link}`)
  if (!id || !SAFE.test(id)) return null
  const ep = await s.get(`ep/${id}`, { type: "json" }) as { id: string; share?: string } | null
  return ep && ep.share === link ? ep : null
}

const newLink = () => {
  const a = new Uint8Array(18); crypto.getRandomValues(a)
  return btoa(String.fromCharCode(...a)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

async function media(req: Request, id: string) {
  const s = store()
  const meta = await s.get(`file/${id}/meta`, { type: "json" }) as { type: string; size: number; parts: number[] } | null
  if (!meta) return new Response("Not found", { status: 404 })
  const size = meta.size
  let start = 0, end = size - 1, status = 200
  const range = req.headers.get("range")
  const m = range && /bytes=(\d*)-(\d*)/.exec(range)
  if (m && (m[1] || m[2])) {
    if (m[1]) { start = +m[1]; end = m[2] ? Math.min(+m[2], size - 1) : size - 1 }
    else { start = Math.max(size - +m[2], 0) }
    if (start > end || start >= size) return new Response(null, { status: 416, headers: { "content-range": `bytes */${size}` } })
    status = 206
  }
  const headers: Record<string, string> = {
    "content-type": meta.type, "accept-ranges": "bytes", "content-length": String(end - start + 1),
    "cache-control": "private, max-age=31536000, immutable",
  }
  if (status === 206) headers["content-range"] = `bytes ${start}-${end}/${size}`
  const dl = new URL(req.url).searchParams.get("download")
  if (dl) headers["content-disposition"] = `attachment; filename*=UTF-8''${encodeURIComponent(dl)}`
  // which stored parts cover start..end
  const offsets: [number, number, number][] = []
  let pos = 0
  meta.parts.forEach((len, n) => { if (pos + len > start && pos <= end) offsets.push([n, Math.max(start - pos, 0), Math.min(end - pos, len - 1)]); pos += len })
  const body = new ReadableStream({
    async pull(ctrl) {
      const next = offsets.shift()
      if (!next) return ctrl.close()
      const [n, a, b] = next
      const buf = await s.get(`file/${id}/${n}`, { type: "arrayBuffer" })
      if (!buf) return ctrl.error(new Error("missing part"))
      ctrl.enqueue(new Uint8Array(buf, a, b - a + 1))
    },
  })
  return new Response(body, { status, headers })
}

export default async (req: Request, context: Context) => {
  const url = new URL(req.url)
  const parts = url.pathname.split("/").filter(Boolean)  // ["api", ...] or ["media", id]
  const s = store()
  try {
    if (parts[0] === "media" && parts[1] && SAFE.test(parts[1]) && req.method === "GET") return await media(req, parts[1])
    const [, what, id, sub, n] = parts
    if (what === "state" && req.method === "GET") return await state(req)
    if (what === "tick" && req.method === "POST") {
      const body = await req.json().catch(() => null)
      if (!body || typeof body !== "object") return json({ error: "bad tick" }, 400)
      const { e, ep, clip, pl, name, done } = body
      if (![ep, clip, pl].every(x => (typeof x === "string" || typeof x === "number") && SAFE.test(String(x)))) return json({ error: "bad tick" }, 400)
      if (!isAdmin(req) && (await episodeForLink(e))?.id !== ep) return json({ error: "This link can't tick that post." }, 403)
      const prefix = `tick/${ep}~${clip}~${pl}~`
      const old = await s.list({ prefix })
      await Promise.all(old.blobs.map(b => s.delete(b.key)))
      if (done) await s.set(prefix + b64(String(name || "Someone").slice(0, 60)) + "~" + b64(new Date().toISOString()), "")
      return json({ ok: true })
    }
    if (!isAdmin(req)) return json({ error: "That admin key isn't right." }, 403)
    if (what === "admin" && req.method === "GET") return json({ ok: true })
    if (what === "file" && id && SAFE.test(id) && req.method === "PUT") {
      if (sub === "meta") { await s.setJSON(`file/${id}/meta`, await req.json()); return json({ ok: true }) }
      if (sub === "part" && /^\d{1,3}$/.test(n || "")) { await s.set(`file/${id}/${+n}`, await req.arrayBuffer()); return json({ ok: true }) }
    }
    if (what === "episode" && id && SAFE.test(id)) {
      if (sub === "newlink" && req.method === "POST") {
        const old = await s.get(`ep/${id}`, { type: "json" }) as { share?: string } | null
        if (!old) return json({ error: "not found" }, 404)
        if (old.share) await s.delete(`share/${old.share}`)
        const share = newLink()
        await s.set(`share/${share}`, id)
        await s.setJSON(`ep/${id}`, { ...old, share })
        return json({ ok: true, share })
      }
      if (req.method === "PUT") {
        const doc = await req.json().catch(() => null)
        if (!doc || !Array.isArray(doc.posts)) return json({ error: "That episode has no posts in it." }, 400)
        const old = await s.get(`ep/${id}`, { type: "json" }) as { files?: string[]; share?: string; title?: string } | null
        // never let one client's episode replace another's that happens to share the id
        if (old && old.title && doc.title && old.title !== doc.title)
          return json({ error: `"${old.title}" is already on the desk under this name. Rename the zip, or remove that episode first.` }, 409)
        const share = old?.share || newLink()  // adding an episode again keeps its link
        await s.set(`share/${share}`, id)
        await s.setJSON(`ep/${id}`, { ...doc, id, share })
        const keep = new Set(doc.files || [])
        for (const f of old?.files || []) if (!keep.has(f)) await deleteFile(f)
        return json({ ok: true, share })
      }
      if (req.method === "DELETE") {
        const old = await s.get(`ep/${id}`, { type: "json" }) as { files?: string[]; share?: string } | null
        for (const f of old?.files || []) await deleteFile(f)
        if (old?.share) await s.delete(`share/${old.share}`)
        const t = await s.list({ prefix: `tick/${id}~` })
        await Promise.all(t.blobs.map(b => s.delete(b.key)))
        await s.delete(`ep/${id}`)
        return json({ ok: true })
      }
    }
    return json({ error: "not found" }, 404)
  } catch (e) {
    console.error(e)
    return json({ error: "Something went wrong on the server. Try again." }, 500)
  }
}

async function deleteFile(id: string) {
  if (!SAFE.test(id)) return
  const s = store()
  const all = await s.list({ prefix: `file/${id}/` })
  await Promise.all(all.blobs.map(b => s.delete(b.key)))
}

export const config: Config = { path: ["/api/*", "/media/*"] }
