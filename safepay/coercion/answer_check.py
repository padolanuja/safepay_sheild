"""Answer-versus-signal verification (post-pause only).

Victims are coached to lie: only 5-35% of coerced users admit someone is guiding them
(DATA_CARD). So when the pause-screen answers disagree with what the device shows,
that raises risk. Each contradiction is shown to the user gently, never as an accusation.
"""
from __future__ import annotations

GUIDANCE_CALL_MIN = 10   # an unknown call at least this long while saying "no one is guiding me"


def check_answers(ctx: dict) -> list[dict]:
    if ctx.get("stage") != "post_pause":
        return []
    out = []
    on_call = bool(ctx.get("on_voice_call") or ctx.get("on_video_call"))
    unknown_long_call = on_call and not ctx.get("caller_in_contacts") \
        and ctx.get("call_duration_min", 0) >= GUIDANCE_CALL_MIN

    if ctx.get("ans_someone_guiding") == "no" and (unknown_long_call or ctx.get("screen_share_active")):
        sig = []
        if unknown_long_call:
            sig.append(f"a {ctx.get('call_duration_min', 0):.0f}-minute call with an unknown number")
        if ctx.get("screen_share_active"):
            sig.append("screen sharing")
        out.append({
            "id": "guidance",
            "strength": "strong",      # validation precision 0.81

            "answer": "No one is guiding me",
            "signals": sig,
            "evidence": "said no one is guiding them, but the phone shows " + " and ".join(sig),
            "gentle_message": ("You said no one is guiding you. Your phone shows " + " and ".join(sig) +
                               ". If someone is telling you what to do, it is okay to stop and hang up."),
            "message_key": "contradiction.guidance",
        })

    if (ctx.get("ans_know_payee") == "yes" and ctx.get("payee_is_new")
            and not ctx.get("payee_in_contacts") and ctx.get("vpa_pasted")):
        out.append({
            "id": "know_payee",
            # soft: validation precision only 0.24 (hard negatives share this pattern). Shown to
            # the user and adds 1 point, but does not trigger the fusion "contradiction" HOLD row.
            "strength": "soft",

            "answer": "I know this person",
            "signals": ["first payment to this UPI ID", "not in your contacts", "UPI ID was pasted"],
            "evidence": "said they know the payee, but it is a first payment to a pasted UPI ID not in contacts",
            "gentle_message": ("You said you know this person, but this is your first payment to them, they "
                               "are not in your contacts and the UPI ID was pasted. Please check it is really them."),
            "message_key": "contradiction.know_payee",
        })
    return out
