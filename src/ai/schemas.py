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
    reasoning: Optional[str] = Field(
        default="",
        description="CRITICAL FIRST FIELD: Step-by-step unique 2-sentence Chain-of-Thought reasoning explaining this specific message in Russian before classification."
    )
    category: Optional[str] = Field(
        default="BUYER",
        description="Category classification: 'BUYER' (client seeking service), 'SELLER' (b2b seller/service provider), or 'IGNORE' (flood/noise)."
    )
    validation_check: Optional[ValidationCheck] = Field(
        default_factory=ValidationCheck,
        description="Checklist verification of author intent and seller exclusion."
    )
    is_lead: bool = Field(
        default=False,
        description="Set to true ONLY if category is BUYER and is_author_seeking_service is True."
    )
    location_code: Optional[str] = Field(
        default="global",
        description="Target GEO location code inferred from message: 'moscow', 'dubai', 'bali', 'nhatrang', 'vietnam', 'phuket', 'thailand', or 'global'."
    )
    niche_code: Optional[str] = Field(
        default="other",
        description="Target niche code for BUYER or SELLER e.g. 'real_estate', 'bike_rent', 'currency_exchange', 'auto_kasko', 'legal_services', 'other_b2b'."
    )
    rubric_name: Optional[str] = Field(
        default="Прочее",
        description="Human-readable title for the rubric e.g. '🏠 Недвижимость', '🛵 Аренда байков'."
    )
    temperature: Optional[str] = Field(
        default="WARM",
        description="Lead temperature: 'WARM' or 'HOT'."
    )
    confidence_score: Optional[float] = Field(
        default=0.0,
        description="Confidence score from 0 to 100."
    )
    intent_summary: Optional[str] = Field(
        default="",
        description="Short summary of user's purchase inquiry or intention."
    )
    sales_hook: Optional[str] = Field(
        default="",
        description="Actionable advice for the salesperson or outreach script."
    )
