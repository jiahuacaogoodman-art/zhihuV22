# -*- coding: utf-8 -*-
"""
@File    : app/models/admission_schemas.py
@Desc    : 入住流程 Pydantic Schema —— 评估、签约、缴费、入住联动

入住流程状态机：
  inquiry       咨询/预约（初始态）
  ↓
  assessing     评估中（已提交评估申请）
  ↓
  assessed      评估完成（可签约）
  ↓
  contracting   签约中（合同已生成待签字）
  ↓
  contracted    已签约（待缴费）
  ↓
  paying        缴费中（已生成账单）
  ↓
  paid          已缴费（待安排入住）
  ↓
  moving_in     办理入住中（分配床位 + 护理等级）
  ↓
  active        已入住（正常状态）
  ↓
  discharged    已离院

可退回：
  - assessing → inquiry（评估不通过或家属放弃）
  - contracting → assessed（合同条款不接受）
  - paying → contracted（缴费方式变更）
  - 任何状态 → cancelled（主动取消）
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 状态类型
# ============================================================
AdmissionStatus = Literal[
    "inquiry",       # 咨询/预约
    "assessing",     # 评估中
    "assessed",      # 评估完成
    "contracting",   # 签约中
    "contracted",    # 已签约
    "paying",        # 缴费中
    "paid",          # 已缴费
    "moving_in",     # 办理入住中
    "active",        # 已入住
    "discharged",    # 已离院
    "cancelled",     # 已取消
]

# 合法的状态迁移表
VALID_TRANSITIONS: dict[str, list[str]] = {
    "inquiry":      ["assessing", "cancelled"],
    "assessing":    ["assessed", "inquiry", "cancelled"],
    "assessed":     ["contracting", "cancelled"],
    "contracting":  ["contracted", "assessed", "cancelled"],
    "contracted":   ["paying", "cancelled"],
    "paying":       ["paid", "contracted", "cancelled"],
    "paid":         ["moving_in", "cancelled"],
    "moving_in":    ["active", "paid", "cancelled"],
    "active":       ["discharged"],
    "discharged":   [],
    "cancelled":    ["inquiry"],  # 已取消可重新进入咨询
}


# ============================================================
# 通用
# ============================================================
class _CodeMessage(BaseModel):
    code: int = 200
    message: Optional[str] = None


# ============================================================
# 1. 入住申请 (Admission Application)
# ============================================================
class AdmissionCreateRequest(BaseModel):
    """POST /api/admissions — 创建入住申请"""
    # 申请人基本信息
    applicant_name: str = Field(..., min_length=1, max_length=64, description="申请人(老人)姓名")
    applicant_gender: Optional[str] = Field(default=None, max_length=8)
    applicant_age: Optional[int] = Field(default=None, ge=0, le=150)
    applicant_id_card: Optional[str] = Field(default=None, max_length=32, description="身份证号")
    applicant_phone: Optional[str] = Field(default=None, max_length=32)

    # 家属/担保人
    guardian_name: Optional[str] = Field(default=None, max_length=64, description="担保人/家属姓名")
    guardian_phone: Optional[str] = Field(default=None, max_length=32)
    guardian_relation: Optional[str] = Field(default=None, max_length=32, description="与老人关系")
    guardian_id_card: Optional[str] = Field(default=None, max_length=32)

    # 健康/需求概况
    health_summary: Optional[str] = Field(default=None, description="健康状况摘要")
    care_needs: Optional[str] = Field(default=None, description="护理需求描述")
    preferred_room_type: Optional[str] = Field(default=None, max_length=32, description="期望房型: 单人间/双人间/多人间")
    expected_admission_date: Optional[str] = Field(default=None, description="期望入住日期")

    # 来源
    referral_source: Optional[str] = Field(default=None, max_length=64, description="来源: 家属来访/社区推荐/医院转介/网络")
    notes: Optional[str] = Field(default=None, max_length=512)


class AdmissionResponse(BaseModel):
    """入住申请完整响应"""
    model_config = ConfigDict(extra="ignore")

    admission_id: str
    status: str = "inquiry"

    # 申请人信息
    applicant_name: str
    applicant_gender: Optional[str] = None
    applicant_age: Optional[int] = None
    applicant_id_card: Optional[str] = None
    applicant_phone: Optional[str] = None

    # 家属/担保人
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    guardian_relation: Optional[str] = None
    guardian_id_card: Optional[str] = None

    # 健康/需求
    health_summary: Optional[str] = None
    care_needs: Optional[str] = None
    preferred_room_type: Optional[str] = None
    expected_admission_date: Optional[str] = None

    # 来源
    referral_source: Optional[str] = None
    notes: Optional[str] = None

    # 评估信息
    assessment_id: Optional[str] = None
    assessed_level: Optional[str] = None
    assessment_conclusion: Optional[str] = None
    assessed_at: Optional[str] = None
    assessed_by: Optional[str] = None

    # 合同信息
    contract_id: Optional[str] = None
    contract_signed_at: Optional[str] = None

    # 缴费信息
    payment_id: Optional[str] = None
    payment_status: Optional[str] = None
    paid_at: Optional[str] = None

    # 入住信息
    patient_id: Optional[str] = None
    bed_id: Optional[str] = None
    bed_number: Optional[str] = None
    care_level_key: Optional[str] = None
    actual_admission_date: Optional[str] = None

    # 离院信息
    discharge_date: Optional[str] = None
    discharge_reason: Optional[str] = None
    settlement_amount: Optional[float] = None
    refund_amount: Optional[float] = None

    # 时间戳
    created_at: str = ""
    updated_at: str = ""


class AdmissionListResponse(_CodeMessage):
    total: int
    admissions: List[AdmissionResponse] = Field(default_factory=list)


# ============================================================
# 2. 评估 (Assessment)
# ============================================================
class AssessmentSubmitRequest(BaseModel):
    """POST /api/admissions/{admission_id}/assess — 提交评估结果"""
    # Barthel / ADL 评分
    adl_score: Optional[int] = Field(default=None, ge=0, le=100, description="Barthel ADL 评分(0-100)")
    cognitive_score: Optional[int] = Field(default=None, ge=0, le=30, description="认知评分(MMSE 0-30)")
    nutrition_score: Optional[int] = Field(default=None, ge=0, le=14, description="营养风险评分(MNA 0-14)")
    fall_risk_score: Optional[int] = Field(default=None, ge=0, le=28, description="跌倒风险评分(Morse 0-28+)")
    pressure_ulcer_risk: Optional[int] = Field(default=None, ge=0, le=23, description="压疮风险(Braden 6-23)")

    # 评估结论
    recommended_level: str = Field(..., min_length=1, max_length=32, description="建议护理等级 level_key")
    conclusion: str = Field(..., min_length=1, description="评估结论/综合意见")
    special_needs: Optional[str] = Field(default=None, description="特殊需求/风险提示")

    # 评估人
    assessor: Optional[str] = Field(default=None, max_length=64, description="评估人")
    assessment_date: Optional[str] = Field(default=None, description="评估日期(默认当天)")

    # 是否通过
    approved: bool = Field(default=True, description="评估是否通过(不通过则退回咨询)")


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    assessment_id: str
    admission_id: str
    adl_score: Optional[int] = None
    cognitive_score: Optional[int] = None
    nutrition_score: Optional[int] = None
    fall_risk_score: Optional[int] = None
    pressure_ulcer_risk: Optional[int] = None
    recommended_level: str
    conclusion: str
    special_needs: Optional[str] = None
    assessor: Optional[str] = None
    assessment_date: str = ""
    approved: bool = True
    created_at: str = ""


# ============================================================
# 3. 合同 (Contract)
# ============================================================
ContractType = Literal["standard", "trial", "respite", "custom"]


class ContractCreateRequest(BaseModel):
    """POST /api/admissions/{admission_id}/contract — 创建/签约合同"""
    contract_type: ContractType = Field(default="standard", description="合同类型: 标准/试住/喘息/自定义")
    start_date: str = Field(..., min_length=1, description="合同开始日期")
    end_date: Optional[str] = Field(default=None, description="合同结束日期(空=长期)")
    care_level_key: str = Field(..., min_length=1, max_length=32, description="约定护理等级")
    monthly_fee: float = Field(..., ge=0, description="月费用(元)")
    deposit: float = Field(default=0, ge=0, description="押金(元)")
    payment_cycle: Optional[str] = Field(default="monthly", max_length=32,
                                          description="缴费周期: monthly/quarterly/yearly")

    # 服务条款
    service_scope: Optional[str] = Field(default=None, description="服务范围说明")
    special_terms: Optional[str] = Field(default=None, description="特别约定")

    # 签约人
    signed_by_guardian: Optional[str] = Field(default=None, max_length=64, description="家属签字人")
    signed_by_institution: Optional[str] = Field(default=None, max_length=64, description="机构代表")


class ContractResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contract_id: str
    admission_id: str
    contract_type: str = "standard"
    contract_number: str = ""
    start_date: str
    end_date: Optional[str] = None
    care_level_key: str
    monthly_fee: float
    deposit: float = 0
    payment_cycle: str = "monthly"
    service_scope: Optional[str] = None
    special_terms: Optional[str] = None
    signed_by_guardian: Optional[str] = None
    signed_by_institution: Optional[str] = None
    signed_at: Optional[str] = None
    status: str = "active"  # active / terminated / expired
    created_at: str = ""


# ============================================================
# 4. 缴费 (Payment)
# ============================================================
PaymentMethod = Literal["cash", "bank_transfer", "wechat", "alipay", "pos", "other"]
PaymentType = Literal["deposit", "monthly", "quarterly", "yearly", "other"]


class PaymentCreateRequest(BaseModel):
    """POST /api/admissions/{admission_id}/pay — 记录缴费"""
    payment_type: PaymentType = Field(default="deposit", description="缴费类型: 押金/月费/季费/年费")
    amount: float = Field(..., gt=0, description="缴费金额(元)")
    payment_method: PaymentMethod = Field(default="cash", description="支付方式")
    receipt_number: Optional[str] = Field(default=None, max_length=64, description="收据编号")
    period_start: Optional[str] = Field(default=None, description="费用起始日期")
    period_end: Optional[str] = Field(default=None, description="费用截止日期")
    payer: Optional[str] = Field(default=None, max_length=64, description="缴费人")
    notes: Optional[str] = Field(default=None, max_length=256)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    payment_id: str
    admission_id: str
    contract_id: Optional[str] = None
    payment_type: str = "deposit"
    amount: float
    payment_method: str = "cash"
    receipt_number: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    payer: Optional[str] = None
    notes: Optional[str] = None
    status: str = "completed"  # completed / refunded / pending
    paid_at: str = ""
    created_at: str = ""


class PaymentListResponse(_CodeMessage):
    total: int
    payments: List[PaymentResponse] = Field(default_factory=list)


# ============================================================
# 5. 办理入住 (Move-In / Check-In)
# ============================================================
class MoveInRequest(BaseModel):
    """POST /api/admissions/{admission_id}/move-in — 办理入住"""
    bed_id: str = Field(..., min_length=1, max_length=64, description="分配的床位ID")
    care_level_key: Optional[str] = Field(default=None, max_length=32,
                                           description="确认护理等级(默认使用合同约定)")
    primary_nurse: Optional[str] = Field(default=None, max_length=64, description="责任护士")
    admission_date: Optional[str] = Field(default=None, description="入住日期(默认当天)")
    notes: Optional[str] = Field(default=None, max_length=256)


# ============================================================
# 6. 状态变更/离院
# ============================================================
class StatusChangeRequest(BaseModel):
    """PATCH /api/admissions/{admission_id}/status — 手动变更状态"""
    target_status: AdmissionStatus
    reason: Optional[str] = Field(default=None, max_length=256, description="变更原因")


class DischargeRequest(BaseModel):
    """POST /api/admissions/{admission_id}/discharge — 办理离院"""
    discharge_date: Optional[str] = Field(default=None, description="离院日期(默认当天)")
    discharge_reason: Optional[str] = Field(default=None, max_length=256, description="离院原因")
    settlement_amount: Optional[float] = Field(default=None, ge=0, description="结算金额")
    refund_amount: Optional[float] = Field(default=None, ge=0, description="退费金额")
    notes: Optional[str] = Field(default=None, max_length=512)


# ============================================================
# 7. 入住流程时间线
# ============================================================
class AdmissionTimelineEntry(BaseModel):
    timestamp: str
    action: str
    operator: Optional[str] = None
    detail: Optional[str] = None


class AdmissionTimelineResponse(_CodeMessage):
    admission_id: str
    timeline: List[AdmissionTimelineEntry] = Field(default_factory=list)


# ============================================================
# 8. 经营统计（院长仪表盘）
# ============================================================
class AdmissionStatsRecent(BaseModel):
    """近 N 天滚动窗口指标"""
    period: str
    new_admissions: int = 0
    moved_in: int = 0
    discharged: int = 0
    revenue: float = 0.0


class AdmissionStatsOccupancy(BaseModel):
    occupied_beds: int = 0
    total_beds: int = 0
    occupancy_rate: Optional[float] = None  # 0..1，无床位时为 null


class AdmissionStatsConversion(BaseModel):
    inquiry_to_active: Optional[float] = None  # 0..1，无申请时为 null


class AdmissionStatsResponse(_CodeMessage):
    """GET /api/admissions/stats —— 经营统计聚合"""
    total: int = 0
    active_residents: int = 0
    discharged: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_referral: dict[str, int] = Field(default_factory=dict)
    recent: AdmissionStatsRecent
    revenue_total: float = 0.0
    occupancy: AdmissionStatsOccupancy
    conversion: AdmissionStatsConversion


__all__ = [
    "AdmissionStatus", "VALID_TRANSITIONS",
    "AdmissionCreateRequest", "AdmissionResponse", "AdmissionListResponse",
    "AssessmentSubmitRequest", "AssessmentResponse",
    "ContractCreateRequest", "ContractResponse",
    "PaymentCreateRequest", "PaymentResponse", "PaymentListResponse",
    "MoveInRequest", "StatusChangeRequest", "DischargeRequest",
    "AdmissionTimelineEntry", "AdmissionTimelineResponse",
    "AdmissionStatsRecent", "AdmissionStatsOccupancy", "AdmissionStatsConversion",
    "AdmissionStatsResponse",
]
