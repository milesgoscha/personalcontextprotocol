#!/usr/bin/env python3
"""
Migration script: Per-User Containers → Multi-Tenant Architecture

This script migrates existing PCP user data from per-user Docker containers
to the multi-tenant shared storage format.

Usage:
    # Preview what would be migrated (dry run)
    python migrate_to_multitenant.py --dry-run

    # Run the migration
    python migrate_to_multitenant.py --source /old/data --dest /new/shared/data

    # Migrate a specific user
    python migrate_to_multitenant.py --user alice --source /old/data --dest /new/shared/data

Architecture Changes:
    Before: /data/objects.jsonl (per-container, one container per user)
    After:  /data/{user_id}/objects.jsonl (shared volume, scoped by user)

Files Migrated:
    - objects.jsonl (events, learnings, reflections)
    - grants.json (pending and active grants)
    - identity.json (user identity info)

Files NOT Migrated (must be regenerated):
    - signing_key.bin (new signing key per user in multi-tenant)
    - tokens.json (tokens must be reissued with new signing key)

Post-Migration Steps:
    1. Update Node records in control plane database
    2. Notify users to regenerate their API tokens
    3. Remove old Docker containers and volumes
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Generator


def get_container_volumes() -> dict[str, str]:
    """Get mapping of pcp-{username} containers to their volumes."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=pcp-", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        containers = result.stdout.strip().split("\n")
        containers = [c for c in containers if c.startswith("pcp-") and c != "pcp-traefik" and c != "pcp-control-plane" and c != "pcp-postgres" and c != "pcp-node"]

        volume_map = {}
        for container in containers:
            username = container.replace("pcp-", "")
            result = subprocess.run(
                ["docker", "inspect", container, "--format", "{{range .Mounts}}{{.Name}}{{end}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            volume_name = result.stdout.strip()
            if volume_name:
                volume_map[username] = volume_name

        return volume_map
    except subprocess.CalledProcessError as e:
        print(f"Error listing Docker containers: {e}")
        return {}


def export_from_container(container_name: str, dest_dir: Path) -> bool:
    """Export data files from a running container."""
    files_to_export = ["objects.jsonl", "grants.json", "identity.json"]

    dest_dir.mkdir(parents=True, exist_ok=True)

    for filename in files_to_export:
        source_path = f"{container_name}:/data/{filename}"
        dest_path = dest_dir / filename

        try:
            subprocess.run(
                ["docker", "cp", source_path, str(dest_path)],
                capture_output=True,
                check=True,
            )
            print(f"  ✓ Exported {filename}")
        except subprocess.CalledProcessError:
            # File might not exist (e.g., no grants yet)
            print(f"  - Skipped {filename} (not found)")

    return True


def copy_from_directory(source_dir: Path, dest_dir: Path) -> bool:
    """Copy data files from a directory (for local migration)."""
    files_to_copy = ["objects.jsonl", "grants.json", "identity.json"]

    dest_dir.mkdir(parents=True, exist_ok=True)

    for filename in files_to_copy:
        source_path = source_dir / filename
        dest_path = dest_dir / filename

        if source_path.exists():
            shutil.copy2(source_path, dest_path)
            print(f"  ✓ Copied {filename}")
        else:
            print(f"  - Skipped {filename} (not found)")

    return True


def validate_destination(dest_dir: Path, user_id: str) -> list[str]:
    """Check if destination already has data (prevent accidental overwrite)."""
    user_dir = dest_dir / user_id
    conflicts = []

    if user_dir.exists():
        for filename in ["objects.jsonl", "grants.json", "identity.json"]:
            if (user_dir / filename).exists():
                conflicts.append(str(user_dir / filename))

    return conflicts


def count_objects(filepath: Path) -> int:
    """Count objects in a JSONL file."""
    if not filepath.exists():
        return 0
    with open(filepath) as f:
        return sum(1 for line in f if line.strip())


def migrate_user_docker(username: str, dest_dir: Path, dry_run: bool = False) -> bool:
    """Migrate a single user from Docker container to shared storage."""
    container_name = f"pcp-{username}"
    user_dir = dest_dir / username

    print(f"\nMigrating user: {username}")
    print(f"  Container: {container_name}")
    print(f"  Destination: {user_dir}")

    # Check for conflicts
    conflicts = validate_destination(dest_dir, username)
    if conflicts:
        print(f"  ⚠ WARNING: Destination already has data:")
        for c in conflicts:
            print(f"    - {c}")
        if not dry_run:
            response = input("  Continue and overwrite? [y/N]: ")
            if response.lower() != "y":
                print("  Skipped.")
                return False

    if dry_run:
        print("  [DRY RUN] Would export files from container")
        return True

    return export_from_container(container_name, user_dir)


def migrate_user_directory(source_dir: Path, dest_dir: Path, user_id: str, dry_run: bool = False) -> bool:
    """Migrate a single user from source directory to shared storage."""
    user_dest = dest_dir / user_id

    print(f"\nMigrating user: {user_id}")
    print(f"  Source: {source_dir}")
    print(f"  Destination: {user_dest}")

    # Count objects
    objects_file = source_dir / "objects.jsonl"
    object_count = count_objects(objects_file)
    print(f"  Objects: {object_count}")

    # Check for conflicts
    conflicts = validate_destination(dest_dir, user_id)
    if conflicts:
        print(f"  ⚠ WARNING: Destination already has data:")
        for c in conflicts:
            print(f"    - {c}")
        if not dry_run:
            response = input("  Continue and overwrite? [y/N]: ")
            if response.lower() != "y":
                print("  Skipped.")
                return False

    if dry_run:
        print("  [DRY RUN] Would copy files")
        return True

    return copy_from_directory(source_dir, user_dest)


def generate_sql_updates(users: list[str], dest_dir: Path, pcp_domain: str) -> str:
    """Generate SQL to update Node records in control plane database."""
    statements = [
        "-- Update Node records for multi-tenant mode",
        "-- Run these in the control plane PostgreSQL database",
        "",
    ]

    for user_id in users:
        public_url = f"https://{user_id}.{pcp_domain}"
        node_id = f"pcp://{user_id}"

        # Note: This assumes user_id matches the username in the users table
        # In practice, you'd need to map usernames to UUIDs
        statements.append(f"""
-- Update node for user: {user_id}
UPDATE nodes
SET container_id = NULL,
    container_name = NULL,
    volume_name = NULL,
    admin_token_encrypted = NULL,
    status = 'running',
    health_status = 'healthy',
    public_url = '{public_url}',
    node_id = '{node_id}',
    updated_at = NOW()
WHERE user_id = (SELECT id FROM users WHERE username = '{user_id}');
""")

    return "\n".join(statements)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate PCP from per-user containers to multi-tenant architecture",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="Source directory containing user data (for local migration)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="Destination directory for shared storage",
    )
    parser.add_argument(
        "--user",
        type=str,
        help="Migrate only this specific user",
    )
    parser.add_argument(
        "--from-docker",
        action="store_true",
        help="Migrate from running Docker containers (requires Docker access)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--pcp-domain",
        type=str,
        default="pcp.example.com",
        help="PCP domain for generating SQL updates",
    )
    parser.add_argument(
        "--generate-sql",
        action="store_true",
        help="Generate SQL to update control plane database",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("PCP Multi-Tenant Migration Tool")
    print("=" * 60)

    if args.dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")

    migrated_users = []

    if args.from_docker:
        # Migrate from Docker containers
        print("\nDiscovering Docker containers...")
        volume_map = get_container_volumes()

        if not volume_map:
            print("No pcp-* containers found.")
            sys.exit(1)

        print(f"Found {len(volume_map)} user containers:")
        for username, volume in volume_map.items():
            print(f"  - {username} ({volume})")

        if args.user:
            if args.user not in volume_map:
                print(f"\nError: User '{args.user}' not found in containers")
                sys.exit(1)
            users_to_migrate = [args.user]
        else:
            users_to_migrate = list(volume_map.keys())

        for username in users_to_migrate:
            if migrate_user_docker(username, args.dest, args.dry_run):
                migrated_users.append(username)

    elif args.source:
        # Migrate from local directories
        if not args.source.exists():
            print(f"Error: Source directory does not exist: {args.source}")
            sys.exit(1)

        if args.user:
            # Single user migration
            user_source = args.source / args.user if (args.source / args.user).exists() else args.source
            if migrate_user_directory(user_source, args.dest, args.user, args.dry_run):
                migrated_users.append(args.user)
        else:
            # Multi-user migration - assume source has user subdirectories
            for user_dir in args.source.iterdir():
                if user_dir.is_dir() and (user_dir / "objects.jsonl").exists():
                    if migrate_user_directory(user_dir, args.dest, user_dir.name, args.dry_run):
                        migrated_users.append(user_dir.name)

    else:
        print("Error: Must specify either --from-docker or --source")
        parser.print_help()
        sys.exit(1)

    # Summary
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"Users migrated: {len(migrated_users)}")
    for user in migrated_users:
        print(f"  ✓ {user}")

    # Generate SQL if requested
    if args.generate_sql and migrated_users:
        sql = generate_sql_updates(migrated_users, args.dest, args.pcp_domain)
        sql_file = args.dest / "update_nodes.sql"
        if not args.dry_run:
            with open(sql_file, "w") as f:
                f.write(sql)
            print(f"\nSQL updates written to: {sql_file}")
        else:
            print("\n[DRY RUN] SQL that would be generated:")
            print(sql)

    # Post-migration notes
    print("\n" + "=" * 60)
    print("Post-Migration Steps")
    print("=" * 60)
    print("""
1. Update control plane database:
   - Run the generated SQL file, or
   - Manually update Node records to clear Docker fields

2. Notify users to regenerate API tokens:
   - Old tokens used per-container signing keys
   - New tokens will use per-user signing keys in shared storage

3. Clean up old resources:
   - docker rm pcp-{username}  # Remove old containers
   - docker volume rm pcp-data-{username}  # Remove old volumes

4. Test the migration:
   - Verify users can log in to dashboard
   - Verify data is accessible via API
   - Verify MCP connections work with subdomain routing
""")


if __name__ == "__main__":
    main()
