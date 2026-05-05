// POST /api/pair/redeem
// Phone-side call: trade a 6-char pair code for a long-lived mobile_token.
// The pair code is one-shot — first redeem wins, subsequent attempts fail.
//
// Body: { code: "ABC123" }
// Response: { instance_id, mobile_token }

import { json, err, preflight, readJson, newToken, TTL_INSTANCE } from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestPost({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

  const body = (await readJson(request)) || {};
  const code = String(body.code || "").trim().toUpperCase();
  if (!/^[A-Z2-9]{6}$/.test(code)) return err(400, "invalid code format (expected 6 chars A-Z 2-9)");

  const instance_id = await env.PAIR_KV.get(`pair:${code}`);
  if (!instance_id) return err(404, "code expired or not found");

  // One-shot: delete the pair code immediately so it can't be replayed.
  await env.PAIR_KV.delete(`pair:${code}`);

  const raw = await env.PAIR_KV.get(`inst:${instance_id}`);
  if (!raw) return err(410, "instance expired");
  const inst = JSON.parse(raw);

  // Mint fresh mobile_token. Replace any old one (revokes previous phone).
  if (inst.mobile_token) {
    await env.PAIR_KV.delete(`mtok:${inst.mobile_token}`);
  }
  const mobile_token = newToken();
  inst.mobile_token = mobile_token;
  inst.last_seen = Date.now();

  await env.PAIR_KV.put(`inst:${instance_id}`, JSON.stringify(inst), {
    expirationTtl: TTL_INSTANCE,
  });
  await env.PAIR_KV.put(`mtok:${mobile_token}`, instance_id, {
    expirationTtl: TTL_INSTANCE,
  });

  return json({ instance_id, mobile_token });
}
