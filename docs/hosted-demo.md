# Run Study Buddy without your laptop

Vercel continues serving `demo/` and its API proxy. A single Render Docker web service runs the existing Python controller and Node agent adapters. The selected free plan stores SQLite temporarily; records disappear after sleep, restart or redeploy. No Cloudflare tunnel is needed.

This is a shared assignment demo, not separate authenticated student accounts. All visitors use one learner profile. The daily session-start limit remains enabled; it is not a dollar spending cap. Set a provider spending limit separately. The selected hosting plan is free within Render limits; Claude usage is still billed separately.

## Prepare GitHub

Merge the hosting files into the branch Render will deploy (normally `main`). The Docker context is the repository root, not `demo/`. Local `.env`, databases, node_modules and evaluation logs are excluded from the image. This setup creates a fresh hosted database; it does not upload your laptop's saved sessions.

## Create the Render service

1. Create a Render account at https://dashboard.render.com/register and connect GitHub. Grant access to `Soma-Majumder/Study-Buddy`.
2. Choose **New → Blueprint**, select the repository and deployment branch, and use the root `render.yaml`.
3. Select Free compute ($0/month) with no disk. It sleeps after inactivity and has a cold-start delay. Keep one instance.
4. Enter `ANTHROPIC_API_KEY` privately in Render. Never paste it into a commit or chat.
5. Enter `STUDY_BUDDY_PROXY_SECRET`: use the SAME existing value as Vercel's `PROXY_SECRET`, or generate a new strong secret and update both dashboards. Minimum length is 32 characters. Never include it in a URL.
6. Deploy. The Docker entrypoint uses Render's `PORT`, binds `0.0.0.0`, and saves to `/tmp/study-buddy/workbook-live.sqlite`. Startup prepares/reconciles the database before reporting healthy. Builds do not need provider secrets.
7. Verify `https://YOUR-SERVICE.onrender.com/healthz` returns `{"status":"ok"}`. Direct `/api/config` without the proxy secret should return 403; this is expected.

If you create a Web Service manually instead of a Blueprint: Docker runtime, root directory unset, Dockerfile `./Dockerfile`, health path `/healthz`, Free instance, no disk, and the environment variables from `render.yaml`.

## Connect Vercel

In the existing `study-buddy-demo` project, set Production `BACKEND_URL` to `https://YOUR-SERVICE.onrender.com` (no `/api` suffix). Confirm Production `PROXY_SECRET` matches the Render secret. Redeploy Vercel so its server-side proxy gets the new values. These secrets are server-only; do not create `NEXT_PUBLIC_*` versions.

## Verify before claiming success

- Public `/student/` loads and `/api/config` through Vercel returns 200.
- Chapter catalogue and original-text links work without AI calls.
- After deliberately starting paid practice: summary, reviewed quiz, grading, feedback, one retry where needed, and final saved summary work.
- Restart the Render service and refresh the browser (the per-run workbook token changes). On Free, old sessions are lost; start fresh. A restart must not replay paid calls automatically.
- Test Home during generation and verify cancellation and late-result fencing.

The code and offline tests do not prove a container build or hosted live run succeeded. Inspect the first Render build/start logs and the read-only health/API responses. Keep API keys, proxy secrets, raw model logs and learner records out of screenshots.

## Operational limits

Run a single instance; this SQLite design is not a multi-instance deployment. For permanent records later, explicitly approve paid compute/storage and mount a disk at `/var/data`, then set `STUDY_BUDDY_DATABASE=/var/data/workbook-live.sqlite`. Existing Vercel proxy request timeouts still apply. The public demo's shared-profile access model is unchanged; real student accounts require separate work.

Render documentation: https://render.com/docs/web-services and https://render.com/docs/disks
