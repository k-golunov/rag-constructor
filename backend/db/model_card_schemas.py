"""Human-authored context and explicitly attributed manual measurements."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CardContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intended_use: str = Field("", max_length=6000)
    users: str = Field("", max_length=3000)
    domain: str = Field("", max_length=3000)
    owner: str = Field("", max_length=500)
    model_developer: str = Field("", max_length=500)
    model_revision: str = Field("", max_length=500)
    license: str = Field("", max_length=2000)
    training_data: str = Field("", max_length=6000)
    evaluation_data: str = Field("", max_length=6000)
    factors: str = Field("", max_length=6000)
    ethical_considerations: str = Field("", max_length=6000)
    recommendations: str = Field("", max_length=6000)


class ManualMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=40)
    sample_size: int = Field(ge=1, le=100000000)
    dataset: str = Field(min_length=1, max_length=2000)
    method: str = Field(min_length=1, max_length=4000)
    measured_at: date
    reviewer: str = Field(min_length=1, max_length=500)


class CardDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: CardContext = Field(default_factory=CardContext)
    measurements: list[ManualMeasurement] = Field(default_factory=list, max_length=50)


class CardCreate(CardDraft):
    base_version_id: UUID | None = None
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
