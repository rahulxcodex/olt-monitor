/**
 * Google Apps Script to fetch scraped data from Supabase, compare it to the last seen state,
 * and email you if there are changes. It also pings Supabase to prevent the project from sleeping.
 */

// Your email address where you want to receive the notifications
const TO_EMAIL = Session.getActiveUser().getEmail() + ",rahulr24g@gmail.com";

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
    let htmlBody = `<h3>Changes detected in your OLT portal on ${latest.scraped_at}</h3>`;
    for (const diff of changes) {
      htmlBody += `<h4 style="margin-bottom: 5px; color: #333;">PAGE: ${diff.page}</h4>`;
      htmlBody += `<table border="1" cellpadding="5" style="border-collapse: collapse; font-family: sans-serif; text-align: left; margin-bottom: 20px;">`;
      const lines = diff.new.split('\\n');
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        htmlBody += `<tr>`;
        const cells = line.split('|');
        for (const cell of cells) {
          // make the first row bold if it looks like a header, or just keep it normal.
          if (i === 0) {
            htmlBody += `<th style="background-color: #f2f2f2;">${cell.trim()}</th>`;
          } else {
            htmlBody += `<td>${cell.trim()}</td>`;
          }
        }
        htmlBody += `</tr>`;
      }
      htmlBody += `</table>`;
    }
    
    htmlBody += `<p><a href="https://olt.iimsirmaur.ac.in/">Check the OLT portal here</a></p>`;

    // Send the email
    MailApp.sendEmail({
      to: TO_EMAIL,
      subject: "OLT Portal Update - Grades/Attendance Changed",
      htmlBody: htmlBody
    });
    console.log("Notification email sent.");

    // Update id=2 with the latest pages so we don't email again for this change
    updateLastSeen(url, key, latest);
  } else {
    console.log("No changes detected.");
  }
}

function updateLastSeen(url, key, latestData) {
  const reqUrl = url.replace(/\/$/, '') + '/rest/v1/olt_snapshot?id=eq.2';
  
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
      'Prefer': 'resolution=merge-duplicates' // Upsert
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  
  if (res.getResponseCode() >= 300) {
    console.error("Failed to update id=2:", res.getContentText());
  } else {
    console.log("Successfully updated last seen state (id=2).");
  }
}

/**
 * Run this function on a separate schedule if you want to explicitly keep Supabase awake.
 * However, the main() function running hourly is enough to keep Supabase active.
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
