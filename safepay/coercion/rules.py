"""Transparent payer-side coercion indicators.

Each indicator has an id, a category, a weight, a severity (normal or severe), the
evidence that triggered it and a source note from DATA_CARD.md.

Two kinds of indicator:
  * pressure: signs that someone is pushing, threatening or controlling the payer.
  * context:  facts that make pressure more worrying (new payee, large amount, pasted
              UPI ID, ...). Context indicators are COUNTED ONLY IF AT LEAST ONE PRESSURE
              INDICATOR FIRED. So an unknown payee or a high amount can never establish
              coercion on its own. Uncounted context is still returned (counted=False)
              so the UI can show it was noted.

Severe indicators force the coercion band to "high", and the fusion engine HOLDs on
any severe indicator. Trust (trusted beneficiary or planned payment) never overrides
a severe indicator.

The input `ctx` uses the coercion dataset's column names, plus:
    checklist      list of victim-checklist item ids (post-pause only)
    stage          "pre_pause" | "post_pause"
Pause answers (ans_*) are read only at the post-pause stage.
"""
from __future__ import annotations

import config

CHECKLIST_ITEMS = {
    "under_arrest": "The caller said I am under arrest",
    "claims_authority": "The caller says they are police, CBI, RBI or my bank",
    "told_not_to_tell": "They told me not to tell anyone or not to hang up",
    "asked_to_install_app": "They asked me to install an app",
}

SRC = {
    "call": "DATA_CARD: about 1 in 4 APP scams involve a live call; active-call detection raised detection 120% (LexisNexis)",
    "digital_arrest": "DATA_CARD: digital arrest uses long video calls (median about 140 min); I4C/MHA case reporting",
    "behaviour": "DATA_CARD: coaching shows up as hesitation and segmented typing (BioCatch, Transmit Security, ITM Conf. 2026)",
    "remote": "DATA_CARD: remote-access tools and screen sharing are coercion signs (LexisNexis ThreatMetrix)",
    "velocity": "DATA_CARD: victims are pushed into repeated transfers and savings liquidation (I4C/MHA)",
    "answers": "DATA_CARD: only 5-35% of coerced users admit someone is guiding them; families helping say yes ~45%",
    "payee": "DATA_CARD: coached victims pay new payees (Mastercard/NuData); hard negatives share this on purpose",
}


def _on_call(c) -> bool:
    return bool(c.get("on_voice_call") or c.get("on_video_call"))


def _unknown_call(c) -> bool:
    return _on_call(c) and not c.get("caller_in_contacts")


def _ind(id_, category, weight, kind, evidence, source, severity="normal"):
    return {"id": id_, "category": category, "weight": weight, "severity": severity,
            "kind": kind, "evidence": evidence, "source_note": source, "counted": True,
            "source": "rule", "text_key": f"rule.{id_}"}


