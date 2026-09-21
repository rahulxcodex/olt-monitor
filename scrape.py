#!/usr/bin/env python3
"""
Scrapes the OLT portal and upserts the result into a private Supabase table.

That's all it does. No diffing, no email — the Apps Script project reads
that row, compares it against what it saw last time, and mails you. A local
copy is also written to data/latest.json for debugging, but data/ is
gitignored: the real payload (grades, attendance) never touches git, which
is what lets this repo be public. Only a non-sensitive status.json heartbeat
(timestamp + ok/error) gets committed, so scheduled Actions runs keep
working and GitHub doesn't disable the schedule after 60 days of no commits.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

IST = timezone(timedelta(hours=5, minutes=30))


def env(name: str, default: str = "") -> str:
    """os.getenv treats an empty value as set, which breaks us.

    GitHub Actions expands an undefined `${{ vars.X }}` to an empty string, so
    the variable arrives defined-but-blank and clobbers the default. Treat
    blank as absent.
    """
    return (os.environ.get(name) or "").strip() or default


OUT = Path(env("OUT_PATH", "data/latest.json"))

# Public-repo-safe heartbeat: no grades, no attendance, nothing personal.
# Only its presence/timestamp matters — this is what keeps GitHub from
# auto-disabling the schedule after 60 days with no commits, without ever
# putting real data in git history.
STATUS = Path(env("STATUS_PATH", "status.json"))

# The real payload never touches git. It's pushed straight to a private
# Supabase table; only whoever holds SUPABASE_SERVICE_KEY can read it back.
SUPABASE_URL = env("SUPABASE_URL")
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_KEY")

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("olt")

BASE_URL = env("BASE_URL", "https://olt.iimsirmaur.ac.in/").rstrip("/") + "/"
LOGIN_URL = urljoin(BASE_URL, "default")
LOGIN_ID = env("LOGIN_ID")
PASSWORD = env("PASSWORD")
TIMEOUT_MS = int(env("NAV_TIMEOUT_MS", "60000"))
HEADLESS = env("HEADLESS", "true").lower() == "true"

PAGES = [
    p.strip() for p in env(
        "PAGES",
        "SubjectAttendence,SubjectMarksNew,TermMarksNew,GradeRange,StudentElectiveSelection",
    ).split(",") if p.strip()
]

# Lines that differ on every single load. Without stripping these, every run
# would look like a change: the portal prints your client IP in the header.
NOISE = [
    re.compile(r"client\s*ip", re.I),
    re.compile(r"public\s*ip", re.I),
    re.compile(r"^welcome\b", re.I),
    re.compile(r"sign\s*off", re.I),
    re.compile(r"processing\s*\.*$", re.I),
    re.compile(r"copyright|©", re.I),
    re.compile(r"OTP Verification|Please enter OTP generated", re.I),
    re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?$", re.I),
]


def clean(text: str) -> str:
    out = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if line and not any(p.search(line) for p in NOISE):
            out.append(line)
    return "\n".join(out)


def dump(page, tag: str) -> None:
    """Write a screenshot and the DOM so a failed run can be diagnosed.

    Playwright's fill() sets the DOM property rather than the attribute, so the
    password does not appear in the saved HTML.
    """
    d = Path("debug")
    d.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(d / f"{tag}.png"), full_page=True)
        (d / f"{tag}.html").write_text(page.content(), encoding="utf-8")
        (d / f"{tag}.txt").write_text(
            f"url: {page.url}\n\n{page.inner_text('body')}", encoding="utf-8")
        log.info("wrote debug/%s.{png,html,txt}", tag)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write debug files: %s", exc)


def login(page) -> None:
    # Actions logs are public on a public repo — never print LOGIN_ID itself,
    # only enough to confirm the secret made it into the job at all.
    log.info("logging in (id %d chars, password %d chars)", len(LOGIN_ID), len(PASSWORD))
    page.goto(LOGIN_URL, timeout=TIMEOUT_MS, wait_until="networkidle")
    log.info("login page loaded: %s", page.url)

    if page.locator("a:has-text('Sign off')").is_visible() or "/StudentProfile" in page.url:
        log.info("already logged in")
        return

    pwd = page.locator("#ctl00_Login1_LoginView1_Password, input[name*='Password'], input[type=password]").first
    pwd.wait_for(state="visible", timeout=TIMEOUT_MS)

    user_input = page.locator("#ctl00_Login1_LoginView1_UserName, input[name*='UserName'], input[type=text]:visible").first
    user_input.wait_for(state="visible", timeout=TIMEOUT_MS)

    user_input.fill(LOGIN_ID)
    pwd.fill(PASSWORD)

    # The portal fills a hidden field with a WebRTC-derived IP. Containers
    # often produce no ICE candidate, so seed it rather than post it blank.
    try:
        page.evaluate(
            """() => { const el = document.getElementById('ctl00_Login1_TextBoxIP');
                       if (el && !el.value) el.value = '127.0.0.1'; }"""
        )
    except Exception:  # noqa: BLE001
        pass

    clicked = False
    for sel in ("#ctl00_Login1_LoginView1_ButtonLogin",
                "a[href*='ButtonLogin']",
                "a:has-text('Login')",
                "input[type=submit][value*='Login' i]",
                "button:has-text('Login')"):
        el = page.locator(sel).first
        if el.count() and el.is_visible():
            log.info("clicking login via %s", sel)
            el.click()
            clicked = True
            break
    if not clicked:
        log.warning("no login button matched; pressing Enter instead")
        pwd.press("Enter")

    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    page.wait_for_timeout(2500)
    log.info("after login attempt, url is %s", page.url)

    # #modalOTP sits in the markup of every page but stays hidden. Only act on
    # it if it's actually displayed.
    otp = page.locator("#ctl00_Login1_TextBoxOTP")
    if otp.count() and otp.is_visible():
        secret = env("TOTP_SECRET").replace(" ", "")
        if not secret:
            raise RuntimeError("Portal asked for an OTP but TOTP_SECRET is unset")
        import pyotp
        otp.fill(pyotp.TOTP(secret).now())
        page.locator("#ctl00_Login1_ButtonClose").click()
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

    if not (page.locator("a:has-text('Sign off')").is_visible() or "/StudentProfile" in page.url):
        dump(page, "login-failed")
        # Surface whatever the portal actually said, rather than guessing.
        said = ""
        for frag in ("invalid", "incorrect", "not registered", "locked",
                     "blocked", "expired", "try again", "captcha", "error"):
            for line in page.inner_text("body").splitlines():
                if frag in line.lower() and line.strip():
                    said = line.strip()
                    break
            if said:
                break
        raise RuntimeError(
            "Login failed — still on the login page after submitting."
            + (f" Portal says: {said!r}" if said else
               " The page showed no error message; see debug/login-failed.png.")
        )
    log.info("logged in")


TERMS = [t.strip() for t in env("TERMS", "Term-IV,Term-V,Term-VI").split(",") if t.strip()]

# Option values look like "Term-I" ... "Term-IV".
TERM_RE = re.compile(r"^\s*Term[-\s]", re.I)


def get_term_options(page) -> list[dict[str, str]]:
    """Return all valid term options from any term dropdown on the page."""
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        try:
            opts = sel.locator("option").evaluate_all(
                "els => els.map(e => ({v: e.value, t: (e.textContent||'').trim()}))")
        except Exception:  # noqa: BLE001
            continue

        real = [o for o in opts
                if (o["v"] or o["t"]) and "select" not in o["t"].lower()]
        if len(real) >= 2 and all(TERM_RE.match(o["t"]) or TERM_RE.match(o["v"]) for o in real):
            return real
    return []


def select_term(page, target_term: str) -> str | None:
    """Switch the term dropdown to target_term, handling ASP.NET postbacks and UpdatePanels."""
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        try:
            opts = sel.locator("option").evaluate_all(
                "els => els.map(e => ({v: e.value, t: (e.textContent||'').trim()}))")
        except Exception:  # noqa: BLE001
            continue

        real = [o for o in opts
                if (o["v"] or o["t"]) and "select" not in o["t"].lower()]
        if len(real) < 2 or not all(TERM_RE.match(o["t"]) or TERM_RE.match(o["v"]) for o in real):
            continue

        want = None
        for o in real:
            if target_term.lower() in (o["v"].lower(), o["t"].lower()):
                want = o
                break
        if not want:
            return "_MISSING_"

        shown = want["t"] or want["v"]
        if sel.input_value() == want["v"]:
            log.info("  term dropdown already on %s", shown)
            return shown

        log.info("  switching term dropdown to %s", shown)
        sel.select_option(want["v"])
        page.wait_for_timeout(1000)
        try:
            proc = page.locator("text=/Processing/i")
            if proc.count() and proc.first.is_visible():
                proc.first.wait_for(state="hidden", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        page.wait_for_timeout(1500)
        return shown

    return None


def tables(page) -> str:
    """Read real data tables as 'cell | cell | cell' lines.

    Handles standard GridView tables, nested subject grade distribution tables
    (GradeRange), and GPA/CGPA summary metrics (TermMarksNew).
    """
    return page.evaluate(
        """() => {
             // 1. GradeRange special nested handling
             const gradeRangeParent = document.getElementById('ctl00_Main_GridView1');
             const hasNestedGridView2 = document.querySelector('[id*="GridView2"]');
             if (gradeRangeParent && hasNestedGridView2) {
               const lines = [];
               for (let i = 0; i < gradeRangeParent.rows.length; i++) {
                 const row = gradeRangeParent.rows[i];
                 const childTable = row.querySelector('table');
                 let subjectTitle = '';
                 if (childTable) {
                   const clone = row.cloneNode(true);
                   const inner = clone.querySelector('table');
                   if (inner) inner.remove();
                   subjectTitle = clone.innerText.replace(/\\s+/g, ' ').trim();
                 } else {
                   subjectTitle = row.innerText.replace(/\\s+/g, ' ').trim();
                 }
                 if (subjectTitle && !/grade ranges/i.test(subjectTitle)) {
                   lines.push(`### ${subjectTitle}`);
                 }
                 if (childTable) {
                   for (const cr of childTable.rows) {
                     const cells = Array.from(cr.cells).map(c => c.innerText.replace(/\\s+/g, ' ').trim()).filter(c => c);
                     if (cells.length) lines.push(cells.join(' | '));
                   }
                   lines.push('');
                 }
               }
               return lines.join('\\n').trim();
             }

             // 2. Standard GridView tables
             const all = Array.from(document.querySelectorAll('table'));
             const grids = all.filter(t => /gridview/i.test((t.className || '') + ' ' + (t.id || '')));
             const use = grids.length ? grids : all;

             const lines = [];
             for (const t of use) {
               const rows = Array.from(t.rows).map(r =>
                 Array.from(r.cells).map(c => {
                   const i = c.querySelector('input[type=text]');
                   return ((i ? i.value : c.innerText) || '').replace(/\\s+/g, ' ').trim();
                 }).filter(c => c)
               ).filter(r => r.length > 0);

               // Skip layout tables (< 2 rows or single cell rows)
               if (rows.length < 2 || Math.max(...rows.map(r => r.length), 0) < 2) continue;

               for (const r of rows) {
                 lines.push(r.join(' | '));
               }
               lines.push('');
             }

             // 3. GPA / CGPA extraction (on TermMarksNew)
             const gpaEl = document.getElementById('ctl00_Main_Label3')?.nextElementSibling ||
                           Array.from(document.querySelectorAll('td')).find(td => /^GPA/i.test(td.innerText.trim()));
             const cgpaEl = document.getElementById('ctl00_Main_Label2')?.nextElementSibling ||
                            Array.from(document.querySelectorAll('td')).find(td => /^CGPA/i.test(td.innerText.trim()));

             const gpaText = gpaEl?.innerText?.replace(/\\s+/g, ' ')?.replace(/^GPA\\s*/i, '')?.trim();
             const cgpaText = cgpaEl?.innerText?.replace(/\\s+/g, ' ')?.replace(/^CGPA\\s*/i, '')?.trim();

             const extras = [];
             if (gpaText && /\\d/.test(gpaText)) extras.push(`GPA | ${gpaText}`);
             if (cgpaText && /\\d/.test(cgpaText)) extras.push(`CGPA | ${cgpaText}`);
             if (extras.length > 0) {
               lines.push('');
               lines.push(...extras);
             }

             return lines.join('\\n').trim();
           }"""
    )


def push_supabase(record: dict) -> bool:
    """Upsert the scrape result into a single-row Supabase table.

    This is the only place the real (personal) data goes. The repo and its
    Actions logs never see it — only the request body does, over HTTPS, with
    the service-role key that bypasses RLS. Returns True on success so the
    caller can decide whether it's safe to skip the local/backup copy.
    """
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        log.info("SUPABASE_URL/SUPABASE_SERVICE_KEY not set; skipping remote push")
        return False

    endpoint = SUPABASE_URL.rstrip("/") + "/rest/v1/olt_snapshot?on_conflict=id"
    body = json.dumps({"id": 1, **record}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            # merge-duplicates -> upsert on the id=1 row instead of erroring;
            # return=minimal -> don't bother echoing the row back to us.
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("pushed snapshot to Supabase (status %s)", resp.status)
            return True
    except urllib.error.HTTPError as exc:
        log.error("Supabase rejected the push: %s %s", exc.code, exc.read()[:500])
    except Exception as exc:  # noqa: BLE001
        log.error("failed to push snapshot to Supabase: %s", exc)
    return False


def main() -> int:
    if not (LOGIN_ID and PASSWORD):
        log.error("LOGIN_ID and PASSWORD are not set")
        return 1
    if not PAGES:
        log.error("PAGES is empty — nothing to scrape. Unset the PAGES "
                  "repository variable, or give it a real comma-separated list.")
        return 1
    log.info("will scrape %d page(s): %s", len(PAGES), ", ".join(PAGES))

    pages: dict[str, str] = {}
    error = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36"))
        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        try:
            login(page)
            for path in PAGES:
                try:
                    page.goto(urljoin(BASE_URL, path), timeout=TIMEOUT_MS,
                              wait_until="networkidle")
                    page.wait_for_timeout(1200)
                    log.info("%s:", path)
                    
                    opts = get_term_options(page)
                    if not opts:
                        body = clean(tables(page))
                        if body:
                            pages[path] = body
                            log.info("%s -> %d chars", path, len(body))
                        else:
                            log.info("%s has no data tables published yet", path)
                    else:
                        log.info("  found %d term options: %s", len(opts), [o["t"] or o["v"] for o in opts])
                        terms_to_scrape = []
                        for t in TERMS:
                            for o in opts:
                                val = o["t"] or o["v"]
                                if t.lower() in (o["v"].lower(), o["t"].lower()) and val not in terms_to_scrape:
                                    terms_to_scrape.append(val)

                        # Fallback to latest available term if none of the configured TERMS match
                        # (e.g. TermMarksNew where only earlier terms are published so far)
                        if not terms_to_scrape:
                            fallback = opts[-1]["t"] or opts[-1]["v"]
                            log.info("  no matching terms from %r; falling back to latest: %s", TERMS, fallback)
                            terms_to_scrape = [fallback]

                        for target_term in terms_to_scrape:
                            term = select_term(page, target_term)
                            if term and term != "_MISSING_":
                                body = clean(tables(page))
                                if body:
                                    key = f"{path}_{term}"
                                    pages[key] = f"Term | {term}\n{body}"
                                    log.info("%s -> %d chars (%s)", key, len(pages[key]), term)
                                else:
                                    log.info("%s_%s has no data tables published yet", path, term)
                except Exception as exc:  # noqa: BLE001
                    log.error("failed on %s: %s", path, exc)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log.error("run failed: %s", exc)
        finally:
            ctx.close()
            browser.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)

    # On failure, keep the last good page contents so Apps Script doesn't read
    # an empty file and report everything as removed. Local file first, then
    # (if the local copy was itself empty, e.g. first run in a fresh CI
    # container) fall back to whatever Supabase last had.
    if not pages and OUT.exists():
        try:
            pages = json.loads(OUT.read_text(encoding="utf-8")).get("pages", {})
            log.info("kept %d page(s) from the previous local run", len(pages))
        except Exception:  # noqa: BLE001
            pass
    if not pages and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            req = urllib.request.Request(
                SUPABASE_URL.rstrip("/") + "/rest/v1/olt_snapshot?id=eq.1&select=pages",
                headers={"apikey": SUPABASE_SERVICE_KEY,
                         "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                rows = json.loads(resp.read())
            if rows:
                pages = rows[0].get("pages") or {}
                log.info("kept %d page(s) from the previous Supabase row", len(pages))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fall back to Supabase's previous row: %s", exc)

    record = {
        "scraped_at": datetime.now(IST).isoformat(timespec="seconds"),
        "ok": error is None and bool(pages),
        "error": error,
        "pages": pages,
    }

    # Local copy is a debug convenience (data/ is gitignored — it never gets
    # committed, public repo or not).
    OUT.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s (%d pages)", OUT, len(pages))

    pushed = push_supabase(record)

    # Heartbeat is the only thing that goes into git. No grades, no
    # attendance — just enough for Apps Script (and GitHub's 60-day
    # scheduled-workflow inactivity check) to see the scraper is alive.
    STATUS.write_text(json.dumps({
        "scraped_at": record["scraped_at"],
        "ok": record["ok"],
        "pushed_to_supabase": pushed,
    }, indent=2), encoding="utf-8")

    return 0 if (error is None and pages) else 1


if __name__ == "__main__":
    sys.exit(main())
