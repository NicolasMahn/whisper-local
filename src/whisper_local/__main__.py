import sys


def main() -> int:
    if sys.platform != "linux":
        sys.exit("whisper-local: this is the Linux app; on the Mac, build the one in macos/")
    from whisper_local.gnome import main as run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
