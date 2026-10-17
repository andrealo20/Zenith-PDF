"""Entry point of the program that tools/build.py builds, on every system."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()  # render processes start from the executable itself: they stop here
    from zenith_pdf.app import main

    raise SystemExit(main())
