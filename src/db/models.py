import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import BigInteger, Integer, String, Text, Float, Numeric, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    behavior_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    activities: Mapped[List["UserActivityLog"]] = relationship(
        "UserActivityLog", back_populates="user", cascade="all, delete-orphan"
    )
    leads: Mapped[List["Lead"]] = relationship("Lead", back_populates="user")


class UserActivityLog(Base):
    __tablename__ = "user_activity_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["UserProfile"] = relationship("UserProfile", back_populates="activities")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    niche_code: Mapped[str] = mapped_column(String(100), nullable=False)
    location_code: Mapped[Optional[str]] = mapped_column(String(100), default="global", nullable=True)
    temperature: Mapped[str] = mapped_column(String(20), nullable=False) # 'WARM', 'HOT'
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    intent_summary: Mapped[str] = mapped_column(Text, nullable=False)
    sales_hook: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="AVAILABLE") # 'AVAILABLE', 'SOLD', 'EXPIRED'
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=1.00) # $1.00 USD per lead
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["UserProfile"] = relationship("UserProfile", back_populates="leads")
    purchases: Mapped[List["LeadPurchase"]] = relationship("LeadPurchase", back_populates="lead")


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="DEMO") # 'DEMO', 'REGULAR', 'VIP', 'ADMIN', 'SUPERADMIN'
    moderation_status: Mapped[str] = mapped_column(String(50), default="PENDING") # 'PENDING', 'APPROVED', 'REJECTED'
    balance: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00) # USD Balance
    subscribed_niches: Mapped[list] = mapped_column(JSON, default=list)
    subscribed_locations: Mapped[list] = mapped_column(JSON, default=list)
    niche_priorities: Mapped[dict] = mapped_column(JSON, default=dict)  # {"auto_kasko": 1, "real_estate": 2} (1=VIP 0s, 2=High 30s, 3=Standard 60s)
    is_monitoring_active: Mapped[bool] = mapped_column(default=True)
    is_debug_monitoring: Mapped[bool] = mapped_column(default=False)
    webhook_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    onboarding_step: Mapped[int] = mapped_column(default=0)
    last_nudge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    referred_by_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("partners.id"), nullable=True)
    referral_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    referral_balance: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    total_referral_earned: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    purchases: Mapped[List["LeadPurchase"]] = relationship("LeadPurchase", back_populates="partner")


class ReferralAccrual(Base):
    __tablename__ = "referral_accruals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    lead_purchase_id: Mapped[str] = mapped_column(String(36), ForeignKey("lead_purchases.id"), unique=True, nullable=False)
    referrer_id: Mapped[str] = mapped_column(String(36), ForeignKey("partners.id"), nullable=False)
    referred_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("partners.id"), nullable=False)
    payment_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    accrual_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    partner_id: Mapped[str] = mapped_column(String(36), ForeignKey("partners.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    payment_details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING") # 'PENDING', 'APPROVED', 'REJECTED', 'PAID'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LeadPurchase(Base):
    __tablename__ = "lead_purchases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id"), nullable=False)
    partner_id: Mapped[str] = mapped_column(String(36), ForeignKey("partners.id"), nullable=False)
    price_paid: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lead: Mapped["Lead"] = relationship("Lead", back_populates="purchases")
    partner: Mapped["Partner"] = relationship("Partner", back_populates="purchases")


