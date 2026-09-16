# Backups

Managed Postgres providers handle this differently — pick based on which host you use for Phase 3:

- **Supabase / Neon / Railway Postgres** — automated daily backups are included on paid tiers; free tiers typically do NOT include point-in-time recovery, so if you're on a free tier, add your own:
  ```bash
  # Run daily via cron or GitHub Actions (same pattern as ingestion scheduling)
  pg_dump "$DATABASE_URL" | gzip > backup_$(date +%Y%m%d).sql.gz
  # Upload backup_*.sql.gz to S3/Backblaze/anywhere with retention rules
  ```
- **AWS RDS** — enable automated backups in the console (Configuration → Backup retention period); also supports point-in-time recovery natively, no extra script needed.

Test restores periodically — an untested backup is not a backup. Minimum viable check: restore the latest dump into a scratch database monthly and confirm row counts match.
