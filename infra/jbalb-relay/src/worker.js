// Fetch relay for JBALB (jbalb.sarawak.gov.my) utility notices.
//
// Status (2026-11-11): NOT usable from Fly.io. The .gov.my firewall drops
// datacenter-ASN connections at TCP level (connect timeout), and Cloudflare's
// edge additionally returns error 1010 for requests to workers.dev coming from
// Fly's network — so a live-invoked relay from the bot never reaches this code.
// It DOES work when invoked from a non-blocked network (e.g. a Malaysian
// residential connection), and the cron+KV decoupled variant never fired.
// The working production solution is scripts/jbalb_local_relay.py (fetch from a
// Malaysian machine, insert into the shared DB). This worker is kept as a
// documented artifact of that investigation.
//
// Abuse resistance: shared-secret header + single allowlisted host + GET only.

const ALLOWED_HOST = "jbalb.sarawak.gov.my";

export default {
  async fetch(request, env) {
    if (request.method !== "GET") {
      return new Response("method not allowed", { status: 405 });
    }
    if (!env.RELAY_SECRET || request.headers.get("x-relay-secret") !== env.RELAY_SECRET) {
      return new Response("unauthorized", { status: 401 });
    }
    const target = new URL(request.url).searchParams.get("u");
    if (!target) {
      return new Response("missing ?u=", { status: 400 });
    }
    let t;
    try {
      t = new URL(target);
    } catch {
      return new Response("bad url", { status: 400 });
    }
    if (t.hostname !== ALLOWED_HOST) {
      return new Response("host not allowed", { status: 403 });
    }
    const upstream = await fetch(t.toString(), {
      redirect: "follow",
      cf: { cacheTtl: 300, cacheEverything: false },
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") || "text/html; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  },
};
