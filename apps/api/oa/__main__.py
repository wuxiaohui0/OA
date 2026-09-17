import argparse
import os

import uvicorn
from dotenv import load_dotenv

from .app import API_ROOT, PROJECT_ROOT


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Smart OA FastAPI server")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "9000")))
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    args = parser.parse_args()
    uvicorn.run(
        "oa.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(API_ROOT / "oa")] if args.reload else None,
    )


if __name__ == "__main__":
    main()
