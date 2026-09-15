from __future__ import annotations


def main() -> int:
    raise RuntimeError(
        "Durable scheduler is task F01; no in-memory production fallback is allowed."
    )


if __name__ == "__main__":
    raise SystemExit(main())
