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
    is_lead: bool = Field(
        default=False,
        description="Set to true ONLY if the user is actively seeking a service, product, or rental (BUYER)."
    )
    is_vendor: bool = Field(
        default=False,
        description="Set to true ONLY if the author is an advertiser, freelancer, or business offering services (SELLER)."
    )
    is_vacancy: bool = Field(
        default=False,
        description="Set to true ONLY if the message is a job opening or hiring announcement (HR)."
    )
    is_job_seeker: bool = Field(
        default=False,
        description="Set to true ONLY if the message is from a person looking for a job (CV, resume, offering themselves as employee)."
    )
    intent_type: Optional[str] = Field(
        default=None,
        description="'BUY', 'RENT', 'NEED_SERVICE', 'PROBLEM_SOLVING', 'JOB_SEEKING' or null."
    )
    niche: Optional[str] = Field(
        default="OTHER",
        description="Target niche code in UPPER_SNAKE_CASE (e.g. REAL_ESTATE, LEGAL_SERVICES, YACHT_RENTAL)."
    )
    niche_code: Optional[str] = Field(
        default=None,
        description="Alias/code for niche (e.g. real_estate, bike_rent)."
    )
    rubric_name: Optional[str] = Field(
        default=None,
        description="Rubric or intent name (e.g. BUYER, WARM_LEAD, JOB_SEEKER)."
    )
    confidence_score: float = Field(
        default=0.5,
        description="AI confidence score from 0.0 to 1.0."
    )
    intent_summary: Optional[str] = Field(
        default="",
        description="Summary of intent."
    )
    sales_hook: Optional[str] = Field(
        default=None,
        description="Sales hook suggestion."
    )
    validation_check: Optional[Dict] = Field(
        default_factory=dict,
        description="Validation check metadata."
    )
    is_new_niche: bool = Field(
        default=False,
        description="True if the client's request doesn't fit base niches and you created a new one."
    )
    lead_summary: Optional[str] = Field(
        default="",
        description="Short summary of user's purchase inquiry or intention (in original language, max 100 chars)."
    )
    urgency: Optional[str] = Field(
        default="MEDIUM",
        description="Lead urgency: 'HIGH', 'MEDIUM', or 'LOW'."
    )
    estimated_budget: Optional[str] = Field(
        default=None,
        description="Estimated budget or numbers mentioned by the user (if any)."
    )
    reasoning: Optional[str] = Field(
        default="",
        description="One sentence explaining why you decided this is a lead, a vendor, a vacancy, or noise."
    )

    class Config:
        extra = "allow"
