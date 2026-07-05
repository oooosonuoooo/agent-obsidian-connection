# BACKUP_AND_RESTORE.md

# Backup and Restore Guide for AI Second Brain

This document provides instructions for manually creating and restoring backups of the AI Second Brain system. **This setup does NOT create automatic backups** - all backups must be created manually as described here.

## When to Backup

You should create backups before:

- **Major system changes**: Before updating agents, installing new software, or modifying system configuration
- **Release deployments**: Before deploying new versions of the system
- **Risky operations**: Before making significant configuration changes
- **Initial setup**: After completing the initial setup and verification
- **Regular maintenance**: On a schedule (weekly, monthly, or project-based)

## Backup Strategy

### 1. Full Backup

Create a complete snapshot of the AI Second Brain system:

```bash
# Create backup directory structure
cd ~/AI-Second-Brain
mkdir -p backup_$(date +%Y%m%d_%H%M%S)
cp -r AI-Second-Brain-Vault backup_$(date +%Y%m%d_%H%M%S)/
cp -r .agent_mesh backup_$(date +%Y%m%d_%H%M%S}/
cp README_SETUP.md SECURITY.md BACKUP_AND_RESTORE.md backup_$(date +%Y%m%d_%H%M%S)/
```

### 2. Selective Backup

If you only need to backup specific components:

```bash
# Backup only the Obsidian vault
cp -r AI-Second-Brain-Vault /path/to/backup/

# Backup only the database
cp ~/.agent_mesh/agent_mesh.sqlite /path/to/backup/

# Backup only configuration files
cp ~/.agent_mesh/config.json /path/to/backup/
cp AI-Second-Brain-Vault/00_System/Operating_Rules.md /path/to/backup/
```

### 3. Verification

After creating backups, verify they are complete and functional:

```bash
# Test SQLite database integrity
cd ~/AI-Second-Brain
python3 -c "
import sqlite3
import sys
try:
    conn = sqlite3.connect('.agent_mesh/agent_mesh.sqlite')
    conn.execute('SELECT name FROM sqlite_master WHERE type=\"table\" LIMIT 1')
    print('Database: OK')
    conn.close()
except Exception as e:
    print(f'Database Error: {e}')
    sys.exit(1)
"

# Test Obsidian vault structure
if [ -d "AI-Second-Brain-Vault/00_System" ] && \
   [ -f "AI-Second-Brain-Vault/00_System/Home.md" ] && \
   [ -f "AI-Second-Brain-Vault/00_System/Operating_Rules.md" ]; then
    echo "Vault structure: OK"
else
    echo "Vault structure: ERROR"
    exit 1
fi
```

## Restore Procedures

### 1. Full Restore

To restore a complete backup:

```bash
# Before restoring, stop any running services
# (e.g., kill any Agent Mesh service, stop Obsidian if running)

# Create backup directory structure if it doesn't exist
cd ~/AI-Second-Brain
mkdir -p temp_restore

# Extract backup files (assuming you have a backup archive)
# Example for a compressed backup:
if [ -f "backup_20241201_123456.tar.gz" ]; then
    tar -xzf "backup_20241201_123456.tar.gz" -C .
    echo "Backup extracted successfully"

    # Verify extracted files
    if [ -d "AI-Second-Brain-Vault" ] && [ -d ".agent_mesh" ]; then
        echo "Extracted structure verified"
        
        # Stop existing services if running
        # (implementation specific to your environment)
        
        # Remove existing directories
        rm -rf AI-Second-Brain-Vault .agent_mesh
        
        # Restore from backup
        mv temp_restore/AI-Second-Brain-Vault .
        mv temp_restore/.agent_mesh .
        
        # Restore configuration files
        cp temp_restore/README_SETUP.md .
        cp temp_restore/SECURITY.md .
        cp temp_restore/BACKUP_AND_RESTORE.md .
        
        echo "System restored successfully from backup"
    else
        echo "Error: Backup files are corrupted"
        exit 1
    fi
else
    echo "Error: Backup file not found"
    exit 1
fi
```

### 2. Selective Restore

To restore only specific components:

```bash
# Restore just the Obsidian vault
cd ~/AI-Second-Brain
rm -rf AI-Second-Brain-Vault
tar -xzf backup_20241201_123456.tar.gz AI-Second-Brain-Vault -C .

# Restore just the database
cd ~/AI-Second-Brain
rm -f .agent_mesh/agent_mesh.sqlite
tar -xzf backup_20241201_123456.tar.gz .agent_mesh/agent_mesh.sqlite -C .
```

### 3. Incremental Restore

