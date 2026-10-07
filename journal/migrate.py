"""Perform an explicitly approved, versioned Health-bee schema upgrade."""

import argparse
from pathlib import Path

from journal import store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Existing schema-v2 journal database")
    parser.add_argument("--confirm-v3-upgrade", action="store_true",
                        help="Confirm that a verified backup exists and approve the schema-v3 upgrade")
    args = parser.parse_args(argv)
    if not args.confirm_v3_upgrade:
        parser.error("--confirm-v3-upgrade is required after creating a verified backup")
    store.migrate_v2_to_v3(args.db)
    print("Upgraded journal schema to v3.")


if __name__ == "__main__":
    main()
