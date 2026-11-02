"""Identify separate software lines supplied with Costco product bundles."""
import re


# Match software titles, not hardware titles such as Microsoft Surface bundles.
_SOFTWARE_TITLE = re.compile(
    r"^(?:microsoft\s+(?:office\b|365\b)|mcafee\b|norton\b|"
    r"adobe\s+(?:acrobat\b|creative\b|photoshop\b)|"
    r"(?:antivirus|security)\s+software\b)",
    re.IGNORECASE,
)


def bundled_software_names(store_name: str, items: list[dict]) -> set[str]:
    if store_name.strip().lower() != "costco":
        return set()
    names = {(item.get("name") or "").strip() for item in items}
    software = {name for name in names if _SOFTWARE_TITLE.search(name)}
    # A software-only order does not establish a product bundle.
    return software if names - software - {""} else set()
