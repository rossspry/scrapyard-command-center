#!/bin/bash

# SCC Backup Script
BACKUP_DIR="/srv/scc-backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="scc_backup_${DATE}.tar.gz"

echo "Creating backup: $BACKUP_NAME"

# Create backup directory if it doesn't exist
mkdir -p $BACKUP_DIR

# Backup important directories
tar -czf "${BACKUP_DIR}/${BACKUP_NAME}" \
    --exclude='/srv/scc-ui/__pycache__' \
    --exclude='/srv/scc-ui/*.pyc' \
    --exclude='/srv/scc-ui/venv' \
    /srv/scc-ui \
    /srv/frigate/config \
    /etc/systemd/system/scc-ui.service \
    /etc/systemd/system/glitch-voice.service \
    2>/dev/null

if [ $? -eq 0 ]; then
    echo "✅ Backup created: ${BACKUP_DIR}/${BACKUP_NAME}"
    echo "📦 Size: $(du -h ${BACKUP_DIR}/${BACKUP_NAME} | cut -f1)"
    
    # Keep only last 10 backups
    cd $BACKUP_DIR
    ls -t scc_backup_*.tar.gz | tail -n +11 | xargs rm -f 2>/dev/null
    echo "🗑️  Cleaned old backups (keeping last 10)"
else
    echo "❌ Backup failed!"
    exit 1
fi
