// Posting Desk API: episodes, shared "Posted" ticks and the clip files, all in Netlify Blobs.
//   GET    /api/state                     episodes + ticks (anyone past the site password)
//   POST   /api/tick                      {ep, clip, pl, name, done}
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
  const key = Netlify.env.get("ADMIN_KEY")
  return !!key && req.headers.get("x-admin-key") === key
}
// a tick is one key, so listing the ticks reads them all at once and two people can't overwrite each other
const b64 = (s: string) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
const unb64 = (s: string) => { try { return decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/")))) } catch { return "" } }

async function state() {
  const s = store()
  const [eps, ticks] = await Promise.all([s.list({ prefix: "ep/" }), s.list({ prefix: "tick/" })])
  const episodes = (await Promise.all(eps.blobs.map(b => s.get(b.key, { type: "json" })))).filter(Boolean)
  const t: Record<string, { name: string; at: string }> = {}
  for (const b of ticks.blobs) {
    const [ep, clip, pl, name, at] = b.key.slice(5).split("~")
    t[`${ep}~${clip}~${pl}`] = { name: unb64(name), at: unb64(at) }
  }
  return { episodes, ticks: t }
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
    if (what === "state" && req.method === "GET") return json(await state())
    if (what === "tick" && req.method === "POST") {
      const { ep, clip, pl, name, done } = await req.json()
      if (![ep, String(clip), pl].every(x => SAFE.test(String(x)))) return json({ error: "bad tick" }, 400)
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
      if (req.method === "PUT") {
        const doc = await req.json()
        const old = await s.get(`ep/${id}`, { type: "json" }) as { files?: string[] } | null
        await s.setJSON(`ep/${id}`, { ...doc, id })
        const keep = new Set(doc.files || [])
        for (const f of old?.files || []) if (!keep.has(f)) await deleteFile(f)
        return json({ ok: true })
      }
      if (req.method === "DELETE") {
        const old = await s.get(`ep/${id}`, { type: "json" }) as { files?: string[] } | null
        for (const f of old?.files || []) await deleteFile(f)
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
