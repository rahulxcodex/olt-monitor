# olt-monitor

Scrapes the OLT portal on a schedule and upserts the result into a private
Supabase table. An Apps Script project reads that row, diffs it against what
it saw last time, and emails you.

## Why this repo is public

GitHub Actions minutes are unlimited on public repos and capped (2,000
min/month on the free personal tier) on private ones. This repo is public so
scraping is never rate-limited by that cap. Privacy is handled a different
way: **the real data (grades, attendance) never touches git.** It's pushed
straight to Supabase over HTTPS and only readable with a service-role key
that lives in secrets, never in the repo. The only thing this repo ever
commits is `status.json` — a timestamp and an ok/fail flag, nothing personal.

## One-time setup

1. **Create a Supabase project** (free tier is plenty — this is a few KB of
   JSON updated hourly). [supabase.com](https://supabase.com).
2. **Run `supabase.sql`** in the project's SQL editor. It creates one table
   with row-level security on and *no* policies — meaning the anon/public
   key can't read or write it at all, only the `service_role` key can. It allows storing two rows (`id=1` and `id=2`) for diffing.
3. **Grab two values** from Project Settings → API: the Project URL, and the
   `service_role` secret key (not the `anon` key — that one's meant to be
   public-ish, this one isn't).
4. **Add GitHub Actions secrets** (Settings → Secrets and variables →
   Actions → New repository secret), alongside your existing `LOGIN_ID` /
   `PASSWORD` / `TOTP_SECRET`:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
5. **Update the Apps Script project** to diff the results and send you an email.
   - Go to [script.google.com](https://script.google.com/) and create a new project.
   - Copy the contents of `appscript.js` from this repository into `Code.gs`.
   - Go to Project Settings → Script Properties and add:
     - `SUPABASE_URL`: (your Supabase URL)
     - `SUPABASE_SERVICE_KEY`: (your service_role key)
   - Go to Triggers (the clock icon) and add a trigger for `main` to run on a Time-driven schedule (e.g., every hour).
   - Optionally add a trigger for `pingSupabase` to run once a week, though the hourly `main` execution already keeps Supabase active.
6. **Push this repo as a brand-new public repo — do not flip your existing
   private repo to public.** See the warning below.
7. **Trigger the workflow once by hand** (Actions tab → OLT Scrape → Run
   workflow) and confirm a row shows up in Supabase's Table Editor before
   trusting the schedule.

## ⚠️ Don't just flip the old repo's visibility

If your current private repo has been running hourly, every one of those
runs committed `data/latest.json` — your actual grades and attendance — into
git history. Changing that repo from private to public would publish that
entire history immediately, including years of past academic data, not just
today's snapshot. `git rm` or deleting the file afterwards doesn't help;
it'd still be sitting in old commits.

The safe path is what's laid out above: create a **new, empty** public
repo, push this cleaned-up code into it (no history carried over), archive
or keep the old private repo as-is (or delete it once you're confident the
new setup works), and update `LOGIN_ID`/`PASSWORD`/`TOTP_SECRET` (plus the
two new Supabase secrets) on the new repo.

## What changed from the old setup

- `scrape.py` upserts to Supabase (`push_supabase`) instead of relying on a
  git commit to hand data to Apps Script. It still writes a local
  `data/latest.json` for debugging, but `data/` is gitignored — that file
  never gets committed, public repo or not.
- A non-sensitive `status.json` (timestamp + ok/fail only) is the one thing
  the workflow still commits, so the schedule doesn't get disabled by
  GitHub's 60-day-no-commits rule.
- The login-ID is no longer printed to the Actions log (those logs are
  public on a public repo).
- The debug-artifact upload step is commented out, since Actions artifacts
  on a public repo are downloadable by anyone. It only ever captured the
  login page, but "low risk" isn't "no risk" — re-enable it only if you're
  fine with that, or point it at a private destination instead.

## Environment variables

| Variable               | Where it's set        | Purpose                          |
|-------------------------|------------------------|-----------------------------------|
| `LOGIN_ID`, `PASSWORD`  | Actions secret         | Portal login                      |
| `TOTP_SECRET`           | Actions secret         | OTP, if the portal asks for one   |
| `SUPABASE_URL`          | Actions secret         | Supabase project URL              |
| `SUPABASE_SERVICE_KEY`  | Actions secret         | Bypasses RLS to write the row     |
| `PAGES`                 | Actions repo variable  | Comma-separated list of pages to scrape |
| `TERM`                  | Actions repo variable  | Optional: Comma-separated list of terms (e.g., `Term-IV,Term-V`) |
