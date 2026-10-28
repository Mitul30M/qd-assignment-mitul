"""Reply templates (deterministic; only verified numbers/trip ids are ever inserted).

The templates are the post-validation: every rupee figure and trip id is inserted
from verified facts in flow.py, never from rider text or model output.
"""

from __future__ import annotations

# ------------- templates -------------


def need_info(remaining: bool) -> str:
    if remaining:
        return "Samajh nahi pa raha — kaunsi date ya kaunsa order id (jaise T123456) galat lag raha hai? Bata dijiye."
    return "Details clear nahi hain. Main aapka case ops team ko bhej deta hoon, wo aapse baat karenge."


def pay_confirmed(amount: int, route: str, facts: dict) -> str:
    parts: list[str] = []
    for tid, s in (facts.get("trip_shortfalls") or {}).items():
        parts.append(f"order {tid} pe ₹{int(s)} kam aaya")
    day = facts.get("day")
    if facts.get("incentive_shortfall"):
        parts.append(f"{day or 'us'} din ki ₹{int(facts['incentive_shortfall'])} incentive")
    summary = " aur ".join(parts) if parts else f"₹{amount} ka shortfall"
    if route == "auto":
        return (f"Dekha: {summary}. Total ₹{amount} aapko due tha. "
                f"PaySwift par ₹{amount} payment process ho gaya hai.")
    return (f"Dekha: {summary}. Total ₹{amount} aapko due tha. "
            f"Ye amount ops approval ke liye bhej diya hai; approve hote hi PaySwift se aayega.")


def approval_logged(amount: int, route_reason: str, facts: dict) -> str:
    short = "; ".join(
        f"order {t} pe ₹{int(s)}" for t, s in (facts.get("trip_shortfalls") or {}).items())
    if facts.get("incentive_gap"):
        short = (short + "; " if short else "") + f"{facts.get('day') or 'us'} din ki ₹150 incentive"
    return (f"Aapne sahi kehna. {short.strip()}. "
            f"₹{amount} aapke due hai lekin {route_reason or 'auto limit ke bahar'}, "
            "isliye ops approval ke baad PaySwift se jayega.")


def incentive_not_earned(day: str, completed: int) -> str:
    return (f"{day} ko aapke {completed} orders complete hue the. "
            f"Incentive 12 orders tak milta hai, isliye us din incentive nahi bana.")


def already_paid(info: dict) -> str:
    return (f"Order {info['trip_id']} ke liye humare record ke mutabik ₹{int(info['expected'])} "
            f"bante the aur ₹{int(info['paid'])} aapko aa gaye, farak nahi raha.")


def out_of_window(day: str) -> str:
    return (f"Us payment ({day}) ki objection ke liye 7 din ka window nikal chuka hai, "
            "isliye ab wo dispute nahi ho sakta. Koi aur mudda hai to batayein.")


def escalation(reason: str) -> str:
    return ("Aapki baat samajh gaya. Main ye case ops team ko bhej raha hoon; "
            "wo aapse jald contact karenge.")


def identity_bound() -> str:
    return ("Main sirf is number se jude account ki help kar sakta hoon. "
            "Koi aur rider id ka dispute is number se approve nahi ho sakta.")


def silent_ok() -> str:
    return "Theek hain 🙏 Aapki payments dekhne mein help chahiye to batayein."


def status_update(amount: int) -> str:
    return f"₹{amount} ka payment PaySwift pe process ho gaya hai."


def repeat_explained(amount: int, facts: dict) -> str:
    return (f"Ye dispute pehle hi process ho chuka hai — ₹{amount} PaySwift se aa chuke "
            "hain. Dobara payment nahi ho sakta.")


def generic_greeting(remaining_asks: bool) -> str:
    return need_info(remaining_asks)


# Rider-safe categories for ops decisions. Internal ops reasons are NEVER echoed
# verbatim; the notice says what the decision was and the rider-safe category.
_CATEGORY_TEXT = {
    "records_full": "records ke mutabik aapko poora payment mil chuka hai",
    "out_of_window": "dispute 7 din ke window ke bahar tha",
    "over_cap": "₹200 se badi raqam policy ke mutabik automatic nahi ho sakti thi",
    "already_autopaid": "is dispute ka payment pehle hi aa chuka hai",
    "unverifiable": "jo details confirm karni thi wo verify nahi ho payi",
    "provider_unconfirmed": "payment provider se is raqam ki confirmation nahi mili",
    "identity": "ye order aapke rider account se juda nahi hai",
    "injection": "is message ko humari policy ke mutabik aage nahi badhaya ja sakta",
    "tone": "baat-cheet ki wajah se ye case aage nahi badha ja sakta",
    "human_request": "humari ops team ne aapki request note kar li hai",
}


def ops_decision(notice: dict) -> str:
    """First segment of the rider's next reply: an ops decision on their dispute."""
    if notice["decision"] == "approved":
        amount = notice["amount"] or 0
        return (f"Update: aapka dispute ops approval ke baad process ho gaya — "
                f"₹{amount} PaySwift se aa gaya hai.")
    category = notice.get("category") or "unverifiable"
    reason = _CATEGORY_TEXT.get(category, _CATEGORY_TEXT["unverifiable"])
    return (f"Update: aapka dispute ops team ne review kiya aur reject kar diya — "
            f"{reason}. Naya issue ho to zaroor bataiye.")
