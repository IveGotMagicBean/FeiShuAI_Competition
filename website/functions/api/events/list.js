// GET /api/events/list?since=<ts_ms>&limit=<n>
// Phone-side: fetch this instance's recent events. Auth via x-mobile-token header.
//
// Returns: { events: [...], count, next_since }
// `next_since` = the highest ts seen, so the phone can poll efficiently:
//   GET /api/events/list?since=<next_since>

import { json, err, preflight, authMobile } from "../../_lib.js";

const MAX_LIMIT = 200;

export const onRequestOptions = preflight;

export async function onRequestGet({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

  const mobile_token = request.headers.get("x-mobile-token") || "";
  const auth = await authMobile(env, mobile_token);
  if (!auth) return err(401, "auth failed");

  const url = new URL(request.url);
  const since = parseInt(url.searchParams.get("since") || "0", 10);
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10), MAX_LIMIT);

  const prefix = `evt:${auth.instance_id}:`;
  // Build a key to start scanning *after* `since`. KV list returns lexically-sorted keys,
  // and our keys are zero-padded ms timestamps, so lex order == time order.
  const startKey = since > 0
    ? `${prefix}${(since + 1).toString().padStart(13, "0")}`
    : prefix;

  const listing = await env.PAIR_KV.list({ prefix, start: startKey, limit });
  const events = [];
  let next_since = since;

  for (const k of listing.keys) {
    const val = await env.PAIR_KV.get(k.name);
    if (!val) continue;
    try {
      const ev = JSON.parse(val);
      events.push(ev);
      if (ev._ts && ev._ts > next_since) next_since = ev._ts;
    } catch { /* skip malformed */ }
  }

  return json({ events, count: events.length, next_since });
}
