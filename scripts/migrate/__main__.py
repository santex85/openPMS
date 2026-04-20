"""Allow `python -m scripts.migrate` from repo root with PYTHONPATH=."""

from scripts.migrate.migrate import main

if __name__ == "__main__":
    main()
