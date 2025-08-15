# extractor/domains/__init__.py
from typing import Callable, Awaitable, Dict
from ..base import ExtractResult

from . import detik, kompas, cnnindonesia, tempo, liputan6, tribunnews, kumparan

DOMAIN_HANDLERS: Dict[str, Callable[[str], Awaitable[ExtractResult]]] = {
    "detik.com": detik.extract,
    "kompas.com": kompas.extract,
    "cnnindonesia.com": cnnindonesia.extract,
    "tempo.co": tempo.extract,
    "liputan6.com": liputan6.extract,
    "tribunnews.com": tribunnews.extract,
    "kumparan.com": kumparan.extract,
}
