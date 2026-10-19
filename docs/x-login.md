# Log in to X from the browser (`login x`)

`unhoard login x` signs you in to X with OAuth 2.0 (Authorization Code with PKCE), so you do not have
to create a user token by hand and export it. You still bring **your own X developer app**: this project cannot
ship a client id, and your app's access plan decides whether the bookmarks endpoint works.

## One-time setup in the X developer portal

1. Open the [X developer portal](https://developer.x.com/en/portal/dashboard) and create (or pick) a project and an app.
2. In the app's **User authentication settings**, turn on **OAuth 2.0**.
   - App type **Native App** is fine and needs no secret.
   - **Web App, Automated App or Bot** also works. It is a *confidential* client with a client secret.
3. Set the **Callback URI / Redirect URL** to exactly

   ```text
   http://127.0.0.1:8766/callback
   ```

   If you use `--port N`, use `http://127.0.0.1:N/callback` instead. X compares the URL character for
   character. Set any Website URL. App permissions: **Read**.
4. Copy the OAuth 2.0 **Client ID** (and the **Client Secret** for a confidential app).

## Log in

```bash
export X_CLIENT_ID=...          # or: --client-id ...   or:  [x] client_id = "..."  in config.toml
export X_CLIENT_SECRET=...      # confidential apps only; env is preferred, [x] client_secret also works
unhoard login x
```

Without a client id, the command prints these steps and the exact callback URL it will use, and exits.

What happens:

1. The command starts a one-shot web server on `127.0.0.1` (never another interface) and opens your browser at
   X's authorize page. The address is printed as well, for a machine without a browser.
2. You approve the app. X redirects to the callback URL. The command checks the `state` value, exchanges the
   code (with the PKCE verifier) for tokens, and calls `GET /2/users/me`.
3. It prints `Logged in as @name (id ...)` and, when `[x] user_id` in your config is empty, writes the numeric id there.

Scopes requested: `tweet.read users.read bookmark.read offline.access` (`offline.access` is what makes X issue a
refresh token). The command waits five minutes (`--timeout` changes this), then gives up and saves nothing.

### On a remote or headless machine

The browser must reach the callback port on the machine where the command runs. Forward it, then open the
printed address in the browser on your own computer:

```bash
ssh -L 8766:127.0.0.1:8766 <the-machine>
unhoard login x --no-browser
```

### Logging in again

If a login is already saved, `unhoard login x` asks before it replaces it:

```text
A saved X login exists. Sign it out (revoke it at X and delete it) and log in again? [y/N]
```

Answer `y` to continue; anything else leaves the saved login as it is. Without a terminal the question cannot be
asked, so the command stops with a message instead. `unhoard login x --replace` answers yes up front, for scripts
and for a terminal where you do not want the question. `unhoard logout x` on its own signs the login out and does
not start a new one.

## Where the login is kept, and how it is used

- The tokens (access token, refresh token, expiry, scopes, client id, your username) are in
  `<data dir>/x_oauth.json`. The file is created with mode `0600` and replaced atomically, so it is never
  readable by other users, not even for a moment. The data directory is the one `status` shows.
- The tokens are **never** written to the SQLite database or to `config.toml`, never put in an environment variable,
  and never printed. The client secret is not stored at all; it comes from `X_CLIENT_SECRET` or `[x] client_secret`.
- `sync` picks its token in this order: `X_BEARER_TOKEN`, then `[x] bearer_token`, then the saved login.
  The first two are used exactly as before.
- With the saved login, `sync` refreshes the access token when it is within a minute of expiring (X access tokens
  last about two hours) and again if X answers 401. X rotates refresh tokens: the new one is saved before anything
  else happens, and a lock keeps two overlapping `sync` runs from using the same refresh token.
- If X rejects the refresh token (revoked app, a token older than X allows, password change), `sync` stops with a
  message to run `unhoard login x` again. A network error or an X outage does not ask for a new login.
- `unhoard status` shows `x_token_source` (`env`, `config` or `login`) and, for a login, the user,
  expiry and file mode. `unhoard doctor` has an `X token` row with the same facts. Neither prints a token.
- `unhoard logout x` asks X to revoke the tokens (best effort; the endpoint is
  `POST /2/oauth2/revoke`) and deletes the file. It deletes the file even if the revoke fails, and then tells you
  where to remove the app by hand (X Settings, Security, Connected apps).

## Security notes

- **Loopback only.** The callback server binds `127.0.0.1`, serves only `GET /callback`, handles one login, and
  closes. It answers with fixed pages: nothing from the request is ever put into HTML.
- **State and PKCE.** A fresh random `state` and a fresh S256 code challenge are made for each login. An answer
  with a missing or wrong `state` is refused (HTTP 400) and ignored; the command keeps waiting for the real one.
  X's authorization code lives only 30 seconds, so the exchange happens right away.
- **Another user on the same machine** can reach `127.0.0.1:8766` while a login is running, but cannot complete it
  without the `state` value, and cannot read the token file. On a shared machine, still prefer a short wait.
- **The token is never put into an agent's environment or prompt.** The saved tokens are read by `sync` (and
  `logout`). The environments of the enrichment agents and of the launched worker do not contain `X_BEARER_TOKEN`
  or `X_CLIENT_SECRET`, and the enrichment agents work in a scratch directory, not in the data directory. This is
  not a sandbox: the launched worker runs as you, so it could still read the token file if it went looking (it is
  mode 0600, owned by you). Treat a worker like any program you run yourself.
- **No redirects.** Calls that carry a token or the client secret refuse HTTP redirects,
  so a credential is never copied to another address. Running `login x` while a login is saved first signs
  that one out (revokes it at X and deletes the file, and says so) and only then starts the new authorization, so a
  revoke can never hit the new tokens, whether X revokes per token or per user and app. If the new login is cancelled
  or fails, you are left logged out. Because that is destructive, it is never silent: on a terminal the command asks
  `[y/N]` first (anything but `y` keeps the saved login and exits), and with no terminal (a script, cron, an agent)
  it refuses and changes nothing unless you pass `--replace`. `logout` runs under the same lock a refresh uses, so a refresh cannot run in the
  middle of it. Sending the token over plain `http` to another machine is refused (`[x] api_base` must be `https`),
  for `sync` as well as for the login.
- **https only.** Tokens and the client secret are sent only to an `https` API base (or to a loopback address,
  which tests use). A different `[x] api_base` over plain http is refused.
- A token in the process list or shell history is the usual way these leak. This command never takes a token
  or a secret as an argument.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| The browser shows an X error before the redirect | The callback URL in the app is not exactly the one printed, or OAuth 2.0 is not on. |
| `X refused the code exchange` | Wrong client id, a confidential app without `X_CLIENT_SECRET`, or the code waited longer than 30 seconds. |
| `cannot listen on 127.0.0.1:8766` | Another login (or program) uses the port: pick `--port` and change the callback URL to match. |
| Logged in, but `sync` says HTTP 403 | Your API plan or app permissions do not include the bookmarks endpoint (`bookmark.read`). |
| `X did not grant bookmark.read` | The app's permissions do not allow it. Fix them in the portal, then log in again. |

## X documentation this follows

- Authorization code flow with PKCE: <https://docs.x.com/resources/fundamentals/authentication/oauth-2-0/authorization-code>
- User access token (token, refresh and revoke endpoints): <https://docs.x.com/resources/fundamentals/authentication/oauth-2-0/user-access-token>
- `GET /2/users/me`: <https://docs.x.com/x-api/users/get-my-user>
