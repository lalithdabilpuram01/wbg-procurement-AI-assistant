"""Personal data masking with Microsoft Presidio (pattern recognizers with validation; no language model, no network).

Masked everywhere a message is stored or forwarded (conversation turns, agent questions, prompts, investigation
records): e-mail addresses, phone numbers, payment card and IBAN numbers, IP addresses and US social security numbers.
Each is replaced by a typed placeholder such as <EMAIL_ADDRESS>. Person names are not masked: without a named-entity
model the detection is unreliable, and on this corpus a small model tags project and place names as people. Personal
contact data in the documents is withheld by the RAG privacy guard.
"""
from __future__ import annotations

import re
from functools import lru_cache

ENTITIES = ("EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS", "US_SSN")
MIN_SCORE = 0.4                       # Presidio's phone score; the phone parser itself is strict about what is a number
PHONE_REGIONS = ("ET", "LK", "ID", "AR", "SN", "KE", "IN", "US", "GB", "FR", "DE")   # corpus countries and common ones
IDENTIFIER_LEFT = re.compile(r"[A-Za-z]{2,}[-/]$")    # 'SN-PDEC-' before the digits: a procurement reference, not a phone
IDENTIFIER_RIGHT = re.compile(r"^[-/][A-Za-z]{2,}")    # '-CW-RFB' after the digits


class _BlankSpacyEngine:
    """A Presidio NLP engine over a blank spaCy pipeline: tokens only, so only the pattern recognizers apply."""

    @staticmethod
    def build():
        """Return the engine; spaCy and Presidio are imported here so they load only when masking is first used."""
        import spacy
        from presidio_analyzer.nlp_engine import SpacyNlpEngine

        class Engine(SpacyNlpEngine):
            def load(self) -> None:                       # never download or load a trained model
                self.nlp = {"en": spacy.blank("en")}
        return Engine(models=[{"lang_code": "en", "model_name": "blank"}])


def _offline_domain_list() -> None:
    """The e-mail recognizer checks top-level domains with tldextract, which downloads the public suffix list on first
    use. Use the list bundled with the package instead, so masking never goes online (and works in a fresh environment)."""
    import tldextract.tldextract as tld
    tld.TLD_EXTRACTOR = tld.TLDExtract(suffix_list_urls=(), cache_dir=None)


@lru_cache(maxsize=1)
def _engines():
    """The Presidio analyzer and anonymizer, built once and kept for the life of the process."""
    _offline_domain_list()
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_analyzer.predefined_recognizers import PhoneRecognizer
    analyzer = AnalyzerEngine(nlp_engine=_BlankSpacyEngine.build(), supported_languages=["en"])
    # Replace the default phone recognizer with one that knows the corpus countries' number formats.
    analyzer.registry.remove_recognizer("PhoneRecognizer")
    analyzer.registry.add_recognizer(PhoneRecognizer(supported_regions=list(PHONE_REGIONS)))
    return analyzer, AnonymizerEngine()


def _detect(text: str) -> list:
    """Presidio results, without phone matches that are the digits inside a hyphenated reference such as
    SN-PDEC-377835-CW-RFB or ET-ERA-221389-CW-DIR (those are procurement identifiers)."""
    analyzer, _ = _engines()
    results = analyzer.analyze(text=text, entities=list(ENTITIES), language="en", score_threshold=MIN_SCORE)
    return [r for r in results if not (r.entity_type == "PHONE_NUMBER" and (
        IDENTIFIER_LEFT.search(text[:r.start]) or IDENTIFIER_RIGHT.match(text[r.end:])))]


def find_pii(text: str) -> list[str]:
    """Entity types of the personal data found (for audit; never the values)."""
    if not text:
        return []
    return sorted({r.entity_type for r in _detect(text)})


def redact_personal_data(text: str) -> str:
    """Secrets redacted, then personal data masked: the text filter for everything stored or forwarded."""
    from conversation.redaction import redact_secrets
    return mask_pii(redact_secrets(text))


def mask_pii(text: str) -> str:
    """The text with each detected personal data value replaced by <ENTITY_TYPE>."""
    if not text:
        return text
    results = _detect(text)
    return _engines()[1].anonymize(text=text, analyzer_results=results).text if results else text
