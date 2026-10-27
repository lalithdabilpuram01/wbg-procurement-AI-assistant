"""Personal data masking with Microsoft Presidio (pattern recognizers with validation; no language model, no network).

Masked everywhere a message is stored or forwarded (conversation turns, agent questions, prompts, investigation
records): e-mail addresses, phone numbers, payment card and IBAN numbers, IP addresses and US social security numbers.
Each is replaced by a typed placeholder such as <EMAIL_ADDRESS>. Person names are not masked: without a named-entity
model the detection is unreliable, and on this corpus a small model tags project and place names as people. Personal
contact data in the documents is withheld by the RAG privacy guard.
"""
from __future__ import annotations

from functools import lru_cache

ENTITIES = ("EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS", "US_SSN")
MIN_SCORE = 0.4                       # Presidio's phone score; the phone parser itself is strict about what is a number
PHONE_REGIONS = ("ET", "LK", "ID", "AR", "SN", "KE", "IN", "US", "GB", "FR", "DE")   # corpus countries and common ones


class _BlankSpacyEngine:
    """A Presidio NLP engine over a blank spaCy pipeline: tokens only, so only the pattern recognizers apply."""

    @staticmethod
    def build():
        import spacy
        from presidio_analyzer.nlp_engine import SpacyNlpEngine

        class Engine(SpacyNlpEngine):
            def load(self) -> None:                       # never download or load a trained model
                self.nlp = {"en": spacy.blank("en")}
        return Engine(models=[{"lang_code": "en", "model_name": "blank"}])


@lru_cache(maxsize=1)
def _engines():
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_analyzer.predefined_recognizers import PhoneRecognizer
    analyzer = AnalyzerEngine(nlp_engine=_BlankSpacyEngine.build(), supported_languages=["en"])
    analyzer.registry.remove_recognizer("PhoneRecognizer")
    analyzer.registry.add_recognizer(PhoneRecognizer(supported_regions=list(PHONE_REGIONS)))
    return analyzer, AnonymizerEngine()


def find_pii(text: str) -> list[str]:
    """Entity types of the personal data found (for audit; never the values)."""
    if not text:
        return []
    analyzer, _ = _engines()
    return sorted({r.entity_type for r in analyzer.analyze(text=text, entities=list(ENTITIES), language="en",
                                                           score_threshold=MIN_SCORE)})


def redact_personal_data(text: str) -> str:
    """Secrets redacted, then personal data masked: the text filter for everything stored or forwarded."""
    from conversation.redaction import redact_secrets
    return mask_pii(redact_secrets(text))


def mask_pii(text: str) -> str:
    """The text with each detected personal data value replaced by <ENTITY_TYPE>."""
    if not text:
        return text
    analyzer, anonymizer = _engines()
    results = analyzer.analyze(text=text, entities=list(ENTITIES), language="en", score_threshold=MIN_SCORE)
    return anonymizer.anonymize(text=text, analyzer_results=results).text if results else text
