"""A market for retired battery packs, anchored on their own valuations.

The second-hand traction battery market is thin, and the reason is trust rather
than demand. A buyer cannot verify what a seller claims about state of health,
and a pack that turns out to be tired is a total loss on a several-hundred-kilo
item nobody wants to ship back. So buyers discount everything heavily, good
packs cannot fetch what they are worth, and their owners scrap them instead.

The rule here is one line long: **a listing can only be created from a valuation
reference.** The seller has scanned the pack, and the buyer opens the same
independent assessment -- health, wear against others of the same model, the
bill of materials, the prices used and where each came from.

Two consequences fall out of that, and both matter more than the listings:

- The asking price is shown against a **guide derived from the pack's own
  valuation**, so "is this a fair price" stops being a matter of opinion.
- A completed sale becomes an **observation** that flows back into battery-data,
  turning the weakest numbers in the model -- estimated used-part values -- into
  measurements of what packs actually fetched.
"""

from .models import Listing, ListingKind, ListingStatus, Offer, OfferStatus
from .pricing import PriceGuide, PriceVerdict, guide_price
from .service import MarketError, MarketService
from .store import MarketStore, default_market

__all__ = [
    "Listing",
    "ListingKind",
    "ListingStatus",
    "MarketError",
    "MarketService",
    "MarketStore",
    "Offer",
    "OfferStatus",
    "PriceGuide",
    "PriceVerdict",
    "default_market",
    "guide_price",
]
