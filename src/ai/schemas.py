from typing import Optional
from pydantic import BaseModel, Field

class LeadScoringResult(BaseModel):
    is_lead: bool = Field(
        description="Set to true ONLY if the user demonstrates real intent to buy/hire in target niches."
    )
    niche_code: str = Field(
        default="unknown",
        description="Target niche code e.g. 'auto_kasko', 'real_estate', 'auto_broker'."
    )
    temperature: str = Field(
        default="WARM",
        description="Lead temperature: 'WARM' or 'HOT'."
    )
    confidence_score: float = Field(
        default=0.0,
        description="Confidence score from 0.00 to 1.00."
    )
    intent_summary: str = Field(
        default="",
        description="Short summary of user's purchase inquiry or intention."
    )
    sales_hook: str = Field(
        default="",
        description="Actionable advice for the salesperson on how to approach this lead."
    )