class ChannelCandidate(Base):
    __tablename__ = "channel_candidates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username_or_link: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="RECURSIVE_MENTION") # 'RECURSIVE_MENTION', 'FORWARDED_POST', 'GLOBAL_SEARCH', 'MASS_IMPORT', 'DIRECTORY_CATALOG'
    niche_code: Mapped[Optional[str]] = mapped_column(String(100), default="community")
    location_code: Mapped[Optional[str]] = mapped_column(String(100), default="nhatrang")
    status: Mapped[str] = mapped_column(String(50), default="DISCOVERED") # 'DISCOVERED', 'APPROVED', 'REJECTED', 'FAILED'
    member_count: Mapped[Optional[int]] = mapped_column(default=0)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class MonitoredChannel(Base):
    __tablename__ = "monitored_channels"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username_or_link: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    niche_code: Mapped[str] = mapped_column(String(100), default="auto_kasko")
    location_code: Mapped[Optional[str]] = mapped_column(String(100), default="nhatrang")
    chat_type: Mapped[Optional[str]] = mapped_column(String(50), default="channel") # 'channel' or 'group'
    status: Mapped[str] = mapped_column(String(50), default="PENDING")  # 'JOINED', 'PENDING', 'FAILED'
    last_scraped_msg_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Rubric(Base):
    __tablename__ = "rubrics"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    icon: Mapped[str] = mapped_column(String(50), default="🏷️")
    is_custom: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AIStudyExemplar(Base):
    __tablename__ = "ai_study_exemplars"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    raw_message_text: Mapped[str] = mapped_column(Text, nullable=False)
    niche_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    temperature: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_lead: Mapped[bool] = mapped_column(default=True)
    intent_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sales_hook: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class NicheRequest(Base):
    __tablename__ = "niche_requests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    requested_niche: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AIEvaluationLog(Base):
    __tablename__ = "ai_evaluation_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    niche_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    temperature: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CollectorLog(Base):
    __tablename__ = "collector_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    chat_title: Mapped[str] = mapped_column(String(255), nullable=False)
    username_or_link: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_fetched_count: Mapped[int] = mapped_column(default=0)
    new_messages_count: Mapped[int] = mapped_column(default=0)
    new_leads_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(50), default="OK") # 'OK', 'EMPTY', 'RATE_LIMIT', 'ERROR'
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class CustomChatSubscription(Base):
    __tablename__ = "custom_chat_subscriptions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    partner_id: Mapped[str] = mapped_column(String(36), ForeignKey("partners.id"), nullable=False, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username_or_link: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    monthly_price: Mapped[float] = mapped_column(Numeric(10, 2), default=2.00) # $2.00 USD / month
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE") # 'ACTIVE', 'FREEZING', 'EXPIRED'
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    paid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminder_sent: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class OutreachLead(Base):
    __tablename__ = "outreach_leads"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    author_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    author_first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    niche_code: Mapped[str] = mapped_column(String(100), nullable=False) # REAL_ESTATE, AUTO_RENTAL, CURRENCY_EXCHANGE, LEGAL_SERVICES, OTHER_B2B
    location_code: Mapped[Optional[str]] = mapped_column(String(100), default="global")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(50), default="READY_FOR_OUTREACH") # 'READY_FOR_OUTREACH', 'NEED_APPROVAL', 'REJECTED', 'SENT'
    raw_ad_text: Mapped[str] = mapped_column(Text, nullable=False)
    sales_hook: Mapped[str] = mapped_column(Text, nullable=False)
    chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    messages_history: Mapped[list] = mapped_column(JSON, default=list) # Full history of author's ad messages across monitored channels
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class OutreachAccount(Base):
    __tablename__ = "outreach_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(50), unique=True, nullable=True)
    session_string: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    manager_name: Mapped[Optional[str]] = mapped_column(String(255), default="Екатерина")
    manager_role: Mapped[Optional[str]] = mapped_column(String(255), default="Руководитель отдела B2B развития LeadRadar")
    persona_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    daily_sent_count: Mapped[int] = mapped_column(Integer, default=0)
    max_daily_limit: Mapped[int] = mapped_column(Integer, default=15)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE") # 'ACTIVE', 'COOL_DOWN', 'BANNED', 'DISABLED'
    has_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class B2BProspect(Base):
    __tablename__ = "b2b_prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    niche: Mapped[str] = mapped_column(String(100), default="OTHER_B2B") # REAL_ESTATE, AUTO_RENTAL, CURRENCY_EXCHANGE, LEGAL_SERVICES, OTHER_B2B
    source_chat: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    raw_ad_text: Mapped[str] = mapped_column(Text, nullable=False)
    sales_hook: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[int] = mapped_column(Integer, default=80)
    status: Mapped[str] = mapped_column(String(50), default="PENDING_APPROVAL") # PENDING_APPROVAL, READY_FOR_OUTREACH, SENT, FAILED, BLACKLISTED
    assigned_account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("outreach_accounts.id"), nullable=True)
    generated_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dialogue_history: Mapped[list] = mapped_column(JSON, default=list) # Array of dialogue messages with timestamps
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True) # If False, AI employee is turned OFF for this chat and superadmin handles it manually
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class DiscoveredChat(Base):
    __tablename__ = "discovered_chats"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    chat_username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="REGEX_EXTRACT") # 'REGEX_EXTRACT', 'GLOBAL_SEARCH', 'COMMON_CHATS', 'MASS_IMPORT'
    audit_status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True) # 'PENDING', 'AUDITING', 'APPROVED', 'REJECTED', 'FAILED'
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chat_type: Mapped[Optional[str]] = mapped_column(String(50), default="LIVE_COMMUNITY") # 'LIVE_COMMUNITY', 'COMMERCIAL_BOARD', 'SPAM_DUMP'
    detected_niches: Mapped[list] = mapped_column(JSON, default=list)
    verdict_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BlacklistedChat(Base):
    __tablename__ = "blacklisted_chats"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    chat_username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    blacklisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )



