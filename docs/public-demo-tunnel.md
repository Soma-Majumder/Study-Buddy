# Public demo through a Cloudflare Tunnel

Puts the live student workbook on `https://study-buddy-demo-seven.vercel.app/student/` while the Python backend keeps running on your own computer. Free, no card. It is a demo setup: the site works only while your computer, the server and the tunnel are all running.

```
Student browser -> Vercel (static pages + demo/api/proxy.js) -> Cloudflare quick tunnel -> your laptop (student_workbook.py)
```

## What protects it

- The browser never sees the backend URL, the proxy secret or the Anthropic key. They live in Vercel env vars and on your laptop.
- The backend still binds `127.0.0.1`. Only the tunnel reaches it, and it answers a non-loopback Host only when the request carries `X-Proxy-Secret` (checked in constant time). The per-run workbook token still applies.
- `demo/api/proxy.js` forwards only the eight workbook routes.
- `--public-proxy` caps new study sessions at 20 per UTC day (`--max-starts-per-day N` to change). Each session makes about 6 paid Anthropic calls. Also set a monthly spend limit in the Anthropic console.
- All visitors share the one local learner profile. Sessions are reachable only by their random IDs, but this is not per-student accounts.

## One-time setup

```sh
brew install cloudflared
```

Create a long random secret and keep it:

```sh
python3 -c 'import secrets;print(secrets.token_urlsafe(32))'
```

In Vercel (project `study-buddy-demo` -> Settings -> Environment Variables, Production) add:

| Name | Value |
| --- | --- |
| `PROXY_SECRET` | the secret above |
| `BACKEND_URL` | set in the run steps below (changes every run) |

## Each time you run the demo

1. Start the backend (Terminal 1). The Anthropic key comes from your `.env`; unset a stale `ANTHROPIC_API_KEY` first if your shell has one.

   ```sh
   export STUDY_BUDDY_PROXY_SECRET='<the secret>'
   env -u ANTHROPIC_API_KEY .venv/bin/python -B scripts/student_workbook.py --agents live --allow-paid-calls --public-proxy --env-file .env
   ```

2. Start the tunnel (Terminal 2) and copy the `https://<random>.trycloudflare.com` URL it prints:

   ```sh
   cloudflared tunnel --url http://127.0.0.1:8778
   ```

3. Point Vercel at it and redeploy, because env var changes apply to new deployments:

   ```sh
   vercel env rm BACKEND_URL production --yes; printf '%s' 'https://<random>.trycloudflare.com' | vercel env add BACKEND_URL production
   vercel --prod
   ```

   The dashboard works too: edit `BACKEND_URL`, then redeploy the latest production deployment.

4. Open the site and click Biology. If the backend is off the page says the study backend is offline; reload after starting it.

Stop the tunnel and the server when you are done. Restarting the server issues a new workbook token, so reload open pages.

## Limits

- A quick tunnel URL changes every run, so step 3 repeats. A fixed URL needs a named tunnel, which needs a domain on a free Cloudflare account.
- A study session start can take a couple of minutes. The proxy function allows up to 300 seconds, which is the Vercel Hobby maximum.
- Progress is stored in `.student-local/workbook-live.sqlite` on your computer. Back it up if it matters.
