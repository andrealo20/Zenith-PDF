"""Entry point of the Windows executable that tools/build_exe.py builds."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()  # render processes start from the executable itself: they stop here
    from zenith_pdf.app import main

    raise SystemExit(main())
