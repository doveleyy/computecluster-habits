import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "services.habit_tracker.main:app",
        host=os.environ.get("APP_HOST", "0.0.0.0"),
        port=int(os.environ.get("APP_PORT", "8100")),
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
