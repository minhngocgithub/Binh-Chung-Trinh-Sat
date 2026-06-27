import re
from app.utils.money_parser import extract_max_budget

NDA_PATTERN = re.compile(
    r"\b(nda|non-disclosure|non disclosure|sign\s+an?\s+nda)\b",
    re.IGNORECASE
)

VIP_THRESHOLD = 10000


def check_vip_whale(text: str) -> tuple[bool, int | None]:
    """
    Dùng extract_max_budget từ money_parser, không tự parse nữa.
    LUÔN trả về budget thật phát hiện được (dù có đạt ngưỡng VIP hay không) —
    để Scorer Agent dùng budget này tính B1 (+25, ngưỡng >= $300).
    Chỉ cờ is_vip (True/False) mới phụ thuộc ngưỡng $10,000.
    """
    budget = extract_max_budget(text)
    is_vip = budget is not None and budget >= VIP_THRESHOLD
    return is_vip, budget


def check_nda(text: str) -> bool:
    return bool(NDA_PATTERN.search(text))


def run_pre_filters(content: str, title: str = "") -> dict:
    """
    Bộ lọc cứng layer Python — chỉ check những gì AN TOÀN bằng pattern.
    DQ1 (deadline <48h) và DQ3 (sub-contractor) KHÔNG check ở đây
    vì cần ngữ cảnh -> được xử lý trong Scorer Agent prompt (Ngày 2).

    Thứ tự bắt buộc: VIP check TRƯỚC NDA check, vì VIP Whale
    bypass toàn bộ Hard Disqualifier kể cả DQ2 (NDA).
    """
    full_text = f"{title} {content}"

    is_vip, budget = check_vip_whale(full_text)

    if is_vip:
        return {
            "is_disqualified": False,
            "disqualification_reason": None,
            "is_vip_whale": True,
            "detected_budget": budget,
        }

    if check_nda(full_text):
        return {
            "is_disqualified": True,
            "disqualification_reason": "DQ2: NDA required from first contact",
            "is_vip_whale": False,
            "detected_budget": None,
        }

    return {
        "is_disqualified": False,
        "disqualification_reason": None,
        "is_vip_whale": False,
        "detected_budget": budget,
    }