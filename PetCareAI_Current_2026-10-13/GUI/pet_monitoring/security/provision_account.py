"""Compatibility entry point for the single-admin local enrollment wizard.

Username provisioning and password-only replacement are intentionally removed.
"""
from pet_monitoring.main import main as app_main


def main():
    import sys
    return app_main(["--setup-admin", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
