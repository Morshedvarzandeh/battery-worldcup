"""Pack model catalogue: identify the pack, then know what is inside it."""

from .battery_data import (
    BatteryDataHttpProvider,
    BatteryDataPostgresProvider,
    battery_data_providers,
)
from .catalogue import (
    MATCH_THRESHOLD,
    PackCatalogue,
    catalogue_from_documents,
    load_catalogue,
    synthesise_components,
)
from .enrichment import EnrichmentResult, FilledField, enrich_passport
from .models import PackComponent, PackMatch, PackModel
from .providers import (
    BundledCatalogueProvider,
    HttpPackProvider,
    JsonDirectoryProvider,
    PackDataProvider,
    PackResolver,
    build_pack_resolver,
)

__all__ = [
    "MATCH_THRESHOLD",
    "BatteryDataHttpProvider",
    "BatteryDataPostgresProvider",
    "battery_data_providers",
    "BundledCatalogueProvider",
    "EnrichmentResult",
    "FilledField",
    "HttpPackProvider",
    "JsonDirectoryProvider",
    "PackCatalogue",
    "PackComponent",
    "PackDataProvider",
    "PackMatch",
    "PackModel",
    "PackResolver",
    "build_pack_resolver",
    "catalogue_from_documents",
    "enrich_passport",
    "load_catalogue",
    "synthesise_components",
]
