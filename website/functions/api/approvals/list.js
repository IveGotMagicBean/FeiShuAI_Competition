// GET /api/approvals/list?only_pending=1
// Phone-side: fetch this instance's approvals. Auth via x-mobile-token.

import { json, err, preflight, authMobile } from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestGet({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

  const mobile_token = request.headers.get("x-mobile-token") || "";
  const auth = await authMobile(env, mobile_token);
  if (!auth) return err(401, "auth failed");

  const url = new URL(request.url);
  const only_pending = url.searchParams.get("only_pending") !== "0";

  const prefix = `apr:${auth.instance_id}:`;
  const listing = await env.PAIR_KV.list({ prefix, limit: 100 });
  const approvals = [];
  for (const k of listing.keys) {
    const val = await env.PAIR_KV.get(k.name);
    if (!val) continue;
    try {
      const a = JSON.parse(val);
      if (only_pending && a._state !== "pending") continue;
      approvals.push(a);
    } catch { /* skip */ }
  }
  approvals.sort((a, b) => (b._ts || 0) - (a._ts || 0));

  return json({ approvals, count: approvals.length });
}
