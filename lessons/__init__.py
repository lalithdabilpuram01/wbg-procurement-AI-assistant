"""Continuous Learning: lesson cards from completion reports (ICR), IEG reviews (ICRR) and appraisal documents (PAD).

Every paragraph of a lessons section becomes a card (headline + text, page citation, topics, lifecycle stages and
procurement categories, tagged by rules). Cards start as `proposed`; a reviewer approves or rejects each one, and only
approved cards are used: in tender checks, monitoring answers and planning briefs (lessons.match). Re-ingesting a
document refreshes its cards' text and tags but never their review decision.
"""