For restoring just specific files without full system restoration:

```bash
# Restore a single file
cd ~/AI-Second-Brain
# Assuming backup_20241201_123456.tar.gz contains the file at path/to/file.txt

# Extract the specific file
mkdir -p temp_restore
cd temp_restore
tar -xzf ../backup_20241201_123456.tar.gz path/to/file.txt

# If the target file exists, create backup first
cd ~/AI-Second-Brain
if [ -f "existing/path/to/file.txt" ]; then
    cp existing/path/to/file.txt existing/path/to/file.txt.backup
fi

# Restore the file
mv temp_restore/path/to/file.txt existing/path/to/file.txt

# Cleanup
rm -rf temp_restore
```

## Backup Best Practices

### 1. Backup Frequency

- **Daily**: For active projects or systems in development
- **Weekly**: For stable production systems
- **Monthly**: For archival or infrequently modified systems
- **Before changes**: Always before major system modifications

### 2. Backup Storage

- **Separate location**: Store backups in a different location than the main system
- **Multiple copies**: For critical systems, keep multiple copies in different locations
- **Test restore**: Regularly test backup restore procedures
- **Versioning**: Maintain backup history for quick rollback

### 3. Security Considerations

- **Encrypted backups**: Encrypt backup files to protect sensitive information
- **Access control**: Restrict backup access to authorized personnel only
- **Audit trail**: Log all backup and restore operations
- **Retention policy**: Define how long backups are kept

### 4. Backup Automation (Optional)

For automated backups, you can create scripts:

```bash
#!/bin/bash
# ~/AI-Second-Brain/scripts/backup_system.sh

BACKUP_DIR="~/backups/AI-Second-Brain"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

cp -r ~/AI-Second-Brain/AI-Second-Brain-Vault "$BACKUP_DIR/vault_$DATE"
cp -r ~/AI-Second-Brain/.agent_mesh "$BACKUP_DIR/mesh_$DATE"
cp ~/AI-Second-Brain/README_SETUP.md "$BACKUP_DIR/setup_$DATE.md"

# Verify backup
python3 -c "
import sqlite3
import os
conn = sqlite3.connect('$BACKUP_DIR/mesh_$DATE/agent_mesh.sqlite')
conn.execute('SELECT 1')
conn.close()
print('Backup $DATE verified')
"

# Clean old backups (keep last 10)
scd "$BACKUP_DIR" && ls -t vault_*. | tail -n +11 | xargs rm -rf
```

## Restore Best Practices

### 1. Preparation

- **Verify backup**: Check backup integrity before attempting restore
- **Test restore**: Test restore in a safe environment if possible
- **Stop services**: Stop all dependent services before restore
- **Environment**: Ensure restore environment matches original

### 2. Execution

- **Sequential process**: Follow restore steps in order
- **Verification**: Verify each step before proceeding
- **Rollback plan**: Have a rollback plan if restore fails
- **Documentation**: Record restore actions for future reference

### 3. Post-restore

- **Test system**: Verify system is fully functional after restore
- **Verify data**: Check that all data is correctly restored
- **Update configuration**: Update any configuration files for the restored system
- **Notify users**: Inform all users of the restore and any affected services

## Emergency Backup and Restore

### 1. Quick Backup (Emergency Only)

```bash
# Emergency backup - use when in a hurry
cd ~/AI-Second-Brain
mkdir -p emergency_backup_$(date +%Y%m%d)
cp AI-Second-Brain-Vault/00_System/Home.md emergency_backup_$(date +%Y%m%d)/
cp AI-Second-Brain-Vault/04_Tasks/ emergency_backup_$(date +%Y%m%d)/
cp .agent_mesh/agent_mesh.sqlite emergency_backup_$(date +%Y%m%d)/
```

### 2. Emergency Restore

```bash
# Emergency restore - use only when necessary
cd ~/AI-Second-Brain

# List available emergency backups
declare -a backups=()
for backup in emergency_backup_*; do
    if [ -d "$backup" ] && [ -f "$backup/Home.md" ]; then
        echo "$backup"
    fi
done

# Restore from emergency backup
BACKUP_DIR="emergency_backup_$(date -r /path/to/file +%Y%m%d)"

if [ -d "$BACKUP_DIR" ]; then
    cp -r "$BACKUP_DIR/Home.md" AI-Second-Brain-Vault/00_System/
    cp -r "$BACKUP_DIR/*.md" AI-Second-Brain-Vault/04_Tasks/ 2>/dev/null || true
    if [ -f "$BACKUP_DIR/agent_mesh.sqlite" ]; then
        cp "$BACKUP_DIR/agent_mesh.sqlite" .agent_mesh/
    fi
    echo "Emergency restore completed from $BACKUP_DIR"
else
    echo "Error: No valid emergency backup found"
    exit 1
fi
```

