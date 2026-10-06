"""Create, verify, and restore local Health-bee journal backups."""

import argparse
from pathlib import Path

from journal import store


def _summary(action, path, counts):
    print(f"{action}: {Path(path).expanduser().resolve()}")
    print(f"Verified {counts['entries']} entries and {counts['observations']} observations.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="Create a new verified local backup.")
    backup.add_argument("--db", type=Path, required=True, help="Existing journal database")
    backup.add_argument("--destination", type=Path, required=True, help="New backup file path")
    backup.add_argument("--confirm-encrypted-destination", action="store_true",
                        help="Confirm the chosen local destination is encrypted")

    verify = commands.add_parser("verify", help="Read-only backup verification.")
    verify.add_argument("--backup", type=Path, required=True)

    restore = commands.add_parser("restore", help="Restore a verified backup.")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True,
                         help="New output path, or existing database when replacing")
    restore.add_argument("--replace", action="store_true",
                         help="Allow replacement of the existing output database")
    restore.add_argument("--confirm-replace", default="",
                         help="Exact resolved output path required with --replace")

    args = parser.parse_args(argv)
    if args.command == "backup":
        if not args.confirm_encrypted_destination:
            parser.error("confirm the destination is encrypted before creating a personal backup")
        _summary("Created verified backup", args.destination,
                 store.backup_database(args.db, args.destination))
    elif args.command == "verify":
        _summary("Verified backup", args.backup, store.verify_database(args.backup))
    else:
        expected = str(args.output.expanduser().resolve())
        if args.replace and args.confirm_replace != expected:
            parser.error("--replace requires --confirm-replace with the exact resolved output path")
        if not args.replace and args.confirm_replace:
            parser.error("--confirm-replace requires --replace")
        _summary("Restored verified backup", args.output,
                 store.restore_database(args.backup, args.output, replace=args.replace))


if __name__ == "__main__":
    main()
