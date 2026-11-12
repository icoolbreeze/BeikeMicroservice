# Beike Archive Vision Deployment Guide

## Isolated Release Path
- Working Directory: `/opt/crm-connector/vision-current`
- Rental DB: `/var/lib/crm-connector/rental_archive.sqlite3`
- Sale DB: `/var/lib/crm-connector/sale_archive.sqlite3`
- Vision DB: `/var/lib/crm-connector/archive_vision.sqlite3`
- Environment File: `/etc/crm-connector/archive-vision.env`

## Commands
- **Manual Backfill (One-off batch):**
  `python3 -m app.application.archive_vision --rental-db /var/lib/crm-connector/rental_archive.sqlite3 --sale-db /var/lib/crm-connector/sale_archive.sqlite3 --vision-db /var/lib/crm-connector/archive_vision.sqlite3 --backfill --batch-size 500`
- **Watch Mode:**
  `python3 -m app.application.archive_vision --rental-db /var/lib/crm-connector/rental_archive.sqlite3 --sale-db /var/lib/crm-connector/sale_archive.sqlite3 --vision-db /var/lib/crm-connector/archive_vision.sqlite3 --watch --batch-size 20 --poll-interval 60`
- **Service Management (systemd):**
  `sudo cp deploy/archive-vision.service /etc/systemd/system/`
  `sudo systemctl daemon-reload`
  `sudo systemctl enable --now archive-vision`
  `sudo systemctl stop archive-vision`
  `sudo journalctl -u archive-vision -f`
