from typing import Optional
from pydantic import BaseModel, Field

class LeadScoringResult(BaseModel):
    is_lead: bool = Field(
        description="Set to true ONLY if the user demonstrates real intent to buy/hire in target niches."
    )
    niche_code: str = Field(
        default="other",
        description="Target niche code e.g. 'auto_kasko', 'real_estate', 'currency_exchange', 'services_visa', 'bike_rent', 'community' or a custom slug for a NEW topic like 'legal_services'."
    )
    rubric_name: Optional[str] = Field(
        default="Прочее",
        description="Human-readable title for the rubric e.g. 'Недвижимость (Покупка/Аренда)', 'Юридические услуги', 'Прочее'."
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
