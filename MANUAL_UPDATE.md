# Manual update — when automatic fetching fails

Every bank tab on the Central Banks page has a **Manual update** box at the bottom. Use it when:
- the tab says **LOW confidence / Summary only** (the bank's site blocked automated access), or
- a bank has no working feed (RBNZ, PBOC, Banxico right now), or
- a statement just came out and you don't want to wait for the next scheduled run.

## One-time setup (required — the box is switched off until you do this)
1. Make up a long random string, 16+ characters (a password manager is ideal).
2. Railway → your **backend** (API) service → Variables → add `ADMIN_TOKEN` = that string. Save; let it redeploy.
   (Only the backend service needs it. Anyone holding this token can add statements to your dashboard — keep it private.)
3. `requirements.txt` now includes `pypdf` (for PDF upload). Railway installs it automatically on the redeploy.

## Using it
1. Open the bank's tab → scroll to **Manual update**.
2. Enter your admin token (kept in the page's memory only — you re-enter it after a refresh).
3. Choose **which statement**: pick an existing one to add its full text, or **Add as a NEW statement** (then give its date; title and link are optional).
4. Paste the statement text **or** choose its PDF — not both. PDFs must contain selectable text (a scanned image has none; paste the text from the bank's web page instead).
5. **Save and analyse.** You'll see how the text was read (tone + detected action) and any warnings, and the tab re-runs the comparison.

## Good to know
- Pasted text is labelled **"Pasted by you"** and never passed off as fetched. It counts toward the confidence rating like real text.
- A statement you add for the same meeting as a fetched one replaces it in the comparison.
- **Remove** buttons appear on anything you added. Fetched statements can never be deleted from the dashboard.
- Limits: 300,000 characters of text, 5 MB per PDF.
- Paste the *policy statement itself* — not a news article, speech or press-conference Q&A. If no hawkish/dovish wording is found, you'll get a warning.
