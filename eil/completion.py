"""Text of the dialog shown when an EIL run finishes. No Qt here."""

TITLE = "EIL pipeline complete"


def completion_message(output_folder, routing_ran):
    """(title, text) telling the user the run is done and where results are."""
    text = (
        "The EIL pipeline finished successfully.\n\n"
        f"Results are in:\n{output_folder}"
    )
    if not routing_ran:
        text += (
            "\n\nDepositional routing was skipped; the log explains why."
        )
    return TITLE, text
