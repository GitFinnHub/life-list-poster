# Deploying the website

This has been built and tested locally (including a full Docker build and
run) — these are the remaining steps that need your own accounts/credentials,
which nobody else can do for you.

## 1. Push this project to GitHub

If you don't already have a GitHub account, make one at github.com (free).

1. Go to github.com → **New repository**. Name it whatever you like (e.g.
   `life-list-poster`). Leave it **private** unless you want it public.
   Don't initialize with a README (this project already has one).
2. Back in this project folder, connect and push:
   ```
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git branch -M main
   git push -u origin main
   ```

## 2. Create a Render account and deploy

1. Go to [render.com](https://render.com) and sign up (signing up with your
   GitHub account is the fastest — it also handles connecting your repos).
2. Dashboard → **New** → **Blueprint**.
3. Connect the GitHub repo you just pushed. Render will find `render.yaml`
   in this project automatically and read the service configuration from it.
4. When prompted for `SITE_PASSWORD`, enter: **LifeListForFinn**
   (`SESSION_SECRET` is generated for you automatically — no action needed.)
5. Confirm the plan Render selected is a **paid tier with a persistent
   disk** (the "Starter" plan or similar — free tiers don't support
   persistent disks, and this app needs one so photos/posters survive
   redeploys). Check Render's current pricing page for the exact cost.
6. Click **Deploy**. The first build takes a while (~5-10 minutes) — it's
   downloading and installing everything, including a ~180MB image-processing
   model, from scratch.
7. Once live, Render gives you a URL like `https://life-list-poster.onrender.com`.
   That's the link to share with friends/family.

**Note on cold starts:** the very first request after a deploy or restart
takes 15-25 seconds (the image-processing libraries need to warm up) before
the site responds. After that it's normal speed. This is expected, not a bug.

## 3. Cancelling / pausing later

Render is billed month-to-month with no contract — you can delete or
suspend the service anytime from its dashboard page, no cancellation
process beyond that.

## 4. After deploying

- Visit `/admin/coolness` on your live site (same shared password) to set
  baseline "coolness" scores for species you care about.
- Every `git push` to `main` auto-redeploys the live site with your changes
  (Render watches the connected GitHub repo).
