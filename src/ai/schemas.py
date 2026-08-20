from typing import Optional, Dict
from pydantic import BaseModel, Field

class ValidationCheck(BaseModel):
    is_author_seeking_service: bool = Field(
        default=False,
        description="True if author is a client actively seeking/buying a service/property."
    )
    is_author_offering_service: bool = Field(
        default=False,
        description="True if author is a seller, landlord, realtor, or agent offering a service/property."
    )
    is_time_relevant: bool = Field(
        default=True,
        description="True if inquiry is relevant now/recently, False if past experience discussion or irrelevant."
    )

class LeadScoringResult(BaseModel):
    reasoning: str = Field(
        default="",
        description="Step-by-step logical reasoning analysis of the user's message BEFORE making the lead decision."
    )
    validation_check: Optional[ValidationCheck] = Field(
        default_factory=ValidationCheck,
        description="Checklist verification of author intent and seller exclusion."
    )
    is_lead: bool = Field(
        description="Set to true ONLY if reasoning & validation_check confirm real buyer/tenant intent AND is_author_offering_service is False."
    )
    niche_code: str = Field(
        default="other",
        description="Target niche code e.g. 'auto_kasko', 'real_estate', 'currency_exchange', 'services_visa', 'bike_rent', 'community' or custom slug."
    )
    rubric_name: Optional[str] = Field(
        default="Прочее",
        description="Human-readable title for the rubric e.g. '🏠 Недвижимость', '🛵 Аренда байков', '💱 Обмен валюты'."
    )
    temperature: Optional[str] = Field(
        default="WARM",
        description="Lead temperature: 'WARM' or 'HOT'."
    )
    confidence_score: float = Field(
        default=0.0,
        description="Confidence score from 0.00 to 1.00."
    )
    intent_summary: Optional[str] = Field(
        default="",
        description="Short summary of user's purchase inquiry or intention."
    )
    sales_hook: Optional[str] = Field(
        default="",
        description="Actionable advice for the salesperson on how to approach this lead."
    )