## Monitoring and Maintenance

### 1. Backup Health Check

```bash
#!/bin/bash
# ~/AI-Second-Brain/scripts/check_backup_health.sh

BACKUP_DIR="~/backups/AI-Second-Brain"
TODAY=$(date +%Y%m%d)

# Check if backup for today exists
if [ ! -d "$BACKUP_DIR/vault_$TODAY" ]; then
    echo "WARNING: No backup created for $TODAY"
    exit 1
fi

# Verify backup integrity
python3 -c "
import sqlite3
import os
try:
    conn = sqlite3.connect('$BACKUP_DIR/mesh_$TODAY/agent_mesh.sqlite')
    cursor = conn.execute('SELECT COUNT(*) FROM agents')
    agent_count = cursor.fetchone()[0]
    print(f"Backup contains $agent_count agents")
    conn.close()
    
    if agent_count == 0:
        print('WARNING: Backup appears to be empty')
        exit(1)
except Exception as e:
    print(f'ERROR: Backup verification failed: {e}')
    exit(1)
" || exit 1

# Check for old backups (older than 30 days)
find "$BACKUP_DIR" -name "vault_*" -mtime +30 -type d | while read backup; do
    echo "WARNING: Old backup found: $backup"
done

# Check for corrupted backups
find "$BACKUP_DIR" -name "*.sqlite" -type f ! -readable | while read corrupt; do
    echo "ERROR: Corrupted backup file: $corrupt"
    exit 1
    # Remove corrupted backup
    rm -rf "$corrupt"
done

echo "Backup health check completed for $TODAY"
```

### 2. Automated Monitoring

```bash
#!/bin/bash
# ~/AI-Second-Brain/scripts/monitor_backups.sh

# Run backup health check daily
~/AI-Second-Brain/scripts/check_backup_health.sh

# Check service health
curl -s http://127.0.0.1:8000/health | grep -q '"status": "healthy"' || echo "WARNING: Agent Mesh service may be down"

# Check for temporary files
temp_files=$(find ~/.agent_mesh/tmp -type f 2>/dev/null | wc -l)
if [ $temp_files -gt 0 ]; then
    echo "WARNING: $temp_files temporary files found in .agent_mesh/tmp/"
    echo "Cleaning up..."
    rm -rf ~/.agent_mesh/tmp/*
fi

# Log monitoring results
timestamp=$(date +%Y-%m-%d_%H:%M:%S)
echo "[$timestamp] Backup health check: PASSED" >> ~/AI-Second-Brain/.agent_mesh/logs/monitor.log
```

## Common Issues and Solutions

### 1. Backup Failure

**Problem**: Backup command fails or partial backup created.

**Solutions**:

1. **Disk Space**: Check available disk space
   ```bash
   df -h ~/AI-Second-Brain
   ```

2. **Permission Issues**: Check user permissions
   ```bash
   ls -ld ~/AI-Second-Brain
   ```

3. **Network Issues** (for network backups):
   ```bash
   # Test network connectivity
   ping backup-server.local
   ```

### 2. Restore Failure

**Problem**: Restore operation fails or results in corrupted system.

**Solutions**:

1. **Verify backup integrity**: Always test backup before attempting restore
2. **Test restore**: In development environment first
3. **Have rollback plan**: Keep previous working version available
4. **Incremental restore**: Restore individual files if needed

### 3. Backup Verification Failure

**Problem**: Backup appears to be empty or corrupted.

**Solutions**:

1. **Check backup contents**: List files in backup directory
2. **Verify database**: Test SQLite database integrity
3. **Check disk space**: Ensure enough space for backup
4. **Use simpler backup**: Try backup of individual files instead of entire system

## Conclusion

The backup and restore process is critical for maintaining system reliability and recovering from failures. While this setup does not create automatic backups (to adhere to the no-junk principle), manual backups are essential for:

- **Disaster recovery**: Restoring system after major failures
- **Version control**: Maintaining different versions of the system
- **Migration**: Moving system to different environments
- **Testing**: Testing changes in isolated environments

Remember: **The most valuable backup is one that has been tested successfully** in a restore scenario. Always test your backup and restore procedures before they are needed.

For ongoing backup and restore operations, use the scripts provided or implement your own backup strategy that fits your operational requirements and security policies.

The no-junk principle means avoid automatic backups, but regular manual backups following this guide will ensure you have recovery options when needed.
