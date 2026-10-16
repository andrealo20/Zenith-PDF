from .app import main

# Render processes are spawned and re-import the main module: without this guard
# each of them would start another copy of the app.
if __name__ == "__main__":
    raise SystemExit(main())
