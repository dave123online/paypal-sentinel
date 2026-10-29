import uuid
from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field

ThreatLevel = Literal["low", "medium", "high", "critical"]
ActionTaken = Literal["approved", "held", "blocked", "alerted"]
# Provenance : appel API réel au sandbox, webhook vérifié, ou simulation locale
Source = Literal["paypal_sandbox", "paypal_webhook", "simulated"]
SuggestionType = Literal["revenue_opportunity", "cash_flow_alert", "customer_retention", "operational_improvement"]

def _now() -> datetime:
    return datetime.now(timezone.utc)

class TransactionEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"EVT_{uuid.uuid4().hex[:12]}")
    event_type: str
    transaction_id: str
    amount: float
    currency: str = "EUR"
    buyer_email: Optional[str] = None
    buyer_country: Optional[str] = None
    seller_email: Optional[str] = None
    timestamp: datetime = Field(default_factory=_now)
    raw_payload: dict = Field(default_factory=dict)

class ShieldVerdict(BaseModel):
    transaction_id: str
    threat_level: ThreatLevel
    risk_score: float = Field(ge=0, le=100)
    reason: str
    action_taken: ActionTaken
    confidence: float = Field(ge=0, le=1, default=0.9)
    explanation_for_merchant: str
    source: Source = "simulated"
    acknowledged: bool = False   # alerte prise en compte par le marchand (sans capture)
    timestamp: datetime = Field(default_factory=_now)

class AllySuggestion(BaseModel):
    suggestion_id: str = Field(default_factory=lambda: f"SUG_{uuid.uuid4().hex[:12]}") # <--- NOUVEAU
    merchant_id: str
    suggestion_type: SuggestionType
    title: str
    description: str
    estimated_impact_eur: float
    action: str
    requires_approval: bool = True
    timestamp: datetime = Field(default_factory=_now)

class MerchantState(BaseModel):
    merchant_id: str
    stress_level: float = Field(ge=0, le=10, default=3)
    monthly_revenue: float = 5000
    pending_disputes: int = 0
    fraud_attempts_blocked: int = 0
    money_saved_by_shield: float = 0
    unpaid_invoices_count: int = 0      # <--- NOUVEAU
    unpaid_invoices_amount: float = 0.0 # <--- NOUVEAU
    last_activity: datetime = Field(default_factory=_now)
