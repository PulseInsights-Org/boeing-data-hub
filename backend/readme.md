# Backend Server

## Prerequisites

- Python installed
- Redis running (required for Celery)
- Environment variables configured

## Running the Server

Open **6 separate terminals** in the `backend` folder and run each command in its own terminal:

### Terminal 1 - FastAPI Server
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Terminal 2 - Extraction & Normalization Worker
```bash
celery -A celery_app worker --pool=solo -Q extraction,normalization -l info -n extract@%h
```

### Terminal 3 - Publishing Worker
```bash
celery -A celery_app worker --pool=solo -Q publishing -l info -n publish@%h
```

### Terminal 4 - Sync Worker
```bash
celery -A celery_app worker --pool=solo -Q sync_boeing,sync_shopify --concurrency=1 -l info -n sync@%h
```

### Terminal 5 - Celery Beat (Scheduler)
```bash
celery -A celery_app.celery_config:celery_app beat --loglevel=info
```

### Terminal 6 - Default Worker
```bash
celery -A celery_app worker --pool=solo -Q default -l info -n default@%h
```

## Notes

- All commands should be run from the `backend` directory
- The FastAPI server runs on `http://127.0.0.1:8000`
- Make sure Redis is running before starting Celery workers
