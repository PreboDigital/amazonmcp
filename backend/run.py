import os
import uvicorn

if __name__ == "__main__":
    is_dev = os.environ.get("ENVIRONMENT", "development").lower() != "production"
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=is_dev,
        workers=1 if is_dev else int(os.environ.get("WEB_CONCURRENCY", 4)),
    )
