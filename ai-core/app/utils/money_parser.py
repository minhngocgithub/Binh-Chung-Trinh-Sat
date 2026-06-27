import re

# VIP Whale: budget >= $10,000
# Cho phép dấu phẩy hoặc chấm phân tách hàng nghìn: $10,000 / $10.000 / $15000
BUDGET_PATTERN = re.compile(
    r"\$\s?(\d{1,3}(?:[.,]\d{3})+|\d{3,})",
    re.IGNORECASE
)

# Khớp ký tự viết tắt K (5k, 10K, 1.5k) trong 3 trường hợp:
# 1. Có "$" đứng trước  -> "$10k"                      (luôn chắc chắn là tiền)
# 2. Từ khóa tiền tệ đứng TRƯỚC số+k -> "budget: 10k", "budget of 1.5K USD"
# 3. Từ khóa tiền tệ đứng SAU số+k   -> "5k budget", "10k USD"
# Không có "$" và không có từ khóa nào ở gần -> KHÔNG match (tránh "5k users", "10K race")
K_PATTERN = re.compile(
    r"\$\s?(\d+(?:\.\d+)?)\s*[kK]\b"
    r"|(?:budget|usd|dollars?)\D{0,15}?(\d+(?:\.\d+)?)\s*[kK]\b"
    r"|\b(\d+(?:\.\d+)?)\s*[kK]\D{0,15}?(?:budget|usd|dollars?)\b",
    re.IGNORECASE
)

def parse_amount(raw: str) -> int:
    # Xử lý trường hợp có phần thập phân dạng cents (ví dụ 1500.00 -> lấy 1500)
    if "." in raw and len(raw.split(".")[1]) <= 2:
        raw = raw.split(".")[0]
    cleaned = re.sub(r"[.,]", "", raw)
    return int(cleaned) if cleaned.isdigit() else 0

def extract_max_budget(text: str) -> int | None:
    budgets = []

    # 1. Quét định dạng $ thông thường
    matches = BUDGET_PATTERN.findall(text)
    for m in matches:
        val = parse_amount(m)
        if val > 0:
            budgets.append(val)

    # 2. Quét định dạng K viết tắt (3 group, lấy group nào không rỗng)
    k_matches = K_PATTERN.findall(text)
    for match in k_matches:
        amount = match[0] or match[1] or match[2]
        try:
            budgets.append(int(float(amount) * 1000))
        except ValueError:
            pass

    return max(budgets) if budgets else None