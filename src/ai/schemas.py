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

class B2BSellerData(BaseModel):
    author_username: Optional[str] = Field(default=None, description="Author telegram @username if present")
    geo_or_chat: Optional[str] = Field(default="Global", description="Location or chat context")
    raw_ad_text: Optional[str] = Field(default="", description="Original advertising text up to 200 chars")
    sales_hook: Optional[str] = Field(default="", description="Short personalized sales hook for LeadRadar outreach")

class LeadScoringResult(BaseModel):
    reasoning: str = Field(
        default="",
        description="CRITICAL FIRST FIELD: Step-by-step unique Chain-of-Thought reasoning explaining this specific message in Russian before classification."
    )
    category: str = Field(
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
    niche_code: str = Field(
        default="other",
        description="Target niche code e.g. 'REAL_ESTATE', 'AUTO_RENTAL', 'CURRENCY_EXCHANGE', 'LEGAL_SERVICES', 'OTHER_B2B', or standard buyer niche."
    )
    rubric_name: Optional[str] = Field(
        default="Прочее",
        description="Human-readable title for the rubric e.g. '🏠 Недвижимость', '🛵 Аренда байков'."
    )
    temperature: Optional[str] = Field(
        default="WARM",
        description="Lead temperature: 'WARM' or 'HOT'."
    )
    confidence_score: float = Field(
        default=0.0,
        description="Confidence score from 0 to 100."
    )
    action_required: Optional[str] = Field(
        default="AUTO_SAVE",
        description="Required action: 'AUTO_SAVE' (confidence >= 85), 'NEED_APPROVAL' (60-84), 'DISCARD' (<60)."
    )
    extracted_data: Optional[B2BSellerData] = Field(
        default_factory=B2BSellerData,
        description="Extracted B2B seller data if category == SELLER."
    )
    intent_summary: Optional[str] = Field(
        default="",
        description="Short summary of user's purchase inquiry or intention."
    )
    sales_hook: Optional[str] = Field(
        default="",
        description="Actionable advice for the salesperson or outreach script."
    )
