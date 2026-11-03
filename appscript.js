/**
 * Google Apps Script to fetch scraped data from Supabase, compare it to the last seen state,
 * and email you if there are changes. It also pings Supabase to prevent the project from sleeping.
 */

// Recipient resolution: checks Script Property 'TO_EMAIL', then active user and fallback
function getRecipients() {
  const customTo = PropertiesService.getScriptProperties().getProperty('TO_EMAIL');
  if (customTo) return customTo;

  const activeUser = (Session.getActiveUser().getEmail() || '').trim();
  const emails = [];
  if (activeUser) emails.push(activeUser);
  if (!emails.includes("rahulr24g@gmail.com")) emails.push("rahulr24g@gmail.com");
  return emails.join(",");
}

function main() {
  const url = PropertiesService.getScriptProperties().getProperty('SUPABASE_URL');
  const key = PropertiesService.getScriptProperties().getProperty('SUPABASE_SERVICE_KEY');
  
  if (!url || !key) {
    console.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in Script Properties.");
    return;
  }

  // Fetch id=1 (latest scrape) and id=2 (last acknowledged)
  const reqUrl = url.replace(/\/$/, '') + '/rest/v1/olt_snapshot?id=in.(1,2)&select=id,scraped_at,ok,error,pages';
  const res = UrlFetchApp.fetch(reqUrl, {
    headers: { apikey: key, Authorization: 'Bearer ' + key },
    muteHttpExceptions: true,
  });

  if (res.getResponseCode() !== 200) {
    console.error("Failed to fetch from Supabase:", res.getContentText());
    return;
  }

  const rows = JSON.parse(res.getContentText());
  const latest = rows.find(r => r.id === 1);
  const last = rows.find(r => r.id === 2);

  if (!latest) {
    console.log("No snapshot found for id=1.");
    return;
  }
  
  if (!latest.ok) {
    console.warn("Latest scrape reported an error:", latest.error);
    return;
  }

  const latestPages = latest.pages || {};
  const lastPages = last ? (last.pages || {}) : {};

  // Find changes
  const changes = [];
  
  for (const pageName in latestPages) {
    const latestContent = latestPages[pageName];
    const lastContent = lastPages[pageName];
    
    if (latestContent !== lastContent) {
      changes.push({
        page: pageName,
        old: lastContent,
        new: latestContent
      });
    }
  }

  if (changes.length > 0) {
    console.log(`Found changes in ${changes.length} pages.`);
    
    // Construct email body as HTML
    let htmlBody = `<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; max-width: 800px; margin: 0 auto; padding: 15px;">`;
    htmlBody += `<h2 style="color: #0f172a; margin-bottom: 4px;">OLT Portal Update</h2>`;
    htmlBody += `<p style="color: #64748b; font-size: 14px; margin-top: 0; margin-bottom: 24px;">Changes detected in your academic records at <strong>${latest.scraped_at}</strong></p>`;
    
    for (const diff of changes) {
      htmlBody += formatPageHtml(diff);
    }
    
    htmlBody += `<div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #e2e8f0; font-size: 13px;">`;
    htmlBody += `<a href="https://olt.iimsirmaur.ac.in/" style="color: #2563eb; text-decoration: none; font-weight: 600;">&rarr; Open OLT Portal</a>`;
    htmlBody += `</div></div>`;

    // Send the email
    MailApp.sendEmail({
      to: getRecipients(),
      subject: `OLT Portal Update: ${changes.map(c => c.page).join(', ')}`,
      htmlBody: htmlBody
    });
    console.log("Notification email sent.");

    // Update id=2 with the latest pages so we don't email again for this change
    updateLastSeen(url, key, latest);
  } else {
    console.log("No changes detected.");
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatPageHtml(diff) {
  const newText = (diff.new || '').trim();
  const oldText = (diff.old || '').trim();
  const oldLinesSet = new Set(oldText.split(/\r?\n|\\n/).map(l => l.trim()).filter(Boolean));
  
  const rawLines = newText.split(/\r?\n|\\n/);
  let termBadge = '';
  const filteredLines = [];
  
  for (const line of rawLines) {
    const trimmed = line.trim();
    if (!trimmed) {
      filteredLines.push('');
      continue;
    }
    const termMatch = trimmed.match(/^Term\s*\|\s*(.+)$/i);
    if (termMatch && !termBadge) {
      termBadge = termMatch[1].trim();
      continue;
    }
    filteredLines.push(trimmed);
  }

  let html = `<div style="margin-bottom: 25px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">`;
  html += `<div style="border-bottom: 2px solid #2563eb; padding-bottom: 6px; margin-bottom: 12px;">`;
  html += `<span style="color: #1e293b; font-size: 15px; font-weight: 700;">PAGE: ${escapeHtml(diff.page)}</span>`;
  if (termBadge) {
    html += ` <span style="background-color: #dbeafe; color: #1e40af; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px; margin-left: 8px;">Term: ${escapeHtml(termBadge)}</span>`;
  }
  html += `</div>`;

  let inTable = false;
  let summaryMetrics = [];

  for (let i = 0; i < filteredLines.length; i++) {
    const line = filteredLines[i];

    if (!line) {
      if (inTable) {
        html += `</tbody></table>`;
        inTable = false;
      }
      continue;
    }

    if (line.startsWith('###')) {
      if (inTable) {
        html += `</tbody></table>`;
        inTable = false;
      }
      const headingText = line.replace(/^###\s*/, '').trim();
      html += `<h4 style="margin: 14px 0 6px 0; color: #334155; font-size: 14px; font-weight: 600;">${escapeHtml(headingText)}</h4>`;
      continue;
    }

    const gpaMatch = line.match(/^(C?GPA)\s*\|\s*(.+)$/i);
    if (gpaMatch) {
      if (inTable) {
        html += `</tbody></table>`;
        inTable = false;
      }
      summaryMetrics.push({ label: gpaMatch[1].toUpperCase(), value: gpaMatch[2].trim() });
      continue;
    }

    const cells = line.split('|').map(c => c.trim());
    if (cells.length === 0) continue;

    if (!inTable) {
      html += `<table style="width: 100%; border-collapse: collapse; margin-bottom: 14px; font-size: 13px; text-align: left; border: 1px solid #cbd5e1;">`;
      html += `<thead><tr>`;
      for (const cell of cells) {
        html += `<th style="background-color: #f1f5f9; color: #334155; font-weight: 600; padding: 8px 10px; border: 1px solid #cbd5e1;">${escapeHtml(cell)}</th>`;
      }
      html += `</tr></thead><tbody>`;
      inTable = true;
      continue;
    }

    const isChanged = oldText && !oldLinesSet.has(line);
    const rowBg = isChanged ? '#dcfce7' : '#ffffff';
    const rowStyle = `background-color: ${rowBg};`;
    
    html += `<tr style="${rowStyle}">`;
    for (const cell of cells) {
      const badge = isChanged ? ' <span style="color: #16a34a; font-size: 10px; font-weight: bold;">(NEW)</span>' : '';
      html += `<td style="padding: 7px 10px; border: 1px solid #e2e8f0; color: #1e293b;">${escapeHtml(cell)}${badge}</td>`;
    }
    html += `</tr>`;
  }

  if (inTable) {
    html += `</tbody></table>`;
  }

  if (summaryMetrics.length > 0) {
    html += `<div style="margin: 10px 0; display: inline-flex; gap: 10px;">`;
    for (const m of summaryMetrics) {
      html += `<div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px 14px; text-align: center;">`;
      html += `<div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">${escapeHtml(m.label)}</div>`;
      html += `<div style="font-size: 16px; color: #0f172a; font-weight: 700;">${escapeHtml(m.value)}</div>`;
      html += `</div>`;
    }
    html += `</div>`;
  }

  html += `</div>`;
  return html;
}

function updateLastSeen(url, key, latestData) {
  const reqUrl = url.replace(/\/$/, '') + '/rest/v1/olt_snapshot?on_conflict=id';
  
  const payload = {
    id: 2,
    scraped_at: latestData.scraped_at,
    ok: latestData.ok,
    error: latestData.error,
    pages: latestData.pages
  };

  const res = UrlFetchApp.fetch(reqUrl, {
    method: 'post',
    headers: {
      apikey: key,
      Authorization: 'Bearer ' + key,
      'Content-Type': 'application/json',
      'Prefer': 'resolution=merge-duplicates,return=minimal' // Upsert
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  
  if (res.getResponseCode() >= 300) {
    console.error("Failed to update id=2:", res.getResponseCode(), res.getContentText());
  } else {
    console.log("Successfully updated last seen state (id=2).");
  }
}

/**
 * Optional: Run this on a 5-minute timer in Apps Script to force GitHub Actions
 * to run on time (bypasses GitHub's scheduled cron queue delays).
 * Requires GITHUB_PAT (personal access token with repo/actions scope) in Script Properties.
 */
function triggerGithubScrape() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_PAT') ||
                PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    console.warn("Missing GITHUB_PAT/GITHUB_TOKEN in Script Properties; cannot dispatch workflow.");
    return;
  }

  const repo = PropertiesService.getScriptProperties().getProperty('GITHUB_REPO') || "rahulxcodex/olt-monitor";
  const url = `https://api.github.com/repos/${repo}/actions/workflows/scrape.yml/dispatches`;

  try {
    const res = UrlFetchApp.fetch(url, {
      method: 'post',
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/vnd.github.v3+json',
        'User-Agent': 'Google-Apps-Script'
      },
      payload: JSON.stringify({ ref: 'main' }),
      muteHttpExceptions: true
    });
    const status = res.getResponseCode();
    if (status >= 300) {
      console.error(`Failed to dispatch GitHub workflow (status: ${status}):`, res.getContentText());
    } else {
      console.log(`Dispatched GitHub workflow successfully (status: ${status})`);
    }
  } catch (err) {
    console.error("Failed to trigger GitHub workflow:", err);
  }
}

/**
 * Run this function on a separate schedule if you want to explicitly keep Supabase awake.
 * Running main() every 5 minutes also keeps Supabase active.
 */
function pingSupabase() {
  const url = PropertiesService.getScriptProperties().getProperty('SUPABASE_URL');
  const key = PropertiesService.getScriptProperties().getProperty('SUPABASE_SERVICE_KEY');
  
  if (!url || !key) return;

  const reqUrl = url.replace(/\/$/, '') + '/rest/v1/olt_snapshot?id=eq.1&select=id';
  UrlFetchApp.fetch(reqUrl, {
    headers: { apikey: key, Authorization: 'Bearer ' + key },
    muteHttpExceptions: true,
  });
  console.log("Pinged Supabase to keep it awake.");
}

/**
 * Web App entry point: allows scrape.py to POST changes directly for zero-delay email alerts.
 */
function doPost(e) {
  try {
    main();
    return ContentService.createTextOutput(JSON.stringify({ status: "ok" }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