def evaluate_rules(ctx: dict) -> list[dict]:
    """Return every indicator that fired, with `counted` set according to the pressure gate."""
    c, out = ctx, []
    post = c.get("stage") == "post_pause"
    checklist = set(c.get("checklist") or []) if post else set()
    W = config.RULE_WEIGHTS
    SEV = config.RULE_SEVERE_WEIGHT

    # ---- threats and authority impersonation
    long_video = c.get("on_video_call") and not c.get("caller_in_contacts") \
        and c.get("call_duration_min", 0) >= config.DIGITAL_ARREST_VIDEO_MIN
    if long_video or "under_arrest" in checklist:
        ev = (f"video call with an unknown number for {c.get('call_duration_min', 0):.0f} min"
              if long_video else "checklist: caller said I am under arrest")
        out.append(_ind("digital_arrest_pattern", "threats_authority", SEV, "pressure", ev,
                        SRC["digital_arrest"], "severe"))
    if post and c.get("ans_purpose") == "govt_fine_verification":
        out.append(_ind("govt_verification_purpose", "threats_authority", W["govt_verification_purpose"],
                        "pressure", "stated purpose: government fine / verification", SRC["digital_arrest"]))
    if "claims_authority" in checklist:
        out.append(_ind("authority_claim", "threats_authority", W["authority_claim"], "pressure",
                        "checklist: caller claims to be police, CBI, RBI or bank", SRC["call"]))
    if _on_call(c) and c.get("caller_is_international"):
        out.append(_ind("international_caller", "threats_authority", W["international_caller"], "pressure",
                        "on a call with an international number", SRC["call"]))

    # ---- urgent payment pressure
    if _unknown_call(c) and not long_video:
        out.append(_ind("unknown_caller_during_payment", "urgent_pressure", W["unknown_caller_during_payment"],
                        "pressure", f"on a call with an unknown number ({c.get('call_duration_min', 0):.0f} min)",
                        SRC["call"]))
    if c.get("hour_of_day") in config.NIGHT_HOURS:
        out.append(_ind("night_payment", "urgent_pressure", W["night_payment"], "pressure",
                        f"payment at {c['hour_of_day']:02d}:00", SRC["behaviour"]))
    if c.get("hesitation_events", 0) >= config.HIGH_HESITATION_EVENTS:
        out.append(_ind("high_hesitation", "urgent_pressure", W["high_hesitation"], "pressure",
                        f"{c['hesitation_events']} hesitation pauses", SRC["behaviour"]))
    if c.get("segmented_typing"):
        out.append(_ind("segmented_typing", "urgent_pressure", W["segmented_typing"], "pressure",
                        "typing in stop-start bursts (as if being dictated)", SRC["behaviour"]))

    # ---- secrecy
    if "told_not_to_tell" in checklist:
        out.append(_ind("secrecy_request", "secrecy", SEV, "pressure",
                        "checklist: told not to tell anyone or not to disconnect", SRC["answers"], "severe"))

    # ---- remote access / screen sharing (severe)
    if c.get("screen_share_active"):
        out.append(_ind("screen_sharing_active", "remote_access", SEV, "pressure",
                        "screen sharing is on during the payment", SRC["remote"], "severe"))
    if c.get("remote_access_app_installed_7d"):
        out.append(_ind("remote_app_recent", "remote_access", SEV, "pressure",
                        "a remote-access app was installed in the last 7 days", SRC["remote"], "severe"))
    if "asked_to_install_app" in checklist:
        out.append(_ind("asked_to_install_app", "remote_access", SEV, "pressure",
                        "checklist: asked to install an app", SRC["remote"], "severe"))

    # ---- repeated payment pressure
    if c.get("new_payee_txns_24h", 0) >= config.REPEAT_NEW_PAYEE_TXNS_24H:
        out.append(_ind("repeated_new_payee_payments", "repeated_payments", W["repeated_new_payee_payments"],
                        "pressure", f"{c['new_payee_txns_24h']} earlier payments to new payees in 24 h",
                        SRC["velocity"]))
    if c.get("amount_sent_new_payees_24h", 0) >= config.HIGH_NEW_PAYEE_VOLUME_24H:
        out.append(_ind("high_new_payee_volume", "repeated_payments", W["high_new_payee_volume"], "pressure",
                        f"Rs {c['amount_sent_new_payees_24h']:,.0f} already sent to new payees in 24 h",
                        SRC["velocity"]))
    if c.get("fd_or_loan_liquidated_7d"):
        out.append(_ind("savings_liquidated", "repeated_payments", W["savings_liquidated"], "pressure",
                        "FD broken or loan taken in the last 7 days", SRC["velocity"]))

    # ---- victim-reported pressure
    # Rule revision v2 (validation evidence, see model card): "yes" is severe only when the
    # payer is on a call with an unknown number (validation precision 0.83). With no call
    # active it is weak (0.22), and with a saved contact on the call weaker still (0.12).
    if post and c.get("ans_someone_guiding") == "yes":
        if _unknown_call(c):
            out.append(_ind("guided_by_unknown_caller", "victim_reported", SEV, "pressure",
                            "says someone on the phone is guiding them, and the caller is an unknown number",
                            SRC["answers"], "severe"))
        elif _on_call(c):
            out.append(_ind("guided_by_known_contact", "victim_reported", W["guided_by_known_contact"],
                            "pressure", "says someone is guiding them (a saved contact is on the call)",
                            SRC["answers"]))
        else:
            out.append(_ind("guided_no_call", "victim_reported", W["guided_no_call"], "pressure",
                            "says someone is guiding them (no call active)", SRC["answers"]))

    # ---- context (counted only with pressure)
    if c.get("vpa_pasted"):
        out.append(_ind("vpa_pasted", "suspicious_context", W["vpa_pasted"], "context",
                        "UPI ID was pasted, not typed or picked", SRC["payee"]))
    if c.get("app_switches", 0) >= config.FREQUENT_APP_SWITCHES:
        out.append(_ind("frequent_app_switching", "suspicious_context", W["frequent_app_switching"], "context",
                        f"switched apps {c['app_switches']} times", SRC["behaviour"]))
    if _unknown_call(c) and c.get("payee_is_new"):
        out.append(_ind("unknown_call_and_new_payee", "suspicious_context", W["unknown_call_and_new_payee"],
                        "context", "paying a new payee while on a call with an unknown number", SRC["call"]))
    if c.get("payee_is_new") and c.get("amount_to_avg_ratio", 0) >= config.LARGE_AMOUNT_RATIO:
        out.append(_ind("new_payee_large_amount", "suspicious_context", W["new_payee_large_amount"], "context",
                        f"new payee and {c['amount_to_avg_ratio']:.1f}x the usual amount", SRC["payee"]))
    if c.get("amount_pct_of_balance", 0) >= config.HIGH_PCT_OF_BALANCE:
        out.append(_ind("large_share_of_balance", "suspicious_context", W["large_share_of_balance"], "context",
                        f"{c['amount_pct_of_balance']:.0f}% of the balance", SRC["payee"]))

    # ---- answer-vs-signal contradictions (computed by answer_check.py, passed in)
    for con in c.get("contradictions") or []:
        w = config.CONTRADICTION_WEIGHT if con["strength"] == "strong" else config.CONTRADICTION_SOFT_WEIGHT
        out.append(_ind(f"contradiction_{con['id']}", "victim_reported", w, "pressure", con["evidence"],
                        SRC["answers"]))

    # Pressure gate: context counts only when real pressure is present.
    has_pressure = any(i["kind"] == "pressure" for i in out)
    for i in out:
        if i["kind"] == "context" and not has_pressure:
            i["counted"] = False
    return out


def rule_points(indicators: list[dict]) -> float:
    return float(sum(i["weight"] for i in indicators if i["counted"]))


def has_severe(indicators: list[dict]) -> bool:
    return any(i["severity"] == "severe" and i["counted"] for i in indicators)
