# extractor/domains/kompas.py
from ..generic import extract_generic
from ..base import ExtractResult

async def extract(url: str) -> ExtractResult:
    return await extract_generic(url)
