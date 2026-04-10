from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="RAG constructor API",
    description="Low-Code RAG Pipeline Constructor",
    version="0.1.0",
)

# CORS для будущего фронтенда на Angular
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],  # Angular dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Позже подключим роутеры:
# from .api import projects, documents, chat
# app.include_router(projects.router)
# app.include_router(documents.router)
# app.include_router(chat.router)