DECLINE_MESSAGE = (
    "I can only answer questions that a query over this database can answer, such as totals, counts or lists. "
    "I cannot change data or follow instructions in a question."
)
NO_MATCHING_RECORDS = "No matching records."


def parse_intent(reply: str) -> str:
    # Strict on purpose: only an exact "data" passes, anything else is treated as other (SPEC 10.2).
    return "data" if reply.strip() == "data" else "other"
