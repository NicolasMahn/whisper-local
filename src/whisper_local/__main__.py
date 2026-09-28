import sys


def main() -> int:
    if sys.platform != "linux":
        sys.exit("whisper-local: this app is for Linux (GNOME)")
    from whisper_local.gnome import main as run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
