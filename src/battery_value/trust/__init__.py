"""Making a battery's history cheap to check.

The used battery trade is a market for lemons. A buyer cannot tell a good pack
from a tired one before paying for it and arranging dangerous-goods freight, so
every pack is priced as though it might be tired, and the owners of good packs
decline to sell at that price. What is left in the market is the packs nobody
wanted, which confirms the discount and completes the circle.

The classical fix is not a mandate. It is **credible disclosure cheap enough
that a good seller bothers** -- and that is a transaction-cost problem, not a
market failure. Once checking a claim costs a buyer nothing, sellers who have
something to show start showing it, and the discount unwinds on its own.

So this package does exactly two things:

- :mod:`~battery_value.trust.certificate` records *who said what*, keeping
  measurement, declaration and computation apart, because a document that blurs
  them cannot be priced.
- :mod:`~battery_value.trust.signing` makes that record tamper-evident, so
  checking it needs no account, no API call and no trust in the seller.

:mod:`~battery_value.trust.compliance` is the same idea pointed at the
regulation: which of the fields 2023/1542 will ask for are present, and when
each one starts to matter.
"""

from .certificate import ATTESTATION, Certificate, Claim, ClaimBasis, issue
from .compliance import ComplianceView, Readiness, Requirement, assess
from .signing import Signature, Signer, default_signer, signing_available, verify

__all__ = [
    "ATTESTATION",
    "Certificate",
    "Claim",
    "ClaimBasis",
    "ComplianceView",
    "Readiness",
    "Requirement",
    "Signature",
    "Signer",
    "assess",
    "default_signer",
    "issue",
    "signing_available",
    "verify",
]
