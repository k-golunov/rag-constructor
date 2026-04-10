from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    chunk_size = Column(Integer, default=800)
    chunk_overlap = Column(Integer, default=100)
    embedding_model = Column(String(100))
    llm_model = Column(String(100))
    system_prompt = Column(Text, default="Вы полезный ассистент, отвечающий на вопросы по документам.")
    created_at = Column(DateTime(timezone=True), server_default=func.now())