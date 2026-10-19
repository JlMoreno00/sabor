# Sabor Data Backup and Restore

This runbook backs up or restores the Compose `sabor_data` volume for your deployment
checkout. Docker volume names depend on the Compose project name. Backups contain user data and provider evidence: keep them in
protected storage outside the repository.

## Tested Validation Commands

These read-only checks validate the Compose model and identify the deployed
volume. They do not stop services, create archives, or alter volume data.

```bash
cd <deploy-dir>
docker compose config -q
docker compose config --volumes
docker inspect "$(docker compose ps -q sabor)" --format '{{json .Mounts}}'
```

Find the mount whose destination is `/data` and record its volume name as
`<verified-volume-name>`. Do not assume a project prefix or guess a volume name.
Replace all angle-bracket placeholders before running these commands.

Never run `docker compose down -v`; it may remove the persistent volume.
Back up before schema-affecting nutrition changes. Do not print resolved
environment values. Mealie needs its own independent backup.

## Operator-Only Backup

The following procedure stops Sabor writes and creates an archive. It is
mutating operational work and MUST NOT be used as validation.

1. Choose a protected backup directory outside the repository and ensure it
   already exists with restrictive permissions.

   ```bash
   cd <deploy-dir>
   BACKUP_DIR=<backup-dir>
   VOLUME_NAME=<verified-volume-name>
   ARCHIVE_NAME="sabor-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
   test -d "$BACKUP_DIR"
   test ! -e "$BACKUP_DIR/$ARCHIVE_NAME"
   ```

2. Quiesce writes. This causes service downtime.

   ```bash
   docker compose stop sabor
   docker compose ps
   ```

3. Archive the stopped volume without mounting it writable.

   ```bash
   docker run --rm \
     --volume "$VOLUME_NAME:/source:ro" \
     --volume "$BACKUP_DIR:/backup" \
     --env ARCHIVE_NAME="$ARCHIVE_NAME" \
     alpine:3.20 \
     sh -eu -c 'cd /source && tar -czf "/backup/$ARCHIVE_NAME" .'
   ```

4. Validate the archive before restarting Sabor.

   ```bash
   test -s "$BACKUP_DIR/$ARCHIVE_NAME"
   gzip -t "$BACKUP_DIR/$ARCHIVE_NAME"
   tar -tzf "$BACKUP_DIR/$ARCHIVE_NAME" >/dev/null
   ```

5. Restart and verify only transport health. Do not invoke mutation tools as a
   smoke test.

   ```bash
   docker compose up -d sabor
   docker compose ps
   docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q sabor)"
   ```

If archive creation or validation fails, leave the existing volume unchanged,
restart Sabor, preserve the failure output without secrets, and investigate
before retrying.

## Operator-Only Restore

Restore replaces the complete current volume. It does not roll back Mealie or
any external provider. Schedule downtime and obtain explicit approval before
continuing.

1. Select and validate an archive. Never restore an untrusted archive.

   ```bash
   cd <deploy-dir>
   BACKUP_DIR=<backup-dir>
   VOLUME_NAME=<verified-volume-name>
   RESTORE_ARCHIVE=sabor-data-YYYYmmddTHHMMSSZ.tar.gz
   test -f "$BACKUP_DIR/$RESTORE_ARCHIVE"
   gzip -t "$BACKUP_DIR/$RESTORE_ARCHIVE"
   tar -tzf "$BACKUP_DIR/$RESTORE_ARCHIVE" >/dev/null
   ```

2. Stop Sabor, then create and validate a rollback archive of the current volume
   before deleting any current data.

   ```bash
   set -eu
   ROLLBACK_ARCHIVE="sabor-data-pre-restore-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
   docker compose stop sabor
   docker run --rm \
     --volume "$VOLUME_NAME:/source:ro" \
     --volume "$BACKUP_DIR:/backup" \
     --env ARCHIVE_NAME="$ROLLBACK_ARCHIVE" \
      alpine:3.20 \
      sh -eu -c 'cd /source && tar -czf "/backup/$ARCHIVE_NAME" .'
   test -s "$BACKUP_DIR/$ROLLBACK_ARCHIVE"
   gzip -t "$BACKUP_DIR/$ROLLBACK_ARCHIVE"
   tar -tzf "$BACKUP_DIR/$ROLLBACK_ARCHIVE" >/dev/null
   ```

3. Replace the stopped volume contents. THIS IS THE DESTRUCTIVE BOUNDARY. Check
   every variable immediately before running it.

   ```bash
   docker run --rm \
     --volume "$VOLUME_NAME:/target" \
     --volume "$BACKUP_DIR:/backup:ro" \
     --env RESTORE_ARCHIVE="$RESTORE_ARCHIVE" \
     alpine:3.20 \
     sh -eu -c 'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf "/backup/$RESTORE_ARCHIVE" -C /target'
   ```

4. While Sabor remains stopped, check the restored SQLite sidecar when present.
   The connection uses SQLite read-only mode.

   ```bash
   docker compose run --rm --no-deps --entrypoint python sabor -c '
   from pathlib import Path
   import sqlite3
   path = Path("/data/nutrition.db")
   if not path.exists():
       print("nutrition.db absent; nutrition may never have been enabled")
       raise SystemExit(0)
   connection = sqlite3.connect("file:/data/nutrition.db?mode=ro", uri=True)
   integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
   schema = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
   print(f"integrity={integrity} schema_version={schema}")
   if integrity != "ok" or schema is None:
       raise SystemExit(1)
   '
   ```

5. Restart Sabor only after archive and SQLite checks pass.

   ```bash
   docker compose up -d sabor
   docker compose ps
   docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q sabor)"
   ```

## Restore Rollback Boundary

If restored data fails validation, keep Sabor stopped and repeat the restore
procedure using `$ROLLBACK_ARCHIVE`. Do not attempt manual SQLite repair first;
it can destroy evidence needed for diagnosis. A volume rollback does not revert
nutrition already published to Mealie, Bring changes, provider-side Gemini
uploads, or any other external mutation. Those systems require their own
reviewed recovery procedures.
