# Home setup: serving A-eye over Tailscale

**Status:** written only. None of these commands have been run by an agent, because they change network
settings on the Mac. The owner runs them.

A-eye runs at home on the Mac (the server) and the GPU box (vision, speech, Kokoro). The stand phone in the
kitchen needs HTTPS, because Safari only gives a page the camera and mic over HTTPS. Tailscale provides that
HTTPS at home. The Cloudflare Tunnel stays as the fallback.

Before any tunnel, set `A_EYE_TOKEN` to a long random secret in the server environment
and restart `make server`, or configure both `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD`
with working Access sign-in. Do not expose an unconfigured server. `make phone` prints
this prerequisite first and withholds tunnel commands until credentials are configured.
Proxy requests do not count as trusted localhost, including private Tailscale Serve.
Custom Uvicorn launches need `--no-proxy-headers` to preserve the TCP peer for pairing limits.

## Option 1: Tailscale Serve, private to your tailnet (recommended)

Only devices signed in to your tailnet can open the page. Nothing is published on the internet.

On the Mac that runs `make server` (port 8000):

```sh
tailscale serve --bg 8000          # HTTPS on https://<mac-name>.<tailnet>.ts.net -> http://127.0.0.1:8000
tailscale serve status             # shows what is being served
```

On the stand phone:

1. Install the Tailscale app.
2. Sign in to the same tailnet, either the caregiver's account or a shared device.
3. Open `https://<mac-name>.<tailnet>.ts.net/agent.html?token=<A_EYE_TOKEN>`, or scan a live call's camera QR.

**What it exposes:** the A-eye web page and its `/ws2` and `/api` routes, only to devices on your tailnet. It does
not expose the GPU box ports. The box is already reachable only inside the tailnet, and the server talks to it
there.

**To stop:** `tailscale serve reset`. That removes every Serve rule on this Mac.

## Option 2: Tailscale Funnel, public on the internet (only when the phone cannot join the tailnet)

Funnel publishes the same HTTPS address to the whole internet, so anyone who knows the URL can reach the page.
Turn on the app's own lock first:

```sh
# 1) Set a long random token in .env and restart the server:
#    A_EYE_TOKEN=<32+ random characters>
# 2) Publish:
tailscale funnel --bg 8000         # public https://<mac-name>.<tailnet>.ts.net -> http://127.0.0.1:8000
tailscale funnel status
```

On the stand phone, open `https://<mac-name>.<tailnet>.ts.net/agent.html?token=<A_EYE_TOKEN>`.

**What it exposes:** the A-eye page, `/ws2` and `/api` to the internet, behind `A_EYE_TOKEN`. There is no
Cloudflare Access sign-in in front of it. Funnel must be allowed for the tailnet in the admin console, under
Access controls, through the `funnel` node attribute.

**To stop:** `tailscale funnel reset` (checked against `tailscale funnel --help` on this Mac).

## Fallback: the Cloudflare Tunnel (the current public URL)

The Cloudflare Tunnel, with Access sign-in in front (see `server/auth.py`), keeps working as it does today. Use it
from outside the home, or when Tailscale is down. Keep the Access policy in place. Cloudflare sees the traffic
(`docs/privacy.md`, gap 7). With Tailscale Serve, the traffic stays on WireGuard between your own devices.

## What leaves the home

That depends on the three switches in Settings > Privacy, not on the transport. The "What leaves your home"
panel in Settings lists what is on right now. In Private mode, nothing leaves the home: with Tailscale Serve, the
page, the camera frames and the speech all stay on your own devices.
