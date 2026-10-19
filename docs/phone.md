# Using unhoard from your phone

You can start and answer cards from your phone, over a private network that you control. This is opt-in. By default
unhoard does what it always did: the card page listens on `127.0.0.1` only, and nothing else can reach it.

`unhoard phone` prints the exact steps for your machine. It changes nothing on its own.

## The server never binds anything but loopback

`unhoard serve` always binds `127.0.0.1`. There is no option to change that. Phone access works by putting a small
proxy in front of it on the same machine (Tailscale's `serve`, or an SSH tunnel). The proxy is what is reachable from
your phone; unhoard only learns the host name it is reached by (`allowed_hosts`, below). Its Host and Origin checks
then accept that name only in the shape the proxy uses (the rules are listed under "What the server checks").

## Tailscale (recommended)

[Tailscale](https://tailscale.com) gives your machines private names (`mybox.tail1234.ts.net`) that only your tailnet
can reach, with HTTPS certificates. `tailscale serve` shares a local port on the tailnet.

1. Install Tailscale on this machine and on your phone, log in to the same tailnet on both, and turn on MagicDNS and
   HTTPS certificates in the Tailscale admin console.
2. Allow this machine's name in the config. Either run `unhoard phone --yes` (it looks up the name with
   `tailscale status --json`, which only reads) or add it yourself:

   ```toml
   [server]
   allowed_hosts = ["mybox.tail1234.ts.net"]
   ```

   Entries are host names only, lower-cased: no scheme, no port, no path, no wildcard, no IP address. See "What the
   server checks" below for how the name is matched.
3. Start the page: `unhoard serve --no-open`.
4. Share it on your tailnet. Run this yourself; unhoard never runs it:

   ```bash
   tailscale serve --bg 8765
   ```

   (8765 is the default `[server] port`.) `tailscale serve status` shows what is shared; `tailscale serve reset`
   stops sharing, and clears every Serve entry on the machine.
5. On your phone, with the Tailscale app connected, open `https://mybox.tail1234.ts.net/`. Add it to your home screen
   if you like.

![The card page on a phone: the first card with the post, why you saved it and the task](images/phone-cards.png)

**Show brief** shows the task brief on the page (with a Copy button), so you can read it on the phone and paste it
into any agent app:

![The brief panel on a phone after pressing Show brief](images/phone-brief.png)

If you use Omnigent, its web UI can be shared over the tailnet in the same way (see Omnigent's own documentation for
its port and settings), so the session that Go starts is one you can open on your phone too.

## SSH forwarding (no configuration change)

If you have an SSH app on your phone, you do not need `allowed_hosts` at all. Forward the port, then open the loopback
address on the phone:

```bash
ssh -L 8765:127.0.0.1:8765 you@your-machine
# then open http://127.0.0.1:8765 in the phone's browser
```

The browser then talks to a loopback address, so the default checks apply unchanged.

## What the server checks

For a request that arrives by an `allowed_hosts` name (not by `127.0.0.1` or `localhost`), the server requires all of:

- **Host** is the name, bare or with a port, but **never the server's own port** (8765 by default). `tailscale serve`
  sends the bare name, or the name with its HTTPS port. A web page that reaches the server directly, as in a DNS
  rebinding attack where the page's own name is pointed at `127.0.0.1`, is loaded from `http://NAME:8765`, so its Host
  carries the server's own port and is refused.
- **Origin**, when the browser sends one, is `https://<name>` or `https://<name>:<port>` (again never the server's own
  port). `http://` origins are refused for these names: the proxy is the only way in and it speaks HTTPS. An Origin for
  one of these names is not accepted on a request that arrived by `127.0.0.1` or `localhost`.
- **A `Tailscale-User-Login` header.** `tailscale serve` adds it to requests from users on your tailnet. Requests that
  come through Funnel carry no identity header, so a funnel started by mistake gets "forbidden" instead of the page.
  The value is only checked for being present: it is never read, logged or shown, and it is not a login. Any program
  on this machine can send the header, so it is a guard against a mistaken funnel, not a defence against local
  software. Requests from tagged Tailscale devices carry no user identity either, so they are refused too.
  - Behind another HTTPS proxy that you trust to be the only way in (and that sends no such header), set
    `[server] require_tailscale_identity = false`. **Warning:** with it off, nothing here stops a mistaken funnel or
    a proxy that is open to the internet, and `unhoard doctor` says so. Leave it on with Tailscale. A value that is
    not `true` or `false` is reported by `unhoard status` and leaves the check on.
- State-changing requests still need the custom `X-Unhoard` header, as for loopback.

Requests by `127.0.0.1` and `localhost` are checked as before and need no identity header, so SSH forwarding is
unchanged.

## Security notes

- **Use `tailscale serve`, never `tailscale funnel`.** Serve is reachable by your tailnet only. Funnel publishes the
  port on the public internet. unhoard has no login of its own, so a funneled page would let anyone on the internet
  press Go.
- **Anyone who can reach the machine on your tailnet can press Go**, and Go can start an agent that runs as you.
  Share your tailnet only with people you trust, or restrict who may reach this machine with a Tailscale ACL.
- `allowed_hosts` only widens the Host and Origin checks to the names you list, under the rules above. It does not
  open a port. An invalid entry (a wildcard, an IP address, a URL) is refused with a message by `unhoard status` and
  ignored, so a typo never widens access. `unhoard phone --yes` will not write into an `allowed_hosts` that is not a
  list of valid names; it stops and tells you to fix the entry by hand.
- `unhoard doctor` shows a `phone access` row while `allowed_hosts` is set, and `unhoard status` lists the names.
- To turn phone access off: remove the entry (or the whole line), restart `unhoard serve`, and stop sharing
  (`tailscale serve reset`).

## At phone width

The card page is a single column at phone width, with Go / Later / Skip buttons at least 44 px tall, and nothing
scrolls sideways.
